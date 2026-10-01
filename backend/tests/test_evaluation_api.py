"""Module 9 over HTTP, against a real trained model.

These tests train through Module 08's own API, then evaluate through Module 09's, so
the model being scored is one the pipeline actually fitted to records the database
actually holds. Nothing here stubs the scorer, the resampler or the split: if a
metric is absent from a response, it is absent because the data made it undefined,
and the assertions below check that the reason came with it.

The centre of gravity is the honesty property. Every other kind of test -- that the
endpoint returns 201, that the table has four rows, that the export parses -- would
still pass on a module that reported accuracy 1.00 for everything. These tests fail
on that module, and the ones that matter most are:

* an absent metric is absent, with its reason, and is never ``0.0``;
* ROC-AUC is refused rather than reported as the score of a coin;
* the selected row is flagged by a rule that is stated in the response beside it, and
  nothing anywhere says "best";
* re-running replaces one report rather than accumulating a second claim about the
  same rows;
* the CSV export writes an empty cell where a metric was never computed.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.datasets import get_dataset_service, get_dataset_status_service
from app.api.routers.evaluation import get_evaluation_service
from app.api.routers.ml import get_ml_service
from app.api.routers.observations import (
    get_observation_service,
    get_observation_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.weather import get_weather_service, get_weather_status_service
from app.db.collections import MODEL_EVALUATIONS, PAN_OBSERVATIONS, SALT_PANS
from app.db.repository import MongoRepository
from app.main import app
from app.models.pan_observation import PanObservation
from app.models.salt_pan import RiskThresholds, SaltPan
from app.services.datasets import DatasetService
from app.services.evaluation import EvaluationService
from app.services.ml import MLService
from app.services.observations import ObservationService
from app.services.salt_pans import SaltPanService
from app.services.weather import WeatherService
from tests.fakes import FakeDatabase

#: The same seed Module 08's tests use: four pans, thirty hourly readings each,
#: salinity spanning both band edges. Real records either way, so the classes are a
#: fact about the readings rather than about how the fixture was written.
BIN_EDGES = [262.0, 272.0]
PANS = ["KZN-2026-A01", "KZN-2026-B02", "KZN-2026-C03", "KZN-2026-D04"]
READINGS_PER_PAN = 30
START = datetime(2026, 3, 1, tzinfo=timezone.utc)
PAN_DRIFT = {"KZN-2026-A01": -1.2, "KZN-2026-B02": 0.0, "KZN-2026-C03": 1.4, "KZN-2026-D04": 0.6}

#: The six columns the comparison asks for, in order. Spelled out here rather than
#: imported so a change to the backend's column list fails a test rather than being
#: silently adopted by one.
EXPECTED_COLUMNS = ["accuracy", "precision", "recall", "f1", "roc_auc", "pr_auc"]


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
    app.dependency_overrides[get_observation_service] = lambda: ObservationService(
        database
    )
    app.dependency_overrides[get_observation_status_service] = lambda: (
        ObservationService(database)
    )
    app.dependency_overrides[get_weather_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_weather_status_service] = lambda: WeatherService(
        database
    )
    app.dependency_overrides[get_dataset_service] = lambda: DatasetService(database)
    app.dependency_overrides[get_dataset_status_service] = lambda: DatasetService(
        database
    )
    app.dependency_overrides[get_ml_service] = lambda: MLService(
        database, artifact_directory=artifacts
    )
    # The same directory the training service writes to. Evaluations load the
    # registered artifact rather than refitting, so a different directory here would
    # make every run refuse with artifact_unavailable and test nothing else.
    app.dependency_overrides[get_evaluation_service] = lambda: EvaluationService(
        database, artifact_directory=artifacts
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def seed(database: FakeDatabase, *, pans: int = 4, readings: int = READINGS_PER_PAN) -> None:
    asyncio.run(_seed(database, pans=pans, readings=readings))


async def _seed(
    database: FakeDatabase, *, pans: int = 4, readings: int = READINGS_PER_PAN
) -> None:
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


def build_dataset(client: TestClient) -> dict[str, Any]:
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
    return {"dataset_id": response.json()["dataset"]["dataset_id"]}


def train(client: TestClient, dataset_id: str, **body: Any) -> dict[str, Any]:
    response = client.post(
        "/api/ml/train", json={"dataset_id": dataset_id, **body}
    )
    assert response.status_code == 201, response.text
    return response.json()


def evaluate(client: TestClient, model_id: str, **body: Any) -> dict[str, Any]:
    response = client.post(
        "/api/evaluation/run", json={"model_id": model_id, **body}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def stored_evaluations(database: FakeDatabase) -> list[dict[str, Any]]:
    return database[MODEL_EVALUATIONS].documents()


def compare(client: TestClient, model_id: str) -> dict[str, Any]:
    """The stored comparison table, as the compare endpoint serves it.

    The endpoint returns the table that was written at run time rather than
    rebuilding it, so this reads the same bytes a reader's download contains.
    """
    response = client.get("/api/evaluation/compare", params={"model_id": model_id})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture()
def trained(client: TestClient, database: FakeDatabase) -> dict[str, Any]:
    """A registered model version, fitted to real stored records."""
    seed(database)
    dataset = build_dataset(client)
    model = train(client, dataset["dataset_id"])
    return {"dataset": dataset, "model": model}


@pytest.fixture()
def evaluated(client: TestClient, trained: dict[str, Any]) -> dict[str, Any]:
    """A stored evaluation of a real registered model."""
    report = evaluate(client, trained["model"]["model_id"])
    return {**trained, "report": report}


# -- the status endpoint -------------------------------------------------


def test_the_status_endpoint_states_the_metric_representation(client):
    """The contract a client has to know: a metric is a reading, not a number."""
    response = client.get("/api/evaluation/")
    assert response.status_code == 200
    body = response.json()
    assert body["implemented"] is True
    # False, because nothing is evaluated until a caller asks. A status page that
    # claimed otherwise would imply a report exists.
    assert body["evaluated"] is False
    assert "never a bare number" in body["metric_representation"]
    assert body["comparison_columns"] == ["model", *EXPECTED_COLUMNS]


def test_the_status_endpoint_does_not_claim_a_winner(client):
    body = client.get("/api/evaluation/").json()
    # The rule is stated rather than implied, so a reader can check the selection
    # against the scores instead of trusting a label. "Macro F1" is the criterion
    # Module 08 actually applies, which makes it checkable, and the tie-breaks are
    # written down so the outcome is deterministic rather than luck.
    rule = body["selection_rule"]
    assert "macro F1" in rule
    assert "balanced accuracy" in rule
    # The superlative is disclaimed in words. A rule that merely omitted the word
    # would leave open whether the omission was a choice or an oversight.
    assert "No row is labelled 'best'" in rule
    # Random Forest is still a candidate; it just cannot win the rule.
    assert "never selected automatically" in rule


# -- running an evaluation -----------------------------------------------


def test_evaluating_a_registered_model_returns_201(evaluated):
    report = evaluated["report"]
    assert report["model_id"] == evaluated["model"]["model_id"]
    assert report["version"] == evaluated["model"]["version"]
    assert report["recomputed"] is False
    assert report["evaluated_rows"] > 0


def test_the_report_is_stored_against_the_model_version(client, database, evaluated):
    documents = stored_evaluations(database)
    assert len(documents) == 1
    document = documents[0]
    assert document["model_id"] == evaluated["model"]["model_id"]
    assert document["version"] == evaluated["model"]["version"]
    # The registry's own key is (target, version); the evaluation cites both so a
    # figure can be traced to the exact artifact that produced it.
    assert document["target"] == evaluated["model"]["target"]
    assert document["artifact_path"] == evaluated["model"]["artifact_path"]


def test_the_report_carries_the_provenance_of_the_training_run(evaluated):
    report = evaluated["report"]
    model = evaluated["model"]
    provenance = report["provenance"]
    assert provenance["dataset_id"] == model["dataset_id"]
    assert provenance["dataset_content_hash"] == model["dataset_content_hash"]
    assert provenance["random_seed"] == model["random_seed"]
    assert provenance["split_strategy"] == model["split_strategy"]
    assert provenance["feature_names"] == model["feature_names"]
    # Library versions travel with the numbers, because a figure from a different
    # scikit-learn is not the same figure.
    assert provenance["library_versions"]["scikit-learn"]


def test_the_rows_are_named_on_every_figure(client, evaluated):
    """A metric with no stated denominator is the one figure this module refuses."""
    report = evaluated["report"]
    assert report["evaluation_set"]
    assert report["evaluation_set"] == report["evaluation"]["evaluation_set"]
    assert report["evaluated_rows"] == report["evaluation"]["evaluated_rows"]
    for evaluation in report["candidate_evaluations"].values():
        assert evaluation["evaluation_set"] == report["evaluation_set"]
        assert evaluation["evaluated_rows"] == report["evaluated_rows"]


def test_every_candidate_is_evaluated_not_only_the_selected_one(evaluated):
    candidates = evaluated["report"]["candidate_evaluations"]
    assert set(candidates) == {
        "logistic_regression",
        "decision_tree",
        "random_forest",
        "gradient_boosting",
    }
    # The selected model's figures come from the registered artifact; the others
    # from a deterministic refit. The report says which, because a reader who
    # assumed all four came out of a registered file would be wrong about three.
    assert any("refitting" in note for note in evaluated["report"]["notes"])


def test_every_metric_is_a_reading_and_never_a_bare_number(evaluated):
    report = evaluated["report"]
    evaluation = report["evaluation"]
    readings = [
        evaluation["accuracy"],
        evaluation["precision"],
        evaluation["recall"],
        evaluation["f1"],
        evaluation["roc_auc"],
        evaluation["pr_auc"],
    ]
    for reading in readings:
        assert set(reading) >= {"value", "computed", "reason"}
        # The invariant, checked here rather than trusted: computed iff a value.
        assert reading["computed"] is (reading["value"] is not None)
        if not reading["computed"]:
            assert reading["reason"], "an absent metric must say why"


def test_per_class_performance_is_reported_for_every_label(client, evaluated):
    report = evaluated["report"]
    per_class = report["evaluation"]["per_class"]
    classes = report["evaluation"]["classes"]
    assert [entry["label"] for entry in per_class] == classes
    for entry in per_class:
        assert entry["support"] >= 0
        assert entry["predicted"] >= 0
        for metric in ("precision", "recall", "f1", "average_precision"):
            reading = entry[metric]
            assert reading["computed"] is (reading["value"] is not None)


def test_the_confusion_matrix_covers_the_models_whole_label_space(evaluated):
    matrix = evaluated["report"]["evaluation"]["confusion_matrix"]
    assert matrix is not None
    labels = matrix["labels"]
    assert labels == evaluated["report"]["evaluation"]["classes"]
    assert len(matrix["matrix"]) == len(labels)
    assert all(len(row) == len(labels) for row in matrix["matrix"])
    # Row sums are true counts, so they must total the evaluated rows. A matrix
    # that dropped a class would not, and a reader comparing two runs would see a
    # 2x2 and a 3x3 with no way to tell which.
    total = sum(sum(row) for row in matrix["matrix"])
    assert total == evaluated["report"]["evaluated_rows"]


def test_absent_classes_are_named_rather_than_shown_as_zero(evaluated):
    matrix = evaluated["report"]["evaluation"]["confusion_matrix"]
    # Either list may be empty, but the fields exist so a reader can tell a class
    # that did not occur from a class that was never predicted.
    assert isinstance(matrix["classes_absent_from_evaluation"], list)
    assert isinstance(matrix["classes_absent_from_evaluation_rows"], list)


# -- the comparison table ------------------------------------------------


def test_the_comparison_table_has_the_six_asked_for_columns(client, evaluated):
    report = compare(client, evaluated["model"]["model_id"])
    assert report["columns"] == EXPECTED_COLUMNS
    # 'model' leads the table and is not a metric, which is why the metric columns
    # and the table's row label are separate fields.
    assert report["column_titles"]["accuracy"]
    for row in report["rows"]:
        assert set(row["metrics"]) == set(EXPECTED_COLUMNS)


def test_the_comparison_table_rows_are_the_candidate_algorithms(client, evaluated):
    report = compare(client, evaluated["model"]["model_id"])
    assert [row["model"] for row in report["rows"]] == [
        "logistic_regression",
        "decision_tree",
        "random_forest",
        "gradient_boosting",
    ]


def test_the_selected_row_is_the_one_module_eight_selected(client, evaluated):
    report = compare(client, evaluated["model"]["model_id"])
    selected = [row for row in report["rows"] if row["selected"]]
    assert len(selected) == 1
    assert selected[0]["model"] == evaluated["model"]["algorithm"]
    # The registry's choice, not a fresh ranking computed here. A module that
    # re-picked a winner from the table could disagree with the artifact it grades.
    assert selected[0]["model"] != "random_forest"


def test_nothing_anywhere_in_the_table_is_called_best(client, evaluated):
    report = compare(client, evaluated["model"]["model_id"])
    # The selection is a boolean plus a stated rule. A superlative would be a claim
    # a reader could not check against the scores beside it.
    blob = repr(report).lower()
    assert '"model": "best"' not in blob
    assert "best model" not in blob


def test_the_selection_criterion_is_stated_in_the_table(client, evaluated):
    report = compare(client, evaluated["model"]["model_id"])
    assert "macro F1" in report["selection_criterion"]
    assert "never selected" in report["selection_criterion"]


def test_random_forest_is_shown_and_marked_ineligible(client, evaluated):
    report = compare(client, evaluated["model"]["model_id"])
    forest = next(row for row in report["rows"] if row["model"] == "random_forest")
    assert forest["auto_selectable"] is False
    # The reason travels with the row. A model shown beside the others without a
    # note that it cannot win looks like it is in the running.
    assert "never" in forest["selection_note"]


def test_the_table_states_that_its_rows_share_a_denominator(client, evaluated):
    report = compare(client, evaluated["model"]["model_id"])
    comparable = report["comparable"]
    assert comparable["same_row_count"] is True
    assert comparable["same_evaluation_set"] is True
    assert comparable["evaluated_rows"] == evaluated["report"]["evaluated_rows"]
    assert comparable["caveats"] == []


def test_the_table_states_the_conventions_in_force(client, evaluated):
    report = compare(client, evaluated["model"]["model_id"])
    # The zero_division convention is declared on the table, not only inside a
    # per-class reading nobody opens. A macro figure that is half convention and
    # half measurement should not read like a measured one.
    assert any("zero_division" in note for note in report["conventions_in_force"])


def test_the_table_explains_what_pr_auc_means(client, evaluated):
    report = compare(client, evaluated["model"]["model_id"])
    # "PR-AUC" is a different computation for two classes than for three, and a
    # bare header invites a reader to treat the two as the same number.
    assert "one-vs-rest" in report["average_precision_note"]


def test_the_table_says_what_it_will_not_do(client, evaluated):
    report = compare(client, evaluated["model"]["model_id"])
    assert "best" in report["does_not"].lower()


# -- refusals ------------------------------------------------------------


def test_evaluating_an_unknown_model_is_a_404(client):
    response = client.post("/api/evaluation/run", json={"model_id": "model-nope"})
    assert response.status_code == 404
    body = response.json()
    assert body["refusal"] == "not_evaluable"
    assert "fitted and registered" in body["detail"]


def test_reading_an_evaluation_that_was_never_run_is_a_404(client, trained):
    response = client.get(f"/api/evaluation/{trained['model']['model_id']}/metrics")
    assert response.status_code == 404
    body = response.json()
    assert body["refusal"] == "not_evaluable"
    # The fix is stated: a model existing is not evidence it has been scored.
    assert "POST /api/evaluation/run" in body["detail"]


def test_a_missing_artifact_is_a_409_and_writes_nothing(
    client, database, trained, artifacts: Path
):
    for path in artifacts.glob("*.joblib"):
        path.unlink()
    response = client.post(
        "/api/evaluation/run", json={"model_id": trained["model"]["model_id"]}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["refusal"] == "artifact_unavailable"
    # The alternative -- a figure attributed to a registered artifact that is not
    # there -- is the one thing this module must not produce.
    assert stored_evaluations(database) == []


def test_a_drifted_dataset_is_a_409_and_writes_nothing(client, database, trained):
    response = client.post(
        "/api/evaluation/run", json={"model_id": trained["model"]["model_id"]}
    )
    assert response.status_code == 201
    assert len(stored_evaluations(database)) == 1

    # A reading that arrives after the model was trained makes the stored records
    # no longer hash to what the manifest recorded. The evaluation must refuse
    # rather than attach itself to a dataset revision the model was not fitted to.
    asyncio.run(_add_a_later_reading(database))
    second = client.post(
        "/api/evaluation/run", json={"model_id": trained["model"]["model_id"]}
    )
    assert second.status_code == 409
    body = second.json()
    assert body["refusal"] == "dataset_drift"
    assert "no longer matches" in body["detail"]
    # The report written before the drift is still the only report; the refused run
    # added nothing and did not overwrite it.
    assert len(stored_evaluations(database)) == 1


async def _add_a_later_reading(database: FakeDatabase) -> None:
    repository: MongoRepository[PanObservation] = MongoRepository(
        database, PAN_OBSERVATIONS, PanObservation
    )
    await repository.insert(
        PanObservation(
            observation_id="obs-drift-001",
            pan_id=PANS[0],
            timestamp=START + timedelta(hours=READINGS_PER_PAN + 5),
            # Within the model's own bounds. The drift is the arrival of a reading
            # at all, not an impossible value: a real reading arriving is exactly
            # how this happens in production.
            salinity_g_l=270.5,
            water_depth_cm=20.0,
            brine_temperature_c=30.0,
            humidity_percent=60.0,
            ambient_temperature_c=25.0,
            created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
    )


def test_the_request_body_refuses_unknown_fields(client, trained):
    response = client.post(
        "/api/evaluation/run",
        json={
            "model_id": trained["model"]["model_id"],
            "cross_validation_split": 3,
        },
    )
    assert response.status_code == 422
    # A misspelled field is rejected rather than silently ignored, so a caller
    # cannot believe they chose a fold count they did not.


@pytest.mark.parametrize("splits", [1, 6])
def test_a_fold_count_outside_the_supported_range_is_a_422(client, trained, splits):
    response = client.post(
        "/api/evaluation/run",
        json={"model_id": trained["model"]["model_id"], "cross_validation_splits": splits},
    )
    assert response.status_code == 422


# -- one report per model version ---------------------------------------


def test_re_running_replaces_the_report_rather_than_adding_a_second(
    client, database, evaluated
):
    model_id = evaluated["model"]["model_id"]
    first = evaluated["report"]
    second = evaluate(client, model_id)
    assert len(stored_evaluations(database)) == 1
    # The run is deterministic given the recorded seed, so this is one measurement
    # refreshed and not a second one. A 201 would claim otherwise.
    assert second["recomputed"] is True
    assert second["evaluated_at"] >= first["evaluated_at"]
    assert second["evaluation"] == first["evaluation"]


def test_re_running_produces_the_same_figures(client, evaluated):
    model_id = evaluated["model"]["model_id"]
    first = compare(client, model_id)
    second = compare(client, evaluate(client, model_id) and model_id)
    assert [row["metrics"] for row in second["rows"]] == [
        row["metrics"] for row in first["rows"]
    ]


# -- the list and the cross-version view --------------------------------


def test_the_list_reports_stored_evaluations(client, evaluated):
    response = client.get("/api/evaluation/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["evaluations"][0]["model_id"] == evaluated["model"]["model_id"]
    assert body["evaluations"][0]["evaluated_rows"] > 0
    # The summary's metrics are the selected model's readings, so a list row and a
    # detail page cannot disagree about the headline number.
    assert body["evaluations"][0]["metrics"]["accuracy"]["computed"] is True


def test_the_list_offers_the_models_that_have_not_been_evaluated(client, database):
    """A model hidden behind a decision to score it is a model a client cannot score."""
    seed(database)
    dataset = build_dataset(client)
    model = train(client, dataset["dataset_id"])
    body = client.get("/api/evaluation/metrics").json()
    assert body["count"] == 0
    assert body["evaluable_models"][0]["model_id"] == model["model_id"]
    assert body["evaluable_models"][0]["evaluated"] is False


def test_an_empty_store_compares_to_nothing_rather_than_to_itself(client):
    body = client.get("/api/evaluation/compare/versions").json()
    assert body["count"] == 0
    assert body["comparison"] is None
    assert any("nothing to compare" in note for note in body["not_comparable"])


def test_one_evaluation_is_not_a_comparison(client, evaluated):
    body = client.get("/api/evaluation/compare/versions").json()
    assert body["count"] == 1
    assert body["comparison"] is None
    assert any("One number is not a comparison" in note for note in body["not_comparable"])


def test_two_versions_of_the_same_dataset_can_be_compared(client, database, evaluated):
    # A second training run allocates a new version over the same records, so the
    # two evaluations share a row set and a row count and the table is meaningful.
    second_model = train(client, evaluated["dataset"]["dataset_id"])
    evaluate(client, second_model["model_id"])
    body = client.get("/api/evaluation/compare/versions").json()
    assert body["count"] == 2
    assert body["not_comparable"] == []
    assert body["comparison"] is not None
    assert body["comparison"]["columns"] == EXPECTED_COLUMNS
    # Nothing selects a winner across versions: the macro-F1 rule picks among
    # algorithms fitted on one dataset, and applying it here would rank figures
    # from different held-out sets under a criterion written for a single one.
    assert body["comparison"]["selected_algorithm"] is None
    assert all(row["selected"] is False for row in body["comparison"]["rows"])


# -- the download --------------------------------------------------------


def test_the_json_export_is_the_stored_record(client, evaluated):
    model_id = evaluated["model"]["model_id"]
    response = client.get(f"/api/evaluation/{model_id}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith(".json\"")
    body = response.json()
    assert body["model_id"] == model_id
    assert body["provenance"]["dataset_content_hash"] == evaluated["model"][
        "dataset_content_hash"
    ]
    assert body["comparison"]["columns"] == EXPECTED_COLUMNS
    # The flat table the API does not serve, for a reader with a spreadsheet.
    assert len(body["comparison_table"]) == 4


def test_the_csv_export_writes_the_table_with_reasons(client, evaluated):
    import csv as csv_module
    import io

    model_id = evaluated["model"]["model_id"]
    response = client.get(f"/api/evaluation/{model_id}/export?format=csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    rows = list(csv_module.DictReader(io.StringIO(response.text)))
    assert len(rows) == 4
    for row in rows:
        for column in EXPECTED_COLUMNS:
            # An absent metric is an empty cell, and the reason is in its own
            # column. A 0 here would be summed by whoever opens the file.
            assert f"{column}_computed" in row
            assert f"{column}_reason" in row
            if row[f"{column}_computed"] == "False":
                assert row[column] == ""
                assert row[f"{column}_reason"]


def test_the_export_of_an_unevaluated_model_is_a_404(client, trained):
    response = client.get(f"/api/evaluation/{trained['model']['model_id']}/export")
    assert response.status_code == 404


def test_the_export_cannot_contain_a_metric_the_response_omitted(client, evaluated):
    """JSON and CSV are built from the same readings, so neither invents one."""
    import csv as csv_module
    import io

    model_id = evaluated["model"]["model_id"]
    detail = client.get(f"/api/evaluation/{model_id}/metrics").json()
    exported = client.get(f"/api/evaluation/{model_id}/export").json()
    rows = list(
        csv_module.DictReader(
            io.StringIO(
                client.get(f"/api/evaluation/{model_id}/export?format=csv").text
            )
        )
    )
    for row in rows:
        candidate = detail["candidate_evaluations"][row["model"]]
        for column in EXPECTED_COLUMNS:
            reading = candidate[column]
            if reading["computed"]:
                assert float(row[column]) == pytest.approx(reading["value"])
                assert row[f"{column}_computed"] == "True"
            else:
                assert row[column] == ""
                assert row[f"{column}_reason"] == reading["reason"]
    assert exported["comparison_table"][0]["model"] == rows[0]["model"]
