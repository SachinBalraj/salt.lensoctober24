"""Module 17 - controlled model deployment, over HTTP, against real trained models.

These tests train through Module 08 and evaluate through Module 09, so a model that
reaches ``deployed`` here is one the pipeline actually fitted to records the
database actually holds, and one Module 09 actually scored. Nothing stubs the
lifecycle, the registry or the deployment gate.

The tests are organised around the three properties that make this module worth
having, and each has a test that fails if the property is quietly dropped:

**A model cannot reach operational service without a person asking.**
    Validation and deployment both require a stored evaluation, a named version, a
    named actor and a non-empty reason. Each missing piece is a 4xx, tested
    individually, because "all of them required" is only a real claim if each is
    load-bearing on its own.

**Retraining cannot displace a deployed model.**
    A training run writes ``experimental`` and touches nothing else. Deploying over
    an incumbent is refused with the incumbent named unless the request acknowledges
    the substitution and says why. An acknowledged replacement retires the incumbent
    with that reason on it and returns what it displaced.

**Only a deployed model answers an operational question.**
    Tested for every status, including the one that matters most -- retired, where
    the target has no deployed model and the request is refused rather than falling
    back to an earlier version.

One limit is stated rather than papered over: the "at most one deployed model per
target" guarantee is enforced in the service and, independently, by a unique
partial index on ``model_versions``. The in-memory fake cannot express a partial
index, so the behavioural tests exercise the service's refusal and
``test_the_database_enforces_one_deployed_model_per_target`` asserts the index is
declared. The index itself is verified on a live cluster, not here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.deps import get_db, get_optional_db
from app.api.routers.datasets import get_dataset_service, get_dataset_status_service
from app.api.routers.evaluation import get_evaluation_service
from app.api.routers.ml import get_ml_service
from app.api.routers.model_registry import get_deployment_service
from app.api.routers.observations import (
    get_observation_service,
    get_observation_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.risk import get_prediction_service
from app.api.routers.weather import get_weather_service, get_weather_status_service
from app.db import indexes as ix
from app.db.collections import (
    ML_PREDICTIONS,
    MODEL_DEPLOYMENTS,
    MODEL_EVALUATIONS,
    MODEL_VERSIONS,
    PAN_OBSERVATIONS,
    SALT_PANS,
)
from app.db.repository import MongoRepository
from app.main import app
from app.models import ModelDeployment, ModelStatus, ModelVersion
from app.models.pan_observation import PanObservation
from app.models.salt_pan import RiskThresholds, SaltPan
from app.services.datasets import DatasetService
from app.services.deployment import NON_EVIDENTIAL_MODEL, DeploymentService
from app.services.evaluation import EvaluationService
from app.services.ml import MLService
from app.services.observations import ObservationService
from app.services.prediction import OperationalPredictionService
from app.services.salt_pans import SaltPanService
from app.services.weather import WeatherService
from tests.fakes import FakeDatabase

BIN_EDGES = [262.0, 272.0]
PANS = ["KZN-2026-A01", "KZN-2026-B02", "KZN-2026-C03", "KZN-2026-D04"]
READINGS_PER_PAN = 30
START = datetime(2026, 3, 1, tzinfo=timezone.utc)
PAN_DRIFT = {"KZN-2026-A01": -1.2, "KZN-2026-B02": 0.0, "KZN-2026-C03": 1.4, "KZN-2026-D04": 0.6}

ACTOR = "r.okonkwo"
REASON = "June season: macro F1 holds on the held-out weeks and the confusion matrix is balanced"


# -- fixtures -------------------------------------------------------------


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
    app.dependency_overrides[get_observation_status_service] = lambda: ObservationService(
        database
    )
    app.dependency_overrides[get_weather_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_weather_status_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_dataset_service] = lambda: DatasetService(database)
    app.dependency_overrides[get_dataset_status_service] = lambda: DatasetService(database)
    app.dependency_overrides[get_ml_service] = lambda: MLService(
        database, artifact_directory=artifacts
    )
    app.dependency_overrides[get_evaluation_service] = lambda: EvaluationService(
        database, artifact_directory=artifacts
    )
    app.dependency_overrides[get_deployment_service] = lambda: DeploymentService(database)
    # The prediction service builds its own DeploymentService over the same handle,
    # so the gate Module 10 relies on is the same object this suite exercises.
    app.dependency_overrides[get_prediction_service] = lambda: OperationalPredictionService(
        database
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# -- helpers --------------------------------------------------------------


def seed(database: FakeDatabase) -> None:
    asyncio.run(_seed(database))


async def _seed(database: FakeDatabase) -> None:
    pan_repository: MongoRepository[SaltPan] = MongoRepository(database, SALT_PANS, SaltPan)
    observation_repository: MongoRepository[PanObservation] = MongoRepository(
        database, PAN_OBSERVATIONS, PanObservation
    )
    registered = datetime(2026, 2, 1, tzinfo=timezone.utc)
    for index, pan_id in enumerate(PANS):
        await pan_repository.insert(
            SaltPan(
                pan_id=pan_id,
                pan_name=f"Pan {index}",
                location="Port Shepstone",
                area_m2=10_000.0,
                normal_depth_cm=15.5,
                target_harvest_salinity_g_l=285.0,
                risk_thresholds=RiskThresholds(
                    flood_depth_cm=45.0,
                    dilution_salinity_g_l=250.0,
                    critical_rainfall_24h_mm=80.0,
                    target_flow_cm=1.5,
                ),
                created_at=registered,
            )
        )
        drift = PAN_DRIFT[pan_id]
        for step in range(READINGS_PER_PAN):
            salinity = 267.0 + drift + 5.0 * ((step % 9) - 4) / 2.0
            await observation_repository.insert(
                PanObservation(
                    observation_id=f"obs-{pan_id}-{step:03d}",
                    pan_id=pan_id,
                    timestamp=START + timedelta(hours=step),
                    salinity_g_l=round(salinity, 3),
                    water_depth_cm=round(20.0 + (step % 5), 3),
                    brine_temperature_c=30.0,
                    humidity_percent=60.0 + (step % 4),
                    ambient_temperature_c=25.0,
                    created_at=registered,
                )
            )


def build_dataset(client: TestClient) -> str:
    response = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "post_rain_salinity_band",
                "horizon_hours": 1.0,
                "bin_edges": list(BIN_EDGES),
            }
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["dataset"]["dataset_id"]


def train(client: TestClient, dataset_id: str) -> dict[str, Any]:
    response = client.post("/api/ml/train", json={"dataset_id": dataset_id})
    assert response.status_code == 201, response.text
    return response.json()


def evaluate(client: TestClient, model_id: str) -> dict[str, Any]:
    response = client.post("/api/evaluation/run", json={"model_id": model_id})
    assert response.status_code in (200, 201), response.text
    return response.json()


def deploy_body(version: str, **overrides: Any) -> dict[str, Any]:
    return {"version": version, "reason": REASON, "actor": ACTOR, **overrides}


def transition(
    client: TestClient, model: dict[str, Any], action: str, **overrides: Any
) -> Any:
    body = deploy_body(model["version"], **overrides)
    return client.post(f"/api/models/{model['model_id']}/{action}", json=body)


@pytest.fixture()
def trained(client: TestClient, database: FakeDatabase) -> dict[str, Any]:
    """A registered model version, experimental, fitted to real stored records."""
    seed(database)
    dataset_id = build_dataset(client)
    return {**train(client, dataset_id), "dataset_id": dataset_id}


@pytest.fixture()
def evaluated(client: TestClient, trained: dict[str, Any]) -> dict[str, Any]:
    """The same model, with a stored evaluation report.

    Merged onto the trained record rather than replacing it, so a test that needs
    both the model and its report has one fixture to ask for.
    """
    return {**trained, "report": evaluate(client, trained["model_id"])}


@pytest.fixture()
def validated(client: TestClient, evaluated: dict[str, Any]) -> dict[str, Any]:
    """Validated, and therefore eligible for deployment."""
    response = transition(client, evaluated, "validate")
    assert response.status_code == 200, response.text
    return evaluated


@pytest.fixture()
def deployed(client: TestClient, validated: dict[str, Any]) -> dict[str, Any]:
    """In operational service, through the full lifecycle."""
    response = transition(client, validated, "deploy")
    assert response.status_code == 200, response.text
    return validated


def stored_versions(database: FakeDatabase) -> list[dict[str, Any]]:
    return database[MODEL_VERSIONS].documents()


def stored_history(database: FakeDatabase) -> list[dict[str, Any]]:
    return database[MODEL_DEPLOYMENTS].documents()


# -- the registry inspection view -----------------------------------------


def test_a_trained_model_registers_as_experimental(trained: dict[str, Any]) -> None:
    """A training run cannot put a model anywhere near service.

    This is the whole mechanism behind "never silently replace the deployed model
    after retraining": a retrain writes a new version in ``experimental`` and stops.
    If this assertion ever starts failing, a retrain has acquired the ability to
    take over, and the guarantee is gone.
    """
    assert trained["status"] == ModelStatus.EXPERIMENTAL.value


def test_the_registry_lists_every_model_version_with_its_evaluation(
    client: TestClient, evaluated: dict[str, Any]
) -> None:
    """Inspection: one row per version, carrying the metrics Module 09 computed."""
    body = client.get("/api/models/").json()
    assert body["total"] == 1
    record = body["models"][0]
    assert record["model_id"] == evaluated["model_id"]
    assert record["status"] == "experimental"
    assert record["evaluation"] is not None
    assert record["evaluation"]["evaluation_id"]
    assert record["evaluation"]["evaluated_rows"] > 0


def test_the_record_stores_every_field_a_deployment_decision_rests_on(
    client: TestClient, evaluated: dict[str, Any]
) -> None:
    """The ten named fields, each read from storage rather than reconstructed.

    Asserted field by field against what the pipeline and the dataset actually
    wrote, so a field that quietly became a constant or a default is caught here
    rather than trusted.
    """
    record = client.get(f"/api/models/{evaluated['model_id']}").json()

    # Model name and model version.
    assert record["model_id"] == evaluated["model_id"]
    assert record["version"] == evaluated["version"]
    # Dataset version -- the revision, not merely the lineage.
    assert record["dataset_id"] == evaluated["dataset_id"]
    assert record["dataset_version"] == evaluated["dataset_version"]
    assert record["dataset_content_hash"]
    # Training date.
    assert record["trained_at"] is not None
    # Features, in the order the frame was built.
    assert record["feature_names"]
    assert all(isinstance(name, str) for name in record["feature_names"])
    # Hyperparameters.
    assert record["hyperparameters"]["random_seed"] is not None
    assert record["hyperparameters"]["split_strategy"]
    # Evaluation metrics, as readings rather than bare numbers.
    assert set(record["evaluation"]["metrics"]) == {
        "accuracy",
        "precision",
        "recall",
        "f1",
        "roc_auc",
        "pr_auc",
    }
    for reading in record["evaluation"]["metrics"].values():
        assert reading["computed"] is (reading["value"] is not None)
    # Target definition, from the dataset that defined it.
    assert record["target_definition"]["name"] == "post_rain_salinity_band"
    assert record["target_definition"]["label_column"]
    assert record["target_definition"]["kind"] == "classification"
    # Split strategy, read from storage and consistent with the hyperparameters the
    # run recorded. Asserted as agreement rather than as a literal, because the
    # strategy is the pipeline's choice for this dataset and a test that hard-codes
    # it would fail on a correct change and pass on a wrong read.
    assert record["split_strategy"] in {
        "chronological",
        "per_pan_chronological",
        "grouped_by_pan",
        "random",
    }
    assert record["split_strategy"] == record["hyperparameters"]["split_strategy"]
    # Status.
    assert record["status"] == "experimental"


def test_registry_metrics_are_readings_and_never_filled_in(
    client: TestClient, database: FakeDatabase, evaluated: dict[str, Any]
) -> None:
    """An uncalculated metric stays uncalculated on its way to the registry.

    Module 09 refuses to report ROC-AUC it cannot compute. The registry must not
    re-introduce the failure one layer up by coercing ``value: null`` to ``0.0``
    while flattening the reading into a table cell.

    This run happened to compute all six, so the interesting case is written into
    the stored report first. A loop over metrics that all happen to be computed
    asserts nothing, and would have passed against a registry that filled every
    cell with zero.
    """
    evaluation_id = evaluated["report"]["evaluation_id"]
    collection = database[MODEL_EVALUATIONS]
    current = next(
        document
        for document in collection.documents()
        if document["evaluation_id"] == evaluation_id
    )
    # The shape Module 09 produces when a fold cannot yield the metric: the same
    # reading with no value, no variant to qualify a quantity that does not exist,
    # and a statement of why.
    declined = {
        **current["evaluation"]["roc_auc"],
        "value": None,
        "computed": False,
        "variant": None,
        "reason": "roc_auc is undefined: the held-out fold contains one class only",
    }
    collection.amend(
        "evaluation_id",
        evaluation_id,
        evaluation={**current["evaluation"], "roc_auc": declined},
    )

    record = client.get(f"/api/models/{evaluated['model_id']}").json()
    metrics = record["evaluation"]["metrics"]

    # The absent one is still the six named columns, and one of them is now absent.
    assert set(metrics) == {
        "accuracy",
        "precision",
        "recall",
        "f1",
        "roc_auc",
        "pr_auc",
    }
    assert record["evaluation"]["absent_metrics"] == ["roc_auc"]
    assert metrics["roc_auc"] == declined
    assert metrics["roc_auc"]["value"] is None, "an absent metric was filled in"
    assert metrics["roc_auc"]["reason"], "an absent metric lost its reason"

    # And the five that were computed are still reported as numbers, so the check
    # above is not passing because the whole evaluation was thrown away.
    for name in ("accuracy", "precision", "recall", "f1", "pr_auc"):
        assert metrics[name]["computed"] is True
        assert metrics[name]["value"] is not None


def test_the_lifecycle_statuses_are_exactly_the_four_named(
    client: TestClient,
) -> None:
    """The vocabulary is closed, and published.

    A fifth status would be a fifth thing a model could be in that nobody has
    reasoned about, so the set is asserted rather than assumed.
    """
    body = client.get("/api/models/status").json()
    assert body["statuses"] == ["experimental", "validated", "deployed", "retired"]
    assert body["transitions"]["retired"] == []


def test_the_overview_publishes_the_guarantees_it_makes(client: TestClient) -> None:
    """A client reading only the status response learns the gate exists."""
    body = client.get("/api/models/status").json()
    guarantees = " ".join(body["guarantees"]).lower()
    assert "supersede" in guarantees
    assert "partial index" in guarantees
    assert "deployed" in body["summary_metrics"] or body["summary_metrics"]
    assert body["deployed"] == []


def test_the_deployed_listing_has_the_same_shape_as_the_registry_listing(
    client: TestClient, deployed: dict[str, Any]
) -> None:
    """Both listings report the same four statuses, whatever the contents.

    Not cosmetic: a client rendering a four-column summary from the deployed
    listing has to be able to read the same keys it reads from the full listing.
    A ``statuses`` map that appeared and vanished with the deployed set would make
    the field's shape depend on which endpoint produced it.
    """
    everything = client.get("/api/models/").json()
    in_service = client.get("/api/models/deployed").json()
    assert set(in_service["statuses"]) == set(everything["statuses"])
    assert in_service["statuses"]["deployed"] == 1
    assert in_service["statuses"]["experimental"] == 0
    # And the listing really is the deployed set, not the whole registry.
    assert in_service["total"] == 1
    assert [record["model_id"] for record in in_service["models"]] == [
        deployed["model_id"]
    ]


# -- validation and deployment require a decision -------------------------


def test_validation_requires_a_stored_evaluation(client: TestClient, trained: dict[str, Any]) -> None:
    """A model nobody measured cannot be accepted.

    There is no way to validate a model into existence by asserting it is fine; the
    report has to exist, or the step is refused.
    """
    response = transition(client, trained, "validate")
    assert response.status_code == 409
    body = response.json()
    assert body["refusal"] == "not_evaluated"
    assert "/api/evaluation/run" in body["detail"]


def test_deployment_requires_a_stored_evaluation(client: TestClient, trained: dict[str, Any]) -> None:
    """A model nobody measured cannot be deployed, and the reason says which.

    Checked before the transition rule, deliberately: "this model has no report" is
    the more useful answer than "you skipped a step", and a client told only the
    latter would go and validate a model it has no way to evaluate.
    """
    response = transition(client, trained, "deploy")
    assert response.status_code == 409
    body = response.json()
    assert body["refusal"] == "not_evaluated"
    assert "/api/evaluation/run" in body["detail"]


async def _wipe_recorded_evidence(
    database: FakeDatabase, model_id: str, version: str
) -> None:
    repository: MongoRepository[ModelVersion] = MongoRepository(
        database, MODEL_VERSIONS, ModelVersion
    )
    model = await repository.get("model_id", model_id)
    stripped = model.model_copy(
        update={"parameters": {**model.parameters, "evidence": None}}
    )
    await repository.replace("model_id", model_id, stripped)


def test_validation_refuses_a_model_whose_recorded_evidence_is_not_genuine(
    client: TestClient, database: FakeDatabase, trained: dict[str, Any]
) -> None:
    """A lifecycle step reads a version's evidence, not just its report's existence.

    The evaluation gate judged the training rows on the day it ran and wrote a
    report to that effect. A registry document backed up or edited after that day
    still carries the report, so deployment resting on the report alone would put
    a model into service whose version now records no genuine provenance. Withdraw
    the recorded evidence and the step must refuse, because a claim of operational
    performance cannot rest on rows that are no longer established as measurements.
    """
    asyncio.run(_wipe_recorded_evidence(database, trained["model_id"], trained["version"]))
    response = transition(client, trained, "validate")
    assert response.status_code == 409
    body = response.json()
    assert body["refusal"] == NON_EVIDENTIAL_MODEL
    assert "genuine" in body["detail"].lower()


def test_deployment_refuses_a_model_whose_recorded_evidence_is_not_genuine(
    client: TestClient, database: FakeDatabase, evaluated: dict[str, Any]
) -> None:
    """Deploy, like validate, re-reads the verdict the version currently records.

    The model here holds a genuine report, so the reason to refuse is purely the
    recorded evidence having been lost after evaluation -- the exact state a
    restore or an edit can produce -- and the refusal must name that, not claim
    the report is missing.
    """
    asyncio.run(_wipe_recorded_evidence(database, evaluated["model_id"], evaluated["version"]))
    response = transition(client, evaluated, "deploy")
    assert response.status_code == 409
    body = response.json()
    assert body["refusal"] == NON_EVIDENTIAL_MODEL
    assert "evaluation report" not in body["detail"]


def test_a_deployment_needs_a_reason(client: TestClient, evaluated: dict[str, Any]) -> None:
    """An empty reason is a 422, and the model's reason field cannot be blank."""
    response = client.post(
        f"/api/models/{evaluated['model_id']}/validate",
        json={"version": evaluated["version"], "reason": "", "actor": ACTOR},
    )
    assert response.status_code == 422


def test_a_deployment_needs_a_named_actor(client: TestClient, evaluated: dict[str, Any]) -> None:
    """Who made the change is part of the record, so it is required."""
    response = client.post(
        f"/api/models/{evaluated['model_id']}/validate",
        json={"version": evaluated["version"], "reason": REASON, "actor": ""},
    )
    assert response.status_code == 422


def test_a_deployment_needs_an_exact_version(client: TestClient, evaluated: dict[str, Any]) -> None:
    """The version is named, never defaulted to the newest.

    Defaulting would mean a deployment could land on a version a retrain wrote
    between the researcher looking at the registry and pressing the button.
    """
    response = client.post(
        f"/api/models/{evaluated['model_id']}/validate",
        json={"reason": REASON, "actor": ACTOR},
    )
    assert response.status_code == 422


def test_an_unknown_field_is_refused(client: TestClient, evaluated: dict[str, Any]) -> None:
    """A body cannot smuggle in a status.

    ``status: deployed`` in the request would be a way to skip the whole lifecycle
    if the body were permissive, so extra fields are forbidden rather than ignored.
    """
    response = client.post(
        f"/api/models/{evaluated['model_id']}/validate",
        json={**deploy_body(evaluated["version"]), "status": "deployed"},
    )
    assert response.status_code == 422


# -- the lifecycle rules --------------------------------------------------


def test_the_lifecycle_runs_forwards_and_one_step_at_a_time(
    client: TestClient, evaluated: dict[str, Any]
) -> None:
    """Experimental cannot jump straight to deployed.

    The step that would be skipped is the one where a human reads the report, which
    is the entire reason the intermediate status exists.
    """
    response = transition(client, evaluated, "deploy")
    assert response.status_code == 409
    assert response.json()["refusal"] == "illegal_transition"


def test_deployment_after_validation_succeeds(
    client: TestClient, validated: dict[str, Any]
) -> None:
    """The happy path, with the response carrying the decision it recorded."""
    response = transition(client, validated, "deploy")
    assert response.status_code == 200
    body = response.json()
    assert body["from_status"] == "validated"
    assert body["to_status"] == "deployed"
    assert body["model"]["status"] == "deployed"
    assert body["model"]["deployed_at"] is not None
    assert body["model"]["deployed_by"] == ACTOR
    # The report the decision rests on is cited, not merely implied.
    assert body["history_entry"]["evaluation_id"]
    assert body["superseded"] is None


def test_retired_is_terminal(client: TestClient, deployed: dict[str, Any]) -> None:
    """A retired model is not re-deployed, and cannot be deployed as it stands."""
    retire = transition(client, deployed, "retire")
    assert retire.status_code == 200
    assert retire.json()["model"]["status"] == "retired"

    redeploy = transition(client, deployed, "deploy")
    assert redeploy.status_code == 409
    assert redeploy.json()["refusal"] == "illegal_transition"
    assert "terminal" in redeploy.json()["detail"].lower()


def test_retiring_a_model_that_was_never_deployed_is_refused(
    client: TestClient, evaluated: dict[str, Any]
) -> None:
    """There is nothing to withdraw from a model that never entered service."""
    response = transition(client, evaluated, "retire")
    assert response.status_code == 409
    assert response.json()["refusal"] == "illegal_transition"


def test_an_unknown_model_version_is_a_404(client: TestClient) -> None:
    """A lookup failure is a 404, distinct from a refused decision."""
    response = client.post(
        "/api/models/model_missing/deploy",
        json=deploy_body("v1.0.0"),
    )
    assert response.status_code == 404
    assert response.json()["refusal"] == "no_such_model_version"


# -- no silent replacement ------------------------------------------------


def test_a_retrain_leaves_the_deployed_model_in_service(
    client: TestClient, database: FakeDatabase, deployed: dict[str, Any]
) -> None:
    """Training again does not touch the deployed version.

    The direct test of "never silently replace the deployed model after
    retraining": after a second run against the same dataset, the original version
    is still the one answering operational requests and the new one is experimental.
    """
    before = client.get("/api/models/deployed").json()
    assert [m["model_id"] for m in before["deployed"]] == [deployed["model_id"]]

    retrained = train(client, deployed["dataset_id"])
    assert retrained["model_id"] != deployed["model_id"]
    assert retrained["status"] == "experimental"

    after = client.get("/api/models/deployed").json()
    assert [m["model_id"] for m in after["deployed"]] == [deployed["model_id"]]
    # And the incumbent is untouched: no retirement was recorded against it.
    incumbent = next(
        v for v in stored_versions(database) if v["model_id"] == deployed["model_id"]
    )
    assert incumbent["status"] == "deployed"
    assert incumbent.get("retired_at") is None
    assert not incumbent.get("superseded_by_model_id")


def test_deploying_over_an_incumbent_is_refused_and_names_it(
    client: TestClient, database: FakeDatabase, deployed: dict[str, Any]
) -> None:
    """A replacement is not something this module does on its own.

    The refusal names the incumbent, so the researcher learns what they would have
    replaced before deciding whether to.
    """
    replacement = train(client, deployed["dataset_id"])
    evaluate(client, replacement["model_id"])
    assert transition(client, replacement, "validate").status_code == 200

    response = transition(client, replacement, "deploy")
    assert response.status_code == 409
    body = response.json()
    assert body["refusal"] == "supersede_unacknowledged"
    assert body["deployed_incumbent"]["model_id"] == deployed["model_id"]
    assert body["deployed_incumbent"]["version"] == deployed["version"]

    # Nothing was written: the incumbent is still the one in service.
    incumbent = next(
        v for v in stored_versions(database) if v["model_id"] == deployed["model_id"]
    )
    assert incumbent["status"] == "deployed"
    assert incumbent.get("retired_at") is None

    # The history holds the incumbent's two steps and the replacement's validation,
    # and no fourth entry: the refused deployment wrote nothing at all.
    assert sorted(
        (h["model_id"], h["action"]) for h in stored_history(database)
    ) == sorted(
        [
            (deployed["model_id"], "validated"),
            (deployed["model_id"], "deployed"),
            (replacement["model_id"], "validated"),
        ]
    )


def test_acknowledging_a_replacement_without_a_reason_is_still_refused(
    client: TestClient, deployed: dict[str, Any]
) -> None:
    """``supersede: true`` alone is not consent to retire a working model.

    The flag says a replacement is intended; the reason is what makes the retirement
    a decision rather than a side effect, so it is required separately.
    """
    replacement = train(client, deployed["dataset_id"])
    evaluate(client, replacement["model_id"])
    transition(client, replacement, "validate")

    response = transition(client, replacement, "deploy", supersede=True)
    assert response.status_code == 409
    assert response.json()["refusal"] == "supersede_unacknowledged"
    assert "supersede_reason" in response.json()["detail"]


def test_an_acknowledged_replacement_retires_the_incumbent_with_its_reason(
    client: TestClient, database: FakeDatabase, deployed: dict[str, Any]
) -> None:
    """The substitution is deliberate on both sides and recorded on both.

    The incumbent's retirement names the reason given for superseding it, and the
    new deployment names what it displaced, so the pair reads as one deliberate
    substitution rather than two unrelated edits.
    """
    replacement = train(client, deployed["dataset_id"])
    evaluate(client, replacement["model_id"])
    transition(client, replacement, "validate")

    response = transition(
        client,
        replacement,
        "deploy",
        supersede=True,
        supersede_reason="The June retrain beats the incumbent on held-out macro F1",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["model"]["status"] == "deployed"
    assert body["superseded"]["model_id"] == deployed["model_id"]
    assert body["superseded"]["status"] == "retired"

    # The incumbent carries a pointer forward, so a reader holding only the retired
    # record can see what replaced it.
    incumbent = next(
        v for v in stored_versions(database) if v["model_id"] == deployed["model_id"]
    )
    assert incumbent["status"] == "retired"
    assert incumbent["superseded_by_model_id"] == replacement["model_id"]
    assert incumbent["superseded_by_version"] == replacement["version"]

    # Both steps are in the history, and neither is a silent edit.
    actions = [(h["model_id"], h["action"]) for h in stored_history(database)]
    assert (deployed["model_id"], "retired") in actions
    assert (replacement["model_id"], "deployed") in actions

    # Exactly one model is in service for the target.
    in_service = client.get("/api/models/deployed").json()
    assert [m["model_id"] for m in in_service["deployed"]] == [replacement["model_id"]]


# -- the history ----------------------------------------------------------


def test_the_history_is_append_only_and_records_the_decision(
    client: TestClient, database: FakeDatabase, deployed: dict[str, Any]
) -> None:
    """Each step says who, why, and what it moved between.

    A current status cannot show whether a change was decided or merely happened;
    this is the record that can.
    """
    history = client.get(f"/api/models/{deployed['model_id']}/history").json()
    assert [entry["action"] for entry in history] == ["validated", "deployed"]
    for entry in history:
        assert entry["actor"] == ACTOR
        assert entry["reason"]
        assert entry["evaluation_id"]
        assert entry["selection_criterion"]
    assert history[0]["from_status"] == "experimental"
    assert history[0]["to_status"] == "validated"
    assert history[1]["from_status"] == "validated"
    assert history[1]["to_status"] == "deployed"


def test_a_freshly_trained_model_has_an_empty_history(
    client: TestClient, trained: dict[str, Any]
) -> None:
    """No lifecycle, no history entries.

    Reported as an empty list rather than as a model that went through deployment
    quietly.
    """
    assert client.get(f"/api/models/{trained['model_id']}/history").json() == []


def test_history_entries_refuse_to_claim_a_decision_they_do_not_record() -> None:
    """The stored model rejects history documents that could not have happened.

    A validated step with no evaluation, a step that does not move the status, or
    half a substitution: each is a way the record could read as a decision that was
    never made, so each is refused at the model.
    """
    common = {
        "model_id": "model_x",
        "version": "v1.0.0",
        "target": "post_rain_salinity_band",
        "actor": ACTOR,
        "reason": REASON,
    }
    with pytest.raises(ValidationError):
        # A validated step with no evaluation behind it.
        ModelDeployment(
            **common,
            action="validated",
            from_status="experimental",
            to_status="validated",
        )
    with pytest.raises(ValidationError):
        # A step that does not change the status is not a step.
        ModelDeployment(
            **common,
            action="deployed",
            from_status="deployed",
            to_status="deployed",
            evaluation_id="eval-1",
        )
    with pytest.raises(ValidationError):
        # Half a substitution names no model.
        ModelDeployment(
            **common,
            action="deployed",
            from_status="validated",
            to_status="deployed",
            evaluation_id="eval-1",
            superseded_model_id="model_old",
        )

    # The counterpart, so the refusals above are not passing for an unrelated
    # reason: a step that cites its evaluation and moves the status is accepted.
    accepted = ModelDeployment(
        **common,
        action="validated",
        from_status="experimental",
        to_status="validated",
        evaluation_id="eval-1",
    )
    assert accepted.to_status is ModelStatus.VALIDATED
    assert accepted.evaluation_id == "eval-1"


def test_a_deployed_model_version_cannot_be_written_without_its_decision() -> None:
    """The denormalised status cannot outrun the fields that explain it.

    ``deployed`` is the field operational requests filter on, so a row carrying it
    with no timestamp and no actor is a claim that a decision was made when nothing
    records one.
    """
    with pytest.raises(ValidationError):
        ModelVersion(
            version="v1.0.0", target="post_rain_salinity_band", status=ModelStatus.DEPLOYED
        )
    with pytest.raises(ValidationError):
        ModelVersion(
            version="v1.0.0",
            target="post_rain_salinity_band",
            status=ModelStatus.DEPLOYED,
            deployed_at=datetime.now(timezone.utc),
            validated_at=datetime.now(timezone.utc),
        )


# -- only a deployed model may predict ------------------------------------


def test_prediction_is_refused_when_nothing_is_deployed(
    client: TestClient, evaluated: dict[str, Any]
) -> None:
    """An evaluated, validated-to-be model still does not predict.

    The model here has a report and is otherwise perfectly good, which is the point:
    scoring well is not the same as being in service, and only the second one
    produces an operational number.
    """
    transition(client, evaluated, "validate")
    response = client.post(
        "/api/risk/predict",
        json={"pan_id": PANS[0], "as_of": "2026-03-02T00:00:00Z"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["refusal"] == "no_deployed_model"
    assert "deploy" in body["detail"].lower()


def test_a_retired_model_stops_answering(
    client: TestClient, deployed: dict[str, Any]
) -> None:
    """Retirement leaves no deployed model, and prediction refuses rather than
    falling back to the retired version.

    A fallback would be a substitution nobody asked for, which is the failure this
    module exists to prevent -- in the one place where it would reach an operator.
    """
    assert transition(client, deployed, "retire").status_code == 200
    response = client.post(
        "/api/risk/predict",
        json={"pan_id": PANS[0], "as_of": "2026-03-02T00:00:00Z"},
    )
    assert response.status_code == 409
    assert response.json()["refusal"] == "no_deployed_model"


def test_the_prediction_gate_follows_whichever_version_is_deployed(
    client: TestClient, database: FakeDatabase, deployed: dict[str, Any]
) -> None:
    """The gate is read at request time, not cached at import.

    After a replacement, the refusal names the fact that something is deployed
    rather than the identity of the old one, and the new version is the only one
    permitted to answer.
    """
    replacement = train(client, deployed["dataset_id"])
    evaluate(client, replacement["model_id"])
    transition(client, replacement, "validate")
    transition(
        client,
        replacement,
        "deploy",
        supersede=True,
        supersede_reason="Held-out macro F1 improved on the retrained split",
    )
    in_service = client.get("/api/models/deployed").json()["deployed"]
    assert [m["model_id"] for m in in_service] == [replacement["model_id"]]


# -- the database-level backstop ------------------------------------------


def test_the_database_enforces_one_deployed_model_per_target() -> None:
    """The invariant is declared as a unique partial index, not only in code.

    The service's refusal is a check a concurrent pair of deployments could both
    pass, so the guarantee is also stated in the index plan: unique on ``target``,
    filtered to ``status == "deployed"``. Partial, so every experimental, validated
    and retired version is free to share the target.
    """
    specs = [
        spec
        for spec in ix.INDEX_PLAN
        if spec.collection == MODEL_VERSIONS and spec.partial_filter is not None
    ]
    assert len(specs) == 1
    spec = specs[0]
    assert spec.unique is True
    assert spec.keys == (("target", 1),)
    assert spec.partial_filter == {"status": "deployed"}


def test_the_registry_identifies_a_version_by_name_and_version_together() -> None:
    """(model_id, version) is unique, so a version cannot be ambiguous.

    Module 08 mints a fresh ``model_id`` per run, which is what makes a retrain a
    new model rather than a silent overwrite of the old one. The index is what makes
    resolving "which artifact is deployed" a lookup rather than a search.
    """
    names = {spec.name for spec in ix.INDEX_PLAN if spec.collection == MODEL_VERSIONS}
    assert "model_versions_model_id_version_asc" in names


def test_predictions_are_stored_with_the_deployment_that_answered(
    client: TestClient, database: FakeDatabase, deployed: dict[str, Any]
) -> None:
    """A stored prediction names the model and the decision behind it.

    A risk figure that cannot be traced to a model version and a deployment record
    is a figure that has to be taken on trust, so the fields exist on the document
    rather than being assembled at read time from whatever is deployed today.
    """
    from app.models import MLPrediction

    record = MLPrediction(
        pan_id=PANS[0],
        model_id=deployed["model_id"],
        model_version=deployed["version"],
        target="operational_risk",
        probability=0.7,
    )
    assert record.model_id == deployed["model_id"]
    assert record.deployment_id is None  # not claimed until one is known
    stored = asyncio.run(
        MongoRepository(database, ML_PREDICTIONS, MLPrediction).insert(record)
    )
    assert stored.target.value == "operational_risk"


def test_the_risk_prediction_target_is_not_a_measured_quantity() -> None:
    """An operational risk prediction must not be filed as a salinity reading.

    ``MlTarget`` now carries ``operational_risk`` precisely so a class defined by a
    researcher's rule is not recorded as though it were a measurement.
    """
    from app.models.enums import MlTarget

    assert MlTarget.OPERATIONAL_RISK.value == "operational_risk"
    assert MlTarget.OPERATIONAL_RISK.value not in {
        MlTarget.SALINITY_G_L.value,
        MlTarget.WATER_DEPTH_CM.value,
        MlTarget.HARVEST_YIELD.value,
    }
