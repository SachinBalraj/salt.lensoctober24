from typing import Any

from pydantic import Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_pan_id,
    utcnow,
)
from app.models.enums import MlTarget


class MLPrediction(BaseDocument):
    prediction_id: str = Field(
        default_factory=lambda: new_id("pred"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    pan_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    model_version: str = Field(min_length=1, max_length=64)
    #: Which registered model answered. Required for every prediction this project
    #: produces: ``model_version`` on its own is a string that several models share,
    #: so a prediction recorded without it cannot be traced to the artifact that
    #: made it. Nullable rather than required because historical records predate it.
    model_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
    )
    #: The deployment the model was answering under when it produced this value.
    #: Recorded rather than looked up later, because a deployed model is retired and
    #: replaced routinely, and a series of predictions has to remain readable
    #: afterwards as a series of decisions rather than collapsing into whatever is
    #: deployed today.
    deployment_id: str | None = Field(default=None, max_length=64)
    deployed_at: UtcDateTime | None = None
    target: MlTarget
    timestamp: UtcDateTime = Field(default_factory=utcnow)
    prediction: float | None = Field(default=None, allow_inf_nan=False)
    predicted_class: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "The class the model emitted, for a classification prediction. Kept "
            "beside `prediction` rather than inside `input_snapshot` because it is "
            "an output: a snapshot is the state the model was asked about, and "
            "mixing the answer into it would make the snapshot's id depend on the "
            "answer."
        ),
    )
    probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    input_snapshot_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "The content hash of `input_snapshot`. Two predictions made from the "
            "same state by the same model share an id, and a changed state changes "
            "it, so a stored prediction can be checked against the input it claims "
            "to have come from."
        ),
    )
    input_snapshot: dict[str, Any] = Field(default_factory=dict)
    created_at: UtcDateTime = Field(default_factory=utcnow)
    is_simulated: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize_target(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        values = dict(value)
        target = values.get("target")
        if target in {"salinity", "salinity_gl"}:
            values["target"] = MlTarget.SALINITY_G_L.value
        return values

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        return normalize_pan_id(value)


MlPrediction = MLPrediction
