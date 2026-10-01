"""Module 16 - the HTTP contract for predicted-vs-actual validation.

This module answers one question: do the recorded outcomes agree with what the
project predicted before they happened? Its five comparison blocks grade three
kinds of prediction (forecast rain, simulated salinity from the rain that fell,
simulated salinity from the rain that was forecast) and two kinds of judgement
(the risk rule the model read against the rule re-applied to what happened, and
the action advised against the action the operator recorded taking).

The contract holds to the project's metric discipline at every level:

* a metric that was not calculated is a :class:`MetricReading` with
  ``computed=false`` and a reason, never a number -- so a model that was never
  scored cannot be read as having scored zero;
* every statistic travels beside how many events produced it and how many could
  not, with the reasons the excluded ones could not;
* a comparison block reports ``n_compared`` and ``n_excluded`` together, so a
  figure built from two of twenty events cannot be read as the twenty.

The pair rows are the research artefact the aggregates are built from. Both the
aggregates and the raw rows are exported, so a reader can check any figure
against the events that produced it rather than taking it on trust.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.evaluation import MetricReading

#: The five comparison families, in the order the dashboard reports them.
#: Simple keys so a query string or an exported file can name one unambiguously.
COMPARISON_KEYS = (
    "forecast_vs_actual_rainfall",
    "simulated_vs_actual_salinity",
    "forecast_simulated_vs_actual_salinity",
    "predicted_risk_vs_outcome",
    "recommended_vs_operator_action",
)

ComparisonKey = Literal[
    "forecast_vs_actual_rainfall",
    "simulated_vs_actual_salinity",
    "forecast_simulated_vs_actual_salinity",
    "predicted_risk_vs_outcome",
    "recommended_vs_operator_action",
]

COMPARISON_KIND_CONTINUOUS = "continuous"
COMPARISON_KIND_CLASSIFICATION = "classification"


class ExcludedReason(BaseModel):
    """Why events could not be compared, counted by reason.

    Grouped and counted rather than listed, so a block can say "three events had
    no post-event salinity" in one place and still name each one in the pairs
    view.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=600)
    count: int = Field(ge=0)


class ContinuousComparison(BaseModel):
    """One predicted-vs-actual series measured in a continuous unit."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="The comparison family this block reports.")
    title: str = Field(description="The block's headline, in words.")
    unit: str = Field(description="The unit every number in the block shares.")
    description: str = Field(
        description="What is being measured, and why it matters for validation."
    )
    n_compared: int = Field(
        ge=0, description="Events with both a stored prediction and a measured actual."
    )
    n_excluded: int = Field(
        ge=0, description="Events in scope that could not be compared."
    )
    excluded_reasons: list[ExcludedReason] = Field(default_factory=list)
    mean_absolute_error: MetricReading
    root_mean_squared_error: MetricReading
    r_squared: MetricReading
    mean_bias_error: MetricReading
    notes: list[str] = Field(default_factory=list)


class PerClassScore(BaseModel):
    """One class's own agreement, with the counts it came from.

    ``support`` is how many compared events really carried the class and
    ``predicted`` how many the model said so. A precision of zero and a
    precision of "never predicted" are the same number and different facts, and
    the counts tell them apart.
    """

    model_config = ConfigDict(extra="forbid")

    label: str = Field(description="The class, as stored on the rule or action.")
    description: str = Field(
        default="",
        description="What the class means, when a risk rule says so.",
    )
    support: int = Field(ge=0, description="Compared events whose actual class is this.")
    predicted: int = Field(ge=0, description="Compared events predicted as this class.")
    precision: MetricReading
    recall: MetricReading
    f1: MetricReading


class ConfusionMatrix(BaseModel):
    """Who was mistaken for whom, with the rows that were never filled.

    ``matrix[actual][predicted]``, so each row sums to that actual class's true
    count. A class in the vocabulary that no compared event carried appears as a
    zero row and column and is named in ``classes_absent_from_evaluation``, so
    the zeros read as "did not occur" rather than "scored perfectly".
    """

    model_config = ConfigDict(extra="forbid")

    labels: list[str] = Field(
        description="Row and column labels, in the order the matrix is written."
    )
    matrix: list[list[int]] = Field(
        description="matrix[actual][predicted]. Row sums are true counts."
    )
    classes_absent_from_evaluation: list[str] = Field(default_factory=list)


class ClassificationComparison(BaseModel):
    """Two vocabularies graded against each other, class for class."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(description="The comparison family this block reports.")
    title: str = Field(description="The block's headline, in words.")
    description: str = Field(
        description="What is being compared, and where each label comes from."
    )
    classes: list[str] = Field(
        default_factory=list,
        description=(
            "The label space the matrix and the distributions are written against: "
            "a risk rule's vocabulary, or the four recorded actions."
        ),
    )
    n_compared: int = Field(
        ge=0, description="Events holding both a stored prediction and its actual label."
    )
    n_excluded: int = Field(
        ge=0, description="Events in scope that could not be compared."
    )
    excluded_reasons: list[ExcludedReason] = Field(default_factory=list)
    predicted_distribution: dict[str, int] = Field(default_factory=dict)
    actual_distribution: dict[str, int] = Field(default_factory=dict)

    accuracy: MetricReading
    precision: MetricReading = Field(description="Macro-average over observed classes.")
    recall: MetricReading = Field(description="Macro-average over observed classes.")
    f1: MetricReading = Field(description="Macro-average over observed classes.")
    roc_auc: MetricReading
    pr_auc: MetricReading

    per_class: list[PerClassScore] = Field(default_factory=list)
    confusion_matrix: ConfusionMatrix | None = Field(
        default=None, description="Absent only when no event could be compared."
    )
    notes: list[str] = Field(default_factory=list)

    #: The risk rule the compared events were graded under. Filled for
    #: ``predicted_risk_vs_outcome`` when a rule can be identified, so a reader
    #: knows exactly which thresholds a figure was measured against.
    definition_id: str | None = None
    definition_name: str | None = None
    definition_version: int | None = None


class ValidationScope(BaseModel):
    """What a validation report covers, so its numbers cannot be over-read."""

    model_config = ConfigDict(extra="forbid")

    pan_id: str | None = Field(
        default=None, description="The pan reported, or null for every pan."
    )
    total_outcomes: int = Field(
        ge=0, description="Recorded events considered by the report."
    )


class ValidationOverview(BaseModel):
    """Reported outcomes against every prediction the project makes."""

    model_config = ConfigDict(extra="forbid")

    scope: ValidationScope
    forecast_vs_actual_rainfall: ContinuousComparison
    simulated_vs_actual_salinity: ContinuousComparison
    forecast_simulated_vs_actual_salinity: ContinuousComparison
    predicted_risk_vs_outcome: ClassificationComparison
    recommended_vs_operator_action: ClassificationComparison


class ValidationPairRow(BaseModel):
    """One event in one comparison, whether or not it could be compared.

    ``compared=false`` events still appear, so an excluded event is not a
    missing event. ``reason`` names why the pair could not be made. Which of the
    predicted/actual fields are filled depends on the comparison family: the
    three continuous families fill ``predicted_number``/``actual_number``, and
    the two classification families fill ``predicted_class``/``actual_class``
    (plus ``probability`` where the model stored one).
    """

    model_config = ConfigDict(extra="forbid")

    outcome_id: str
    pan_id: str
    event_id: str
    timestamp: datetime
    compared: bool = Field(
        description="True only when a stored prediction and a real label both existed."
    )
    reason: str | None = Field(
        default=None, description="Why the pair could not be made, when it could not."
    )

    predicted_number: float | None = None
    actual_number: float | None = None
    signed_error: float | None = Field(
        default=None, description="predicted - actual. Positive means the model ran high."
    )

    predicted_class: str | None = None
    actual_class: str | None = None
    probability: float | None = Field(
        default=None, description="The probability the model stored for the predicted class."
    )

    risk_definition_id: str | None = None
    risk_definition_name: str | None = None
    risk_definition_version: int | None = None


class ValidationPairsResponse(BaseModel):
    """Every event in one comparison, newest first, compared or not."""

    model_config = ConfigDict(extra="forbid")

    comparison: str
    kind: Literal["continuous", "classification"]
    unit: str | None = None
    pan_id: str | None = None
    total_outcomes: int = Field(
        ge=0, description="Recorded events in scope, before any exclusion."
    )
    items: list[ValidationPairRow] = Field(default_factory=list)


__all__ = [
    "COMPARISON_KEYS",
    "COMPARISON_KIND_CLASSIFICATION",
    "COMPARISON_KIND_CONTINUOUS",
    "ClassificationComparison",
    "ConfusionMatrix",
    "ContinuousComparison",
    "ExcludedReason",
    "PerClassScore",
    "ValidationOverview",
    "ValidationPairRow",
    "ValidationPairsResponse",
    "ValidationScope",
]