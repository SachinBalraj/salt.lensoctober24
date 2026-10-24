"""WhatsApp farmer-communication tests.

Everything here runs against the in-memory ``FakeDatabase`` the rest of the backend
suite uses, so the full HTTP contract is exercised without a live cluster.

The tests are organised around the promises this feature makes, and most of them
are about what must *not* happen:

* the message is built from the stored recommendation, so a client that supplies
  its own text, pan, action or number has nowhere to put them;
* the pan whose farmer is contacted comes from ``recommendation.pan_id``, so a
  caller cannot aim a send at a pan it chose;
* a send writes one append-only record that says ``SEND_INITIATED`` and
  ``not_confirmed``, and does not modify the recommendation, create an operator
  decision, or touch verification, outcome or field-validation state;
* the full number never appears in a response body, in the audit record, or in the
  click-to-chat URL's host — only a masked form and the digits WhatsApp itself needs;
* refusals are distinguishable: a missing contact, an unusable number, an unknown
  recommendation and a note that named no action each get their own code, and none
  of them writes anything.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.decisions import get_whatsapp_service
from app.api.routers.pans import get_salt_pan_service
from app.db.collections import (
    OPERATOR_DECISIONS,
    PAN_OBSERVATIONS,
    RAINFALL_EVENTS,
    RECOMMENDATION_DELIVERIES,
    RECOMMENDATIONS,
    SALT_PANS,
)
from app.db.repository import MongoRepository
from app.main import app
from app.models.base import BaseDocument
from app.models.decision_rule import DecisionEvaluation
from app.models.enums import (
    DecisionAction,
    DecisionBasis,
    RecommendationPriority,
)
from app.models.field_validation import VerificationState
from app.models.recommendation import (
    DecisionPredictionSummary,
    DecisionSimulationSummary,
    DecisionTwinSummary,
    Recommendation,
)
from app.models.salt_pan import RiskThresholds, SaltPan
from app.services.salt_pans import SaltPanService
from app.services.whatsapp_delivery import WhatsAppService
from tests.fakes import FakeDatabase

DocumentT = TypeVar("DocumentT", bound=BaseDocument)

MARCH_13 = datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc)
SPEC_HASH = "sha256:" + "a" * 64
NUMBER = "+919876543210"
MASKED = "+919•••••••10"

VALID_PAN: dict[str, Any] = {
    "pan_id": "KZN-2026-A01",
    "pan_name": "Northern Evaporation Pan",
    "location": "Port Shepstone",
    "area_m2": 10_000.0,
    "normal_depth_cm": 15.5,
    "target_harvest_salinity_g_l": 285.0,
}


# -- fixtures ---------------------------------------------------------------


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def wa_client(database: FakeDatabase) -> Any:
    """A TestClient whose pan and WhatsApp routes are backed by memory."""
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_whatsapp_service] = lambda: WhatsAppService(database)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _insert(
    database: FakeDatabase,
    collection: str,
    model: type[DocumentT],
    document: DocumentT,
) -> DocumentT:
    return asyncio.run(
        MongoRepository(database, collection, model).insert(document)  # type: ignore[arg-type]
    )


def _register_pan(
    client: TestClient, **overrides: Any
) -> str:
    payload = {**VALID_PAN, **overrides}
    response = client.post("/api/pans/", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["pan_id"]


def _twin() -> DecisionTwinSummary:
    return DecisionTwinSummary(
        twin_id="twin-42",
        as_of=MARCH_13,
        provenance="observed",
        salinity_g_l=282.0,
        water_depth_cm=19.0,
    )


def _simulation() -> DecisionSimulationSummary:
    return DecisionSimulationSummary(
        model_name="mass-balance-v1",
        rainfall_mm=0.0,
        rainfall_source="forecast",
        initial_salinity_g_l=282.0,
        initial_depth_cm=19.0,
        post_rain_salinity_g_l=282.0,
        post_rain_depth_cm=19.0,
        salinity_drop_g_l=0.0,
    )


def _seed_recommendation(
    database: FakeDatabase,
    *,
    pan_id: str,
    action: DecisionAction = DecisionAction.HARVEST_NOW,
    **evidence: Any,
) -> Recommendation:
    return _insert(
        database,
        RECOMMENDATIONS,
        Recommendation,
        Recommendation(
            pan_id=pan_id,
            title=evidence.pop("title", "Harvest the top bed"),
            body="The salinity is at the harvest threshold.",
            rationale="The reading crossed the 285 g/L target.",
            action=action,
            reason="The brine is at or below the harvest salinity.",
            basis=evidence.pop("basis", DecisionBasis.PHYSICS_AND_RULES),
            notice="Physics/rule-based recommendation - ML prediction unavailable.",
            twin=evidence.pop("twin", _twin()),
            simulation=evidence.pop("simulation", _simulation()),
            rule_id=evidence.pop("rule_id", "rule-12"),
            rule_name=evidence.pop("rule_name", "harvest-at-target"),
            rule_version=evidence.pop("rule_version", 3),
            evaluation=evidence.pop(
                "evaluation",
                DecisionEvaluation(
                    action=action,
                    rule_id="rule-12",
                    rule_name="harvest-at-target",
                    rule_version=3,
                    specification_hash=SPEC_HASH,
                ),
            ),
            specification_hash=evidence.pop("specification_hash", SPEC_HASH),
            priority=evidence.pop("priority", RecommendationPriority.MEDIUM),
            issued_at=MARCH_13,
            expires_at=MARCH_13 + timedelta(days=2),
            **evidence,
        ),
    )


def _seed_note(database: FakeDatabase, *, pan_id: str) -> Recommendation:
    return _insert(
        database,
        RECOMMENDATIONS,
        Recommendation,
        Recommendation(
            pan_id=pan_id,
            title="High tide advisory",
            body="Keep the outlet clear over the weekend.",
            rationale="The spring tide will raise the inlet level.",
            issued_at=MARCH_13,
            expires_at=MARCH_13 + timedelta(days=2),
        ),
    )


def _send(client: TestClient, recommendation_id: str, **body: Any) -> Any:
    return client.post(f"/api/decisions/{recommendation_id}/whatsapp-send", json=body)


def _link(client: TestClient, pan_id: str, message: str, **extra: Any) -> Any:
    return client.post(
        f"/api/pans/{pan_id}/whatsapp-link",
        json={"message": message, **extra},
    )


def _raw(database: FakeDatabase, collection: str) -> list[dict[str, Any]]:
    """The stored documents of one collection, read outside the app's event loop.

    Raw rather than parsed: these tests assert on what is *on disk*, including the
    absence of a field, and a model would supply defaults for the very fields the
    assertions are about.
    """
    return copy.deepcopy(database[collection].documents())


def _deliveries(database: FakeDatabase) -> list[dict[str, Any]]:
    return _raw(database, RECOMMENDATION_DELIVERIES)


def _models(database: FakeDatabase, collection: str, model: type[Any]) -> list[Any]:
    """The stored documents of one collection, parsed through its model."""
    return [model.from_mongo(document) for document in _raw(database, collection)]


def _ready(client: TestClient, database: FakeDatabase) -> str:
    """A pan with a contact and one stored recommendation ready to send."""
    pan_id = _register_pan(
        client, farmer_name="Ravi Patel", farmer_whatsapp=NUMBER
    )
    recommendation = _seed_recommendation(database, pan_id=pan_id)
    return recommendation.recommendation_id


# -- the happy path ---------------------------------------------------------


class TestSendInitiated:
    def test_returns_click_to_chat_url_and_masked_recipient(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        response = _send(wa_client, recommendation_id)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["click_to_chat_url"].startswith("https://wa.me/919876543210?text=")
        assert body["recipient_contact_reference"] == MASKED
        assert body["delivery_confirmed"] is False

    def test_record_states_initiated_and_never_delivered(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        delivery = _send(wa_client, recommendation_id).json()["delivery"]

        assert delivery["action"] == "SEND_INITIATED"
        assert delivery["delivery_status"] == "not_confirmed"
        assert delivery["delivery_confirmed"] is False
        assert delivery["farmer_response_recorded"] is False
        assert delivery["physical_action_recorded"] is False

    def test_record_identifies_the_recommendation_and_the_rule(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        delivery = _send(wa_client, recommendation_id).json()["delivery"]

        assert delivery["recommendation_id"] == recommendation_id
        assert delivery["pan_id"] == "kzn-2026-a01"  # pans normalise to lower case
        assert delivery["recommended_action"] == DecisionAction.HARVEST_NOW.value
        assert delivery["rule_name"] == "harvest-at-target"
        assert delivery["rule_version"] == 3
        assert delivery["specification_hash"] == SPEC_HASH

    def test_message_is_derived_from_the_stored_recommendation(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        message = _send(wa_client, recommendation_id).json()["message"]

        assert "Northern Evaporation Pan" in message
        assert "HARVEST NOW" in message
        assert "harvest-at-target" in message
        # The 285 g/L figure comes from the pan's configured target, not a
        # number written into this feature.
        assert "285" in message
        # The simulation label must survive into the message: a farmer reading
        # "222 g/L" has to know it is a simulation.
        assert "simulat" in message.lower()

    def test_hashes_and_counts_the_text_it_recorded(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        body = _send(wa_client, recommendation_id).json()
        delivery = body["delivery"]

        expected = "sha256:" + hashlib.sha256(
            body["message"].encode("utf-8")
        ).hexdigest()
        assert delivery["message_sha256"] == expected
        assert delivery["message_character_count"] == len(body["message"])
        assert delivery["message_template_version"]

    def test_disclaimer_must_survive_into_the_message(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        message = _send(wa_client, recommendation_id).json()["message"]

        assert "decision-support" in message
        assert "not a measured" in message
        assert "verify" in message.lower()

    def test_records_the_operator_when_one_is_supplied(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        delivery = _send(
            wa_client, recommendation_id, operator_id="op-team-3"
        ).json()["delivery"]

        assert delivery["operator_id"] == "op-team-3"

    def test_operator_is_optional(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        response = _send(wa_client, recommendation_id)

        assert response.status_code == 200
        assert response.json()["delivery"]["operator_id"] is None


# -- the audit record's meaning ---------------------------------------------


class TestAuditRecord:
    def test_writes_exactly_one_document(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        _send(wa_client, recommendation_id)

        assert len(_deliveries(database)) == 1

    def test_never_stores_the_full_number(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        _send(wa_client, recommendation_id)
        stored = _deliveries(database)[0]

        assert stored["farmer_contact_reference"] == MASKED
        assert "919876543210" not in str(stored)

    def test_never_stores_the_full_message(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """The hash and length are the evidence; the text is not stored again.

        The message is reconstructible from the recommendation and the pan, so
        keeping a copy here would be a second, driftable statement of what was
        said.
        """
        recommendation_id = _ready(wa_client, database)

        body = _send(wa_client, recommendation_id).json()
        stored = _deliveries(database)[0]

        assert body["message"] not in str(stored)
        assert "sha256:" in stored["message_sha256"]

    def test_repeated_sends_append_rather_than_replace(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """Re-sending is legitimate and must stay visible as repeated presses.

        If the second press updated the first row, the collection would report one
        send for two presses — exactly the misreporting an append-only audit is
        meant to prevent.
        """
        recommendation_id = _ready(wa_client, database)

        first = _send(wa_client, recommendation_id).json()["delivery"]
        second = _send(wa_client, recommendation_id).json()["delivery"]

        assert first["delivery_id"] != second["delivery_id"]
        assert len(_deliveries(database)) == 2

    def test_history_lists_every_initiation_newest_first(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)
        _send(wa_client, recommendation_id)
        _send(wa_client, recommendation_id)

        response = wa_client.get(
            f"/api/decisions/{recommendation_id}/whatsapp-sends"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["found"] is True
        assert len(body["deliveries"]) == 2
        assert all(d["action"] == "SEND_INITIATED" for d in body["deliveries"])
        assert (
            body["deliveries"][0]["initiated_at"]
            >= body["deliveries"][1]["initiated_at"]
        )

    def test_history_for_an_unsent_recommendation_is_not_an_error(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(client=wa_client, farmer_whatsapp=NUMBER)
        recommendation = _seed_recommendation(database, pan_id=pan_id)

        response = wa_client.get(
            f"/api/decisions/{recommendation.recommendation_id}/whatsapp-sends"
        )

        assert response.status_code == 200
        assert response.json() == {
            "recommendation_id": recommendation.recommendation_id,
            "found": False,
            "deliveries": [],
        }


class TestSendsChangeNothingElse:
    """A send is a communication event. It is not a decision or an observation."""

    def test_does_not_modify_the_recommendation(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        recommendation = _seed_recommendation(database, pan_id=pan_id)
        before = _raw(database, RECOMMENDATIONS)

        _send(wa_client, recommendation.recommendation_id)

        after = _raw(database, RECOMMENDATIONS)
        assert after == before

    def test_does_not_create_an_operator_decision(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """The tempting bug: a send reads as the operator agreeing.

        It is not. Accepting the recommendation is a separate, explicit act through
        Module 14, and a send that implied acceptance would let a message be filed
        as a disposition nobody made.
        """
        recommendation_id = _ready(wa_client, database)

        _send(wa_client, recommendation_id)

        assert _raw(database, OPERATOR_DECISIONS) == []

    def test_creates_no_field_validation_or_observation(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """A send is not a field measurement and must not be filed as one.

        Physical verification in this system is someone going to the pan: a rainfall
        event recorded as observed, a reading recorded against the pan. If a send
        could satisfy either, the project's measured-outcome claim would rest on an
        operator tapping a button.
        """
        recommendation_id = _ready(wa_client, database)

        _send(wa_client, recommendation_id)

        assert _raw(database, RAINFALL_EVENTS) == []
        assert _raw(database, PAN_OBSERVATIONS) == []

    def test_leaves_existing_verification_untouched(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """Even where verification exists, a send neither creates nor clears it."""
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        recommendation = _seed_recommendation(database, pan_id=pan_id)
        # Inserted raw: the point is that an existing, independently verified event
        # is left exactly as it is, so building a full valid event would only add
        # fields this assertion does not read.
        event = {
            "event_id": "storm-2026-03-14",
            "pan_id": pan_id,
            "name": "March storm",
            "verification_state": VerificationState.VERIFIED.value,
            "actual_rainfall_mm": 40.0,
            "reported_by": "field team",
        }
        asyncio.run(database[RAINFALL_EVENTS].insert_one(event))

        _send(wa_client, recommendation.recommendation_id)

        rows = _raw(database, RAINFALL_EVENTS)
        assert len(rows) == 1
        assert rows[0]["event_id"] == event["event_id"]
        assert rows[0]["verification_state"] == VerificationState.VERIFIED.value

    def test_does_not_touch_the_pan_beyond_its_stored_contact(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """Reading a contact for the message must not update the pan.

        Sending twice would otherwise write twice, and a pan's document would carry
        a modification timestamp for an act that changed nothing about the pan.
        """
        recommendation_id = _ready(wa_client, database)
        before = _raw(database, SALT_PANS)

        _send(wa_client, recommendation_id)
        _send(wa_client, recommendation_id)

        assert _raw(database, SALT_PANS) == before


# -- refusals ---------------------------------------------------------------


class TestRefusals:
    def test_unknown_recommendation(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        _register_pan(wa_client, farmer_whatsapp=NUMBER)

        response = _send(wa_client, "rec-does-not-exist")

        assert response.status_code == 409
        assert response.json()["code"] == "recommendation_unknown"

    def test_note_that_named_no_action(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """There is nothing to communicate if the record made no recommendation."""
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        note = _seed_note(database, pan_id=pan_id)

        response = _send(wa_client, note.recommendation_id)

        assert response.status_code == 409
        assert response.json()["code"] == "recommendation_not_a_decision"

    def test_demonstration_recommendation_is_never_sent(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """A demonstration-backed recommendation is never communicated at all.

        The send and its preview share the guard in ``_recommendation``, so neither
        the click-to-chat URL nor the message text can be produced for one. Refusing
        both keeps the guarantee at the service rather than at a button an operator
        could otherwise reach past.
        """
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        recommendation = _seed_recommendation(
            database,
            pan_id=pan_id,
            twin=DecisionTwinSummary(
                twin_id="twin-42",
                as_of=MARCH_13,
                provenance="observed",
                salinity_g_l=282.0,
                water_depth_cm=19.0,
                is_demonstration=True,
                demonstration_sources=["demonstration"],
            ),
        )

        send = _send(wa_client, recommendation.recommendation_id)
        preview = wa_client.get(
            f"/api/decisions/{recommendation.recommendation_id}/whatsapp-preview"
        )

        assert send.status_code == 409
        assert send.json()["code"] == "demonstration_recommendation_not_sendable"
        assert preview.status_code == 409
        assert preview.json()["code"] == "demonstration_recommendation_not_sendable"
        assert _deliveries(database) == []

    def test_pan_with_no_contact(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client)
        recommendation = _seed_recommendation(database, pan_id=pan_id)

        response = _send(wa_client, recommendation.recommendation_id)

        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "farmer_contact_not_configured"
        assert pan_id in body["detail"]

    def test_an_unusable_stored_number_never_produces_a_send(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """A number the model rejects cannot become a link, by two independent routes.

        The stored document is written straight past the validated registration
        path, as a record predating this constraint or written by some other tool
        would be. Reading the pan re-validates it, so the request fails before a URL
        is built — and the ``farmer_contact_invalid`` refusal in the service is a
        second line of defence that this test cannot reach over HTTP. Both are
        asserted here because the guarantee that matters is the one a user sees:
        nothing is sent, and nothing is recorded.
        """
        asyncio.run(
            database[SALT_PANS].insert_one(
                SaltPan.model_construct(
                    pan_id="kzn-2026-legacy",
                    pan_name="Legacy Pan",
                    location="Port Shepstone",
                    area_m2=10_000.0,
                    normal_depth_cm=15.5,
                    target_harvest_salinity_g_l=285.0,
                    risk_thresholds=RiskThresholds(),
                    farmer_whatsapp="098765 4321",
                ).to_mongo()
            )
        )
        recommendation = _seed_recommendation(database, pan_id="kzn-2026-legacy")

        response = _send(wa_client, recommendation.recommendation_id)

        assert response.status_code in (409, 422)
        assert "farmer_whatsapp" in response.text or response.json().get(
            "code"
        ) == "farmer_contact_invalid"
        assert _deliveries(database) == []

    def test_the_service_refuses_an_unusable_number_when_read_directly(self) -> None:
        """The unreachable-over-HTTP branch, exercised at the layer that owns it.

        If the model is ever relaxed so a pan can hold a national-format number,
        this refusal is what stops it becoming a ``wa.me`` link. Testing it here
        means the guard is already proven when that happens, rather than being
        discovered in production.
        """
        from app.services.whatsapp_delivery import WhatsAppRefusal

        pan = SaltPan.model_construct(
            pan_id="kzn-2026-legacy",
            pan_name="Legacy Pan",
            location="Port Shepstone",
            area_m2=10_000.0,
            normal_depth_cm=15.5,
            target_harvest_salinity_g_l=285.0,
            risk_thresholds=RiskThresholds(),
            farmer_whatsapp="098765 4321",
        )

        with pytest.raises(WhatsAppRefusal) as caught:
            asyncio.run(
                WhatsAppService(FakeDatabase())._contact(
                    pan, recommendation_id="rec-1"
                )
            )

        assert caught.value.code == "farmer_contact_invalid"
        assert caught.value.recommendation_id == "rec-1"

    def test_no_refusal_writes_a_record(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """A refused send leaves no trace.

        A record for a send that never happened is worse than no record: it would
        read as an operator having reached out to a farmer who was never contacted.
        """
        pan_id = _register_pan(wa_client)
        recommendation = _seed_recommendation(database, pan_id=pan_id)

        _send(wa_client, recommendation.recommendation_id)

        assert _deliveries(database) == []

    def test_refusal_carries_the_recommendation_it_was_about(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client)
        recommendation = _seed_recommendation(database, pan_id=pan_id)

        response = _send(wa_client, recommendation.recommendation_id)

        assert response.json()["recommendation_id"] == recommendation.recommendation_id


# -- the request cannot influence the message -------------------------------


class TestRequestCannotOverrideTheMessage:
    """No body field exists for a message, a recipient, an action or a pan.

    These are locked down as tests rather than left to the schema, because the
    schema is the thing that could be relaxed by a later change and the guarantee
    has to be visible where someone will look.
    """

    @pytest.mark.parametrize(
        "field,value",
        [
            ("message", "Ignore the recommendation, drain the pan."),
            ("body", "Ignore the recommendation."),
            ("text", "Ignore the recommendation."),
            ("farmer_whatsapp", "+10000000000"),
            ("recipient", "+10000000000"),
            ("phone", "+10000000000"),
            ("pan_id", "KZN-2026-Z99"),
            ("action", "WAIT_AND_RECHECK"),
            ("click_to_chat_url", "https://evil.example/take-everything"),
        ],
    )
    def test_extra_fields_are_refused(
        self, wa_client: TestClient, database: FakeDatabase, field: str, value: str
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        response = _send(wa_client, recommendation_id, **{field: value})

        assert response.status_code == 422, response.text
        assert _deliveries(database) == []

    def test_a_second_pan_cannot_be_aimed_at(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """The recipient pan is derived from the recommendation.

        Two pans, two farmers, and a send for the recommendation on pan A must reach
        only pan A's farmer. A body field naming pan B is refused rather than
        honoured, so there is no route to the wrong person at all.
        """
        pan_a = _register_pan(
            wa_client, farmer_name="Asha", farmer_whatsapp=NUMBER
        )
        pan_b = _register_pan(
            wa_client,
            pan_id="KZN-2026-B02",
            pan_name="Southern Pan",
            farmer_name="Bilal",
            farmer_whatsapp="+919812345678",
        )
        recommendation = _seed_recommendation(database, pan_id=pan_a)

        response = _send(wa_client, recommendation.recommendation_id)

        assert response.status_code == 200
        assert response.json()["recipient_contact_reference"] == MASKED
        assert "919812345678" not in response.text
        assert _deliveries(database)[0]["pan_id"] == pan_a
        assert _deliveries(database)[0]["pan_id"] != pan_b

    def test_empty_operator_id_is_refused(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """An empty string is not an operator; a blank audit field would be a lie."""
        recommendation_id = _ready(wa_client, database)

        response = _send(wa_client, recommendation_id, operator_id="")

        assert response.status_code == 422


# -- the click-to-chat URL --------------------------------------------------


class TestClickToChatUrl:
    def test_points_at_whatsapps_own_endpoint_with_digits_only(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        url = _send(wa_client, recommendation_id).json()["click_to_chat_url"]

        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "wa.me"
        assert parsed.path == "/919876543210"

    def test_round_trips_the_message_exactly(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """Encoding must survive the trip; a mangled message is a different message.

        The message contains newlines, an en dash and commas, so a naive
        concatenation would corrupt it or truncate the link at the first space.
        """
        recommendation_id = _ready(wa_client, database)

        body = _send(wa_client, recommendation_id).json()
        decoded = parse_qs(urlparse(body["click_to_chat_url"]).query)["text"][0]

        assert decoded == body["message"]

    def test_query_holds_no_unescaped_delimiters(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        url = _send(wa_client, recommendation_id).json()["click_to_chat_url"]
        query = urlparse(url).query

        assert "&" not in query
        assert " " not in query
        assert "\n" not in query

    def test_separators_in_the_stored_number_are_stripped(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """A number is often entered the way it is read aloud. It still has to work."""
        pan_id = _register_pan(
            wa_client, farmer_whatsapp="+91 98765-43210"
        )
        recommendation = _seed_recommendation(database, pan_id=pan_id)

        response = _send(wa_client, recommendation.recommendation_id)

        assert response.status_code == 200
        assert urlparse(response.json()["click_to_chat_url"]).path == "/919876543210"


# -- the preview ------------------------------------------------------------


class TestPreview:
    def test_shows_the_message_and_a_masked_recipient_without_writing(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        recommendation_id = _ready(wa_client, database)

        response = wa_client.get(
            f"/api/decisions/{recommendation_id}/whatsapp-preview"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["recipient_contact_reference"] == MASKED
        assert "HARVEST NOW" in body["message"]
        assert "919876543210" not in response.text
        assert _deliveries(database) == []

    def test_names_the_farmer_the_operator_is_about_to_contact(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """An operator confirming a message should see whose farmer it goes to.

        The name is read from the pan the recommendation points at, alongside the
        message, so the two cannot come from different places — the failure a
        caller passing a separate lookup would risk.
        """
        recommendation_id = _ready(wa_client, database)

        body = wa_client.get(
            f"/api/decisions/{recommendation_id}/whatsapp-preview"
        ).json()

        assert body["farmer_name"] == "Ravi Patel"

    def test_farmer_name_is_optional_and_not_required_to_send(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """A number is what a send needs; a name is a courtesy for the operator."""
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        recommendation = _seed_recommendation(database, pan_id=pan_id)

        preview = wa_client.get(
            f"/api/decisions/{recommendation.recommendation_id}/whatsapp-preview"
        )
        sent = _send(wa_client, recommendation.recommendation_id)

        assert preview.json()["farmer_name"] is None
        assert sent.status_code == 200, sent.text

    def test_preview_matches_what_a_send_then_produces(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """An operator who approves a preview must get that message.

        This is why the preview shares a code path with the send rather than
        rendering its own: two implementations would drift, and the operator would
        be approving text nobody sends.
        """
        recommendation_id = _ready(wa_client, database)

        preview = wa_client.get(
            f"/api/decisions/{recommendation_id}/whatsapp-preview"
        ).json()
        sent = _send(wa_client, recommendation_id).json()

        assert preview["message"] == sent["message"]
        assert preview["message_sha256"] == sent["delivery"]["message_sha256"]

    def test_refuses_for_the_same_reasons_a_send_does(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client)
        recommendation = _seed_recommendation(database, pan_id=pan_id)

        response = wa_client.get(
            f"/api/decisions/{recommendation.recommendation_id}/whatsapp-preview"
        )

        assert response.status_code == 409
        assert response.json()["code"] == "farmer_contact_not_configured"


# -- the mask ---------------------------------------------------------------


class TestMasking:
    def test_read_apis_expose_only_the_mask(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """A response body is the easiest place in this system for a number to leak.

        Pan reads, the preview and the send response all carry a masked form. The
        unmasked number exists on the pan document, where the send needs it, and
        goes nowhere else.
        """
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)

        pan_read = wa_client.get(f"/api/pans/{pan_id}")
        assert pan_read.status_code == 200
        assert "919876543210" not in pan_read.text
        assert pan_read.json()["farmer_whatsapp_masked"] == MASKED

    def test_a_registered_number_is_stored_normalised(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """Normalising on write means the stored value is already diallable."""
        _register_pan(wa_client, farmer_whatsapp="+91 98765-43210")

        rows = _raw(database, SALT_PANS)
        assert rows[0]["farmer_whatsapp"] == NUMBER

    @pytest.mark.parametrize("value", ["9876543210", "+919", "+9" * 20, "+91abc"])
    def test_a_non_e164_number_is_refused_at_registration(
        self, wa_client: TestClient, value: str
    ) -> None:
        """Rejected at the boundary, so an unusable number never reaches storage."""
        response = wa_client.post(
            "/api/pans/", json={**VALID_PAN, "farmer_whatsapp": value}
        )

        assert response.status_code == 422

    def test_a_pan_may_be_registered_without_a_contact(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """Contact is optional on registration; the send is where it is required."""
        pan_id = _register_pan(wa_client)

        response = wa_client.get(f"/api/pans/{pan_id}")

        assert response.status_code == 200
        assert response.json()["farmer_whatsapp_masked"] is None

    def test_a_contact_can_be_added_and_cleared_after_registration(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """Recording a farmer contact must not require re-registering the pan."""
        pan_id = _register_pan(wa_client)

        added = wa_client.patch(
            f"/api/pans/{pan_id}",
            json={"farmer_name": "Asha Naidoo", "farmer_whatsapp": NUMBER},
        )
        assert added.status_code == 200, added.text
        assert added.json()["farmer_whatsapp_masked"] == MASKED

        cleared = wa_client.patch(
            f"/api/pans/{pan_id}", json={"farmer_whatsapp": None}
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["farmer_whatsapp_masked"] is None

        # The name is untouched: clearing a number is not clearing the person.
        assert cleared.json()["farmer_name"] == "Asha Naidoo"


# -- every action and every evidence shape ----------------------------------


class TestAllRecommendationsAreSendable:
    @pytest.mark.parametrize(
        "action",
        [
            DecisionAction.HARVEST_NOW,
            DecisionAction.TRANSFER_BRINE,
            DecisionAction.PROTECT_DRAIN,
            DecisionAction.WAIT_AND_RECHECK,
        ],
    )
    def test_any_action_can_be_communicated(
        self, wa_client: TestClient, database: FakeDatabase, action: DecisionAction
    ) -> None:
        """All four actions are operational instructions a farmer needs, so all four
        are sendable. The send must not be quietly reserved for one of them."""
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        recommendation = _seed_recommendation(
            database, pan_id=pan_id, action=action
        )

        response = _send(wa_client, recommendation.recommendation_id)

        assert response.status_code == 200
        assert action.value in response.json()["message"]

    def test_the_message_never_carries_the_model_probability(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """A probability on a phone is the figure most easily read as certainty.

        Module 12 holds the model's estimate apart from the action and labels a null
        probability with its reason. Printing it in a message addressed to a person
        deciding whether to open a gate would undo that at the worst possible point,
        so the builder omits it — and this asserts the omission rather than trusting
        the code comment.
        """
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        recommendation = _seed_recommendation(
            database,
            pan_id=pan_id,
            basis=DecisionBasis.PHYSICS_AND_ML,
            ml=_ml_prediction(),
        )

        body = _send(wa_client, recommendation.recommendation_id).json()

        assert recommendation.ml is not None and recommendation.ml.probability == 0.82
        assert "0.82" not in body["message"]
        assert "%" not in body["message"]

    def test_the_action_still_comes_through_for_a_model_assisted_recommendation(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """Omitting the probability must not silently downgrade the recommendation.

        The rule still chose the action and the farmer still needs to hear it. If
        dropping the model figure had emptied the message, the right fix would be to
        put the figure back, not to send nothing.
        """
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        recommendation = _seed_recommendation(
            database,
            pan_id=pan_id,
            basis=DecisionBasis.PHYSICS_AND_ML,
            ml=_ml_prediction(),
        )

        message = _send(
            wa_client, recommendation.recommendation_id
        ).json()["message"]

        assert "HARVEST NOW" in message
        assert "harvest-at-target" in message
        assert "verify" in message.lower()


class TestTheBuilderOmitsWhatItCannotKnow:
    """The builder's behaviour on shapes the API cannot currently produce.

    ``Recommendation`` refuses a decision record that is missing its simulation, so
    these figures cannot be reached over HTTP today. They are still the behaviour the
    builder promises — an absent value is omitted rather than invented — and if that
    validation is ever relaxed the promise should already hold. ``model_construct``
    skips validation, which is how a shape the schema forbids can be rendered at
    all; the tests say so rather than implying the shape is reachable.
    """

    def _pan(self) -> SaltPan:
        return SaltPan(
            pan_id="kzn-2026-a01",
            pan_name="Northern Evaporation Pan",
            location="Port Shepstone",
            area_m2=10_000.0,
            normal_depth_cm=15.5,
            target_harvest_salinity_g_l=285.0,
            risk_thresholds=RiskThresholds(),
        )

    def test_a_record_without_a_simulation_has_no_predicted_salinity_line(self) -> None:
        from app.services.whatsapp import build_message

        recommendation = _seedless_record()
        message = build_message(recommendation, self._pan())

        assert "Predicted salinity" not in message.text
        assert message.salinity_g_l is None
        # The threshold comes from the pan's registered target, not the simulation,
        # so it is still true and still belongs in the message.
        assert "Configured harvest threshold: 285 g/L" in message.text

    def test_a_record_without_a_rule_omits_the_rule_line(self) -> None:
        from app.services.whatsapp import build_message

        # Built without the rule fields rather than assigned to ``None``: the model
        # validates on assignment, and a record that named no rule is not one this
        # project issues a decision from anyway.
        recommendation = _seedless_record(with_rule=False)
        message = build_message(recommendation, self._pan())

        assert "Recommendation rule" not in message.text

    def test_the_disclaimer_is_present_whatever_is_missing(self) -> None:
        """The one line that must never be dropped for want of data."""
        from app.services.whatsapp import VERIFICATION_DISCLAIMER, build_message

        message = build_message(_seedless_record(), self._pan())

        assert VERIFICATION_DISCLAIMER in message.text
        assert message.text.rstrip().endswith("physical action.")


def _seedless_record(*, with_rule: bool = True) -> Any:
    """A recommendation-shaped object with no simulation block, built past validation."""
    fields: dict[str, Any] = {
        "recommendation_id": "rec-1",
        "pan_id": "kzn-2026-a01",
        "action": DecisionAction.HARVEST_NOW,
        "priority": RecommendationPriority.MEDIUM,
        "reason": "r",
        "basis": DecisionBasis.PHYSICS_AND_RULES,
        "notice": "n",
        "title": "Harvest",
        "body": "b",
        "rationale": "r",
        "specification_hash": SPEC_HASH,
        "simulation": None,
        "issued_at": MARCH_13,
    }
    if with_rule:
        fields |= {
            "rule_id": "rule-12",
            "rule_name": "harvest-at-target",
            "rule_version": 3,
        }
    return Recommendation.model_construct(**fields)


def _ml_prediction() -> Any:
    """A stored model estimate, as Module 12 would have read it off Module 10."""
    return DecisionPredictionSummary(
        prediction_id="pred-1",
        model_id="model-logreg-v1",
        model_version="2",
        target="saline",
        predicted_class="saline",
        probability=0.82,
        predicted_at=MARCH_13,
        twin_as_of=MARCH_13,
        matches_recommendation_snapshot=True,
    )


# -- the routes themselves --------------------------------------------------


class TestRouteContract:
    def test_send_is_reachable_and_documented(self) -> None:
        schema = app.openapi()
        path = "/api/decisions/{recommendation_id}/whatsapp-send"

        assert path in schema["paths"]
        operation = schema["paths"][path]["post"]
        assert "whatsapp" in operation["summary"].lower()
        assert "send" in operation["summary"].lower()
        assert operation["responses"]["200"]["content"]["application/json"]

    def test_the_body_schema_offers_no_content_fields(self) -> None:
        """The guarantee stated in prose, checked against the served contract.

        Anyone integrating reads the OpenAPI document, so the absence of a message or
        recipient field has to be true there rather than only in a docstring.
        """
        schema = app.openapi()
        body = schema["paths"]["/api/decisions/{recommendation_id}/whatsapp-send"][
            "post"
        ]["requestBody"]["content"]["application/json"]["schema"]
        # The body is a $ref into components; resolve it rather than assert on the
        # reference, which would pass whatever the body grew to contain.
        if "$ref" in body:
            body = schema["components"]["schemas"][body["$ref"].rsplit("/", 1)[-1]]

        assert set(body["properties"]) == {"operator_id"}

    def test_preview_does_not_write_and_history_is_readable(self) -> None:
        schema = app.openapi()["paths"]
        assert "get" in schema["/api/decisions/{recommendation_id}/whatsapp-preview"]
        assert "get" in schema["/api/decisions/{recommendation_id}/whatsapp-sends"]
        assert not [
            method
            for method in ("post", "put", "patch", "delete")
            if method
            in schema["/api/decisions/{recommendation_id}/whatsapp-preview"]
        ]


def _unquote(value: str) -> str:
    return unquote(value)


# -- the operator-composed link ---------------------------------------------


class TestFarmerLink:
    """The link a panel builds from text it composed, without storing anything.

    The button in Module 12 summarises the live recommendation — pan, action,
    current salinity, depth, rainfall scenario, simulated salinity, target, rule and
    basis — which is more than the stored recommendation document carries. That text
    is composed in the browser and turned into a ``wa.me`` link here. What this class
    pins down is that doing so stays a *link*: the recipient is the pan on the path,
    the number never leaves as a value, the message is encoded so it survives the
    trip, and no document is written.
    """

    def test_points_at_whatsapps_own_endpoint_with_digits_only(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)

        url = _link(wa_client, pan_id, "Hello farmer").json()["click_to_chat_url"]

        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "wa.me"
        assert parsed.path == "/919876543210"

    def test_round_trips_the_message_exactly(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """Encoding must survive the trip; a mangled message is a different message."""
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        message = (
            "SaltLens DSS - Salt-pan recommendation\n\n"
            "Recommendation: TRANSFER BRINE & recheck + depth 19 cm"
        )

        url = _link(wa_client, pan_id, message).json()["click_to_chat_url"]

        decoded = parse_qs(urlparse(url).query)["text"][0]
        assert decoded == message

    def test_query_holds_no_unescaped_delimiters(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)

        url = _link(
            wa_client, pan_id, "Line one\nLine two & more + plus = equals"
        ).json()["click_to_chat_url"]

        query = urlparse(url).query
        assert "&" not in query
        assert " " not in query
        assert "\n" not in query
        assert "+" not in query

    def test_separators_in_the_stored_number_are_stripped(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client, farmer_whatsapp="+91 98765-43210")

        url = _link(wa_client, pan_id, "Hello").json()["click_to_chat_url"]

        assert urlparse(url).path == "/919876543210"

    def test_returns_the_masked_recipient_and_character_count(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        message = "A recommendation for the farmer."

        body = _link(wa_client, pan_id, message).json()

        assert body["recipient_contact_reference"] == MASKED
        assert body["pan_id"] == pan_id
        assert body["message_character_count"] == len(message)
        # The number reaches the browser only inside the link, never as its own
        # field, exactly as the send response already handles it.
        assert "farmer_whatsapp" not in body

    def test_writes_nothing(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """Building a link is a read: no send record, no recommendation, no edit."""
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        pans_before = _raw(database, SALT_PANS)

        assert _link(wa_client, pan_id, "Hello").status_code == 200

        assert _deliveries(database) == []
        assert _raw(database, SALT_PANS) == pans_before
        assert _raw(database, RECOMMENDATIONS) == []

    def test_refuses_a_pan_with_no_contact(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client)

        response = _link(wa_client, pan_id, "Hello")

        assert response.status_code == 409
        assert response.json()["code"] == "farmer_contact_not_configured"
        assert pan_id in response.json()["detail"]

    def test_refuses_an_unusable_stored_number(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        """A legacy national-format number cannot become a link, and writes nothing."""
        asyncio.run(
            database[SALT_PANS].insert_one(
                SaltPan.model_construct(
                    pan_id="kzn-2026-legacy",
                    pan_name="Legacy Pan",
                    location="Port Shepstone",
                    area_m2=10_000.0,
                    normal_depth_cm=15.5,
                    target_harvest_salinity_g_l=285.0,
                    risk_thresholds=RiskThresholds(),
                    farmer_whatsapp="098765 4321",
                ).to_mongo()
            )
        )

        response = _link(wa_client, "kzn-2026-legacy", "Hello")

        assert response.status_code in (409, 422)
        assert "farmer_whatsapp" in response.text or response.json().get(
            "code"
        ) == "farmer_contact_invalid"

    def test_an_unknown_pan_is_a_404(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        response = _link(wa_client, "kzn-2026-missing", "Hello")

        assert response.status_code == 404

    def test_an_empty_message_is_refused(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)

        response = _link(wa_client, pan_id, "")

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "field,value",
        [
            ("pan_id", "KZN-2026-Z99"),
            ("farmer_whatsapp", "+10000000000"),
            ("recipient", "+10000000000"),
            ("phone", "+10000000000"),
            ("click_to_chat_url", "https://evil.example/take-everything"),
        ],
    )
    def test_extra_fields_are_refused(
        self, wa_client: TestClient, database: FakeDatabase, field: str, value: str
    ) -> None:
        """The recipient is the pan on the path; a body cannot redirect or replace it."""
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)

        response = wa_client.post(
            f"/api/pans/{pan_id}/whatsapp-link",
            json={"message": "Hello", field: value},
        )

        assert response.status_code == 422, response.text

    def test_the_message_is_never_stored(
        self, wa_client: TestClient, database: FakeDatabase
    ) -> None:
        pan_id = _register_pan(wa_client, farmer_whatsapp=NUMBER)
        marker = "a phrase that must not be persisted anywhere"

        _link(wa_client, pan_id, marker)

        stored = _raw(database, RECOMMENDATION_DELIVERIES)
        assert stored == []
        assert all(marker not in str(row) for row in _raw(database, SALT_PANS))

    def test_the_openapi_contract_declares_the_message_and_no_phone_field(self) -> None:
        schema = app.openapi()["paths"]["/api/pans/{pan_id}/whatsapp-link"]["post"]
        body = schema["requestBody"]["content"]["application/json"]["schema"]
        if "$ref" in body:
            body = app.openapi()["components"]["schemas"][
                body["$ref"].rsplit("/", 1)[-1]
            ]

        assert set(body["properties"]) == {"message"}
        assert schema["responses"]["200"]["content"]["application/json"]
