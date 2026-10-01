"""Module 6 - request and response payloads for the rainfall simulator.

Units are named in every field and spelled out in the descriptions, because a
simulator whose inputs can be misread is a simulator whose outputs get quoted
wrong. Millimetres are accepted at the boundary since that is how every weather
record in this project is stored; the balance itself runs in metres and cubic
metres.

Nothing here accepts a result. A caller supplies the four inputs and receives
the arithmetic, so a client cannot post a salinity curve that no volume balance
produced.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.pan_observation import SALINITY_RANGE
from app.models.weather import RAINFALL_RANGE
from app.services.mass_balance import (
    SIMULATOR_DISCLAIMER,
    RainfallDilution,
)

#: A pan area, in square metres. The ceiling is a kilometre square and exists to
#: catch a typed digit rather than to rule out a real pan.
#:
#: There is deliberately no lower bound here. The balance's own guards in
#: :mod:`app.services.mass_balance` refuse a non-positive area or depth, and they
#: explain why in operator terms; a `gt=0` field constraint would reject the same
#: input first with a bare "Input should be greater than 0" and lose the reason.
PAN_AREA_RANGE = {"le": 1_000_000.0}

#: Starting brine depth, in metres. The ceiling is the 10 m that the
#: observation module already treats as the deepest plausible water depth.
INITIAL_DEPTH_RANGE = {"le": 10.0}

#: Salinity and rainfall ceilings, taken from the modules that measure them. The
#: floors are left to the balance guards, for the reason given above.
SALINITY_CEILING = {"le": float(SALINITY_RANGE["le"])}
RAINFALL_CEILING = {"le": float(RAINFALL_RANGE["le"])}

#: How many custom rainfall depths one sweep may carry. The presets are always
#: included, so this bounds the work a single request can ask for.
MAX_CUSTOM_SCENARIOS = 24

#: The balance as arithmetic, returned with every result so a reader can check
#: the answer without opening the source.
MASS_BALANCE_EQUATIONS: tuple[str, ...] = (
    "V0 = A x D0  —  initial brine volume (m3)",
    "Vr = A x R  —  rainfall volume (m3)",
    "V1 = V0 + Vr  —  post-rain volume (m3)",
    "C1 = C0 x V0 / V1  —  predicted post-rain salinity (g/L)",
    "Dilution % = ((C0 - C1) / C0) x 100",
)

_INPUTS_DOC = (
    "A = pan area (m2), D0 = initial water/brine depth (m), C0 = current "
    "salinity (g/L), R = rainfall depth (mm)."
)


def _scenario_label(rainfall_mm: float, is_standard: bool) -> str:
    """A short, human name for one scenario column."""
    if rainfall_mm == 0:
        return "0 mm — no rain"
    if is_standard:
        return f"{rainfall_mm:g} mm"
    return f"{rainfall_mm:g} mm — custom"


class MassBalanceRequest(BaseModel):
    """One rainfall event against one starting brine.

    Every field is required. The balance is meaningless without a starting
    salinity, and a default would be a fabricated one.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    area_m2: float = Field(
        allow_inf_nan=False,
        **PAN_AREA_RANGE,
        description=(
            f"A — the pan area. {_INPUTS_DOC} Must be greater than zero, or the "
            "balance is refused with an explanation."
        ),
        examples=[12_500.0],
    )
    initial_depth_m: float = Field(
        allow_inf_nan=False,
        **INITIAL_DEPTH_RANGE,
        description=(
            f"D0 — depth of water/brine in the pan before the rain. {_INPUTS_DOC} "
            "Must be greater than zero: with no brine there is no salinity to "
            "dilute."
        ),
        examples=[0.155],
    )
    initial_salinity_g_l: float = Field(
        allow_inf_nan=False,
        **SALINITY_CEILING,
        description=(
            f"C0 — salinity of that brine. {_INPUTS_DOC} Zero is allowed and "
            "means the pan holds fresh water, but the expected dilution "
            "percentage is then undefined and is reported as null with a "
            "reason rather than as 0."
        ),
        examples=[285.0],
    )
    rainfall_mm: float = Field(
        allow_inf_nan=False,
        **RAINFALL_CEILING,
        description=f"R — rainfall depth. {_INPUTS_DOC} 0 means no rain fell.",
        examples=[20.0],
    )


class RainfallScenariosRequest(BaseModel):
    """A sweep of rainfall depths against one starting brine.

    ``rainfall_scenarios_mm`` adds custom depths to the six presets rather than
    replacing them, so a custom scenario stays on the same axis as the rest.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    area_m2: float = Field(
        allow_inf_nan=False,
        **PAN_AREA_RANGE,
        description=(
            f"A — the pan area. {_INPUTS_DOC} Must be greater than zero, or the "
            "balance is refused with an explanation."
        ),
        examples=[12_500.0],
    )
    initial_depth_m: float = Field(
        allow_inf_nan=False,
        **INITIAL_DEPTH_RANGE,
        description=(
            f"D0 — depth of water/brine in the pan before the rain. {_INPUTS_DOC} "
            "Must be greater than zero: with no brine there is no salinity to "
            "dilute."
        ),
        examples=[0.155],
    )
    initial_salinity_g_l: float = Field(
        allow_inf_nan=False,
        **SALINITY_CEILING,
        description=(
            f"C0 — the current salinity. {_INPUTS_DOC} Zero is allowed; the "
            "expected dilution percentage is then undefined and is reported as "
            "null with a reason."
        ),
        examples=[285.0],
    )
    rainfall_scenarios_mm: list[float] | None = Field(
        default=None,
        max_length=MAX_CUSTOM_SCENARIOS,
        description=(
            "Extra rainfall depths in millimetres to resolve alongside the "
            "presets. Leave blank to sweep the six presets alone. Preset "
            "depths supplied here are not duplicated, and a negative depth is "
            "refused with an explanation rather than treated as no rain."
        ),
        examples=[[12.5, 75.0]],
    )


class RainfallScenarioRead(BaseModel):
    """One resolved rainfall depth."""

    model_config = ConfigDict(from_attributes=True)

    rainfall_mm: float = Field(description="R — the rainfall depth for this scenario.")
    label: str = Field(description="Short human name for the column.")
    is_standard_preset: bool = Field(
        description="False for a custom depth supplied by the caller."
    )
    rainfall_volume_m3: float = Field(description="Vr = A x R.")
    post_rain_volume_m3: float = Field(description="V1 = V0 + Vr.")
    post_rain_depth_m: float = Field(description="D0 + R.")
    post_rain_salinity_g_l: float = Field(
        description="C1 — the predicted post-rain salinity."
    )
    salinity_drop_g_l: float = Field(description="C0 - C1, in g/L.")
    dilution_percent: float | None = Field(
        default=None,
        description=(
            "((C0 - C1) / C0) x 100, or null when C0 is 0 and the ratio is "
            "undefined."
        ),
    )
    dilution_percent_reason: str | None = Field(
        default=None,
        description="Why dilution_percent is null, when it is.",
    )

    @classmethod
    def from_dilution(
        cls, dilution: RainfallDilution, *, is_standard: bool
    ) -> RainfallScenarioRead:
        return cls(
            rainfall_mm=dilution.rainfall_mm,
            label=_scenario_label(dilution.rainfall_mm, is_standard),
            is_standard_preset=is_standard,
            rainfall_volume_m3=dilution.rainfall_volume_m3,
            post_rain_volume_m3=dilution.post_rain_volume_m3,
            post_rain_depth_m=dilution.post_rain_depth_m,
            post_rain_salinity_g_l=dilution.post_rain_salinity_g_l,
            salinity_drop_g_l=dilution.salinity_drop_g_l,
            dilution_percent=dilution.dilution_percent,
            dilution_percent_reason=dilution.dilution_percent_reason,
        )


class MassBalanceResponse(BaseModel):
    """The whole balance for one rainfall event, inputs echoed back.

    ``is_simulated`` is ``true`` and is part of the contract rather than a UI
    decoration, so a stored or exported result cannot lose the fact that it came
    from these equations.
    """

    model_config = ConfigDict(from_attributes=True)

    area_m2: float
    initial_depth_m: float
    initial_salinity_g_l: float
    rainfall_mm: float

    initial_volume_m3: float = Field(description="V0 = A x D0.")
    rainfall_volume_m3: float = Field(description="Vr = A x R.")
    post_rain_volume_m3: float = Field(description="V1 = V0 + Vr.")

    initial_salt_mass_kg: float = Field(
        description="C0 x V0, the salt the pan holds before the rain."
    )
    post_rain_salt_mass_kg: float = Field(
        description="C1 x V1, the salt it holds after. Equal to the figure above: "
        "the model dilutes brine, it does not remove salt."
    )
    salt_mass_conserved: bool = Field(
        description="Whether the two salt masses agree to floating-point tolerance."
    )

    post_rain_salinity_g_l: float = Field(description="C1 — the prediction.")
    salinity_drop_g_l: float = Field(description="C0 - C1, in g/L.")
    dilution_percent: float | None = Field(
        default=None,
        description="((C0 - C1) / C0) x 100, or null when C0 is 0.",
    )
    dilution_percent_reason: str | None = Field(
        default=None,
        description="Why dilution_percent is null, when it is.",
    )
    post_rain_depth_m: float = Field(description="D0 + R.")

    is_simulated: Literal[True] = True
    model_name: str = Field(
        description="Which equations produced these figures.",
        examples=["rainfall-dilution-mass-balance-v1"],
    )
    disclaimer: str = Field(description="Carried with every result, not just in the UI.")
    assumptions: list[str] = Field(
        description="The simplifying assumptions this result depends on."
    )
    equations: list[str] = Field(description="The balance, as applied.")

    @classmethod
    def from_dilution(
        cls, dilution: RainfallDilution, *, assumptions: list[str]
    ) -> MassBalanceResponse:
        return cls(
            area_m2=dilution.area_m2,
            initial_depth_m=dilution.initial_depth_m,
            initial_salinity_g_l=dilution.initial_salinity_g_l,
            rainfall_mm=dilution.rainfall_mm,
            initial_volume_m3=dilution.initial_volume_m3,
            rainfall_volume_m3=dilution.rainfall_volume_m3,
            post_rain_volume_m3=dilution.post_rain_volume_m3,
            initial_salt_mass_kg=dilution.initial_salt_mass_kg,
            post_rain_salt_mass_kg=dilution.post_rain_salt_mass_kg,
            salt_mass_conserved=dilution.salt_mass_conserved,
            post_rain_salinity_g_l=dilution.post_rain_salinity_g_l,
            salinity_drop_g_l=dilution.salinity_drop_g_l,
            dilution_percent=dilution.dilution_percent,
            dilution_percent_reason=dilution.dilution_percent_reason,
            post_rain_depth_m=dilution.post_rain_depth_m,
            model_name="rainfall-dilution-mass-balance-v1",
            disclaimer=SIMULATOR_DISCLAIMER,
            assumptions=assumptions,
            equations=list(MASS_BALANCE_EQUATIONS),
        )


class RainfallScenariosResponse(BaseModel):
    """Every requested rainfall depth resolved against one starting brine.

    ``scenarios`` is ordered by rainfall ascending, which is also the order the
    salinity curve falls in, so a chart can plot it directly.
    """

    model_config = ConfigDict(from_attributes=True)

    area_m2: float
    initial_depth_m: float
    initial_salinity_g_l: float = Field(description="C0 — the current salinity.")

    initial_volume_m3: float = Field(description="V0 = A x D0, shared by every scenario.")

    standard_scenarios_mm: list[float] = Field(
        description="The six preset depths, always present."
    )
    scenarios: list[RainfallScenarioRead] = Field(
        description="Presets plus any custom depths, ordered by rainfall ascending."
    )

    is_simulated: Literal[True] = True
    model_name: str
    disclaimer: str
    assumptions: list[str]
    equations: list[str]


__all__ = [
    "INITIAL_DEPTH_RANGE",
    "MASS_BALANCE_EQUATIONS",
    "MAX_CUSTOM_SCENARIOS",
    "PAN_AREA_RANGE",
    "RAINFALL_CEILING",
    "SALINITY_CEILING",
    "MassBalanceRequest",
    "MassBalanceResponse",
    "RainfallScenarioRead",
    "RainfallScenariosRequest",
    "RainfallScenariosResponse",
]
