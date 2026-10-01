"""Module 09 - the HTTP contract for evaluation.

The contract's job is to make it impossible for a client to read a number the system
did not compute. Three decisions follow from that, and all three are about what
``null`` means.

**A metric is a reading, not a number.** Every figure in every response is the
:class:`MetricReading` shape -- ``value``, ``computed``, ``variant``, ``reason`` --
never a bare float and never a nullable float. A nullable float is the failure this
contract is written to prevent: a client that wants a chart series gets ``null``,
converts it to ``0`` because a chart library will not take a hole, and draws a bar
at zero for a metric that was never calculated. The reading makes ``value: null``
mean *not calculated*, with a reason attached, and a client that respects
``computed`` cannot get this wrong. :mod:`app.models.evaluation` enforces the same
invariant on the way in, so a hand-written request body cannot produce a reading
claiming a number it does not have.

**A refusal is a status code and a code, never an empty report.** A drifted dataset
or a missing artifact returns 409/422 with an
:class:`app.models.evaluation.EvaluationRefusal` body: a fixed ``detail`` sentence, a
machine-readable ``refusal`` code, and the counts that caused it. Returning 200 with
an evaluation whose every reading is absent would be indistinguishable from a model
that scored nothing on anything, which is a real result and a different thing.

**The comparison table carries its own criterion and its own comparability verdict.**
The response says which rule selected the selected row, and it says whether the rows
share a denominator. A table that ranks numbers without either of those is a
decorative table, and the two facts are the difference between a reader checking a
selection and a reader believing a label.

The download endpoints serve the same readings as the same text, JSON or CSV. There
is no second formatting of the numbers for the export: an export that rounded, or
filled a gap, or reordered a column would be a second set of claims about the same
run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.ml.comparison import (
    COLUMN_TITLES,
    COMPARISON_COLUMNS,
    COMPARISON_DOES_NOT_DO,
    SELECTION_CRITERION,
)
from app.ml.cross_validation import MAX_SPLITS, MIN_SPLITS
from app.models.evaluation import (
    AlgorithmCrossValidation,
    ClassificationEvaluation,
    CrossValidationFold,
    CrossValidationReport,
    EvaluationRefusal,
    MetricAggregate,
)
from app.models.model_evaluation import ModelEvaluation

#: The export formats offered. JSON is the complete record; CSV is the comparison
#: table flattened, with a reason column beside every metric so an empty cell is
#: interpretable. Both are built from the same readings the API serves.
ExportFormat = Literal["json", "csv"]


class RunEvaluationRequestBody(BaseModel):
    """Which model to evaluate, and how many folds to ask for.

    ``extra="forbid"`` for the same reason Module 08's does: a misspelled
    ``cross_validation_split`` would otherwise be silently ignored and the caller
    would believe they had chosen a fold count they did not.

    ``cross_validation_splits`` is optional and deliberately so. The design is
    derived from the dataset's structure, and the fold count is bounded by what the
    data supports rather than by what a caller asks for. A caller who names a count
    is stating a preference; a preference the data cannot satisfy is refused with
    the counts that failed, not quietly reduced.
    """

    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "The registered model to evaluate. The registry's key is model_id; the "
            "version is read from the record rather than required here, so a caller "
            "cannot ask for a version the model does not have."
        ),
    )
    cross_validation_splits: int | None = Field(
        default=None,
        ge=MIN_SPLITS,
        le=MAX_SPLITS,
        description=(
            f"Requested fold count, {MIN_SPLITS} to {MAX_SPLITS}. Null lets the data "
            "decide. The design itself is never chosen by the caller: it is derived "
            "from the split strategy the model was trained with, so a request cannot "
            "shuffle a time-ordered dataset to make a score look better."
        ),
    )


class EvaluationSummary(BaseModel):
    """The stored report for one model version, for the list and compare views."""

    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    model_id: str
    version: str
    target: str
    algorithm: str | None = None
    evaluation_set: str = Field(
        description="What the rows behind every figure are, in words. Never blank."
    )
    evaluated_rows: int = Field(
        ge=0, description="How many rows each figure was computed on."
    )
    metrics: dict[str, Any] = Field(
        description=(
            "The selected model's six headline readings: accuracy, precision, "
            "recall, f1, roc_auc, pr_auc. Each is a MetricReading, not a number."
        )
    )
    comparison: dict[str, Any] = Field(
        description="The full comparison table, including its selection criterion."
    )
    cross_validation: dict[str, Any] = Field(
        description="Whether cross-validation ran, and if not, why."
    )
    dataset_id: str | None = None
    dataset_version: int | None = None
    dataset_content_hash: str | None = None
    random_seed: int | None = None
    library_versions: dict[str, str] = Field(default_factory=dict)
    trained_at: datetime | None = None
    evaluated_at: datetime
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "Caveats that qualify the whole report. Includes how the non-selected "
            "candidates were produced and any disagreement with the training run's "
            "own recorded scores."
        ),
    )


class EvaluationDetailResponse(BaseModel):
    """One evaluation in full: the selected model, every candidate, and the resampling.

    The per-candidate evaluations and the per-fold evaluations are the two places a
    reader goes to check a headline figure, so both are in the payload rather than
    behind a second request. Each carries its own ``evaluated_rows`` and
    ``evaluation_set``, because a fold's score and the single held-out score describe
    different rows and the difference is the point.
    """

    model_config = ConfigDict(extra="forbid")

    evaluation_id: str
    model_id: str
    version: str
    target: str
    algorithm: str | None = None
    evaluation: ClassificationEvaluation = Field(
        description=(
            "The registered artifact scored on the reproduced held-out rows. Every "
            "reading carries the reason it is absent, where it is absent."
        )
    )
    candidate_evaluations: dict[str, ClassificationEvaluation] = Field(
        description="One entry per trained classifier, keyed by algorithm."
    )
    cross_validation: CrossValidationReport
    comparison: dict[str, Any]
    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Dataset id, version and content hash; artifact path; features; split "
            "strategy and detail; seed; library versions. Everything needed to "
            "re-derive the run."
        ),
    )
    evaluation_set: str
    evaluated_rows: int
    evaluated_at: datetime = Field(
        description=(
            "When this report was computed. Present on the detail view as well as the "
            "summary because a figure without a date is not reproducible: it says "
            "nothing about which run of the code, or against which records, produced "
            "it. It moves when a re-run replaces the report, which is the visible "
            "signal that the numbers were refreshed."
        )
    )
    notes: list[str] = Field(default_factory=list)
    recomputed: bool = Field(
        default=False,
        description=(
            "True when this run replaced an existing report for the same model "
            "version. The run is deterministic given the recorded seed, so a re-run "
            "is one measurement refreshed and not a second one; saying so is the "
            "difference between a repeated check and a repeated sample."
        ),
    )


class ComparisonRow(BaseModel):
    """One row of the comparison table: a classifier and its six readings."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(description="The algorithm, as registered.")
    algorithm: str
    model_id: str | None = None
    version: str | None = None
    selected: bool = Field(
        description=(
            "Whether Module 8's documented rule selected this candidate. Carried as "
            "a boolean with the criterion attached, never as the word 'best'."
        )
    )
    auto_selectable: bool
    selection_note: str
    evaluated_rows: int
    evaluation_set: str
    classes: list[str] = Field(default_factory=list)
    class_distribution: dict[str, int] = Field(default_factory=dict)
    metrics: dict[str, Any] = Field(
        description="The six headline readings, keyed by the table's column names."
    )
    cross_validation: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Cross-validated figures for this candidate, in their own block. Null "
            "when cross-validation did not run. Never folded into `metrics`, because "
            "a fold mean and a single-split score are measured on different rows."
        )
    )


class ComparisonResponse(BaseModel):
    """The comparison table, with the criterion and the comparability verdict.

    The two trailing blocks are the reason this response is trustworthy rather than
    decorative: ``selection_criterion`` states the rule that produced ``selected``,
    and ``comparable`` states whether the rows share a denominator. Without them the
    table is a set of numbers with a highlighted one.
    """

    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(
        default_factory=lambda: list(COMPARISON_COLUMNS),
        description="The metric columns, in display order. 'model' leads the table.",
    )
    column_titles: dict[str, str] = Field(default_factory=lambda: dict(COLUMN_TITLES))
    rows: list[ComparisonRow] = Field(default_factory=list)
    row_count: int = 0
    selected_algorithm: str | None = None
    selection_criterion: str = SELECTION_CRITERION
    average_precision_note: str | None = None
    cross_validation_performed: bool = False
    cross_validation_note: str | None = None
    comparable: dict[str, Any] = Field(default_factory=dict)
    conventions_in_force: list[str] = Field(default_factory=list)
    does_not: str = COMPARISON_DOES_NOT_DO


class CompareVersionsResponse(BaseModel):
    """Stored evaluations across model versions, one row each.

    For answering "did the later model do better", which is the question a reader
    arrives with and the one a single table of four candidates cannot answer. The
    ``comparable`` block is where that answer may not be available: two versions
    trained on different dataset revisions, or on different held-out fractions, are
    not comparable however good their numbers look side by side.
    """

    model_config = ConfigDict(extra="forbid")

    evaluations: list[EvaluationSummary] = Field(default_factory=list)
    count: int = 0
    comparison: ComparisonResponse | None = Field(
        default=None,
        description=(
            "The same six columns across versions rather than across candidates, "
            "when every row shares a row set and row count. Null otherwise, with the "
            "reason in `not_comparable` -- a table built from rows that do not share "
            "a denominator would rank the datasets, not the models."
        )
    )
    not_comparable: list[str] = Field(default_factory=list)


class EvaluationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluations: list[EvaluationSummary] = Field(default_factory=list)
    count: int = 0
    evaluable_models: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Registered models, including those never evaluated, so the picker's "
            "list is what can be evaluated rather than what has been."
        ),
    )


class RefusalResponse(EvaluationRefusal):
    """The 409/422 body. The model's own schema, named for the OpenAPI document."""


def to_summary(document: ModelEvaluation) -> EvaluationSummary:
    """The list/compare view of a stored report.

    Reads the comparison's stored rows rather than recomputing the headline from
    ``candidate_evaluations``, so the summary cannot disagree with the table stored
    beside it. The selected row is found by flag rather than by looking up
    ``algorithm``, which keeps this correct if the registry's algorithm name is one
    the candidate set did not produce.
    """
    rows = document.comparison.get("rows", [])
    selected_row = next(
        (row for row in rows if row.get("selected")),
        None,
    )
    metrics: dict[str, Any] = {}
    if selected_row is not None:
        metrics = dict(selected_row.get("metrics", {}))
    elif document.evaluation.evaluated_rows:
        # The registry named an algorithm the candidate set did not produce, so no
        # row is flagged. The selected model's own readings are still reported, under
        # the registry's name -- the alternative is a summary with no metrics at all,
        # which reads as a model that scored nothing.
        metrics = {
            column: reading.as_dict()
            for column, reading in document.evaluation.headline().items()
        }
    return EvaluationSummary(
        evaluation_id=document.evaluation_id,
        model_id=document.model_id,
        version=document.version,
        target=document.target.value,
        algorithm=document.algorithm,
        evaluation_set=document.evaluation_set,
        evaluated_rows=document.evaluated_rows,
        metrics=metrics,
        comparison=document.comparison,
        cross_validation=document.cross_validation.as_dict(),
        dataset_id=document.dataset_id,
        dataset_version=document.dataset_version,
        dataset_content_hash=document.dataset_content_hash,
        random_seed=document.random_seed,
        library_versions=dict(document.library_versions),
        trained_at=document.trained_at,
        evaluated_at=document.evaluated_at,
        notes=list(document.notes),
    )


def to_detail(
    document: ModelEvaluation, *, recomputed: bool = False
) -> EvaluationDetailResponse:
    """The full view of a stored report, provenance included."""
    return EvaluationDetailResponse(
        evaluation_id=document.evaluation_id,
        model_id=document.model_id,
        version=document.version,
        target=document.target.value,
        algorithm=document.algorithm,
        evaluation=document.evaluation,
        candidate_evaluations=dict(document.candidate_evaluations),
        cross_validation=document.cross_validation,
        comparison=document.comparison,
        provenance={
            "dataset_id": document.dataset_id,
            "dataset_version": document.dataset_version,
            "dataset_content_hash": document.dataset_content_hash,
            "artifact_path": document.artifact_path,
            "feature_names": list(document.feature_names),
            "split_strategy": document.split_strategy,
            "split_detail": dict(document.split_detail),
            "random_seed": document.random_seed,
            "library_versions": dict(document.library_versions),
        },
        evaluation_set=document.evaluation_set,
        evaluated_rows=document.evaluated_rows,
        evaluated_at=document.evaluated_at,
        notes=list(document.notes),
        recomputed=recomputed,
    )


__all__ = [
    "AlgorithmCrossValidation",
    "ClassificationEvaluation",
    "CompareVersionsResponse",
    "ComparisonResponse",
    "ComparisonRow",
    "CrossValidationFold",
    "CrossValidationReport",
    "EvaluationDetailResponse",
    "EvaluationListResponse",
    "EvaluationSummary",
    "ExportFormat",
    "MetricAggregate",
    "RefusalResponse",
    "RunEvaluationRequestBody",
    "to_detail",
    "to_summary",
]
