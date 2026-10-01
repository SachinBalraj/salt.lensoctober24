from typing import Any

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.base import BaseDocument, UtcDateTime, normalize_pan_id, utcnow
from app.models.enums import ResearchDataSource


class RiskThresholds(BaseModel):
    model_config = {"extra": "allow", "validate_assignment": True}

    flood_depth_cm: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    dilution_salinity_g_l: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    critical_rainfall_24h_mm: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    target_flow_cm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    max_observation_age_hours: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _map_threshold_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        values = dict(value)
        aliases = {
            "max_depth_cm": "flood_depth_cm",
            "min_salinity_g_l": "dilution_salinity_g_l",
            "max_rainfall_24h_mm": "critical_rainfall_24h_mm",
            "flow_depth_cm": "target_flow_cm",
        }
        for source, target in aliases.items():
            if target not in values and source in values:
                values[target] = values[source]
            values.pop(source, None)
        return values


class LatestObservationState(BaseModel):
    """A denormalised copy of the most recent reading for a pan.

    Keeping the newest observation on the pan document means a registry list
    can show current condition without a lookup or aggregation per row. The
    authoritative copy always remains the observation in ``pan_observations``.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    observation_id: str = Field(min_length=1, max_length=64)
    timestamp: UtcDateTime
    salinity_g_l: float | None = None
    water_depth_cm: float | None = None
    brine_temperature_c: float | None = None
    humidity_percent: float | None = None
    ambient_temperature_c: float | None = None
    data_source: ResearchDataSource = ResearchDataSource.MANUAL

    @classmethod
    def from_observation(cls, observation: object) -> "LatestObservationState":
        return cls(
            observation_id=observation.observation_id,
            timestamp=observation.timestamp,
            salinity_g_l=observation.salinity_g_l,
            water_depth_cm=observation.water_depth_cm,
            brine_temperature_c=observation.brine_temperature_c,
            humidity_percent=observation.humidity_percent,
            ambient_temperature_c=observation.ambient_temperature_c,
            data_source=observation.data_source,
        )


class SaltPan(BaseDocument):
    pan_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    pan_name: str = Field(min_length=1, max_length=128)
    location_name: str = Field(
        min_length=1,
        max_length=128,
        validation_alias=AliasChoices("location_name", "location"),
    )
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    area_m2: float = Field(gt=0, allow_inf_nan=False)
    normal_depth_cm: float = Field(gt=0, allow_inf_nan=False)
    target_harvest_salinity_g_l: float = Field(gt=0, allow_inf_nan=False)
    risk_thresholds: RiskThresholds
    latest_observation: LatestObservationState | None = None
    is_active: bool = True
    created_at: UtcDateTime = Field(default_factory=utcnow)
    updated_at: UtcDateTime = Field(default_factory=utcnow)
    deactivated_at: UtcDateTime | None = None

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        return normalize_pan_id(value)

    @model_validator(mode="after")
    def _validate_coordinates_and_audit_times(self) -> "SaltPan":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.is_active and self.deactivated_at is not None:
            raise ValueError("deactivated_at can only be set on an inactive pan")
        if not self.is_active and self.deactivated_at is None:
            raise ValueError("an inactive pan must record deactivated_at")
        return self
