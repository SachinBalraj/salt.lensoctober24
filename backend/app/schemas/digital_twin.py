"""Module 5 - the HTTP contract for a pan's digital twin.

The read schema is deliberately wider than the model: it adds the *ages* of the
inputs, because "how old is this evidence" is the first question an operator
asks of a twin and it cannot be answered from the document alone. Ages are
computed per response rather than stored, so a snapshot read an hour later
reports the age at that moment instead of a figure frozen at assembly time.

The response also carries ``missing_inputs`` verbatim. A consumer that wanted to
treat an absent salinity as zero now has to look at ``is_complete`` instead,
which is the point.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from app.models.digital_twin_state import (
    EXPECTED_INPUT,
    TWIN_QUANTITIES,
    DigitalTwinState,
    MissingTwinInput,
    public_field_name,
)
from app.models.enums import ResearchDataSource, TwinProvenance, WeatherSource


def _age_seconds(moment: datetime | None, now: datetime) -> float | None:
    """Seconds elapsed since ``moment``, or None when there is no moment.

    Clamped at zero: a clock skew between the writer and this process can put an
    input fractionally in the future, and reporting a negative age would be a
    confusing way to say "just recorded".
    """
    if moment is None:
        return None
    return max(0.0, (now - moment).total_seconds())


class TwinInputRead(BaseModel):
    """The provenance and age of one record the twin was built from."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = None
    timestamp: datetime | None = None
    source: str | None = Field(
        default=None,
        description=(
            "How that record reached SaltLens. null when no record of this kind "
            "was found."
        ),
    )
    age_seconds: float | None = Field(
        default=None,
        ge=0,
        description="Seconds between that record and this response. null when absent.",
    )


class DigitalTwinStateRead(BaseModel):
    """A pan's current known state, with everything it is based on made visible."""

    model_config = ConfigDict(from_attributes=True)

    twin_id: str
    pan_id: str
    pan_name: str | None = None

    #: When this state was assembled. Not the age of the evidence.
    timestamp: datetime
    provenance: TwinProvenance
    twin_version: str

    salinity_g_l: float | None = None
    water_depth_cm: float | None = None
    brine_temperature_c: float | None = None
    ambient_temperature_c: float | None = None
    humidity_percent: float | None = None
    pan_area_m2: float | None = None
    location: str | None = None
    forecast_rainfall_mm: float | None = None
    rainfall_probability_percent: float | None = None

    observation: TwinInputRead | None = Field(
        default=None, description="The pan observation this state was built from."
    )
    weather: TwinInputRead | None = Field(
        default=None,
        description="The observed weather record this state was built from.",
    )
    weather_forecast: TwinInputRead | None = Field(
        default=None,
        description="The forecast weather record this state was built from.",
    )

    is_complete: bool = Field(
        description="True when every requested quantity came from a real record."
    )
    missing_inputs: list[MissingTwinInput] = Field(default_factory=list)
    missing_fields: list[str] = Field(
        default_factory=list, description="Names of the unavailable quantities."
    )
    quantities: list[str] = Field(
        default_factory=lambda: [public_field_name(name) for name in TWIN_QUANTITIES],
        description="Every quantity the twin attempts to supply, available or not.",
    )

    @classmethod
    def from_state(
        cls,
        state: DigitalTwinState,
        *,
        pan_name: str | None = None,
        now: datetime | None = None,
    ) -> DigitalTwinStateRead:
        """Project a stored twin into the response contract.

        ``now`` is injectable so the age arithmetic is testable without freezing
        the clock.
        """
        moment = now if now is not None else datetime.now(timezone.utc)
        return cls(
            twin_id=state.twin_id,
            pan_id=state.pan_id,
            pan_name=pan_name,
            timestamp=state.as_of,
            provenance=state.provenance,
            twin_version=state.twin_version,
            salinity_g_l=state.estimated_salinity_g_l,
            water_depth_cm=state.water_depth_cm,
            brine_temperature_c=state.brine_temperature_c,
            ambient_temperature_c=state.ambient_temperature_c,
            humidity_percent=state.humidity_percent,
            pan_area_m2=state.pan_area_m2,
            location=state.location_name,
            forecast_rainfall_mm=state.forecast_rainfall_mm,
            rainfall_probability_percent=state.rainfall_probability_percent,
            observation=_input(
                state.observation_id,
                state.observation_timestamp,
                state.observation_data_source,
                moment,
            ),
            weather=_input(
                state.weather_id,
                state.weather_timestamp,
                state.weather_source,
                moment,
            ),
            weather_forecast=_input(
                state.weather_forecast_id,
                state.weather_forecast_timestamp,
                state.weather_forecast_source,
                moment,
            ),
            is_complete=state.is_complete,
            missing_inputs=[
                entry.model_copy(update={"field": public_field_name(entry.field)})
                for entry in state.missing_inputs
            ],
            missing_fields=[public_field_name(name) for name in state.missing_fields],
        )


def _input(
    record_id: str | None,
    timestamp: datetime | None,
    source: ResearchDataSource | WeatherSource | None,
    now: datetime,
) -> TwinInputRead | None:
    """Describe one contributing record, or None when there was not one."""
    if record_id is None and timestamp is None and source is None:
        return None
    return TwinInputRead(
        id=record_id,
        timestamp=timestamp,
        source=source.value if source is not None else None,
        age_seconds=_age_seconds(timestamp, now),
    )


class DigitalTwinHistoryResponse(BaseModel):
    """Stored twin snapshots for one pan, newest first."""

    pan_id: str
    items: list[DigitalTwinStateRead] = Field(default_factory=list)
    total: int = Field(ge=0, description="Snapshots stored for this pan.")
    limit: int = Field(ge=1, le=1000)
    offset: int = Field(ge=0)
    earliest_timestamp: datetime | None = None
    latest_timestamp: datetime | None = None


class DigitalTwinRefreshResponse(BaseModel):
    """The outcome of reassembling and storing a twin snapshot."""

    state: DigitalTwinStateRead
    stored: bool = Field(
        description="Whether a new snapshot document was written."
    )
    changed: bool = Field(
        description=(
            "Whether this snapshot differs from the previous one. False means the "
            "inputs have not moved, so the change log has no new entry."
        ),
    )
    previous_twin_id: str | None = Field(
        default=None, description="The snapshot this one was compared against."
    )


__all__ = [
    "EXPECTED_INPUT",
    "TWIN_QUANTITIES",
    "DigitalTwinHistoryResponse",
    "DigitalTwinRefreshResponse",
    "DigitalTwinStateRead",
    "MissingTwinInput",
    "TwinInputRead",
]
