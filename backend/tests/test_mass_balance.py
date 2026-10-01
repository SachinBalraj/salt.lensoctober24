"""Module 6 - the rainfall mass-balance equations, verified numerically.

Every assertion here is the arithmetic from the module docstring written out
longhand, so a failure names the step that broke rather than a mismatch between
two functions that could both be wrong in the same way.

The cases cover the four identities that must hold for every input:

    V0 = A x D0      Vr = A x R      V1 = V0 + Vr
    C1 = C0 x V0 / V1                salt is conserved

plus the properties a volume balance must have: salinity falls monotonically
with rainfall, it never falls below zero, and the reported dilution is exactly
the fraction of the starting salinity that went away.
"""

from __future__ import annotations

import itertools
import math

import pytest

from app.services.mass_balance import (
    DILUTION_UNDEFINED_FOR_FRESH_BRINE,
    SIMULATION_ASSUMPTIONS,
    SIMULATOR_DISCLAIMER,
    STANDARD_RAINFALL_SCENARIOS_MM,
    MassBalanceService,
    RainfallDilutionError,
    simulate_rainfall_dilution,
)

# A worked case used throughout: a 12,500 m2 pan holding 0.155 m of 285 g/L
# brine, with 20 mm of rain. The numbers are deliberately not round so that a
# wrong unit anywhere in the chain shows up as a mismatch rather than a
# coincidence.
AREA_M2 = 12_500.0
DEPTH_M = 0.155
SALINITY_G_L = 285.0
RAINFALL_MM = 20.0


def solve(
    *,
    area_m2: float = AREA_M2,
    depth_m: float = DEPTH_M,
    salinity: float = SALINITY_G_L,
    rainfall_mm: float = RAINFALL_MM,
):
    """The five steps, transcribed independently of the implementation."""
    rainfall_m = rainfall_mm / 1000.0
    v0 = area_m2 * depth_m
    vr = area_m2 * rainfall_m
    v1 = v0 + vr
    c1 = salinity * v0 / v1
    dilution = None if salinity == 0 else (salinity - c1) / salinity * 100.0
    return v0, vr, v1, c1, dilution


# -- the three volume steps -----------------------------------------------


def test_initial_volume_is_area_times_depth():
    result = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_m=RAINFALL_MM / 1000.0,
    )
    # V0 = A x D0 = 12500 x 0.155 = 1937.5 m3
    assert result.initial_volume_m3 == pytest.approx(1937.5)
    assert result.initial_volume_m3 == pytest.approx(solve()[0])


def test_rainfall_volume_is_area_times_rainfall_depth():
    result = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_m=RAINFALL_MM / 1000.0,
    )
    # Vr = A x R = 12500 x 0.020 = 250.0 m3
    assert result.rainfall_volume_m3 == pytest.approx(250.0)
    assert result.rainfall_volume_m3 == pytest.approx(solve()[1])


def test_post_rain_volume_is_the_sum_of_the_two_volumes():
    result = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_m=RAINFALL_MM / 1000.0,
    )
    # V1 = V0 + Vr = 1937.5 + 250.0 = 2187.5 m3
    assert result.post_rain_volume_m3 == pytest.approx(2187.5)
    assert result.post_rain_volume_m3 == pytest.approx(
        result.initial_volume_m3 + result.rainfall_volume_m3
    )
    assert result.post_rain_volume_m3 == pytest.approx(solve()[2])


# -- salinity and dilution ------------------------------------------------


def test_post_rain_salinity_is_c0_scaled_by_the_volume_share():
    result = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_m=RAINFALL_MM / 1000.0,
    )
    expected = SALINITY_G_L * 1937.5 / 2187.5
    # C1 = C0 x V0 / V1 = 285 x 1937.5 / 2187.5 = 252.428571... g/L
    assert expected == pytest.approx(252.42857142857142)
    assert result.post_rain_salinity_g_l == pytest.approx(expected)
    assert result.post_rain_salinity_g_l == pytest.approx(solve()[3])


def test_dilution_percent_is_the_fraction_of_salinity_that_went_away():
    result = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_m=RAINFALL_MM / 1000.0,
    )
    # ((285 - 252.428571...) / 285) x 100 = 11.428571...%
    expected = (285.0 - 285.0 * 1937.5 / 2187.5) / 285.0 * 100.0
    assert expected == pytest.approx(11.428571428571432)
    assert result.dilution_percent == pytest.approx(expected)
    assert result.dilution_percent == pytest.approx(solve()[4])


def test_salinity_drop_is_the_absolute_difference_in_g_per_litre():
    result = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_m=RAINFALL_MM / 1000.0,
    )
    assert result.salinity_drop_g_l == pytest.approx(285.0 - result.post_rain_salinity_g_l)
    assert result.salinity_drop_g_l == pytest.approx(32.571428571428584)


def test_dilution_percent_and_volume_share_agree_with_each_other():
    """The percentage and the volume ratio are two views of one number.

    A rainfall depth equal to the starting depth should dilute by half, which is
    the one figure a reader can check without a calculator.
    """
    result = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=0.2, initial_salinity_g_l=100.0,
        rainfall_m=0.2,
    )
    # Rain as deep as the brine doubles the volume, so the salinity halves.
    assert result.post_rain_salinity_g_l == pytest.approx(50.0)
    assert result.dilution_percent == pytest.approx(50.0)


# -- the salt is carried, not destroyed -----------------------------------


def test_the_salt_in_the_pan_is_identical_before_and_after_the_rain():
    result = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_m=RAINFALL_MM / 1000.0,
    )
    # 285 g/L x 1937.5 m3 x 1000 L/m3 = 552,187,500 g = 552,187.5 kg
    assert result.initial_salt_mass_kg == pytest.approx(552_187.5)
    assert result.post_rain_salt_mass_kg == pytest.approx(result.initial_salt_mass_kg)
    assert result.salt_mass_conserved is True


def test_salt_mass_follows_from_the_two_concentrations_and_the_two_volumes():
    """C1 x V1 must reproduce the salt the pan started with, not merely equal it."""
    result = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_m=RAINFALL_MM / 1000.0,
    )
    recomputed = (
        result.post_rain_salinity_g_l * result.post_rain_volume_m3 * 1000.0 / 1000.0
    )
    assert recomputed == pytest.approx(result.initial_salt_mass_kg)


# -- rainfall in millimetres at the boundary ------------------------------


def test_rainfall_is_accepted_in_millimetres_and_applied_in_metres():
    service = MassBalanceService()
    from_mm = service.evaluate(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_mm=RAINFALL_MM,
    )
    from_m = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_m=RAINFALL_MM / 1000.0,
    )
    assert from_mm.rainfall_m == pytest.approx(0.020)
    assert from_mm.rainfall_mm == pytest.approx(RAINFALL_MM)
    assert from_mm.post_rain_salinity_g_l == pytest.approx(from_m.post_rain_salinity_g_l)
    assert from_mm.rainfall_volume_m3 == pytest.approx(from_m.rainfall_volume_m3)


def test_rainfall_of_one_hundred_millimetres_is_a_tenth_of_a_metre():
    service = MassBalanceService()
    result = service.evaluate(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_mm=100.0,
    )
    assert result.rainfall_m == pytest.approx(0.1)
    # 12500 x 0.1 = 1250 m3 of rain on 1937.5 m3 of brine.
    assert result.rainfall_volume_m3 == pytest.approx(1250.0)
    assert result.post_rain_salinity_g_l == pytest.approx(285.0 * 1937.5 / 3187.5)


# -- the boundary cases a volume balance has ------------------------------


def test_no_rain_leaves_the_salinity_exactly_where_it_started():
    service = MassBalanceService()
    result = service.evaluate(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=SALINITY_G_L, rainfall_mm=0.0,
    )
    assert result.rainfall_volume_m3 == pytest.approx(0.0)
    assert result.post_rain_volume_m3 == pytest.approx(result.initial_volume_m3)
    assert result.post_rain_salinity_g_l == pytest.approx(SALINITY_G_L)
    assert result.dilution_percent == pytest.approx(0.0)
    assert result.salinity_drop_g_l == pytest.approx(0.0)


def test_more_rain_only_ever_approaches_fresh_water_without_reaching_it():
    """The curve tends to 0 as rainfall grows, and never attains it.

    A pan holding any salt at all cannot reach exactly fresh water by adding
    finite rain, so the limit is approached rather than reached. A model that
    reported 0 g/L for a very large storm would be claiming the salt left.
    """
    service = MassBalanceService()
    salinities = [
        service.evaluate(
            area_m2=AREA_M2, initial_depth_m=DEPTH_M,
            initial_salinity_g_l=SALINITY_G_L, rainfall_mm=mm,
        ).post_rain_salinity_g_l
        for mm in (1_000.0, 10_000.0, 100_000.0)
    ]
    assert salinities == pytest.approx([38.246753246753244, 4.350073855243722, 0.4410663471618991])
    assert all(0.0 < value < SALINITY_G_L for value in salinities)


def test_salinity_falls_monotonically_as_rainfall_grows():
    """A larger rain can only ever dilute further, never concentrate the brine."""
    service = MassBalanceService()
    sweep = service.sweep(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M, initial_salinity_g_l=SALINITY_G_L,
        rainfall_scenarios_mm=[7.5, 35.0, 250.0],
    )
    salinities = [scenario.post_rain_salinity_g_l for scenario in sweep]
    assert all(
        later < earlier for earlier, later in itertools.pairwise(salinities)
    )
    assert all(0.0 <= value <= SALINITY_G_L for value in salinities)


def test_fresh_water_in_the_pan_has_no_dilution_percentage_to_report():
    result = simulate_rainfall_dilution(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M,
        initial_salinity_g_l=0.0, rainfall_m=0.020,
    )
    # C1 = 0 x V0 / V1 is well defined even though the percentage is not.
    assert result.post_rain_salinity_g_l == pytest.approx(0.0)
    assert result.dilution_percent is None
    assert result.dilution_percent_reason == DILUTION_UNDEFINED_FOR_FRESH_BRINE
    assert "undefined" in result.dilution_percent_reason


def test_a_zero_area_or_depth_is_refused_rather_than_divided_by():
    with pytest.raises(RainfallDilutionError) as error:
        simulate_rainfall_dilution(
            area_m2=0.0, initial_depth_m=DEPTH_M,
            initial_salinity_g_l=SALINITY_G_L, rainfall_m=0.020,
        )
    assert "greater than zero" in str(error.value)

    with pytest.raises(RainfallDilutionError) as error:
        simulate_rainfall_dilution(
            area_m2=AREA_M2, initial_depth_m=0.0,
            initial_salinity_g_l=SALINITY_G_L, rainfall_m=0.020,
        )
    assert "no salinity to dilute" in str(error.value)


def test_negative_rainfall_or_salinity_is_refused():
    with pytest.raises(RainfallDilutionError) as error:
        simulate_rainfall_dilution(
            area_m2=AREA_M2, initial_depth_m=DEPTH_M,
            initial_salinity_g_l=SALINITY_G_L, rainfall_m=-1.0,
        )
    assert "Zero is a valid scenario" in str(error.value)

    with pytest.raises(RainfallDilutionError) as error:
        simulate_rainfall_dilution(
            area_m2=AREA_M2, initial_depth_m=DEPTH_M,
            initial_salinity_g_l=-5.0, rainfall_m=0.020,
        )
    assert "negative" in str(error.value)


def test_the_balance_stays_exact_over_a_wide_sweep_of_inputs():
    """V1 = V0 + Vr and C1 = C0 x V0 / V1 hold to floating-point tolerance.

    Checked across magnitudes rather than on one case, because a formula that
    is only right for a mid-sized pan is not a formula.
    """
    for area in (0.5, 12_500.0, 4_000_000.0):
        for depth in (0.01, 0.155, 3.0):
            for rainfall in (0.0, 0.005, 0.75):
                for salinity in (0.5, 35.0, 400.0):
                    result = simulate_rainfall_dilution(
                        area_m2=area, initial_depth_m=depth,
                        initial_salinity_g_l=salinity, rainfall_m=rainfall,
                    )
                    assert result.post_rain_volume_m3 == pytest.approx(
                        result.initial_volume_m3 + result.rainfall_volume_m3
                    )
                    assert result.post_rain_salinity_g_l == pytest.approx(
                        salinity * result.initial_volume_m3 / result.post_rain_volume_m3
                    )
                    assert result.salt_mass_conserved is True
                    assert result.dilution_percent is not None
                    assert 0.0 <= result.dilution_percent <= 100.0
                    assert not math.isnan(result.post_rain_salinity_g_l)


# -- the golden table: hand-checked numbers that pin the equations --------------
#
# Every row is a scenario resolved by :func:`MassBalanceService.evaluate`, whose
# only work is to convert millimetres to metres and call the balance. The
# expected figures are written out longhand -- by a calculator, not by the
# module -- so that changing any equation, or silently switching a unit (e.g.
# treating millimetres as metres or litres as cubic metres), makes these tests
# fail.


@pytest.mark.parametrize(
    "area_m2,initial_depth_m,salinity_g_l,rainfall_mm,"
    "v0,vr,v1,c1,dilution,salt_kg",
    [
        # The worked case used across these tests: 12,500 m2 x 0.155 m of
        # 285 g/L brine with 20 mm of rain.
        (
            12_500.0, 0.155, 285.0, 20.0,
            1937.5, 250.0, 2187.5, 252.42857142857142, 11.428571428571432,
            552_187.5,
        ),
        # Round numbers: 1 m2-free arithmetic. 50 mm doubles the volume, so the
        # salinity falls to two thirds.
        (10_000.0, 0.100, 300.0, 50.0, 1000.0, 500.0, 1500.0, 200.0, 33.333333333333329, 300_000.0),
        # No rain: nothing may change, and the salt is exactly the 250 g/L x
        # 1,000 m3 the pan held.
        (5_000.0, 0.200, 250.0, 0.0, 1000.0, 0.0, 1000.0, 250.0, 0.0, 250_000.0),
        # Rain deeper than the brine (200 mm on 50 mm) does not flush salt: it
        # dilutes it to a fifth of the starting concentration.
        (2_000.0, 0.050, 200.0, 200.0, 100.0, 400.0, 500.0, 40.0, 80.0, 20_000.0),
        # A rain equal to half the starting depth. The 100 g/L halves again
        # only once the rain is equal to the depth; here the volume grows by
        # exactly one third.
        (3_000.0, 0.200, 100.0, 100.0, 600.0, 300.0, 900.0, 66.666666666666671, 33.333333333333329, 60_000.0),
    ],
)
def test_golden_values_pin_the_equations_and_the_unit_conversion(
    area_m2, initial_depth_m, salinity_g_l, rainfall_mm,
    v0, vr, v1, c1, dilution, salt_kg,
):
    result = MassBalanceService().evaluate(
        area_m2=area_m2, initial_depth_m=initial_depth_m,
        initial_salinity_g_l=salinity_g_l, rainfall_mm=rainfall_mm,
    )
    # The boundary conversion first: the figure the caller typed is millimetres
    # and the arithmetic runs in metres.
    assert result.rainfall_m == pytest.approx(rainfall_mm / 1000.0)
    assert result.rainfall_mm == pytest.approx(rainfall_mm)
    assert result.initial_volume_m3 == pytest.approx(v0)
    assert result.rainfall_volume_m3 == pytest.approx(vr)
    assert result.post_rain_volume_m3 == pytest.approx(v1)
    assert result.post_rain_salinity_g_l == pytest.approx(c1)
    assert result.salinity_drop_g_l == pytest.approx(salinity_g_l - c1)
    assert result.dilution_percent == pytest.approx(dilution)
    assert result.initial_salt_mass_kg == pytest.approx(salt_kg)
    assert result.post_rain_salt_mass_kg == pytest.approx(salt_kg)
    assert result.salt_mass_conserved is True
    assert result.post_rain_depth_m == pytest.approx(initial_depth_m + rainfall_mm / 1000.0)


# -- the sweep the what-if panel is built on ------------------------------


def test_a_sweep_always_covers_the_six_presets_ascending():
    service = MassBalanceService()
    sweep = service.sweep(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M, initial_salinity_g_l=SALINITY_G_L,
    )
    assert [scenario.rainfall_mm for scenario in sweep] == [
        0.0, 5.0, 10.0, 20.0, 50.0, 100.0,
    ]
    assert STANDARD_RAINFALL_SCENARIOS_MM == (0.0, 5.0, 10.0, 20.0, 50.0, 100.0)


def test_a_custom_depth_joins_the_presets_instead_of_replacing_them():
    service = MassBalanceService()
    sweep = service.sweep(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M, initial_salinity_g_l=SALINITY_G_L,
        rainfall_scenarios_mm=[12.5],
    )
    assert [scenario.rainfall_mm for scenario in sweep] == [
        0.0, 5.0, 10.0, 12.5, 20.0, 50.0, 100.0,
    ]


def test_a_custom_depth_that_repeats_a_preset_is_not_counted_twice():
    service = MassBalanceService()
    sweep = service.sweep(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M, initial_salinity_g_l=SALINITY_G_L,
        rainfall_scenarios_mm=[20.0, 20.0, 5.0],
    )
    assert [scenario.rainfall_mm for scenario in sweep] == [
        0.0, 5.0, 10.0, 20.0, 50.0, 100.0,
    ]


def test_every_scenario_in_a_sweep_shares_one_starting_volume():
    service = MassBalanceService()
    sweep = service.sweep(
        area_m2=AREA_M2, initial_depth_m=DEPTH_M, initial_salinity_g_l=SALINITY_G_L,
        rainfall_scenarios_mm=[33.0],
    )
    assert {scenario.initial_volume_m3 for scenario in sweep} == {1937.5}


# -- the disclosure that travels with the numbers -------------------------


def test_the_disclaimer_says_this_is_physics_and_not_a_prediction():
    assert SIMULATOR_DISCLAIMER == (
        "Physics-based simulation under simplifying assumptions; "
        "not an ML prediction."
    )


def test_every_stated_assumption_is_a_process_the_model_leaves_out():
    """The list is the model's actual limits, so each entry must name one."""
    joined = " ".join(SIMULATION_ASSUMPTIONS).lower()
    for excluded in (
        "evaporation",
        "drainage",
        "overflow",
        "seepage",
        "transfer",
        "crystallization",
    ):
        assert excluded in joined, f"{excluded} is not disclosed as an assumption"
    assert any("complete mixing" in entry.lower() for entry in SIMULATION_ASSUMPTIONS)
