"""Module 09 - the shapes an evaluation is stored and reported in.

This module exists because of one rule the rest of the project holds to everywhere
else and this one makes mechanical: **a metric that was not calculated is not a
number, and it is never rendered as one.** The way that is enforced here is the
:class:`MetricReading` invariant, which makes an uncalculated metric
*unrepresentable* rather than merely discouraged.

A naive evaluation payload is a flat ``dict[str, float]`` with absent keys meaning
"not computed". That is ambiguous in the one direction that matters: a consumer
that wants a number finds a missing key, decides for itself that this means zero,
and draws a bar at zero. A model that has never been scored on a class and a model
that scores zero on that class then become the same object. The whole of
:mod:`app.ml.evaluation` exists to stop that, and it stops it at the type level
here.

So a metric is never a float. It is a :class:`MetricReading`, which carries three
things together and validates them against each other:

``value``
    The number, or ``None`` when there is none.
``computed``
    Whether the metric was actually calculated on the rows named beside it.
``reason``
    Why it was not, in words specific enough to act on.

The validator enforces ``computed`` iff ``value is not None``, and requires a
``reason`` whenever the metric is absent. A caller cannot construct a reading that
claims a metric was not computed without saying why, and cannot construct one that
carries a number while claiming the opposite. Downstream, that means the frontend
can trust ``value === null`` to mean "not calculated" because the backend had no way
to say otherwise.

**A convention is not a computation.** Some metrics are undefined rather than
merely unmeasured -- ROC-AUC needs both classes present, a class's precision needs
at least one prediction of it. Where a denominator is zero, the reading is marked
not computed and carries the reason. Where scikit-learn's ``zero_division=0``
convention is applied instead, the value is 0.0 *and* the reading carries a reason
saying the 0.0 is a declared convention rather than a measured precision, and the
enclosing evaluation repeats that in its ``notes``. Both are honest; only one of
them would survive a reader who looked at the number alone.
"""

from __future__ import annotations

import math
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The averaging schemes reported for precision, recall and F1.
#:
#: Macro is the headline because these datasets are rarely balanced, and a macro
#: figure is the one that moves when a minority class is actually learned. Weighted
#: is reported beside it because it is the figure a reader is most likely to
#: compare against something else, and the two answering different questions on an
#: unbalanced dataset is the whole reason to show both.
MACRO = "macro"
WEIGHTED = "weighted"
BINARY = "binary"

#: Named so a stored evaluation can say *which* quantity a number is, rather than
#: leaving a reader to guess whether ``roc_auc`` was the binary curve or a
#: one-vs-rest average. A two-class and a three-class run produce different numbers
#: under the same key, and a comparison table that does not say which it is
#: comparing is a comparison of two different quantities.
VARIANT_BINARY = "binary_positive_class"
VARIANT_OVR_MACRO = "one_vs_rest_macro"


class MetricReading(BaseModel):
    """One metric, and whether it exists.

    The invariant below is the load-bearing part of this module. It is a
    ``model_validator`` rather than a comment because the failure it prevents --
    a number with no computation behind it, or a computation reported as absent --
    is silent in every downstream consumer: a table, a chart, and a CSV all render
    a reading without inspecting it.
    """

    model_config = ConfigDict(extra="forbid")

    value: float | None = Field(
        default=None,
        description="The metric. Null exactly when it was not calculated.",
    )
    computed: bool = Field(
        description=(
            "True only when the metric was calculated on the rows named by the "
            "enclosing evaluation. False means no number exists, and the reason "
            "field says why."
        ),
    )
    variant: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Which quantity this number is, where the name is ambiguous: "
            f"'{BINARY}' or '{VARIANT_BINARY}' for a two-class curve, "
            f"'{VARIANT_OVR_MACRO}' for a one-vs-rest average. Null when the "
            "metric has only one meaning."
        ),
    )
    reason: str | None = Field(
        default=None,
        max_length=600,
        description=(
            "Why the value is what it is. Required when the metric was not "
            "computed. Also set, alongside a real value, when a declared "
            "convention produced that value rather than a measurement."
        ),
    )

    @model_validator(mode="after")
    def _enforce_reading(self) -> Self:
        if self.computed and self.value is None:
            raise ValueError(
                "A metric marked computed must carry a value. 'computed' without a "
                "number is a claim that something was calculated, which is exactly "
                "what a consumer of this field cannot check."
            )
        if not self.computed:
            if self.value is not None:
                raise ValueError(
                    "A metric that was not computed must not carry a value. Storing "
                    "0.0 here would be indistinguishable from a measured zero, which "
                    "is the failure this type exists to prevent."
                )
            if not self.reason:
                raise ValueError(
                    "A metric that was not computed must say why. 'No value' and "
                    "'no value because the held-out rows contained only one class' "
                    "call for different responses from whoever reads this, and only "
                    "the second one tells them which."
                )
        if self.value is not None and not math.isfinite(self.value):
            raise ValueError(
                f"metric value {self.value!r} is not finite. NaN and infinity are "
                "what a division by an empty class produces, and they are not "
                "numbers a comparison can rank."
            )
        if not self.computed and self.variant:
            raise ValueError(
                "A metric that was not computed has no variant, because there is no "
                "quantity for the variant to qualify. Naming one would suggest the "
                "metric exists in that form and is merely blank."
            )
        return self

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def not_computed(reason: str) -> MetricReading:
    """An absent metric, with the reason it is absent.

    Constructed through here rather than inline so that every absent metric in the
    system is built by the one function that fills in the required ``reason`` -- a
    literal ``MetricReading(computed=False)`` would be rejected by the validator,
    which is the intended pressure.
    """
    return MetricReading(value=None, computed=False, reason=reason)


def computed(value: float, *, variant: str | None = None, reason: str | None = None) -> MetricReading:
    """A metric that was calculated, optionally qualified or annotated.

    ``reason`` is used for the case where the value is real but rests on a declared
    convention -- ``zero_division=0`` on a class the model never predicted -- so the
    number travels with the caveat rather than the caveat being lost in a separate
    field nobody reads.
    """
    return MetricReading(
        value=float(value), computed=True, variant=variant, reason=reason
    )


class ClassScore(BaseModel):
    """One class's own performance, with the counts it came from.

    ``support`` and ``predicted`` are here for a specific reason: a precision of
    0.00 and a precision of "the model never once said this class" are the same
    number and completely different facts, and the counts are what tells them
    apart. The per-class ``reason`` says so in words as well.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(max_length=200, description="The class label, as stored.")
    support: int = Field(
        ge=0, description="Evaluated rows whose true label is this class."
    )
    predicted: int = Field(
        ge=0, description="Evaluated rows the model predicted as this class."
    )
    precision: MetricReading
    recall: MetricReading
    f1: MetricReading
    average_precision: MetricReading = Field(
        description=(
            "One-vs-rest average precision for this class. Computed only when the "
            "class has at least one positive row in the evaluated set, so it is "
            "absent for a class the evaluated rows never carried -- required "
            "rather than defaulted, because a default here would be a metric "
            "reading nobody chose."
        ),
    )


class ConfusionMatrixReport(BaseModel):
    """The confusion matrix, with its orientation and its empty rows stated.

    ``labels`` is the model's own label space -- the classes it was fitted on --
    not merely the classes that happened to appear. A class the model could predict
    but that no evaluated row carried therefore gets a row and column of zeros, and
    is named in ``classes_absent_from_evaluation`` so the zeros read as "did not
    occur" rather than "scored perfectly".
    """

    model_config = ConfigDict(extra="forbid")

    labels: list[str] = Field(
        description="Row and column labels, in the order the matrix is written."
    )
    matrix: list[list[int]] = Field(
        description="matrix[actual][predicted]. Row sums are true counts."
    )
    classes_absent_from_evaluation: list[str] = Field(
        default_factory=list,
        description=(
            "Label-space classes that appear in neither the evaluated labels nor "
            "the predictions, so their whole row and column are zero."
        ),
    )
    classes_absent_from_evaluation_rows: list[str] = Field(
        default_factory=list,
        description=(
            "Classes that are label-space members but appear in no evaluated row, "
            "so their recall is undefined. A subset of the row above whenever a "
            "class was predicted but never true, which is the more common case."
        ),
    )
    most_confused_pair: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The largest off-diagonal cell: which class was mistaken for which, "
            "and how often. Null when the matrix is diagonal throughout, so the "
            "absence of a confusion is distinguishable from an empty matrix."
        ),
    )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ClassificationEvaluation(BaseModel):
    """Everything computed about one classifier on one set of rows.

    The name of those rows is in ``evaluation_set`` and it is not decoration. A
    held-out score, a cross-validation fold and a score against freshly recorded
    outcomes answer different questions, and a figure without its denominator
    invites the reading that it is better than the neighbouring figure it is
    compared with.
    """

    model_config = ConfigDict(extra="forbid")

    evaluated_rows: int = Field(
        ge=0, description="How many rows the metrics below were computed on."
    )
    evaluation_set: str = Field(
        max_length=200,
        description=(
            "What these rows are, in words: e.g. 'the held-out rows of the "
            "recorded split'. Never blank, because a metric with no named "
            "denominator is the one figure this system refuses to publish."
        ),
    )
    classes: list[str] = Field(
        description="The model's label space, sorted. The matrix and per-class order."
    )
    class_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="True counts per class over the evaluated rows.",
    )
    predicted_distribution: dict[str, int] = Field(
        default_factory=dict,
        description="Predicted counts per class over the evaluated rows.",
    )

    accuracy: MetricReading
    balanced_accuracy: MetricReading
    precision: MetricReading = Field(description=f"Averaged {MACRO}.")
    precision_weighted: MetricReading
    recall: MetricReading = Field(description=f"Averaged {MACRO}.")
    recall_weighted: MetricReading
    f1: MetricReading = Field(description=f"Averaged {MACRO}.")
    f1_weighted: MetricReading
    matthews_corrcoef: MetricReading
    roc_auc: MetricReading
    pr_auc: MetricReading
    brier_score: MetricReading
    log_loss: MetricReading

    per_class: list[ClassScore] = Field(
        default_factory=list, description="One entry per label-space class, in order."
    )
    confusion_matrix: ConfusionMatrixReport | None = Field(
        default=None,
        description="Absent only when there were no rows to build one from.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description=(
            "The conventions in force while these numbers were produced, stated so "
            "they cannot be lost. Includes the zero-division policy and any class "
            "that was absent from the evaluated rows."
        ),
    )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def headline(self) -> dict[str, MetricReading]:
        """The readings the comparison table shows, in its column order.

        The table asks for six figures. This returns those six, and nothing else,
        so a column cannot quietly gain a seventh metric that only some rows have.
        """
        return {
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "roc_auc": self.roc_auc,
            "pr_auc": self.pr_auc,
        }


class MetricAggregate(BaseModel):
    """A metric summarised across folds.

    ``folds_aggregated`` and ``folds_omitted`` are read as a pair, and they are the
    reason this type exists. A mean of ROC-AUC over three of five folds is a real
    number, but presented alone it reads as a five-fold mean, and the two folds
    that could not produce it are exactly the folds a reader most needs to know
    about. Omitting a fold silently is how a cross-validated table overstates its
    own agreement.
    """

    model_config = ConfigDict(extra="forbid")

    mean: MetricReading
    standard_deviation: MetricReading
    minimum: MetricReading
    maximum: MetricReading
    folds_aggregated: int = Field(ge=0, description="Folds that produced a value.")
    folds_omitted: list[int] = Field(
        default_factory=list,
        description="Fold numbers (1-based) that could not produce the metric.",
    )
    reason: str | None = Field(
        default=None,
        description="Why those folds are missing, taken from the first of them.",
    )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class CrossValidationFold(BaseModel):
    """One fold's evaluation, plus the rows it was computed on."""

    model_config = ConfigDict(extra="forbid")

    fold: int = Field(ge=1, description="1-based fold number, in training order.")
    train_rows: int = Field(ge=0)
    test_rows: int = Field(ge=0)
    train_pans: list[str] = Field(default_factory=list)
    test_pans: list[str] = Field(default_factory=list)
    train_time_range: dict[str, str | None] = Field(
        default_factory=dict,
        description=(
            "Earliest and latest training anchor instants, for a temporal split. "
            "Empty for a split that has no time axis."
        ),
    )
    test_time_range: dict[str, str | None] = Field(default_factory=dict)
    evaluation: ClassificationEvaluation


class CrossValidationReport(BaseModel):
    """The cross-validation run: whether it happened, and if so what it found.

    ``performed`` is ``False`` with a ``reason`` whenever the dataset could not
    support the requested design. That is the designed outcome for data this
    small, exactly as refusing to train is for data too small to train on -- so
    the report is stored on the model version either way, and a reader learns that
    cross-validation was considered and declined rather than having to infer from
    its absence that nobody thought of it.
    """

    model_config = ConfigDict(extra="forbid")

    performed: bool
    reason: str | None = Field(
        default=None,
        description="Why cross-validation was not performed. Required when it was not.",
    )
    method: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Which resampling design was used, and chosen to match the dataset's "
            "structure rather than applied by default."
        ),
    )
    n_splits: int | None = Field(default=None, ge=2)
    shuffled: bool | None = Field(
        default=None,
        description=(
            "Whether rows were shuffled before splitting. False for every temporal "
            "design, and stated because a shuffled time split trains on the future "
            "to predict the past and reports the result as generalisation."
        ),
    )
    evaluates_unseen_pans: bool | None = Field(
        default=None,
        description=(
            "Whether a fold's test rows come from pans absent from its training "
            "rows. Null when the design does not hold pans out."
        ),
    )
    matched_split_strategy: str | None = Field(
        default=None,
        max_length=64,
        description="The single-split strategy this design was derived from.",
    )
    rationale: str | None = Field(
        default=None, max_length=1200, description="Why this design suits this data."
    )
    folds: list[CrossValidationFold] = Field(default_factory=list)
    aggregate: dict[str, MetricAggregate] = Field(
        default_factory=dict,
        description=(
            "Mean, spread and range per headline metric, each with the folds it "
            "covers and the folds it could not include."
        ),
    )
    per_algorithm: dict[str, AlgorithmCrossValidation] = Field(
        default_factory=dict,
        description=(
            "One report per candidate, so the comparison is not a single model's "
            "and a candidate that folds badly is visible as such."
        ),
    )

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AlgorithmCrossValidation(BaseModel):
    """One candidate's cross-validation, and whether it may be selected.

    ``auto_selectable`` is carried per candidate rather than left to a reader of
    the comparison to remember, because the whole point of the field is that a
    candidate excluded from selection still appears in every table. A model shown
    beside the others without a note that it cannot win is a model that looks like
    it is being considered.
    """

    model_config = ConfigDict(extra="forbid")

    algorithm: str
    auto_selectable: bool
    selection_note: str = Field(
        max_length=600,
        description="Why it is or is not eligible for automatic selection.",
    )
    folds: list[CrossValidationFold] = Field(default_factory=list)
    aggregate: dict[str, MetricAggregate] = Field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class EvaluationRefusal(BaseModel):
    """Why a model version could not be evaluated, stated as data.

    Returned instead of an evaluation, with the counts that caused it, in the same
    spirit as Module 08's refusal: a bare 422 sends the reader back with no idea
    whether to rebuild the dataset, retrain, or gather more records.
    """

    model_config = ConfigDict(extra="forbid")

    detail: str = Field(description="The sentence a caller matches on.")
    refusal: str = Field(
        description=(
            "One of 'not_evaluable', 'dataset_drift', 'artifact_unavailable', "
            "'insufficient_data' or 'invalid_request'."
        )
    )
    model_id: str
    reasons: list[str] = Field(default_factory=list)
    dataset_id: str | None = None


__all__ = [
    "BINARY",
    "MACRO",
    "VARIANT_BINARY",
    "VARIANT_OVR_MACRO",
    "WEIGHTED",
    "AlgorithmCrossValidation",
    "ClassScore",
    "ClassificationEvaluation",
    "ConfusionMatrixReport",
    "CrossValidationFold",
    "CrossValidationReport",
    "EvaluationRefusal",
    "MetricAggregate",
    "MetricReading",
    "computed",
    "not_computed",
]
