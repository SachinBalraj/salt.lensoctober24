import { describe, expect, it } from "vitest";
import {
  EMPTY_SIMULATION_FORM,
  INPUT_RANGES,
  MASS_BALANCE_EQUATIONS,
  RAINFALL_LIMITS,
  SIMULATOR_DISCLAIMER,
  SIMULATION_ASSUMPTIONS,
  STANDARD_RAINFALL_SCENARIOS_MM,
  buildDilutionSeries,
  buildSimulationRequest,
  checkSweepConsistency,
  findScenario,
  formatDilution,
  formatRainfall,
  formatSalinity,
  formatSaltMass,
  formatVolume,
  needsTwoScenarios,
  parseCustomRainfall,
  salinityAxis,
  salinityTickLabel,
  saltMassKg,
  saltMassUnit,
  seedSimulationForm,
  summarizeScenario,
  type RainfallScenario,
  type RainfallSweep,
} from "@/lib/simulation";

/** A 12,500 m² pan holding 0.155 m of 285 g/L brine, with 20 mm of rain. */
const INPUTS = {
  area_m2: 12_500.0,
  initial_depth_m: 0.155,
  initial_salinity_g_l: 285.0,
};

function scenario(overrides: Partial<RainfallScenario> = {}): RainfallScenario {
  const rainfallMm = overrides.rainfall_mm ?? 20.0;
  return {
    rainfall_mm: rainfallMm,
    label: `${rainfallMm} mm`,
    is_standard_preset: true,
    rainfall_volume_m3: 12_500.0 * (rainfallMm / 1000),
    post_rain_volume_m3: 1937.5 + 12_500.0 * (rainfallMm / 1000),
    post_rain_depth_m: 0.155 + rainfallMm / 1000,
    post_rain_salinity_g_l: 252.42857142857142,
    salinity_drop_g_l: 32.571428571428584,
    dilution_percent: 11.428571428571432,
    dilution_percent_reason: null,
    ...overrides,
  };
}

/** The six presets, with the figures the server's mass balance returns. */
const PRESETS: RainfallScenario[] = [
  { rainfall_mm: 0, post_rain_salinity_g_l: 285.0, dilution_percent: 0.0, salinity_drop_g_l: 0.0, post_rain_volume_m3: 1937.5, rainfall_volume_m3: 0 },
  { rainfall_mm: 5, post_rain_salinity_g_l: 276.09375, dilution_percent: 3.125, salinity_drop_g_l: 8.90625, post_rain_volume_m3: 2000.0 },
  { rainfall_mm: 10, post_rain_salinity_g_l: 267.7272727272727, dilution_percent: 6.06060606060606, salinity_drop_g_l: 17.2727272727273, post_rain_volume_m3: 2062.5 },
  { rainfall_mm: 20, post_rain_salinity_g_l: 252.42857142857142, dilution_percent: 11.428571428571432, salinity_drop_g_l: 32.571428571428584, post_rain_volume_m3: 2187.5 },
  { rainfall_mm: 50, post_rain_salinity_g_l: 215.48780487804878, dilution_percent: 24.390243902439025, salinity_drop_g_l: 69.51219512195122, post_rain_volume_m3: 2562.5 },
  { rainfall_mm: 100, post_rain_salinity_g_l: 173.23529411764707, dilution_percent: 39.2156862745098, salinity_drop_g_l: 111.76470588235293, post_rain_volume_m3: 3187.5 },
].map((figures) => {
  const rainfallMm = figures.rainfall_mm;
  return scenario({
    rainfall_mm: rainfallMm,
    label: rainfallMm === 0 ? "0 mm — no rain" : `${rainfallMm} mm`,
    post_rain_salinity_g_l: figures.post_rain_salinity_g_l,
    dilution_percent: figures.dilution_percent,
    salinity_drop_g_l: figures.salinity_drop_g_l,
    post_rain_volume_m3: figures.post_rain_volume_m3,
    rainfall_volume_m3: 12_500.0 * (rainfallMm / 1000),
  });
});

function sweep(overrides: Partial<RainfallSweep> = {}): RainfallSweep {
  return {
    area_m2: 12_500.0,
    initial_depth_m: 0.155,
    initial_salinity_g_l: 285.0,
    initial_volume_m3: 1937.5,
    standard_scenarios_mm: [...STANDARD_RAINFALL_SCENARIOS_MM],
    scenarios: PRESETS,
    is_simulated: true,
    model_name: "rainfall-dilution-mass-balance-v1",
    disclaimer: SIMULATOR_DISCLAIMER,
    assumptions: SIMULATION_ASSUMPTIONS,
    equations: MASS_BALANCE_EQUATIONS,
    ...overrides,
  };
}

// -- the presets ----------------------------------------------------------

describe("the rainfall presets", () => {
  it("offer zero alongside the depths an operator would choose", () => {
    expect(STANDARD_RAINFALL_SCENARIOS_MM).toEqual([0, 5, 10, 20, 50, 100]);
  });

  it("treat zero as a scenario rather than the absence of one", () => {
    // 0 mm is the "nothing fell" reference the dilution figures are read against.
    expect(STANDARD_RAINFALL_SCENARIOS_MM).toContain(0);
  });
});

// -- the disclosure that travels with the numbers -------------------------

describe("the disclosure", () => {
  it("says plainly that this is physics and not a prediction", () => {
    expect(SIMULATOR_DISCLAIMER).toBe(
      "Physics-based simulation under simplifying assumptions; not an ML prediction.",
    );
  });

  it("names every process the model leaves out", () => {
    const joined = SIMULATION_ASSUMPTIONS.join(" ").toLowerCase();
    for (const excluded of [
      "complete mixing",
      "evaporation",
      "drainage",
      "overflow",
      "seepage",
      "transfer",
      "crystallization",
    ]) {
      expect(joined).toContain(excluded);
    }
  });

  it("states the balance itself so a reader can check the answer", () => {
    expect(MASS_BALANCE_EQUATIONS).toHaveLength(5);
    expect(MASS_BALANCE_EQUATIONS[0]).toContain("V0 = A");
    expect(MASS_BALANCE_EQUATIONS[4]).toContain("Dilution %");
  });
});

// -- the form -------------------------------------------------------------

describe("the what-if form", () => {
  it("converts a depth in centimetres to the metres the balance needs", () => {
    const { payload, errors } = buildSimulationRequest({
      area_m2: "12500",
      initial_depth_cm: "15.5",
      initial_salinity_g_l: "285",
    });
    expect(errors).toEqual({});
    expect(payload).toEqual(INPUTS);
    // 15.5 cm is 0.155 m, the single unit conversion in the module.
    expect(payload.initial_depth_m).toBeCloseTo(0.155, 12);
  });

  it("asks for a value rather than assuming one", () => {
    const { errors } = buildSimulationRequest(EMPTY_SIMULATION_FORM);
    expect(Object.keys(errors).sort()).toEqual([
      "area_m2",
      "initial_depth_cm",
      "initial_salinity_g_l",
    ]);
    expect(errors.area_m2).toContain("Enter the pan area");
  });

  it("refuses a zero area, because the balance divides by it", () => {
    const { errors } = buildSimulationRequest({
      area_m2: "0",
      initial_depth_cm: "15.5",
      initial_salinity_g_l: "285",
    });
    expect(errors.area_m2).toContain("greater than zero");
  });

  it("refuses a zero depth, because there is no brine to dilute", () => {
    const { errors } = buildSimulationRequest({
      area_m2: "12500",
      initial_depth_cm: "0",
      initial_salinity_g_l: "285",
    });
    expect(errors.initial_depth_cm).toContain("no salinity to dilute");
  });

  it("catches a negative salinity and a mistyped unit", () => {
    const negative = buildSimulationRequest({
      area_m2: "12500",
      initial_depth_cm: "15.5",
      initial_salinity_g_l: "-5",
    });
    expect(negative.errors.initial_salinity_g_l).toContain("cannot be negative");

    const mistyped = buildSimulationRequest({
      area_m2: "12500",
      initial_depth_cm: "15.5",
      initial_salinity_g_l: "2850",
    });
    expect(mistyped.errors.initial_salinity_g_l).toContain("g/L");
  });

  it("rejects text where a number belongs", () => {
    const { errors } = buildSimulationRequest({
      area_m2: "12 500 m²",
      initial_depth_cm: "15.5",
      initial_salinity_g_l: "285",
    });
    expect(errors.area_m2).toContain("must be a number");
  });

  it("seeds from a pan's record and leaves an unknown figure blank", () => {
    expect(seedSimulationForm(12_500, 15.5, 285)).toEqual({
      area_m2: "12500",
      initial_depth_cm: "15.5",
      initial_salinity_g_l: "285",
    });
    // Nothing is invented for a pan with no recorded salinity.
    expect(seedSimulationForm(12_500, 15.5, null)).toEqual({
      area_m2: "12500",
      initial_depth_cm: "15.5",
      initial_salinity_g_l: "",
    });
  });
});

describe("a custom rainfall depth", () => {
  it("accepts a depth the presets do not cover", () => {
    expect(parseCustomRainfall("12.5")).toEqual({ value: 12.5, error: null });
  });

  it("accepts zero, which is a real scenario", () => {
    expect(parseCustomRainfall("0")).toEqual({ value: 0, error: null });
  });

  it("explains an empty or non-numeric entry", () => {
    expect(parseCustomRainfall("").error).toContain("Enter a rainfall depth");
    expect(parseCustomRainfall("20mm").error).toContain("must be a number");
  });

  it("refuses a depth beyond any plausible storm", () => {
    const { value, error } = parseCustomRainfall("50000");
    expect(value).toBeNull();
    expect(error).toContain("beyond any plausible storm");
  });

  it("refuses a negative depth rather than reading it as no rain", () => {
    const { value, error } = parseCustomRainfall("-5");
    expect(value).toBeNull();
    expect(error).toContain("cannot be negative");
  });

  it("agrees with the server's own ceiling", () => {
    expect(parseCustomRainfall(String(RAINFALL_LIMITS.max)).value).toBe(
      RAINFALL_LIMITS.max,
    );
  });
});

// -- formatting -----------------------------------------------------------

describe("formatting", () => {
  it("keeps a measured zero distinct from a figure that does not exist", () => {
    expect(formatSalinity(0)).toBe("0.0");
    expect(formatSalinity(null)).toBe("—");
    expect(formatDilution(0)).toBe("0.0");
    expect(formatDilution(null)).toBe("—");
    expect(formatVolume(0)).toBe("0.0");
    expect(formatVolume(undefined)).toBe("—");
  });

  it("reports a salinity to one decimal place", () => {
    expect(formatSalinity(252.42857142857142)).toBe("252.4");
  });

  it("drops a trailing .0 from a whole-number rainfall depth", () => {
    expect(formatRainfall(20)).toBe("20");
    expect(formatRainfall(12.5)).toBe("12.5");
    expect(formatRainfall(0)).toBe("0");
    expect(formatRainfall(null)).toBe("—");
  });

  it("reads a large salt mass in tonnes and a small one in kilograms", () => {
    expect(formatSaltMass(552_187.5)).toBe("552.19");
    expect(saltMassUnit(552_187.5)).toBe("t");
    expect(formatSaltMass(4.5)).toBe("4.5");
    expect(saltMassUnit(4.5)).toBe("kg");
  });

  it("converts a salinity and a volume into a salt mass", () => {
    // 285 g/L across 1937.5 m3 is 552,187.5 kg, which reads as 552.19 t.
    expect(saltMassKg(1937.5, 285)).toBeCloseTo(552_187.5, 6);
    expect(formatSaltMass(saltMassKg(1937.5, 285))).toBe("552.19");
    expect(saltMassUnit(saltMassKg(1937.5, 285))).toBe("t");
  });

  it("reports no salt mass rather than zero for a figure that is missing", () => {
    expect(saltMassKg(null, 285)).toBeNull();
    expect(saltMassKg(1937.5, null)).toBeNull();
    // Fresh water genuinely holds no salt, so that is a real zero.
    expect(saltMassKg(1937.5, 0)).toBe(0);
  });
});

// -- chart shaping --------------------------------------------------------

describe("the rainfall-against-salinity curve", () => {
  it("plots one point per evaluated scenario, in rainfall order", () => {
    const series = buildDilutionSeries(PRESETS, null);
    expect(series.map((point) => point.rainfall_mm)).toEqual([0, 5, 10, 20, 50, 100]);
    expect(series).toHaveLength(PRESETS.length);
  });

  it("marks the selected scenario and no other", () => {
    const series = buildDilutionSeries(PRESETS, 20);
    expect(series.filter((point) => point.selected).map((point) => point.rainfall_mm)).toEqual([20]);
  });

  it("marks nothing when no scenario is selected", () => {
    const series = buildDilutionSeries(PRESETS, null);
    expect(series.some((point) => point.selected)).toBe(false);
  });

  it("plots a custom depth on the same line as the presets", () => {
    const custom = scenario({
      rainfall_mm: 12.5,
      label: "12.5 mm — custom",
      is_standard_preset: false,
    });
    const series = buildDilutionSeries([...PRESETS, custom], 12.5);
    const point = series.find((entry) => entry.rainfall_mm === 12.5);
    expect(point?.is_standard_preset).toBe(false);
    expect(point?.selected).toBe(true);
  });

  it("adds no point for a rainfall depth that was never evaluated", () => {
    // Every position on the line names a scenario the server actually ran.
    const series = buildDilutionSeries(PRESETS, 35);
    expect(series.some((point) => point.rainfall_mm === 35)).toBe(false);
    expect(series.filter((point) => point.selected)).toHaveLength(0);
  });

  it("will not draw a curve from a single point", () => {
    expect(needsTwoScenarios(buildDilutionSeries(PRESETS, null))).toBe(false);
    expect(needsTwoScenarios(buildDilutionSeries([PRESETS[0]], 0))).toBe(true);
  });
});

/**
 * The September north pan sweep, exactly as the server returns it: 12,300 m²
 * holding 0.20 m of 250 g/L brine, swept over the presets plus the 25 mm depth
 * in force as the forecast.
 */
const NORTH_PAN_SEPTEMBER: RainfallScenario[] = [
  { rainfall_mm: 0, post_rain_salinity_g_l: 250.0, dilution_percent: 0.0 },
  { rainfall_mm: 5, post_rain_salinity_g_l: 243.90243902439025, dilution_percent: 2.4390243902439022 },
  { rainfall_mm: 10, post_rain_salinity_g_l: 238.0952380952381, dilution_percent: 4.761904761904759 },
  { rainfall_mm: 20, post_rain_salinity_g_l: 227.27272727272728, dilution_percent: 9.090909090909088 },
  { rainfall_mm: 25, post_rain_salinity_g_l: 222.22222222222223, dilution_percent: 11.111111111111109 },
  { rainfall_mm: 50, post_rain_salinity_g_l: 200.0, dilution_percent: 20.0 },
  { rainfall_mm: 100, post_rain_salinity_g_l: 166.66666666666669, dilution_percent: 33.33333333333333 },
].map((overrides) => scenario(overrides));

// -- the salinity axis ----------------------------------------------------

describe("the salinity axis", () => {
  const september = buildDilutionSeries(NORTH_PAN_SEPTEMBER, 25);
  const axis = salinityAxis(september.map((point) => point.salinity));

  it("increases from bottom to top", () => {
    // A vertical salinity axis read the usual way: the larger value is the
    // higher one. The curve descends left to right because rain removes salt,
    // and that descent is only legible if the axis itself ascends.
    expect(axis).not.toBeNull();
    expect(axis!.domain[0]).toBeLessThan(axis!.domain[1]);
  });

  it("starts near the salinity it is plotting, not at zero", () => {
    // Anchoring at zero squashes 250 g/L and 222.22 g/L into a few pixels at
    // the top of the plot, so the chart cannot answer the question it is on
    // screen to answer.
    expect(axis!.domain[0]).toBeGreaterThan(0);
    expect(axis!.domain[0]).toBeLessThan(166.66666666666669);
  });

  it("covers every scenario it plots", () => {
    for (const point of september) {
      expect(point.salinity).toBeGreaterThanOrEqual(axis!.domain[0]);
      expect(point.salinity).toBeLessThanOrEqual(axis!.domain[1]);
    }
  });

  it("leaves the highest scenario clear of the top edge", () => {
    // 250 g/L is the un-rained brine and a round number, so an axis rounded to
    // fit it exactly would put that marker on the frame, half clipped.
    expect(axis!.domain[1]).toBeGreaterThan(250);
  });

  it("ticks on readable steps, ascending from the bottom", () => {
    expect(axis!.ticks).toEqual([150, 175, 200, 225, 250, 275]);
    expect(axis!.step).toBe(25);
    for (let index = 1; index < axis!.ticks.length; index += 1) {
      expect(axis!.ticks[index]).toBeGreaterThan(axis!.ticks[index - 1]);
    }
  });

  it("puts the 25 mm result between the two ticks that bracket it", () => {
    // 222.22 g/L is the answer for the depth in force, and it has to be
    // readable off the axis rather than inferred.
    const forecast = september.find((point) => point.rainfall_mm === 25);
    expect(forecast?.salinity).toBeCloseTo(222.22222222222223, 6);
    expect(forecast?.dilution).toBe("11.1");
    const below = [...axis!.ticks].reverse().find((tick) => tick < 222.22222222222223);
    const above = axis!.ticks.find((tick) => tick > 222.22222222222223);
    expect(below).toBe(200);
    expect(above).toBe(225);
  });

  it("spans a flat sweep rather than collapsing to one line", () => {
    const flat = salinityAxis([222.22, 222.22]);
    expect(flat).not.toBeNull();
    expect(flat!.domain[0]).toBeLessThan(222.22);
    expect(flat!.domain[1]).toBeGreaterThan(222.22);
  });

  it("rounds a tight range outwards to whole steps", () => {
    // 166.67 to 250 would otherwise tick on 20.83 g/L, which is not a number a
    // reader can hold in their head.
    const tight = salinityAxis([166.6667, 250]);
    expect(tight!.step).toBe(25);
    expect(tight!.ticks).toEqual([150, 175, 200, 225, 250, 275]);
  });

  it("handles a range needing a half step without float drift", () => {
    const half = salinityAxis([100, 140]);
    expect(half!.step).toBe(10);
    for (const tick of half!.ticks) {
      expect(Number.isInteger(tick)).toBe(true);
    }
  });

  it("declines to invent a range when there is no salinity to span", () => {
    expect(salinityAxis([])).toBeNull();
    expect(salinityAxis([Number.NaN, Number.POSITIVE_INFINITY])).toBeNull();
  });
});

describe("salinity tick labels", () => {
  it("carries no decimals an axis on whole steps cannot resolve", () => {
    expect(salinityTickLabel(175, 25)).toBe("175");
    expect(salinityTickLabel(200, 25)).toBe("200");
  });

  it("carries the one decimal a half-step axis needs", () => {
    expect(salinityTickLabel(162.5, 2.5)).toBe("162.5");
  });

  it("says so rather than printing a broken label", () => {
    expect(salinityTickLabel(Number.NaN, 25)).toBe("—");
  });
});

describe("selecting a scenario", () => {
  it("finds the scenario at the chosen depth", () => {
    expect(findScenario(PRESETS, 20)?.rainfall_mm).toBe(20);
  });

  it("finds nothing for a depth that was not swept", () => {
    expect(findScenario(PRESETS, 35)).toBeNull();
    expect(findScenario(PRESETS, null)).toBeNull();
  });
});

// -- reading the result ---------------------------------------------------

describe("the summary line", () => {
  it("leads with the outcome and the dilution it represents", () => {
    const summary = summarizeScenario(PRESETS[3], 285);
    expect(summary.headline).toContain("20 mm");
    expect(summary.headline).toContain("252.4 g/L");
    expect(summary.headline).toContain("285.0 g/L");
    expect(summary.detail).toContain("11.4%");
  });

  it("reports the undefined percentage rather than showing a zero", () => {
    const fresh = scenario({
      rainfall_mm: 20,
      post_rain_salinity_g_l: 0,
      dilution_percent: null,
      salinity_drop_g_l: 0,
      dilution_percent_reason:
        "Expected dilution percentage is undefined for a starting salinity of 0 g/L.",
    });
    const summary = summarizeScenario(fresh, 0);
    expect(summary.headline).toContain("0.0 g/L");
    expect(summary.detail).toContain("undefined");
    expect(summary.detail).not.toContain("0.0%");
  });

  it("says so when no scenario is selected", () => {
    expect(summarizeScenario(null, 285).headline).toBe(
      "No rainfall scenario is selected.",
    );
  });
});

describe("checking the sweep the server returned", () => {
  it("accepts a sweep whose salinity falls with rainfall", () => {
    expect(checkSweepConsistency(sweep())).toEqual({ consistent: true, problems: [] });
  });

  it("reports a curve that rises instead of falling", () => {
    const rising = sweep({
      scenarios: [
        scenario({ rainfall_mm: 0, post_rain_salinity_g_l: 100 }),
        scenario({ rainfall_mm: 20, post_rain_salinity_g_l: 200 }),
      ],
    });
    const result = checkSweepConsistency(rising);
    expect(result.consistent).toBe(false);
    expect(result.problems[0]).toContain("does not fall");
  });

  it("reports a scenario that concentrates the brine past its own start", () => {
    const concentrated = sweep({
      scenarios: [scenario({ rainfall_mm: 0, post_rain_salinity_g_l: 400 })],
    });
    const result = checkSweepConsistency(concentrated);
    expect(result.consistent).toBe(false);
    expect(result.problems[0]).toContain("exceeds the salinity");
  });

  it("reports a pan that holds less water after the rain than before", () => {
    const leaking = sweep({
      scenarios: [scenario({ rainfall_mm: 0, post_rain_volume_m3: 100 })],
    });
    const result = checkSweepConsistency(leaking);
    expect(result.consistent).toBe(false);
    expect(result.problems[0]).toContain("less water");
  });
});

describe("the input ranges", () => {
  it("are wide enough for a real pan and mirror the server's ceilings", () => {
    expect(INPUT_RANGES.area_m2.max).toBe(1_000_000);
    expect(INPUT_RANGES.initial_depth_m.max).toBe(10);
    expect(INPUT_RANGES.initial_salinity_g_l.max).toBe(500);
  });
});
