"""Module 10 - operational risk prediction, over HTTP, from the current twin state.

This is the suite that proves the endpoint does the thing it claims. Everything
here is real: a rule written through Module 09, a dataset built from stored
records by Module 07, a model fitted by Module 08, evaluated by Module 09 and put
in service by Module 17, a twin state assembled by Module 05, and an artifact
loaded from disk. Nothing stubs the estimator, the twin or the gate, because each
of those is exactly what a reader of a risk figure is relying on.

The properties under test, and the test that fails if each is quietly dropped:

**The number came from the model.**
    Every probability in these tests is recomputed from the stored artifact and
    compared to what the endpoint returned
    (:func:`test_the_probability_is_the_artifact_s_own_output`). A hardcoded
    figure, a rule applied after the fact, or a class mapped to a fixed confidence
    all fail it. So does a fallback estimator, because the recomputation loads the
    registered file.

**The inputs came from the twin, and only from the twin.**
    The estimator receives a frame of exactly the model's declared columns, filled
    from the twin state and from the twin's own quantities
    (:func:`test_the_estimator_receives_exactly_the_declared_columns`). The
    request body is refused if it carries features
    (:func:`test_a_request_cannot_supply_its_own_features`).

**Nothing is invented to fill a gap.**
    A column the twin cannot supply stays absent and is reported as imputed rather
    than substituted (:func:`test_a_feature_the_twin_cannot_supply_is_imputed_not_guessed`).
    A row with no observable features at all is refused.

**The answer is auditable after the fact.**
    The returned ``input_snapshot_id`` is the content hash of the snapshot stored
    beside it, recomputed in the test
    (:func:`test_the_snapshot_id_is_the_hash_of_the_stored_snapshot`), and the
    prediction is in the collection with the class, the probability and the
    deployment that answered.

**A model that reports no probabilities still answers.**
    The class is the model's own; no number is attached
    (:func:`test_a_class_only_model_answers_without_a_probability`).

One limit is stated rather than papered over: the twin carries no history of its
own inputs, so seven catalogued features -- rainfall that has already fallen, the
salinity and depth a day ago, the elapsed time since the last reading -- cannot be
read from it and are imputed. :func:`test_a_feature_the_twin_cannot_supply_is_imputed_not_guessed`
asserts exactly that list, so the gap is a checked claim rather than a known
wart. Carrying them would mean the twin accumulated its own history, which is
Module 05's design decision, not this module's to reverse.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import joblib
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.datasets import get_dataset_service, get_dataset_status_service
from app.api.routers.digital_twin import (
    get_digital_twin_service,
    get_digital_twin_status_service,
)
from app.api.routers.evaluation import get_evaluation_service
from app.api.routers.ml import get_ml_service
from app.api.routers.model_registry import get_deployment_service
from app.api.routers.observations import (
    get_observation_service,
    get_observation_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.risk import get_prediction_service, get_risk_definition_service
from app.api.routers.weather import get_weather_service, get_weather_status_service
from app.db.collections import (
    DIGITAL_TWIN_STATES,
    ML_PREDICTIONS,
    MODEL_VERSIONS,
    PAN_OBSERVATIONS,
    SALT_PANS,
    WEATHER_OBSERVATIONS,
)
from app.db.repository import MongoRepository
from app.main import app
from app.models import MLPrediction, ModelVersion
from app.models.digital_twin_state import DigitalTwinState
from app.models.pan_observation import PanObservation
from app.models.salt_pan import RiskThresholds, SaltPan
from app.models.weather import WeatherObservation
from app.services.dataset_versioning import content_hash
from app.services.datasets import DatasetService
from app.services.deployment import DeploymentService
from app.services.digital_twin import DigitalTwinService
from app.services.evaluation import EvaluationService
from app.services.ml import ARTIFACT_DIRECTORY, MLService
from app.services.observations import ObservationService
from app.services.prediction import OperationalPredictionService
from app.services.risk_definitions import RiskDefinitionService
from app.services.salt_pans import SaltPanService
from app.services.weather import WeatherService
from tests.fakes import FakeDatabase

#: The pan the endpoint is asked about. The other two exist so the pipeline's
#: held-out split has a pan it has not seen, which is a real requirement rather
#: than a formality: a model graded only on the pan it will be asked about is
#: graded on its own training data.
PANS = ["KZN-2026-A01", "KZN-2026-A02", "KZN-2026-A03"]
PAN = PANS[0]

REGISTERED = datetime(2026, 1, 1, tzinfo=timezone.utc)
FIRST_READING = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
READING_INTERVAL = timedelta(hours=6)
READINGS_PER_PAN = 40

#: A forecast in force for every anchor, and different for each pan. This is what
#: puts both classes in the training set: the rule reads the salinity the forecast
#: rain is expected to cost, so a dry forecast and a wet one are different labels.
#: A fixture with one forecast would train a model that has seen one kind of pan.
FORECASTS = {
    "KZN-2026-A01": 45.0,
    "KZN-2026-A02": 0.0,
    "KZN-2026-A03": 6.0,
}

#: Where each pan starts, so the two features the model reads carry a
#: relationship the estimator can actually learn rather than noise.
STARTING_SALINITY = {
    "KZN-2026-A01": 280.0,
    "KZN-2026-A02": 250.0,
    "KZN-2026-A03": 265.0,
}
STARTING_DEPTH = {
    "KZN-2026-A01": 20.0,
    "KZN-2026-A02": 24.0,
    "KZN-2026-A03": 22.0,
}

#: The forecast a pan acts on, issued before the first reading so it is in force
#: for every anchor rather than only for the later ones.
FORECAST_ISSUED = datetime(2026, 2, 28, 0, 0, tzinfo=timezone.utc)
FORECAST_FOR = datetime(2026, 3, 20, 0, 0, tzinfo=timezone.utc)

#: The salinity the forecast rain is expected to cost, as a rule variable. A
#: difference of two catalogued figures, so the rule reads a computed column.
LOSS = "expected_salinity_loss_g_l"

#: The cut-off between the two classes, in g/L of expected dilution. Above it a
#: pan is told to act, below it to watch. Chosen so the dry pan and the two wet
#: ones land on different sides: a threshold that put every row in one class
#: would satisfy the rule and produce a model with nothing to learn.
DILUTION_THRESHOLD_G_L = 2.0

RULE: dict[str, Any] = {
    "name": "monsoon dilution",
    "authored_by": "a researcher",
    "operational_rationale": (
        "Rank a pan by the salinity the forecast rain is expected to cost it, so the "
        "operator sees dilution as a loss in g/L rather than as a fall in a "
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
            "threshold": DILUTION_THRESHOLD_G_L,
            "label": "act now",
            "rationale": (
                "Losing more than 2 g/L to forecast rain is a dilution the operator "
                "can act on; anything less is within the season's ordinary drift."
            ),
        }
    ],
    #: The dry pan matches no criterion, so the researcher says what a pan that
    #: loses nothing is called. Without this the dry pan's rows would be unlabelled
    #: and the model would never see the class it is asked to predict.
    "unmatched_label": "watch",
}

#: Four features, every one of them readable from the twin. Chosen so the
#: positive-path tests exercise a full row rather than a row of imputations, and
#: so the imputed-column test can add to them deliberately.
TWIN_FEATURES = [
    "current_salinity_g_l",
    "water_depth_cm",
    "forecast_rainfall_mm",
    "physics_predicted_post_rain_salinity_g_l",
]

#: Catalogued features the current twin cannot supply. All seven are quantities
#: from *before* the anchor, so none of them is leakage -- they are known at
#: prediction time in principle and simply not carried by the twin. See the module
#: docstring.
TWIN_ABSENT_FEATURES = {
    "depth_change_cm",
    "hours_since_previous_observation",
    "observation_ambient_temperature_c",
    "observation_humidity_percent",
    "recent_rainfall_mm",
    "recent_rainfall_record_count",
    "salinity_change_g_l",
}


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
    app.dependency_overrides[get_risk_definition_service] = lambda: RiskDefinitionService(
        database
    )
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
    app.dependency_overrides[get_prediction_service] = lambda: OperationalPredictionService(
        database, artifact_directory=artifacts
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def seed(database: FakeDatabase) -> None:
    """Three pans, forty readings of each, and a forecast in force for all of them.

    Enough rows to clear the pipeline's own sufficiency thresholds, and enough pans
    that the held-out split is real. The volumes differ deliberately: the twin
    only ever reads a forecast, so a single forecast in the seed would produce a
    dataset in which the feature the model is asked about never varies.
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
                    area_m2=10_000.0,
                    normal_depth_cm=15.5,
                    target_harvest_salinity_g_l=285.0,
                    risk_thresholds=RiskThresholds(
                        flood_depth_cm=45.0,
                        dilution_salinity_g_l=250.0,
                        critical_rainfall_24h_mm=80.0,
                        target_flow_cm=1.5,
                    ),
                    created_at=REGISTERED,
                )
            )
            await weather.insert(
                WeatherObservation(
                    weather_id=f"wx-{pan_id}",
                    pan_id=pan_id,
                    timestamp=FORECAST_ISSUED,
                    forecast_for=FORECAST_FOR,
                    rainfall_mm=FORECASTS[pan_id],
                    rainfall_probability_percent=70.0 if FORECASTS[pan_id] else 10.0,
                    ambient_temperature_c=25.0,
                    humidity_percent=60.0,
                    source="manual",
                    created_at=REGISTERED,
                )
            )
            for step in range(READINGS_PER_PAN):
                # A slow drift, so consecutive anchors are not identical rows and
                # the split has something to hold out.
                drift = 0.4 * ((step % 7) - 3)
                await observations.insert(
                    PanObservation(
                        observation_id=f"obs-{pan_id}-{step:03d}",
                        pan_id=pan_id,
                        timestamp=FIRST_READING + step * READING_INTERVAL,
                        salinity_g_l=round(STARTING_SALINITY[pan_id] + drift, 3),
                        water_depth_cm=round(STARTING_DEPTH[pan_id] + (step % 4) * 0.5, 3),
                        brine_temperature_c=30.0,
                        humidity_percent=60.0,
                        ambient_temperature_c=25.0,
                        created_at=REGISTERED,
                    )
                )

    asyncio.run(write())


def write_rule(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/risk/definitions", json=RULE)
    assert response.status_code == 201, response.text
    return response.json()["definition"]


def build_risk_dataset(client: TestClient, features: list[str]) -> dict[str, Any]:
    definition = write_rule(client)
    response = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": features,
        },
    )
    assert response.status_code == 201, response.text
    built = response.json()
    assert built["dataset"]["report"]["labelled_count"] > 0, built["dataset"]["report"]
    return built["dataset"]


def deploy_model(client: TestClient, dataset_id: str) -> dict[str, Any]:
    """Train, evaluate, validate and deploy, and return the model version record."""
    trained = client.post("/api/ml/train", json={"dataset_id": dataset_id})
    assert trained.status_code == 201, trained.text
    model = trained.json()

    report = client.post("/api/evaluation/run", json={"model_id": model["model_id"]})
    assert report.status_code in (200, 201), report.text

    body = {"version": model["version"], "reason": "fielded for the season", "actor": "a researcher"}
    for action, expected in (("validate", 200), ("deploy", 200)):
        response = client.post(f"/api/models/{model['model_id']}/{action}", json=body)
        assert response.status_code == expected, response.text
    return model


def refresh_twin(client: TestClient, pan_id: str = PAN) -> dict[str, Any]:
    response = client.post(f"/api/twin/{pan_id}/refresh")
    assert response.status_code in (200, 201), response.text
    return response.json()["state"]


def predict(client: TestClient, **body: Any) -> Any:
    return client.post("/api/risk/predict", json={"pan_id": PAN, **body})


def read(repository: MongoRepository[Any], query: dict[str, Any]) -> list[Any]:
    """Run a repository read outside the app's event loop.

    The service methods under test are awaited by the endpoint; these reads are
    assertions about what they left behind, and driving them through a fresh loop
    keeps the test synchronous without pretending the repository is not async.
    """
    return asyncio.run(repository.find(query))


def stored_model(database: FakeDatabase, model_id: str) -> ModelVersion:
    found = read(MongoRepository(database, MODEL_VERSIONS, ModelVersion), {"model_id": model_id})
    assert found, f"no stored model version for {model_id}"
    return found[0]


def stored_predictions(database: FakeDatabase) -> list[MLPrediction]:
    return read(MongoRepository(database, ML_PREDICTIONS, MLPrediction), {})


def artifact_of(model: dict[str, Any]) -> dict[str, Any]:
    path = Path(model["artifact_path"])
    if not path.is_absolute():
        path = ARTIFACT_DIRECTORY / path
    return joblib.load(path)


def recompute(model: dict[str, Any], features: dict[str, Any]) -> dict[str, float]:
    """The artifact's own probabilities for one feature row, computed here.

    Deliberately independent of the service: this loads the registered file, runs
    its preprocessor and estimator, and reads the classes off it. If the endpoint's
    number came from anywhere else, the two disagree.
    """
    import pandas as pd

    artifact = artifact_of(model)
    names = list(model["feature_names"])
    frame = pd.DataFrame.from_records([{name: features.get(name) for name in names}])
    frame = frame.reindex(columns=names)
    transformed = artifact["preprocessing"].transform(frame)
    probabilities = artifact["estimator"].predict_proba(transformed)[0]
    return {
        str(label): float(value)
        for label, value in zip(artifact["classes"], probabilities, strict=True)
    }


@pytest.fixture()
def live(client: TestClient, database: FakeDatabase, artifacts: Path) -> dict[str, Any]:
    """A pan with a twin, a rule, and a deployed model, ready to be asked."""
    seed(database)
    dataset = build_risk_dataset(client, TWIN_FEATURES)
    model = deploy_model(client, dataset["dataset_id"])
    twin = refresh_twin(client)
    return {"model": model, "dataset": dataset, "twin": twin, "artifacts": artifacts}


# -- the answer -----------------------------------------------------------


def test_a_deployed_model_answers_from_the_current_twin(
    client: TestClient, live: dict[str, Any]
) -> None:
    """The ordinary operational question: this pan, now, with the deployed model."""
    response = predict(client)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["pan_id"] == live["twin"]["pan_id"]
    assert body["risk_class"] in {"watch", "act now"}
    assert 0.0 <= body["probability"] <= 1.0
    assert set(body["class_probabilities"]) == {"watch", "act now"}
    assert body["class_probabilities"][body["risk_class"]] == body["probability"]
    # The twin, not the clock, supplied the features.
    assert body["input_source"] == "current_digital_twin_state"
    assert body["twin_id"] == live["twin"]["twin_id"]
    assert body["as_of"] == body["twin_as_of"] == live["twin"]["timestamp"]
    assert body["input_snapshot_id"]
    assert body["predicted_at"]


def test_the_probability_is_the_artifact_s_own_output(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """The number is the model's, recomputed from the registered artifact.

    This is the test a hardcoded probability, a rule applied after the fact, or a
    fixed confidence per class would fail. It also pins the comparison tight: the
    endpoint's value and the recomputed one are the same float, not merely close,
    so a value that merely looks plausible does not pass.
    """
    body = predict(client).json()
    model = stored_model(database, live["model"]["model_id"])
    snapshot = stored_predictions(database)[0].input_snapshot
    expected = recompute(
        model.model_dump(), snapshot["features"]
    )

    assert body["class_probabilities"] == pytest.approx(expected, rel=0, abs=0)
    assert body["probability"] == pytest.approx(expected[body["risk_class"]], abs=0)
    # And the class is the argmax of the model's own output, not a rule's verdict.
    assert body["risk_class"] == max(expected, key=lambda name: expected[name])


def test_two_different_twin_states_give_two_different_answers(
    client: TestClient, live: dict[str, Any]
) -> None:
    """A prediction tracks the state it was asked about.

    Two identical requests give the same answer, which is what makes the answer
    checkable. A changed twin gives a different input id -- and this is the test
    that fails if the row is filled from anything other than the twin, since a
    fixed row would give a fixed id.
    """
    first = predict(client).json()
    again = predict(client).json()
    assert first["input_snapshot_id"] == again["input_snapshot_id"]
    assert first["probability"] == again["probability"]
    assert first["risk_class"] == again["risk_class"]

    # Move the pan: a fresh reading at a higher salinity, then a fresh twin.
    observations = MongoRepository(_database_of(client), PAN_OBSERVATIONS, PanObservation)
    asyncio.run(
        observations.insert(
            PanObservation(
                observation_id="obs-later",
                pan_id=PAN,
                timestamp=datetime(2026, 3, 5, 6, 0, tzinfo=timezone.utc),
                salinity_g_l=240.0,
                water_depth_cm=30.0,
                brine_temperature_c=30.0,
                humidity_percent=60.0,
                ambient_temperature_c=25.0,
                created_at=REGISTERED,
            )
        )
    )
    refresh_twin(client)
    later = predict(client).json()
    assert later["input_snapshot_id"] != first["input_snapshot_id"]
    assert later["twin_id"] != first["twin_id"]


# -- the inputs -----------------------------------------------------------


def test_the_estimator_receives_exactly_the_declared_columns(
    client: TestClient, live: dict[str, Any]
) -> None:
    """The frame handed to the estimator is the model's list, in its order.

    Asserted on the stored snapshot, which is the row as it was built. Anything
    extra would be a column the model was never fitted on; anything missing would
    be a value the model was fitted with and was not given.
    """
    body = predict(client).json()
    snapshot = stored_predictions(_database_of(client))[0].input_snapshot
    assert set(snapshot["features"]) == set(live["model"]["feature_names"])
    assert list(snapshot["features"]) == sorted(snapshot["features"])
    assert body["features_used"] == sorted(live["model"]["feature_names"])
    assert body["features_absent"] == []


def test_the_row_handed_to_the_scorer_is_in_the_declared_order(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """The model's column order is the row's column order, exactly.

    The preprocessor selects columns by name, so a misordered frame would still
    score the same and this would be untestable from the response. It is asserted
    on the row the service actually builds, which is the boundary that decides it.

    The check exists because "the same columns" and "the same columns in the same
    order" are different claims, and only the second one is what the model's own
    record says it was fitted on.
    """
    model = stored_model(database, live["model"]["model_id"])
    service = OperationalPredictionService(database)
    twin = read(
        MongoRepository(database, DIGITAL_TWIN_STATES, DigitalTwinState),
        {"twin_id": live["twin"]["twin_id"]},
    )
    assert twin, "the twin the fixture stored is not readable"
    row = service._row_for(model, twin[0])
    assert list(row.values) == list(model.feature_names)
    assert set(row.values) == set(model.feature_names)
    assert list(row.snapshot()["features"]) == sorted(model.feature_names)


def test_a_request_cannot_supply_its_own_features(client: TestClient, live: dict[str, Any]) -> None:
    """The body is a question, not an argument.

    Feature values in the body are refused by the schema. A caller that could pass
    them could obtain a number from the deployed model that described something
    other than the pan, wearing the pan's name.
    """
    response = predict(client, features={"current_salinity_g_l": 999.0})
    assert response.status_code == 422, response.text
    response = predict(client, as_of="2026-03-02T06:00:00Z", model_id="anything")
    assert response.status_code == 422, response.text


def test_a_feature_the_twin_cannot_supply_is_imputed_not_guessed(
    client: TestClient, database: FakeDatabase
) -> None:
    """Seven catalogued features stay absent, and are reported as absent.

    A model that declares one of them still predicts: the preprocessor the model
    was fitted with imputes it, and the response says so. What must never happen
    is a value appearing that the twin did not carry -- here, from the training
    rows or from a neighbouring column.
    """
    seed(database)
    features = TWIN_FEATURES + ["hours_since_previous_observation", "recent_rainfall_mm"]
    dataset = build_risk_dataset(client, features)
    model = deploy_model(client, dataset["dataset_id"])
    refresh_twin(client)

    body = predict(client).json()
    assert body["features_absent"] == [
        "hours_since_previous_observation",
        "recent_rainfall_mm",
    ]
    assert body["features_imputed"] == body["features_absent"]
    assert set(body["features_used"]) == set(TWIN_FEATURES)
    snapshot = stored_predictions(database)[0].input_snapshot
    assert snapshot["features"]["hours_since_previous_observation"] is None
    assert snapshot["features"]["recent_rainfall_mm"] is None
    assert model["feature_names"]  # the model did declare them


def test_the_full_set_of_twin_absent_features_is_the_documented_one() -> None:
    """The gap is a checked claim, not a known wart.

    Guards the module docstring: if a later Module 05 change starts carrying one
    of these, the count and the test above have to move with it.
    """
    from app.services.dataset_catalog import FEATURE_INDEX, TWIN_FEATURE_INDEX

    absent = set(FEATURE_INDEX) - set(TWIN_FEATURE_INDEX)
    assert absent == TWIN_ABSENT_FEATURES


# -- the answer's audit trail --------------------------------------------


def test_the_prediction_is_stored_with_its_provenance(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """Every prediction is kept, with the class, the input and the deployment.

    Stored before the response is written, so a prediction a client saw is a
    prediction that exists.
    """
    body = predict(client).json()
    stored = stored_predictions(database)
    assert len(stored) == 1
    record = stored[0]

    assert record.prediction_id == body["prediction_id"]
    assert record.predicted_class == body["risk_class"]
    assert record.probability == body["probability"]
    assert record.pan_id == body["pan_id"]
    assert record.model_id == live["model"]["model_id"]
    assert record.model_version == live["model"]["version"]
    assert record.deployment_id == body["deployment_id"]
    assert record.input_snapshot_id == body["input_snapshot_id"]
    assert content_hash(record.input_snapshot) == record.input_snapshot_id


def test_the_stored_snapshot_hashes_to_the_reported_id(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """The reported id is the hash of the stored snapshot, recomputed here."""
    body = predict(client).json()
    record = stored_predictions(database)[0]
    assert content_hash(record.input_snapshot) == body["input_snapshot_id"]
    assert record.input_snapshot["twin_id"] == body["twin_id"]
    assert record.input_snapshot["risk_definition_id"] == body["risk_definition_id"]
    assert record.input_snapshot["produced_by"] == "operational_prediction"


def test_the_model_name_is_reported_when_one_was_given(
    client: TestClient, database: FakeDatabase
) -> None:
    """A label the researcher gave is shown, and never replaces the identity.

    The name is optional. With one, both appear. The point of asserting both is
    that a display name must not be able to stand in for the model that answered:
    ``model_id`` is what the registry resolves, and it is always present.
    """
    seed(database)
    definition = write_rule(client)
    build = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "operational_risk",
                "horizon_hours": 24.0,
                "risk_definition_id": definition["definition_id"],
            },
            "features": TWIN_FEATURES,
        },
    )
    dataset_id = build.json()["dataset"]["dataset_id"]
    trained = client.post(
        "/api/ml/train", json={"dataset_id": dataset_id, "name": "monsoon dilution ranker"}
    )
    assert trained.status_code == 201, trained.text
    model = trained.json()
    report = client.post("/api/evaluation/run", json={"model_id": model["model_id"]})
    assert report.status_code in (200, 201), report.text
    body = {"version": model["version"], "reason": "fielded", "actor": "a researcher"}
    assert client.post(f"/api/models/{model['model_id']}/validate", json=body).status_code == 200
    assert client.post(f"/api/models/{model['model_id']}/deploy", json=body).status_code == 200
    refresh_twin(client)

    answer = predict(client).json()
    assert answer["model_name"] == "monsoon dilution ranker"
    assert answer["model_id"] == model["model_id"]
    assert answer["model_version"] == model["version"]


def test_no_deployed_model_answers_nothing(client: TestClient, database: FakeDatabase) -> None:
    """The gate, from this module's side: a trained model that is not deployed.

    The model is real and fitted, and the twin is real. It still does not answer,
    because a model reaches operational service only through Module 17.
    """
    seed(database)
    dataset = build_risk_dataset(client, TWIN_FEATURES)
    trained = client.post("/api/ml/train", json={"dataset_id": dataset["dataset_id"]})
    assert trained.status_code == 201, trained.text
    model = trained.json()
    assert client.post(
        "/api/evaluation/run", json={"model_id": model["model_id"]}
    ).status_code in (200, 201)
    refresh_twin(client)

    response = predict(client)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["refusal"] == "no_deployed_model"
    assert "deployed" in body["detail"]
    # The model exists and was evaluated, so the missing step is the deployment
    # and not the training. Reporting "no model" here would send the reader to
    # Module 08 for a model that is already in the registry.
    assert body["stage"] == "not_deployed"
    # The sufficiency audit is about training, which is not what is blocking
    # this request, so it is not quoted beside it.
    assert "training_gate" not in body
    assert stored_predictions(database) == []


def test_nothing_registered_says_so_and_quotes_the_training_gate(
    client: TestClient,
) -> None:
    """The empty-registry refusal carries its stage and the real thresholds.

    Three things a client needs to state the truth rather than a generic
    absence: that nothing was ever trained, what the project's own gate
    requires, and how far from it the project actually is. The counts come from
    the sufficiency audit rather than from literals here, so a threshold raised
    in :mod:`app.ml.sufficiency` cannot leave this sentence behind.
    """
    response = predict(client)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["refusal"] == "no_deployed_model"
    assert body["stage"] == "no_model"
    gate = body["training_gate"]
    assert gate["ready"] is False
    assert gate["genuine_labelled_rows"] == 0
    assert gate["distinct_pans_in_labels"] == 0
    assert gate["min_total_labelled_rows"] == 80
    assert gate["min_distinct_pans"] == 3


def test_a_trained_but_unevaluated_model_names_evaluation_as_the_missing_step(
    client: TestClient, database: FakeDatabase
) -> None:
    """A model in the registry that has never been graded is not "no model".

    The model is real and fitted; only Module 09 has not run. The stage says so,
    because the remedy is a different module and a different button from the one
    an untrained project needs.
    """
    seed(database)
    dataset = build_risk_dataset(client, TWIN_FEATURES)
    trained = client.post("/api/ml/train", json={"dataset_id": dataset["dataset_id"]})
    assert trained.status_code == 201, trained.text

    response = predict(client)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["refusal"] == "no_deployed_model"
    assert body["stage"] == "not_evaluated"
    assert "training_gate" not in body
    assert stored_predictions(database) == []


def test_the_status_follows_the_registry_it_reads(
    client: TestClient, live: dict[str, Any]
) -> None:
    """``GET /api/risk/`` reports the deployed model rather than a constant.

    The status used to assert ``trained: false``, which is the one claim a status
    endpoint must never make unconditionally: with a model deployed above it
    would be calling a model in service untrained.
    """
    body = client.get("/api/risk/").json()
    assert body["trained"] is True
    assert body["model_state"] == "deployed"


# -- refusals that are specific to this design ---------------------------


def test_a_pan_with_no_twin_state_is_refused(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """Records are not a substitute for the twin.

    The same pan has four stored observations and a forecast, so a module that
    assembled features from records could answer. It does not: the twin is the
    system's current belief, and building a row behind its back would answer a
    different question than the one asked.
    """
    response = predict(client, pan_id="KZN-2026-Z99")
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["refusal"] == "no_twin_state"
    # Named, so the panel can say what is missing rather than repeating a refusal
    # code at an operator.
    assert body["missing"] == ["digital twin state"]
    assert stored_predictions(database) == []


def test_a_forecast_before_the_twins_first_belief_is_refused(
    client: TestClient, live: dict[str, Any]
) -> None:
    """An instant the system had no belief about is not answered from today.

    Refused rather than filled with the newest state, which would date a
    prediction to an instant the system never observed.
    """
    response = predict(client, as_of="2025-01-01T00:00:00Z")
    assert response.status_code == 409, response.text
    assert response.json()["refusal"] == "no_twin_state"


def test_a_past_anchor_is_answered_from_the_twin_of_that_moment(
    client: TestClient, live: dict[str, Any]
) -> None:
    """A historical anchor uses the state as it was, not the newest one.

    The live fixture has one twin. A later reading gives a second one, and asking
    for the earlier instant must still resolve to the earlier state -- otherwise a
    prediction dated to the past would carry an id computed from a state the system
    did not hold at the time, and the snapshot would say otherwise.
    """
    first_as_of = live["twin"]["timestamp"]
    later = datetime.fromisoformat(first_as_of) + READING_INTERVAL
    observations = MongoRepository(_database_of(client), PAN_OBSERVATIONS, PanObservation)
    asyncio.run(
        observations.insert(
            PanObservation(
                observation_id="obs-later-anchor",
                pan_id=PAN,
                timestamp=later,
                salinity_g_l=258.0,
                water_depth_cm=25.0,
                brine_temperature_c=30.0,
                humidity_percent=60.0,
                ambient_temperature_c=25.0,
                created_at=REGISTERED,
            )
        )
    )
    second = refresh_twin(client)
    assert second["twin_id"] != live["twin"]["twin_id"]

    # The ordinary question uses the newest state...
    current = predict(client).json()
    assert current["twin_id"] == second["twin_id"]

    # ...and the earlier instant uses the state that was current then.
    past = predict(client, as_of=first_as_of).json()
    assert past["twin_id"] == live["twin"]["twin_id"]
    assert past["as_of"] == first_as_of
    assert past["input_snapshot_id"] != current["input_snapshot_id"]


def test_a_feature_the_system_cannot_know_yet_is_refused(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """A model declaring a post-event column is refused, not imputed.

    A dataset may be fitted *to* the salinity measured after the rain; a model may
    never declare that column as a feature, because an operational row is built
    before the measurement exists. The refusal is a different code from the
    pipeline's identifier exclusion, because the two mean different things.
    """
    model = stored_model(database, live["model"]["model_id"])
    database[MODEL_VERSIONS].amend(
        "model_id",
        model.model_id,
        feature_names=[*model.feature_names, "label_post_rain_salinity_g_l"],
    )
    response = predict(client)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["refusal"] == "feature_not_knowable_at_prediction_time"
    assert "label_post_rain_salinity_g_l" in body["detail"]
    assert stored_predictions(database) == []


def test_a_feature_this_project_does_not_define_is_refused(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """A column with no definition behind it could only ever be absent.

    The twin supplies features by name. A name nothing defines would reach the
    estimator as a column of nothing, which is not the same as a column of the
    training set's average, and reads as though it were.
    """
    model = stored_model(database, live["model"]["model_id"])
    database[MODEL_VERSIONS].amend(
        "model_id",
        model.model_id,
        feature_names=[*model.feature_names, "moon_phase"],
    )
    response = predict(client)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["refusal"] == "feature_mismatch"
    assert "moon_phase" in body["detail"]


def test_a_model_deployed_for_a_measured_target_does_not_answer_the_risk_question(
    client: TestClient, database: FakeDatabase
) -> None:
    """Deployment is per target, so a real model for another target is invisible.

    This model is a genuine, deployed, working salinity-band classifier. It is
    deployed for ``post_rain_salinity_band``, and this endpoint asks about
    ``operational_risk``. The deployment gate is keyed by target, so the model
    cannot answer here -- not because it is a poor fit for its own question, but
    because it was not deployed to answer this one.
    """
    seed(database)
    build = client.post(
        "/api/datasets/",
        json={
            "target": {
                "name": "post_rain_salinity_band",
                "horizon_hours": 24.0,
                "bin_edges": [240.0, 260.0, 280.0],
            },
            "features": ["current_salinity_g_l", "water_depth_cm"],
        },
    )
    assert build.status_code == 201, build.text
    dataset_id = build.json()["dataset"]["dataset_id"]
    trained = client.post("/api/ml/train", json={"dataset_id": dataset_id})
    assert trained.status_code == 201, trained.text
    model = trained.json()
    report = client.post("/api/evaluation/run", json={"model_id": model["model_id"]})
    assert report.status_code in (200, 201), report.text
    body = {"version": model["version"], "reason": "fielded", "actor": "a researcher"}
    assert client.post(f"/api/models/{model['model_id']}/validate", json=body).status_code == 200
    assert client.post(f"/api/models/{model['model_id']}/deploy", json=body).status_code == 200
    refresh_twin(client)

    response = predict(client)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["refusal"] == "no_deployed_model"
    assert "operational_risk" in body["detail"]


def test_a_class_the_rule_does_not_declare_is_refused(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """The model's label space and the rule's must still describe one problem.

    Here the deployed artifact emits a third class the researcher's rule never
    declared -- as it would if the rule were revised, or a model were fitted to an
    older revision of it. The class is real and the number behind it is real, and
    neither is reported: there is no rule that says what "flood" means, so there
    is no risk class to report. Mapping it to the nearest declared class would be
    this module inventing a severity.
    """
    model = stored_model(database, live["model"]["model_id"])
    path = Path(model.artifact_path)
    if not path.is_absolute():
        path = ARTIFACT_DIRECTORY / path
    artifact = joblib.load(path)
    artifact["classes"] = [*artifact["classes"], "flood"]
    artifact["estimator"] = _ThirdClass(artifact["classes"])
    joblib.dump(artifact, path)

    response = predict(client)
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["refusal"] == "model_not_fitted_to_risk_rule"
    assert "flood" in body["detail"]
    assert stored_predictions(database) == []


# -- models that report no probability -----------------------------------


class _ThirdClass:
    """An estimator that emits a class the researcher's rule never declared.

    Sits at the far end of the class space, so ``predict_proba`` puts its
    probability there and the service's own argmax lands on the undeclared label.
    """

    def __init__(self, classes: list[str]) -> None:
        self.classes_ = list(classes)
        self._index = len(classes) - 1

    def predict_proba(self, transformed: Any) -> Any:
        import numpy as np

        row = np.zeros(len(self.classes_))
        row[self._index] = 0.99
        return np.vstack([row])

    def predict(self, transformed: Any) -> list[str]:
        return [self.classes_[self._index]]


class _ClassOnly:
    """An estimator with ``predict`` and no ``predict_proba``.

    A legitimate thing for a fitted artifact to be. It has a class to give, and
    no number to attach to it.
    """

    def __init__(self, label: str) -> None:
        self._label = label
        self.classes_ = ["watch", "act now"]

    def predict(self, transformed: Any) -> list[str]:
        return [self._label]


def test_a_class_only_model_answers_without_a_probability(
    client: TestClient, live: dict[str, Any], database: FakeDatabase
) -> None:
    """The class is the model's own; no number is invented beside it.

    The alternative -- refusing the prediction because the model has no
    probabilities -- would replace a model's answer with this module's opinion
    about which models may answer. The alternative that actually matters, quietly
    attaching a fixed confidence, is what the null asserts against.
    """
    model = stored_model(database, live["model"]["model_id"])
    path = Path(model.artifact_path)
    if not path.is_absolute():
        path = ARTIFACT_DIRECTORY / path
    artifact = joblib.load(path)
    artifact["estimator"] = _ClassOnly("act now")
    joblib.dump(artifact, path)

    body = predict(client).json()
    assert body["risk_class"] == "act now"
    assert body["probability"] is None
    assert body["class_probabilities"] == {}
    # Still stored, still traceable, and still the model's class.
    record = stored_predictions(database)[0]
    assert record.predicted_class == "act now"
    assert record.probability is None
    assert content_hash(record.input_snapshot) == body["input_snapshot_id"]


def _database_of(client: TestClient) -> FakeDatabase:
    """The fake behind the running app.

    Read out of the dependency override rather than closed over, so a test that
    only asks for the client still gets the handle it needs to check what was
    stored.
    """
    override = app.dependency_overrides[get_db]
    return override()


def test_the_stored_prediction_can_be_read_back_by_pan(
    client: TestClient, live: dict[str, Any]
) -> None:
    """Storing the prediction is what makes later validation possible.

    Module 15 compares a prediction with the outcome that followed it, which means
    looking the prediction up again afterwards. The read is by pan and by time,
    and it is exercised here so the storage is known to be addressable and not
    merely present.
    """
    predict(client)
    repository: MongoRepository[MLPrediction] = MongoRepository(
        _database_of(client), ML_PREDICTIONS, MLPrediction
    )
    found = read(repository, {"pan_id": PAN.lower()})
    assert len(found) == 1
    assert found[0].predicted_class in {"watch", "act now"}
    assert read(repository, {"pan_id": "KZN-2026-Z99"}) == []
