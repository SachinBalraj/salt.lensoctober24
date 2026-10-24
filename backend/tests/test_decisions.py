"""Module 12 - the decision-support engine, over HTTP, from real records.

Everything here is real. A pan is registered, forty observations of it are stored,
a weather forecast is put in force, a twin state is assembled by Module 05, a risk
rule is written through Module 10's API, a model is fitted, evaluated and deployed
by Modules 08/09/17, and a prediction is made by Module 10. Nothing stubs the mass
balance, the rule evaluator or the repository, because a recommendation whose
evidence was faked would be exactly the artefact this module promises not to
produce.

The properties under test, and the test that fails if each is quietly dropped:

**The physics is the mass balance's own arithmetic.**
    :func:`test_the_simulation_is_the_mass_balance_s_own_arithmetic` recomputes
    ``C1 = C0 x V0 / V1`` here from the twin's readings and the pan's area, and
    compares it to what the response reported. A hardcoded salinity, a number read
    from the twin rather than computed, and a figure scaled to look plausible all
    fail it.

**The model result is the stored prediction's, copied.**
    :func:`test_the_ml_result_is_the_stored_prediction_s_own_figures` asserts the
    class, the probability and the snapshot id are the ones Module 10 wrote, and
    :func:`test_the_engine_scores_nothing` asserts no new prediction is created by
    asking for advice. A client that posted to ``/advise`` and got a probability no
    stored prediction held would be unable to audit where it came from.

**A recommendation without a model carries the exact required label.**
    :func:`test_without_a_prediction_the_exact_label_is_reported` asserts the
    verbatim string, ``ml_result: null``, ``basis: physics_and_rules``, and that the
    response still names all seven required things.

**The three kinds of evidence stay in three fields.**
    :func:`test_the_three_kinds_of_evidence_are_reported_apart` checks the
    simulation block is ``is_ml: false`` with a run id, the ML block is null or
    carries a model identity, and every input names its source.

**A rule's own agreement is never presented as a probability.**
    :func:`test_a_recommendation_carries_no_confidence_field` asserts neither the
    response nor the stored record has a top-level confidence, so a rule's decision
    cannot be read beside the model's own.

**The action comes from the rule, and the rule is versioned.**
    :func:`test_each_criterion_fires_on_its_own_pan` walks the four actions;
    :func:`test_a_new_version_supersedes_and_the_old_one_still_reads` writes a
    second version and checks both remain addressable; and
    :func:`test_the_recommendation_cites_the_rule_content_hash` checks the response
    names the hash the rule was stored under.

**Nothing is invented when the evidence is missing.**
    A pan with no twin (:func:`test_a_pan_with_no_twin_state_is_refused`), a twin
    with no depth or salinity (:func:`test_a_twin_the_physics_cannot_run_is_refused`),
    no written rule (:func:`test_no_written_rule_refuses_rather_than_improvising`),
    a rule over a class-only model with no probability
    (:func:`test_a_class_only_model_keeps_the_class_and_the_null_probability`) and a
    rule whose criterion cannot be read
    (:func:`test_a_rule_that_cannot_read_its_input_withholds_its_fallback`) each
    produce a named code and no action.

**A reading without a stored snapshot is not a recorded state.**
    :func:`test_records_plus_a_refreshed_snapshot_resolve_the_dashboard_workflow`
    freezes the exact loop the dashboard had broken: the observation and the
    forecast exist, the twin assembles, and advising still refuses until a
    snapshot is stored — and then resolves the same records into a
    recommendation.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_db, get_optional_db
from app.api.routers.datasets import get_dataset_service
from app.api.routers.decisions import get_decision_rule_service, get_decision_service
from app.api.routers.digital_twin import (
    get_digital_twin_service,
    get_digital_twin_status_service,
)
from app.api.routers.evaluation import get_evaluation_service
from app.api.routers.ml import get_ml_service
from app.api.routers.model_registry import DeploymentService, get_deployment_service
from app.api.routers.observations import (
    get_observation_service,
    get_observation_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.risk import get_prediction_service, get_risk_definition_service
from app.api.routers.weather import get_weather_service, get_weather_status_service
from app.db.collections import (
    ML_PREDICTIONS,
    PAN_OBSERVATIONS,
    RECOMMENDATIONS,
    SALT_PANS,
    SIMULATION_RUNS,
    WEATHER_OBSERVATIONS,
)
from app.db.repository import MongoRepository
from app.main import app
from app.models.decision_rule import DecisionEvaluation
from app.models.enums import DecisionAction, ResearchDataSource, WeatherSource
from app.models.explanation import Explanation
from app.models.ml_prediction import MLPrediction
from app.models.pan_observation import PanObservation
from app.models.recommendation import (
    DecisionSimulationSummary,
    DecisionTwinSummary,
    Recommendation,
)
from app.models.salt_pan import RiskThresholds, SaltPan
from app.models.simulation_run import SimulationRun
from app.models.weather import WeatherObservation
from app.services.datasets import DatasetService
from app.services.decision_catalog import DECISION_VARIABLE_CATALOG
from app.services.decision_reference_rule import REFERENCE_RULE_NAME
from app.services.decision_rules import DecisionRuleService
from app.services.decisions import ML_UNAVAILABLE_LABEL, DecisionEngineService
from app.services.digital_twin import DigitalTwinService
from app.services.evaluation import EvaluationService
from app.services.explain import ShapExplanationService as ExplainService
from app.services.mass_balance import MASS_BALANCE_MODEL_NAME
from app.services.ml import MLService
from app.services.observations import ObservationService
from app.services.prediction import OperationalPredictionService
from app.services.risk_definitions import RiskDefinitionService
from app.services.salt_pans import SaltPanService
from app.services.weather import WeatherService
from tests.fakes import FakeDatabase

#: The exact label this project requires on a recommendation that did not read a
#: model estimate. Written out here as a literal rather than imported from the
#: service, because the test's job is to hold the *wording* rather than to agree
#: with whatever the service currently says: importing the constant would make a
#: paraphrase of a required string pass every assertion in this file.
REQUIRED_ML_UNAVAILABLE_LABEL = (
    "Physics/rule-based recommendation — ML prediction unavailable."
)

PANS = ["KZN-2026-B01", "KZN-2026-B02", "KZN-2026-B03", "KZN-2026-B04"]
PAN = PANS[0]

REGISTERED = datetime(2026, 1, 1, tzinfo=timezone.utc)
FIRST_READING = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
READING_INTERVAL = timedelta(hours=6)
READINGS_PER_PAN = 40

FORECAST_ISSUED = datetime(2026, 2, 28, 0, 0, tzinfo=timezone.utc)
FORECAST_FOR = datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc)

AREA_M2 = 10_000.0
TARGET_SALINITY = 285.0

#: A depth small enough that a lot of rain moves the salinity a long way, so a
#: rule's threshold can separate a diluted pan from a barely-touched one.
DEPTH_CM = 12.0
STARTING_SALINITY = 300.0

#: The rain each pan is forecast to receive. Four pans, four different
#: post-rain salinities, so a rule over the simulated salinity resolves to a
#: different action on each of them. One forecast would make the whole engine
#: untestable in the way that matters: every pan would get the same action for
#: the same uninteresting reason.
FORECASTS_MM = {
    "KZN-2026-B01": 0.0,
    "KZN-2026-B02": 20.0,
    "KZN-2026-B03": 60.0,
    "KZN-2026-B04": 120.0,
}

#: The four actions, in the order the rule ranks them. The ladder is written as a
#: real operational rule would be: too saline to harvest, too diluted to harvest,
#: too deep to hold, otherwise wait.
RULE_ACTIONS = [
    {
        "action": "WAIT AND RECHECK",
        "rank": 0,
        "description": "Nothing in the current state calls for an intervention.",
        "priority": "low",
    },
    {
        "action": "TRANSFER BRINE",
        "rank": 1,
        "description": "Move brine to a holding pan before the harvest window.",
        "priority": "medium",
    },
    {
        "action": "HARVEST NOW",
        "rank": 2,
        "description": "The brine is at or below the harvest salinity and can be taken.",
        "priority": "high",
    },
    {
        "action": "PROTECT / DRAIN",
        "rank": 3,
        "description": "Rain has raised the pan's depth above what it can hold.",
        "priority": "high",
    },
]

RULE_CRITERIA = [
    {
        "variable": "simulated_post_rain_salinity_g_l",
        "operator": "gte",
        "threshold": 296.0,
        "action": "TRANSFER BRINE",
        "rationale": (
            "Above 296 g/L the brine is too concentrated to take at the season's "
            "target and the operator would be harvesting a product that will not "
            "grade, so it is moved instead."
        ),
    },
    {
        "variable": "simulated_post_rain_salinity_g_l",
        "operator": "lte",
        "threshold": 250.0,
        "action": "HARVEST NOW",
        "rationale": (
            "At or below 250 g/L the rain has brought the brine under the target "
            "salinity, which is the window a harvest is for."
        ),
    },
    {
        "variable": "simulated_post_rain_depth_cm",
        "operator": "gt",
        "threshold": 20.0,
        "action": "PROTECT / DRAIN",
        "rationale": (
            "Above 20 cm the pan is deeper than the 12 cm it started at by more "
            "than the forecast can account for safely, and it is drained before the "
            "embankment is overtopped."
        ),
    },
]

RULE: dict[str, Any] = {
    "name": "wet-season harvest ladder",
    "authored_by": "an agronomist",
    "operational_rationale": (
        "Turn the physics and, where a model has spoken, its estimate into one of "
        "four actions an operator can carry out this afternoon, and record which rule "
        "version said so."
    ),
    "actions": RULE_ACTIONS,
    "criteria": RULE_CRITERIA,
    "combinator": "highest_rank",
    "unmatched_action": "WAIT AND RECHECK",
    "uses_ml": False,
}

#: A second rule, differing only in one threshold, used to show that a revision is
#: a new version and that the first version still reads back unchanged.
REVISED_RULE: dict[str, Any] = {
    **RULE,
    "operational_rationale": (
        "As the season's ladder, with the harvest threshold lowered because the "
        "buyer tightened the specification."
    ),
    "criteria": [
        {**criterion, "threshold": 260.0}
        if criterion["action"] == "HARVEST NOW"
        else dict(criterion)
        for criterion in RULE_CRITERIA
    ],
}

#: A rule that reads the model's estimate, for the cases where one exists.
ML_RULE: dict[str, Any] = {
    "name": "model-informed harvest ladder",
    "authored_by": "an agronomist",
    "operational_rationale": (
        "The same ladder, escalated by the deployed model's own risk class: a pan "
        "the model calls high risk is drained even when the physics alone would wait."
    ),
    "actions": RULE_ACTIONS,
    "criteria": [
        *RULE_CRITERIA,
        {
            "variable": "predicted_risk_class",
            "operator": "eq",
            "threshold": "act now",
            "action": "PROTECT / DRAIN",
            "rationale": (
                "The deployed model classes this event as one requiring action, so "
                "the pan is protected rather than left on the physics alone."
            ),
        },
    ],
    "combinator": "highest_rank",
    "unmatched_action": "WAIT AND RECHECK",
    "uses_ml": True,
}

RISK_RULE: dict[str, Any] = {
    "name": "dilution loss",
    "authored_by": "a researcher",
    "operational_rationale": "Rank a pan by the salinity the forecast rain is expected to cost it.",
    "classes": [
        {"name": "watch", "rank": 0, "description": "Monitor the pan; no action."},
        {"name": "act now", "rank": 1, "description": "Flush early to protect the harvest."},
    ],
    "criteria": [
        {
            "variable": "expected_salinity_loss_g_l",
            "operator": "gte",
            "threshold": 2.0,
            "label": "act now",
            "rationale": "Losing more than 2 g/L to forecast rain is a dilution worth acting on.",
        }
    ],
    "unmatched_label": "watch",
}

LOSS = "expected_salinity_loss_g_l"
RISK_FEATURES = [
    "current_salinity_g_l",
    "water_depth_cm",
    "forecast_rainfall_mm",
    "physics_predicted_post_rain_salinity_g_l",
]


# -- fixtures ---------------------------------------------------------------


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def artifacts(tmp_path: Path) -> Path:
    return tmp_path / "models"


@pytest.fixture()
def client(database: FakeDatabase, artifacts: Path) -> Any:
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_observation_service] = lambda: ObservationService(database)
    app.dependency_overrides[get_observation_status_service] = lambda: ObservationService(database)
    app.dependency_overrides[get_weather_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_weather_status_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_dataset_service] = lambda: DatasetService(database)
    app.dependency_overrides[get_ml_service] = lambda: MLService(
        database, artifact_directory=artifacts
    )
    app.dependency_overrides[get_evaluation_service] = lambda: EvaluationService(
        database, artifact_directory=artifacts
    )
    app.dependency_overrides[get_deployment_service] = lambda: DeploymentService(database)
    app.dependency_overrides[get_digital_twin_service] = lambda: DigitalTwinService(database)
    app.dependency_overrides[get_digital_twin_status_service] = lambda: DigitalTwinService(
        database
    )
    app.dependency_overrides[get_risk_definition_service] = lambda: RiskDefinitionService(database)
    app.dependency_overrides[get_prediction_service] = lambda: OperationalPredictionService(
        database,
        artifact_directory=artifacts,
        explainer=ExplainService(database, artifact_directory=artifacts),
    )
    app.dependency_overrides[get_decision_service] = lambda: DecisionEngineService(database)
    app.dependency_overrides[get_decision_rule_service] = lambda: DecisionRuleService(database)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def seed(database: FakeDatabase) -> None:
    """Four pans, forty readings of each, and a forecast in force for all of them.

    The forecast differs per pan on purpose: it is the only thing that makes the
    four pans resolve to four different actions, and a fixture where every pan is
    forecast dry would make the engine's ladder look correct while never testing a
    rung of it.
    """
    pans: MongoRepository[SaltPan] = MongoRepository(database, SALT_PANS, SaltPan)
    observations: MongoRepository[PanObservation] = MongoRepository(
        database, PAN_OBSERVATIONS, PanObservation
    )
    weather: MongoRepository[WeatherObservation] = MongoRepository(
        database, WEATHER_OBSERVATIONS, WeatherObservation
    )

    async def write() -> None:
        for pan_id in PANS:
            await pans.insert(
                SaltPan(
                    pan_id=pan_id,
                    pan_name=f"Pan {pan_id[-3:]}",
                    location="Port Shepstone",
                    area_m2=AREA_M2,
                    normal_depth_cm=DEPTH_CM,
                    target_harvest_salinity_g_l=TARGET_SALINITY,
                    risk_thresholds=RiskThresholds(
                        flood_depth_cm=45.0,
                        dilution_salinity_g_l=250.0,
                        critical_rainfall_24h_mm=80.0,
                        target_flow_cm=1.5,
                    ),
                    created_at=REGISTERED,
                )
            )
            forecast_mm = FORECASTS_MM[pan_id]
            await weather.insert(
                WeatherObservation(
                    weather_id=f"wx-{pan_id}",
                    pan_id=pan_id,
                    timestamp=FORECAST_ISSUED,
                    forecast_for=FORECAST_FOR,
                    rainfall_mm=forecast_mm,
                    rainfall_probability_percent=70.0 if forecast_mm else 10.0,
                    ambient_temperature_c=25.0,
                    humidity_percent=60.0,
                    source="manual",
                    created_at=REGISTERED,
                )
            )
            for step in range(READINGS_PER_PAN):
                drift = 0.4 * ((step % 7) - 3)
                await observations.insert(
                    PanObservation(
                        observation_id=f"obs-{pan_id}-{step:03d}",
                        pan_id=pan_id,
                        timestamp=FIRST_READING + step * READING_INTERVAL,
                        salinity_g_l=round(STARTING_SALINITY + drift, 3),
                        water_depth_cm=DEPTH_CM,
                        brine_temperature_c=30.0,
                        humidity_percent=60.0,
                        ambient_temperature_c=25.0,
                        created_at=REGISTERED,
                    )
                )

    asyncio.run(write())


def refresh_twins(client: TestClient) -> dict[str, Any]:
    states = {}
    for pan_id in PANS:
        response = client.post(f"/api/twin/{pan_id}/refresh")
        assert response.status_code in (200, 201), response.text
        states[pan_id] = response.json()["state"]
    return states


def write_rule(client: TestClient, rule: dict[str, Any] = RULE) -> dict[str, Any]:
    response = client.post("/api/decisions/rules", json=rule)
    assert response.status_code == 201, response.text
    return response.json()["rule"]


def advise(client: TestClient, pan_id: str = PAN, **body: Any) -> Any:
    return client.post("/api/decisions/advise", json={"pan_id": pan_id, **body})


def read(repository: MongoRepository[Any], query: dict[str, Any]) -> list[Any]:
    """Run a repository read outside the app's event loop."""
    return asyncio.run(repository.find(query))


def stored_recommendations(database: FakeDatabase) -> list[Recommendation]:
    return read(MongoRepository(database, RECOMMENDATIONS, Recommendation), {})


def stored_runs(database: FakeDatabase) -> list[SimulationRun]:
    return read(MongoRepository(database, SIMULATION_RUNS, SimulationRun), {})


def stored_predictions(database: FakeDatabase) -> list[MLPrediction]:
    return read(MongoRepository(database, ML_PREDICTIONS, MLPrediction), {})


def build_and_deploy(client: TestClient) -> dict[str, Any]:
    """Write a risk rule, fit a model on it, and deploy it.

    The full Module 08/09/17 lifecycle, because a stored prediction is the only
    thing the engine will ever read as a model's estimate and a hand-written
    prediction row would not prove that.
    """
    definition = client.post("/api/risk/definitions", json=RISK_RULE)
    assert definition.status_code == 201, definition.text
    built = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition.json()["definition"]["definition_id"],
            },
            "features": RISK_FEATURES,
        },
    )
    assert built.status_code == 201, built.text
    dataset = built.json()["dataset"]
    trained = client.post("/api/ml/train", json={"dataset_id": dataset["dataset_id"]})
    assert trained.status_code == 201, trained.text
    model = trained.json()
    report = client.post("/api/evaluation/run", json={"model_id": model["model_id"]})
    assert report.status_code in (200, 201), report.text
    body = {"version": model["version"], "reason": "fielded for the season", "actor": "a researcher"}
    for action, expected in (("validate", 200), ("deploy", 200)):
        response = client.post(f"/api/models/{model['model_id']}/{action}", json=body)
        assert response.status_code == expected, response.text
    return model


def predict(client: TestClient, pan_id: str) -> dict[str, Any]:
    response = client.post("/api/risk/predict", json={"pan_id": pan_id})
    assert response.status_code == 201, response.text
    return response.json()


# -- module status and catalogue -------------------------------------------


def test_the_module_status_states_the_closed_vocabulary(client: TestClient) -> None:
    status = client.get("/api/decisions/")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "implemented"
    assert body["actions"] == [
        "HARVEST NOW",
        "TRANSFER BRINE",
        "PROTECT / DRAIN",
        "WAIT AND RECHECK",
    ]
    assert body["action_vocabulary_is_closed"] is True
    assert body["simulation"]["is_ml"] is False
    assert body["ml_unavailable_label"] == REQUIRED_ML_UNAVAILABLE_LABEL


def test_the_catalogue_names_where_each_quantity_comes_from(client: TestClient) -> None:
    body = client.get("/api/decisions/variables").json()
    sources = {entry["name"]: entry["reads_from"] for entry in body["variables"]}
    assert sources["simulated_post_rain_salinity_g_l"] == "simulation"
    assert sources["predicted_risk_class"] == "prediction"
    assert sources["current_salinity_g_l"] == "twin"
    assert sources["target_harvest_salinity_g_l"] == "pan_registration"
    assert set(body["actions"]) == {action.value for action in DecisionAction}


# -- the rules --------------------------------------------------------------


def test_a_rule_is_stored_with_a_hash_and_the_catalogue_derived_fields(
    client: TestClient,
) -> None:
    rule = write_rule(client)
    assert rule["version"] == 1
    assert rule["specification_hash"].startswith("sha256:")
    assert rule["variables_used"] == ["simulated_post_rain_depth_cm", "simulated_post_rain_salinity_g_l"]
    assert rule["sources_used"] == ["simulation"]
    assert rule["uses_ml"] is False
    assert rule["supersedes_rule_id"] is None


def test_reposting_an_unchanged_rule_returns_the_one_already_stored(
    client: TestClient,
) -> None:
    first = write_rule(client)
    again = client.post("/api/decisions/rules", json=RULE)
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["created"] is False
    assert body["reason"] == "identical_rule_already_stored"
    assert body["rule"]["rule_id"] == first["rule_id"]


def test_a_new_version_supersedes_and_the_old_one_still_reads(client: TestClient) -> None:
    first = write_rule(client)
    second = write_rule(client, REVISED_RULE)
    assert second["version"] == 2
    assert second["supersedes_rule_id"] == first["rule_id"]
    assert second["specification_hash"] != first["specification_hash"]

    history = client.get("/api/decisions/rules/name/wet-season harvest ladder/versions")
    assert history.status_code == 200, history.text
    assert [rule["version"] for rule in history.json()["rules"]] == [1, 2]

    # The first version is still readable and still says what it said.
    original = client.get(f"/api/decisions/rules/{first['rule_id']}").json()
    assert original["version"] == 1
    assert original["supersedes_rule_id"] is None


def test_a_criterion_naming_an_uncatalogued_variable_is_refused(client: TestClient) -> None:
    broken = {
        **RULE,
        "criteria": [
            {
                "variable": "weather_vibes",
                "operator": "gt",
                "threshold": 1.0,
                "action": "HARVEST NOW",
                "rationale": "A variable this project does not measure.",
            }
        ],
    }
    response = client.post("/api/decisions/rules", json=broken)
    assert response.status_code == 422, response.text
    assert "weather_vibes" in response.json()["detail"]


def test_a_criterion_whose_unit_contradicts_the_catalogue_is_refused(
    client: TestClient,
) -> None:
    broken = {
        **RULE,
        "criteria": [
            {
                **RULE_CRITERIA[0],
                "threshold": 296.0,
                "unit": "mm",
            }
        ],
    }
    response = client.post("/api/decisions/rules", json=broken)
    assert response.status_code == 422, response.text
    assert "mm" in response.json()["detail"] and "g/L" in response.json()["detail"]


def test_a_numeric_criterion_given_a_string_threshold_is_refused(
    client: TestClient,
) -> None:
    """A threshold of '296' is not the number 296, and must not be read as it.

    The alternative is worse than a refusal: the comparison would either be reported
    as an unusable input at evaluation time, when an operator is waiting, or -- if
    anything coerced the string to a number -- would fire on a comparison nobody
    wrote. Either way the stored rule reads as a decision that was considered.
    """
    for operator in ("gt", "gte", "lt", "lte", "eq", "neq"):
        broken = {
            **RULE,
            "criteria": [
                {
                    **RULE_CRITERIA[0],
                    "operator": operator,
                    "threshold": "296.0",
                }
            ],
        }
        response = client.post("/api/decisions/rules", json=broken)
        assert response.status_code == 422, (operator, response.text)
        assert "numeric" in response.json()["detail"]
        assert "296.0" in response.json()["detail"]


def test_a_rule_whose_uses_ml_flag_contradicts_its_criteria_is_refused(
    client: TestClient,
) -> None:
    lying = {**ML_RULE, "uses_ml": False}
    response = client.post("/api/decisions/rules", json=lying)
    assert response.status_code == 422, response.text
    assert "uses_ml" in response.json()["detail"]


def test_a_criterion_pointing_at_an_undeclared_action_is_refused(client: TestClient) -> None:
    narrow = {**RULE, "actions": RULE_ACTIONS[:1], "unmatched_action": "WAIT AND RECHECK"}
    response = client.post("/api/decisions/rules", json=narrow)
    assert response.status_code == 422, response.text


def test_ordering_a_categorical_variable_is_refused(client: TestClient) -> None:
    broken = {
        **ML_RULE,
        "criteria": [
            {
                "variable": "predicted_risk_class",
                "operator": "gt",
                "threshold": "act now",
                "action": "PROTECT / DRAIN",
                "rationale": "A risk class is a label, not an ordered quantity.",
            }
        ],
    }
    response = client.post("/api/decisions/rules", json=broken)
    assert response.status_code == 422, response.text


# -- the answer -------------------------------------------------------------


def test_the_simulation_is_the_mass_balance_s_own_arithmetic(
    client: TestClient, database: FakeDatabase
) -> None:
    seed(database)
    twins = refresh_twins(client)
    write_rule(client)
    response = advise(client)
    assert response.status_code == 200, response.text
    body = response.json()
    simulation = body["simulation_result"]

    # Recomputed here, independently, from the twin's own reading rather than from
    # the fixture's constant: C1 = C0 x V0 / V1, with V0 = A x D0 and Vr = A x R.
    starting_salinity = twins[PAN]["salinity_g_l"]
    starting_depth_cm = twins[PAN]["water_depth_cm"]
    initial_volume = AREA_M2 * (starting_depth_cm / 100.0)
    rainfall_volume = AREA_M2 * (FORECASTS_MM[PAN] / 1000.0)
    expected = starting_salinity * initial_volume / (initial_volume + rainfall_volume)
    assert simulation["post_rain_salinity_g_l"] == pytest.approx(expected, rel=1e-9)
    assert simulation["initial_salinity_g_l"] == pytest.approx(starting_salinity, rel=1e-12)
    assert simulation["post_rain_depth_cm"] == pytest.approx(
        starting_depth_cm + FORECASTS_MM[PAN] / 10.0, rel=1e-9
    )
    assert simulation["is_ml"] is False
    assert simulation["rainfall_source"] == "twin.forecast_rainfall_mm"

    # And the run is stored, so the figures can be replayed rather than trusted.
    runs = stored_runs(database)
    assert len(runs) == 1
    assert runs[0].run_id == simulation["run_id"]
    assert runs[0].inputs["twin_id"] == body["twin"]["twin_id"]
    assert runs[0].outputs["post_rain_salinity_g_l"] == pytest.approx(expected, rel=1e-9)


def test_the_seven_required_things_are_all_present(client: TestClient, database: FakeDatabase) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client)
    body = advise(client).json()

    assert body["recommendation"] == "TRANSFER BRINE"
    assert isinstance(body["reason"], str) and body["reason"]
    assert body["inputs_used"]
    assert body["simulation_result"]["post_rain_salinity_g_l"] is not None
    assert "ml_result" in body  # present and null, which is the reported state
    assert body["rule_version"] and "v1" in body["rule_version"]
    assert body["timestamp"]


def test_each_criterion_fires_on_its_own_pan(
    client: TestClient, database: FakeDatabase
) -> None:
    """One pan per rung of the ladder, so every action is exercised by real evidence."""
    seed(database)
    refresh_twins(client)
    write_rule(client)

    issued = {}
    for pan_id in PANS:
        response = advise(client, pan_id)
        assert response.status_code == 200, response.text
        issued[pan_id] = response.json()["recommendation"]

    # B01: no rain, so the salinity is untouched at 300 g/L and it is moved.
    assert issued["KZN-2026-B01"] == "TRANSFER BRINE"
    # B03: 60 mm takes it to 200 g/L, under the harvest threshold.
    assert issued["KZN-2026-B03"] == "HARVEST NOW"
    # B04: 120 mm brings the depth to 24 cm, over the drainage threshold, and the
    # ladder's highest rank wins over the harvest one.
    assert issued["KZN-2026-B04"] == "PROTECT / DRAIN"
    # B02: 20 mm leaves 257 g/L, which is above the harvest cut and below the
    # transfer cut, so nothing matched and the declared fallback was assigned.
    assert issued["KZN-2026-B02"] == "WAIT AND RECHECK"


def test_the_fallback_is_reported_as_the_rules_own(client: TestClient, database: FakeDatabase) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client)
    body = advise(client, "KZN-2026-B02").json()
    assert body["recommendation"] == "WAIT AND RECHECK"
    assert "no criterion" in body["reason"].lower()
    assert "fallback" in body["reason"].lower()


def test_the_most_severe_match_decides(client: TestClient, database: FakeDatabase) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client)
    body = advise(client, "KZN-2026-B04").json()
    # Both the depth criterion and the harvest criterion fired; PROTECT / DRAIN
    # outranks HARVEST NOW, and both are recorded.
    assert body["recommendation"] == "PROTECT / DRAIN"
    assert "simulated_post_rain_depth_cm" in body["matched_criteria"]
    assert "simulated_post_rain_salinity_g_l" in body["matched_criteria"]


def test_the_three_kinds_of_evidence_are_reported_apart(
    client: TestClient, database: FakeDatabase
) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client)
    body = advise(client).json()

    simulation = body["simulation_result"]
    assert simulation["is_ml"] is False
    assert simulation["run_id"] and simulation["model_name"]
    assert simulation["assumptions"] and simulation["disclaimer"]

    assert body["ml_result"] is None
    assert body["basis"] == "physics_and_rules"

    sources = {entry["source"] for entry in body["inputs_used"]}
    assert sources == {"simulation"}
    for entry in body["inputs_used"]:
        assert entry["available"] is True
        assert entry["value"] is not None


def test_the_inputs_are_read_from_the_twin_and_the_physics(
    client: TestClient, database: FakeDatabase
) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client)
    body = advise(client).json()
    readings = {entry["variable"]: entry["value"] for entry in body["inputs_used"]}
    assert readings["simulated_post_rain_salinity_g_l"] == pytest.approx(
        body["simulation_result"]["post_rain_salinity_g_l"], rel=1e-12
    )
    assert body["twin"]["twin_id"]
    assert body["simulation_result"]["initial_salinity_g_l"] == pytest.approx(
        body["twin"]["salinity_g_l"], rel=1e-12
    )


def test_the_recommendation_cites_the_rule_content_hash(client: TestClient, database: FakeDatabase) -> None:
    seed(database)
    refresh_twins(client)
    rule = write_rule(client)
    body = advise(client).json()
    assert body["rule_id"] == rule["rule_id"]
    assert body["rule_name"] == rule["name"]
    assert body["rule_version_number"] == 1
    assert body["specification_hash"] == rule["specification_hash"]
    assert rule["specification_hash"] in body["rule_version"]


def test_a_stored_recommendation_keeps_its_evidence(
    client: TestClient, database: FakeDatabase
) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client)
    advise(client)
    stored = stored_recommendations(database)
    assert len(stored) == 1
    record = stored[0]
    assert record.action is DecisionAction.TRANSFER_BRINE
    assert record.twin.twin_id
    assert record.simulation.run_id
    assert record.evaluation.rule_version == 1
    assert [reading.variable for reading in record.evaluation.input_readings] == [
        "simulated_post_rain_depth_cm",
        "simulated_post_rain_salinity_g_l",
    ]
    assert record.input_readings_text.startswith("simulated_post_rain_depth_cm=")


def test_a_recommendation_carries_no_confidence_field(
    client: TestClient, database: FakeDatabase
) -> None:
    """A rule's agreement with an action is not a likelihood, and must not look like one."""
    seed(database)
    refresh_twins(client)
    write_rule(client)
    body = advise(client).json()
    assert "confidence" not in body
    record = stored_recommendations(database)[0]
    assert "confidence" not in record.model_dump()
    assert "rule_confidence" not in record.model_dump()


def test_a_stored_recommendation_is_either_a_whole_decision_or_a_plain_note() -> None:
    """A decision with any evidence missing is refused; a note with no action is kept.

    Module 8's outcome grading writes a note — an operator's own words with no action
    and nothing behind it — and that has to stay writable. The record that must not
    exist is the middle one: an action with no twin, or a rule id with no evaluation.
    It reads as auditable, so a consumer would rely on it, and there is nothing to
    audit.
    """
    note = Recommendation(
        pan_id="KZN-2026-A01",
        title="Hold the gate closed",
        body="No action until the front has passed.",
        rationale="The forecast is for a light event only.",
    )
    assert note.action is None
    assert note.is_decision is False
    assert note.twin is None and note.simulation is None and note.evaluation is None

    for partial, missing in (
        ({"action": "HARVEST NOW"}, "twin"),
        ({"action": "HARVEST NOW", "rule_id": "rule-1"}, "twin"),
        ({"rule_id": "rule-1", "rule_name": "wet-season"}, "action"),
    ):
        with pytest.raises(ValidationError) as caught:
            Recommendation(
                pan_id="KZN-2026-A01",
                title="HARVEST NOW",
                body="body",
                rationale="rationale",
                **partial,
            )
        assert missing in str(caught.value)

    # A model estimate is the one evidence field that is legitimately optional on a
    # whole decision: a recommendation without one is complete, and says so.
    whole = Recommendation(
        pan_id="KZN-2026-A01",
        title="HARVEST NOW",
        body="body",
        rationale="rationale",
        action="HARVEST NOW",
        reason="The brine is at or below the harvest salinity.",
        basis="physics_and_rules",
        notice="Physics/rule-based recommendation — ML prediction unavailable.",
        twin=DecisionTwinSummary(
            twin_id="twin-1",
            as_of=REGISTERED,
            provenance="observed",
            salinity_g_l=200.0,
        ),
        simulation=DecisionSimulationSummary(
            model_name=MASS_BALANCE_MODEL_NAME,
            rainfall_mm=0.0,
            rainfall_source="forecast",
            initial_salinity_g_l=200.0,
            initial_depth_cm=12.0,
            post_rain_salinity_g_l=200.0,
            post_rain_depth_cm=12.0,
            salinity_drop_g_l=0.0,
        ),
        rule_id="rule-1",
        rule_name="wet-season",
        rule_version=1,
        evaluation=DecisionEvaluation(
            action="HARVEST NOW",
            rule_id="rule-1",
            rule_name="wet-season",
            rule_version=1,
            specification_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
        specification_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    assert whole.is_decision is True
    assert whole.ml is None
    assert whole.is_ml_informed is False


def test_a_recommendation_is_stored_once_per_request(
    client: TestClient, database: FakeDatabase
) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client)
    advise(client)
    advise(client)
    # Two requests are two recommendations: the advice log is a record of what an
    # operator was told, and collapsing repeats would hide that they asked twice.
    assert len(stored_recommendations(database)) == 2
    assert len(stored_runs(database)) == 2


def test_the_latest_route_reports_the_newest_advice(
    client: TestClient, database: FakeDatabase
) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client)
    assert client.get(f"/api/decisions/{PAN}/latest").json()["found"] is False
    advise(client)
    latest = client.get(f"/api/decisions/{PAN}/latest").json()
    assert latest["found"] is True
    assert latest["advice"]["recommendation"] == "TRANSFER BRINE"


def test_the_log_is_ordered_and_can_be_narrowed(
    client: TestClient, database: FakeDatabase
) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client)
    for pan_id in PANS:
        advise(client, pan_id)
    everything = client.get("/api/decisions/log").json()
    assert everything["count"] == 4
    stamps = [row["timestamp"] for row in everything["advice"]]
    assert stamps == sorted(stamps, reverse=True)

    only_harvest = client.get("/api/decisions/log", params={"action": "HARVEST NOW"}).json()
    assert only_harvest["count"] == 1
    # Pan ids are stored lowercased, as they are everywhere in this project, so the
    # log reports the stored form rather than echoing the request's casing.
    assert only_harvest["advice"][0]["pan_id"] == "KZN-2026-B03".lower()

    one_pan = client.get("/api/decisions/log", params={"pan_id": PAN}).json()
    assert one_pan["count"] == 1
    assert one_pan["advice"][0]["pan_id"] == PAN.lower()


def test_an_unknown_action_filter_is_refused_rather_than_returning_nothing(
    client: TestClient,
) -> None:
    response = client.get("/api/decisions/log", params={"action": "HARVEST"})
    assert response.status_code == 422, response.text
    assert "HARVEST NOW" in response.json()["detail"]


# -- the ML branch ----------------------------------------------------------


def test_without_a_prediction_the_exact_label_is_reported(
    client: TestClient, database: FakeDatabase
) -> None:
    """The required wording, verbatim, on the response and on the stored record."""
    seed(database)
    refresh_twins(client)
    write_rule(client)
    body = advise(client).json()
    assert body["ml_result"] is None
    assert body["basis"] == "physics_and_rules"
    # Nothing was scored to get here. Advice is a read of the twin, a simulation and
    # a rule; a model that was fitted, deployed and run for the first time by this
    # request would make the module's own figures look like independent evidence.
    assert stored_predictions(database) == []
    # The run this advice rests on is the physics module's own, recorded under the
    # name the physics service now declares -- not a copy of it.
    assert [run.outputs["model"] for run in stored_runs(database)] == [MASS_BALANCE_MODEL_NAME]
    # The physics result says so itself, so no reader has to infer it from the
    # absence of a model block.
    assert body["simulation_result"]["is_ml"] is False
    # Verbatim, and first in the notice, so a client can render it as the label
    # without having to find it inside a sentence.
    assert body["notice"].startswith(REQUIRED_ML_UNAVAILABLE_LABEL)
    record = stored_recommendations(database)[0]
    assert record.notice.startswith(REQUIRED_ML_UNAVAILABLE_LABEL)
    assert record.ml is None
    # And the constant the service and the status endpoint share is the same string,
    # so a client reading it from either place gets the required wording.
    assert ML_UNAVAILABLE_LABEL == REQUIRED_ML_UNAVAILABLE_LABEL


def test_a_rule_that_does_not_read_ml_says_so_even_when_a_prediction_exists(
    client: TestClient, database: FakeDatabase
) -> None:
    """A stored estimate the rule never consulted must not be attached as evidence."""
    seed(database)
    refresh_twins(client)
    write_rule(client)
    build_and_deploy(client)
    for pan_id in PANS:
        predict(client, pan_id)
    before = stored_predictions(database)
    assert before, "the fixture must have produced a prediction"

    body = advise(client).json()
    assert body["ml_result"] is None
    assert body["basis"] == "physics_and_rules"
    # The stored estimates are left exactly as they were: this request read nothing
    # from them, so it wrote nothing to them either.
    assert stored_predictions(database) == before
    assert "not read" in body["notice"]


def test_a_stored_explanation_is_cited_and_only_the_right_one(
    client: TestClient,
    database: FakeDatabase,
) -> None:
    """The operator is pointed at the explanation of the estimate the rule read.

    Module 11 stores a SHAP attribution for the prediction; this recommendation
    read that prediction, so it cites the attribution by id. The link is by id
    alone -- an explanation belonging to a different prediction, or to a different
    version of the same model, describes some other estimate and citing it would be
    a false claim about the evidence. None is a normal state, not a reason to
    withhold the recommendation, and no attribution is ever computed here.
    """
    seed(database)
    refresh_twins(client)
    build_and_deploy(client)
    write_rule(client, ML_RULE)
    prediction = predict(client, PAN)
    explanations = database.collections["explanations"]

    cited = advise(client, PAN).json()["ml_result"]["explanation_id"]
    assert cited is not None, "the prediction stored an explanation; it should be cited"

    explanations._documents.clear()
    without = advise(client, PAN).json()
    assert without["ml_result"]["explanation_id"] is None
    # The recommendation is still complete and still model-informed.
    assert without["basis"] == "physics_and_ml"
    assert without["notice"]

    for mismatched in (
        {"prediction_id": "pred-someone-else"},
        {"model_version": "v9.9.9"},
        {"model_id": "model-someone-else"},
    ):
        explanations._documents.clear()
        stored = Explanation(
            model_version=prediction["model_version"],
            model_id=prediction["model_id"],
            prediction_id=prediction["prediction_id"],
            pan_id=PAN,
        ).model_dump()
        stored.update(mismatched)
        asyncio.run(explanations.insert_one(stored))
        cited = advise(client, PAN).json()["ml_result"]["explanation_id"]
        assert cited is None, f"cited an explanation that differs by {mismatched}"


def test_the_ml_result_is_the_stored_prediction_s_own_figures(
    client: TestClient, database: FakeDatabase
) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client, ML_RULE)
    build_and_deploy(client)
    prediction = predict(client, PAN)

    body = advise(client).json()
    ml = body["ml_result"]
    assert ml is not None
    assert ml["prediction_id"] == prediction["prediction_id"]
    assert ml["model_version"] == prediction["model_version"]
    assert ml["target"] == "operational_risk"
    assert ml["predicted_class"] == prediction["risk_class"]
    assert ml["probability"] == pytest.approx(prediction["probability"])
    assert ml["input_snapshot_id"] == prediction["input_snapshot_id"]
    assert ml["matches_recommendation_snapshot"] is True
    assert datetime.fromisoformat(ml["predicted_at"]) == datetime.fromisoformat(
        prediction["predicted_at"]
    )
    assert body["basis"] == "physics_and_ml"


def test_the_engine_scores_nothing(client: TestClient, database: FakeDatabase) -> None:
    """Asking for advice must not create a prediction the recommendation then cites."""
    seed(database)
    refresh_twins(client)
    write_rule(client, ML_RULE)
    build_and_deploy(client)
    predict(client, PAN)
    before = stored_predictions(database)
    assert len(before) == 1

    advise(client)
    advise(client, "KZN-2026-B02")
    assert stored_predictions(database) == before


def test_a_stale_prediction_is_attached_but_marked_as_stale(
    client: TestClient, database: FakeDatabase
) -> None:
    """An older estimate is still evidence, and the mismatch has to be visible."""
    seed(database)
    refresh_twins(client)
    write_rule(client, ML_RULE)
    build_and_deploy(client)
    predict(client, PAN)

    # A later twin state: new readings, so the stored prediction is now older.
    later = FIRST_READING + READINGS_PER_PAN * READING_INTERVAL + timedelta(hours=6)
    extra: MongoRepository[PanObservation] = MongoRepository(
        database, PAN_OBSERVATIONS, PanObservation
    )
    asyncio.run(
        extra.insert(
            PanObservation(
                observation_id=f"obs-{PAN}-later",
                pan_id=PAN,
                timestamp=later,
                salinity_g_l=250.0,
                water_depth_cm=10.0,
                brine_temperature_c=30.0,
                humidity_percent=60.0,
                ambient_temperature_c=25.0,
                created_at=REGISTERED,
            )
        )
    )
    assert client.post(f"/api/twin/{PAN}/refresh").status_code in (200, 201)

    body = advise(client).json()
    assert body["ml_result"] is not None
    assert body["ml_result"]["matches_recommendation_snapshot"] is False
    assert body["basis"] == "physics_and_ml"


def test_a_class_only_model_keeps_the_class_and_the_null_probability(
    client: TestClient, database: FakeDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A class with no likelihood is reported as such, and no likelihood is invented."""
    seed(database)
    refresh_twins(client)
    write_rule(client, ML_RULE)
    build_and_deploy(client)
    predict(client, PAN)

    # Strip the probability from the stored row the way a class-only artifact would
    # have written it, and drop the probability from the ML block's own copy.
    predictions: MongoRepository[Any] = MongoRepository(database, ML_PREDICTIONS, MLPrediction)
    stored = asyncio.run(predictions.find({"pan_id": PAN.lower()}))
    asyncio.run(
        predictions.replace_where(
            {"pan_id": PAN.lower()},
            stored[0].model_copy(update={"probability": None, "confidence": None}),
        )
    )

    body = advise(client).json()
    ml = body["ml_result"]
    assert ml["predicted_class"] is not None
    assert ml["probability"] is None
    assert ml["confidence"] is None
    assert ml["probability_is_null_reason"]


# -- refusals ---------------------------------------------------------------


def test_a_pan_with_no_twin_state_is_refused(client: TestClient, database: FakeDatabase) -> None:
    seed(database)
    write_rule(client)
    response = advise(client)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "no_twin_state"
    assert "twin" in body["detail"].lower()
    assert stored_recommendations(database) == []


def test_records_plus_a_refreshed_snapshot_resolve_the_dashboard_workflow(
    client: TestClient, database: FakeDatabase
) -> None:
    """The reading → snapshot → advise loop the dashboard is built around.

    A pan with a saved observation and a saved forecast but no stored twin
    snapshot refuses, exactly as the dashboard once made every operator hit it:
    the records exist, the live twin assembles, and yet "Run analysis" answers
    that there is no recorded state to reason from. The step that finishes the
    workflow is storing a snapshot (Module 05 refresh); only then do the same
    demonstration records advise into a recommendation whose simulation is the
    mass balance's own arithmetic: 250 g/L at 20 cm with a 25 mm forecast gives
    222.2 g/L and an 11.1% dilution.
    """
    pan_id = "field-p01"
    area_m2 = 12_000.0
    observed_at = datetime(2026, 9, 30, 6, 29, tzinfo=timezone.utc)
    forecast_issued = datetime(2026, 9, 30, 6, 30, tzinfo=timezone.utc)
    forecast_for = datetime(2026, 9, 30, 12, 30, tzinfo=timezone.utc)

    pans: MongoRepository[SaltPan] = MongoRepository(database, SALT_PANS, SaltPan)
    observations: MongoRepository[PanObservation] = MongoRepository(
        database, PAN_OBSERVATIONS, PanObservation
    )
    weather: MongoRepository[WeatherObservation] = MongoRepository(
        database, WEATHER_OBSERVATIONS, WeatherObservation
    )

    async def write() -> None:
        await pans.insert(
            SaltPan(
                pan_id=pan_id,
                pan_name="Actual salt-pan name",
                location="Port Shepstone",
                area_m2=area_m2,
                normal_depth_cm=20.0,
                target_harvest_salinity_g_l=285.0,
                risk_thresholds=RiskThresholds(
                    flood_depth_cm=45.0,
                    dilution_salinity_g_l=250.0,
                    critical_rainfall_24h_mm=80.0,
                    target_flow_cm=1.5,
                ),
                created_at=observed_at,
            )
        )
        await observations.insert(
            PanObservation(
                observation_id="obs-field-p01-demo",
                pan_id=pan_id,
                timestamp=observed_at,
                salinity_g_l=250.0,
                water_depth_cm=20.0,
                brine_temperature_c=32.0,
                humidity_percent=None,
                ambient_temperature_c=None,
                data_source=ResearchDataSource.DEMONSTRATION,
                created_at=observed_at,
            )
        )
        await weather.insert(
            WeatherObservation(
                weather_id="wx-field-p01-demo",
                pan_id=pan_id,
                timestamp=forecast_issued,
                forecast_for=forecast_for,
                rainfall_mm=25.0,
                rainfall_probability_percent=70.0,
                ambient_temperature_c=None,
                humidity_percent=None,
                source=WeatherSource.DEMONSTRATION,
                is_forecast=True,
                created_at=forecast_issued,
            )
        )

    asyncio.run(write())
    write_rule(client)

    # Every record exists, and the twin assembles from them — yet without a
    # stored snapshot there is no recorded state to reason from. This is the
    # refusal the dashboard's save flow used to leave the operator staring at.
    refused = advise(client, pan_id)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "no_twin_state"
    assert stored_recommendations(database) == []

    # Storing the snapshot is the act that finishes the workflow. The same
    # records then advise.
    response = client.post(f"/api/twin/{pan_id}/refresh")
    assert response.status_code in (200, 201), response.text
    assert response.json()["state"]["pan_id"] == pan_id

    body = advise(client, pan_id).json()
    assert body["recommendation"] == "PROTECT / DRAIN"
    assert body["basis"] == "physics_and_rules"
    assert body["ml_result"] is None

    simulation = body["simulation_result"]
    assert simulation["initial_salinity_g_l"] == pytest.approx(250.0, rel=1e-12)
    assert simulation["initial_depth_cm"] == pytest.approx(20.0, rel=1e-12)
    assert simulation["rainfall_mm"] == pytest.approx(25.0, rel=1e-12)
    # C1 = C0 x V0 / V1, with V0 = A x 0.20 m and Vr = A x 0.025 m — 222.22 g/L
    # and 11.11% dilution, recomputed rather than hardcoded.
    initial_volume = area_m2 * 0.20
    rainfall_volume = area_m2 * (25.0 / 1000.0)
    expected_salinity = 250.0 * initial_volume / (initial_volume + rainfall_volume)
    assert simulation["post_rain_salinity_g_l"] == pytest.approx(
        expected_salinity, rel=1e-9
    )
    assert simulation["salinity_drop_g_l"] == pytest.approx(
        250.0 - expected_salinity, rel=1e-9
    )
    assert simulation["dilution_percent"] == pytest.approx(
        (250.0 - expected_salinity) / 250.0 * 100.0, rel=1e-9
    )
    assert simulation["post_rain_depth_cm"] == pytest.approx(22.5, rel=1e-9)
    assert simulation["is_ml"] is False
    assert simulation["rainfall_source"] == "twin.forecast_rainfall_mm"


def test_a_current_observation_and_forecast_reach_a_recommendation_via_refresh(
    client: TestClient, database: FakeDatabase
) -> None:
    """The exact gap the pan detail page hit: records exist, the twin assembles,
    yet advising refuses until a snapshot is stored.

    Everything here is written through the real HTTP endpoints — a pan is
    registered, a manual observation (300 g/L, 20 cm) and a weather forecast (0.9
    mm at 53%) are recorded — so the twin Module 05 assembles and displays is
    genuinely complete and is drawn from live records, not from a fixture.

    The refusal that opens the test is the bug: `GET /api/twin/{id}/state` reads
    the twin without storing it, so a pan whose state is plainly complete still
    answers `no_twin_state` when Module 12 asks. The step that closes the gap is
    `POST /api/twin/{id}/refresh`; only then does the history report a persisted
    state and the recommendation resolve. Nothing is marked demonstration: this
    is a real pan, a real reading and a real forecast.
    """
    pan_id = "kzn-current-observation"
    area_m2 = 12_300.0

    # Register the pan and record a current observation plus a forecast in force,
    # all through the HTTP API so nothing is stubbed into existence.
    registered = client.post(
        "/api/pans/",
        json={
            "pan_id": pan_id,
            "pan_name": "Current observation pan",
            "location": "Marakkanam",
            "area_m2": area_m2,
            "normal_depth_cm": 20.0,
            "target_harvest_salinity_g_l": 285.0,
            "risk_thresholds": {
                "flood_depth_cm": 45.0,
                "dilution_salinity_g_l": 250.0,
                "critical_rainfall_24h_mm": 80.0,
                "target_flow_cm": 1.5,
            },
        },
    )
    assert registered.status_code == 201, registered.text

    observed = client.post(
        "/api/observations/",
        json={
            "pan_id": pan_id,
            "salinity_g_l": 300.0,
            "water_depth_cm": 20.0,
            "brine_temperature_c": 30.0,
            "data_source": "manual",
        },
    )
    assert observed.status_code in (200, 201), observed.text

    # A forecast is written by supplying `forecast_for`; the schema marks the row
    # as a forecast and refuses an explicit flag alongside it.
    forecast_issued = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
    forecast_applies = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)
    forecast = client.post(
        "/api/weather/",
        json={
            "pan_id": pan_id,
            "timestamp": forecast_issued.isoformat(),
            "forecast_for": forecast_applies.isoformat(),
            "rainfall_mm": 0.9,
            "rainfall_probability_percent": 53.0,
            "source": "manual",
        },
    )
    assert forecast.status_code in (200, 201), forecast.text
    assert forecast.json()["is_forecast"] is True

    # The twin is complete only when all three records exist, so an observed
    # weather reading sits beside the forecast rather than being folded into it.
    observed_weather = client.post(
        "/api/weather/",
        json={
            "pan_id": pan_id,
            "rainfall_mm": 0.0,
            "ambient_temperature_c": 30.0,
            "humidity_percent": 65.0,
            "source": "manual",
        },
    )
    assert observed_weather.status_code in (200, 201), observed_weather.text
    assert observed_weather.json()["is_forecast"] is False

    # The twin assembles completely from those two records and shows so on Module
    # 05 — yet advising refuses, because nothing has been stored.
    state = client.get(f"/api/twin/{pan_id}/state")
    assert state.status_code == 200, state.text
    assert state.json()["is_complete"] is True
    assert state.json()["forecast_rainfall_mm"] == pytest.approx(0.9, rel=1e-9)

    write_rule(client)
    refused = advise(client, pan_id)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "no_twin_state"
    assert stored_recommendations(database) == []

    # The one step that finishes the workflow: store the twin that is on screen.
    refreshed = client.post(f"/api/twin/{pan_id}/refresh")
    assert refreshed.status_code in (200, 201), refreshed.text
    assert refreshed.json()["stored"] is True

    # GET/read now reports a persisted state rather than only a live assembly.
    history = client.get(f"/api/twin/{pan_id}/history")
    assert history.status_code == 200, history.text
    assert history.json()["total"] >= 1

    # And the same pan now answers with a recommendation, not no_twin_state.
    advised = advise(client, pan_id)
    assert advised.status_code == 200, advised.text
    body = advised.json()
    assert body["recommendation"] in {a["action"] for a in RULE_ACTIONS}
    assert body["pan_id"] == pan_id
    assert body["simulation_result"]["initial_salinity_g_l"] == pytest.approx(
        300.0, rel=1e-12
    )
    assert body["simulation_result"]["rainfall_mm"] == pytest.approx(0.9, rel=1e-9)


def test_a_twin_the_physics_cannot_run_is_refused(
    client: TestClient, database: FakeDatabase
) -> None:
    """A recommendation whose simulation result would have to be invented is not issued."""
    seed(database)
    observations: MongoRepository[PanObservation] = MongoRepository(
        database, PAN_OBSERVATIONS, PanObservation
    )
    rows = asyncio.run(observations.find({"pan_id": PAN.lower()}, sort=[("timestamp", -1)], limit=1))
    asyncio.run(
        observations.replace_where(
            {"observation_id": rows[0].observation_id},
            rows[0].model_copy(update={"water_depth_cm": None}),
        )
    )
    refresh_twins(client)
    write_rule(client)

    response = advise(client)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "simulation_inputs_missing"
    assert "water_depth_cm" in body["detail"]
    assert stored_recommendations(database) == []
    assert stored_runs(database) == []


def test_no_written_rule_refuses_rather_than_improvising(
    client: TestClient, database: FakeDatabase
) -> None:
    seed(database)
    refresh_twins(client)
    response = advise(client)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["code"] == "no_rule_defined"
    assert "POST /api/decisions/rules" in body["detail"]
    assert stored_recommendations(database) == []


def test_the_rule_the_engine_chose_is_named_on_the_answer(
    client: TestClient, database: FakeDatabase
) -> None:
    """With two rule families written, the choice is the newest -- and is visible.

    A caller that does not name a rule is making a choice among rules rather than
    getting a fallback, so the answer has to say which one it used. Asserting that
    here is what stops the choice from quietly becoming a hidden default: the name,
    version and content hash are on every response, and this test reads them.
    """
    seed(database)
    refresh_twins(client)
    build_and_deploy(client)
    for pan_id in PANS:
        predict(client, pan_id)
    older = write_rule(client)
    newer = write_rule(client, {**ML_RULE, "name": "model-led catch-up"})

    chosen = advise(client).json()
    assert chosen["rule_name"] == newer["name"]
    assert chosen["rule_version_number"] == newer["version"]
    assert chosen["specification_hash"] == newer["specification_hash"]
    # The contract's `rule_version` is the citation a human reads; the structured
    # fields beside it are what a client parses. Both quote the same rule.
    assert newer["name"] in chosen["rule_version"]
    assert f"v{newer['version']}" in chosen["rule_version"]

    # Naming the older rule gets the older rule, not the newest one.
    named = advise(client, rule_id=older["rule_id"])
    assert named.status_code == 200, named.text
    assert named.json()["rule_id"] == older["rule_id"]
    assert named.json()["rule_name"] == older["name"]


def test_a_rule_that_cannot_read_its_input_withholds_its_fallback(
    client: TestClient, database: FakeDatabase
) -> None:
    """A fallback must not absorb exactly the pans whose evidence is thinnest."""
    seed(database)
    refresh_twins(client)
    write_rule(client, ML_RULE)

    # The ML rule reads the model's class, and no prediction is stored, so its
    # criterion is unread. The physics criteria would still match or miss, so the
    # rule's *read* of the pan is incomplete and the fallback is withheld.
    response = advise(client, "KZN-2026-B02")
    assert response.status_code == 409, response.text
    body = response.json()
    # The criterion could not be read, so this is a statement about the evidence
    # rather than about the rule, and is reported as such. A client that cannot
    # tell "we could not read what you need" from "the rule matched nothing" will
    # tell the operator the wrong thing about which one to go and fix.
    assert body["code"] == "insufficient_evidence"
    assert "predicted_risk_class" in body["detail"]


def test_a_what_if_rainfall_depth_is_used_and_recorded_as_a_request(
    client: TestClient, database: FakeDatabase
) -> None:
    """An operator's own figure is not a forecast, and the record says which it was."""
    seed(database)
    refresh_twins(client)
    write_rule(client)
    body = advise(client, "KZN-2026-B01", rainfall_mm=120.0).json()
    assert body["simulation_result"]["rainfall_source"] == "request.rainfall_mm"
    assert body["simulation_result"]["rainfall_mm"] == 120.0
    # B01 is forecast dry, so with no rain it would be TRANSFER BRINE; with 120 mm
    # of what-if rain the same rule resolves differently.
    assert body["recommendation"] == "PROTECT / DRAIN"


def test_an_anchored_advice_uses_the_twin_of_that_moment(
    client: TestClient, database: FakeDatabase
) -> None:
    """A past anchor is answered from what the system believed then, not from today."""
    seed(database)
    first = refresh_twins(client)[PAN]
    write_rule(client)

    later = datetime.fromisoformat(first["timestamp"]) + READING_INTERVAL
    observations: MongoRepository[PanObservation] = MongoRepository(
        database, PAN_OBSERVATIONS, PanObservation
    )
    asyncio.run(
        observations.insert(
            PanObservation(
                observation_id="obs-later-anchor",
                pan_id=PAN,
                timestamp=later,
                salinity_g_l=250.0,
                water_depth_cm=10.0,
                brine_temperature_c=30.0,
                humidity_percent=60.0,
                ambient_temperature_c=25.0,
                created_at=REGISTERED,
            )
        )
    )
    second = refresh_twins(client)[PAN]
    assert second["twin_id"] != first["twin_id"]

    current = advise(client).json()
    assert current["twin"]["twin_id"] == second["twin_id"]

    past = advise(client, PAN, as_of=first["timestamp"]).json()
    assert past["twin"]["twin_id"] == first["twin_id"]
    assert datetime.fromisoformat(past["twin"]["as_of"]) == datetime.fromisoformat(
        first["timestamp"]
    )

    # Earlier than the system's first belief about the pan: refused rather than
    # reasoned from whatever happens to be on record now.
    earlier = client.post(
        "/api/decisions/advise",
        json={
            "pan_id": PAN,
            "as_of": (
                datetime.fromisoformat(first["timestamp"]) - timedelta(days=1)
            ).isoformat(),
        },
    )
    assert earlier.status_code == 409
    assert earlier.json()["code"] == "no_twin_state"


def test_the_widest_rule_the_schema_accepts_can_still_be_recorded(
    client: TestClient,
    database: FakeDatabase,
) -> None:
    """A rule at the ``max_length=32`` criterion cap must produce a stored answer.

    The audit cells the engine writes are bounded by the storage limits on
    ``Recommendation``, and those limits are enforced on assignment. A 32-criterion
    rule whose criteria all fire produced more than 2000 characters of match text,
    and the overflow surfaced as a 422 naming ``body.reason`` -- reporting a legal
    answer the server could not store as though the caller's ``pan_id`` were
    malformed. The bound is derived from the rule limits for that reason; this is
    the test that says the derivation is still wide enough.
    """
    seed(database)
    refresh_twins(client)
    widest = {
        **RULE,
        "name": "the widest rule the schema accepts",
        "criteria": [
            {
                "variable": "simulated_post_rain_salinity_g_l",
                "operator": "lte",
                # A threshold no salinity reaches, so every criterion fires and the
                # engine records all of them rather than the one that decided.
                "threshold": 999999.0,
                "unit": "g/L",
                "action": "HARVEST NOW",
                "rationale": f"Criterion {i}, which every pan satisfies.",
            }
            for i in range(32)
        ],
    }
    assert client.post("/api/decisions/rules", json=widest).status_code == 201

    response = advise(client, PAN)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recommendation"] == "HARVEST NOW"

    # The whole rule really was recorded, none of it dropped to fit.
    assert body["matched_criteria"].count(" -> ") == 32


def test_the_audit_storage_bounds_cover_the_catalogue() -> None:
    """The constants the storage bounds are built from still match the catalogue.

    ``app.models`` cannot import the catalogue to measure it -- that module reaches
    the db layer, and the import closes a cycle -- so the two catalogue bounds are
    stated as numbers in ``decision_rule`` and pinned here instead. Widening the
    catalogue fails this test rather than producing records the storage refuses.
    """
    from app.models.decision_rule import (
        MAX_CATALOGUE_NAME_CHARS,
        MAX_CATALOGUE_REASON_CHARS,
    )

    widest_name = max(len(entry.name) for entry in DECISION_VARIABLE_CATALOG)
    widest_reason = max(
        len(entry.nullable_reason or "") for entry in DECISION_VARIABLE_CATALOG
    )
    assert MAX_CATALOGUE_NAME_CHARS >= widest_name, (
        f"a catalogued name is {widest_name} characters; the storage bound assumes "
        f"{MAX_CATALOGUE_NAME_CHARS}. Raise MAX_CATALOGUE_NAME_CHARS."
    )
    assert MAX_CATALOGUE_REASON_CHARS >= widest_reason, (
        f"a catalogued reason is {widest_reason} characters; the storage bound "
        f"assumes {MAX_CATALOGUE_REASON_CHARS}. Raise MAX_CATALOGUE_REASON_CHARS."
    )


def test_as_of_must_say_which_timezone_it_is_in(client: TestClient) -> None:
    """A naive instant names no point in time, so it cannot pick a twin state.

    Every stored instant is timezone-aware, and a request without an offset could
    mean UTC, the pan's local time or the caller's own -- three readings that can
    select three different states. Rather than guess one and answer a question the
    caller did not ask, the request is refused. Left unchecked this reaches the
    comparison as an offset-naive versus offset-aware ``TypeError``, which is a 500
    for a malformed request rather than the 422 it is.
    """
    for body in (
        {"pan_id": PAN, "as_of": "2026-09-01T00:00:00"},
        {"pan_id": PAN, "as_of": "2026-09-01T05:30:00"},
        # The model's own default, which is the naive case this test is about.
        {"pan_id": PAN, "as_of": datetime(2026, 9, 1).isoformat()},  # noqa: DTZ001
    ):
        response = client.post("/api/decisions/advise", json=body)
        assert response.status_code == 422, (body, response.text)
        assert "timezone offset" in str(response.json()["detail"]), response.text

    # The same instant written with an offset is a well-formed request, and is
    # answered on its own terms rather than refused for its format.
    for value in ("2026-09-01T00:00:00Z", "2026-09-01T05:30:00+05:30"):
        response = client.post("/api/decisions/advise", json={"pan_id": PAN, "as_of": value})
        assert response.status_code in (200, 409), (value, response.text)


def test_a_request_cannot_supply_features_or_an_action(client: TestClient) -> None:
    """A recommendation whose inputs the caller also chose is about the caller's assumptions."""
    for body in (
        {"pan_id": PAN, "action": "HARVEST NOW"},
        {"pan_id": PAN, "features": {"current_salinity_g_l": 1.0}},
        {"pan_id": PAN, "model_id": "whatever"},
        {"pan_id": PAN, "simulated_post_rain_salinity_g_l": 12.0},
    ):
        response = client.post("/api/decisions/advise", json=body)
        assert response.status_code == 422, f"{body} was accepted: {response.text}"


def test_an_unknown_pan_is_refused_as_having_no_state(client: TestClient, database: FakeDatabase) -> None:
    seed(database)
    refresh_twins(client)
    write_rule(client)
    response = advise(client, "KZN-2026-Z99")
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "no_twin_state"


# -- the document's own claims ----------------------------------------------


def test_the_engine_recommends_only_from_the_closed_vocabulary(
    client: TestClient, database: FakeDatabase
) -> None:
    """Whatever the evidence, the action is one of the four. Nothing else can be stored."""
    seed(database)
    refresh_twins(client)
    write_rule(client)
    for pan_id in PANS:
        advise(client, pan_id)
    allowed = {action.value for action in DecisionAction}
    for record in stored_recommendations(database):
        assert record.action.value in allowed


def test_the_index_plan_covers_the_new_collections() -> None:
    from app.db.indexes import INDEX_PLAN, validate_index_plan

    validate_index_plan()
    specs = {(spec.collection, spec.name) for spec in INDEX_PLAN}
    assert ("decision_rules", "decision_rules_name_version_asc") in specs
    assert ("decision_rules", "decision_rules_specification_hash_asc") in specs
    unique_rules = {
        spec.name
        for spec in INDEX_PLAN
        if spec.collection == "decision_rules" and spec.unique
    }
    assert unique_rules == {
        "decision_rules_name_version_asc",
        "decision_rules_specification_hash_asc",
    }
    assert any(
        spec.collection == "recommendations" and spec.keys[0][0] == "rule_id"
        for spec in INDEX_PLAN
    )


# -- the documentation holds the same claims this suite does -----------------

#: The API reference, checked here because a documented contract that the server
#: does not honour is worse than an undocumented one: a reader has no way to tell.
API_DOC = Path(__file__).resolve().parents[2] / "docs" / "api.md"


def documented_rule_example() -> dict[str, Any]:
    """The rule example in docs/api.md, parsed out of the document itself.

    Read from the file rather than restated here, so this test compares the
    documentation against the server instead of comparing two copies of the
    documentation against each other.
    """
    text = API_DOC.read_text()
    section = text[text.index("### `POST /api/decisions/rules`") :]
    return json.loads(re.search(r"```json\n(.*?)\n```", section, re.DOTALL).group(1))


@pytest.mark.skipif(not API_DOC.exists(), reason="docs/api.md is not present")
def test_the_documented_rule_example_is_a_rule_this_server_accepts(
    client: TestClient,
) -> None:
    response = client.post("/api/decisions/rules", json=documented_rule_example())
    assert response.status_code == 201, response.text


@pytest.mark.skipif(not API_DOC.exists(), reason="docs/api.md is not present")
def test_the_documented_routes_are_the_routes_that_exist(client: TestClient) -> None:
    """Every path in the documented table answers, and answers as described."""
    text = API_DOC.read_text()
    section = text[text.index("## Module 12") :]
    section = section[: section.index("\n## Module 15")]

    # Every mention of a path, whether it appears in the route table, a section
    # heading or a sentence. Matching only table cells would leave a wrong path in
    # a heading untested, which is where a renamed route is most likely to hide.
    documented = {
        path.rstrip("/.") for path in re.findall(r"/api/decisions/[A-Za-z0-9_{}/.-]*", section)
    }
    for path in documented:
        # The table writes the prefix root with a trailing slash; every other path
        # in it is read as written.
        if path == "/api/decisions":
            assert client.get("/api/decisions/").status_code == 200, path
        elif path == "/api/decisions/advise":
            # A POST with no pan_id is a 422 from the schema, which still proves
            # the route exists and is the one documented.
            assert client.post(path, json={}).status_code == 422, path
        elif "{rule_id}" in path or "{pan_id}" in path or "{name}" in path:
            continue
        else:
            assert client.get(path).status_code == 200, path

    # The two paths this module is most easily misremembered as, named so a
    # documented wrong path fails here rather than as a 404 a reader meets.
    assert "/api/decisions/variables" in documented
    assert "/api/decisions/catalogue" not in documented, (
        "the catalogue is served at /variables; a documented /catalogue would be a "
        "404 a reader would have to discover for themselves"
    )
    assert "/api/decisions/status" not in documented, (
        "the status route is the prefix root, GET /api/decisions/"
    )


@pytest.mark.skipif(not API_DOC.exists(), reason="docs/api.md is not present")
def test_the_documented_action_vocabulary_is_the_whole_vocabulary(
    client: TestClient,
) -> None:
    status = client.get("/api/decisions/").json()
    assert status["actions"] == [action.value for action in DecisionAction]
    assert status["action_vocabulary_is_closed"] is True


@pytest.mark.skipif(not API_DOC.exists(), reason="docs/api.md is not present")
def test_the_documented_catalogue_is_the_whole_catalogue(client: TestClient) -> None:
    catalogue = client.get("/api/decisions/variables").json()
    names = {variable["name"] for variable in catalogue["variables"]}
    assert names == {variable.name for variable in DECISION_VARIABLE_CATALOG}, (
        "docs/api.md lists the catalogue; the server serves a different one"
    )


# -- operator-configured thresholds, and a rule that contains none ------------
#
# The tests below cover the change that lets this project supply a usable rule
# without stating a single threshold of its own. Each criterion binds its bound to
# a variable the operator configures, so the numbers live on the pan and the rule
# carries only comparisons. The properties asserted are the ones that make that
# honest: that a bound really is read from configuration, that an unconfigured
# bound produces a refusal rather than a default, and that the reference draft
# contains no literals to have crept back in.


def test_the_reference_draft_is_served_and_contains_no_threshold_of_its_own(
    client: TestClient,
) -> None:
    """The draft this project offers must not smuggle in a number.

    Asserted over the served JSON rather than the Python object, because the JSON
    is what an operator reads before deciding whether to post it. A literal here
    would be indistinguishable, in a recommendation, from a figure the operator
    had configured themselves.
    """
    response = client.get("/api/decisions/rules/reference-draft")
    assert response.status_code == 200, response.text
    draft = response.json()

    assert draft["name"] == REFERENCE_RULE_NAME
    assert draft["criteria"], "the draft must actually contain criteria"
    for criterion in draft["criteria"]:
        assert criterion["bound_variable"] is not None, (
            f"{criterion['variable']} carries a literal bound; every criterion in "
            "the reference draft must read its bound from the operator's "
            "configuration"
        )
        assert criterion["threshold"] is None
        assert criterion["lower_bound"] is None
        assert criterion["upper_bound"] is None

    # And every name used is one the catalogue actually publishes, which is what
    # makes the draft postable rather than merely well-formed JSON.
    catalogued = {variable.name for variable in DECISION_VARIABLE_CATALOG}
    for criterion in draft["criteria"]:
        assert criterion["variable"] in catalogued
        assert criterion["bound_variable"] in catalogued


def test_a_stored_rule_read_back_still_says_where_each_bound_came_from(
    client: TestClient,
) -> None:
    """Reading a rule back must not make it look as though it asserted no bound.

    The draft endpoint and the stored-rule endpoint serialise criteria by different
    paths, so it is possible for one of them to drop ``bound_variable`` while the
    other keeps it. When that happens a client auditing a stored rule sees every
    threshold as null and can only recover where the bounds came from by parsing
    the comparator string, which is exactly the coupling the structured field
    exists to avoid.
    """
    draft = client.get("/api/decisions/rules/reference-draft").json()
    stored = client.post("/api/decisions/rules", json=draft)
    assert stored.status_code == 201, stored.text
    rule_id = stored.json()["rule"]["rule_id"]

    for path in (f"/api/decisions/rules/{rule_id}", "/api/decisions/rules?limit=1"):
        body = client.get(path).json()
        rule = body.get("rule", body.get("rules", [body])[0])
        assert rule["criteria"], f"{path} served a rule with no criteria"
        for criterion in rule["criteria"]:
            assert criterion["bound_variable"] is not None, (
                f"{path} lost the bound on {criterion['variable']}; a rule whose "
                "criteria read the operator's configuration must not read back as "
                "one that specified nothing"
            )
            assert criterion["threshold"] is None


def test_the_served_reference_draft_can_be_posted_unchanged(
    client: TestClient,
) -> None:
    """What is served must be writable, or the offer is decorative.

    Posting it exercises the same validation an operator's own hand-written rule
    goes through, which is what makes this a test of the draft rather than of the
    endpoint.
    """
    draft = client.get("/api/decisions/rules/reference-draft").json()
    response = client.post("/api/decisions/rules", json=draft)
    assert response.status_code == 201, response.text
    stored = response.json()["rule"]
    assert stored["name"] == REFERENCE_RULE_NAME
    assert stored["version"] == 1
    assert stored["uses_ml"] is False, (
        "the reference draft reads no prediction variable, so it must be recorded "
        "as a physics-and-rules rule"
    )


def test_a_bound_is_read_from_the_pan_rather_than_asserted_by_the_rule(
    client: TestClient, database: FakeDatabase
) -> None:
    """The same written rule must give different answers for different pans.

    The two pans are identical in every respect except their configured harvest
    target, and the forecast on one puts it at the other's target. If the rule
    carried the number, both would resolve the same way and this test would fail
    with the two actions equal.
    """
    seed(database)
    draft = client.get("/api/decisions/rules/reference-draft").json()
    # Isolate the harvest criterion. The full draft also carries flood and
    # transfer criteria, which on this fixture outrank harvest, so leaving them in
    # would let this test pass for the wrong reason -- or fail while the bound
    # under test was working exactly as intended.
    draft["criteria"] = [
        criterion
        for criterion in draft["criteria"]
        if criterion["bound_variable"] == "target_harvest_salinity_g_l"
    ]
    assert client.post("/api/decisions/rules", json=draft).status_code == 201
    refresh_twins(client)

    # Two configurations, one written rule. Both pans are otherwise identical --
    # both are forecast dry, so both sit at the same salinity -- so the only thing
    # that can separate the two answers is the target the operator configured on
    # each. The two figures straddle that shared salinity, which makes the pan
    # below the target and the pan above it; they are fixture values chosen to sit
    # either side, not proposed harvest targets for anything.
    low, high = PANS[0], PANS[1]
    for pan_id, target in ((low, 400.0), (high, 200.0)):
        response = client.patch(
            f"/api/pans/{pan_id}",
            json={"target_harvest_salinity_g_l": target},
        )
        assert response.status_code == 200, response.text

    low_action = advise(client, low)
    high_action = advise(client, high)
    assert low_action.status_code == 200, low_action.text
    assert high_action.status_code == 200, high_action.text

    assert low_action.json()["recommendation"] == "HARVEST NOW", (
        "with a target above this pan's post-rain salinity, the harvest criterion "
        "must fire"
    )
    assert high_action.json()["recommendation"] == "WAIT AND RECHECK", (
        "with a target below the same water, the harvest criterion must not fire. "
        "A rule carrying its own threshold would give both pans the same answer and "
        "fail here"
    )


def test_the_audit_line_names_the_configured_field_the_bound_came_from(
    client: TestClient, database: FakeDatabase
) -> None:
    """A reader must be able to see that a bound was configured, not computed.

    Without ``bound_variable`` on the match, the audit line would show a resolved
    number and give no way to tell it from a threshold the rule had asserted.
    """
    seed(database)
    draft = client.get("/api/decisions/rules/reference-draft").json()
    assert client.post("/api/decisions/rules", json=draft).status_code == 201
    refresh_twins(client)
    response = advise(client, PANS[0])
    assert response.status_code == 200, response.text
    body = response.json()

    matches = body["criteria"]
    assert matches, "the fixture is built so at least one criterion fires"
    for match in matches:
        assert match["bound_variable"] is not None
        assert match["threshold"] is not None, (
            "the resolved figure travels too, so the comparison can be re-checked "
            "without re-running the engine"
        )
        assert match["comparator"], (
            "the human-readable form travels alongside the structured fields, so a "
            "client can show either"
        )
        assert match["rationale"], (
            "the author's stated reason is part of the audit; an action shown "
            "without it is advice rather than a decision anyone can review"
        )
    # The bound is named in the comparator as the configured field, not as the
    # figure, because the figure belongs to the pan and not to the rule.
    assert any(
        match["bound_variable"] in match["comparator"] for match in matches
    ), "a bound read from configuration must read as its own name"

    # And the variable is recorded as an input, not only as a bound: a rule that
    # reads the operator's target reads it.
    read_variables = {entry["variable"] for entry in body["inputs_used"]}
    assert "target_harvest_salinity_g_l" in read_variables


def test_a_pan_with_no_configured_thresholds_is_refused_not_defaulted(
    client: TestClient, database: FakeDatabase
) -> None:
    """An unconfigured bound must withhold the answer, not assume a number.

    The fixture's pans have limits set directly in the database; clearing them
    here is the only way to reach the case that matters, and it is the case the
    whole design exists for. The refusal has to arrive instead of a
    recommendation.
    """
    seed(database)
    draft = client.get("/api/decisions/rules/reference-draft").json()
    assert client.post("/api/decisions/rules", json=draft).status_code == 201
    refresh_twins(client)

    pan_id = PANS[0]
    response = client.patch(f"/api/pans/{pan_id}", json={"risk_thresholds": {}})
    assert response.status_code == 200, response.text
    assert response.json()["risk_thresholds"]["flood_depth_cm"] is None

    advised = advise(client, pan_id)
    assert advised.status_code == 409, advised.text
    assert advised.json()["code"] == "insufficient_evidence", (
        "a rule that needs limits nobody configured has not been evaluated; the "
        "refusal has to say the evidence is missing rather than that the rule was "
        "undecided, because the rule is fine and the configuration is not"
    )
    # It names what is missing, so the operator is told which limit to set.
    for variable in ("flood_depth_cm", "dilution_salinity_g_l", "target_flow_cm"):
        assert variable in advised.json()["detail"]
    assert "harvest" in advised.text.lower() or "target" in advised.text.lower()


def test_unreadable_evidence_and_a_rule_that_matched_nothing_are_not_the_same_refusal(
    client: TestClient, database: FakeDatabase
) -> None:
    """The two reasons for naming no action must stay distinguishable.

    They send the operator to different places. Unreadable evidence is a
    configuration or recording gap, fixed at the pan. A rule that read everything
    and matched nothing is a gap in the rule, fixed at the author. Reporting both
    the same way tells them to go and look in the wrong place.
    """
    seed(database)
    refresh_twins(client)

    # A rule with no fallback, whose only criterion cannot match on these pans.
    # Every input is read; nothing fires because nothing is true.
    silent = {
        "name": "Salinity target nothing reaches",
        "operational_rationale": (
            "Name an action only when the pan genuinely satisfies the one condition "
            "this rule cares about, and say nothing otherwise."
        ),
        "actions": RULE_ACTIONS,
        "criteria": [
            {
                "variable": "simulated_post_rain_salinity_g_l",
                "operator": "gte",
                "threshold": 100000.0,
                "action": "HARVEST NOW",
                "rationale": "Never satisfied here; exercises the undecided path.",
            }
        ],
        "combinator": "highest_rank",
        "uses_ml": False,
    }
    assert client.post("/api/decisions/rules", json=silent).status_code == 201

    evaluated = advise(client, PANS[0])
    assert evaluated.status_code == 409, evaluated.text
    assert evaluated.json()["code"] == "rule_undecided", (
        "a rule that was fully evaluated and matched nothing is a statement about "
        "the rule, and must not be reported as missing evidence"
    )

    # The same rule, but bound to an input nobody configured. Now there is a
    # genuine gap, and the refusal has to say so.
    bound = client.get("/api/decisions/rules/reference-draft").json()
    stored = client.post("/api/decisions/rules", json=bound)
    assert stored.status_code == 201, stored.text
    assert client.patch(f"/api/pans/{PANS[0]}", json={"risk_thresholds": {}}).status_code == 200

    unread = advise(client, PANS[0], rule_id=stored.json()["rule"]["rule_id"])
    assert unread.status_code == 409, unread.text
    assert unread.json()["code"] == "insufficient_evidence"


def test_a_configured_threshold_survives_registration_and_update(
    client: TestClient, database: FakeDatabase
) -> None:
    """The operator's limits have to be settable and readable, or nothing works.

    Without this the reference rule is unusable in practice: every bound it names
    would be permanently unconfigured, because no endpoint accepted one.
    """
    seed(database)
    response = client.patch(
        f"/api/pans/{PANS[0]}",
        json={
            "risk_thresholds": {
                "flood_depth_cm": 41.5,
                "dilution_salinity_g_l": 244.0,
                "critical_rainfall_24h_mm": 77.0,
                "target_flow_cm": 1.25,
                "max_observation_age_hours": 36.0,
            }
        },
    )
    assert response.status_code == 200, response.text
    stored = response.json()["risk_thresholds"]
    assert stored["flood_depth_cm"] == 41.5
    assert stored["dilution_salinity_g_l"] == 244.0
    assert stored["max_observation_age_hours"] == 36.0

    read_back = client.get(f"/api/pans/{PANS[0]}").json()
    assert read_back["risk_thresholds"] == stored, (
        "what an operator configured must come back on the next read, or the "
        "configured value is not really stored"
    )


def test_a_stale_reading_is_refused_as_insufficient_evidence(
    client: TestClient, database: FakeDatabase
) -> None:
    """An old reading produces a stated refusal, not a decision about old water.

    The fixture's newest reading is in March 2026 and the pan is given a two-hour
    window, so every reading on it is far older than the operator allows. The
    refusal names the age and the limit, because an operator who has to raise a
    limit should be able to see what they are raising it past.
    """
    seed(database)
    draft = client.get("/api/decisions/rules/reference-draft").json()
    assert client.post("/api/decisions/rules", json=draft).status_code == 201
    refresh_twins(client)

    response = client.patch(
        f"/api/pans/{PANS[0]}",
        json={"risk_thresholds": {"max_observation_age_hours": 2.0}},
    )
    assert response.status_code == 200, response.text

    advised = advise(client, PANS[0])
    assert advised.status_code == 409, advised.text
    body = advised.json()
    assert body["code"] == "insufficient_evidence"
    assert "2 h" in body["detail"]
    assert "not been read for" in body["detail"]


def test_a_fresh_reading_within_the_configured_window_is_advised_normally(
    client: TestClient, database: FakeDatabase
) -> None:
    """The staleness check must not refuse a pan whose reading is current.

    A window wide enough to admit the fixture's March readings, so the only thing
    that can be refused here is the staleness rule itself. Without this, a check
    that refused everything would pass the test above.
    """
    seed(database)
    draft = client.get("/api/decisions/rules/reference-draft").json()
    assert client.post("/api/decisions/rules", json=draft).status_code == 201
    refresh_twins(client)

    # The whole thresholds block is sent, because an update replaces it whole: the
    # fixture's other limits have to be restated or the rule would be unevaluable
    # for want of them and this test would be measuring the wrong refusal.
    response = client.patch(
        f"/api/pans/{PANS[0]}",
        json={
            "risk_thresholds": {
                "flood_depth_cm": 45.0,
                "dilution_salinity_g_l": 250.0,
                "critical_rainfall_24h_mm": 80.0,
                "target_flow_cm": 1.5,
                "max_observation_age_hours": 100_000.0,
            }
        },
    )
    assert response.status_code == 200, response.text

    advised = advise(client, PANS[0])
    assert advised.status_code == 200, advised.text
    assert "within the" in advised.json()["notice"], (
        "a recommendation from a checked reading says so, so the reader can tell a "
        "checked age from an unchecked one"
    )


def test_a_pan_with_no_freshness_limit_says_that_none_was_checked(
    client: TestClient, database: FakeDatabase
) -> None:
    """Silence about age must not read as a freshness check having passed.

    The fixture stores no ``max_observation_age_hours``, so the engine cannot
    check anything -- and the recommendation has to say that rather than let the
    reader assume a check it never made.
    """
    seed(database)
    draft = client.get("/api/decisions/rules/reference-draft").json()
    assert client.post("/api/decisions/rules", json=draft).status_code == 201
    refresh_twins(client)

    advised = advise(client, PANS[0])
    assert advised.status_code == 200, advised.text
    assert "No freshness limit is configured" in advised.json()["notice"]


def test_a_demonstration_recommendation_is_labelled_on_the_response(
    client: TestClient, database: FakeDatabase
) -> None:
    """A recommendation built from demonstration data must say so.

    Read from the twin's recorded provenance, so the label is derived rather than
    passed in by whoever happens to call the endpoint.
    """
    seed(database)
    draft = client.get("/api/decisions/rules/reference-draft").json()
    assert client.post("/api/decisions/rules", json=draft).status_code == 201

    pan_id = PANS[0]
    created = client.post(
        "/api/observations",
        json={
            "pan_id": pan_id,
            "timestamp": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "salinity_g_l": 295.0,
            "water_depth_cm": DEPTH_CM,
            "data_source": "demonstration",
        },
    )
    assert created.status_code == 201, created.text
    refresh_twins(client)

    advised = advise(client, pan_id)
    assert advised.status_code == 200, advised.text
    body = advised.json()
    assert body["twin"]["is_demonstration"] is True
    assert "observation_data_source" in body["twin"]["demonstration_sources"]
    assert "DEMONSTRATION ONLY" in body["notice"]


def test_a_recommendation_from_real_records_carries_no_demonstration_label(
    client: TestClient, database: FakeDatabase
) -> None:
    """The label must be absent, not merely false, on a real recommendation.

    Asserted on the source list as well as the flag, because a flag left on by
    accident is caught by the first assertion and a source list populated wrongly
    is not.
    """
    seed(database)
    draft = client.get("/api/decisions/rules/reference-draft").json()
    assert client.post("/api/decisions/rules", json=draft).status_code == 201
    refresh_twins(client)

    advised = advise(client, PANS[0])
    assert advised.status_code == 200, advised.text
    body = advised.json()
    assert body["twin"]["is_demonstration"] is False
    assert body["twin"]["demonstration_sources"] == []
    assert "DEMONSTRATION ONLY" not in body["notice"]


def test_an_operator_configured_threshold_is_not_a_hardcoded_default(
    client: TestClient, database: FakeDatabase
) -> None:
    """Two pans, two different configured limits, two different flood decisions.

    The flood criterion is the one that would do real damage if its bound were
    baked in, so it is the one this checks: the same predicted depth has to drain
    the pan that configured a low limit and leave the pan that configured a high
    one alone.
    """
    seed(database)
    draft = client.get("/api/decisions/rules/reference-draft").json()
    # Take the harvest and dilution criteria out, so this test isolates the flood
    # criterion rather than passing because some other criterion happened to fire.
    draft["criteria"] = [
        criterion
        for criterion in draft["criteria"]
        if criterion["bound_variable"] == "flood_depth_cm"
    ]
    assert client.post("/api/decisions/rules", json=draft).status_code == 201
    refresh_twins(client)

    shallow, deep = PANS[3], PANS[0]
    for pan_id, limit in ((shallow, 1.0), (deep, 10_000.0)):
        response = client.patch(
            f"/api/pans/{pan_id}", json={"risk_thresholds": {"flood_depth_cm": limit}}
        )
        assert response.status_code == 200, response.text

    flooded = advise(client, shallow)
    safe = advise(client, deep)
    assert flooded.status_code == 200, flooded.text
    assert safe.status_code == 200, safe.text
    assert flooded.json()["recommendation"] == "PROTECT / DRAIN", (
        "a 1 cm flood limit is below any predicted depth, so the flood criterion "
        "must fire"
    )
    assert safe.json()["recommendation"] == "WAIT AND RECHECK", (
        "a 10,000 cm flood limit is above any predicted depth, so it must not"
    )


def test_a_bound_naming_an_uncatalogued_variable_is_refused_at_write_time(
    client: TestClient,
) -> None:
    """A typo in a bound is a rejected rule, not an unevaluable recommendation.

    The same reasoning the catalogue applies to a mistyped variable: a mistake in
    the rule should surface when the rule is written, while its author is reading
    the error, rather than as a refusal on some pan weeks later.
    """
    draft = client.get("/api/decisions/rules/reference-draft").json()
    draft["criteria"][0]["bound_variable"] = "target_harvest_salinity"
    response = client.post("/api/decisions/rules", json=draft)
    assert response.status_code == 422, response.text


def test_a_bound_in_the_wrong_unit_is_refused_at_write_time(
    client: TestClient,
) -> None:
    """Comparing g/L against a depth must not be storable as a rule.

    The comparison would succeed and produce an action, which is the problem: it
    would be a fact about the unit mistake and would be presented as a decision
    about a pan.
    """
    draft = client.get("/api/decisions/rules/reference-draft").json()
    draft["criteria"][0]["bound_variable"] = "target_flow_cm"
    response = client.post("/api/decisions/rules", json=draft)
    assert response.status_code == 422, response.text
    assert "cm" in response.text


def test_a_criterion_cannot_bind_a_bound_and_a_literal_together(
    client: TestClient,
) -> None:
    """Exactly one bound, or the rule says something its author did not write.

    A criterion carrying both would compare against whichever won, silently. The
    ambiguity has to be refused rather than resolved.
    """
    draft = client.get("/api/decisions/rules/reference-draft").json()
    draft["criteria"][0]["threshold"] = 250.0
    response = client.post("/api/decisions/rules", json=draft)
    assert response.status_code == 422, response.text


def test_a_criterion_cannot_bind_itself_as_its_own_bound(
    client: TestClient,
) -> None:
    """A variable compared with itself is not a comparison at all."""
    draft = client.get("/api/decisions/rules/reference-draft").json()
    draft["criteria"][0]["bound_variable"] = draft["criteria"][0]["variable"]
    response = client.post("/api/decisions/rules", json=draft)
    assert response.status_code == 422, response.text


def test_the_new_configured_limits_are_published_in_the_variable_catalogue(
    client: TestClient,
) -> None:
    """A bound may only name a variable the catalogue publishes.

    Published because the alternative is a rule that validates and then cannot be
    read by anyone trying to work out what it compares against.
    """
    catalogue = client.get("/api/decisions/variables").json()
    by_name = {variable["name"]: variable for variable in catalogue["variables"]}
    for name in ("flood_depth_cm", "dilution_salinity_g_l", "critical_rainfall_24h_mm"):
        assert name in by_name, f"{name} is catalogued but not published"
        assert by_name[name]["reads_from"] == "pan_registration", (
            f"{name} is a configured limit, so a reader must be able to see it is "
            "operator configuration rather than a measurement"
        )
        assert by_name[name]["nullable_reason"], (
            f"{name} can be unconfigured, and the catalogue has to say what that "
            "means for a rule that names it"
        )
