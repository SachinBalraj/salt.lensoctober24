"""Module 3 - a single operator-entered reading of a pan's current condition.

An observation is a measurement, so it is stored exactly as reported. Nothing
is inferred: a field the operator did not measure stays ``None`` and is
rendered as missing rather than as zero, a typical value, or a carried-forward
previous reading.
"""

from pydantic import AliasChoices, Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_pan_id,
    utcnow,
)
from app.models.enums import ResearchDataSource

#: Shared shape rule for the pan identifier. Module 2 owns registration, but
#: observations reference pans by the same key, so the rule is defined once.
PAN_ID_PATTERN = r"^[A-Za-z0-9._-]+$"

#: Physical plausibility limits used to catch mistyped entries.
#:
#: These are deliberately far wider than any plausible salt-pan operating
#: range. They exist to reject data-entry mistakes such as ``28500`` g/L, not
#: to police genuine measurements, and they are shared with the HTTP schema so
#: the form and the API agree on what is acceptable.
SALINITY_RANGE = {"ge": 0.0, "le": 500.0}
WATER_DEPTH_RANGE = {"ge": 0.0, "le": 1_000.0}
BRINE_TEMPERATURE_RANGE = {"ge": -20.0, "le": 120.0}
HUMIDITY_RANGE = {"ge": 0.0, "le": 100.0}
AMBIENT_TEMPERATURE_RANGE = {"ge": -50.0, "le": 80.0}


class PanObservation(BaseDocument):
    observation_id: str = Field(
        default_factory=lambda: new_id("obs"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    pan_id: str = Field(
        min_length=1, max_length=64, pattern=PAN_ID_PATTERN
    )
    timestamp: UtcDateTime
    salinity_g_l: float | None = Field(
        default=None, allow_inf_nan=False, **SALINITY_RANGE
    )
    water_depth_cm: float | None = Field(
        default=None, allow_inf_nan=False, **WATER_DEPTH_RANGE
    )
    brine_temperature_c: float | None = Field(
        default=None, allow_inf_nan=False, **BRINE_TEMPERATURE_RANGE
    )
    humidity_percent: float | None = Field(
        default=None, allow_inf_nan=False, **HUMIDITY_RANGE
    )
    ambient_temperature_c: float | None = Field(
        default=None, allow_inf_nan=False, **AMBIENT_TEMPERATURE_RANGE
    )
    data_source: ResearchDataSource = ResearchDataSource.MANUAL
    observer: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Who took the reading, if the record names them. Left unset rather "
            "than guessed: an unattributed reading stays unattributed."
        ),
    )
    evidence_source: str | None = Field(
        default=None,
        max_length=300,
        description=(
            "Where this reading is documented, e.g. 'North Pan field notebook'. "
            "This is provenance, not verification: a reading with a known source "
            "is still not a second, independent source."
        ),
    )
    created_at: UtcDateTime = Field(
        default_factory=utcnow,
        validation_alias=AliasChoices("created_at", "recorded_at"),
    )
    note: str | None = Field(default=None, max_length=500)

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        return normalize_pan_id(value)

    @model_validator(mode="after")
    def _require_a_measurement(self) -> "PanObservation":
        measurements = (
            self.salinity_g_l,
            self.water_depth_cm,
            self.brine_temperature_c,
            self.humidity_percent,
            self.ambient_temperature_c,
        )
        if all(value is None for value in measurements):
            raise ValueError(
                "an observation must contain at least one measured value"
            )
        return self


#: Fields that make up a pan's latest observed state, in display order.
MEASUREMENT_FIELDS: tuple[str, ...] = (
    "salinity_g_l",
    "water_depth_cm",
    "brine_temperature_c",
    "humidity_percent",
    "ambient_temperature_c",
)
