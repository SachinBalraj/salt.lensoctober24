"""Module 10 - operational risk definitions, exercised over HTTP.

The tests are organised around the claims this module makes, because those are
what a reviewer will attack. That a severity vocabulary is the researcher's and
not the code's; that a rule is versioned and immutable rather than editable; that
a label cannot be produced without the rule and the source variables that produced
it travelling with it; and that the class distribution is available, with its
imbalance warnings, *before* anything is fitted.

Two of these tests are deliberately negative in a way that is easy to get wrong.
One checks that a rule naming a variable outside the published catalogue is
refused, because a rule about a quantity the project cannot measure cannot be
checked by anyone. The other checks that a rule reading a post-anchor measurement
cannot be used to label a dataset without an explicit acknowledgement, because a
retrospective label that looks prospective will be read as a forecast — and the
causal audit is the last place that can be caught.

What these tests cannot do is prove the module is not leaky in some way nobody
thought of. They prove the checks it does make are real, that they run on every
build, and that the rule which was validated is the rule which labels the rows.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.datasets import get_dataset_service
from app.api.routers.observations import get_observation_service
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.risk import get_risk_definition_service
from app.api.routers.weather import get_weather_service
from app.db.repository import MongoRepository
from app.main import app
from app.models.enums import LeakageClass
from app.models.risk_definition import (
    RiskDefinition,
    RiskDefinitionDraft,
    RiskSourceVariable,
)
from app.services.datasets import DatasetService
from app.services.observations import ObservationService
from app.services.risk_definitions import RiskDefinitionService
from app.services.risk_evaluation import evaluate_definition
from app.services.salt_pans import SaltPanService
from app.services.weather import WeatherService
from tests.fakes import FakeDatabase

VALID_PAN = {
    "pan_id": "KZN-2026-A01",
    "pan_name": "Northern Evaporation Pan",
    "location": "Port Shepstone",
    "area_m2": 10_000.0,
    "normal_depth_cm": 15.5,
    "target_harvest_salinity_g_l": 285.0,
}

#: Four daily readings a day apart. The first is the anchor every other reading is
#: measured against, so a 24-hour horizon gives the first three rows something to
#: read and leaves the fourth without.
OBSERVATIONS: list[dict[str, Any]] = [
    {"timestamp": "2026-03-01T06:00:00Z", "salinity_g_l": 280.0, "water_depth_cm": 20.0},
    {"timestamp": "2026-03-02T06:00:00Z", "salinity_g_l": 265.0, "water_depth_cm": 24.0},
    {"timestamp": "2026-03-03T06:00:00Z", "salinity_g_l": 250.0, "water_depth_cm": 28.0},
    {"timestamp": "2026-03-04T06:00:00Z", "salinity_g_l": 240.0, "water_depth_cm": 30.0},
]

#: A rain forecast issued before the first reading, so it is in force for every
#: anchor. Without one the mass balance has nothing to dilute by, and the loss
#: variable below stays unset on every row: a rule reading it would label nothing,
#: which is correct behaviour and a useless fixture.
FORECAST = {
    "timestamp": "2026-02-28T00:00:00Z",
    "forecast_for": "2026-03-05T06:00:00Z",
    "rainfall_mm": 25.0,
    "source": "manual",
}

#: The loss variable the rules below are written against. A difference of two
#: catalogued figures, so it is computed on the row rather than read from it.
LOSS = "expected_salinity_loss_g_l"

#: A complete, valid rule. Deliberately not the labels anyone might expect: the
#: point of the module is that these names are the researcher's, and a test that
#: used "high"/"medium"/"low" would be asserting the opposite.
DILUTION_RULE: dict[str, Any] = {
    "name": "monsoon dilution",
    "authored_by": "a researcher",
    "operational_rationale": (
        "Rank a pan by the salinity the forecast rain is expected to cost it, so "
        "the operator sees dilution as a loss in g/L rather than as a fall in a "
        "concentration they have to compute themselves."
    ),
    "classes": [
        {"name": "watch", "rank": 0, "description": "Monitor the pan; no action."},
        {"name": "act now", "rank": 1, "description": "Flush early to protect the harvest."},
    ],
    "criteria": [
        {
            "variable": LOSS,
            "operator": "gte",
            "threshold": 0.0,
            "label": "act now",
            "rationale": "Any forecast dilution at all warrants a decision this week.",
        }
    ],
}

RISK_FEATURES = [
    "current_salinity_g_l",
    "water_depth_cm",
    "physics_predicted_post_rain_salinity_g_l",
    "forecast_rainfall_mm",
]


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def client(database: FakeDatabase) -> Any:
    """A TestClient whose routes are backed by memory."""
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_observation_service] = (
        lambda: ObservationService(database)
    )
    app.dependency_overrides[get_dataset_service] = lambda: DatasetService(database)
    app.dependency_overrides[get_weather_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_risk_definition_service] = (
        lambda: RiskDefinitionService(database)
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _backdate_pan_registration(database: FakeDatabase) -> None:
    """Move the stored pan's ``created_at`` to before the readings it describes.

    A pan registered today cannot be a static property of a March anchor, and the
    builder says so rather than borrowing the area backwards in time. Every rule
    below reads a figure derived from the pan, so the fixture moves it rather than
    each test rediscovering why the loss variable stayed unset.
    """
    collection = database["salt_pans"]
    stored = collection.documents()[0]
    collection._documents[0] = {
        **stored,
        "created_at": datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
    }


def _seed(client: TestClient, database: FakeDatabase, *, backdate: bool) -> TestClient:
    """One pan, four daily readings, and a rain forecast in force for all of them.

    ``backdate=False`` leaves the pan's ``created_at`` at the present, which makes
    its static target salinity unavailable to rows anchored in March. That is the
    natural way to get a catalogued variable that is genuinely unset on a row.
    """
    assert client.post("/api/pans/", json=VALID_PAN).status_code == 201
    for observation in OBSERVATIONS:
        response = client.post(
            "/api/observations/",
            json={
                "pan_id": VALID_PAN["pan_id"],
                "brine_temperature_c": 30.0,
                **observation,
            },
        )
        assert response.status_code == 201, response.text
    forecast = client.post(
        "/api/weather/", json={"pan_id": VALID_PAN["pan_id"], **FORECAST}
    )
    assert forecast.status_code == 201, forecast.text
    assert forecast.json()["is_forecast"] is True
    if backdate:
        _backdate_pan_registration(database)
    return client


@pytest.fixture()
def seeded(client: TestClient, database: FakeDatabase) -> TestClient:
    """The ordinary fixture: a pan that existed before its readings."""
    return _seed(client, database, backdate=True)


def _detail(response: Any) -> str:
    """The failure text, whichever shape FastAPI reported it in.

    A rejected rule can fail in the model (a field is missing, a cross-field
    invariant does not hold) or in the service (a criterion contradicts the
    catalogue). FastAPI reports the first as a list of error objects and the
    second as a string, and a test asserting on the reason should not care which.
    """
    detail = response.json().get("detail", "")
    if isinstance(detail, str):
        return detail
    return " ".join(
        f"{'.'.join(str(part) for part in item.get('loc', ()))} {item.get('msg', item)}"
        for item in detail
    )


def _write_rule(client: TestClient, **overrides: Any) -> dict[str, Any]:
    """Write a rule and return the stored definition."""
    response = client.post("/api/risk/definitions", json={**DILUTION_RULE, **overrides})
    assert response.status_code == 201, response.text
    return response.json()["definition"]


# -- the module refuses to supply a vocabulary -------------------------------


def test_module_publishes_no_default_labels(client: TestClient) -> None:
    """The status endpoint must not name a severity vocabulary of its own.

    If this test ever fails because a class name appeared in the response, the
    module has started answering a question the researcher's rule should answer.
    """
    body = client.get("/api/risk/").json()
    assert body["defaults"]["label_vocabulary"] is None
    assert body["trained"] is False
    # Prediction is implemented and conditional, and the status says so rather than
    # claiming either that it works unconditionally or that it is absent.
    assert body["capabilities"]["risk_prediction"] == "implemented_gated_on_deployment"
    # The gate itself is published, so a client can learn the rule from the status
    # response instead of discovering it by being refused.
    assert "deployed" in body["prediction_gate"]["rule"]
    assert body["prediction_gate"]["with_nothing_deployed"].startswith("409")


def test_the_status_reports_the_registry_it_has(client: TestClient) -> None:
    """``trained`` and ``model_state`` are read, not written.

    With an empty registry the honest answer is "no model", which is also what a
    constant used to say — the difference is that this one changes when a model
    is registered, evaluated or deployed, and a constant could not.
    """
    body = client.get("/api/risk/").json()
    assert body["trained"] is False
    assert body["model_state"] == "no_model"
    # The sufficiency gate, with its own thresholds, so a client can state what
    # is actually required instead of hardcoding a number that would drift from
    # the code that enforces it.
    gate = body["training_gate"]
    assert gate["ready"] is False
    assert gate["genuine_labelled_rows"] == 0
    assert gate["min_total_labelled_rows"] == 80
    assert gate["min_distinct_pans"] == 3


def test_predict_needs_a_configured_database_before_anything_else(client: TestClient) -> None:
    """The 503 comes first, and that ordering is deliberate.

    A request that cannot reach the database cannot be told whether a model is
    deployed, so it is refused there rather than answered with a guess about the
    registry. With Mongo configured the same request reaches the deployment gate
    and is refused there for a different and more specific reason.
    """
    response = client.post(
        "/api/risk/predict",
        json={"pan_id": "KZN-2026-A01", "as_of": "2026-06-01T00:00:00Z"},
    )
    assert response.status_code == 503


def test_the_definition_ledger_is_refused_rather_than_reported_empty() -> None:
    """Reading rules needs the ledger, so an absent database is a 503.

    ``RiskDefinitionService`` is built with ``get_db`` itself, not with what it
    returns. A service that treated that provider as the database would fail with
    a 500 on the first query -- reporting a server fault for what is a missing
    configuration, and, worse, indistinguishable from a real defect.
    """
    with TestClient(app) as test_client:
        assert test_client.get("/api/risk/definitions").status_code == 503
        assert test_client.get("/api/risk/definitions/rd-1").status_code == 503


def test_predict_will_not_accept_a_model_from_the_request(client: TestClient) -> None:
    """The caller cannot choose which model answers.

    If ``model_id`` were accepted, the gate would be decorative: a request could
    name a model that is not deployed and be scored by it. Extra fields are refused
    outright rather than ignored, so a client that tries learns it from the 422.
    """
    response = client.post(
        "/api/risk/predict",
        json={
            "pan_id": "KZN-2026-A01",
            "as_of": "2026-06-01T00:00:00Z",
            "model_id": "model_deadbeef",
        },
    )
    assert response.status_code == 422


def test_predict_will_not_accept_features_from_the_request(client: TestClient) -> None:
    """Nor hand over its own feature values.

    A prediction assembled from values the caller supplied is not a prediction from
    the deployed model, whatever the number looks like. The window, the feature list
    and the risk rule all come from the model's own dataset manifest.
    """
    response = client.post(
        "/api/risk/predict",
        json={
            "pan_id": "KZN-2026-A01",
            "as_of": "2026-06-01T00:00:00Z",
            "features": {"rainfall_mm": 0.0},
        },
    )
    assert response.status_code == 422


def test_variable_catalogue_publishes_its_own_policy(client: TestClient) -> None:
    """What a rule may reference, and the imbalance policy, in one place.

    Served from the catalogue the evaluator reads, so it cannot drift from the
    rules that will be enforced.
    """
    body = client.get("/api/risk/variables").json()
    names = {variable["name"] for variable in body["variables"]}
    assert LOSS in names
    assert "harvest_outcome" in names
    # The one post-anchor variable is marked as such, because a rule reading it
    # produces a label that describes rather than forecasts.
    assert body["retrospective_variables"] == ["harvest_outcome"]
    for variable in body["variables"]:
        assert variable["known_at_anchor"] == (variable["name"] != "harvest_outcome")
        assert variable["operational_meaning"]
        assert variable["derivation"]
    assert body["severe_imbalance_ratio"] == 10.0
    assert body["rare_class_minimum_rows"] == 5


# -- a rule is versioned, and immutable ---------------------------------------


def test_writing_a_rule_assigns_identity_the_client_cannot_supply(
    client: TestClient,
) -> None:
    """The hash and version come from the server.

    A client that computed the hash would be reimplementing the canonical
    encoding; one that guessed it would file a rule under a false identity.
    """
    definition = _write_rule(client)
    assert definition["version"] == 1
    assert definition["specification_hash"].startswith("sha256:")
    assert definition["supersedes_definition_id"] is None
    assert definition["short_hash"] == definition["specification_hash"][7:19]
    # Derived, not supplied: the class names come from the criteria.
    assert definition["variables_used"] == [LOSS]
    assert definition["label_basis"] == "prospective"
    assert definition["class_names"] == ["watch", "act now"]


def test_reposting_an_unchanged_rule_does_not_advance_the_version(
    client: TestClient,
) -> None:
    """A version that changes no label is a revision nobody made.

    Returning 200 and the existing document is the only honest answer: a history
    containing a phantom revision misrepresents how often the rule was
    reconsidered.
    """
    first = _write_rule(client)
    response = client.post("/api/risk/definitions", json=DILUTION_RULE)
    assert response.status_code == 200
    body = response.json()
    assert body["created"] is False
    assert body["reason"] == "identical_definition_already_stored"
    assert body["definition"]["version"] == 1
    assert body["definition"]["definition_id"] == first["definition_id"]


def test_changing_a_threshold_writes_a_new_linked_version(
    client: TestClient,
) -> None:
    """A revision is additive, and says what it supersedes.

    The old document stays exactly as written, so a model fitted against version 1
    remains describable as "fitted against version 1" after version 2 exists.
    """
    first = _write_rule(client)
    second = _write_rule(
        client,
        criteria=[
            {
                "variable": LOSS,
                "operator": "gte",
                "threshold": 9.0,
                "label": "act now",
                "rationale": "Nine g/L is the action limit agreed with operations.",
            }
        ],
    )
    assert second["version"] == 2
    assert second["supersedes_definition_id"] == first["definition_id"]
    assert second["specification_hash"] != first["specification_hash"]

    history = client.get("/api/risk/definitions/name/monsoon dilution/versions").json()
    assert [entry["version"] for entry in history["versions"]] == [1, 2]
    assert history["versions"][0]["criteria"][0]["threshold"] == 0.0
    assert history["versions"][1]["criteria"][0]["threshold"] == 9.0


def test_a_readonly_rule_reads_back_by_the_hash_a_dataset_cites(
    client: TestClient,
) -> None:
    """A stored manifest names a rule by hash, so a hash must resolve.

    This is the check that a manifest still describes a rule that exists.
    """
    definition = _write_rule(client)
    response = client.get(f"/api/risk/definitions/{definition['specification_hash']}")
    assert response.status_code == 200
    assert response.json()["definition_id"] == definition["definition_id"]


# -- the rule is checked before it is stored ----------------------------------


@pytest.mark.parametrize(
    ("criterion", "expected"),
    [
        (
            {"variable": "moon_phase", "operator": "gte", "threshold": 1.0,
             "label": "act now", "rationale": "r"},
            "unknown risk variable",
        ),
        (
            {"variable": LOSS, "operator": "gte", "threshold": 5.0, "unit": "mm",
             "label": "act now", "rationale": "r"},
            "measured in",
        ),
        (
            {"variable": "harvest_outcome", "operator": "gt", "threshold": "lost",
             "label": "act now", "rationale": "r"},
            "no order",
        ),
        (
            {"variable": LOSS, "operator": "gte", "threshold": "5.0",
             "label": "act now", "rationale": "r"},
            "must be a number",
        ),
    ],
)
def test_a_criterion_contradicting_the_catalogue_is_refused(
    client: TestClient, criterion: dict[str, Any], expected: str
) -> None:
    """A rule the catalogue contradicts is a 422, not a stored rule.

    Each of these would otherwise produce a class distribution that is a fact
    about the mistake rather than about any pan.
    """
    response = client.post(
        "/api/risk/definitions",
        json={**DILUTION_RULE, "criteria": [criterion]},
    )
    assert response.status_code == 422, response.text
    assert expected in _detail(response)


def test_a_criterion_naming_an_undeclared_class_is_refused(
    client: TestClient,
) -> None:
    """A stored rule can never be unevaluable.

    A criterion pointing at a class the definition does not declare would make the
    rule complete-looking and unusable.
    """
    response = client.post(
        "/api/risk/definitions",
        json={
            **DILUTION_RULE,
            "criteria": [
                {
                    "variable": LOSS,
                    "operator": "gte",
                    "threshold": 5.0,
                    "label": "escalate to the regional office",
                    "rationale": "r",
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "not one of this definition's classes" in _detail(response)


@pytest.mark.parametrize(
    "classes",
    [
        # A gap in the ordering, so "the most severe matching criterion" is
        # undefined.
        [{"name": "watch", "rank": 0, "description": "a"},
         {"name": "act now", "rank": 2, "description": "b"}],
        # One class, which does not discriminate.
        [{"name": "watch", "rank": 0, "description": "a"}],
    ],
)
def test_an_incoherent_vocabulary_is_refused(
    client: TestClient, classes: list[dict[str, Any]]
) -> None:
    response = client.post(
        "/api/risk/definitions", json={**DILUTION_RULE, "classes": classes}
    )
    assert response.status_code == 422


def test_a_criterion_without_a_rationale_is_refused(client: TestClient) -> None:
    """The reason is the part a reviewer can disagree with.

    A threshold with no stated reason is a number a future reader cannot evaluate.
    """
    response = client.post(
        "/api/risk/definitions",
        json={
            **DILUTION_RULE,
            "criteria": [
                {
                    "variable": LOSS,
                    "operator": "gte",
                    "threshold": 5.0,
                    "label": "act now",
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "rationale" in _detail(response)


# -- a retrospective rule cannot masquerade as a forecast ---------------------


def test_a_rule_reading_the_harvest_outcome_is_marked_retrospective(
    client: TestClient,
) -> None:
    """The basis is derived from the variables, not taken on trust.

    A rule reading a post-anchor measurement is filed as retrospective whether or
    not the author said so, so a reader never has to reconstruct the causal
    argument from a list of names.
    """
    definition = _write_rule(
        client,
        name="outcome review",
        criteria=[
            {
                "variable": "harvest_outcome",
                "operator": "eq",
                "threshold": "lost",
                "label": "act now",
                "rationale": "A recorded 'lost' is the outcome worth reviewing.",
            }
        ],
    )
    assert definition["label_basis"] == "retrospective"
    assert definition["variables_used"] == ["harvest_outcome"]


def test_a_retrospective_rule_cannot_label_a_dataset_without_acknowledgement(
    client: TestClient, seeded: TestClient
) -> None:
    """The refusal happens at build time, and names the way out.

    A retrospective label that looks prospective will be quoted as a forecast, and
    the dataset build is the last point at which that can be caught.
    """
    definition = _write_rule(
        client,
        name="outcome review",
        criteria=[
            {
                "variable": "harvest_outcome",
                "operator": "eq",
                "threshold": "lost",
                "label": "act now",
                "rationale": "A recorded 'lost' is the outcome worth reviewing.",
            }
        ],
    )
    response = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    )
    assert response.status_code == 422
    detail = _detail(response)
    assert "allow_retrospective_label" in detail
    assert "harvest_outcome" in detail


def test_an_acknowledged_retrospective_rule_builds_and_says_so(
    client: TestClient, seeded: TestClient
) -> None:
    """Acknowledged, it is allowed, and the dataset is labelled retrospective.

    The point is not to forbid describing what happened. It is to make sure nobody
    mistakes a description for a forecast.
    """
    definition = _write_rule(
        client,
        name="outcome review",
        criteria=[
            {
                "variable": "harvest_outcome",
                "operator": "eq",
                "threshold": "lost",
                "label": "act now",
                "rationale": "A recorded 'lost' is the outcome worth reviewing.",
            }
        ],
    )
    response = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
                "allow_retrospective_label": True,
            },
            "features": RISK_FEATURES,
        },
    )
    assert response.status_code == 201, response.text
    target = response.json()["dataset"]["target"]
    assert target["allow_retrospective_label"] is True
    assert target["risk_specification_hash"] == definition["specification_hash"]


# -- the label travels with its rule and its source variables -----------------


def test_a_risk_dataset_requires_a_rule(client: TestClient, seeded: TestClient) -> None:
    """No rule, no labels.

    The alternative is a severity the module invented, and a class distribution
    that is a fact about this codebase rather than about any pan.
    """
    response = client.post(
        "/api/datasets/",
        json={
            "target": {"name": "operational_risk", "horizon_hours": 24.0},
            "features": RISK_FEATURES,
        },
    )
    assert response.status_code == 422
    assert "risk_definition_id is required" in _detail(response)


def test_a_measured_target_refuses_a_rule_reference(
    client: TestClient, seeded: TestClient
) -> None:
    """A measured label is not attributable to a rule that did not produce it."""
    definition = _write_rule(client)
    response = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "post_rain_salinity_g_l",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    )
    assert response.status_code == 422
    assert "no meaning for it" in _detail(response)


def test_every_row_records_its_rule_version_and_source_variables(
    client: TestClient, seeded: TestClient
) -> None:
    """The provenance the module exists to guarantee.

    A label without its rule is unattributable, and a rule without its source
    variables cannot be re-checked against the pan it fired on.
    """
    definition = _write_rule(client)
    response = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    rows = body["preview_rows"]
    assert rows, "the seeded pan should produce at least one row"
    assert len(rows) == body["dataset"]["report"]["record_count"]

    for row in rows:
        assert row["risk_definition_id"] == definition["definition_id"]
        assert row["risk_definition_version"] == definition["version"]
        assert row["risk_specification_hash"] == definition["specification_hash"]
        assert row["risk_label_basis"] == "prospective"
        # The rule read exactly one variable, and that it is named on the row.
        assert LOSS in row["risk_source_variables"]
        # A matched criterion is written out, so the cell is readable alone.
        if row["is_labelled"]:
            assert f"{LOSS} gte" in row["risk_matched_criteria"]
        else:
            assert row["risk_unlabelled_reason"]


def test_a_row_the_rule_cannot_label_is_unlabelled_not_defaulted(
    client: TestClient, seeded: TestClient
) -> None:
    """No fallback, no invented severity.

    The rule declares no ``unmatched_label``, so a row whose criteria all miss
    keeps no class. Assigning it the lowest one would be the module making a
    judgement the researcher never made, on exactly the rows where the data is
    thinnest.
    """
    definition = _write_rule(
        client,
        criteria=[
            {
                "variable": LOSS,
                "operator": "gte",
                # Unreachable: the loss on these pans is a fraction of a g/L.
                "threshold": 500.0,
                "label": "act now",
                "rationale": "A loss this large has not been seen in the record.",
            }
        ],
    )
    response = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    for row in body["preview_rows"]:
        assert row["is_labelled"] is False
        assert row["label_operational_risk"] is None
        assert "fallback" in row["risk_unlabelled_reason"]
    assert body["dataset"]["report"]["labelled_count"] == 0


def test_a_declared_fallback_class_is_used_when_no_criterion_matches(
    client: TestClient, seeded: TestClient
) -> None:
    """The fallback is the researcher's own class, and it is recorded as such.

    A rule may say "anything below my action limit is 'watch'", and that is its
    call. The module does not add the class; it only refuses to add one silently.
    """
    definition = _write_rule(
        client,
        unmatched_label="watch",
        criteria=[
            {
                "variable": LOSS,
                "operator": "gte",
                "threshold": 500.0,
                "label": "act now",
                "rationale": "A loss this large has not been seen in the record.",
            }
        ],
    )
    response = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["dataset"]["report"]["labelled_count"] > 0
    for row in body["preview_rows"]:
        if row["is_labelled"]:
            assert row["label_operational_risk"] == "watch"
            # The fallback assigned it, so no criterion fired.
            assert row["risk_matched_criteria"] == ""


def test_the_audit_reports_the_derived_label_check(
    client: TestClient, seeded: TestClient
) -> None:
    """A computed label has no measurement instant, so the audit says what it did check.

    "The target was measured at or after the horizon" is meaningless for a label
    no measurement produced, and a report that still claimed it would be asserting
    a check that never ran.
    """
    definition = _write_rule(client)
    response = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    )
    assert response.status_code == 201, response.text
    audit = response.json()["dataset"]["report"]["leakage_audit"]
    assert audit["violations"] == []
    assert audit["rows_checked"] > 0
    assert audit["rows_passed"] == audit["rows_checked"]
    assert any("known at the anchor" in check for check in audit["checks"])
    assert any("definition, version and source" in check for check in audit["checks"])


def test_two_datasets_from_two_rules_are_different_datasets(
    client: TestClient, seeded: TestClient
) -> None:
    """The rule is part of the dataset's identity.

    Two datasets built from the same pans by different rules carry different
    labels, and sharing a content hash would let an experiment cite one and mean
    the other.
    """
    first = _write_rule(client)
    second = _write_rule(
        client,
        criteria=[
            {
                "variable": LOSS,
                "operator": "gte",
                "threshold": 0.5,
                "label": "act now",
                "rationale": "Half a g/L of expected loss is worth acting on.",
            }
        ],
    )

    def build(definition: dict[str, Any]) -> str:
        response = client.post(
            "/api/datasets/",
            json={
                "target": {
                    "name": "operational_risk",
                    "horizon_hours": 24.0,
                    "risk_definition_id": definition["definition_id"],
                },
                "features": RISK_FEATURES,
            },
        )
        # 201 the first time, 200 on an identical rebuild: same specification over
        # the same records is the same dataset, not a second copy.
        assert response.status_code in (200, 201), response.text
        return response.json()["dataset"]["content_hash"]

    assert build(first) != build(second)
    # And each is reproducible from its own rule.
    assert build(first) == build(first)


# -- the distribution is available before training ---------------------------


def test_distribution_reports_the_shape_of_a_rules_labels(
    client: TestClient, seeded: TestClient
) -> None:
    """The document a researcher reads before a model is fitted.

    It says which classes were never observed, how lopsided the split is, and
    whether the distribution supports training at all — and it says that nothing
    was trained.
    """
    definition = _write_rule(client)
    response = client.post(
        f"/api/risk/definitions/{definition['definition_id']}/distribution",
        json={"horizon_hours": 24.0},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["trained_model"] is None
    assert body["record_count"] > 0
    assert body["definition"]["definition_id"] == definition["definition_id"]

    distribution = body["distribution"]
    assert distribution["label_basis"] == "prospective"
    # Every declared class appears, even one nothing landed in.
    assert {share["label"] for share in distribution["classes"]} == {"watch", "act now"}
    for share in distribution["classes"]:
        assert share["description"]
    assert distribution["unlabelled_count"] + distribution["labelled_count"] == (
        body["record_count"]
    )
    assert distribution["note"]


def test_distribution_warns_about_a_degenerate_split(
    client: TestClient, seeded: TestClient
) -> None:
    """One populated class supports no model, and says so.

    An accuracy figure on such a split would be a statement about the rule matching
    one class, not about a model.
    """
    definition = _write_rule(
        client,
        criteria=[
            {
                "variable": LOSS,
                "operator": "gte",
                "threshold": 0.0,
                "label": "act now",
                "rationale": "Any forecast dilution at all warrants a decision.",
            }
        ],
    )
    response = client.post(
        f"/api/risk/definitions/{definition['definition_id']}/distribution",
        json={"horizon_hours": 24.0},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    distribution = body["distribution"]

    assert distribution["populated_class_count"] == 1
    assert body["is_usable_for_training"] is False
    assert "watch" in distribution["unobserved_classes"]
    assert any("landed in one class" in warning for warning in distribution["warnings"])


def test_distribution_reports_no_rows_rather_than_zero_of_them(
    client: TestClient,
) -> None:
    """An empty store is described, not summarised as a class balance of zero.

    Every share is null rather than 0.0, because zero would be indistinguishable
    from a measurement.
    """
    definition = _write_rule(client)
    response = client.post(
        f"/api/risk/definitions/{definition['definition_id']}/distribution",
        json={"horizon_hours": 24.0},
    )
    assert response.status_code == 200, response.text
    distribution = response.json()["distribution"]
    assert distribution["row_count"] == 0
    assert distribution["labelled_count"] == 0
    assert distribution["imbalance_ratio"] is None
    assert all(share["percent"] is None for share in distribution["classes"])
    assert "no distribution to report" in distribution["note"]


def test_distribution_warns_that_a_retrospective_rule_cannot_forecast(
    client: TestClient, seeded: TestClient
) -> None:
    """The warning travels with the numbers, not only with the definition.

    A distribution of retrospective labels looks exactly like a distribution of
    forecastable ones unless something says otherwise at the point of reading it.
    """
    definition = _write_rule(
        client,
        name="outcome review",
        criteria=[
            {
                "variable": "harvest_outcome",
                "operator": "eq",
                "threshold": "lost",
                "label": "act now",
                "rationale": "A recorded 'lost' is the outcome worth reviewing.",
            }
        ],
    )
    response = client.post(
        f"/api/risk/definitions/{definition['definition_id']}/distribution",
        json={"horizon_hours": 24.0},
    )
    assert response.status_code == 200, response.text
    distribution = response.json()["distribution"]
    assert distribution["label_basis"] == "retrospective"
    assert any("cannot be used to" in warning for warning in distribution["warnings"])


def test_targets_endpoint_explains_how_a_rule_becomes_a_label(
    client: TestClient,
) -> None:
    """The two modules meet at one target, and the endpoint says so.

    A rule here does not produce a risk score; it produces the label column of a
    Module 07 dataset.
    """
    body = client.get("/api/risk/targets").json()
    assert body["rule_backed_target"] == "operational_risk"
    derived = [t for t in body["research_targets"] if t["derived_from_rule"]]
    assert [t["name"] for t in derived] == ["operational_risk"]
    assert all(t["derived_from_rule"] is False for t in body["research_targets"]
               if t["name"] != "operational_risk")


# -- two failure modes that only show up on awkward data ---------------------


def test_an_unobserved_class_disqualifies_an_otherwise_healthy_split(
    client: TestClient, seeded: TestClient
) -> None:
    """Two populated classes are not enough when a third declared class got nothing.

    A 50/50 split between two of three classes passes every numeric test, so the
    report would look healthy while being silent on a third of the rule's own
    vocabulary. The class is named in the warnings so the gap is visible rather
    than merely counted.
    """
    definition = _write_rule(
        client,
        name="three way",
        classes=[
            {"name": "watch", "rank": 0, "description": "Monitor the pan."},
            {"name": "act now", "rank": 1, "description": "Flush early."},
            {"name": "escalate", "rank": 2, "description": "Call the regional office."},
        ],
        criteria=[
            {
                "variable": LOSS,
                "operator": "gte",
                # Inside the range of losses on these rows, which run from about
                # 18 to 31 g/L, so the criterion fires on two of them and the
                # fallback covers the other two.
                "threshold": 22.0,
                "label": "act now",
                "rationale": "Half a g/L of expected loss is worth acting on.",
            }
        ],
        unmatched_label="watch",
    )
    response = client.post(
        f"/api/risk/definitions/{definition['definition_id']}/distribution",
        json={"horizon_hours": 24.0},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    distribution = body["distribution"]

    assert distribution["populated_class_count"] == 2
    assert distribution["unobserved_classes"] == ["escalate"]
    assert body["is_usable_for_training"] is False
    assert any("escalate" in warning for warning in distribution["warnings"])


# -- the evaluator, directly, on rows an HTTP fixture cannot easily describe ---


def _stored_definition(
    *, criteria: list[dict[str, Any]], classes: list[dict[str, Any]] | None = None
) -> Any:
    """Persist a rule and read it back, the way a dataset build would find it.

    Written through the service rather than constructed by hand, so the definition
    under test is the one the service would actually store, with the same
    validation, hash and derived fields a build would resolve.
    """
    service = RiskDefinitionService(FakeDatabase())
    draft = RiskDefinitionDraft.model_validate(
        {
            "name": "probe",
            "authored_by": "a researcher",
            "operational_rationale": "A rule written to be probed.",
            "classes": classes
            or [
                {"name": "watch", "rank": 0, "description": "Monitor the pan."},
                {"name": "act now", "rank": 1, "description": "Flush early."},
            ],
            "criteria": criteria,
        }
    )
    return asyncio.run(service.create(draft)).definition


def test_the_fallback_is_withheld_when_the_rule_could_not_read_the_row() -> None:
    """A fallback means "no criterion fired", which needs the row to have been read.

    Where this regressed: a criterion whose variable held no value was skipped
    without being recorded anywhere, so the rule looked fully evaluated, the
    fallback fired, and a class was assigned to precisely the rows whose data was
    too thin to evaluate -- the rows where assuming a severity is least defensible.
    The fallback is now withheld, and the row says the rule could not read it.
    """
    definition = _stored_definition(
        criteria=[
            {
                    # A threshold no row reaches, so the only question left is
                    # whether the fallback may answer for it.
                    "variable": LOSS,
                    "operator": "gte",
                    "threshold": 500.0,
                    "label": "act now",
                    "rationale": "A loss this large has not been seen in the record.",
                },
                {
                    # Never supplied for a row, standing in for any figure the
                    # record set happens not to carry.
                    "variable": "harvest_outcome",
                    "operator": "eq",
                    "threshold": "lost",
                    "label": "act now",
                    "rationale": "A recorded loss is the outcome worth reviewing.",
            },
        ]
    )
    evaluation = evaluate_definition(
        definition,
        {
            LOSS: RiskSourceVariable(
                variable=LOSS,
                value=12.0,
                unit="g/L",
                leakage_class=LeakageClass.AS_OF,
                reason=None,
            )
        },
    )

    assert evaluation.is_labelled is False
    assert evaluation.label is None
    assert "withheld" in evaluation.unlabelled_reason
    # And the two inputs are both named, so the row is auditable.
    sources = {source.variable: source for source in evaluation.source_variables}
    assert sources[LOSS].value == 12.0
    assert sources["harvest_outcome"].value is None


def test_a_fully_read_row_still_gets_its_fallback() -> None:
    """The fix withholds the fallback only where the rule could not read the row.

    A rule that declares a fallback and reads every variable it needs must still
    use it, or the change would have made declared fallbacks unusable.
    """
    definition = _stored_definition(
        criteria=[
            {
                    "variable": LOSS,
                    "operator": "gte",
                    "threshold": 500.0,
                    "label": "act now",
                    "rationale": "A loss this large has not been seen in the record.",
            }
        ]
    )
    definition = definition.model_copy(update={"unmatched_label": "watch"})
    evaluation = evaluate_definition(
        definition,
        {
            LOSS: RiskSourceVariable(
                variable=LOSS,
                value=12.0,
                unit="g/L",
                leakage_class=LeakageClass.AS_OF,
                reason=None,
            )
        },
    )
    assert evaluation.is_labelled is True
    assert evaluation.label == "watch"
    assert list(evaluation.matched_criteria) == []


def test_rebuilding_a_rule_derived_dataset_reproduces_it(
    client: TestClient, seeded: TestClient
) -> None:
    """A rebuild must reload the rule, not re-derive the rows without it.

    Where this regressed: the manifest names its rule by id and hash, but the
    specification was rebuilt from the manifest without the rule, so every rebuild
    of a rule-derived dataset was refused outright -- the reproducibility check was
    impossible for exactly the datasets this module produces. The rule is now
    reloaded and its specification hash checked against the one the manifest
    recorded, so the same rule labels the same rows and the verdict means something.
    """
    definition = _write_rule(client)
    build = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    )
    assert build.status_code == 201, build.text
    built = build.json()
    assert built["dataset"]["report"]["labelled_count"] > 0

    response = client.post(f"/api/datasets/{built['dataset']['dataset_id']}/rebuild")
    assert response.status_code == 200, response.text
    rebuilt = response.json()
    assert rebuilt["rebuild"]["unchanged"] is True
    assert rebuilt["dataset"]["content_hash"] == built["dataset"]["content_hash"]
    # And the rows came back labelled, by the same rule, at the same version.
    assert rebuilt["dataset"]["report"]["labelled_count"] == built["dataset"]["report"][
        "labelled_count"
    ]
    for row in rebuilt["preview_rows"]:
        assert row["risk_definition_version"] == definition["version"]
        assert row["risk_specification_hash"] == definition["specification_hash"]


def test_each_manifest_rebuilds_against_its_own_rule(
    client: TestClient, seeded: TestClient
) -> None:
    """Two rule-derived datasets both rebuild unchanged, each against its own rule.

    A rebuild that resolved "the current rule" rather than "the rule this manifest
    cites" would re-label the older dataset with the newer vocabulary and report
    drift, which is a false alarm about data that has not moved at all.
    """
    first = _write_rule(client)
    second = _write_rule(
        client,
        criteria=[
            {
                "variable": LOSS,
                "operator": "gte",
                "threshold": 22.0,
                "label": "act now",
                "rationale": "Twenty-two g/L is the limit agreed with operations.",
            }
        ],
    )

    def build(definition: dict[str, Any]) -> dict[str, Any]:
        response = client.post(
            "/api/datasets/",
            json={
                "target": {
                    "name": "operational_risk",
                    "horizon_hours": 24.0,
                    "risk_definition_id": definition["definition_id"],
                },
                "features": RISK_FEATURES,
            },
        )
        assert response.status_code in (200, 201), response.text
        return response.json()["dataset"]

    older = build(first)
    newer = build(second)
    assert older["dataset_id"] != newer["dataset_id"]

    for dataset, definition in ((older, first), (newer, second)):
        response = client.post(f"/api/datasets/{dataset['dataset_id']}/rebuild")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["rebuild"]["unchanged"] is True
        assert body["dataset"]["content_hash"] == dataset["content_hash"]
        assert body["dataset"]["target"]["risk_definition_id"] == definition["definition_id"]


def test_a_manifest_citing_a_hash_no_stored_rule_produces_is_refused(
    client: TestClient, seeded: TestClient, database: FakeDatabase
) -> None:
    """A manifest whose rule no longer hashes to what it recorded cannot be rebuilt.

    Definitions are immutable, so a stored hash that disagrees with the stored rule
    means the two no longer describe the same thing. Rebuilding anyway would
    re-label the rows with a rule nobody chose and report it as a new dataset, and
    the earlier experiment would stay citable against a rule it never named. The
    refusal is the only outcome that keeps the citation honest.
    """
    definition = _write_rule(client)
    dataset = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    ).json()["dataset"]

    # Rewrite the stored manifest so it cites a specification hash that no stored
    # rule produces, as a corrupted or hand-edited manifest would.
    stored = database["research_datasets"].documents()[0]
    assert stored["dataset_id"] == dataset["dataset_id"]
    tampered_target = {
        **stored["target"],
        "risk_specification_hash": "sha256:" + "0" * 64,
    }
    database["research_datasets"]._documents[0] = {
        **stored,
        "target": tampered_target,
    }

    response = client.post(f"/api/datasets/{dataset['dataset_id']}/rebuild")
    assert response.status_code == 422, response.text
    detail = _detail(response)
    assert "no longer agree" in detail
    assert dataset["target"]["risk_specification_hash"] in detail


def test_the_exported_csv_carries_the_rule_provenance(
    client: TestClient, seeded: TestClient
) -> None:
    """A file whose label names a severity must also name the rule that assigned it.

    Where this regressed: the provenance was carried on every row and shown in the
    API preview, but the export's column order was fixed, so the CSV dropped all
    seven columns. The file that actually leaves the system held a severity
    vocabulary stripped of the rule, version, source variables and criteria that
    produced it -- the one thing this module exists to prevent, and invisible in
    the API that the tests had been reading.
    """
    definition = _write_rule(client)
    built = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    ).json()

    response = client.get(f"/api/datasets/{built['dataset']['dataset_id']}/export")
    assert response.status_code == 200, response.text
    lines = response.text.splitlines()
    header = lines[0].split(",")

    for column in (
        "label_operational_risk",
        "risk_definition_id",
        "risk_definition_version",
        "risk_specification_hash",
        "risk_label_basis",
        "risk_source_variables",
        "risk_matched_criteria",
        "risk_unlabelled_reason",
    ):
        assert column in header, f"{column} missing from the exported file"

    # The provenance sits next to the label it qualifies, not at the far end of the
    # file where a reader would treat it as an appendix.
    label_at = header.index("label_operational_risk")
    assert header.index("risk_definition_id") == label_at + 1
    assert header.index("risk_unlabelled_reason") < header.index("is_labelled")

    # And a real cell carries the rule, not just the column.
    row = lines[1].split(",")
    cells = dict(zip(header, row))
    assert cells["risk_definition_id"] == definition["definition_id"]
    assert cells["risk_definition_version"] == "1"
    assert cells["risk_specification_hash"] == definition["specification_hash"]
    assert LOSS in cells["risk_source_variables"]


def test_a_measured_targets_csv_gains_no_risk_columns(
    client: TestClient, seeded: TestClient
) -> None:
    """A measured target is not labelled by a rule, so it cites none.

    The provenance columns are conditional on the target actually being derived.
    Writing them for a measured target would suggest a rule was involved in
    producing a label that no rule produced.
    """
    built = client.post(
        "/api/datasets/",
        json={
            "target": {"name": "post_rain_salinity_g_l", "horizon_hours": 24.0},
            "features": ["current_salinity_g_l", "water_depth_cm"],
        },
    )
    assert built.status_code == 201, built.text
    response = client.get(
        f"/api/datasets/{built.json()['dataset']['dataset_id']}/export"
    )
    assert response.status_code == 200, response.text
    header = response.text.splitlines()[0].split(",")
    assert "risk_definition_id" not in header
    assert "label_post_rain_salinity_g_l" in header


# -- two writers, one version number -----------------------------------------


class _StaleVersionRepository:
    """A repository that reports one version number too few, once.

    The service reads the newest version and adds one, which two concurrent
    writers can both do. This stands in for the loser of that race: its history
    read is stale, so it proposes a version the collection already holds. The
    behaviour under test is what happens next, not the race itself -- a real race
    is a timing accident that a test cannot schedule, and the code path is the
    same either way.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._stale_reads_remaining = 1

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def find(self, query: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        found = await self._inner.find(query, *args, **kwargs)
        if (
            self._stale_reads_remaining
            and query.get("name") == "probe"
            and kwargs.get("sort") == [("version", -1)]
        ):
            self._stale_reads_remaining -= 1
            return found[1:]  # hide the newest, so the caller computes a used number
        return found


def test_a_lost_race_for_a_version_number_retries_instead_of_overwriting(
    client: TestClient, database: FakeDatabase
) -> None:
    """Two writers must not both store "version 2".

    Where this regressed: the version index was not unique and the write treated
    any duplicate-key error as a lost race for the *content* hash. A collision on
    the version was therefore either stored as a second document claiming the same
    version, or reported as a 409 for a rule that had no content conflict at all.
    With the index unique the collision is detectable, and the writer re-reads and
    takes the next number -- so the history reads as a sequence of distinct
    revisions, which is the only thing a version number can mean.
    """
    service = RiskDefinitionService(database)
    draft = RiskDefinitionDraft.model_validate(
        {
            "name": "probe",
            "authored_by": "a researcher",
            "operational_rationale": "A rule written to be probed.",
            "classes": [
                {"name": "watch", "rank": 0, "description": "Monitor the pan."},
                {"name": "act now", "rank": 1, "description": "Flush early."},
            ],
            "criteria": [
                {
                    "variable": LOSS,
                    "operator": "gte",
                    "threshold": 0.0,
                    "label": "act now",
                    "rationale": "Any forecast dilution at all warrants a decision.",
                }
            ],
        }
    )
    first = asyncio.run(service.create(draft))
    assert first.created is True
    assert first.definition.version == 1

    # A second, genuinely different rule under the same name: version 2.
    second = asyncio.run(
        service.create(
            draft.model_copy(
                update={
                    "criteria": [
                        {
                            "variable": LOSS,
                            "operator": "gte",
                            "threshold": 9.0,
                            "label": "act now",
                            "rationale": "Nine g/L is the action limit.",
                        }
                    ]
                }
            )
        )
    )
    assert second.definition.version == 2

    # Now a third write whose history read is stale: it computes version 2 again,
    # which version 2 already holds, and must land on 3 rather than collide.
    stale = RiskDefinitionService(database)
    # One wrapper for the whole write, not one per repository lookup: a fresh
    # wrapper per call would stay perpetually stale and never recover.
    wrapper = _StaleVersionRepository(
        MongoRepository(database, "risk_definitions", RiskDefinition)
    )
    stale._repository = lambda: wrapper
    third = asyncio.run(
        stale.create(
            draft.model_copy(
                update={
                    "criteria": [
                        {
                            "variable": LOSS,
                            "operator": "gte",
                            "threshold": 14.0,
                            "label": "act now",
                            "rationale": "Fourteen g/L is the escalated limit.",
                        }
                    ]
                }
            )
        )
    )
    assert third.created is True
    assert third.definition.version == 3
    # And it supersedes version 2, not version 1.
    assert third.definition.supersedes_definition_id == second.definition.definition_id

    versions = [document["version"] for document in database["risk_definitions"].documents()]
    assert sorted(versions) == [1, 2, 3]


def test_the_dataset_report_names_the_researchers_own_classes(
    client: TestClient, seeded: TestClient
) -> None:
    """A built dataset reports the rule's distribution, not just a class tally.

    The generic target block cannot do this. Its class list comes from the target
    catalogue, which knows nothing about a researcher's vocabulary, so a class the
    rule declared and no row landed in would be invisible there -- and an accuracy
    figure computed over the observed classes alone would look complete. The report
    therefore carries the rule's own distribution alongside the generic one, so the
    dataset and the rule cannot disagree about whether it is worth fitting.
    """
    definition = _write_rule(
        client,
        name="three way",
        classes=[
            {"name": "watch", "rank": 0, "description": "Monitor the pan."},
            {"name": "act now", "rank": 1, "description": "Flush early."},
            {"name": "escalate", "rank": 2, "description": "Call the regional office."},
        ],
        criteria=[
            {
                "variable": LOSS,
                "operator": "gte",
                "threshold": 22.0,
                "label": "act now",
                "rationale": "Twenty-two g/L is the limit agreed with operations.",
            }
        ],
        unmatched_label="watch",
    )
    report = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    ).json()["dataset"]["report"]

    distribution = report["risk_distribution"]
    assert distribution is not None
    assert distribution["definition_id"] == definition["definition_id"]
    assert distribution["definition_version"] == definition["version"]
    assert distribution["specification_hash"] == definition["specification_hash"]
    assert distribution["label_basis"] == "prospective"
    # Every declared class, with the researcher's own description, not a tally.
    assert {share["label"] for share in distribution["classes"]} == {
        "watch",
        "act now",
        "escalate",
    }
    assert distribution["unobserved_classes"] == ["escalate"]
    # And the same warning reaches the dataset's own warning list, so a reader who
    # only ever looks at the build response still sees the gap.
    assert any("escalate" in warning for warning in report["warnings"])


def test_a_measured_targets_report_carries_no_risk_distribution(
    client: TestClient, seeded: TestClient
) -> None:
    """No rule labelled the rows, so there is no rule distribution to report."""
    report = client.post(
        "/api/datasets/",
        json={
            "target": {"name": "post_rain_salinity_g_l", "horizon_hours": 24.0},
            "features": ["current_salinity_g_l", "water_depth_cm"],
        },
    ).json()["dataset"]["report"]
    assert report["risk_distribution"] is None
    assert report["target_distribution"] is not None
