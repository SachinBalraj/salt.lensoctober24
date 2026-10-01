"""Module 8 - the training run, end to end.

Order of operations is the design. It is deliberate, and changing it changes what
the reported numbers mean.

1. **Materialise the dataset.** Re-derive the rows from the manifest and confirm
   the content hash still matches. If the records have changed since the dataset
   was built, the run stops: an experiment citing a version that no longer
   describes its rows is not reproducible, whatever else is true of it.

2. **Build the feature frame, dropping what must never be a feature.** Identifiers,
   the anchor instant, the target measurement, and every ``risk_*`` provenance
   column. The last is the sharp one -- those columns record which criteria fired
   and assigned which class, so a rule-derived label fed alongside its own
   provenance hands the model the answer.

3. **Check sufficiency, and refuse if it fails.** Before any estimator is built and
   before any statistic is computed from the data. A dataset too small to support
   a model leaves nothing behind: no artifact, no metric, no registered version.
   Just the message.

4. **Split.** Chronologically by default, so a past observation informs a future
   one and not the reverse.

5. **Fit preprocessing on the training rows only.** The single most consequential
   line in this file. A median, a category list, or a scaler mean computed over
   train and test together is leakage, and no metric will reveal it.

6. **Fit each candidate, score each on the held-out rows, select.** All four are
   fitted. Random Forest is never selected automatically.

7. **Persist and register**, recording enough to re-derive the run.

The invariant this module exists to hold: **no data is ever synthesised to make a
model possible.** If the data will not support one, the run refuses. There is no
resampling, no augmentation, no fallback baseline, and no "provisional" score
computed on a subset while a bigger dataset is assembled.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.ml.candidates import (
    CANDIDATE_ALGORITHMS,
    NOT_AUTO_SELECTABLE,
    RANDOM_FOREST,
    candidate_specs,
    evaluate,
    select_model,
)
from app.ml.preprocessing import (
    NON_FEATURE_COLUMNS,
    PreprocessingReport,
    apply_preprocessing,
    classify_columns,
    fit_preprocessing,
    library_versions,
)
from app.ml.splitting import (
    DEFAULT_SPLIT_STRATEGY,
    GROUPED_BY_PAN,
    SPLIT_STRATEGIES,
    make_split,
)
from app.ml.sufficiency import (
    INSUFFICIENT_DATA_MESSAGE,
    SufficiencyReport,
    check_sufficiency,
    class_counts,
)
from app.models.base import utcnow

#: The seed used when a caller does not supply one. Fixed so that two runs of the
#: same dataset produce the same models, which is the only way the reproducibility
#: record can be checked rather than asserted.
DEFAULT_RANDOM_SEED = 20260926

#: Fraction of rows held out. Applied as a fraction of the dataset, so the
#: proportion kept for measuring does not drift as the dataset grows.
DEFAULT_TEST_SIZE = 0.2


class InsufficientDataError(RuntimeError):
    """The data cannot support a model, and none will be invented.

    Carries the sufficiency report so the caller can say *why* it was refused
    rather than only that it was. The message is the exact required sentence; the
    reasons are for the console and the log.
    """

    def __init__(self, report: SufficiencyReport) -> None:
        super().__init__(report.message or INSUFFICIENT_DATA_MESSAGE)
        self.message = report.message or INSUFFICIENT_DATA_MESSAGE
        self.report = report

    @property
    def reasons(self) -> tuple[str, ...]:
        return self.report.reasons


@dataclass(frozen=True)
class TrainingOutcome:
    """Everything one run produced, in the shape the API and the log both want."""

    model_id: str
    version: str
    target: str
    algorithm: str
    status: str
    artifact_path: str
    metrics: dict[str, float]
    candidate_metrics: dict[str, dict[str, float]]
    selection_reason: str
    random_seed: int
    split_strategy: str
    split_detail: dict[str, Any]
    sufficiency: SufficiencyReport
    preprocessing: PreprocessingReport
    feature_names: list[str]
    library_versions: dict[str, str]
    dataset_id: str
    dataset_version: int
    dataset_content_hash: str
    trained_at: datetime
    training_duration_seconds: float
    allow_random_forest: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "version": self.version,
            "target": self.target,
            "algorithm": self.algorithm,
            "status": self.status,
            "artifact_path": self.artifact_path,
            "metrics": dict(self.metrics),
            "candidate_metrics": {
                algorithm: dict(scores)
                for algorithm, scores in self.candidate_metrics.items()
            },
            "selection_reason": self.selection_reason,
            "random_seed": self.random_seed,
            "split_strategy": self.split_strategy,
            "split_detail": dict(self.split_detail),
            "sufficiency": self.sufficiency.as_dict(),
            "preprocessing": self.preprocessing.as_dict(),
            "feature_names": list(self.feature_names),
            "library_versions": dict(self.library_versions),
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "dataset_content_hash": self.dataset_content_hash,
            "trained_at": self.trained_at.isoformat(),
            "training_duration_seconds": self.training_duration_seconds,
            "allow_random_forest": self.allow_random_forest,
        }


def build_feature_frame(
    rows: Sequence[Any],
    feature_names: Sequence[str],
) -> pd.DataFrame:
    """Turn dataset rows into a feature frame, dropping everything not a feature.

    The exclusion list is applied here rather than trusted to the caller, because
    the failure it prevents is silent: a model handed an identifier or a risk
    provenance column trains successfully and reports an excellent score, and
    nothing in the output says the score is meaningless.

    Columns that are not in the frame because every value was missing are still
    present, as absent entries for the imputer to fill from the training rows.
    Dropping them silently here would remove a feature the manifest declared, and
    the record of what was fitted would no longer match the dataset.
    """
    if not rows:
        raise ValueError(
            "No rows to build a feature frame from. An empty frame would fit a "
            "model with nothing to learn from."
        )
    selected: list[str] = []
    for name in feature_names:
        if name in NON_FEATURE_COLUMNS:
            # Refuse rather than skip: a caller that asked for one of these has a
            # bug, and quietly training without it would hide that.
            raise ValueError(
                f"Column {name!r} is not a permitted feature. Identifiers, the "
                "anchor instant, the target measurement, and risk provenance "
                "columns are excluded because feeding a model any of them leaks "
                "information it could not have at prediction time."
            )
        selected.append(name)

    records: list[dict[str, Any]] = []
    for row in rows:
        features = getattr(row, "features", None)
        if features is None:
            features = {}
        record = dict(features)
        for column in selected:
            record.setdefault(column, None)
        records.append(record)

    frame = pd.DataFrame.from_records(records)
    missing = [name for name in selected if name not in frame.columns]
    if missing:
        raise ValueError(
            f"Feature(s) {missing} are declared in the manifest but absent from "
            "the built rows. The dataset and its manifest disagree, so the "
            "dataset should be rebuilt before any model is fitted to it."
        )
    # Exactly the selected features, in manifest order. Reindexing rather than
    # trusting the row dicts, so a stray key in a feature map cannot become a
    # column the manifest never declared.
    return frame.reindex(columns=selected)


def label_frame(rows: Sequence[Any]) -> tuple[pd.DataFrame, np.ndarray]:
    """Assemble the labelled subset, returning an empty frame if there are none.

    Unlabelled rows are excluded rather than imputed. A row with no label has
    nothing to learn from, and including it would mean inventing one.

    An empty result is returned rather than raised, so that the sufficiency gate
    is the single place that decides a dataset is unusable. It can then report
    the real cause -- no labelled rows -- instead of this function guessing at a
    reason on its behalf.
    """
    kept: list[int] = []
    labels: list[str] = []
    meta: list[dict[str, Any]] = []
    for position, row in enumerate(rows):
        if not getattr(row, "is_labelled", False):
            continue
        label = getattr(row, "label", None)
        if label is None:
            continue
        kept.append(position)
        labels.append(str(label))
        meta.append(
            {
                "pan_id": str(getattr(row, "pan_id", "")),
                "anchor_timestamp": getattr(row, "anchor_timestamp", None),
            }
        )
    indices = np.asarray(kept, dtype=int)
    # Columns named explicitly so an empty result still has the right shape and
    # the gate downstream can count pans instead of hitting a missing column.
    frame = pd.DataFrame(
        {
            "pan_id": [entry["pan_id"] for entry in meta],
            "anchor_timestamp": [entry["anchor_timestamp"] for entry in meta],
        },
        index=indices,
    )
    return frame, np.asarray(labels, dtype=object)


#: How many rows of each split travel in the artifact for Module 11.
#:
#: A Shapley value is defined against a reference distribution: "this feature moved
#: the output from what the model would say about a typical row to what it says
#: about this one". Without a reference sample, an explainer falls back to one
#: built from the model tree's own structure, or to an average it invents, and the
#: attribution then depends on an artefact of the implementation rather than on the
#: data the model was fitted on.
#:
#: Both splits are carried because they answer different questions. The training
#: rows are the reference distribution the contributions are measured against; the
#: held-out rows are what the global summary is computed over, so that Module 11
#: reports importance on rows the model's metrics were scored on rather than on
#: rows it memorised. 100 rows each keeps the artifact small -- a fitted model
#: dwarfs this -- while being far more than a Shapley sum needs for the reference
#: value to settle.
SHAP_REFERENCE_ROWS = 100


def shap_reference(
    train_matrix: Any, test_matrix: Any, *, rows: int = SHAP_REFERENCE_ROWS
) -> dict[str, Any]:
    """The reference samples Module 11 needs, taken from the split they belong to.

    A plain head-of-array slice rather than a random draw, for two reasons. It is
    deterministic, so the same artifact always explains identically; and the
    training rows are already ordered by anchor time (the splits are chronological
    where the data allows), so the sample spans the season instead of clustering
    around one week of it. The row counts travel with the arrays so an explainer
    can state how much of each split it is standing in for.
    """
    return {
        "background": np.asarray(train_matrix[:rows], dtype=np.float64),
        "holdout": np.asarray(test_matrix[:rows], dtype=np.float64),
        "train_rows": len(train_matrix),
        "holdout_rows": len(test_matrix),
        "sampled_rows": int(min(rows, len(train_matrix), len(test_matrix))),
        "limit": int(rows),
        "selection": "first_rows_of_each_split_in_split_order",
    }


def _persist(
    artifact_directory: Path,
    model_id: str,
    version: str,
    preprocessing_pipeline: Any,
    estimator: Any,
    classes: np.ndarray,
    reference: dict[str, Any] | None = None,
) -> Path:
    """Write the fitted pipeline and estimator, then return the path.

    Both objects go into one joblib file. Keeping them together is what makes the
    artifact self-sufficient: a loader cannot accidentally apply a preprocessing
    pipeline fitted against one dataset to a model fitted against another, which
    is a mismatch that would produce predictions with no error anywhere.

    ``reference`` carries the Module 11 reference samples and is written under
    ``shap_reference``. It is optional so that an artifact written before this key
    existed still loads; such an artifact has no reference distribution, and
    Module 11 reports explainability unavailable for it rather than inventing one.
    """
    artifact_directory.mkdir(parents=True, exist_ok=True)
    path = artifact_directory / f"{model_id}_{version}.joblib"
    payload: dict[str, Any] = {
        "preprocessing": preprocessing_pipeline,
        "estimator": estimator,
        "classes": classes,
        "library_versions": library_versions(),
    }
    if reference is not None:
        payload["shap_reference"] = reference
    joblib.dump(payload, path)
    return path


def train(
    *,
    rows: Sequence[Any],
    feature_names: Sequence[str],
    feature_kinds: dict[str, str],
    dataset_id: str,
    dataset_version: int,
    dataset_content_hash: str,
    target: str,
    artifact_directory: Path,
    next_version: int,
    model_id: str,
    split_strategy: str = DEFAULT_SPLIT_STRATEGY,
    test_size: float = DEFAULT_TEST_SIZE,
    random_seed: int = DEFAULT_RANDOM_SEED,
    allow_random_forest: bool = False,
) -> TrainingOutcome:
    """Fit and evaluate the candidates, then return the selected model.

    Raises ``InsufficientDataError`` if the data cannot support a model. That is
    the designed outcome for small or unrepresentative datasets, not a failure
    mode to be worked around.
    """
    if split_strategy not in SPLIT_STRATEGIES:
        raise ValueError(
            f"Unknown split strategy {split_strategy!r}. Use one of "
            f"{', '.join(SPLIT_STRATEGIES)}."
        )
    if not feature_kinds:
        raise ValueError(
            "No feature kinds were supplied, so the pipeline cannot decide between "
            "numeric and categorical. Kinds come from the feature catalogue and "
            "are not inferred from values."
        )
    # Partition the requested features by declared kind. Raises on an undeclared
    # one rather than guessing, since a guessed encoding invents relationships
    # (an order between categories) that nobody stated.
    numeric_columns, categorical_columns = classify_columns(feature_names, feature_kinds)
    usable_feature_count = len(numeric_columns) + len(categorical_columns)

    # -- the feature frame, and the refusal check ------------------------
    frame = build_feature_frame(rows, feature_names)
    meta, labels = label_frame(rows)
    labelled = frame.loc[meta.index].reset_index(drop=True)
    meta = meta.reset_index(drop=True)

    counts = class_counts(labels)
    distinct_pans = int(meta["pan_id"].nunique()) if "pan_id" in meta else 0

    # The dataset-level gate runs first, before a split is even attempted: a
    # dataset with three labelled rows cannot be split into two usable halves,
    # and the error should be about the data rather than about the split failing.
    sufficiency = check_sufficiency(
        labelled_rows=len(labels),
        class_counts=counts,
        distinct_pans=distinct_pans,
        feature_count=usable_feature_count,
        total_rows=len(rows),
    )
    if not sufficiency.sufficient:
        raise InsufficientDataError(sufficiency)

    # -- the split -------------------------------------------------------
    # Built from the labelled frame so the indices line up with `labelled`, and
    # with `label` present because the stratified strategies read it.
    split_source = meta.copy()
    split_source["label"] = [str(value) for value in labels]
    split = make_split(
        split_source,
        split_strategy,
        test_size=test_size,
        random_seed=random_seed,
    )
    train_index = np.asarray(split.train_indices)
    test_index = np.asarray(split.test_indices)

    # Re-check sufficiency *per split*. A dataset can clear the dataset-level bar
    # and still leave a held-out set with one example of the rare class, where
    # every metric is a description of that one row. Better to refuse than to
    # publish it.
    split_sufficiency = check_sufficiency(
        labelled_rows=len(labels),
        class_counts=counts,
        distinct_pans=distinct_pans,
        feature_count=usable_feature_count,
        total_rows=len(rows),
        split_row_counts={
            "train": len(train_index),
            "test": len(test_index),
        },
        split_class_counts={
            "train": class_counts(labels[train_index]),
            "test": class_counts(labels[test_index]),
        },
    )
    if not split_sufficiency.sufficient:
        # Attributed to the split, not the dataset, because a different strategy
        # may not have this problem and the reader should be able to tell that.
        reasons = tuple(
            f"[{split_strategy}] {reason}" for reason in split_sufficiency.reasons
        )
        raise InsufficientDataError(
            SufficiencyReport(
                sufficient=False,
                message=split_sufficiency.message,
                reasons=reasons,
                labelled_rows=split_sufficiency.labelled_rows,
                total_rows=split_sufficiency.total_rows,
                class_counts=split_sufficiency.class_counts,
                distinct_pans=split_sufficiency.distinct_pans,
                feature_count=split_sufficiency.feature_count,
                split_row_counts=split_sufficiency.split_row_counts,
                split_class_counts=split_sufficiency.split_class_counts,
            )
        )

    # -- preprocessing, fitted on the training rows only -----------------
    # The critical line. Everything fitted below -- medians, category lists,
    # scaler means -- is learned from `train_index` and never sees a held-out
    # row. Passing the whole frame here would leak the test set into training
    # while every metric still looked normal.
    train_frame = labelled.iloc[train_index]
    test_frame = labelled.iloc[test_index]
    preprocessing_pipeline, preprocessing_report = fit_preprocessing(
        train_frame, numeric_columns, categorical_columns
    )
    train_matrix = apply_preprocessing(preprocessing_pipeline, train_frame)
    test_matrix = apply_preprocessing(preprocessing_pipeline, test_frame)

    train_labels = labels[train_index]
    test_labels = labels[test_index]
    # Classes come from the training labels alone. Taking them from the full set
    # would let a class that appears only in the held-out rows become a column
    # the model has no weights for.
    classes = np.unique(np.asarray(train_labels, dtype=object))

    # -- fit, score, select ----------------------------------------------
    started = time.perf_counter()
    candidate_metrics: dict[str, dict[str, float]] = {}
    fitted: dict[str, Any] = {}
    for spec in candidate_specs():
        estimator = spec.build(random_seed)
        estimator.fit(train_matrix, train_labels)
        fitted[spec.algorithm] = estimator
        candidate_metrics[spec.algorithm] = evaluate(
            estimator, test_matrix, test_labels, classes
        )
    duration = time.perf_counter() - started

    selected, reason = select_model(
        candidate_metrics, allow_random_forest=allow_random_forest
    )
    if selected is None:
        # Only Random Forest scored, and it is not auto-selectable. Nothing is
        # persisted: a model nobody selected is not a model this run produced.
        raise InsufficientDataError(
            SufficiencyReport(
                sufficient=False,
                message=INSUFFICIENT_DATA_MESSAGE,
                reasons=(
                    reason,
                    (
                        "No model was selected, so no artifact was written. "
                        "Refusing here is preferable to registering a model the run "
                        "did not choose."
                    ),
                ),
                labelled_rows=len(labels),
                total_rows=len(rows),
                class_counts=counts,
                distinct_pans=distinct_pans,
                feature_count=usable_feature_count,
                split_row_counts={
                    "train": len(train_index),
                    "test": len(test_index),
                },
            )
        )

    version = f"v{next_version}.0.0"
    path = _persist(
        artifact_directory,
        model_id,
        version,
        preprocessing_pipeline,
        fitted[selected],
        classes,
        shap_reference(train_matrix, test_matrix),
    )

    return TrainingOutcome(
        model_id=model_id,
        version=version,
        target=target,
        algorithm=selected,
        # "candidate", not "promoted". A held-out score is not a deployment
        # decision, and a registry that says otherwise invites the belief that
        # it is.
        status="candidate",
        artifact_path=str(path),
        metrics=dict(candidate_metrics[selected]),
        candidate_metrics=candidate_metrics,
        selection_reason=reason,
        random_seed=random_seed,
        split_strategy=split.strategy,
        split_detail=split.as_dict(),
        sufficiency=sufficiency,
        preprocessing=preprocessing_report,
        feature_names=list(feature_names),
        library_versions=library_versions(),
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_content_hash=dataset_content_hash,
        trained_at=utcnow(),
        training_duration_seconds=duration,
        allow_random_forest=allow_random_forest,
    )


__all__ = [
    "CANDIDATE_ALGORITHMS",
    "DEFAULT_RANDOM_SEED",
    "DEFAULT_SPLIT_STRATEGY",
    "DEFAULT_TEST_SIZE",
    "GROUPED_BY_PAN",
    "INSUFFICIENT_DATA_MESSAGE",
    "NOT_AUTO_SELECTABLE",
    "RANDOM_FOREST",
    "SHAP_REFERENCE_ROWS",
    "InsufficientDataError",
    "TrainingOutcome",
    "build_feature_frame",
    "shap_reference",
    "train",
]
