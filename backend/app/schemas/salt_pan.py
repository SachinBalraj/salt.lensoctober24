"""Module 2 - operator-facing request and response payloads for salt pans.

These schemas are the HTTP contract. They are intentionally narrower than the
persistence model in ``app.models.salt_pan``: the identifier is immutable, the
lifecycle flag is driven by the dedicated deactivate operation, and risk
thresholds belong to the risk module rather than to pan registration.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import SaltPan
from app.models.pan_observation import PAN_ID_PATTERN
from app.models.salt_pan import LatestObservationState, RiskThresholds


class RiskThresholdsWrite(BaseModel):
    """The operator's own limits for one pan, as supplied and as returned.

    Exposed here for the first time. The persistence model has always carried
    :class:`~app.models.salt_pan.RiskThresholds` and the risk module reads it, but
    nothing in the HTTP contract let an operator set or even see it, which left
    every threshold in the system unreachable to the person who has to justify it.

    **Every field is optional and null means "not configured".** That is the whole
    point of the type: there are no defaults here, because a default is a scientific
    claim this project has no basis to make. A pan with no flood depth configured has
    no flood depth, and a rule that needs one is refused rather than served a
    number that came from a code default.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    flood_depth_cm: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description=(
            "Water depth above which the pan should be drained, in cm. The "
            "operator's number for this pan; not supplied by this project."
        ),
        examples=[25.0],
    )
    dilution_salinity_g_l: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description=(
            "Salinity below which the brine is too dilute to sell, in g/L. The "
            "operator's number for this pan; not supplied by this project."
        ),
        examples=[180.0],
    )
    critical_rainfall_24h_mm: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description=(
            "Rainfall over 24 h above which the pan is at risk, in mm. The "
            "operator's number for this pan; not supplied by this project."
        ),
        examples=[50.0],
    )
    target_flow_cm: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description=(
            "Water depth the operator intends to keep when transferring brine, in "
            "cm. The operator's number for this pan; not supplied by this project."
        ),
        examples=[12.0],
    )
    max_observation_age_hours: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        description=(
            "How old the newest reading may be and still support a recommendation, "
            "in hours. Beyond this the answer is 'insufficient evidence' rather than "
            "a decision from old water. The operator's number; this project has no "
            "opinion about how quickly a salt pan changes."
        ),
        examples=[24.0],
    )

    @classmethod
    def from_model(cls, thresholds: RiskThresholds | None) -> RiskThresholdsWrite:
        if thresholds is None:
            return cls()
        return cls(
            flood_depth_cm=thresholds.flood_depth_cm,
            dilution_salinity_g_l=thresholds.dilution_salinity_g_l,
            critical_rainfall_24h_mm=thresholds.critical_rainfall_24h_mm,
            target_flow_cm=thresholds.target_flow_cm,
            max_observation_age_hours=thresholds.max_observation_age_hours,
        )

    def to_model(self) -> RiskThresholds:
        return RiskThresholds(
            flood_depth_cm=self.flood_depth_cm,
            dilution_salinity_g_l=self.dilution_salinity_g_l,
            critical_rainfall_24h_mm=self.critical_rainfall_24h_mm,
            target_flow_cm=self.target_flow_cm,
            max_observation_age_hours=self.max_observation_age_hours,
        )


class PanStatus(str, Enum):
    """Filterable lifecycle state of a registered pan."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ALL = "all"


class SaltPanRegistration(BaseModel):
    """Fields an operator supplies when registering a new salt pan."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    pan_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=PAN_ID_PATTERN,
        description="Stable operator identifier, stored lowercased. Letters, digits, dot, underscore and hyphen only.",
        examples=["pans-2026-a01"],
    )
    pan_name: str = Field(
        min_length=1,
        max_length=128,
        description="Human-readable name of the pan.",
    )
    location: str = Field(
        min_length=1,
        max_length=128,
        description="Site or locality where the pan is located.",
    )
    latitude: float | None = Field(
        default=None,
        ge=-90,
        le=90,
        allow_inf_nan=False,
        description="Decimal degrees, -90 to 90. Must be supplied together with longitude.",
    )
    longitude: float | None = Field(
        default=None,
        ge=-180,
        le=180,
        allow_inf_nan=False,
        description="Decimal degrees, -180 to 180. Must be supplied together with latitude.",
    )
    area_m2: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="Usable pan surface area in square metres. Must be greater than 0.",
    )
    normal_depth_cm: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="Normal operating water depth in centimetres. Must be greater than 0.",
    )
    target_harvest_salinity_g_l: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="Target brine salinity at harvest in grams per litre. Must be greater than 0.",
    )
    risk_thresholds: RiskThresholdsWrite = Field(
        default_factory=RiskThresholdsWrite,
        description=(
            "The operator's own limits for this pan. Every field optional; omitted "
            "means not configured, and a rule needing a missing limit is refused "
            "rather than served a default."
        ),
    )

    @model_validator(mode="after")
    def _validate_coordinates(self) -> SaltPanRegistration:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "latitude and longitude must be provided together; "
                "omit both to leave the pan ungeolocated"
            )
        return self


class SaltPanUpdate(BaseModel):
    """Partial update of a registered pan. Every field is optional.

    ``pan_id`` is absent by design: the identifier is referenced by observations,
    weather, twin states and outcomes, so it is immutable after registration.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    pan_name: str | None = Field(default=None, min_length=1, max_length=128)
    location: str | None = Field(default=None, min_length=1, max_length=128)
    latitude: float | None = Field(
        default=None, ge=-90, le=90, allow_inf_nan=False
    )
    longitude: float | None = Field(
        default=None, ge=-180, le=180, allow_inf_nan=False
    )
    area_m2: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    normal_depth_cm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    target_harvest_salinity_g_l: float | None = Field(
        default=None, gt=0, allow_inf_nan=False
    )
    risk_thresholds: RiskThresholdsWrite | None = Field(
        default=None,
        description=(
            "Replace this pan's operator-configured limits. Supplying the object "
            "replaces it whole, so a field left out of an update becomes "
            "unconfigured rather than silently keeping its old value."
        ),
    )

    def editable_fields(self) -> dict[str, Any]:
        """Return only the fields the operator actually supplied.

        ``risk_thresholds`` is rebuilt as the model type the service expects
        rather than passed through as the parsed dict. A plain
        ``model_dump`` would hand the service a dict, and the conversion would
        then have to happen somewhere downstream -- where a block of nulls and a
        block of numbers are indistinguishable from the outside, and where the
        one caller that remembered to convert would not be obvious to a reader.
        """
        fields = self.model_dump(exclude_unset=True, exclude_none=False)
        if "risk_thresholds" in fields:
            fields["risk_thresholds"] = self.risk_thresholds
        return fields


class LatestObservationRead(BaseModel):
    """A pan's most recent reading, as denormalised onto the pan document."""

    model_config = ConfigDict(from_attributes=True)

    observation_id: str
    timestamp: datetime
    salinity_g_l: float | None
    water_depth_cm: float | None
    brine_temperature_c: float | None
    humidity_percent: float | None
    ambient_temperature_c: float | None
    data_source: str

    @classmethod
    def from_latest(cls, latest: LatestObservationState) -> LatestObservationRead:
        return cls(
            observation_id=latest.observation_id,
            timestamp=latest.timestamp,
            salinity_g_l=latest.salinity_g_l,
            water_depth_cm=latest.water_depth_cm,
            brine_temperature_c=latest.brine_temperature_c,
            humidity_percent=latest.humidity_percent,
            ambient_temperature_c=latest.ambient_temperature_c,
            data_source=latest.data_source.value,
        )


class SaltPanRead(BaseModel):
    """A salt pan as returned to the operator dashboard."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = Field(default=None, description="MongoDB document identifier.")
    pan_id: str
    pan_name: str
    location: str
    latitude: float | None
    longitude: float | None
    area_m2: float
    normal_depth_cm: float
    target_harvest_salinity_g_l: float
    risk_thresholds: RiskThresholdsWrite = Field(
        description=(
            "This pan's operator-configured limits, as stored. Null fields are "
            "thresholds nobody has configured."
        ),
    )
    is_active: bool
    created_at: datetime
    updated_at: datetime
    deactivated_at: datetime | None
    latest_observation: LatestObservationRead | None = Field(
        default=None,
        description=(
            "The pan's newest reading. Null until the first observation is "
            "recorded; it is never estimated or pre-filled."
        ),
    )

    @classmethod
    def from_pan(cls, pan: SaltPan) -> SaltPanRead:
        return cls(
            id=str(pan.id) if pan.id is not None else None,
            pan_id=pan.pan_id,
            pan_name=pan.pan_name,
            location=pan.location_name,
            latitude=pan.latitude,
            longitude=pan.longitude,
            area_m2=pan.area_m2,
            normal_depth_cm=pan.normal_depth_cm,
            target_harvest_salinity_g_l=pan.target_harvest_salinity_g_l,
            risk_thresholds=RiskThresholdsWrite.from_model(pan.risk_thresholds),
            is_active=pan.is_active,
            created_at=pan.created_at,
            updated_at=pan.updated_at,
            deactivated_at=pan.deactivated_at,
            latest_observation=(
                LatestObservationRead.from_latest(pan.latest_observation)
                if pan.latest_observation is not None
                else None
            ),
        )


class SaltPanListResponse(BaseModel):
    """A page of registered pans plus the counts the dashboard needs."""

    items: list[SaltPanRead] = Field(default_factory=list)
    total: int = Field(ge=0, description="Total pans matching the filter, ignoring paging.")
    limit: int = Field(ge=1, le=200)
    offset: int = Field(ge=0)
    total_active: int = Field(ge=0)
    total_inactive: int = Field(ge=0)
