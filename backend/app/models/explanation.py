"""One stored SHAP attribution, for a prediction or for a whole model version.

This module exists to hold the answer to one question: *which features moved this
number, and by how much*. Two properties of the record are load-bearing, and both
exist because of the failure mode this project is guarding against.

**A contribution is not a probability and not a measurement.** A SHAP value is a
number in the units of the model output it decomposes, and for a classifier that
is a shift in probability caused by one feature being at this row's value rather
than at its reference value. So the record stores, separately and explicitly, what
was decomposed (:attr:`Explanation.explained_output`), the model's own value for
it (:attr:`Explanation.model_output`), the reference value the contributions are
measured against (:attr:`Explanation.base_value`), and each feature's raw
contribution. A reader can add the base value to the contributions and arrive at
the model's number, and the residual is stored as
:attr:`Explanation.attribution_error` rather than hidden.

**A contribution is only as trustworthy as the arithmetic that produced it.**
:attr:`Explanation.method` records which SHAP explainer ran, whether it is exact
or an approximation (:attr:`Explanation.approximate`), which library version
(:attr:`Explanation.shap_version`), and the reference distribution the values are
measured against (:attr:`Explanation.background`). An explanation whose method is
unknown, or whose contributions do not add up to the model's own output, is not an
explanation, and the service refuses to store one rather than storing it with a
caveat attached.

**Where a feature value came from is part of the attribution.** A contribution
attributed to a mass-balance output is a different kind of statement from one
attributed to a sensor reading, and a contribution attributed to a value the twin
could not supply describes the model's use of its *imputed* value rather than
anything about the pan. Each attribution therefore carries the source the row
recorded (:attr:`FeatureAttribution.source`) and what kind of thing that is
(:attr:`FeatureAttribution.source_kind`).

:attr:`Explanation.is_simulated` exists and is always written ``False``. A
simulated explanation would be indistinguishable from a real one once it reached a
screen, and the field stays so that such a record could be identified rather than
believed.
"""

from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_identifier,
    normalize_pan_id,
    utcnow,
)

#: A local explanation decomposes one prediction; a global one summarises a model
#: version over many rows. Kept apart because they answer different questions and a
#: mean importance read as a per-pan reason would be a misreading.
KIND_LOCAL = "local"
KIND_GLOBAL = "global"

#: The quantity a SHAP value is measured in here. Only ever the model's predicted
#: probability: it is the number the operator is shown, so it is the number an
#: explanation has to account for. A model output with no probability has nothing
#: here to decompose, and the module says so rather than substituting its margin.
EXPLAINED_PROBABILITY = "probability"

#: Where an input value came from. The distinction is carried on every
#: attribution, because "this feature raised the risk" means something different
#: when the feature is a measurement, something else again when it is a simulation
#: output, and something different still when it was invented by the imputer.
SOURCE_TWIN = "digital_twin_state"
SOURCE_PHYSICS = "physical_simulation"
SOURCE_IMPUTED = "imputed_from_training_rows"
SOURCE_UNKNOWN = "unknown"

#: The source string Module 06's mass balance writes into a feature row. Matched
#: here rather than re-derived, so the label follows the module that produced it.
PHYSICS_SOURCE = "module_06.mass_balance"


def classify_source(feature: str, source: str | None, absent: bool) -> str:
    """Name what kind of value ``feature`` held in the row that was explained.

    ``absent`` is the row's own record of a gap: a column the twin could not
    supply is left empty and imputed by the model's fitted preprocessor, so a
    contribution attributed to it describes the imputer rather than the pan, and
    saying so is part of the attribution.
    """
    if absent:
        return SOURCE_IMPUTED
    if source == PHYSICS_SOURCE:
        return SOURCE_PHYSICS
    if source:
        return SOURCE_TWIN
    return SOURCE_UNKNOWN


class FeatureAttribution(BaseModel):
    """One feature's contribution to the decomposed output.

    ``shap_value`` is required. An attribution with no value is not an attribution,
    and a model that cannot supply one is reported as unavailable rather than
    written with a null in this field.
    """

    model_config = {"extra": "ignore"}

    feature: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("feature", "feature_name"),
    )
    #: The row's own value for this feature, before the preprocessor touched it.
    #: ``None`` for an imputed column, because the row held nothing and the
    #: imputer's median is the model's own and is not reported as a measurement.
    feature_value: float | None = Field(
        default=None, validation_alias=AliasChoices("feature_value", "value")
    )
    shap_value: float = Field(allow_inf_nan=False)
    #: The provenance string the input row recorded, verbatim: ``twin.<field>``,
    #: ``twin.as_of`` or ``module_06.mass_balance``. Null when the row recorded
    #: none, which is the case for a column it could not fill.
    source: str | None = Field(default=None, max_length=128)
    source_kind: str = Field(default=SOURCE_UNKNOWN, max_length=64)
    imputed: bool = False


class Explanation(BaseDocument):
    """A stored SHAP attribution, linked to the prediction and the model version."""

    explanation_id: str = Field(
        default_factory=lambda: new_id("exp"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    prediction_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        validation_alias=AliasChoices("prediction_id", "ml_prediction_id"),
    )
    pan_id: str | None = Field(default=None, min_length=1, max_length=64)
    #: Both halves of the model's identity. ``model_version`` alone is a string
    #: several models share, so an attribution stored without the model id cannot
    #: be traced back to the artifact that produced it.
    model_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
    )
    model_version: str = Field(min_length=1, max_length=64)
    kind: str = Field(default=KIND_LOCAL, max_length=16)
    timestamp: UtcDateTime = Field(default_factory=utcnow)
    created_at: UtcDateTime = Field(default_factory=utcnow)
    #: Which class's probability was decomposed, for a local explanation. Null for
    #: a global summary, where each row contributed the class it predicted.
    target_class: str | None = Field(default=None, max_length=128)
    #: What the contributions decompose. Never anything but a probability here; the
    #: field exists so a reader never has to infer the units from the numbers.
    explained_output: str = Field(default=EXPLAINED_PROBABILITY, max_length=64)
    #: The model's own value for the decomposed output: the probability the
    #: prediction stored. Additivity is checked against this and the residual is
    #: stored below, so the two cannot drift apart unnoticed.
    model_output: float | None = Field(default=None, allow_inf_nan=False)
    #: The model's expected value over the reference distribution. Every
    #: contribution is "how much this feature moved the output away from here".
    base_value: float | None = Field(default=None, allow_inf_nan=False)
    #: ``|model_output - (base_value + Σ shap_value)|``. Zero for an exact
    #: explainer, small but non-zero for a sampled one. Stored rather than
    #: discarded so a reader can see how well the numbers add up.
    attribution_error: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    method: str = Field(default="unrecorded", max_length=64)
    #: True when the explainer estimates the Shapley values by sampling rather
    #: than computing them. Reported, not hidden: an approximation presented as an
    #: exact attribution is the failure this module exists to avoid.
    approximate: bool = False
    shap_version: str = Field(default="unrecorded", max_length=32)
    #: The reference distribution the values are measured against: where its rows
    #: came from, how many, and how wide.
    background: dict[str, Any] = Field(default_factory=dict)
    #: How many rows a global summary covered.
    rows_explained: int | None = Field(default=None, ge=0)
    #: The content hash of the input snapshot this explains, for a local one. The
    #: same id the prediction carries, so the attribution and the number it
    #: accounts for are verifiably about the same row.
    input_snapshot_id: str | None = Field(default=None, max_length=128)
    attributions: list[FeatureAttribution] = Field(default_factory=list)
    is_simulated: bool = False

    @model_validator(mode="after")
    def _local_explanations_name_their_prediction(self) -> "Explanation":
        if self.kind == KIND_LOCAL and not self.prediction_id:
            raise ValueError(
                "A local explanation must name the prediction it explains. An "
                "attribution that cannot be traced to one number cannot be checked "
                "against it."
            )
        return self

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        if value is None:
            return None
        return normalize_pan_id(value)

    @field_validator("prediction_id", mode="before")
    @classmethod
    def _normalize_prediction_id(cls, value):
        if value is None:
            return None
        return normalize_identifier(value)
