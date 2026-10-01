"""Module 3 - operator-facing request and response payloads for observations.

These schemas are the HTTP contract for manual data entry. They are narrower
than the persistence model in one important way: they refuse a ``sensor``
source unless a real gateway is enabled, so the API cannot be used to file a
reading that no instrument produced.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ResearchDataSource
from app.models.pan_observation import (
    AMBIENT_TEMPERATURE_RANGE,
    BRINE_TEMPERATURE_RANGE,
    HUMIDITY_RANGE,
    MEASUREMENT_FIELDS,
    PAN_ID_PATTERN,
    SALINITY_RANGE,
    WATER_DEPTH_RANGE,
    PanObservation,
)

#: Readings may be backdated, but never dated forward: the future has not been
#: observed yet. Five minutes of slack absorbs ordinary client clock skew.
MAX_FUTURE_SKEW = timedelta(minutes=5)

SENSOR_UNAVAILABLE_MESSAGE = (
    "Sensor ingest is not available: no sensor gateway is connected to this "
    "deployment. Record the reading as manual or imported instead."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PanObservationCreate(BaseModel):
    """One reading entered by an operator."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    pan_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=PAN_ID_PATTERN,
        description="The pan this reading belongs to. Matched case-insensitively.",
        examples=["pans-2026-a01"],
    )
    timestamp: datetime | None = Field(
        default=None,
        description=(
            "When the reading was taken, ISO 8601. Defaults to the moment the "
            "reading is saved. Supply a value to backdate an entry."
        ),
        examples=["2026-01-05T09:30:00Z"],
    )
    salinity_g_l: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **SALINITY_RANGE,
        description="Brine salinity in grams per litre. Leave blank if not measured.",
    )
    water_depth_cm: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **WATER_DEPTH_RANGE,
        description="Current water depth in centimetres. Leave blank if not measured.",
    )
    brine_temperature_c: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **BRINE_TEMPERATURE_RANGE,
        description="Brine temperature in degrees Celsius. Leave blank if not measured.",
    )
    humidity_percent: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **HUMIDITY_RANGE,
        description="Relative humidity, 0 to 100. Leave blank if not measured.",
    )
    ambient_temperature_c: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **AMBIENT_TEMPERATURE_RANGE,
        description="Ambient air temperature in degrees Celsius. Leave blank if not measured.",
    )
    data_source: ResearchDataSource = Field(
        default=ResearchDataSource.MANUAL,
        description="How this reading reached SaltLens. `sensor` requires a connected gateway.",
    )
    observer: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "Who took the reading, when the record names them. Leave blank rather "
            "than guessing: an unattributed reading stays unattributed."
        ),
    )
    evidence_source: str | None = Field(
        default=None,
        max_length=300,
        description=(
            "Where the reading is documented, e.g. 'North Pan field notebook'. "
            "This records provenance; it does not make the reading independently "
            "verified."
        ),
    )
    note: str | None = Field(
        default=None,
        max_length=500,
        description="Optional operator remark, e.g. why a field could not be read.",
    )

    @model_validator(mode="after")
    def _require_a_measurement(self) -> PanObservationCreate:
        if all(getattr(self, field) is None for field in MEASUREMENT_FIELDS):
            raise ValueError(
                "Record at least one measured value. A reading with no measurements "
                "cannot be charted and is not evidence of the pan's condition."
            )
        return self

    @model_validator(mode="after")
    def _reject_future_timestamp(self) -> PanObservationCreate:
        if self.timestamp is not None and self.timestamp > _now() + MAX_FUTURE_SKEW:
            raise ValueError(
                "timestamp cannot be in the future. A reading describes a moment "
                "that has already happened."
            )
        return self

    def resolved_timestamp(self) -> datetime:
        return self.timestamp if self.timestamp is not None else _now()


class PanObservationRead(BaseModel):
    """A stored reading, exactly as it was recorded."""

    model_config = ConfigDict(from_attributes=True)

    observation_id: str
    pan_id: str
    timestamp: datetime
    salinity_g_l: float | None
    water_depth_cm: float | None
    brine_temperature_c: float | None
    humidity_percent: float | None
    ambient_temperature_c: float | None
    data_source: ResearchDataSource
    observer: str | None
    evidence_source: str | None
    note: str | None
    created_at: datetime

    @classmethod
    def from_observation(cls, observation: PanObservation) -> PanObservationRead:
        return cls(
            observation_id=observation.observation_id,
            pan_id=observation.pan_id,
            timestamp=observation.timestamp,
            salinity_g_l=observation.salinity_g_l,
            water_depth_cm=observation.water_depth_cm,
            brine_temperature_c=observation.brine_temperature_c,
            humidity_percent=observation.humidity_percent,
            ambient_temperature_c=observation.ambient_temperature_c,
            data_source=observation.data_source,
            observer=observation.observer,
            evidence_source=observation.evidence_source,
            note=observation.note,
            created_at=observation.created_at,
        )


class PanObservationListResponse(BaseModel):
    """A page of readings, newest first."""

    items: list[PanObservationRead] = Field(default_factory=list)
    total: int = Field(ge=0, description="Readings matching the filter, ignoring paging.")
    limit: int = Field(ge=1, le=1000)
    offset: int = Field(ge=0)
    earliest_timestamp: datetime | None = Field(
        default=None, description="Oldest reading in the filtered set."
    )
    latest_timestamp: datetime | None = Field(
        default=None, description="Newest reading in the filtered set."
    )


__all__ = [
    "MAX_FUTURE_SKEW",
    "SENSOR_UNAVAILABLE_MESSAGE",
    "PanObservationCreate",
    "PanObservationListResponse",
    "PanObservationRead",
]
