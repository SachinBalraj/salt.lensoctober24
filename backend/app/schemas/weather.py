"""Module 4 - request and response payloads for weather data.

The create schema exists for manual entry and says so. ``source`` accepts
``manual`` and ``sensor`` only: ``weather_api`` is rejected here with a 422 that
explains why, because the forecast ingest route is the only thing allowed to
write that provenance, and it writes it after a registered provider has actually
returned data. A record an operator typed is never filed as machine-retrieved.

Fields left blank are stored as ``None``. A forecast of ``0`` mm is a real
prediction of no rain and is kept distinct from a missing rainfall figure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import WeatherSource
from app.models.pan_observation import (
    AMBIENT_TEMPERATURE_RANGE,
    HUMIDITY_RANGE,
    PAN_ID_PATTERN,
)
from app.models.weather import (
    RAINFALL_RANGE,
    WEATHER_MEASUREMENT_FIELDS,
    WeatherObservation,
)

#: A weather reading may be backdated, never dated forward: the sky at an instant
#: that has not happened yet cannot be observed. Five minutes of slack absorbs
#: ordinary client clock skew.
MAX_FUTURE_SKEW = timedelta(minutes=5)

#: How far ahead a forecast may be aimed. Far beyond any operational forecast
#: range, so it exists to catch a mistyped year rather than to police providers.
MAX_FORECAST_HORIZON = timedelta(days=14)

SENSOR_UNAVAILABLE_MESSAGE = (
    "Sensor ingest is not available: no weather sensor gateway is connected to "
    "this deployment. Record the reading as manual instead."
)

WEATHER_API_SOURCE_REFUSED = (
    "source cannot be weather_api on this endpoint. That provenance is set by the "
    "forecast ingest route after a registered provider returns data, so a record "
    "cannot claim an automated origin that never fetched it. Record the reading as "
    "manual instead."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WeatherObservationCreate(BaseModel):
    """One weather reading entered by an operator."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    pan_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=PAN_ID_PATTERN,
        description="The pan this reading describes. Matched case-insensitively.",
        examples=["pans-2026-a01"],
    )
    timestamp: datetime | None = Field(
        default=None,
        description=(
            "When the reading was taken, ISO 8601. Defaults to the moment the "
            "reading is saved. For an observed event this is the event time; supply "
            "a value to backdate an entry."
        ),
        examples=["2026-01-05T09:30:00Z"],
    )
    forecast_for: datetime | None = Field(
        default=None,
        description=(
            "For a forecast, the instant it applies to. Must not precede "
            "timestamp. Leave blank when recording what actually happened, in "
            "which case timestamp is the event time."
        ),
        examples=["2026-01-06T06:00:00Z"],
    )
    rainfall_mm: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **RAINFALL_RANGE,
        description=(
            "Forecast or observed rainfall in millimetres. 0 means no rain, which "
            "is different from leaving this blank. Leave blank if unknown."
        ),
    )
    rainfall_probability_percent: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **HUMIDITY_RANGE,
        description="Chance of rain, 0 to 100. Leave blank if not known.",
    )
    ambient_temperature_c: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **AMBIENT_TEMPERATURE_RANGE,
        description="Ambient air temperature in degrees Celsius. Leave blank if not measured.",
    )
    humidity_percent: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **HUMIDITY_RANGE,
        description="Relative humidity, 0 to 100. Leave blank if not measured.",
    )
    source: WeatherSource = Field(
        default=WeatherSource.MANUAL,
        description=(
            "How this reading reached SaltLens. Manual entry accepts `manual` or "
            "`sensor`; `weather_api` is reserved for the forecast ingest route."
        ),
    )

    @field_validator("source")
    @classmethod
    def _refuse_api_source(cls, value: WeatherSource) -> WeatherSource:
        if value is WeatherSource.WEATHER_API:
            raise ValueError(WEATHER_API_SOURCE_REFUSED)
        return value

    @model_validator(mode="after")
    def _require_a_measurement(self) -> WeatherObservationCreate:
        if all(getattr(self, field) is None for field in WEATHER_MEASUREMENT_FIELDS):
            raise ValueError(
                "Record at least one measured or forecast value. A weather record "
                "with no values carries no information about the weather."
            )
        return self

    @model_validator(mode="after")
    def _validate_timestamps(self) -> WeatherObservationCreate:
        if self.timestamp is not None and self.timestamp > _now() + MAX_FUTURE_SKEW:
            raise ValueError(
                "timestamp cannot be in the future. A reading describes a moment "
                "that has already happened."
            )
        if self.forecast_for is None:
            return self
        if self.forecast_for < (self.timestamp or _now()):
            raise ValueError(
                "forecast_for cannot be earlier than timestamp. A forecast made "
                "after the instant it describes is not a forecast."
            )
        if self.forecast_for > _now() + MAX_FORECAST_HORIZON:
            raise ValueError(
                f"forecast_for cannot be more than {MAX_FORECAST_HORIZON.days} days "
                "ahead. Check the year before saving."
            )
        return self

    def resolved_timestamp(self) -> datetime:
        return self.timestamp if self.timestamp is not None else _now()


class WeatherObservationRead(BaseModel):
    """A stored weather record, exactly as it was recorded."""

    model_config = ConfigDict(from_attributes=True)

    weather_id: str
    pan_id: str
    timestamp: datetime
    forecast_for: datetime | None
    rainfall_mm: float | None
    rainfall_probability_percent: float | None
    ambient_temperature_c: float | None
    humidity_percent: float | None
    source: WeatherSource
    is_forecast: bool
    created_at: datetime
    provider: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    weather_type: str | None = None
    requested_at: datetime | None = None
    retrieved_at: datetime | None = None

    @classmethod
    def from_observation(
        cls, observation: WeatherObservation
    ) -> WeatherObservationRead:
        return cls(
            weather_id=observation.weather_id,
            pan_id=observation.pan_id,
            timestamp=observation.timestamp,
            forecast_for=observation.forecast_for,
            rainfall_mm=observation.rainfall_mm,
            rainfall_probability_percent=observation.rainfall_probability_percent,
            ambient_temperature_c=observation.ambient_temperature_c,
            humidity_percent=observation.humidity_percent,
            source=observation.source,
            is_forecast=observation.is_forecast,
            created_at=observation.created_at,
            provider=getattr(observation, 'provider', None),
            latitude=getattr(observation, 'latitude', None),
            longitude=getattr(observation, 'longitude', None),
            weather_type=getattr(observation, 'weather_type', None),
            requested_at=getattr(observation, 'requested_at', None),
            retrieved_at=getattr(observation, 'retrieved_at', None),
        )


class WeatherObservationListResponse(BaseModel):
    """A page of weather records, newest first."""

    items: list[WeatherObservationRead] = Field(default_factory=list)
    total: int = Field(ge=0, description="Records matching the filter, ignoring paging.")
    limit: int = Field(ge=1, le=1000)
    offset: int = Field(ge=0)
    earliest_timestamp: datetime | None = Field(
        default=None, description="Oldest record in the filtered set."
    )
    latest_timestamp: datetime | None = Field(
        default=None, description="Newest record in the filtered set."
    )


class WeatherProviderRead(BaseModel):
    """A registered weather data source."""

    name: str = Field(description="Identifier used in the forecast ingest URL.")
    source: WeatherSource = Field(
        description="Provenance this provider's readings are stored under."
    )


class WeatherProvidersResponse(BaseModel):
    """Which weather APIs this deployment can actually reach."""

    registered: list[WeatherProviderRead] = Field(default_factory=list)
    count: int = Field(ge=0)
    manual_entry_available: bool
    detail: str


class WeatherIngestResponse(BaseModel):
    """The outcome of one forecast ingest call.

    ``rejected`` counts points the provider returned that SaltLens would not
    store, such as a forecast aimed at an instant that already passed. They are
    reported rather than silently dropped so a partial ingest stays visible.
    """

    provider: str
    source: WeatherSource
    pan_id: str
    requested_start: datetime
    requested_end: datetime
    stored: int = Field(ge=0)
    rejected: int = Field(ge=0)
    observations: list[WeatherObservationRead] = Field(default_factory=list)


__all__ = [
    "MAX_FORECAST_HORIZON",
    "MAX_FUTURE_SKEW",
    "SENSOR_UNAVAILABLE_MESSAGE",
    "WEATHER_API_SOURCE_REFUSED",
    "WeatherIngestResponse",
    "WeatherObservationCreate",
    "WeatherObservationListResponse",
    "WeatherObservationRead",
    "WeatherProviderRead",
    "WeatherProvidersResponse",
]
