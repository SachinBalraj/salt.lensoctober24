"""Module 8 over HTTP, against real stored datasets.

These tests build datasets through the API, train from them, and check the things
a client depends on: that a refusal says the required sentence and writes nothing,
that a success reports real held-out numbers with their provenance, that Random
Forest is not chosen by default, and that a drifted dataset is refused rather than
trained against.

The fixtures seed observations directly into the fake database rather than posting
each one. A dataset that clears the sufficiency gate needs 80+ labelled rows across
at least three pans, and posting those one at a time would make this file slower
than the module it tests. The measurements are real records either way -- they go
through the same models and the same builder.
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
from app.api.routers.ml import get_ml_service
from app.api.routers.observations import (
    get_observation_service,
    get_observation_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.weather import get_weather_service, get_weather_status_service
from app.db.collections import ACTUAL_OUTCOMES, PAN_OBSERVATIONS, SALT_PANS
from app.db.repository import MongoRepository
from app.main import app
from app.ml.sufficiency import INSUFFICIENT_DATA_MESSAGE
from app.models.actual_outcome import ActualOutcome
from app.models.pan_observation import PanObservation
from app.models.salt_pan import RiskThresholds, SaltPan
from app.services.datasets import DatasetService
from app.services.ml import MLService
from app.services.observations import ObservationService
from app.services.salt_pans import SaltPanService
from app.services.weather import WeatherService
from tests.fakes import FakeDatabase

#: Bands chosen by the caller, not by the module. Two edges give three classes,
#: which is the minimum for a classification target to be worth fitting.
BIN_EDGES = [262.0, 272.0]

#: Hourly readings. The horizon is one hour, so every reading but the last in a
#: pan's series finds a later reading to be its label.
PANS = ["KZN-2026-A01", "KZN-2026-B02", "KZN-2026-C03", "KZN-2026-D04"]
READINGS_PER_PAN = 30
START = datetime(2026, 3, 1, tzinfo=timezone.utc)

#: How much each pan's salinity drifts over the series. The pans end up in
#: different bands, so the class balance across the whole dataset is real rather
#: than uniform, and a model has something to learn from the readings.
PAN_DRIFT = {"KZN-2026-A01": -1.2, "KZN-2026-B02": 0.0, "KZN-2026-C03": 1.4, "KZN-2026-D04": 0.6}


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def artifacts(tmp_path: Path) -> Path:
    """Where fitted models are written for the duration of one test.

    A real directory, because the artifact's existence and its loadability are
    part of what is being tested, and a test that mocked the write would prove
    neither.
    """
    return tmp_path / "models"


@pytest.fixture()
def client(database: FakeDatabase, artifacts: Path) -> Any:
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_observation_service] = (
        lambda: ObservationService(database)
    )
    app.dependency_overrides[get_observation_status_service] = (
        lambda: ObservationService(database)
    )
    app.dependency_overrides[get_weather_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_weather_status_service] = (
        lambda: WeatherService(database)
    )
    app.dependency_overrides[get_dataset_service] = lambda: DatasetService(database)
    app.dependency_overrides[get_dataset_status_service] = (
        lambda: DatasetService(database)
    )
    app.dependency_overrides[get_ml_service] = lambda: MLService(
        database, artifact_directory=artifacts
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def seed(database: FakeDatabase, **kwargs: Any) -> None:
    """Seed real records, from a synchronous test.

    The repository writes are async, and this suite has no async plugin, so the
    work runs on a throwaway loop. The fake collections are plain lists with no
    loop-bound state, so nothing carries over between calls.
    """
    asyncio.run(_seed(database, **kwargs))


async def _seed(
    database: FakeDatabase, *, pans: int = 4, readings: int = READINGS_PER_PAN
) -> None:
    """Store real pans and observations through the repositories.

    Written through :class:`MongoRepository` rather than appended to the fake's
    internals, so every document is validated by its Pydantic model and passes
    through ``to_mongo`` exactly as a real write would. A dataset that clears the
    sufficiency gate needs 80+ labelled rows across three pans, and posting each
    one over HTTP would make this file slower than the module it tests.

    The pans are registered before the readings, so the builder can use the static
    columns derived from a pan rather than reporting them unavailable.
    """
    pan_repository: MongoRepository[SaltPan] = MongoRepository(
        database, SALT_PANS, SaltPan
    )
    observation_repository: MongoRepository[PanObservation] = MongoRepository(
        database, PAN_OBSERVATIONS, PanObservation
    )
    registered = datetime(2026, 2, 1, tzinfo=timezone.utc)

    for index, pan_id in enumerate(PANS[:pans]):
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
        for step in range(readings):
            # A sawtooth within each pan's range, so readings are not a straight
            # line and a time-ordered split has something to separate. The drift
            # offset puts the pans in different bands, so the class balance across
            # the dataset is real rather than uniform.
            # Spans both band edges, so all three classes are genuinely present
            # and the class balance comes from the readings rather than being
            # uniform by construction.
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


def build_dataset(
    client: TestClient, *, dataset_id: str | None = None, **target: Any
) -> dict[str, Any]:
    # The band edges are the caller's, supplied here rather than defaulted by the
    # module: choosing where the classes fall would make the class distribution a
    # fact about the module's choice rather than about the readings.
    body: dict[str, Any] = {
        "target": {
            "name": "post_rain_salinity_band",
            "horizon_hours": 1.0,
            "bin_edges": list(BIN_EDGES),
            **target,
        }
    }
    response = client.post("/api/datasets/", json=body)
    assert response.status_code == 201, response.text
    # The manifest is nested under "dataset"; the id is the only part these tests
    # need from the build, so it is lifted out here rather than at every use.
    return {"dataset_id": response.json()["dataset"]["dataset_id"]}


@pytest.fixture()
def small_dataset(database: FakeDatabase, client: TestClient) -> dict[str, Any]:
    """A dataset far too small to train on: two pans, six readings each."""
    seed(database, pans=2, readings=6)
    return build_dataset(client)


@pytest.fixture()
def trainable_dataset(database: FakeDatabase, client: TestClient) -> dict[str, Any]:
    """A dataset that clears the sufficiency gate."""
    seed(database)
    return build_dataset(client)


# -- the module status endpoint -----------------------------------------


def test_the_status_endpoint_states_what_the_module_refuses_to_do(client):
    response = client.get("/api/ml/")

    assert response.status_code == 200
    body = response.json()
    assert body["implemented"] is True
    # No model exists until a caller supplies data that can support one.
    assert body["trained"] is False
    assert set(body["candidates"]) == {
        "logistic_regression",
        "decision_tree",
        "random_forest",
        "gradient_boosting",
    }
    assert body["insufficient_data_message"] == INSUFFICIENT_DATA_MESSAGE
    assert set(body["split_strategies"]) == {
        "chronological",
        "grouped_by_pan",
        "random",
        "per_pan_chronological",
    }
    assert "post_rain_salinity_band" in body["supported_targets"]


# -- the dataset readiness gate -----------------------------------------


def test_readiness_gate_is_shut_when_no_genuine_labels_exist(client):
    """The exact situation on a fresh cluster: 0 genuine labels, gate shut."""
    response = client.get("/api/ml/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "not_ready"
    assert body["message"] == INSUFFICIENT_DATA_MESSAGE
    assert body["genuine_labelled_rows"] == 0
    assert body["distinct_pans_in_labels"] == 0
    assert body["counts"]["outcomes_on_file"] == 0
    assert body["thresholds"]["min_total_labelled_rows"] == 80
    assert body["reasons"], "the gate must explain itself"


def _insert_outcome(
    database: FakeDatabase, *, pan_id: str, demonstration: bool
) -> None:
    asyncio.run(
        MongoRepository(database, ACTUAL_OUTCOMES, ActualOutcome).insert(  # type: ignore[arg-type]
            ActualOutcome(
                pan_id=pan_id,
                event_id=f"ev-{'demo' if demonstration else 'real'}-{pan_id}",
                actual_rainfall_mm=90.0,
                post_event_salinity_g_l=20.0,
                is_demonstration=demonstration,
            )
        )
    )


def test_readiness_counts_only_genuine_outcomes(
    database: FakeDatabase, client
):
    """Demonstration labels must not inflate the genuine row count."""
    _insert_outcome(database, pan_id="north-pan", demonstration=True)
    _insert_outcome(database, pan_id="north-pan", demonstration=False)

    body = client.get("/api/ml/readiness").json()

    assert body["genuine_labelled_rows"] == 1
    assert body["counts"]["outcomes_on_file"] == 2
    assert body["distinct_pans_in_labels"] == 1
    assert body["verdict"] == "not_ready"  # one row is real but far below 80


# -- the refusal --------------------------------------------------------


def test_insufficient_data_returns_422_with_the_exact_sentence(
    small_dataset, client
):
    """The required behaviour, end to end over HTTP."""
    response = client.post(
        "/api/ml/train", json={"dataset_id": small_dataset["dataset_id"]}
    )

    assert response.status_code == 422
    assert response.json()["detail"] == INSUFFICIENT_DATA_MESSAGE
    assert response.json()["refusal"] == "insufficient_data"


def test_the_refusal_names_what_was_wrong_with_the_data(small_dataset, client):
    """A bare refusal sends the reader back with no idea what to change."""
    response = client.post(
        "/api/ml/train", json={"dataset_id": small_dataset["dataset_id"]}
    )

    reasons = response.json()["reasons"]
    assert reasons
    assert any("labelled row" in reason for reason in reasons)


def test_a_refusal_writes_no_artifact_and_registers_no_model(
    small_dataset, client, artifacts: Path
):
    response = client.post(
        "/api/ml/train", json={"dataset_id": small_dataset["dataset_id"]}
    )

    assert response.status_code == 422
    assert list(artifacts.glob("*.joblib")) == []
    assert client.get("/api/ml/experiments").json()["count"] == 0


def test_a_held_out_fraction_that_leaves_no_training_rows_is_a_422_not_a_crash(
    trainable_dataset, client
):
    """A value inside (0, 1) can still be unusable for this dataset.

    The schema bounds the fraction but cannot know how many rows the dataset has,
    so a fraction of 0.999 passes validation and then leaves nothing to train on.
    That is a caller error and is reported as one, rather than surfacing as a 500.
    """
    response = client.post(
        "/api/ml/train",
        json={"dataset_id": trainable_dataset["dataset_id"], "test_size": 0.999},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["refusal"] == "invalid_request"
    assert "training rows" in body["detail"]


def test_an_unknown_split_strategy_is_a_422_naming_the_real_ones(
    trainable_dataset, client
):
    """Defaulting to a split the caller did not choose would be worse."""
    response = client.post(
        "/api/ml/train",
        json={"dataset_id": trainable_dataset["dataset_id"], "split_strategy": "vibes"},
    )

    assert response.status_code == 422
    assert "chronological" in response.json()["detail"]


def test_training_a_drifted_dataset_is_refused_rather_than_registered(
    trainable_dataset, database: FakeDatabase, client, artifacts: Path
):
    """The records no longer match the manifest, so a version number would lie.

    A new observation is added after the dataset was built. The rows now hash to
    something else, and a model fitted to them would cite a ``dataset_version``
    whose own training data contradicts.
    """
    import asyncio

    from app.db.collections import PAN_OBSERVATIONS
    from app.db.repository import MongoRepository
    from app.models.pan_observation import PanObservation

    async def add_a_later_reading() -> None:
        repository: MongoRepository[PanObservation] = MongoRepository(
            database, PAN_OBSERVATIONS, PanObservation
        )
        await repository.insert(
            PanObservation(
                observation_id="obs-late-arrival",
                pan_id=PANS[0],
                timestamp=START + timedelta(hours=READINGS_PER_PAN + 5),
                salinity_g_l=499.0,
                water_depth_cm=21.0,
                brine_temperature_c=30.0,
            )
        )

    asyncio.run(add_a_later_reading())

    response = client.post(
        "/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]}
    )

    assert response.status_code == 409
    body = response.json()
    assert body["refusal"] == "dataset_drift"
    assert "rebuild" in body["detail"].lower()
    assert list(artifacts.glob("*.joblib")) == []
    assert client.get("/api/ml/experiments").json()["count"] == 0


def test_training_an_unknown_dataset_is_a_404_not_a_refusal(client):
    """A wrong id is a different problem from insufficient data."""
    response = client.post("/api/ml/train", json={"dataset_id": "ds-does-not-exist"})

    assert response.status_code == 404
    assert "refusal" not in response.json()


def test_a_regression_target_is_refused_by_name(database: FakeDatabase, client):
    """No substitute algorithm is fitted under a name nobody approved.

    The four agreed candidates are classifiers. Handing this module a regression
    target and quietly fitting a regressor would register an unrequested model
    under a requested name, so it is refused and the supported targets are named
    so the caller can see what is available.
    """
    seed(database)
    built = client.post(
        "/api/datasets/",
        json={"target": {"name": "post_rain_salinity_g_l", "horizon_hours": 1.0}},
    )
    assert built.status_code == 201, built.text

    response = client.post(
        "/api/ml/train",
        json={"dataset_id": built.json()["dataset"]["dataset_id"]},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["refusal"] == "unsupported_target"
    assert "post_rain_salinity_g_l" in body["detail"]
    assert "classification" in body["detail"]


def test_a_classification_target_that_is_not_trainable_here_is_refused(
    database: FakeDatabase, client
):
    """The refusal names the supported targets, so a caller can see the way in."""
    seed(database)
    built = client.post(
        "/api/datasets/",
        json={"target": {"name": "harvest_outcome", "horizon_hours": 1.0}},
    )
    if built.status_code != 201:
        # No outcomes seeded, so the target cannot be built at all. The
        # classification-only rule is covered by the regression test above.
        pytest.skip("harvest_outcome needs stored outcomes")
    response = client.post(
        "/api/ml/train",
        json={"dataset_id": built.json()["dataset"]["dataset_id"]},
    )

    assert response.status_code == 422
    assert response.json()["refusal"] in {
        "unsupported_target",
        "insufficient_data",
    }


def test_the_request_body_refuses_unknown_fields(trainable_dataset, client):
    """A misspelled setting is a 422, not a silently ignored default."""
    response = client.post(
        "/api/ml/train",
        json={"dataset_id": trainable_dataset["dataset_id"], "split_startegy": "random"},
    )

    assert response.status_code == 422
    assert "split_startegy" in response.text


# -- a real training run ------------------------------------------------


def test_training_a_sufficient_dataset_registers_a_model(trainable_dataset, client):
    response = client.post(
        "/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["model_id"]
    assert body["version"].startswith("v")
    assert body["target"] == "post_rain_salinity_band"
    # A held-out score is not a deployment decision.
    # A training run always registers the model as experimental. It cannot be
    # anything else: deployment is a separate, explicit act in Module 17, and this
    # is the property that stops a retrain from displacing a deployed model.
    assert body["status"] == "experimental"
    assert 0.0 <= body["metrics"]["f1_macro"] <= 1.0


def test_the_response_reports_all_four_candidates(trainable_dataset, client):
    """Fitting four models is only worth it if the comparison is visible."""
    response = client.post(
        "/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]}
    )

    candidates = response.json()["candidate_metrics"]
    assert set(candidates) == {
        "logistic_regression",
        "decision_tree",
        "random_forest",
        "gradient_boosting",
    }
    for scores in candidates.values():
        assert "f1_macro" in scores


def test_random_forest_is_not_the_selected_algorithm(trainable_dataset, client):
    response = client.post(
        "/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]}
    )

    body = response.json()
    assert body["algorithm"] != "random_forest"
    # Its score is still reported; it is withheld from selection, not from the run.
    assert "random_forest" in body["candidate_metrics"]
    assert body["allow_random_forest"] is False
    assert "random_forest" in body["selection_reason"]


def test_random_forest_can_be_selected_when_the_request_asks(
    trainable_dataset, client
):
    """Promotable deliberately, and the request is echoed in the response."""
    first = client.post(
        "/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]}
    )
    allowed = client.post(
        "/api/ml/train",
        json={
            "dataset_id": trainable_dataset["dataset_id"],
            "allow_random_forest": True,
        },
    )

    assert first.status_code == 201
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["allow_random_forest"] is True


def test_the_response_carries_the_provenance_of_the_run(trainable_dataset, client):
    """Everything needed to say what was fitted to what."""
    response = client.post(
        "/api/ml/train",
        json={"dataset_id": trainable_dataset["dataset_id"], "random_seed": 4242},
    )

    body = response.json()
    assert body["dataset_id"] == trainable_dataset["dataset_id"]
    assert body["dataset_version"] >= 1
    assert body["dataset_content_hash"].startswith("sha256:")
    assert body["feature_names"]
    assert body["split_strategy"] == "chronological"
    assert body["random_seed"] == 4242
    assert "scikit-learn" in body["library_versions"]
    assert body["trained_at"]
    assert body["split_detail"]["train_rows"] > 0
    assert body["preprocessing"]["output_feature_count"] > 0


def test_the_run_writes_a_loadable_artifact(trainable_dataset, client, artifacts: Path):
    response = client.post(
        "/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]}
    )

    path = Path(response.json()["artifact_path"])
    assert path.exists()
    blob = joblib.load(path)
    assert {"preprocessing", "estimator", "classes"} <= set(blob)


def test_training_the_same_dataset_twice_allocates_a_new_version(
    trainable_dataset, client
):
    """Each run is a distinct registered experiment, not an overwrite."""
    first = client.post(
        "/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]}
    )
    second = client.post(
        "/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]}
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["model_id"] != second.json()["model_id"]
    assert first.json()["version"] != second.json()["version"]


def test_the_same_seed_reproduces_the_same_metrics(trainable_dataset, client):
    """What the recorded seed is for."""
    first = client.post(
        "/api/ml/train",
        json={"dataset_id": trainable_dataset["dataset_id"], "random_seed": 99},
    )
    second = client.post(
        "/api/ml/train",
        json={"dataset_id": trainable_dataset["dataset_id"], "random_seed": 99},
    )

    assert first.json()["candidate_metrics"] == second.json()["candidate_metrics"]


@pytest.mark.parametrize(
    "strategy", ["chronological", "grouped_by_pan", "per_pan_chronological", "random"]
)
def test_every_split_strategy_can_be_chosen(trainable_dataset, client, strategy: str):
    response = client.post(
        "/api/ml/train",
        json={"dataset_id": trainable_dataset["dataset_id"], "split_strategy": strategy},
    )

    assert response.status_code == 201, response.text
    assert response.json()["split_strategy"] == strategy


def test_grouped_by_pan_reports_the_pans_it_held_out(trainable_dataset, client):
    response = client.post(
        "/api/ml/train",
        json={
            "dataset_id": trainable_dataset["dataset_id"],
            "split_strategy": "grouped_by_pan",
        },
    )

    detail = response.json()["split_detail"]
    assert detail["train_pans"]
    assert detail["test_pans"]
    assert set(detail["train_pans"]) & set(detail["test_pans"]) == set()


# -- the registry endpoints ---------------------------------------------


def test_the_registry_lists_the_registered_experiment(trainable_dataset, client):
    trained = client.post(
        "/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]}
    ).json()

    listed = client.get("/api/ml/experiments")

    assert listed.status_code == 200
    body = listed.json()
    assert body["count"] == 1
    assert body["experiments"][0]["model_id"] == trained["model_id"]
    assert body["experiments"][0]["dataset_id"] == trained["dataset_id"]


def test_the_registry_can_be_filtered_by_target(trainable_dataset, client):
    client.post("/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]})

    matching = client.get("/api/ml/experiments", params={"target": "post_rain_salinity_band"})
    other = client.get("/api/ml/experiments", params={"target": "operational_risk"})

    assert matching.json()["count"] == 1
    assert other.json()["count"] == 0


def test_one_experiment_can_be_read_back(trainable_dataset, client):
    trained = client.post(
        "/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]}
    ).json()

    read = client.get(f"/api/ml/experiments/{trained['model_id']}")

    assert read.status_code == 200
    assert read.json()["model_id"] == trained["model_id"]
    assert read.json()["split_strategy"] == "chronological"
    assert read.json()["random_seed"] == trained["random_seed"]


def test_reading_an_unknown_experiment_is_a_404(client):
    response = client.get("/api/ml/experiments/model-does-not-exist")

    assert response.status_code == 404
    assert "model-does-not-exist" in response.json()["detail"]


def test_an_empty_registry_is_an_empty_list_not_an_error(client):
    response = client.get("/api/ml/experiments")

    assert response.status_code == 200
    assert response.json() == {
        "experiments": [],
        "count": 0,
        "available_targets": response.json()["available_targets"],
        "split_strategies": response.json()["split_strategies"],
    }


def test_the_status_endpoint_does_not_contradict_itself(client):
    """The status note once said training was not implemented, above implemented: true.

    A reader taking either field at face value would be wrong about the other.
    """
    body = client.get("/api/ml/").json()

    assert body["implemented"] is True
    note = body["note"].lower()
    assert "not implemented" not in note
    assert "does not exist" not in note
    # It should say what it refuses to do, which is the useful half.
    assert "synthesise" in note or "synthesize" in note
    assert body["insufficient_data_message"] == INSUFFICIENT_DATA_MESSAGE


def test_the_experiment_list_carries_the_vocabulary_it_filters_on(
    trainable_dataset, client
):
    """A filter a client can only populate from a hardcoded list is a hardcoded list.

    docs/api.md documents this response shape, so the shape is asserted here.
    """
    client.post("/api/ml/train", json={"dataset_id": trainable_dataset["dataset_id"]})

    body = client.get("/api/ml/experiments").json()

    assert set(body) == {
        "count",
        "experiments",
        "available_targets",
        "split_strategies",
    }
    assert "post_rain_salinity_band" in body["available_targets"]
    assert "chronological" in body["split_strategies"]

    filtered = client.get(
        "/api/ml/experiments?target=post_rain_salinity_band"
    ).json()
    assert filtered["count"] == 1
    assert (
        client.get("/api/ml/experiments?target=harvest_outcome").json()["count"] == 0
    )
