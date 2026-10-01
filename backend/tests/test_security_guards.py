"""The security guards added across the API, tested in isolation.

Each guard is a small, dependency-free primitive: the artifact-path resolver,
the CSV prose-cell elider, and the schema-level refusal of non-finite numbers.
Testing them directly keeps the threat-model claims pinned to the code that
makes them, rather than trusting an end-to-end test to notice an escape.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.dataset import DatasetTargetRequest
from app.schemas.experiments import ExperimentCreate
from app.services.artifact_paths import ArtifactPathError, resolve_artifact_path
from app.services.csv_security import sanitize_prose_cell


def test_a_relative_artifact_path_resolves_inside_the_directory() -> None:
    base = Path("/tmp/model-store")
    assert resolve_artifact_path(base, "model-x_1.0.joblib") == (
        base.resolve() / "model-x_1.0.joblib"
    )


def test_an_absolute_path_inside_the_directory_is_allowed() -> None:
    base = Path("/tmp/model-store")
    assert resolve_artifact_path(base, f"{base}/model-x_1.0.joblib") == (
        base.resolve() / "model-x_1.0.joblib"
    )


@pytest.mark.parametrize(
    "escape",
    [
        "../other/model.joblib",
        "/etc/passwd",
        "..",
        "nested/../../leak.joblib",
    ],
)
def test_an_artifact_path_that_escapes_is_refused(escape: str) -> None:
    base = Path("/tmp/model-store")
    with pytest.raises(ArtifactPathError):
        resolve_artifact_path(base, escape)


def test_the_directory_itself_is_accepted_even_though_it_is_unloadable() -> None:
    base = Path("/tmp/model-store")
    assert resolve_artifact_path(base, ".") == base.resolve()


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a note to self", "a note to self"),
        ("-3.5", "'-3.5"),
        ("=SUM(A1:A9)", "'=SUM(A1:A9)"),
        ("+cmd", "'+cmd"),
        ("@import", "'@import"),
        ("\tformula", "'\tformula"),
        ("", ""),
        (None, ""),
    ],
)
def test_prose_cells_are_elided_only_when_a_spreadsheet_would_parse_them(
    raw: str | None, expected: str
) -> None:
    assert sanitize_prose_cell(raw) == expected


def _experiment(metrics: dict, hyperparameters: dict) -> ExperimentCreate:
    return ExperimentCreate(
        dataset_version=1,
        dataset_hash="sha256:" + "a" * 64,
        n_observations=120,
        n_pans=4,
        features=["current_salinity_g_l"],
        target="post_rain_salinity_g_l",
        target_definition_version=2,
        train_test_strategy="80/20 chronological holdout",
        random_seed=7,
        model="random-forest",
        hyperparameters=hyperparameters,
        metrics=metrics,
        software_versions={"scikit-learn": "1.4.0"},
    )


def test_experiment_metrics_reject_non_finite_values() -> None:
    with pytest.raises(ValidationError, match="finite_number"):
        _experiment({"accuracy": float("nan")}, {"n_estimators": 100})


def test_experiment_hyperparameters_reject_non_finite_values() -> None:
    with pytest.raises(ValidationError, match="must be a finite number"):
        _experiment({"accuracy": 0.5}, {"max_leaf_nodes": float("inf")})


def test_finite_metrics_and_hyperparameters_are_accepted() -> None:
    experiment = _experiment({"accuracy": -0.5}, {"n_estimators": 100})
    assert experiment.metrics == {"accuracy": -0.5}


def test_dataset_bin_edges_reject_a_non_finite_boundary() -> None:
    with pytest.raises(ValidationError):
        DatasetTargetRequest(
            name="post_rain_salinity_g_l",
            horizon_hours=24,
            bin_edges=[200.0, float("nan"), 300.0],
        )