"""Module 5 - the current known state of one salt pan.

A digital twin here is not a simulation. It is a *joining* of three things that
were actually recorded: the newest pan observation, the newest relevant weather
record, and the pan's static registration details. It performs no physics and no
interpolation, because a plausible-looking number that nobody measured is worse
than an admitted gap.

Three rules follow from that, and the rest of the module enforces them.

**Absence is reported, never filled.** Every quantity on the twin is a copy of a
stored value. If no input carried a figure, the twin field is ``None`` and the
reason is appended to :attr:`DigitalTwinState.missing_inputs`, so a consumer can
tell "measured as zero" from "never measured".

**Provenance travels with the value.** A figure taken from a hand-entered
observation is not the same claim as one retrieved from a weather API, so the
twin records the source of each input separately. That is what stops a manual
entry from being quoted later as a machine reading.

**Assembly time is distinct from input time.** ``as_of`` is when the twin was
assembled; the three input timestamps say how old the evidence actually is. A
twin can be minutes old and still be built on readings that are days old, and
the two facts must not be conflated.
"""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_pan_id,
    utcnow,
)
from app.models.enums import ResearchDataSource, TwinProvenance, WeatherSource
from app.models.pan_observation import (
    AMBIENT_TEMPERATURE_RANGE,
    BRINE_TEMPERATURE_RANGE,
    HUMIDITY_RANGE,
    PAN_ID_PATTERN,
)
from app.models.weather import RAINFALL_RANGE

#: The quantities a twin must obtain from a real observation or forecast.
#:
#: These are the fields that can legitimately be unavailable. The pan's static
#: properties (``pan_area_m2``, ``location_name``) are absent from this list
#: because a twin only exists for a registered pan, so they are always present.
TWIN_QUANTITIES: tuple[str, ...] = (
    "estimated_salinity_g_l",
    "water_depth_cm",
    "brine_temperature_c",
    "ambient_temperature_c",
    "humidity_percent",
    "forecast_rainfall_mm",
    "rainfall_probability_percent",
)

#: Where a quantity is expected to come from, used to explain a gap.
EXPECTED_INPUT: dict[str, str] = {
    "estimated_salinity_g_l": "observation",
    "water_depth_cm": "observation",
    "brine_temperature_c": "observation",
    "ambient_temperature_c": "weather",
    "humidity_percent": "weather",
    "forecast_rainfall_mm": "weather_forecast",
    "rainfall_probability_percent": "weather_forecast",
}

#: Stored field names that differ from the name the HTTP contract exposes.
#:
#: A gap is reported against the same name the value itself is published under,
#: so a consumer can look a field up in one place and find it in the other.
PUBLIC_FIELD_NAMES: dict[str, str] = {
    "estimated_salinity_g_l": "salinity_g_l",
    "location_name": "location",
}


def public_field_name(field: str) -> str:
    """The name a field is published under over HTTP."""
    return PUBLIC_FIELD_NAMES.get(field, field)


class MissingTwinInput(BaseModel):
    """One requested quantity that no stored record supplied.

    Reported rather than raised. A pan whose operator has not yet measured
    anything is a normal state on day one, not an error, and the twin has to be
    renderable in that state.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    field: str = Field(
        min_length=1,
        max_length=64,
        description="Which twin quantity is unavailable.",
    )
    reason: str = Field(
        min_length=1,
        max_length=300,
        description="Why it is unavailable, in a sentence an operator can act on.",
    )
    expected_from: str = Field(
        min_length=1,
        max_length=32,
        description=(
            "The kind of record that would supply it: 'observation', 'weather' or "
            "'weather_forecast'."
        ),
    )


class DigitalTwinState(BaseDocument):
    twin_id: str = Field(
        default_factory=lambda: new_id("twin"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    pan_id: str = Field(min_length=1, max_length=64, pattern=PAN_ID_PATTERN)
    as_of: UtcDateTime = Field(validation_alias=AliasChoices("as_of", "timestamp"))
    provenance: TwinProvenance = TwinProvenance.OBSERVED
    twin_version: str = Field(default="0.1.0", min_length=1, max_length=64)
    brine_volume_m3: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    brine_mass_kg: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    estimated_salinity_g_l: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        validation_alias=AliasChoices("estimated_salinity_g_l", "salinity_g_l"),
    )
    water_depth_cm: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    estimated_crust_cm: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    created_at: UtcDateTime = Field(
        default_factory=utcnow,
        validation_alias=AliasChoices("created_at", "recorded_at"),
    )

    # -- pan condition, copied from the newest observation -----------------

    brine_temperature_c: float | None = Field(
        default=None, allow_inf_nan=False, **BRINE_TEMPERATURE_RANGE
    )

    # -- ambient condition and forecast, copied from weather ---------------

    ambient_temperature_c: float | None = Field(
        default=None, allow_inf_nan=False, **AMBIENT_TEMPERATURE_RANGE
    )
    humidity_percent: float | None = Field(
        default=None, allow_inf_nan=False, **HUMIDITY_RANGE
    )
    forecast_rainfall_mm: float | None = Field(
        default=None, allow_inf_nan=False, **RAINFALL_RANGE
    )
    rainfall_probability_percent: float | None = Field(
        default=None, allow_inf_nan=False, **HUMIDITY_RANGE
    )

    # -- static pan properties --------------------------------------------

    pan_area_m2: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    location_name: str | None = Field(
        default=None,
        max_length=128,
        validation_alias=AliasChoices("location_name", "location"),
    )

    # -- provenance of each input -----------------------------------------

    observation_id: str | None = Field(default=None, max_length=64)
    observation_timestamp: UtcDateTime | None = None
    observation_data_source: ResearchDataSource | None = None

    weather_id: str | None = Field(default=None, max_length=64)
    weather_timestamp: UtcDateTime | None = None
    weather_source: WeatherSource | None = None

    weather_forecast_id: str | None = Field(default=None, max_length=64)
    weather_forecast_timestamp: UtcDateTime | None = None
    weather_forecast_source: WeatherSource | None = None

    #: Weather records skipped because they were forecasts rather than
    #: observations, or because they were the wrong kind of record for a field.
    #: Kept so a gap can be explained rather than merely reported.
    missing_inputs: list[MissingTwinInput] = Field(default_factory=list)

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        return normalize_pan_id(value)

    @property
    def timestamp(self) -> UtcDateTime:
        return self.as_of

    @property
    def location(self) -> str | None:
        return self.location_name

    @property
    def salinity_g_l(self) -> float | None:
        return self.estimated_salinity_g_l

    @property
    def missing_fields(self) -> list[str]:
        """The unavailable quantities, for a caller that only needs the names."""
        return [entry.field for entry in self.missing_inputs]

    @property
    def is_complete(self) -> bool:
        """True when every requested quantity was supplied by a real record."""
        return not self.missing_fields

    def value_of(self, field: str) -> float | None:
        return getattr(self, field, None)


__all__ = [
    "EXPECTED_INPUT",
    "PUBLIC_FIELD_NAMES",
    "TWIN_QUANTITIES",
    "DigitalTwinState",
    "MissingTwinInput",
    "public_field_name",
]
