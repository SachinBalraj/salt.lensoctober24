"""Module 6 - transparent rainfall / brine mass-balance simulator.

This is arithmetic on a volume balance, not a prediction and not a model that
was fitted to anything. Given the pan area, the starting brine depth, the
starting salinity and a rainfall depth, the post-rain salinity follows from
conservation of volume and conservation of salt:

    V0 = A x D0                 initial brine volume          (m3)
    Vr = A x R                  rainfall volume               (m3)
    V1 = V0 + Vr                post-rain volume              (m3)
    C1 = C0 x V0 / V1           post-rain salinity            (g/L)
    dilution % = ((C0 - C1) / C0) x 100

Every one of those figures is a direct consequence of the inputs, which is what
makes the result checkable by hand. That checkability is the point of the
module, so the same salt mass is reported before and after the event: the model
conserves it exactly, and a reader who disagrees with the answer can compare
the two columns and see that the dilution is dilution rather than salt loss.

The simplifying assumptions are not incidental, they are the entire model, so
they are stated in :data:`SIMULATION_ASSUMPTIONS` and returned with every
response. The disclaimer in :data:`SIMULATOR_DISCLAIMER` travels with the
results for the same reason: a number produced by this arithmetic must never be
read as a forecast of what the weather will do.
"""

from __future__ import annotations

from dataclasses import dataclass

#: A cubic metre holds a thousand litres, so g/L and m3 combine as x 1000 to
#: give grams. Used to state the salt mass explicitly rather than leaving it
#: implied by the salinity figures.
LITRES_PER_CUBIC_METRE = 1000.0

#: The rainfall depths the what-if panel offers as presets, in millimetres.
#: Zero is first and is a real scenario, not an absence of one: it is the
#: "nothing happens" reference every other column is compared against.
STANDARD_RAINFALL_SCENARIOS_MM: tuple[float, ...] = (0.0, 5.0, 10.0, 20.0, 50.0, 100.0)

SIMULATOR_DISCLAIMER = (
    "Physics-based simulation under simplifying assumptions; not an ML prediction."
)

#: The name this model answers to. Every surface that shows a physics result
#: quotes it -- the simulator's own responses, the stored SimulationRun's outputs,
#: and Module 12's summaries -- so it is declared once here and imported by each
#: of them. A second literal anywhere else is a second thing to forget when the
#: model is renamed, and a stored result whose name no longer matches its
#: simulator is untraceable.
MASS_BALANCE_MODEL_NAME = "rainfall-dilution-mass-balance-v1"

#: What this model leaves out. Each entry is a real process that would move the
#: answer, so the list is reported rather than buried in a docstring: a reader
#: has to be able to see which physics is absent before trusting the number.
SIMULATION_ASSUMPTIONS: tuple[str, ...] = (
    (
        "Complete mixing: the rain and the brine reach one uniform salinity "
        "instantly, so a single concentration describes the whole pan."
    ),
    "No evaporation during the event: rainfall is the only water that moves.",
    "No drainage: water leaves the pan by no route while the event runs.",
    (
        "No overflow: the pan is assumed deep enough to hold every drop of rain, "
        "so nothing spills and no brine is lost over the wall."
    ),
    "No seepage: no water percolates through the pan floor.",
    (
        "No brine transfer: brine is neither pumped in nor drawn off during the "
        "event, so the pan keeps exactly the salt it started with."
    ),
    (
        "No crystallization during the calculation: salt stays dissolved, so the "
        "salinity is a solution concentration and no salt is precipitated out."
    ),
    (
        "Rainfall is a single instantaneous depth: the model does not resolve when "
        "the rain fell or how fast, only how much in total."
    ),
)

#: Why the dilution percentage is absent, in the one case where it is undefined.
DILUTION_UNDEFINED_FOR_FRESH_BRINE = (
    "Expected dilution percentage is undefined for a starting salinity of 0 g/L: "
    "the percentage is a ratio against the starting salinity, and there is none. "
    "The post-rain salinity is still reported."
)


class RainfallDilutionError(ValueError):
    """An input that makes the volume balance meaningless.

    Raised instead of returning a number, because every field in the balance
    divides or multiplies by the pan area and the starting depth, and a zero
    there is a mistyped figure rather than a physical situation.
    """


@dataclass(frozen=True)
class RainfallDilution:
    """One rainfall event resolved against one starting brine.

    The names follow the balance in the module docstring, and the SI unit is
    carried in the field name so a figure can never be read in the wrong one.
    """

    area_m2: float
    initial_depth_m: float
    initial_salinity_g_l: float
    rainfall_m: float

    # Volumes, m3.
    initial_volume_m3: float
    rainfall_volume_m3: float
    post_rain_volume_m3: float

    # Salt, conserved across the event.
    initial_salt_mass_kg: float
    post_rain_salt_mass_kg: float

    # Concentrations.
    post_rain_salinity_g_l: float
    salinity_drop_g_l: float

    #: ``None`` only when the starting salinity is 0, which leaves the ratio
    #: undefined. Paired with :attr:`dilution_percent_reason` so the absence is
    #: explained rather than rendered as a zero.
    dilution_percent: float | None
    dilution_percent_reason: str | None

    post_rain_depth_m: float

    @property
    def rainfall_mm(self) -> float:
        """The rainfall depth in the unit operators read weather in."""
        return self.rainfall_m * 1000.0

    @property
    def salt_mass_conserved(self) -> bool:
        """Whether the two salt figures agree to floating-point tolerance.

        Always true for results this module produced. It is exposed so a caller
        can assert the invariant rather than take it on trust.
        """
        return (
            abs(self.post_rain_salt_mass_kg - self.initial_salt_mass_kg)
            <= 1e-9 * max(1.0, abs(self.initial_salt_mass_kg))
        )


def simulate_rainfall_dilution(
    *,
    area_m2: float,
    initial_depth_m: float,
    initial_salinity_g_l: float,
    rainfall_m: float,
) -> RainfallDilution:
    """Resolve one rainfall event against a pan of starting brine.

    The five steps are kept as written in the module docstring, in order, so the
    arithmetic can be followed and checked against a calculator.
    """
    if not area_m2 > 0:
        raise RainfallDilutionError(
            "Pan area must be greater than zero. Every volume in the balance is "
            "the product of the area and a depth, so a zero area leaves nothing "
            "to dilute."
        )
    if not initial_depth_m > 0:
        raise RainfallDilutionError(
            "Initial water/brine depth must be greater than zero. With no brine "
            "in the pan there is no salinity to dilute."
        )
    if initial_salinity_g_l < 0:
        raise RainfallDilutionError(
            "Initial salinity cannot be negative. A negative concentration has "
            "no physical meaning; check the units are g/L."
        )
    if rainfall_m < 0:
        raise RainfallDilutionError(
            "Rainfall depth cannot be negative. Zero is a valid scenario and "
            "means no rain fell."
        )

    # V0 = A x D0
    initial_volume_m3 = area_m2 * initial_depth_m
    # Vr = A x R
    rainfall_volume_m3 = area_m2 * rainfall_m
    # V1 = V0 + Vr
    post_rain_volume_m3 = initial_volume_m3 + rainfall_volume_m3

    # Salt is carried, not solved for: the same kilograms before and after.
    initial_salt_mass_kg = (
        initial_salinity_g_l * initial_volume_m3 * LITRES_PER_CUBIC_METRE / 1000.0
    )
    post_rain_salt_mass_kg = initial_salt_mass_kg

    # C1 = C0 x V0 / V1
    post_rain_salinity_g_l = initial_salinity_g_l * initial_volume_m3 / post_rain_volume_m3

    if initial_salinity_g_l == 0:
        dilution_percent = None
        dilution_percent_reason = DILUTION_UNDEFINED_FOR_FRESH_BRINE
    else:
        # dilution % = ((C0 - C1) / C0) x 100
        dilution_percent = (
            (initial_salinity_g_l - post_rain_salinity_g_l) / initial_salinity_g_l * 100.0
        )
        dilution_percent_reason = None

    return RainfallDilution(
        area_m2=area_m2,
        initial_depth_m=initial_depth_m,
        initial_salinity_g_l=initial_salinity_g_l,
        rainfall_m=rainfall_m,
        initial_volume_m3=initial_volume_m3,
        rainfall_volume_m3=rainfall_volume_m3,
        post_rain_volume_m3=post_rain_volume_m3,
        initial_salt_mass_kg=initial_salt_mass_kg,
        post_rain_salt_mass_kg=post_rain_salt_mass_kg,
        post_rain_salinity_g_l=post_rain_salinity_g_l,
        salinity_drop_g_l=initial_salinity_g_l - post_rain_salinity_g_l,
        dilution_percent=dilution_percent,
        dilution_percent_reason=dilution_percent_reason,
        post_rain_depth_m=initial_depth_m + rainfall_m,
    )


class MassBalanceService:
    """Runs the rainfall balance, for one rainfall depth or for a whole sweep.

    The service holds no state and touches no database: the balance is fully
    determined by its four inputs, so persisting a run would add a way to be
    wrong without adding anything to compute.
    """

    #: The preset rainfall depths, in millimetres.
    standard_scenarios_mm = STANDARD_RAINFALL_SCENARIOS_MM

    def evaluate(
        self,
        *,
        area_m2: float,
        initial_depth_m: float,
        initial_salinity_g_l: float,
        rainfall_mm: float,
    ) -> RainfallDilution:
        """Resolve one rainfall depth, given in millimetres.

        Millimetres at the boundary and metres inside, because that is the unit
        every weather record in this project is stored in and the unit the
        balance is written in.
        """
        return simulate_rainfall_dilution(
            area_m2=area_m2,
            initial_depth_m=initial_depth_m,
            initial_salinity_g_l=initial_salinity_g_l,
            rainfall_m=rainfall_mm / 1000.0,
        )

    def sweep(
        self,
        *,
        area_m2: float,
        initial_depth_m: float,
        initial_salinity_g_l: float,
        rainfall_scenarios_mm: list[float] | None = None,
    ) -> list[RainfallDilution]:
        """Resolve every requested rainfall depth, ascending.

        The presets are always included, because "no rain" is the reference the
        others are read against. A custom depth is added alongside them rather
        than replacing them, so a custom scenario stays comparable with the
        standard set on the same axes.
        """
        depths = list(self.standard_scenarios_mm)
        for depth in rainfall_scenarios_mm or []:
            if depth not in depths:
                depths.append(depth)
        return sorted(
            (
                self.evaluate(
                    area_m2=area_m2,
                    initial_depth_m=initial_depth_m,
                    initial_salinity_g_l=initial_salinity_g_l,
                    rainfall_mm=depth,
                )
                for depth in depths
            ),
            key=lambda result: result.rainfall_mm,
        )


__all__ = [
    "DILUTION_UNDEFINED_FOR_FRESH_BRINE",
    "LITRES_PER_CUBIC_METRE",
    "SIMULATION_ASSUMPTIONS",
    "SIMULATOR_DISCLAIMER",
    "STANDARD_RAINFALL_SCENARIOS_MM",
    "MassBalanceService",
    "RainfallDilution",
    "RainfallDilutionError",
    "simulate_rainfall_dilution",
]
