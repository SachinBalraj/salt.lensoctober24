"""Module 16 - predicted-vs-actual validation API tests.

These run against the same in-memory ``FakeDatabase`` the outcome and feedback
suites use, backing the outcome and validation routes, so the whole
request/response contract is exercised without a live MongoDB.

The behaviours worth locking down are the ones that could quietly turn a
validation report into a flattering one:

* a single event, or a series whose measured values never vary, withholds RMSE
  and R-squared with a reason instead of publishing a number;
* the predicted risk is graded against the *same rule* that produced the class,
  re-applied to what actually happened -- so a physics-and-rules advice, a
  prediction with no traceable rule, or a rule the event cannot evaluate is
  an excluded pair with a reason, never a guessed score;
* the operator's decision is the ground truth for the advised action, so advice
  with no recorded decision is counted as uncompared rather than as followed;
* every metric travels beside the count of events that produced it and the
  reasons the rest could not.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, TypeVar

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.outcomes import (
    get_outcome_service,
    get_outcome_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.validation import (
    get_validation_service,
    get_validation_status_service,
)
from app.db.indexes import (
    ML_PREDICTIONS,
    OPERATOR_DECISIONS,
    RECOMMENDATIONS,
    RISK_DEFINITIONS,
)
from app.db.repository import MongoRepository
from app.main import app
from app.models.base import BaseDocument
from app.models.decision_rule import DecisionEvaluation
from app.models.enums import (
    DecisionAction,
    DecisionBasis,
    HarvestOutcome,
    MlTarget,
    OperatorDecisionType,
    RiskCombinator,
    RiskCriterionOperator,
)
from app.models.ml_prediction import MLPrediction
from app.models.operator_decision import OperatorDecision
from app.models.recommendation import (
    DecisionPredictionSummary,
    DecisionSimulationSummary,
    DecisionTwinSummary,
    Recommendation,
)
from app.models.risk_definition import RiskCriterion, RiskDefinition, RiskLabel
from app.services.mass_balance import MASS_BALANCE_MODEL_NAME
from app.services.outcomes import OutcomeService
from app.services.salt_pans import SaltPanService
from app.services.validation import ValidationService
from tests.fakes import FakeDatabase

DocumentT = TypeVar("DocumentT", bound=BaseDocument)

SPEC_HASH = "sha256:" + "b" * 64

VALID_PAN = {
    "pan_id": "KZN-2026-A01",
    "pan_name": "Northern Evaporation Pan",
    "location": "Port Shepstone",
    "area_m2": 10_000.0,
    "normal_depth_cm": 15.5,
    "target_harvest_salinity_g_l": 285.0,
}

EVENT_AT = "2026-03-14T18:00:00Z"

VALID_EVENT = {
    "pan_id": "kzn-2026-a01",
    "event_id": "storm-2026-03-14",
    "timestamp": EVENT_AT,
    "forecast_rainfall_mm": 25.0,
    "actual_rainfall_mm": 35.0,
    "pre_event_salinity_g_l": 280.0,
    "pre_event_depth_cm": 20.0,
    "post_event_salinity_g_l": 250.0,
    "post_event_depth_cm": 24.0,
    "operator_action": "Opened the outlet gate to spread the brine.",
    "harvest_outcome": HarvestOutcome.PARTIAL,
}


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def validation_client(database: FakeDatabase) -> Any:
    """A TestClient backed by memory for the outcome and validation routes."""
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_outcome_service] = lambda: OutcomeService(database)
    app.dependency_overrides[get_outcome_status_service] = (
        lambda: OutcomeService(database)
    )
    app.dependency_overrides[get_validation_service] = lambda: ValidationService(database)
    app.dependency_overrides[get_validation_status_service] = (
        lambda: ValidationService(database)
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def registered_pan(validation_client: TestClient) -> str:
    response = validation_client.post("/api/pans/", json=VALID_PAN)
    assert response.status_code == 201, response.text
    return response.json()["pan_id"]


def _insert(
    database: FakeDatabase,
    collection: str,
    model: type[DocumentT],
    document: DocumentT,
) -> DocumentT:
    return asyncio.run(
        MongoRepository(database, collection, model).insert(document)  # type: ignore[arg-type]
    )


def record(client: TestClient, **overrides: Any) -> Any:
    return client.post("/api/outcomes/", json={**VALID_EVENT, **overrides})


def record_event(client: TestClient, label: str, **overrides: Any) -> Any:
    return record(
        client, event_id=f"{VALID_EVENT['event_id']}-{label}", **overrides
    )


def overview(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/validation/overview", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _twin() -> DecisionTwinSummary:
    return DecisionTwinSummary(
        twin_id="twin-1001",
        as_of=datetime(2026, 3, 13, 8, 0, tzinfo=timezone.utc),
        provenance="observed",
        salinity_g_l=280.0,
        water_depth_cm=20.0,
        forecast_rainfall_mm=25.0,
    )


def _simulation() -> DecisionSimulationSummary:
    return DecisionSimulationSummary(
        run_id="run-1001",
        model_name=MASS_BALANCE_MODEL_NAME,
        rainfall_mm=25.0,
        rainfall_source="forecast",
        initial_salinity_g_l=280.0,
        initial_depth_cm=20.0,
        post_rain_salinity_g_l=243.5,
        post_rain_depth_cm=22.5,
        salinity_drop_g_l=36.5,
    )


def seed_risk_definition(
    database: FakeDatabase,
    *,
    threshold: float = 300.0,
    version: int = 1,
    definition_id: str = "risk-9001",
    name: str = "salinity-loss-risk",
    specification_hash: str = SPEC_HASH,
) -> RiskDefinition:
    definition = RiskDefinition(
        definition_id=definition_id,
        name=name,
        version=version,
        label_column="label_operational_risk",
        classes=[
            RiskLabel(name="low", rank=0, description="The brine stays usable."),
            RiskLabel(name="high", rank=1, description="Loss worth acting on."),
        ],
        criteria=[
            RiskCriterion(
                variable="current_salinity_g_l",
                operator=RiskCriterionOperator.GTE,
                threshold=threshold,
                label="high",
                unit="g/L",
                rationale="A brine at or above the threshold is at loss risk.",
            ),
        ],
        combinator=RiskCombinator.HIGHEST_RANK,
        unmatched_label="low",
        operational_rationale="Test rule whose threshold separates usable brine.",
        specification_hash=specification_hash,
    )
    return _insert(database, RISK_DEFINITIONS, RiskDefinition, definition)


def seed_prediction(
    database: FakeDatabase,
    *,
    pan_id: str,
    definition: RiskDefinition,
    predicted_class: str,
    probability: float | None = 0.9,
    prediction_id: str = "pred-9001",
) -> MLPrediction:
    prediction = MLPrediction(
        prediction_id=prediction_id,
        pan_id=pan_id,
        model_version="1.0",
        model_id="model-16",
        target=MlTarget.OPERATIONAL_RISK,
        predicted_class=predicted_class,
        probability=probability,
        input_snapshot={
            "risk_definition_id": definition.definition_id,
            "risk_definition_version": definition.version,
            "risk_specification_hash": definition.specification_hash,
            "risk_classes": list(definition.class_names),
        },
    )
    return _insert(database, ML_PREDICTIONS, MLPrediction, prediction)


def seed_recommendation(
    database: FakeDatabase,
    *,
    pan_id: str,
    issued_at: datetime,
    recommendation_id: str,
    action: DecisionAction = DecisionAction.TRANSFER_BRINE,
    prediction: MLPrediction | None = None,
) -> Recommendation:
    evidence: dict[str, Any] = {}
    if prediction is not None:
        evidence = {
            "basis": DecisionBasis.PHYSICS_AND_ML,
            "notice": "ML-informed recommendation.",
            "ml": DecisionPredictionSummary(
                prediction_id=prediction.prediction_id,
                model_id=prediction.model_id,
                model_version=prediction.model_version,
                target=prediction.target.value,
                predicted_class=prediction.predicted_class,
                probability=prediction.probability,
                predicted_at=prediction.timestamp,
            ),
        }
    else:
        evidence = {
            "basis": DecisionBasis.PHYSICS_AND_RULES,
            "notice": "Physics/rule-based recommendation - ML unavailable.",
        }
    recommendation = Recommendation(
        pan_id=pan_id,
        title="Transfer brine",
        body="The brine sits at the loss threshold.",
        rationale="The rule says transfer before the front arrives.",
        action=action,
        reason="The brine is at or above the threshold.",
        twin=_twin(),
        simulation=_simulation(),
        evaluation=DecisionEvaluation(
            action=action,
            rule_id="rule-16",
            rule_name="risk-rule",
            rule_version=1,
            specification_hash=SPEC_HASH,
        ),
        rule_id="rule-16",
        rule_name="risk-rule",
        rule_version=1,
        specification_hash=SPEC_HASH,
        issued_at=issued_at,
        expires_at=issued_at + timedelta(days=2),
        **evidence,
    )
    return _insert(database, RECOMMENDATIONS, Recommendation, recommendation)


def seed_operator_decision(
    database: FakeDatabase,
    *,
    pan_id: str,
    recommendation_id: str,
    actual_action: DecisionAction = DecisionAction.TRANSFER_BRINE,
    decision_id: str = "decision-9001",
) -> OperatorDecision:
    decision = OperatorDecision(
        decision_id=decision_id,
        recommendation_id=recommendation_id,
        pan_id=pan_id,
        decision=OperatorDecisionType.ACCEPTED,
        actual_action=actual_action,
        timestamp=datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc),
    )
    return _insert(database, OPERATOR_DECISIONS, OperatorDecision, decision)


# -- the module reports itself live ----------------------------------------


def test_status_says_the_module_is_implemented(validation_client):
    body = validation_client.get("/api/validation/status").json()

    assert body["module"] == "predicted-vs-actual"
    assert body["module_number"] == 16
    assert body["status"] == "implemented"
    assert body["validation"]["available"] is True
    assert "overview" in "\n".join(body["endpoints"])


def test_an_empty_database_reports_zeroed_blocks(validation_client, registered_pan):
    body = overview(validation_client)

    assert body["scope"]["total_outcomes"] == 0
    basin = body["forecast_vs_actual_rainfall"]
    assert basin["n_compared"] == 0
    assert basin["n_excluded"] == 0
    assert basin["mean_absolute_error"]["computed"] is False
    assert basin["mean_absolute_error"]["value"] is None
    assert basin["mean_absolute_error"]["reason"]


# -- continuous comparisons ------------------------------------------------


def test_continuous_blocks_report_gated_metrics_over_comparable_events(
    validation_client, registered_pan, database
):
    """Both salinity blocks inherit the outcomes suite's stored errors."""
    record_event(
        validation_client,
        "wet",
        timestamp="2026-03-14T18:00:00Z",
        forecast_rainfall_mm=20.0,
        actual_rainfall_mm=30.0,
        post_event_salinity_g_l=250.0,
    )
    record_event(
        validation_client,
        "heavier",
        timestamp="2026-03-14T21:00:00Z",
        forecast_rainfall_mm=10.0,
        actual_rainfall_mm=20.0,
        pre_event_salinity_g_l=300.0,
        pre_event_depth_cm=25.0,
        post_event_salinity_g_l=260.0,
    )

    body = overview(validation_client)

    # -- forecast vs actual rainfall (mm) --
    rain = body["forecast_vs_actual_rainfall"]
    assert rain["unit"] == "mm"
    assert rain["n_compared"] == 2
    assert rain["mean_absolute_error"]["value"] == pytest.approx(10.0)
    assert rain["mean_bias_error"]["value"] == pytest.approx(-10.0)
    assert rain["root_mean_squared_error"]["value"] == pytest.approx(10.0)
    assert rain["r_squared"]["computed"] is True

    # -- simulated salinity from the rain that fell (g/L) --
    sim = body["simulated_vs_actual_salinity"]
    sim_errors = [
        280.0 * 0.2 / 0.23 - 250.0,  # wet: 280 at 20cm against 30mm
        300.0 * 0.25 / 0.27 - 260.0,  # heavier: 300 at 25cm against 20mm
    ]
    assert sim["unit"] == "g/L"
    assert sim["n_compared"] == 2
    assert sim["mean_absolute_error"]["value"] == pytest.approx(
        sum(abs(error) for error in sim_errors) / 2
    )
    assert sim["mean_bias_error"]["value"] == pytest.approx(sum(sim_errors) / 2)
    mean_actual = (250.0 + 260.0) / 2
    ss_tot = (250.0 - mean_actual) ** 2 + (260.0 - mean_actual) ** 2
    assert sim["r_squared"]["value"] == pytest.approx(
        1.0 - sum(error * error for error in sim_errors) / ss_tot
    )

    # -- simulated salinity from the forecast (g/L) --
    forecast_sim = body["forecast_simulated_vs_actual_salinity"]
    forecast_errors = [
        280.0 * 0.2 / 0.22 - 250.0,  # wet: forecast 20mm
        300.0 * 0.25 / 0.26 - 260.0,  # heavier: forecast 10mm
    ]
    assert forecast_sim["n_compared"] == 2
    assert forecast_sim["mean_absolute_error"]["value"] == pytest.approx(
        sum(abs(error) for error in forecast_errors) / 2
    )


def test_a_single_event_withholds_rmse_and_r_squared(validation_client, registered_pan):
    record_event(validation_client, "only")

    body = overview(validation_client)
    sim = body["simulated_vs_actual_salinity"]

    assert sim["n_compared"] == 1
    assert sim["mean_absolute_error"]["computed"] is True
    assert sim["root_mean_squared_error"]["computed"] is False
    assert sim["root_mean_squared_error"]["value"] is None
    assert sim["r_squared"]["computed"] is False
    assert "two comparable events" in sim["root_mean_squared_error"]["reason"]
    assert "two comparable events" in sim["r_squared"]["reason"]


def test_r_squared_is_withheld_when_the_measured_values_never_vary(
    validation_client, registered_pan
):
    record_event(
        validation_client,
        "first",
        timestamp="2026-03-14T18:00:00Z",
        actual_rainfall_mm=20.0,
        post_event_salinity_g_l=250.0,
    )
    record_event(
        validation_client,
        "second",
        timestamp="2026-03-14T21:00:00Z",
        actual_rainfall_mm=40.0,
        post_event_salinity_g_l=250.0,
    )

    body = overview(validation_client)
    sim = body["simulated_vs_actual_salinity"]

    assert sim["n_compared"] == 2
    assert sim["root_mean_squared_error"]["computed"] is True
    assert sim["r_squared"]["computed"] is False
    assert "actually vary" in sim["r_squared"]["reason"]


def test_an_excluded_event_is_counted_with_its_reason(validation_client, registered_pan):
    record_event(
        validation_client,
        "measured",
        timestamp="2026-03-14T18:00:00Z",
    )
    record_event(
        validation_client,
        "rain-unmeasured",
        timestamp="2026-03-14T21:00:00Z",
        actual_rainfall_mm=None,
    )
    record_event(
        validation_client,
        "salinity-unmeasured",
        timestamp="2026-03-14T22:00:00Z",
        post_event_salinity_g_l=None,
    )
    record_event(
        validation_client,
        "forecast-unmeasured",
        timestamp="2026-03-14T23:00:00Z",
        forecast_rainfall_mm=None,
    )

    body = overview(validation_client)

    rain = body["forecast_vs_actual_rainfall"]
    assert rain["n_compared"] == 2
    assert rain["n_excluded"] == 2
    rain_reasons = {
        entry["reason"]: entry["count"] for entry in rain["excluded_reasons"]
    }
    assert any("no forecast" in reason.lower() for reason in rain_reasons)
    assert any("no rainfall figure was recorded" in reason.lower() for reason in rain_reasons)

    sim = body["simulated_vs_actual_salinity"]
    assert sim["n_compared"] == 2
    assert sim["n_excluded"] == 2
    sim_reasons = {
        entry["reason"]: entry["count"] for entry in sim["excluded_reasons"]
    }
    assert any("post-event salinity" in reason for reason in sim_reasons)


# -- predicted risk vs recorded outcome -------------------------------------


def test_predicted_risk_is_graded_against_the_same_rule(
    validation_client, registered_pan, database
):
    definition = seed_risk_definition(database)
    pred_high = seed_prediction(
        database,
        pan_id=registered_pan,
        definition=definition,
        predicted_class="high",
        probability=0.9,
        prediction_id="pred-9001",
    )
    pred_low = seed_prediction(
        database,
        pan_id=registered_pan,
        definition=definition,
        predicted_class="low",
        probability=0.7,
        prediction_id="pred-9002",
    )
    seed_recommendation(
        database,
        pan_id=registered_pan,
        recommendation_id="rec-9001",
        issued_at=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
        prediction=pred_high,
    )
    seed_recommendation(
        database,
        pan_id=registered_pan,
        recommendation_id="rec-9002",
        issued_at=datetime(2026, 3, 14, 19, 0, tzinfo=timezone.utc),
        prediction=pred_low,
    )

    record_event(
        validation_client,
        "high",
        timestamp="2026-03-14T18:00:00Z",
        pre_event_salinity_g_l=310.0,
    )
    record_event(
        validation_client,
        "low",
        timestamp="2026-03-14T21:00:00Z",
        pre_event_salinity_g_l=280.0,
    )

    body = overview(validation_client)
    risk = body["predicted_risk_vs_outcome"]

    assert risk["n_compared"] == 2
    assert risk["classes"] == ["low", "high"]
    assert risk["definition_name"] == "salinity-loss-risk"
    assert risk["definition_version"] == 1
    assert risk["accuracy"]["value"] == pytest.approx(100.0)
    assert risk["precision"]["value"] == pytest.approx(100.0)
    assert risk["recall"]["value"] == pytest.approx(100.0)
    assert risk["f1"]["value"] == pytest.approx(100.0)

    matrix = risk["confusion_matrix"]
    assert matrix["labels"] == ["low", "high"]
    assert matrix["matrix"] == [[1, 0], [0, 1]]
    assert matrix["classes_absent_from_evaluation"] == []

    per_class = {row["label"]: row for row in risk["per_class"]}
    assert per_class["low"]["support"] == 1
    assert per_class["high"]["predicted"] == 1
    assert per_class["low"]["f1"]["computed"] is True

    # Both classes appeared and both predictions carried probabilities, so the
    # ranking curves reconstruct P(high) from the stored probability.
    assert risk["roc_auc"]["computed"] is True
    assert risk["roc_auc"]["value"] == pytest.approx(1.0)
    assert risk["pr_auc"]["value"] == pytest.approx(1.0)


def test_a_physics_and_rules_advice_has_nothing_to_grade(
    validation_client, registered_pan, database
):
    """The rule-based path records no model estimate, so the pair is excluded."""
    seed_risk_definition(database)
    seed_recommendation(
        database,
        pan_id=registered_pan,
        recommendation_id="rec-9001",
        issued_at=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
    )
    record_event(validation_client, "high", pre_event_salinity_g_l=310.0)

    body = overview(validation_client)
    risk = body["predicted_risk_vs_outcome"]

    assert risk["n_compared"] == 0
    reasons = {entry["reason"] for entry in risk["excluded_reasons"]}
    assert any("physics and rules" in reason for reason in reasons)


def test_events_without_advice_are_excluded_not_guessed(
    validation_client, registered_pan, database
):
    record_event(validation_client, "lonely", pre_event_salinity_g_l=280.0)

    body = overview(validation_client)
    risk = body["predicted_risk_vs_outcome"]

    assert risk["n_compared"] == 0
    assert risk["accuracy"]["computed"] is False
    reasons = {entry["reason"] for entry in risk["excluded_reasons"]}
    assert any("no recommendation was in force" in reason.lower() for reason in reasons)


def test_a_rule_the_outcome_cannot_evaluate_stays_unlabelled(
    validation_client, registered_pan, database
):
    """A rule asking for an unrecorded variable cannot label the event."""
    definition = seed_risk_definition(database)
    pred_high = seed_prediction(
        database,
        pan_id=registered_pan,
        definition=definition,
        predicted_class="high",
        prediction_id="pred-9001",
    )
    seed_recommendation(
        database,
        pan_id=registered_pan,
        recommendation_id="rec-9001",
        issued_at=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
        prediction=pred_high,
    )
    record_event(
        validation_client, "thins", pre_event_salinity_g_l=None, pre_event_depth_cm=20.0
    )

    body = overview(validation_client)
    risk = body["predicted_risk_vs_outcome"]

    assert risk["n_compared"] == 0
    reasons = {entry["reason"] for entry in risk["excluded_reasons"]}
    assert any("could not be evaluated" in reason for reason in reasons)
    assert any("current_salinity_g_l" in reason for reason in reasons)


def test_pairs_under_a_revised_rule_are_kept_out_of_the_aggregate(
    validation_client, registered_pan, database
):
    v1 = seed_risk_definition(database, threshold=300.0, version=1)
    v2 = seed_risk_definition(
        database,
        threshold=290.0,
        version=2,
        specification_hash="sha256:" + "c" * 64,
    )

    pred_v1 = seed_prediction(
        database,
        pan_id=registered_pan,
        definition=v1,
        predicted_class="high",
        probability=0.9,
        prediction_id="pred-9001",
    )
    pred_v2a = seed_prediction(
        database,
        pan_id=registered_pan,
        definition=v2,
        predicted_class="high",
        probability=0.8,
        prediction_id="pred-9002",
    )
    pred_v2b = seed_prediction(
        database,
        pan_id=registered_pan,
        definition=v2,
        predicted_class="low",
        probability=0.7,
        prediction_id="pred-9003",
    )
    seed_recommendation(
        database,
        pan_id=registered_pan,
        recommendation_id="rec-9001",
        issued_at=datetime(2026, 3, 14, 17, 0, tzinfo=timezone.utc),
        prediction=pred_v1,
    )
    seed_recommendation(
        database,
        pan_id=registered_pan,
        recommendation_id="rec-9002",
        issued_at=datetime(2026, 3, 14, 18, 30, tzinfo=timezone.utc),
        prediction=pred_v2a,
    )
    seed_recommendation(
        database,
        pan_id=registered_pan,
        recommendation_id="rec-9003",
        issued_at=datetime(2026, 3, 14, 19, 30, tzinfo=timezone.utc),
        prediction=pred_v2b,
    )

    # Under v1 the 295 g/L event would be high; under v2 it still is. The
    # headline rule (v2, two events) grades the last two events, and the event
    # under v1 is excluded from the aggregate.
    record_event(
        validation_client,
        "before-revision",
        timestamp="2026-03-14T18:00:00Z",
        pre_event_salinity_g_l=310.0,
    )
    record_event(
        validation_client,
        "after-revision-a",
        timestamp="2026-03-14T19:00:00Z",
        pre_event_salinity_g_l=295.0,
    )
    record_event(
        validation_client,
        "after-revision-b",
        timestamp="2026-03-14T20:00:00Z",
        pre_event_salinity_g_l=285.0,
    )

    body = overview(validation_client)
    risk = body["predicted_risk_vs_outcome"]

    assert risk["definition_version"] == 2
    assert risk["n_compared"] == 2
    assert risk["n_excluded"] == 1
    reasons = {entry["reason"] for entry in risk["excluded_reasons"]}
    assert any("headline rule" in reason for reason in reasons)
    assert any("version 1" in reason for reason in reasons)
    assert any("different version of the risk rule" in reason for reason in reasons)


# -- recommended action vs operator action ----------------------------------


def test_the_advised_action_is_graded_against_the_recorded_decision(
    validation_client, registered_pan, database
):
    recommendation = seed_recommendation(
        database,
        pan_id=registered_pan,
        recommendation_id="rec-9001",
        issued_at=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
        action=DecisionAction.TRANSFER_BRINE,
    )
    seed_operator_decision(
        database,
        pan_id=registered_pan,
        recommendation_id=recommendation.recommendation_id,
        actual_action=DecisionAction.TRANSFER_BRINE,
    )
    record_event(validation_client, "decided")

    body = overview(validation_client)
    action = body["recommended_vs_operator_action"]

    assert action["n_compared"] == 1
    assert action["classes"] == [
        "HARVEST NOW",
        "TRANSFER BRINE",
        "PROTECT / DRAIN",
        "WAIT AND RECHECK",
    ]
    assert action["predicted_distribution"] == {"TRANSFER BRINE": 1}
    assert action["actual_distribution"] == {"TRANSFER BRINE": 1}
    assert action["confusion_matrix"]["labels"] == [
        "HARVEST NOW",
        "TRANSFER BRINE",
        "PROTECT / DRAIN",
        "WAIT AND RECHECK",
    ]
    assert action["confusion_matrix"]["matrix"] == [[0, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0]]
    assert action["accuracy"]["computed"] is False
    assert "two compared events" in action["accuracy"]["reason"]
    # No probability is stored for an action, so no ranking curve is possible.
    assert action["roc_auc"]["computed"] is False
    assert "no probability is stored" in action["roc_auc"]["reason"].lower()


def test_advice_without_a_recorded_decision_is_uncompared(
    validation_client, registered_pan, database
):
    seed_recommendation(
        database,
        pan_id=registered_pan,
        recommendation_id="rec-9001",
        issued_at=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
        action=DecisionAction.TRANSFER_BRINE,
    )
    record_event(validation_client, "undecided")

    body = overview(validation_client)
    action = body["recommended_vs_operator_action"]

    assert action["n_compared"] == 0
    assert action["n_excluded"] == 1
    reasons = {entry["reason"] for entry in action["excluded_reasons"]}
    assert any("no operator decision was recorded" in reason.lower() for reason in reasons)


def test_an_acceptance_without_an_action_is_never_graded_as_its_advice(
    validation_client, registered_pan, database
):
    """Disposition and action stay separate on the grading side too.

    A decision that accepted the advice but recorded no physical action must not
    be counted as the recommended action having been taken. It is excluded with
    its own honest reason, so a research export can see the gap rather than a
    phantom match.
    """
    recommendation = seed_recommendation(
        database,
        pan_id=registered_pan,
        recommendation_id="rec-9002",
        issued_at=datetime(2026, 3, 13, 9, 0, tzinfo=timezone.utc),
        action=DecisionAction.TRANSFER_BRINE,
    )
    seed_operator_decision(
        database,
        pan_id=registered_pan,
        recommendation_id=recommendation.recommendation_id,
        actual_action=None,  # type: ignore[arg-type]
    )
    record_event(validation_client, "decided")

    body = overview(validation_client)
    action = body["recommended_vs_operator_action"]

    assert action["n_compared"] == 0
    assert action["n_excluded"] == 1
    reasons = {entry["reason"] for entry in action["excluded_reasons"]}
    assert any(
        "did not fill in the action taken" in reason.lower()
        for reason in reasons
    )
    assert action["actual_distribution"] == {}


# -- drill-down rows and export ----------------------------------------------


def test_the_pairs_view_shows_excluded_events_with_their_reasons(
    validation_client, registered_pan
):
    record_event(
        validation_client,
        "measured",
        timestamp="2026-03-14T18:00:00Z",
    )
    record_event(
        validation_client,
        "rain-unmeasured",
        timestamp="2026-03-14T21:00:00Z",
        actual_rainfall_mm=None,
    )

    response = validation_client.get(
        "/api/validation/pairs",
        params={"comparison": "forecast_vs_actual_rainfall"},
    )
    assert response.status_code == 200
    body = response.json()

    assert body["comparison"] == "forecast_vs_actual_rainfall"
    assert body["kind"] == "continuous"
    assert body["unit"] == "mm"
    assert body["total_outcomes"] == 2
    by_id = {row["event_id"]: row for row in body["items"]}
    assert by_id["storm-2026-03-14-measured"]["compared"] is True
    assert by_id["storm-2026-03-14-measured"]["signed_error"] == pytest.approx(-10.0)
    excluded = by_id["storm-2026-03-14-rain-unmeasured"]
    assert excluded["compared"] is False
    assert excluded["reason"]


def test_the_pairs_view_can_be_narrowed_to_one_pan(
    validation_client, registered_pan, database
):
    record_event(validation_client, "a")
    response = validation_client.get(
        "/api/validation/pairs",
        params={"comparison": "forecast_vs_actual_rainfall", "pan_id": registered_pan},
    )
    assert response.status_code == 200
    assert response.json()["total_outcomes"] == 1
    assert response.json()["pan_id"] == registered_pan


def test_an_unknown_comparison_key_is_rejected(validation_client):
    response = validation_client.get(
        "/api/validation/pairs", params={"comparison": "made_up"}
    )
    assert response.status_code == 422


def test_export_produces_csv_and_json_of_the_same_rows(
    validation_client, registered_pan
):
    record_event(validation_client, "a")
    record_event(validation_client, "b", actual_rainfall_mm=None)

    csv_response = validation_client.get(
        "/api/validation/export",
        params={
            "comparison": "forecast_vs_actual_rainfall",
            "output_format": "csv",
        },
    )
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    lines = csv_response.text.strip().splitlines()
    assert lines[0] == (
        "outcome_id,pan_id,event_id,timestamp,compared,reason,"
        "predicted_number,actual_number,signed_error"
    )
    assert len(lines) == 3  # header + two events, one excluded

    json_response = validation_client.get(
        "/api/validation/export",
        params={
            "comparison": "forecast_vs_actual_rainfall",
            "output_format": "json",
        },
    )
    assert json_response.status_code == 200
    assert json_response.headers["content-type"].startswith("application/json")
    records = json_response.json()
    assert len(records) == 2
    compared = [row for row in records if row["compared"]]
    assert compared[0]["predicted_number"] == pytest.approx(25.0)