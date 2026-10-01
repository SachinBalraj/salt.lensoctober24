"""Module 4 - a weather reading for one salt pan, observed or forecast.

A weather record is evidence about the sky over a pan, so two things matter
more than convenience.

First, provenance. ``source`` is part of the document, not a display hint, and
a record entered by an operator carries ``manual`` forever. Nothing in this
module can turn a manual entry into an API-sourced one.

Second, absence. A field nobody measured or nobody forecast stays ``None``.
A forecast of ``0`` mm is a real prediction of no rain and is stored as ``0``;
a missing rainfall figure is stored as ``null``. The two are not interchangeable
and the model keeps them apart.
"""

from typing import Any

from pydantic import AliasChoices, Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_pan_id,
    utcnow,
)
from app.models.enums import WeatherSource
from app.models.pan_observation import (
    AMBIENT_TEMPERATURE_RANGE,
    HUMIDITY_RANGE,
    PAN_ID_PATTERN,
)

#: Upper bound on a single rainfall total, in millimetres.
#:
#: Far beyond any plausible event, so it exists to catch a unit mistake such as
#: centimetres or inches entered into a millimetre field.
RAINFALL_RANGE = {"ge": 0.0, "le": 1_000.0}

#: Fields that carry an actual measured or predicted quantity.
WEATHER_MEASUREMENT_FIELDS: tuple[str, ...] = (
    "rainfall_mm",
    "rainfall_probability_percent",
    "ambient_temperature_c",
    "humidity_percent",
)


class WeatherObservation(BaseDocument):
    weather_id: str = Field(
        default_factory=lambda: new_id("wx"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        validation_alias=AliasChoices("weather_id", "observation_id"),
    )
    pan_id: str = Field(min_length=1, max_length=64, pattern=PAN_ID_PATTERN)

    #: When the reading was taken, or when the forecast was retrieved.
    timestamp: UtcDateTime = Field(
        validation_alias=AliasChoices("timestamp", "observed_at"),
    )

    #: The instant the forecast or rainfall event applies to.
    forecast_for: UtcDateTime | None = None

    rainfall_mm: float | None = Field(default=None, allow_inf_nan=False, **RAINFALL_RANGE)
    rainfall_probability_percent: float | None = Field(
        default=None, allow_inf_nan=False, **HUMIDITY_RANGE
    )
    ambient_temperature_c: float | None = Field(
        default=None, allow_inf_nan=False, **AMBIENT_TEMPERATURE_RANGE
    )
    humidity_percent: float | None = Field(
        default=None, allow_inf_nan=False, **HUMIDITY_RANGE
    )

    source: WeatherSource = WeatherSource.MANUAL
    is_forecast: bool = False
    created_at: UtcDateTime = Field(
        default_factory=utcnow,
        validation_alias=AliasChoices("created_at", "recorded_at"),
    )

    @model_validator(mode="before")
    @classmethod
    def _infer_forecast_flag(cls, value: Any) -> Any:
        """A record that names a forecast instant is a forecast.

        The flag is derived rather than supplied so the two can never disagree:
        a caller cannot file a forecast while claiming to have observed it.
        """
        if (
            isinstance(value, dict)
            and value.get("forecast_for") is not None
            and "is_forecast" not in value
        ):
            values = dict(value)
            values["is_forecast"] = True
            return values
        return value

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        return normalize_pan_id(value)

    @model_validator(mode="after")
    def _validate_forecast(self) -> "WeatherObservation":
        if self.is_forecast and self.forecast_for is None:
            raise ValueError("forecast rows require forecast_for")
        if not self.is_forecast and self.forecast_for is not None:
            raise ValueError("forecast_for requires is_forecast=true")
        if self.forecast_for is not None and self.forecast_for < self.timestamp:
            raise ValueError("forecast_for cannot be earlier than timestamp")
        return self

    @model_validator(mode="after")
    def _require_a_measurement(self) -> "WeatherObservation":
        """Refuse a record that says nothing.

        A row with every quantity null is indistinguishable from a bug in the
        caller and carries no information about the weather.
        """
        if all(
            getattr(self, field) is None for field in WEATHER_MEASUREMENT_FIELDS
        ):
            raise ValueError(
                "a weather observation must contain at least one measured or "
                "forecast value"
            )
        return self

    @property
    def observed_at(self) -> UtcDateTime:
        return self.timestamp

    @property
    def recorded_at(self) -> UtcDateTime:
        return self.created_at

    @property
    def is_observed(self) -> bool:
        """True when this record reports what happened, not what was predicted."""
        return not self.is_forecast
