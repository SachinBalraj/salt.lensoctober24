/**
 * Rainfall-simulator domain types, labels, form validation and chart shaping.
 *
 * Free of React and of `fetch`, so the rules the what-if panel depends on can be
 * unit-tested directly.
 *
 * The dividing line in this module is deliberate: it holds the *presentation* of
 * the mass balance and none of the balance itself. The equations live on the
 * server, in `app/services/mass_balance.py`, and are the only implementation of
 * them. A second copy in TypeScript would be free to drift from the one the API
 * reports, and two versions of a mass balance is one too many. So a scenario
 * here is always something the server computed, and this module's job is to say
 * clearly what it means and where its limits are.
 *
 * The disclosure is part of the contract, not decoration. `SIMULATOR_DISCLAIMER`
 * and `SIMULATION_ASSUMPTIONS` are rendered next to the results on every screen
 * that shows them, and the API returns its own copy of both. A reader who
 * accepts the numbers has to have been shown the assumptions first.
 */

/** One resolved rainfall depth, exactly as the API returns it. */
export type RainfallScenario = {
  rainfall_mm: number;
  label: string;
  is_standard_preset: boolean;
  rainfall_volume_m3: number;
  post_rain_volume_m3: number;
  post_rain_depth_m: number;
  post_rain_salinity_g_l: number;
  salinity_drop_g_l: number;
  /** `null` only when the starting salinity was 0 g/L. */
  dilution_percent: number | null;
  dilution_percent_reason: string | null;
};

/** Every rainfall depth resolved against one starting brine. */
export type RainfallSweep = {
  area_m2: number;
  initial_depth_m: number;
  initial_salinity_g_l: number;
  initial_volume_m3: number;
  standard_scenarios_mm: number[];
  scenarios: RainfallScenario[];
  is_simulated: true;
  model_name: string;
  disclaimer: string;
  assumptions: string[];
  equations: string[];
};

/** The whole balance for a single rainfall event. */
export type MassBalanceResult = {
  area_m2: number;
  initial_depth_m: number;
  initial_salinity_g_l: number;
  rainfall_mm: number;
  initial_volume_m3: number;
  rainfall_volume_m3: number;
  post_rain_volume_m3: number;
  initial_salt_mass_kg: number;
  post_rain_salt_mass_kg: number;
  salt_mass_conserved: boolean;
  post_rain_salinity_g_l: number;
  salinity_drop_g_l: number;
  dilution_percent: number | null;
  dilution_percent_reason: string | null;
  post_rain_depth_m: number;
  is_simulated: true;
  model_name: string;
  disclaimer: string;
  assumptions: string[];
  equations: string[];
};

/** The four inputs the balance needs, in the units the API expects. */
export type SimulationInputs = {
  area_m2: number;
  initial_depth_m: number;
  initial_salinity_g_l: number;
};

/**
 * The rainfall depths the what-if panel offers as presets, in millimetres.
 *
 * Zero is a scenario and not the absence of one: it is the "nothing fell"
 * reference every other column is compared against, and it is what makes the
 * dilution figures interpretable.
 */
export const STANDARD_RAINFALL_SCENARIOS_MM = [0, 5, 10, 20, 50, 100] as const;

export type StandardRainfallScenarioMm =
  (typeof STANDARD_RAINFALL_SCENARIOS_MM)[number];

/** The one line that must appear wherever these numbers appear. */
export const SIMULATOR_DISCLAIMER =
  "Physics-based simulation under simplifying assumptions; not an ML prediction.";

/**
 * The processes this model leaves out.
 *
 * Mirrors the server's list so the disclosure is on screen before the first
 * request has returned. The server's copy is what gets rendered once a sweep is
 * in hand; this one covers the state before then.
 */
export const SIMULATION_ASSUMPTIONS: string[] = [
  "Complete mixing: the rain and the brine reach one uniform salinity instantly, so a single concentration describes the whole pan.",
  "No evaporation during the event: rainfall is the only water that moves.",
  "No drainage: water leaves the pan by no route while the event runs.",
  "No overflow: the pan is assumed deep enough to hold every drop of rain, so nothing spills and no brine is lost over the wall.",
  "No seepage: no water percolates through the pan floor.",
  "No brine transfer: brine is neither pumped in nor drawn off during the event, so the pan keeps exactly the salt it started with.",
  "No crystallization during the calculation: salt stays dissolved, so the salinity is a solution concentration and no salt is precipitated out.",
  "Rainfall is a single instantaneous depth: the model does not resolve when the rain fell or how fast, only how much in total.",
];

/** The balance, as reported by the API, with the units spelled out. */
export const MASS_BALANCE_EQUATIONS: string[] = [
  "V0 = A × D0 — initial brine volume (m³)",
  "Vr = A × R — rainfall volume (m³)",
  "V1 = V0 + Vr — post-rain volume (m³)",
  "C1 = C0 × V0 / V1 — predicted post-rain salinity (g/L)",
  "Dilution % = ((C0 − C1) / C0) × 100",
];

/**
 * Plausibility bounds for the three what-if inputs.
 *
 * Deliberately wide, and mirroring the server's ceilings: these catch a mistyped
 * digit, they do not rule out an unusual but real pan. The area and the depth
 * must be above zero because the balance divides by their product, but the
 * lower bound is enforced on the server, which explains why in operator terms.
 */
export const INPUT_RANGES = {
  area_m2: { label: "Pan area", unit: "m²", max: 1_000_000 },
  initial_depth_m: { label: "Initial brine depth", unit: "m", max: 10 },
  initial_salinity_g_l: { label: "Current salinity", unit: "g/L", max: 500 },
} as const;

export type InputKey = keyof typeof INPUT_RANGES;

/** The rainfall depths a custom entry may request, mirroring the server. */
export const RAINFALL_LIMITS = { min: 0, max: 1_000 } as const;

/**
 * A number the operator typed, allowing a leading minus.
 *
 * The sign is permitted so that a negative figure reaches the specific checks
 * below and is told *why* it is wrong, rather than being dismissed as not a
 * number at all.
 */
export const NUMERIC_PATTERN = /^-?\d*\.?\d+$/;

/** Text shown in place of a figure that does not exist. */
export const UNAVAILABLE = "—";

/** What the operator types into the what-if form. */
export type SimulationFormValues = {
  area_m2: string;
  initial_depth_cm: string;
  initial_salinity_g_l: string;
};

export type SimulationFormErrors = Partial<
  Record<keyof SimulationFormValues, string>
> & { form?: string };

export const EMPTY_SIMULATION_FORM: SimulationFormValues = {
  area_m2: "",
  initial_depth_cm: "",
  initial_salinity_g_l: "",
};

/**
 * Seeds the form from a pan's registered area, its normal depth and an observed
 * salinity.
 *
 * Each figure keeps the provenance it arrived with, so a value typed by the
 * operator and a value read from a record are both shown as what they are.
 * Anything unavailable is left blank rather than filled, because an empty field
 * the operator can see is honest where a plausible default is not.
 */
export function seedSimulationForm(
  area_m2: number | null,
  normalDepthCm: number | null,
  salinityG_l: number | null,
): SimulationFormValues {
  return {
    area_m2:
      area_m2 === null || area_m2 === undefined ? "" : String(area_m2),
    initial_depth_cm:
      normalDepthCm === null || normalDepthCm === undefined
        ? ""
        : String(normalDepthCm),
    initial_salinity_g_l:
      salinityG_l === null || salinityG_l === undefined ? "" : String(salinityG_l),
  };
}

/**
 * Validates the form and builds the request payload.
 *
 * Depth is entered in centimetres because that is how a pan's depth is recorded
 * and registered everywhere else in SaltLens, and converted to metres here.
 * The conversion happens once, in one place, so the unit boundary is visible.
 */
export function buildSimulationRequest(
  values: SimulationFormValues,
): { payload: SimulationInputs; errors: SimulationFormErrors } {
  const errors: SimulationFormErrors = {};

  const area = parseNumeric(values.area_m2, "area_m2", errors, "Pan area");
  const depthCm = parseNumeric(
    values.initial_depth_cm,
    "initial_depth_cm",
    errors,
    "Initial brine depth",
  );
  const salinity = parseNumeric(
    values.initial_salinity_g_l,
    "initial_salinity_g_l",
    errors,
    "Current salinity",
  );

  if (area !== null && area <= 0) {
    errors.area_m2 =
      "Enter a pan area greater than zero. Every volume in the balance is the area times a depth.";
  }
  if (area !== null && area > INPUT_RANGES.area_m2.max) {
    errors.area_m2 = `A pan area above ${INPUT_RANGES.area_m2.max.toLocaleString("en-GB")} m² is a typed digit, not a pan. Check the units are m².`;
  }
  if (depthCm !== null && depthCm <= 0) {
    errors.initial_depth_cm =
      "Enter a depth greater than zero. With no brine in the pan there is no salinity to dilute.";
  }
  if (depthCm !== null && depthCm > INPUT_RANGES.initial_depth_m.max * 100) {
    errors.initial_depth_cm = `A depth above ${INPUT_RANGES.initial_depth_m.max} m is a typed digit, not a pan. Check the units are cm.`;
  }
  if (salinity !== null && salinity < 0) {
    errors.initial_salinity_g_l =
      "Salinity cannot be negative. A negative concentration has no physical meaning.";
  }
  if (salinity !== null && salinity === 0) {
    errors.initial_salinity_g_l =
      "Enter a salinity greater than zero. There is no brine to dilute in a pan holding fresh water.";
  }
  if (salinity !== null && salinity > INPUT_RANGES.initial_salinity_g_l.max) {
    errors.initial_salinity_g_l = `Salinity above ${INPUT_RANGES.initial_salinity_g_l.max} g/L is a typed digit, not brine. Check the units are g/L.`;
  }

  if (Object.keys(errors).length > 0 || area === null || depthCm === null || salinity === null) {
    return { payload: { area_m2: 0, initial_depth_m: 0, initial_salinity_g_l: 0 }, errors };
  }

  return {
    payload: {
      area_m2: area,
      // cm -> m, the one unit conversion in this module.
      initial_depth_m: depthCm / 100,
      initial_salinity_g_l: salinity,
    },
    errors: {},
  };
}

function parseNumeric(
  raw: string,
  field: keyof SimulationFormValues,
  errors: SimulationFormErrors,
  label: string,
): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") {
    errors[field] = `Enter the ${label.toLowerCase()}. A what-if run needs all three inputs, and none of them can be assumed.`;
    return null;
  }
  if (!NUMERIC_PATTERN.test(trimmed)) {
    errors[field] = `${label} must be a number.`;
    return null;
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value)) {
    errors[field] = `${label} must be a number.`;
    return null;
  }
  return value;
}

/**
 * Validates a custom rainfall depth, returning the depth in millimetres.
 *
 * Zero is allowed and is a real scenario, so it is not treated as "nothing
 * entered". Returns `null` alongside the reason rather than a bare failure, so
 * the caller can show the message next to the field.
 */
export function parseCustomRainfall(
  raw: string,
): { value: number | null; error: string | null } {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return { value: null, error: "Enter a rainfall depth in millimetres." };
  }
  if (!NUMERIC_PATTERN.test(trimmed)) {
    return { value: null, error: "Rainfall must be a number in millimetres." };
  }
  const value = Number(trimmed);
  if (!Number.isFinite(value)) {
    return { value: null, error: "Rainfall must be a number in millimetres." };
  }
  if (value < RAINFALL_LIMITS.min) {
    return {
      value: null,
      error:
        "Rainfall cannot be negative. Zero is a valid scenario and means no rain fell.",
    };
  }
  if (value > RAINFALL_LIMITS.max) {
    return {
      value: null,
      error: `Rainfall above ${RAINFALL_LIMITS.max.toLocaleString("en-GB")} mm is beyond any plausible storm for this model.`,
    };
  }
  return { value, error: null };
}

// -- formatting -----------------------------------------------------------

/** A salinity, to one decimal, or an explicit absence. */
export function formatSalinity(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return UNAVAILABLE;
  }
  return value.toFixed(1);
}

/** A volume in m³, to one decimal: the balance is a cubic-metre calculation. */
export function formatVolume(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return UNAVAILABLE;
  }
  return value.toLocaleString("en-GB", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}

/** A dilution percentage, or an explicit absence rather than a zero. */
export function formatDilution(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return UNAVAILABLE;
  }
  return value.toFixed(1);
}

/** A rainfall depth, without a trailing `.0` on the whole-number presets. */
export function formatRainfall(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return UNAVAILABLE;
  }
  return `${value.toLocaleString("en-GB", { maximumFractionDigits: 2 })}`;
}

/** A salt mass in kg, to the tonne once it is big enough to read as one. */
export function formatSaltMass(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return UNAVAILABLE;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toLocaleString("en-GB", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }
  return value.toLocaleString("en-GB", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  });
}

/** The unit that goes with :func:`formatSaltMass` at that magnitude. */
export function saltMassUnit(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "";
  }
  return value >= 1_000 ? "t" : "kg";
}

/**
 * The salt a volume of brine holds, in kilograms.
 *
 * `salinity_g_l x volume_m3`, which looks too simple to be right until the
 * units are carried through: a cubic metre is a thousand litres and a kilogram
 * is a thousand grams, so the two factors of a thousand cancel. 285 g/L across
 * 1937.5 m³ is 552,187.5 kg.
 *
 * Only used to render the salt column. The balance's own salt figures come from
 * the server.
 */
export function saltMassKg(
  volumeM3: number | null | undefined,
  salinityG_l: number | null | undefined,
): number | null {
  if (
    volumeM3 === null ||
    volumeM3 === undefined ||
    salinityG_l === null ||
    salinityG_l === undefined ||
    !Number.isFinite(volumeM3) ||
    !Number.isFinite(salinityG_l)
  ) {
    return null;
  }
  return salinityG_l * volumeM3;
}

// -- chart shaping --------------------------------------------------------

/** One point on the rainfall-against-salinity curve. */
export type DilutionPoint = {
  rainfall_mm: number;
  rainfall: number;
  salinity: number;
  label: string;
  dilution: string;
  volume: string;
  /** True for the scenario the operator has selected. */
  selected: boolean;
  is_standard_preset: boolean;
};

/**
 * Shapes a sweep for the chart.
 *
 * The server already returns the scenarios in ascending rainfall order, which is
 * the order the salinity falls in, so the curve can be drawn left to right
 * without a second sort. Nothing is interpolated or smoothed: every point on the
 * line is a rainfall depth that was actually evaluated, so a reader can point at
 * any position on the curve and name the scenario behind it.
 *
 * A custom depth is drawn on the same line as the presets, distinguished by its
 * dot, because it is the same calculation and would be misleading if it looked
 * like a different kind of result.
 */
export function buildDilutionSeries(
  scenarios: readonly RainfallScenario[],
  selectedRainfallMm: number | null,
): DilutionPoint[] {
  return scenarios.map((scenario) => ({
    rainfall_mm: scenario.rainfall_mm,
    rainfall: scenario.rainfall_mm,
    salinity: scenario.post_rain_salinity_g_l,
    label: scenario.label,
    dilution: formatDilution(scenario.dilution_percent),
    volume: formatVolume(scenario.post_rain_volume_m3),
    selected:
      selectedRainfallMm !== null &&
      Math.abs(selectedRainfallMm - scenario.rainfall_mm) < 1e-9,
    is_standard_preset: scenario.is_standard_preset,
  }));
}

/** Finds one scenario in a sweep, for the selected column of the panel. */
export function findScenario(
  scenarios: readonly RainfallScenario[],
  rainfallMm: number | null,
): RainfallScenario | null {
  if (rainfallMm === null) return null;
  return (
    scenarios.find(
      (scenario) => Math.abs(scenario.rainfall_mm - rainfallMm) < 1e-9,
    ) ?? null
  );
}

/**
 * Whether a sweep can be drawn at all.
 *
 * A single scenario is one point, which is a value rather than a curve, so the
 * chart says so instead of drawing a line that implies a relationship it has not
 * established.
 */
export function needsTwoScenarios(series: readonly DilutionPoint[]): boolean {
  return series.length < 2;
}

// -- the salinity axis -----------------------------------------------------

/** A vertical salinity axis: its range bottom to top, and the ticks on it. */
export type SalinityAxis = {
  /** `[bottom, top]`, always ascending, so salinity rises as the eye moves up. */
  domain: [number, number];
  /** Round tick values in g/L, ascending, in the same order as `domain`. */
  ticks: number[];
  /** The gap between neighbouring ticks, which is what sets the tick precision. */
  step: number;
};

/** The tick steps the axis will choose between: 1, 2, 2.5, 5 or 10 of a decade. */
const TICK_STEPS = [1, 2, 2.5, 5, 10] as const;

/**
 * The finest tick the axis will use, in g/L.
 *
 * A sweep where every scenario lands on the same salinity has no span at all, and
 * dividing that span by four asks for a step of nothing. A sub-gram-per-litre
 * tick would also be a fiction: that resolution is below what a field
 * conductivity meter reports.
 */
const MIN_TICK_STEP_G_L = 1;

/**
 * Rounds a rough interval up to the nearest readable step.
 *
 * `nice(17)` is 20, not 17. Axis ticks exist so a reader can name the height a
 * point sits at without arithmetic, and 0/65/130/195/250 asks them to do a sum.
 */
function niceStep(rough: number): number {
  if (!Number.isFinite(rough) || rough <= 0) return MIN_TICK_STEP_G_L;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / magnitude;
  const step = TICK_STEPS.find((candidate) => candidate >= normalized) ?? 10;
  return Math.max(step * magnitude, MIN_TICK_STEP_G_L);
}

/**
 * The vertical axis for a dilution curve.
 *
 * The range is the data's own range, rounded outwards to whole tick steps, and
 * not zero. A salinity axis anchored at zero puts every scenario in the top
 * tenth of the plot, where 250 g/L and 222.22 g/L are four pixels apart and the
 * answer to "what does 25 mm of rain do" cannot be read off the chart at all.
 * Dilution moves a number a long way from zero, so the axis has to start near the
 * numbers or it is not showing the thing it exists to show.
 *
 * Rounding outwards rather than inwards is what lets a plotted point sit *on* the
 * plot area with room around it. A salinated pan's own maximum is usually a round
 * number, so rounding outwards alone puts the "nothing fell" scenario exactly on
 * the top border where half the marker is clipped; half a step of margin is the
 * least that separates it from the frame.
 *
 * `domain` is always ascending, so salinity increases from bottom to top. The
 * curve itself descends left to right, because more rain means less salt; an
 * ascending axis is what makes that descent legible as a fall in salinity rather
 * than as a reversal of the axis.
 *
 * `null` when there is no finite salinity to span, so a caller can decline to
 * draw rather than plot against an invented range.
 */
export function salinityAxis(values: readonly number[]): SalinityAxis | null {
  const finite = values.filter((value) => Number.isFinite(value));
  if (finite.length === 0) return null;

  const lowest = Math.min(...finite);
  const highest = Math.max(...finite);
  const step = niceStep((highest - lowest) / 4);
  // Half a step of margin on each side, which also gives a lone distinct
  // salinity a range to sit in the middle of.
  const margin = step / 2;

  const bottom = Math.floor((lowest - margin) / step) * step;
  const top = Math.ceil((highest + margin) / step) * step;

  const count = Math.round((top - bottom) / step);
  const ticks: number[] = [];
  for (let index = 0; index <= count; index += 1) {
    // Re-deriving each tick from the step rather than accumulating it keeps
    // 2.5-step axes free of the 0.30000000000000004 drift.
    ticks.push(Number((bottom + index * step).toFixed(10)));
  }

  return { domain: [bottom, top], ticks, step };
}

/**
 * A tick label for a salinity axis.
 *
 * Precision comes from the step, not from each value: an axis on 2.5 g/L steps
 * needs one decimal, one on 25 g/L steps needs none, and a label should never
 * carry digits the axis cannot resolve.
 */
export function salinityTickLabel(value: number, step: number): string {
  if (!Number.isFinite(value)) return UNAVAILABLE;
  if (!Number.isFinite(step) || step <= 0) return String(value);
  const decimals = Number.isInteger(step) ? 0 : 1;
  return value.toFixed(decimals);
}

// -- reading the result ---------------------------------------------------

/**
 * A one-line account of what a scenario means, for the panel header.
 *
 * Leads with the outcome rather than the arithmetic, because "salinity falls to
 * 252.4 g/L, an 11.4% dilution" is the answer an operator reads and the volume
 * terms are already on screen beside it.
 */
export function summarizeScenario(
  scenario: RainfallScenario | null,
  initialSalinity: number | null,
): { headline: string; detail: string | null } {
  if (!scenario) {
    return {
      headline: "No rainfall scenario is selected.",
      detail: null,
    };
  }
  const salinity = formatSalinity(scenario.post_rain_salinity_g_l);
  if (scenario.dilution_percent === null) {
    return {
      headline: `After ${formatRainfall(scenario.rainfall_mm)} mm of rain the salinity is ${salinity} g/L.`,
      detail: scenario.dilution_percent_reason,
    };
  }
  const dilution = formatDilution(scenario.dilution_percent);
  const start =
    initialSalinity === null ? "" : ` from ${formatSalinity(initialSalinity)} g/L`;
  return {
    headline: `${formatRainfall(scenario.rainfall_mm)} mm of rain takes the salinity to ${salinity} g/L${start}.`,
    detail: `That is a ${dilution}% dilution. The pan holds the same salt in a larger volume.`,
  };
}

/**
 * Whether the sweep is internally consistent.
 *
 * Checks the two properties a volume balance must have, so the panel can say so
 * on screen instead of asking the reader to trust that the server did the
 * arithmetic correctly. A false result is a bug to report, not a scenario.
 */
export function checkSweepConsistency(
  sweep: RainfallSweep,
): { consistent: boolean; problems: string[] } {
  const problems: string[] = [];
  const series = sweep.scenarios.map((scenario) => scenario.post_rain_salinity_g_l);

  for (let index = 1; index < series.length; index += 1) {
    if (!(series[index] < series[index - 1])) {
      problems.push(
        `Salinity does not fall between ${sweep.scenarios[index - 1].rainfall_mm} mm and ${sweep.scenarios[index].rainfall_mm} mm.`,
      );
      break;
    }
  }
  for (const scenario of sweep.scenarios) {
    if (scenario.post_rain_salinity_g_l > sweep.initial_salinity_g_l) {
      problems.push(
        `Salinity after ${scenario.rainfall_mm} mm of rain exceeds the salinity the pan started with.`,
      );
      break;
    }
    if (scenario.post_rain_volume_m3 < sweep.initial_volume_m3) {
      problems.push(
        `The pan holds less water after ${scenario.rainfall_mm} mm of rain than before.`,
      );
      break;
    }
  }
  return { consistent: problems.length === 0, problems };
}
