"""Module 14 - operator feedback API tests.

These run against in-memory stand-ins for every collection the module touches
(the same ``FakeDatabase`` the outcome suite uses), so the whole
request/response contract is exercised without a live MongoDB.

The behaviours worth locking down are the ones that could quietly let a research
record claim something nobody actually recorded:

* the pan and the recommended action for a decision are read from the stored
  recommendation, never from the client;
* a decision cannot be filed against advice that does not exist, or against a
  note that made no decision, and one that was already decided is refused rather
  than overwritten;
* a rejected or modified decision must name the action actually taken, while an
  acceptance may name one or record honestly that none followed;
* a recorded decision is opaque — an event only becomes comparable against the
  advice through the later actual-outcome step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.feedback import get_feedback_service
from app.api.routers.outcomes import (
    get_outcome_service,
    get_outcome_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.db.indexes import OPERATOR_DECISIONS, RECOMMENDATIONS
from app.db.repository import MongoRepository
from app.main import app
from app.models.base import BaseDocument
from app.models.decision_rule import DecisionEvaluation
from app.models.enums import (
    DecisionAction,
    DecisionBasis,
    HarvestOutcome,
    OperatorDecisionType,
)
from app.models.operator_decision import OperatorDecision
from app.models.recommendation import (
    DecisionSimulationSummary,
    DecisionTwinSummary,
    Recommendation,
)
from app.services.feedback import FeedbackService
from app.services.mass_balance import MASS_BALANCE_MODEL_NAME
from app.services.outcomes import OutcomeService
from app.services.salt_pans import SaltPanService
from tests.fakes import FakeDatabase

DocumentT = TypeVar("DocumentT", bound=BaseDocument)

MARCH_13 = datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc)
SPEC_HASH = "sha256:" + "a" * 64

VALID_PAN = {
    "pan_id": "KZN-2026-A01",
    "pan_name": "Northern Evaporation Pan",
    "location": "Port Shepstone",
    "area_m2": 10_000.0,
    "normal_depth_cm": 15.5,
    "target_harvest_salinity_g_l": 285.0,
}

VALID_EVENT = {
    "pan_id": "kzn-2026-a01",
    "event_id": "storm-2026-03-14",
    "timestamp": "2026-03-14T18:00:00Z",
    "forecast_rainfall_mm": 30.0,
    "actual_rainfall_mm": 40.0,
    "pre_event_salinity_g_l": 280.0,
    "pre_event_depth_cm": 20.0,
    "post_event_salinity_g_l": 255.0,
    "post_event_depth_cm": 24.0,
    "operator_action": "Opened the outlet gate for 40 minutes to spread the brine.",
    "harvest_outcome": HarvestOutcome.PARTIAL,
}


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def fb_client(database: FakeDatabase) -> Any:
    """A TestClient whose feedback, outcome and pan routes are backed by memory."""
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_feedback_service] = lambda: FeedbackService(database)
    app.dependency_overrides[get_outcome_service] = lambda: OutcomeService(database)
    app.dependency_overrides[get_outcome_status_service] = (
        lambda: OutcomeService(database)
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def registered_pan(fb_client: TestClient) -> str:
    response = fb_client.post("/api/pans/", json=VALID_PAN)
    assert response.status_code == 201, response.text
    return response.json()["pan_id"]


def _insert(
    database: FakeDatabase,
    collection: str,
    model: type[DocumentT],
    document: DocumentT,
) -> DocumentT:
    """Store one document of a neighbouring module directly, then return it."""
    return asyncio.run(
        MongoRepository(database, collection, model).insert(document)  # type: ignore[arg-type]
    )


def _drop_recommendation(database: FakeDatabase, recommendation_id: str) -> None:
    """Take a stored recommendation away, as a prune or a restore would.

    There is no API that does this -- recommendations are not deletable -- which
    is exactly why the decision's own copy of the advice has to be tested against
    it rather than left to a retention policy nobody has written yet.
    """
    asyncio.run(
        database[RECOMMENDATIONS].delete_one(
            {"recommendation_id": recommendation_id}
        )
    )


def _decisions(database: FakeDatabase) -> list[Any]:
    """Every stored operator decision, read outside the app's event loop."""
    return asyncio.run(
        MongoRepository(
            database, OPERATOR_DECISIONS, OperatorDecision
        ).find({})
    )


def _demonstration_twin() -> DecisionTwinSummary:
    """A twin whose state was assembled from worked examples, not field records."""
    return _twin().model_copy(
        update={
            "is_demonstration": True,
            "demonstration_sources": ["observation_data_source"],
        }
    )


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
        model_name=MASS_BALANCE_MODEL_NAME,
        rainfall_mm=0.0,
        rainfall_source="forecast",
        initial_salinity_g_l=282.0,
        initial_depth_cm=19.0,
        post_rain_salinity_g_l=282.0,
        post_rain_depth_cm=19.0,
        salinity_drop_g_l=0.0,
    )


def seed_decision(
    database: FakeDatabase,
    *,
    pan_id: str,
    action: DecisionAction = DecisionAction.HARVEST_NOW,
    issued_at: datetime = MARCH_13,
    **evidence: Any,
) -> Recommendation:
    """Store one complete decision recommendation, then return it.

    A stored decision is all-or-nothing: action, reason, basis, notice, twin,
    simulation, rule identity and the evaluation that fired all travel together.
    The helper fills that set by default; caller-supplied ``evidence`` overrides
    any member (e.g. to attach a model estimate).
    """
    recommendation = Recommendation(
        pan_id=pan_id,
        title=evidence.pop("title", "Harvest the top bed"),
        body="The salinity is at the harvest threshold.",
        rationale="The reading crossed the 285 g/L target.",
        action=action,
        reason="The brine is at or below the harvest salinity.",
        basis=evidence.pop("basis", DecisionBasis.PHYSICS_AND_RULES),
        notice="Physics/rule-based recommendation — ML prediction unavailable.",
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
        issued_at=issued_at,
        expires_at=issued_at + timedelta(days=2),
        **evidence,
    )
    return _insert(database, RECOMMENDATIONS, Recommendation, recommendation)


def seed_note(database: FakeDatabase, *, pan_id: str) -> Recommendation:
    """Store one note (no action, no evidence), then return it."""
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


def decide(client: TestClient, recommendation_id: str, **overrides: Any) -> Any:
    return client.post(
        "/api/feedback/",
        json={
            "recommendation_id": recommendation_id,
            "decision": "accepted",
            **overrides,
        },
    )


def record_event(client: TestClient, **overrides: Any) -> Any:
    return client.post("/api/outcomes/", json={**VALID_EVENT, **overrides})


# -- the module reports itself live --------------------------------------


def test_status_says_the_module_is_implemented(fb_client):
    body = fb_client.get("/api/feedback/status").json()

    assert body["module"] == "operator-feedback"
    assert body["module_number"] == 14
    assert body["status"] == "implemented"
    assert body["decision_vocabulary"] == ["accepted", "modified", "rejected"]
    assert body["decision_vocabulary_is_closed"] is True
    assert body["one_per_recommendation"] is True


# -- recording ------------------------------------------------------------


def test_an_accepted_decision_without_an_action_records_no_physical_action(
    database, fb_client, registered_pan
):
    """Accepting and acting are separate facts; a blank action stays blank.

    The advice recommends HARVEST NOW. Accepting it without naming an action
    records the disposition only: ``actual_action`` is ``None``, both in the
    response and in the stored document, so a reader knows no physical action
    followed — not that the recommended action was carried out.
    """
    recommendation = seed_decision(database, pan_id=registered_pan)

    response = decide(fb_client, recommendation.recommendation_id)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["decision"] == "accepted"
    assert body["pan_id"] == registered_pan
    assert body["actual_action"] is None
    assert body["recommended_action"] == "HARVEST NOW"
    assert body["recommendation_id"] == recommendation.recommendation_id
    assert _decisions(database)[0].actual_action is None


def test_an_accepted_decision_naming_the_recommended_action_stores_it(
    database, fb_client, registered_pan
):
    """The two cases an export must tell apart: acted, and not acted.

    Accepting and explicitly filing the recommended action is a physical act and
    is stored as such; accepting with nothing is not. The first answers
    ``actual_action`` while the second leaves it ``None``, so a reader never has
    to guess what "accepted" meant.
    """
    recommendation = seed_decision(database, pan_id=registered_pan)

    body = decide(
        fb_client,
        recommendation.recommendation_id,
        actual_action="HARVEST NOW",
    ).json()

    assert body["actual_action"] == "HARVEST NOW"
    assert body["recommended_action"] == "HARVEST NOW"
    assert (
        _decisions(database)[0].actual_action
        == DecisionAction.HARVEST_NOW.value
    )


def test_an_explicit_actual_action_is_filed_even_when_accepted(
    database, fb_client, registered_pan
):
    recommendation = seed_decision(database, pan_id=registered_pan)

    body = decide(
        fb_client,
        recommendation.recommendation_id,
        actual_action="WAIT AND RECHECK",
    ).json()

    assert body["actual_action"] == "WAIT AND RECHECK"
    assert body["recommended_action"] == "HARVEST NOW"


def test_a_modified_decision_must_name_its_action(database, fb_client, registered_pan):
    recommendation = seed_decision(database, pan_id=registered_pan)

    response = decide(
        fb_client, recommendation.recommendation_id, decision="modified"
    )
    assert response.status_code == 422

    body = decide(
        fb_client,
        recommendation.recommendation_id,
        decision="modified",
        actual_action="TRANSFER BRINE",
    )
    assert body.status_code == 201
    assert body.json()["actual_action"] == "TRANSFER BRINE"


def test_a_rejected_decision_must_name_its_action(database, fb_client, registered_pan):
    recommendation = seed_decision(database, pan_id=registered_pan)

    response = decide(
        fb_client, recommendation.recommendation_id, decision="rejected"
    )
    assert response.status_code == 422

    body = decide(
        fb_client,
        recommendation.recommendation_id,
        decision="rejected",
        actual_action="WAIT AND RECHECK",
    )
    assert body.status_code == 201
    assert body.json()["actual_action"] == "WAIT AND RECHECK"


def test_an_unknown_recommendation_is_refused_with_a_code(fb_client, registered_pan):
    response = decide(fb_client, "rec-does-not-exist")

    assert response.status_code == 409
    assert response.json()["code"] == "recommendation_unknown"


def test_a_note_that_made_no_decision_is_refused(database, fb_client, registered_pan):
    note = seed_note(database, pan_id=registered_pan)

    response = decide(fb_client, note.recommendation_id)

    assert response.status_code == 409
    assert response.json()["code"] == "recommendation_not_a_decision"


def test_a_recommendation_is_decided_once(database, fb_client, registered_pan):
    recommendation = seed_decision(database, pan_id=registered_pan)

    assert decide(fb_client, recommendation.recommendation_id).status_code == 201
    second = decide(
        fb_client,
        recommendation.recommendation_id,
        decision="rejected",
        actual_action="WAIT AND RECHECK",
    )

    assert second.status_code == 409
    assert second.json()["code"] == "decision_already_recorded"


def test_the_pan_is_never_taken_from_the_client(database, fb_client, registered_pan):
    """The request has no pan; the stored advice supplies it."""
    recommendation = seed_decision(database, pan_id=registered_pan)

    payload = {
        "recommendation_id": recommendation.recommendation_id,
        "decision": "accepted",
        "pan_id": "somewhere-else",
    }
    response = fb_client.post("/api/feedback/", json=payload)

    # extra="forbid" on the create schema: a client cannot sneak a pan in.
    assert response.status_code == 422


def test_an_operator_note_is_carried(database, fb_client, registered_pan):
    recommendation = seed_decision(database, pan_id=registered_pan)

    body = decide(
        fb_client,
        recommendation.recommendation_id,
        decision="accepted",
        operator_id="op-2026-team-3",
        notes="Crew is already staged; proceed at low tide.",
    ).json()

    assert body["operator_id"] == "op-2026-team-3"
    assert "staged" in body["notes"]


def test_a_timestamp_in_the_future_is_refused(database, fb_client, registered_pan):
    recommendation = seed_decision(database, pan_id=registered_pan)

    response = decide(
        fb_client,
        recommendation.recommendation_id,
        timestamp="2999-12-31T23:59:59Z",
    )

    assert response.status_code == 422


# -- a decision re-links the trace ----------------------------------------


def test_the_recorded_decision_carries_the_evidence_chain(
    database, fb_client, registered_pan
):
    """One decision row re-links observation -> twin -> simulation -> prediction.

    The advice block inside the record memorises the identifiers and instants of
    the earlier stages, so a research export reading a single document can
    rebuild the trace without a second query.
    """
    recommendation = seed_decision(
        database,
        pan_id=registered_pan,
        basis=DecisionBasis.PHYSICS_AND_ML,
        ml={
            "prediction_id": "pred-9",
            "model_id": "model-1",
            "model_version": "1.0",
            "target": "salinity_class",
            "predicted_class": "harvest_now",
            "probability": 0.9,
            "confidence": 0.83,
            "predicted_at": MARCH_13,
            "twin_as_of": MARCH_13,
            "matches_recommendation_snapshot": True,
        },
        explanation_id="expl-1",
        specification_hash="sha256:" + "b" * 64,
    )

    body = decide(fb_client, recommendation.recommendation_id).json()
    advice = body["recommendation"]

    assert advice["recommendation_id"] == recommendation.recommendation_id
    assert advice["action"] == "HARVEST NOW"
    assert advice["basis"] == "physics_and_ml"
    assert advice["simulation_run_id"] is None
    assert advice["ml_prediction_id"] == "pred-9"
    assert advice["explanation_id"] == "expl-1"
    assert advice["twin_id"] == "twin-42"
    assert advice["rule_name"] == "harvest-at-target"
    assert advice["rule_version"] == 3
    assert advice["specification_hash"] == "sha256:" + "b" * 64


# -- reads ----------------------------------------------------------------


def test_a_recorded_decision_can_be_read_back(database, fb_client, registered_pan):
    recommendation = seed_decision(database, pan_id=registered_pan)
    created = decide(fb_client, recommendation.recommendation_id).json()

    fetched = fb_client.get(f"/api/feedback/{created['decision_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["decision_id"] == created["decision_id"]
    assert fetched.json()["recommendation_id"] == recommendation.recommendation_id


def test_an_unknown_decision_id_is_a_normal_404(fb_client, registered_pan):
    response = fb_client.get("/api/feedback/decision-absent")

    assert response.status_code == 404
    assert "absent" in response.json()["detail"]


def test_listing_returns_newest_first(database, fb_client, registered_pan):
    earlier_rec = seed_decision(
        database,
        pan_id=registered_pan,
        action=DecisionAction.HARVEST_NOW,
        issued_at=datetime(2026, 3, 12, 9, 0, tzinfo=timezone.utc),
    )
    later_rec = seed_decision(
        database,
        pan_id=registered_pan,
        action=DecisionAction.WAIT_AND_RECHECK,
        issued_at=datetime(2026, 3, 14, 9, 0, tzinfo=timezone.utc),
    )
    earlier = decide(
        fb_client,
        earlier_rec.recommendation_id,
        timestamp="2026-03-13T10:00:00Z",
    ).json()["decision_id"]
    later = decide(
        fb_client,
        later_rec.recommendation_id,
        timestamp="2026-03-15T10:00:00Z",
    ).json()["decision_id"]

    items = fb_client.get("/api/feedback/").json()["items"]
    assert [item["decision_id"] for item in items] == [later, earlier]


def test_listing_can_be_filtered_by_decision(database, fb_client, registered_pan):
    accepted_rec = seed_decision(database, pan_id=registered_pan, action=DecisionAction.HARVEST_NOW)
    rejected_rec = seed_decision(
        database,
        pan_id=registered_pan,
        action=DecisionAction.WAIT_AND_RECHECK,
        title="Refusal cycle",
    )

    accepted_id = decide(fb_client, accepted_rec.recommendation_id).json()["decision_id"]
    decide(
        fb_client,
        rejected_rec.recommendation_id,
        decision="rejected",
        actual_action="PROTECT / DRAIN",
    )

    only_rejected = fb_client.get("/api/feedback/?decision=rejected").json()
    assert only_rejected["count"] == 1
    assert only_rejected["items"][0]["decision"] == "rejected"

    only_accepted = fb_client.get("/api/feedback/?decision=accepted").json()
    assert [item["decision_id"] for item in only_accepted["items"]] == [accepted_id]


def test_a_typo_in_the_decision_filter_is_refused(fb_client, registered_pan):
    response = fb_client.get("/api/feedback/?decision=accepted,%20maybe")

    assert response.status_code == 409
    assert response.json()["code"] == "unknown_decision_vocabulary"


# -- the outcome links the decision into the trace ----------------------


def test_an_outcome_with_a_decision_in_force_links_it(database, fb_client, registered_pan):
    """The chain advice -> operator decision -> outcome is rebuilt in one row."""
    recommendation = seed_decision(database, pan_id=registered_pan)
    decision = decide(fb_client, recommendation.recommendation_id).json()

    body = record_event(fb_client).json()["links"]

    assert body["recommendation"]["is_resolved"] is True
    decision_link = body["operator_decision"]
    assert decision_link["is_resolved"] is True
    assert decision_link["record_id"] == decision["decision_id"]
    assert "accepted" in decision_link["summary"]


def test_an_outcome_without_a_decision_says_so(database, fb_client, registered_pan):
    """No decision filed is an ordinary state, reported rather than invented."""
    seed_decision(database, pan_id=registered_pan)

    decision_link = record_event(fb_client).json()["links"]["operator_decision"]

    assert decision_link["is_resolved"] is False
    assert "No operator decision" in decision_link["summary"]


def test_a_decision_for_another_recommendation_is_refused(
    database, fb_client, registered_pan
):
    """An explicit decision reference must answer the advice in force."""
    in_force = seed_decision(database, pan_id=registered_pan, action=DecisionAction.HARVEST_NOW)
    other = seed_decision(
        database,
        pan_id=registered_pan,
        action=DecisionAction.WAIT_AND_RECHECK,
        issued_at=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
    )
    other_decision = decide(fb_client, other.recommendation_id).json()
    assert in_force.recommendation_id != other.recommendation_id

    response = record_event(
        fb_client,
        references={"decision_id": other_decision["decision_id"]},
    )

    assert response.status_code == 422
    assert "answers recommendation" in response.json()["detail"]


def test_a_decision_for_another_pan_is_refused(database, fb_client, registered_pan):
    """A decision reference from a different pan is refused like any record."""
    other_pan = fb_client.post(
        "/api/pans/",
        json={
            **VALID_PAN,
            "pan_id": "KZN-2026-A02",
            "pan_name": "Southern Evaporation Pan",
        },
    ).json()["pan_id"]
    other_rec = seed_decision(database, pan_id=other_pan)
    other_decision = decide(fb_client, other_rec.recommendation_id).json()

    response = record_event(
        fb_client,
        references={"decision_id": other_decision["decision_id"]},
    )

    assert response.status_code == 422
    assert "belongs to pan" in response.json()["detail"]

# -- the record is self-describing ---------------------------------------

def test_the_stored_decision_copies_the_rule_that_produced_the_advice(
    database, fb_client, registered_pan
):
    """What was advised, and by which rule version, is recorded, not re-derived.

    A decision is the end of the trace, so it is the document most likely to be
    read after the rest has moved on. If the rule version only came from a join,
    then losing the recommendation would leave the record of a person's judgement
    unable to say what they were judging -- and an acceptance indistinguishable
    from a coincidence. Asserted on the stored document, because the API response
    could be satisfying the requirement from the join instead.
    """
    recommendation = seed_decision(
        database, pan_id=registered_pan, rule_name="harvest-at-target", rule_version=3
    )
    assert decide(fb_client, recommendation.recommendation_id).status_code == 201

    stored = _decisions(database)[0]
    assert stored.recommended_action == DecisionAction.HARVEST_NOW
    assert stored.rule_name == "harvest-at-target"
    assert stored.rule_version == 3
    assert stored.specification_hash == SPEC_HASH


def test_the_recorded_decision_survives_the_loss_of_its_recommendation(
    database, fb_client, registered_pan
):
    """An operator's decision is not voided by a document it does not own.

    Recommendations are versioned, superseded and pruned, and a restore or a
    retention policy can take one away. The judgement is still the operator's,
    and the record still says what they were advised and by which rule, because
    those were copied in. What is lost is the deeper trace, and only that -- the
    read says so rather than refusing.
    """
    recommendation = seed_decision(database, pan_id=registered_pan)
    decided = decide(fb_client, recommendation.recommendation_id).json()
    decision_id = decided["decision_id"]

    _drop_recommendation(database, recommendation.recommendation_id)

    read_back = fb_client.get(f"/api/feedback/{decision_id}")
    assert read_back.status_code == 200, read_back.text
    body = read_back.json()
    assert body["decision"] == "accepted"
    # The blank acceptance stays blank across the read: the loss prunes the
    # recommendation, not the operator's record of what was and was not done.
    assert body["actual_action"] is None
    # The copy answers for the advice the join can no longer supply.
    assert body["recommended_action"] == DecisionAction.HARVEST_NOW.value
    assert body["rule_name"] == "harvest-at-target"
    assert body["rule_version"] == 3
    # And the loss is visible rather than papered over with nulls.
    assert body["recommendation"] is None


def test_one_lost_recommendation_does_not_take_down_the_whole_listing(
    database, fb_client, registered_pan
):
    """Every other decision in the listing is unaffected, so the listing answers.

    The orphan is one row. Refusing the read would have made the whole listing
    fail with a 409 for a reason that describes none of the other decisions in
    it, which is a far worse outcome than one row with a thinner trace.
    """
    kept = seed_decision(database, pan_id=registered_pan)
    orphaned = seed_decision(database, pan_id=registered_pan)
    assert decide(fb_client, kept.recommendation_id).status_code == 201
    assert decide(fb_client, orphaned.recommendation_id).status_code == 201

    _drop_recommendation(database, orphaned.recommendation_id)

    listing = fb_client.get(f"/api/feedback/?pan_id={registered_pan.upper()}")
    assert listing.status_code == 200, listing.text
    assert listing.json()["count"] == 2


# -- demonstration provenance ---------------------------------------------

def test_a_decision_on_demonstration_advice_is_labelled_demonstration(
    database, fb_client, registered_pan
):
    """An operator can make a real decision about a worked example.

    The record is worth keeping, and the flag travels with it, so a research
    export reads the label rather than inferring it from source strings it may
    not have been given. Asserted on the stored document, because a response that
    labelled it while the document did not would lose the label on the next read.
    """
    recommendation = seed_decision(
        database, pan_id=registered_pan, twin=_demonstration_twin()
    )
    assert decide(fb_client, recommendation.recommendation_id).status_code == 201

    stored = _decisions(database)[0]
    assert stored.is_demonstration is True
    assert stored.demonstration_sources == ["observation_data_source"]

    body = fb_client.get(f"/api/feedback/?pan_id={registered_pan.upper()}").json()
    assert body["items"][0]["is_demonstration"] is True
    assert body["items"][0]["demonstration_sources"] == ["observation_data_source"]


def test_a_decision_on_real_advice_is_not_labelled_demonstration(
    database, fb_client, registered_pan
):
    """The flag is a positive claim, and a real pan must not inherit it.

    Defaulting it to true would let one worked example detach the label from
    every record filed after it, which is the failure this flag exists to stop.
    """
    recommendation = seed_decision(database, pan_id=registered_pan)
    assert decide(fb_client, recommendation.recommendation_id).status_code == 201

    stored = _decisions(database)[0]
    assert stored.is_demonstration is False
    assert stored.demonstration_sources == []


def test_a_decision_written_before_the_flag_existed_says_it_does_not_know(
    database, fb_client, registered_pan
):
    """Null means "not recorded", which is not the same claim as ``False``.

    A record filed before the flag was copied holds nothing at all, and the live
    advice it cites is the only place left that knows the answer -- so that is
    where the read gets it. Defaulting the gap to ``False`` instead would publish
    a worked example as a field observation, which is the single failure this
    field exists to prevent.
    """
    recommendation = seed_decision(
        database, pan_id=registered_pan, twin=_demonstration_twin()
    )
    assert decide(fb_client, recommendation.recommendation_id).status_code == 201

    # Rewrite the stored document as the pre-flag schema would have left it.
    collection = database[OPERATOR_DECISIONS]
    collection._documents[0]["is_demonstration"] = None
    collection._documents[0]["demonstration_sources"] = []

    read = fb_client.get(f"/api/feedback/{collection._documents[0]['decision_id']}")
    body = read.json()
    assert read.status_code == 200
    assert body["is_demonstration"] is True
    assert body["demonstration_sources"] == ["observation_data_source"]


def test_a_decision_with_nothing_left_to_resolve_its_provenance_from_says_unknown(
    database, fb_client, registered_pan
):
    """With no copy and no advice, the honest answer is null -- not ``False``.

    Reporting "not demonstration" here would be an assertion about provenance that
    no surviving document supports, in a field whose entire purpose is to stop a
    reader assuming one. A null is answerable ("we do not know"); a false is not.
    """
    recommendation = seed_decision(
        database, pan_id=registered_pan, twin=_demonstration_twin()
    )
    assert decide(fb_client, recommendation.recommendation_id).status_code == 201

    decision_id = database[OPERATOR_DECISIONS]._documents[0]["decision_id"]
    database[OPERATOR_DECISIONS]._documents[0]["is_demonstration"] = None
    database[OPERATOR_DECISIONS]._documents[0]["demonstration_sources"] = []
    _drop_recommendation(database, recommendation.recommendation_id)

    body = fb_client.get(f"/api/feedback/{decision_id}").json()
    assert body["is_demonstration"] is None
    assert body["demonstration_sources"] == []
    # Everything the decision itself owns is still readable without the advice.
    assert body["decision"] == "accepted"
    assert body["pan_id"] == registered_pan


def test_a_rejection_on_demonstration_advice_keeps_its_own_action(
    database, fb_client, registered_pan
):
    """The label rides along with a decision that departs from the advice too.

    A rejection is the case most likely to be read as a real-world disagreement
    with the rule, so it is exactly the record that must not read as one.
    """
    recommendation = seed_decision(database, pan_id=registered_pan)
    assert (
        decide(
            fb_client,
            recommendation.recommendation_id,
            decision="rejected",
            actual_action="WAIT AND RECHECK",
        ).status_code
        == 201
    )

    stored = _decisions(database)[0]
    assert stored.decision.value == "rejected"
    assert stored.actual_action == DecisionAction.WAIT_AND_RECHECK
    # Still what was advised, so the departure is legible without the join.
    assert stored.recommended_action == DecisionAction.HARVEST_NOW


# -- duplicate prevention at the database, not only in the service --------

def test_a_concurrent_second_decision_is_refused_by_the_unique_index(
    database, fb_client, registered_pan, monkeypatch
):
    """The guarantee holds when two decisions arrive together.

    The service checks before it writes, but a check and an insert that are not
    one operation are two operators deciding the same recommendation at the same
    moment away from each other: both see it undecided and both write. The unique
    index is what closes that gap, and the branch that turns its refusal into the
    same code a client already handles is what stops the race from surfacing as a
    500. Asserted here by making the repository raise the error Mongo would raise,
    because the fake's enforcement is only reached through the service's own
    pre-check otherwise.
    """
    from app.db.errors import DuplicateDocumentError
    from app.schemas.feedback import FeedbackCreate
    from app.services.feedback import FeedbackRefusal

    recommendation = seed_decision(database, pan_id=registered_pan)
    service = FeedbackService(database)

    # The decision repository refuses, as the unique index would.
    async def refused_insert(document: Any) -> Any:
        raise DuplicateDocumentError(
            "A document with the same natural key already exists in "
            "operator_decisions."
        )

    repository = service._decision_repository()
    monkeypatch.setattr(repository, "insert", refused_insert)

    with pytest.raises(FeedbackRefusal) as caught:
        asyncio.run(
            service.record(
                FeedbackCreate(
                    recommendation_id=recommendation.recommendation_id,
                    decision=OperatorDecisionType.ACCEPTED,
                )
            )
        )
    # The same code the pre-check raises, so a client branches once for both.
    assert caught.value.code == "decision_already_recorded"
    assert caught.value.recommendation_id == recommendation.recommendation_id
