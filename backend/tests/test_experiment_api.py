"""Module 19 - the research experiment ledger, end to end.

The tests that would catch the failure this module exists to prevent: a result
that cannot be reproduced. So the assertions are about *integrity* as much as
about presence -

- an experiment is stored with everything that produced it, and the content hash
  is computed server-side, never accepted from the request;
- re-posting the identical experiment returns the stored one (``created=false``)
  and a run that differs in any recorded fact is a new row - nothing is ever
  overwritten;
- the reproducibility manifest's ``record`` is exactly the hashed payload, so
  re-hashing it reproduces ``content_hash``;
- a fabricated hash, a backwards date range, an empty feature set or an unknown
  field are all rejected, because a record that cannot be checked cannot be
  reproduced.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, TypeVar

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.experiments import (
    get_experiment_service,
    get_experiment_status_service,
)
from app.db.errors import DuplicateDocumentError
from app.db.repository import MongoRepository
from app.main import app
from app.models.base import BaseDocument
from app.models.experiment import ResearchExperiment
from app.services.dataset_versioning import content_hash
from app.services.experiments import ExperimentService
from tests.fakes import FakeDatabase

DocumentT = TypeVar("DocumentT", bound=BaseDocument)


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def experiment_client(database: FakeDatabase) -> Any:
    """A TestClient backed by memory for the experiment routes."""
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_experiment_service] = lambda: ExperimentService(database)
    app.dependency_overrides[get_experiment_status_service] = (
        lambda: ExperimentService(database)
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def experiment(**overrides: Any) -> dict[str, Any]:
    """One complete experiment record, as a researcher would submit it."""
    payload = {
        "dataset_version": 3,
        "dataset_hash": "sha256:" + "a" * 64,
        "n_observations": 1024,
        "n_pans": 6,
        "date_start": "2025-01-01",
        "date_end": "2026-03-31",
        "features": [
            "pre_event_salinity_g_l",
            "pre_event_depth_cm",
            "cumulative_rainfall_mm",
        ],
        "target": "operational_risk",
        "target_definition_version": 2,
        "train_test_strategy": "80/20 chronological holdout",
        "random_seed": 42,
        "model": "random-forest",
        "hyperparameters": {
            "n_estimators": 300,
            "max_depth": 8,
            "class_weight": "balanced",
        },
        "metrics": {"accuracy": 0.94, "macro_f1": 0.91},
        "software_versions": {"python": "3.12", "sklearn": "1.6.1"},
        "notes": "First holdout on the v3 dataset.",
    }
    return {**payload, **overrides}


def record_experiment(client: TestClient, **overrides: Any) -> Any:
    return client.post("/api/experiments/", json=experiment(**overrides))


def ledger(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get("/api/experiments/", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_reports_an_empty_implemented_module(experiment_client: TestClient):
    response = experiment_client.get("/api/experiments/status")
    assert response.status_code == 200
    body = response.json()
    assert body["module"] == "research-experiments"
    assert body["module_number"] == 19
    assert body["status"] == "implemented"
    assert body["experiments"]["available"] is True
    assert body["experiments"]["experiments_total"] == 0


def test_status_counts_distinct_facts_after_recording(
    experiment_client: TestClient,
):
    record_experiment(experiment_client)
    record_experiment(
        experiment_client,
        model="gradient-boosting",
        dataset_hash="sha256:" + "b" * 64,
        random_seed=7,
    )
    record_experiment(experiment_client, metrics={"accuracy": 0.9})  # different content

    body = experiment_client.get("/api/experiments/status").json()
    assert body["experiments"]["experiments_total"] == 3
    assert body["experiments"]["distinct_models"] == 2
    assert body["experiments"]["distinct_datasets"] == 2
    assert body["experiments"]["distinct_targets"] == 1
    assert body["experiments"]["oldest_recorded_at"] is not None
    assert body["experiments"]["newest_recorded_at"] is not None


def test_status_reports_an_unavailable_ledger_without_raising(
    experiment_client: TestClient, database: FakeDatabase
):
    app.dependency_overrides[get_experiment_status_service] = lambda: None
    body = experiment_client.get("/api/experiments/status").json()
    assert body["experiments"]["available"] is False
    assert "detail" in body["experiments"]


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------


def test_create_records_an_experiment_with_every_required_fact(
    experiment_client: TestClient,
):
    response = record_experiment(experiment_client)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["created"] is True
    stored = body["experiment"]
    assert stored["experiment_id"].startswith("exp-")
    assert stored["content_hash"].startswith("sha256:")
    assert stored["short_hash"] == stored["content_hash"].split(":")[1][:12]
    assert stored["timestamp"] is not None
    assert stored["manifest_version"] == "1.0"
    assert stored["n_observations"] == 1024
    assert stored["n_pans"] == 6
    assert stored["features"] == experiment()["features"]
    assert stored["metrics"] == {"accuracy": 0.94, "macro_f1": 0.91}
    assert stored["software_versions"] == {"python": "3.12", "sklearn": "1.6.1"}


def test_content_hash_is_computed_server_side_and_cannot_be_forged(
    experiment_client: TestClient,
):
    response = experiment_client.post(
        "/api/experiments/", json={**experiment(), "content_hash": "sha256:" + "e" * 64}
    )
    assert response.status_code == 422


def test_a_run_that_differs_in_one_recorded_fact_is_a_new_experiment(
    experiment_client: TestClient,
):
    first = record_experiment(experiment_client).json()["experiment"]

    variants = [
        experiment(metrics={"accuracy": 0.95, "macro_f1": 0.91}),
        experiment(random_seed=43),
        experiment(notes="A retry of the identical run to double-check."),
        experiment(hyperparameters={"n_estimators": 400, "max_depth": 8}),
    ]
    for variant in variants:
        response = experiment_client.post("/api/experiments/", json=variant)
        assert response.status_code == 201, response.text
        other = response.json()["experiment"]
        assert other["experiment_id"] != first["experiment_id"]
        assert other["content_hash"] != first["content_hash"]

    assert ledger(experiment_client)["total"] == 5


def test_an_identical_resubmission_returns_the_stored_experiment(
    experiment_client: TestClient,
):
    first = record_experiment(experiment_client)
    assert first.status_code == 201
    first_body = first.json()

    second = record_experiment(experiment_client)
    assert second.status_code == 200, second.text
    second_body = second.json()

    assert second_body["created"] is False
    assert second_body["reason"] is not None
    assert (
        second_body["experiment"]["experiment_id"]
        == first_body["experiment"]["experiment_id"]
    )
    assert second_body["experiment"]["content_hash"] == first_body["experiment"]["content_hash"]
    # One experiment, not two: a duplicate row would look like a reproduction.
    assert ledger(experiment_client)["total"] == 1


def test_the_unique_content_hash_index_is_the_backstop_for_a_duplicate(
    database: FakeDatabase,
):
    repository = MongoRepository(database, "research_experiments", ResearchExperiment)
    stored = asyncio.run(
        repository.insert(
            ResearchExperiment(**experiment(), experiment_id="exp-write-a", content_hash="sha256:" + "a" * 64)
        )
    )
    duplicate = stored.model_copy(update={"experiment_id": "exp-write-b"})
    with pytest.raises(DuplicateDocumentError):
        asyncio.run(repository.insert(duplicate))


def test_malformed_experiments_are_refused_rather_than_stored(
    experiment_client: TestClient,
):
    refused_payloads = [
        (
            "dates_run_backwards",
            experiment(date_start="2026-12-01", date_end="2020-01-01"),
        ),
        ("empty_feature_set", experiment(features=[])),
        ("no_metrics", experiment(metrics={})),
        ("no_software_versions", experiment(software_versions={})),
        ("dataset_hash_not_a_hash", experiment(dataset_hash="not-a-hash")),
        ("unknown_field", {**experiment(), "gpu": "a100"}),
        ("negative_observations", experiment(n_observations=-1)),
    ]
    for _, payload in refused_payloads:
        response = experiment_client.post("/api/experiments/", json=payload)
        assert response.status_code == 422, response.text

    assert ledger(experiment_client)["total"] == 0


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def test_ledger_lists_newest_first_with_filters_and_paging(
    experiment_client: TestClient,
):
    record_experiment(experiment_client, model="random-forest", target="operational_risk", random_seed=1)
    record_experiment(experiment_client, model="gradient-boosting", target="operational_risk", random_seed=2)
    record_experiment(
        experiment_client,
        model="random-forest",
        target="yield_tonnes",
        dataset_hash="sha256:" + "c" * 64,
        random_seed=3,
    )

    # Newest first across the whole ledger.
    all_rows = ledger(experiment_client)
    assert all_rows["total"] == 3
    assert [row["random_seed"] for row in all_rows["items"]] == [3, 2, 1]

    # Filtered by model.
    by_model = ledger(experiment_client, model="random-forest")
    assert by_model["total"] == 2
    assert {row["random_seed"] for row in by_model["items"]} == {1, 3}

    # Filtered by dataset identity, exactly.
    by_dataset = ledger(experiment_client, dataset_hash="sha256:" + "c" * 64)
    assert by_dataset["total"] == 1
    assert by_dataset["items"][0]["random_seed"] == 3

    # Paged.
    page = ledger(experiment_client, limit=2, offset=1)
    assert [row["random_seed"] for row in page["items"]] == [2, 1]
    assert page["total"] == 3


def test_open_one_experiment_by_id(experiment_client: TestClient):
    stored = record_experiment(experiment_client).json()["experiment"]

    response = experiment_client.get(f"/api/experiments/{stored['experiment_id']}")
    assert response.status_code == 200
    assert response.json()["experiment_id"] == stored["experiment_id"]
    assert response.json()["content_hash"] == stored["content_hash"]

    missing = experiment_client.get("/api/experiments/exp-does-not-exist")
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# the reproducibility manifest
# ---------------------------------------------------------------------------


def test_manifest_rehashes_to_its_own_content_hash(experiment_client: TestClient):
    stored = record_experiment(experiment_client).json()["experiment"]

    response = experiment_client.get(
        f"/api/experiments/{stored['experiment_id']}/manifest"
    )
    assert response.status_code == 200
    manifest = response.json()

    assert manifest["experiment_id"] == stored["experiment_id"]
    assert manifest["manifest_version"] == "1.0"
    assert manifest["hash_algorithm"] == "sha256"
    assert manifest["canonical_form"] == "sorted-keys-stable-json"
    assert manifest["record"]["model"] == "random-forest"

    # The integrity claim, verified: re-hashing the manifest's record on the
    # canonical encoding reproduces the stored identity hash.
    recomputed = content_hash(manifest["record"])
    assert recomputed == manifest["content_hash"] == stored["content_hash"]

    # The record is exactly what was submitted - no derived fields leak in.
    assert set(manifest["record"]) == set(experiment())


def test_manifest_endpoint_404s_for_an_unknown_experiment(experiment_client: TestClient):
    response = experiment_client.get("/api/experiments/exp-nope/manifest")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_per_experiment_csv_is_the_metrics_table(experiment_client: TestClient):
    stored = record_experiment(experiment_client).json()["experiment"]
    response = experiment_client.get(
        f"/api/experiments/{stored['experiment_id']}/export", params={"output_format": "csv"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "filename=" in response.headers["content-disposition"]

    lines = response.text.strip().splitlines()
    assert lines[0] == "metric,value"
    assert "accuracy,0.94" in lines
    assert "macro_f1,0.91" in lines


def test_per_experiment_json_export_is_the_reproducibility_manifest(
    experiment_client: TestClient,
):
    stored = record_experiment(experiment_client).json()["experiment"]
    response = experiment_client.get(
        f"/api/experiments/{stored['experiment_id']}/export", params={"output_format": "json"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")

    exported = json.loads(response.text)
    manifest = experiment_client.get(
        f"/api/experiments/{stored['experiment_id']}/manifest"
    ).json()
    assert exported["content_hash"] == manifest["content_hash"]
    assert exported["record"] == manifest["record"]
    assert exported["experiment_id"] == stored["experiment_id"]


def test_ledger_export_carries_every_record_as_rows(experiment_client: TestClient):
    first = record_experiment(experiment_client, random_seed=1).json()["experiment"]
    second = record_experiment(experiment_client, model="gradient-boosting", random_seed=2).json()["experiment"]

    csv_response = experiment_client.get(
        "/api/experiments/export", params={"output_format": "csv"}
    )
    assert csv_response.status_code == 200
    assert "experiments-ledger.csv" in csv_response.headers["content-disposition"]
    lines = csv_response.text.strip().splitlines()
    assert lines[0].startswith("experiment_id,timestamp,dataset_version,dataset_hash")
    assert second["experiment_id"] in lines[1]
    assert first["experiment_id"] in lines[2]

    json_response = experiment_client.get(
        "/api/experiments/export", params={"output_format": "json"}
    )
    rows = json.loads(json_response.text)
    assert {row["experiment_id"] for row in rows} == {
        first["experiment_id"],
        second["experiment_id"],
    }
    assert {row["model"] for row in rows} == {"random-forest", "gradient-boosting"}


def test_ledger_export_honours_filters(experiment_client: TestClient):
    record_experiment(experiment_client, model="random-forest", random_seed=1)
    record_experiment(experiment_client, model="gradient-boosting", random_seed=2)

    response = experiment_client.get(
        "/api/experiments/export",
        params={"output_format": "json", "model": "random-forest"},
    )
    rows = json.loads(response.text)
    assert [row["model"] for row in rows] == ["random-forest"]


def test_exports_require_a_known_format(experiment_client: TestClient):
    response = experiment_client.get(
        "/api/experiments/export", params={"output_format": "xml"}
    )
    assert response.status_code == 422


def test_the_router_has_no_update_or_delete_routes(experiment_client: TestClient):
    from app.api.routers.experiments import router

    methods = {
        method
        for route in router.routes
        if getattr(route, "path", "").startswith("/api/experiments")
        for method in route.methods
    }
    assert not methods & {"PUT", "PATCH", "DELETE"}


def test_persistence_endpoints_return_503_without_a_database() -> None:
    """Without a database the ledger is refused, not reported as empty.

    This router hands the service ``get_db`` itself rather than what it returns,
    so the service has to reach the provider. Subscripting the provider instead
    of resolving it fails with a 500, which would report a server fault for what
    is a missing configuration.
    """
    with TestClient(app) as test_client:
        assert test_client.get("/api/experiments/").status_code == 503
        assert test_client.get("/api/experiments/exp-1").status_code == 503
        assert (
            test_client.post("/api/experiments/", json=experiment()).status_code
            == 503
        )


def test_the_ledger_status_survives_a_missing_database() -> None:
    """Reporting that the ledger is unreachable is this endpoint's whole job."""
    with TestClient(app) as test_client:
        response = test_client.get("/api/experiments/status")
    assert response.status_code == 200
    assert response.json()["experiments"]["available"] is False