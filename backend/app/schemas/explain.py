"""Request and response shapes for Module 11, SHAP explainability.

The three quantity blocks are the load-bearing part of this file. A SHAP
contribution, a model probability and a physical simulation result are different
claims about a pan, and the response shape keeps them in three named fields with
their own labels, units and notes rather than in one flat list of numbers where a
reader would have to know which was which.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: The three quantities a local explanation reports, named once so the schema, the
#: service and the documentation cannot drift apart on what the words mean.
MODEL_PROBABILITY = "model_probability"
SHAP_CONTRIBUTION = "shap_contribution"
PHYSICAL_SIMULATION = "physical_simulation"

#: The only output this module explains.
EXPLAINED_OUTPUT = "probability"

DIRECTION_INCREASES = "increases_risk"
DIRECTION_DECREASES = "decreases_risk"
DIRECTION_NEUTRAL = "neutral"


class QuantityReading(BaseModel):
    """One reported quantity, with its own label and unit stated."""

    model_config = ConfigDict(extra="forbid")

    label: str
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: str
    note: str


class ShapContributionReading(BaseModel):
    """What the SHAP contributions are contributions *of*."""

    model_config = ConfigDict(extra="forbid")

    label: str
    unit: str
    base_value: float | None = Field(default=None, allow_inf_nan=False)
    explained_output: str = EXPLAINED_OUTPUT
    target_class: str | None = None
    note: str


class PhysicalSimulationReading(BaseModel):
    """The mass-balance outputs the explained row carried as inputs."""

    model_config = ConfigDict(extra="forbid")

    label: str
    unit: str
    results: dict[str, Any] = Field(default_factory=dict)
    note: str


class Quantities(BaseModel):
    """The three quantities, kept apart, each with a label and a unit."""

    model_config = ConfigDict(extra="forbid")

    model_probability: QuantityReading
    shap_contribution: ShapContributionReading
    physical_simulation: PhysicalSimulationReading


class AttributionResponse(BaseModel):
    """One feature's contribution to the decomposed probability."""

    model_config = ConfigDict(extra="forbid")

    feature: str
    #: The row's own value, or ``None`` where the row held nothing and the model's
    #: preprocessor imputed it. Reported as absent rather than as the imputed value,
    #: which is a statistic of the training rows and not a measurement of the pan.
    feature_value: float | None = Field(default=None, allow_inf_nan=False)
    shap_value: float = Field(allow_inf_nan=False)
    direction: str
    source: str | None = None
    source_kind: str
    imputed: bool


class ReferenceDistribution(BaseModel):
    """The distribution the contributions are measured against."""

    model_config = ConfigDict(extra="forbid")

    source: str
    rows: int = Field(ge=0)
    width: int = Field(default=0, ge=0)
    train_rows: int | None = Field(default=None, ge=0)
    holdout_rows: int | None = Field(default=None, ge=0)
    selection: str | None = None


class LocalExplanationResponse(BaseModel):
    """A stored attribution for one stored prediction."""

    model_config = ConfigDict(extra="forbid")

    explanation_id: str
    prediction_id: str | None = None
    pan_id: str | None = None
    model_id: str | None = None
    model_version: str
    model_name: str | None = None
    algorithm: str | None = None
    kind: str
    created_at: Any
    #: The class whose probability was decomposed. Null only for a stored record
    #: predating this field.
    target_class: str | None = None
    input_snapshot_id: str | None = None
    twin_id: str | None = None
    twin_as_of: str | None = None
    quantities: Quantities
    model_probability: float | None = Field(default=None, allow_inf_nan=False)
    base_value: float | None = Field(default=None, allow_inf_nan=False)
    #: ``|probability - (base_value + Σ shap_value)|``. Zero for an exact explainer.
    attribution_error: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    method: str
    #: Whether the values were computed or estimated. Never quietly dropped.
    approximate: bool
    shap_version: str
    background: ReferenceDistribution
    attributions: list[AttributionResponse]
    top_increasing: list[AttributionResponse]
    top_decreasing: list[AttributionResponse]
    #: Always ``False``. Stated in the contract so a client can assert it, and so a
    #: simulated attribution could never be mistaken for a computed one.
    is_simulated: bool = False


class GlobalFeatureImportance(BaseModel):
    """One feature's mean absolute contribution across the explained rows."""

    model_config = ConfigDict(extra="forbid")

    feature: str
    mean_abs_shap: float = Field(ge=0, allow_inf_nan=False)
    rank: int = Field(ge=1)


class GlobalImportanceResponse(BaseModel):
    """A model version's feature importance over its held-out rows."""

    model_config = ConfigDict(extra="forbid")

    explanation_id: str
    model_id: str | None = None
    model_version: str
    model_name: str | None = None
    algorithm: str | None = None
    kind: str
    created_at: Any
    explained_output: str = EXPLAINED_OUTPUT
    rows_explained: int = Field(ge=1)
    #: How many rows contributed to each class's average, so a reader can see
    #: whether the ranking rests on one class or several.
    explained_classes: dict[str, int]
    method: str
    approximate: bool
    shap_version: str
    background: ReferenceDistribution
    measure: QuantityReading
    features: list[GlobalFeatureImportance]
    is_simulated: bool = False


class ExplanationSummary(BaseModel):
    """One row of the per-model audit list."""

    model_config = ConfigDict(extra="forbid")

    explanation_id: str
    kind: str
    prediction_id: str | None = None
    pan_id: str | None = None
    target_class: str | None = None
    explained_output: str
    model_output: float | None = Field(default=None, allow_inf_nan=False)
    base_value: float | None = Field(default=None, allow_inf_nan=False)
    attribution_error: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    method: str
    approximate: bool
    shap_version: str
    rows_explained: int | None = None
    created_at: Any
    is_simulated: bool = False


class ModelExplanationListResponse(BaseModel):
    """Every stored explanation for one model version."""

    model_config = ConfigDict(extra="forbid")

    model_id: str
    model_version: str
    count: int = Field(ge=0)
    explanations: list[ExplanationSummary]


class ExplainRefusalResponse(BaseModel):
    """A refusal with a machine-readable code, never a substitute value.

    ``refusal`` is what a client branches on; ``detail`` is the sentence a person
    reads. There is no ``value`` field, and no field that could hold one, because
    the one thing this module must never do is answer with a number it did not
    compute.
    """

    model_config = ConfigDict(extra="forbid")

    detail: str
    refusal: str
    model_id: str | None = None
    prediction_id: str | None = None
