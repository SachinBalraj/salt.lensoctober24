from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_pan_id,
    utcnow,
)
from app.models.enums import SimulationStatus


class SimulationRun(BaseDocument):
    run_id: str = Field(
        default_factory=lambda: new_id("run"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    pan_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    status: SimulationStatus = SimulationStatus.QUEUED
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    rainfall_cm: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    is_simulated: Literal[True] = True
    created_at: UtcDateTime = Field(default_factory=utcnow)

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        return normalize_pan_id(value)

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> "SimulationRun":
        if self.status == SimulationStatus.SUCCEEDED and self.finished_at is None:
            raise ValueError("a succeeded simulation must record finished_at")
        if (
            self.started_at is not None
            and self.finished_at is not None
            and self.finished_at < self.started_at
        ):
            raise ValueError("finished_at cannot be earlier than started_at")
        return self

    @property
    def timestamp(self) -> UtcDateTime:
        return self.created_at
