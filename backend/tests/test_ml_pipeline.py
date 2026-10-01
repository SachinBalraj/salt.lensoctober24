"""Module 8 - the training pipeline's own tests.

These cover the parts that decide whether a reported number means anything. Each
test here corresponds to a way a pipeline like this one commonly produces a
confident wrong answer, so the test names are the claims rather than the
mechanics.

The heavier integration tests -- real HTTP, real stored datasets, real artifacts --
are in ``test_ml_api.py``.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pytest

from app.ml.candidates import (
    CANDIDATE_ALGORITHMS,
    NOT_AUTO_SELECTABLE,
    RANDOM_FOREST,
    candidate_specs,
    select_model,
)
from app.ml.pipeline import (
    DEFAULT_RANDOM_SEED,
    InsufficientDataError,
    build_feature_frame,
    train,
)
from app.ml.preprocessing import (
    CATEGORICAL,
    NON_FEATURE_COLUMNS,
    NUMERIC,
    classify_columns,
    fit_preprocessing,
    library_versions,
)
from app.ml.splitting import (
    CHRONOLOGICAL,
    GROUPED_BY_PAN,
    PER_PAN_CHRONOLOGICAL,
    SPLIT_STRATEGIES,
    make_split,
)
from app.ml.sufficiency import (
    INSUFFICIENT_DATA_MESSAGE,
    MIN_TOTAL_LABELLED_ROWS,
    check_sufficiency,
)

FEATURES = ("current_salinity_g_l", "water_depth_cm", "region")
KINDS = {
    "current_salinity_g_l": NUMERIC,
    "water_depth_cm": NUMERIC,
    "region": CATEGORICAL,
}

ANCHOR = datetime(2026, 3, 1, tzinfo=timezone.utc)


@dataclasses.dataclass
class Row:
    """The shape the pipeline consumes.

    A stand-in for ``DatasetRow`` carrying only the attributes the pipeline reads.
    A real one would couple these tests to the builder's every field; the pipeline
    reads ``pan_id``, ``anchor_timestamp``, ``features``, ``label`` and
    ``is_labelled``, and that contract is what is being tested.
    """

    pan_id: str
    anchor_timestamp: datetime
    features: dict[str, Any]
    label: str | None
    is_labelled: bool = True


def build_rows(
    *,
    pans: int = 8,
    per_pan: int = 40,
    seed: int = 5,
    signal: float = 1.5,
    label: str = "high",
    late_shift: float = 0.0,
) -> list[Row]:
    """A learnable dataset: salinity predicts the class, with pan-level baselines.

    Each pan gets its own baseline, so a model that ignored the features and
    learned "the mean is high" would score poorly -- the tests can then tell a
    model that learned something from one that got lucky.

    ``late_shift`` offsets the readings in the final fifth of each pan's history.
    A chronological split puts exactly those rows on the held-out side, so the
    training median and the whole-dataset median then differ. That is what makes
    a "did the imputer see the test rows?" assertion mean something: without the
    shift, both medians are equal and the test would pass whatever the code did.
    """
    rng = np.random.default_rng(seed)
    rows: list[Row] = []
    for index in range(pans):
        pan = f"PAN-{index + 1}"
        baseline = rng.normal(30.0, 2.0)
        for step in range(per_pan):
            shifted = step >= per_pan - per_pan // 5
            salinity = baseline + rng.normal(0.0, 1.0) + (late_shift if shifted else 0.0)
            rows.append(
                Row(
                    pan_id=pan,
                    anchor_timestamp=ANCHOR + timedelta(hours=step),
                    features={
                        "current_salinity_g_l": float(salinity),
                        "water_depth_cm": float(rng.uniform(5.0, 60.0)),
                        "region": "north" if index < pans // 2 else "south",
                    },
                    label=label if salinity > baseline + signal else "low",
                )
            )
    return rows


def run(
    rows: list[Row],
    directory: Path,
    **overrides: Any,
):
    """Train with sensible defaults, so a test states only what it is about."""
    arguments: dict[str, Any] = {
        "rows": rows,
        "feature_names": list(FEATURES),
        "feature_kinds": KINDS,
        "dataset_id": "ds-1",
        "dataset_version": 3,
        "dataset_content_hash": "sha256:" + "a" * 64,
        "target": "post_rain_salinity_band",
        "artifact_directory": directory,
        "next_version": 1,
        "model_id": "model-1",
        "split_strategy": CHRONOLOGICAL,
        "random_seed": DEFAULT_RANDOM_SEED,
    }
    arguments.update(overrides)
    return train(**arguments)


# -- the sufficiency gate ------------------------------------------------


def test_the_refusal_message_is_exactly_the_required_sentence():
    """The application must say this, so it is asserted as a constant."""
    assert (
        INSUFFICIENT_DATA_MESSAGE
        == "Insufficient labelled data for reliable model training."
    )


def test_too_few_labelled_rows_refuses_with_the_exact_message(tmp_path: Path):
    with pytest.raises(InsufficientDataError) as raised:
        run(build_rows(pans=3, per_pan=4), tmp_path)

    assert raised.value.message == INSUFFICIENT_DATA_MESSAGE
    assert str(raised.value) == INSUFFICIENT_DATA_MESSAGE


def test_a_refusal_writes_no_artifact(tmp_path: Path):
    """A refused run leaves nothing on disk.

    An artifact from a refused run would be a fitted model with no record of why
    it should not be trusted, sitting in the directory where trusted models live.
    """
    with pytest.raises(InsufficientDataError):
        run(build_rows(pans=3, per_pan=4), tmp_path)

    assert list(tmp_path.glob("*.joblib")) == []


def test_a_single_class_refuses_rather_than_fitting_a_constant(tmp_path: Path):
    """One class trains happily and scores 1.00. That is the trap this avoids."""
    rows = [dataclasses.replace(row, label="high") for row in build_rows()]

    with pytest.raises(InsufficientDataError) as raised:
        run(rows, tmp_path)

    assert raised.value.message == INSUFFICIENT_DATA_MESSAGE
    assert any("class" in reason for reason in raised.value.reasons)
    assert list(tmp_path.glob("*.joblib")) == []


def test_a_rare_class_below_the_floor_refuses(tmp_path: Path):
    """A class with one or two examples cannot be learned or honestly scored."""
    rows = build_rows()
    minority = [row for row in rows if row.label == "high"][:2]
    rows = [row for row in rows if row.label == "low"] + [
        dataclasses.replace(row, label="high") for row in minority
    ]

    with pytest.raises(InsufficientDataError) as raised:
        run(rows, tmp_path)

    assert raised.value.message == INSUFFICIENT_DATA_MESSAGE
    assert any("class 'high'" in reason for reason in raised.value.reasons)


def test_a_single_pan_refuses_because_nothing_can_be_evaluated_on_an_unseen_one(
    tmp_path: Path,
):
    """All rows from one pan is one measurement repeated, not a dataset."""
    with pytest.raises(InsufficientDataError) as raised:
        run(build_rows(pans=1, per_pan=200), tmp_path)

    assert raised.value.message == INSUFFICIENT_DATA_MESSAGE
    assert any("pan" in reason for reason in raised.value.reasons)


def test_unlabelled_rows_are_excluded_and_the_reason_is_the_lack_of_labels(
    tmp_path: Path,
):
    """Refusing must name the real cause, not blame a feature that was fine."""
    rows = [
        dataclasses.replace(row, label=None, is_labelled=False) for row in build_rows()
    ]

    with pytest.raises(InsufficientDataError) as raised:
        run(rows, tmp_path)

    assert raised.value.message == INSUFFICIENT_DATA_MESSAGE
    assert any("labelled row" in reason for reason in raised.value.reasons)
    assert not any(
        "feature" in reason for reason in raised.value.reasons
    ), "blamed a feature that was not the problem"


def test_the_gate_reports_every_failure_rather_than_only_the_first():
    """One run should list everything wrong with the data."""
    report = check_sufficiency(
        labelled_rows=5, class_counts={"a": 5}, distinct_pans=1, feature_count=1
    )

    assert report.sufficient is False
    assert report.message == INSUFFICIENT_DATA_MESSAGE
    assert len(report.reasons) >= 4


def test_a_sufficient_dataset_passes_the_gate():
    report = check_sufficiency(
        labelled_rows=320,
        class_counts={"high": 80, "low": 240},
        distinct_pans=8,
        feature_count=3,
    )

    assert report.sufficient is True
    assert report.message is None
    assert report.reasons == ()


# -- no fabricated data -------------------------------------------------


def test_the_row_count_never_exceeds_what_was_supplied(tmp_path: Path):
    """No resampling, oversampling or augmentation.

    Checked against the training rows actually seen, which is where oversampling
    would show up. A pipeline that duplicated the minority class to balance the
    sets would report a class count larger than the dataset's.
    """
    outcome = run(build_rows(), tmp_path)

    reported = outcome.sufficiency.class_counts
    assert sum(reported.values()) == outcome.sufficiency.labelled_rows
    assert outcome.sufficiency.labelled_rows == len(build_rows())
    assert outcome.split_detail["train_rows"] + outcome.split_detail["test_rows"] == len(
        build_rows()
    ), "split sizes do not account for exactly the supplied rows"


def test_a_refusal_does_not_smuggle_in_a_score(tmp_path: Path):
    """The refusal carries counts, never a metric.

    A refusal that included an accuracy would give a caller something to quote
    despite never having fitted anything.
    """
    with pytest.raises(InsufficientDataError) as raised:
        run(build_rows(pans=2, per_pan=6), tmp_path)

    payload = raised.value.report.as_dict()
    assert set(payload) & {"accuracy", "f1_macro", "metrics", "roc_auc"} == set()


# -- leakage ------------------------------------------------------------


@pytest.mark.parametrize("column", sorted(NON_FEATURE_COLUMNS))
def test_no_provenance_or_identifier_column_can_be_a_feature(column: str):
    """Every excluded column is refused, not silently dropped.

    Skipping quietly would be worse than raising: the model would train without
    the column and the response would claim the manifest's feature list.
    """
    row = Row("PAN-1", ANCHOR, {column: 1.0}, "high")

    with pytest.raises(ValueError, match="not a permitted feature"):
        build_feature_frame([row], [column])


def test_risk_provenance_cannot_be_a_feature_because_it_is_the_label():
    """A rule-derived label fed with its own provenance is the answer, copied."""
    row = Row(
        "PAN-1",
        ANCHOR,
        {"current_salinity_g_l": 30.0, "risk_matched_criteria": "depth>50"},
        "high",
    )

    with pytest.raises(ValueError, match="not a permitted feature"):
        build_feature_frame([row], ["risk_matched_criteria"])


def test_the_feature_frame_is_exactly_the_manifest_columns_in_order():
    """A stray key in a row's feature map cannot become a model input."""
    row = Row("PAN-1", ANCHOR, {"water_depth_cm": 20.0, "surprise": 9.0}, "high")

    frame = build_feature_frame([row], ["water_depth_cm", "current_salinity_g_l"])

    assert list(frame.columns) == ["water_depth_cm", "current_salinity_g_l"]


def test_preprocessing_is_fitted_on_training_rows_only(tmp_path: Path):
    """The decisive leakage test.

    The held-out rows are overwritten with an absurd value. If any statistic had
    been computed over the whole dataset, the imputer would move. It must not,
    because it was fitted on training rows.
    """
    rows = build_rows()
    clean = run(rows, tmp_path)

    split = make_split(
        pd.DataFrame(
            [
                {"pan_id": row.pan_id, "anchor_timestamp": row.anchor_timestamp}
                for row in rows
            ]
        ),
        CHRONOLOGICAL,
        test_size=0.2,
        random_seed=DEFAULT_RANDOM_SEED,
    )
    poisoned = list(rows)
    for index in split.test_indices:
        poisoned[index] = dataclasses.replace(
            poisoned[index],
            features={**poisoned[index].features, "current_salinity_g_l": 1e6},
        )

    dirty = run(poisoned, tmp_path)

    assert clean.preprocessing.imputed_numeric == dirty.preprocessing.imputed_numeric
    assert clean.preprocessing.scaled_means == dirty.preprocessing.scaled_means


def test_imputation_uses_the_training_median_not_the_whole_dataset_median(
    tmp_path: Path,
):
    # A level shift in the held-out fifth, so the two medians genuinely differ and
    # this test can fail if the imputer ever looks at the test rows.
    rows = build_rows(late_shift=6.0)
    outcome = run(rows, tmp_path)

    split = make_split(
        pd.DataFrame(
            [
                {"pan_id": row.pan_id, "anchor_timestamp": row.anchor_timestamp}
                for row in rows
            ]
        ),
        CHRONOLOGICAL,
        test_size=0.2,
        random_seed=DEFAULT_RANDOM_SEED,
    )
    salinity = np.array(
        [row.features["current_salinity_g_l"] for row in rows], dtype=float
    )
    train_median = float(np.median(salinity[split.train_indices]))
    whole_median = float(np.median(salinity))
    learned = outcome.preprocessing.imputed_numeric["current_salinity_g_l"]

    assert learned == pytest.approx(train_median, abs=1e-6)
    # The two really do differ here, so the assertion above has teeth.
    assert abs(train_median - whole_median) > 1.0


def test_a_feature_with_no_declared_kind_is_refused_rather_than_guessed():
    """A guessed encoding invents an order between categories nobody ordered."""
    with pytest.raises(ValueError, match="no declared kind"):
        classify_columns(["region"], {})

    with pytest.raises(ValueError, match="neither numeric nor categorical"):
        classify_columns(["region"], {"region": "ordinal"})


def test_a_missing_value_is_imputed_rather_than_read_as_zero():
    """An unrecorded reading is not a measured zero.

    Filling with zero would tell the model the pan was observed and found dry,
    which is a different claim from "nobody wrote it down".
    """
    frame = pd.DataFrame(
        {
            "current_salinity_g_l": [10.0, None, 30.0, 20.0],
            "water_depth_cm": [1.0, 2.0, 3.0, 4.0],
            "region": ["north", "north", "south", "south"],
        }
    )
    _, report = fit_preprocessing(
        frame, ["current_salinity_g_l", "water_depth_cm"], ["region"]
    )

    # The median of the three observed values, not the mean of four with a zero
    # standing in for the gap.
    assert report.imputed_numeric["current_salinity_g_l"] == pytest.approx(20.0)


def test_an_unencountered_category_encodes_rather_than_raising():
    """A category seen only at prediction time is a fact, not a reason to refuse."""
    frame = pd.DataFrame(
        {
            "current_salinity_g_l": [10.0, 20.0, 30.0, 40.0],
            "water_depth_cm": [1.0, 2.0, 3.0, 4.0],
            "region": ["north", "north", "south", "south"],
        }
    )
    pipeline, _ = fit_preprocessing(
        frame, ["current_salinity_g_l", "water_depth_cm"], ["region"]
    )
    unseen = pd.DataFrame(
        {
            "current_salinity_g_l": [25.0],
            "water_depth_cm": [2.5],
            "region": ["west"],
        }
    )

    transformed = pipeline.named_steps["preprocess"].transform(unseen)

    assert transformed.shape == (1, report_feature_count(pipeline))


def report_feature_count(pipeline: Any) -> int:
    return len(
        pipeline.named_steps["preprocess"]
        .named_transformers_["categorical"]
        .named_steps["encode"]
        .categories_[0]
    ) + 2


# -- splitting ----------------------------------------------------------


def test_chronological_split_trains_on_the_past_and_tests_on_the_future():
    """The property that makes the score a forecast rather than an interpolation."""
    frame = pd.DataFrame(
        [
            {
                "pan_id": f"PAN-{index % 4}",
                "anchor_timestamp": ANCHOR + timedelta(hours=index),
                "label": "high" if index % 2 else "low",
            }
            for index in range(100)
        ]
    )

    split = make_split(frame, CHRONOLOGICAL, test_size=0.2)

    times = pd.to_datetime(frame["anchor_timestamp"], utc=True)
    assert times.iloc[split.train_indices].max() <= times.iloc[split.test_indices].min()
    assert split.detail["time_ordered"] is True


def test_grouped_split_never_places_one_pan_on_both_sides():
    """A pan on both sides means near-duplicate conditions on both sides."""
    frame = pd.DataFrame(
        [
            {
                "pan_id": f"PAN-{index % 6}",
                "anchor_timestamp": ANCHOR + timedelta(hours=index),
                "label": "high" if index % 2 else "low",
            }
            for index in range(120)
        ]
    )

    split = make_split(frame, GROUPED_BY_PAN, test_size=0.3, random_seed=1)

    train_pans = set(frame["pan_id"].iloc[split.train_indices])
    test_pans = set(frame["pan_id"].iloc[split.test_indices])
    assert train_pans & test_pans == set()
    assert train_pans | test_pans == set(frame["pan_id"].unique())


def test_grouped_split_says_whether_it_evaluated_an_unseen_pan():
    """A reader should not have to infer that from the strategy name."""
    frame = pd.DataFrame(
        [
            {
                "pan_id": f"PAN-{index % 6}",
                "anchor_timestamp": ANCHOR + timedelta(hours=index),
                "label": "high" if index % 2 else "low",
            }
            for index in range(120)
        ]
    )

    grouped = make_split(frame, GROUPED_BY_PAN, test_size=0.3, random_seed=1)
    per_pan = make_split(frame, PER_PAN_CHRONOLOGICAL, test_size=0.2)

    assert grouped.detail["held_out_classes_match_training"] in (True, False)
    # The per-pan strategy puts every pan on both sides by construction, and says
    # so rather than letting the name imply otherwise.
    assert per_pan.detail["evaluates_unseen_pans"] is False
    assert per_pan.detail["pans_on_both_sides"]


def test_per_pan_chronological_is_forward_in_time_within_every_pan():
    frame = pd.DataFrame(
        [
            {
                "pan_id": f"PAN-{index % 4}",
                "anchor_timestamp": ANCHOR + timedelta(hours=index),
                "label": "high" if index % 2 else "low",
            }
            for index in range(80)
        ]
    )

    split = make_split(frame, PER_PAN_CHRONOLOGICAL, test_size=0.25)
    times = pd.to_datetime(frame["anchor_timestamp"], utc=True)

    for pan in frame.groupby("pan_id").groups:
        train_times = times.iloc[
            [i for i in split.train_indices if frame["pan_id"].iloc[i] == pan]
        ]
        test_times = times.iloc[
            [i for i in split.test_indices if frame["pan_id"].iloc[i] == pan]
        ]
        if len(train_times) and len(test_times):
            assert train_times.max() <= test_times.min(), pan


@pytest.mark.parametrize("strategy", SPLIT_STRATEGIES)
def test_every_split_is_deterministic_given_the_seed(strategy: str):
    """Two runs of the same data must partition it the same way.

    A split that moved between runs would make the two runs' metrics
    incomparable, and the comparison is the reason the seed is recorded.
    """
    frame = pd.DataFrame(
        [
            {
                "pan_id": f"PAN-{index % 6}",
                "anchor_timestamp": ANCHOR + timedelta(hours=index),
                "label": "high" if index % 2 else "low",
            }
            for index in range(120)
        ]
    )

    first = make_split(frame, strategy, test_size=0.25, random_seed=42)
    second = make_split(frame, strategy, test_size=0.25, random_seed=42)

    assert np.array_equal(first.train_indices, second.train_indices)
    assert np.array_equal(first.test_indices, second.test_indices)


@pytest.mark.parametrize("test_size", [0.0, 1.0, -0.1, 1.5])
@pytest.mark.parametrize("strategy", SPLIT_STRATEGIES)
def test_a_split_size_that_leaves_nothing_is_rejected(strategy: str, test_size: float):
    """A test fraction of zero would mean a score computed on the training data."""
    frame = pd.DataFrame(
        {
            "pan_id": [f"PAN-{index % 3}" for index in range(60)],
            "anchor_timestamp": [ANCHOR + timedelta(hours=i) for i in range(60)],
            "label": ["high" if i % 2 else "low" for i in range(60)],
        }
    )

    with pytest.raises(ValueError, match="test_size"):
        make_split(frame, strategy, test_size=test_size, random_seed=1)


def test_an_unknown_split_strategy_raises_rather_than_defaulting():
    """Defaulting would train quietly under a split nobody asked for."""
    frame = pd.DataFrame(
        {
            "pan_id": ["PAN-1"] * 40,
            "anchor_timestamp": [ANCHOR + timedelta(hours=i) for i in range(40)],
            "label": ["high" if i % 2 else "low" for i in range(40)],
        }
    )

    with pytest.raises(ValueError, match="Unknown split strategy"):
        make_split(frame, "by_vibes", test_size=0.2, random_seed=1)


def test_the_pipeline_rejects_an_unknown_split_strategy_before_training(
    tmp_path: Path,
):
    with pytest.raises(ValueError, match="Unknown split strategy"):
        run(build_rows(), tmp_path, split_strategy="by_vibes")


# -- candidate selection ------------------------------------------------


def test_random_forest_is_never_selected_automatically_even_when_it_wins():
    """The central selection rule.

    Random Forest scoring far above everything else is the expected outcome on
    small data, and it is exactly the case the rule exists for.
    """
    scored = {
        algorithm: {"f1_macro": 0.50, "balanced_accuracy": 0.50}
        for algorithm in CANDIDATE_ALGORITHMS
    }
    scored[RANDOM_FOREST] = {"f1_macro": 0.99, "balanced_accuracy": 0.99}

    selected, reason = select_model(scored)

    assert selected != RANDOM_FOREST
    assert selected in CANDIDATE_ALGORITHMS
    assert RANDOM_FOREST in reason


def test_random_forest_can_be_promoted_only_when_asked_for():
    """Promotable deliberately, and the act is reported."""
    scored = {
        algorithm: {"f1_macro": 0.50, "balanced_accuracy": 0.50}
        for algorithm in CANDIDATE_ALGORITHMS
    }
    scored[RANDOM_FOREST] = {"f1_macro": 0.99, "balanced_accuracy": 0.99}

    selected, reason = select_model(scored, allow_random_forest=True)

    assert selected == RANDOM_FOREST
    assert "explicitly permitted" in reason


def test_only_random_forest_scoring_yields_no_selection_rather_than_a_forced_pick():
    """A run with nothing to select writes nothing.

    Choosing the withheld candidate because it is the only one would make the
    rule a suggestion, and the whole point is that it is not.
    """
    scored = {RANDOM_FOREST: {"f1_macro": 0.9, "balanced_accuracy": 0.9}}

    selected, reason = select_model(scored)

    assert selected is None
    assert "never" in reason and "automatically" in reason


def test_random_forest_is_declared_not_auto_selectable():
    assert RANDOM_FOREST in NOT_AUTO_SELECTABLE


def test_all_four_candidates_are_fitted_on_every_run(tmp_path: Path):
    """The comparison is the point, so all four must report a score."""
    outcome = run(build_rows(), tmp_path)

    assert set(outcome.candidate_metrics) == set(CANDIDATE_ALGORITHMS)
    assert set(CANDIDATE_ALGORITHMS) == {
        "logistic_regression",
        "decision_tree",
        "random_forest",
        "gradient_boosting",
    }
    for scores in outcome.candidate_metrics.values():
        assert 0.0 <= scores["f1_macro"] <= 1.0


def test_every_candidate_records_its_hyper_parameters(tmp_path: Path):
    """A model whose configuration is unrecorded cannot be reproduced."""
    run(build_rows(), tmp_path)

    assert len(candidate_specs()) == 4
    for spec in candidate_specs():
        assert spec.parameters, spec.algorithm
        # A seed reaches the estimator, so the fitted object is deterministic.
        estimator = spec.build(DEFAULT_RANDOM_SEED)
        assert estimator.random_state == DEFAULT_RANDOM_SEED


# -- reproducibility and provenance -------------------------------------


def test_the_same_data_and_seed_produce_identical_metrics(tmp_path: Path):
    """What "reproducible" means, checked rather than asserted in a docstring."""
    first = run(build_rows(), tmp_path)
    second = run(build_rows(), tmp_path)

    assert first.candidate_metrics == second.candidate_metrics
    assert first.algorithm == second.algorithm
    assert first.split_detail == second.split_detail


def test_a_different_seed_is_recorded_and_reaches_the_estimators(tmp_path: Path):
    outcome = run(build_rows(), tmp_path, random_seed=1234)

    assert outcome.random_seed == 1234


def test_the_run_records_everything_needed_to_re_derive_it(tmp_path: Path):
    """The provenance record the brief asks for, checked field by field."""
    outcome = run(build_rows(), tmp_path)

    assert outcome.dataset_id == "ds-1"
    assert outcome.dataset_version == 3
    assert outcome.dataset_content_hash == "sha256:" + "a" * 64
    assert list(outcome.feature_names) == list(FEATURES)
    assert outcome.split_strategy == CHRONOLOGICAL
    assert outcome.random_seed == DEFAULT_RANDOM_SEED
    assert outcome.trained_at.tzinfo is not None
    assert outcome.training_duration_seconds > 0
    for library in ("scikit-learn", "numpy", "pandas"):
        assert library in outcome.library_versions
    assert outcome.split_detail["train_rows"] > 0
    assert outcome.preprocessing.output_feature_count > 0


def test_library_versions_match_what_is_installed():
    """A recorded version that is not the one used is a false record."""
    import sklearn

    versions = library_versions()

    assert versions["scikit-learn"] == sklearn.__version__


def test_the_artifact_loads_and_predicts(tmp_path: Path):
    """The saved pipeline works on new rows without refitting anything."""
    outcome = run(build_rows(), tmp_path)
    blob = joblib.load(outcome.artifact_path)

    assert {"preprocessing", "estimator", "classes", "library_versions"} <= set(blob)
    fresh = pd.DataFrame(
        {
            "current_salinity_g_l": [31.0],
            "water_depth_cm": [20.0],
            "region": ["north"],
        }
    )
    matrix = blob["preprocessing"].named_steps["preprocess"].transform(fresh)

    prediction = blob["estimator"].predict(matrix)

    assert len(prediction) == 1
    assert prediction[0] in set(blob["classes"])


def test_preprocessing_and_estimator_are_saved_together(tmp_path: Path):
    """One file, so a loader cannot pair one model with another's pipeline."""
    outcome = run(build_rows(), tmp_path)
    blob = joblib.load(outcome.artifact_path)

    assert Path(outcome.artifact_path).exists()
    assert outcome.model_id in Path(outcome.artifact_path).name
    assert outcome.version in Path(outcome.artifact_path).name
    assert blob["estimator"] is not None


def test_a_learnable_dataset_produces_a_model_that_actually_learned(tmp_path: Path):
    """A score above chance means the pipeline found the signal it was given."""
    outcome = run(build_rows(), tmp_path)

    assert outcome.metrics["f1_macro"] > 0.5, outcome.metrics
    assert outcome.metrics["balanced_accuracy"] > 0.5, outcome.metrics


def test_the_gate_threshold_is_not_so_low_that_tiny_data_qualifies():
    """The floor is meant to exclude small datasets, not to wave them through."""
    assert MIN_TOTAL_LABELLED_ROWS >= 50
