/**
 * The dashboard's domain rules: the provenance taxonomy, the readings each card is
 * allowed to show, and the few derivations the panels need.
 *
 * Free of React and of `fetch`, so the rules the dashboard depends on can be unit
 * tested directly.
 *
 * ## The one idea this module exists to enforce
 *
 * SaltLens produces six kinds of number, and an operator is expected to act on
 * them. They are not interchangeable, and the dashboard's central job is to keep
 * them visibly apart:
 *
 * | Category           | What produced it                               |
 * | ------------------ | ---------------------------------------------- |
 * | `observed`         | a reading from a physical instrument           |
 * | `forecast`         | a weather service's statement about later      |
 * | `simulated`        | the mass balance, run on a recorded state      |
 * | `ml`               | a trained classifier's stored estimate         |
 * | `recommended`      | a written rule applied to the above            |
 * | `operator_decision`| a person's recorded disposition of the advice  |
 *
 * Every one of these is a real, useful quantity, which is exactly why they can be
 * confused: they share units. A simulated salinity and an observed salinity are
 * both g/L, and a reader who sees them side by side without provenance will take
 * the one they trust to be the other. So provenance is not decoration on this
 * screen — it is the claim being made, and {@link PROVENANCE} is the closed list of
 * claims that may be made.
 *
 * **A decision is a category, not a figure.** The sixth provenance kind exists
 * because a recorded decision is the one thing on the dashboard that is not a
 * number — it is the operator's disposition of the advice, and giving it a
 * category keeps it from being read as any of the figures above. It is also the
 * only claim on this screen that is explicitly human: it does not say what is true
 * about the pan, it says what a person did with the recommendation.
 *
 * **A category is stated, never inferred.** {@link DashboardReading} requires a
 * `provenance` on every reading, so there is no way to construct a figure for the
 * dashboard without declaring what kind it is. A component cannot forget the label,
 * because the label is a required field rather than a prop that is usually passed.
 *
 * **A missing figure is a gap, not a zero.** A quantity no record supplied arrives
 * as `null` and keeps its row, carrying the reason it is empty. A pan measured at
 * 0 g/L and a pan never measured are different facts, and only the first is
 * evidence — collapsing them is how a gap becomes a reading.
 *
 * **The dashboard derives nothing that belongs to another module.** The mass
 * balance's equations live on the server and are not reimplemented here; the
 * simulation inputs are a *request*, and a refusal to build one is a refusal to
 * draw a curve rather than a curve with an assumed starting point. The model's
 * confidence is never surfaced as a confidence for the action, because the rule
 * has no confidence to report and borrowing the model's would be a different
 * claim.
 */

import {
  TWIN_INPUT_LABELS,
  describeFreshness,
  describeTwinSource,
  formatAge,
  formatQuantity,
  formatTwinTimestamp,
  oldestInputAge,
  type DigitalTwinState,
  type Freshness,
  type TwinInputKind,
} from "@/lib/digitalTwin";
import { formatContribution, type FeatureAttribution, type LocalExplanation } from "@/lib/explanation";
import type { SimulationInputs } from "@/lib/simulation";
import type { DecisionAction, DecisionAdvice } from "@/lib/decisions";
import type { SaltPan } from "@/lib/pans";

// -- the taxonomy ----------------------------------------------------------

/**
 * The six kinds of number the dashboard may show.
 *
 * Closed, and the order is the order the categories are *earned*: a record, a
 * forecast about it, a calculation over both, a model, a rule, and the decision a
 * person records in answer to the rule. The legend presents them in this order so
 * the screen teaches the hierarchy while it is being read.
 */
export const PROVENANCE_KINDS = [
  "observed",
  "forecast",
  "simulated",
  "ml",
  "recommended",
  "operator_decision",
] as const;

export type ProvenanceKind = (typeof PROVENANCE_KINDS)[number];

export type ProvenanceDefinition = {
  kind: ProvenanceKind;
  /** The word shown on the pill. The primary cue — colour is the second one. */
  label: string;
  /**
   * What produced this kind of number, in one sentence.
   *
   * Shown in the legend and as the hint on each card, because the whole value of
   * the distinction is that a reader can say *why* a figure deserves the trust it
   * is being given.
   */
  meaning: string;
  /** How a figure of this kind may be described in a sentence. */
  verb: string;
  /** Tailwind colour tokens, kept apart from the status scale deliberately. */
  color: string;
  soft: string;
  line: string;
};

export const PROVENANCE: Record<ProvenanceKind, ProvenanceDefinition> = {
  observed: {
    kind: "observed",
    label: "Observed",
    meaning:
      "Measured from a physical reading and recorded against the pan or the site. This is the only category on the screen that reports something that happened.",
    verb: "measured",
    color: "var(--color-prov-observed)",
    soft: "var(--color-prov-observed-soft)",
    line: "var(--color-prov-observed-line)",
  },
  forecast: {
    kind: "forecast",
    label: "Forecast",
    meaning:
      "A weather service's statement about rainfall that has not fallen yet. It is an expectation, not a measurement, and it has not been observed anywhere.",
    verb: "forecast",
    color: "var(--color-prov-forecast)",
    soft: "var(--color-prov-forecast-soft)",
    line: "var(--color-prov-forecast-line)",
  },
  simulated: {
    kind: "simulated",
    label: "Simulated",
    meaning:
      "Computed by the mass balance from the recorded state. A deterministic result of stated equations, not a prediction and not a measurement.",
    verb: "simulated",
    color: "var(--color-prov-simulated)",
    soft: "var(--color-prov-simulated-soft)",
    line: "var(--color-prov-simulated-line)",
  },
  ml: {
    kind: "ml",
    label: "ML-predicted",
    meaning:
      "A trained classifier's stored estimate for this pan, copied from a recorded prediction. This module did not score anything.",
    verb: "predicted by the model",
    color: "var(--color-prov-ml)",
    soft: "var(--color-prov-ml-soft)",
    line: "var(--color-prov-ml-line)",
  },
  recommended: {
    kind: "recommended",
    label: "Recommended",
    meaning:
      "The action a versioned written rule selected from the evidence above. The rule decided; neither the model nor the simulator chose the action.",
    verb: "recommended",
    color: "var(--color-prov-recommended)",
    soft: "var(--color-prov-recommended-soft)",
    line: "var(--color-prov-recommended-line)",
  },
  operator_decision: {
    kind: "operator_decision",
    label: "Decided",
    meaning:
      "The operator's recorded disposition of the advice — accepted, modified or rejected — with the action actually taken. It reports what a person did, and it is the only category on the screen that is a decision rather than a figure.",
    verb: "decided",
    color: "var(--color-prov-decision)",
    soft: "var(--color-prov-decision-soft)",
    line: "var(--color-prov-decision-line)",
  },
};

/** The taxonomy in the order the legend presents it. */
export const PROVENANCE_ORDER: readonly ProvenanceKind[] = PROVENANCE_KINDS;

export function provenanceOf(kind: ProvenanceKind): ProvenanceDefinition {
  return PROVENANCE[kind];
}

// -- readings --------------------------------------------------------------

/**
 * One figure, together with the claim it makes.
 *
 * `provenance` is required rather than defaulted, so a reading cannot be built
 * without saying what kind of number it is. That is the whole mechanism behind
 * "never mix these categories": the mixing is prevented at construction, not
 * caught by review.
 */
export type DashboardReading = {
  key: string;
  label: string;
  /** The formatted figure, or the reason there is none. Never a bare dash. */
  value: string;
  unit: string | null;
  /** True only when a real figure was recorded. A measured zero is true. */
  available: boolean;
  /** Why there is no figure. Null when there is one. */
  reason: string | null;
  provenance: ProvenanceKind;
  /** Which stored record supplied this figure, named in words. */
  fromLabel: string | null;
  fromTimestamp: string | null;
};

function reading(options: {
  key: string;
  label: string;
  value: number | null | undefined;
  unit: string;
  digits?: number;
  provenance: ProvenanceKind;
  from: TwinInputKind;
  twin: DigitalTwinState;
}): DashboardReading {
  const available =
    options.value !== null && options.value !== undefined && Number.isFinite(options.value);
  const missing = options.twin.missing_inputs.find(
    (entry) => entry.field === options.key,
  );
  const input = twinInputFor(options.twin, options.from);
  return {
    key: options.key,
    label: options.label,
    value: available
      ? formatQuantity(options.value as number, options.digits ?? 1)
      : (missing?.reason ?? "No record supplied this quantity."),
    unit: available ? options.unit : null,
    available,
    reason: available ? null : (missing?.reason ?? "No record supplied this quantity."),
    provenance: options.provenance,
    fromLabel: available ? TWIN_INPUT_LABELS[options.from] : null,
    fromTimestamp: available ? (input?.timestamp ?? null) : null,
  };
}

function twinInputFor(
  state: DigitalTwinState,
  kind: TwinInputKind,
): DigitalTwinState["observation"] {
  if (kind === "observation") return state.observation;
  if (kind === "weather") return state.weather;
  return state.weather_forecast;
}

/**
 * The four figures that describe the pan as it is now.
 *
 * All four are `observed`, because all four were measured. Two of them did not
 * come from the pan though: humidity is supplied by the site's weather
 * observation, and each reading carries the record it came from so an operator
 * can see that without leaving the card.
 */
export function conditionReadings(twin: DigitalTwinState): DashboardReading[] {
  return [
    reading({
      key: "salinity_g_l",
      label: "Brine salinity",
      value: twin.salinity_g_l,
      unit: "g/L",
      provenance: "observed",
      from: "observation",
      twin,
    }),
    reading({
      key: "water_depth_cm",
      label: "Water depth",
      value: twin.water_depth_cm,
      unit: "cm",
      provenance: "observed",
      from: "observation",
      twin,
    }),
    reading({
      key: "brine_temperature_c",
      label: "Brine temperature",
      value: twin.brine_temperature_c,
      unit: "°C",
      provenance: "observed",
      from: "observation",
      twin,
    }),
    reading({
      key: "humidity_percent",
      label: "Humidity",
      value: twin.humidity_percent,
      unit: "%",
      digits: 0,
      provenance: "observed",
      from: "weather",
      twin,
    }),
  ];
}

/**
 * What is expected of the weather, none of which has happened yet.
 *
 * `forecast` for all three, including the timestamp. An operator reading a
 * forecast rainfall and a measured rainfall in adjacent cards is the exact reader
 * this dashboard exists to keep straight, so the category is on the card rather
 * than left to the reader's memory of the last screen.
 */
export function weatherReadings(twin: DigitalTwinState): DashboardReading[] {
  const forecast = twin.weather_forecast;
  return [
    reading({
      key: "forecast_rainfall_mm",
      label: "Forecast rainfall",
      value: twin.forecast_rainfall_mm,
      unit: "mm",
      provenance: "forecast",
      from: "weather_forecast",
      twin,
    }),
    reading({
      key: "rainfall_probability_percent",
      label: "Rainfall probability",
      value: twin.rainfall_probability_percent,
      unit: "%",
      digits: 0,
      provenance: "forecast",
      from: "weather_forecast",
      twin,
    }),
    {
      key: "forecast_timestamp",
      label: "Forecast issued",
      value: forecast?.timestamp
        ? formatTwinTimestamp(forecast.timestamp)
        : "No weather forecast is in force.",
      unit: null,
      available: Boolean(forecast?.timestamp),
      reason: forecast?.timestamp
        ? null
        : "No weather forecast is in force, so there is no forecast to show.",
      provenance: "forecast" as const,
      fromLabel: forecast?.timestamp ? TWIN_INPUT_LABELS.weather_forecast : null,
      fromTimestamp: forecast?.timestamp ?? null,
    },
  ];
}

// -- the twin's own provenance --------------------------------------------

/** One stored record the twin was assembled from, and how old it is. */
export type TwinSourceView = {
  kind: TwinInputKind;
  label: string;
  /** How that record was captured, in words. */
  sourceLabel: string;
  id: string | null;
  timestamp: string | null;
  formatted: string;
  age: string;
  freshness: Freshness;
};

/**
 * The three records behind the twin, and the age of each.
 *
 * The ages are the point. A twin is assembled on every read, so its own timestamp
 * says nothing about whether the pan's condition is known; what matters is how old
 * the readings behind it are, and that is graded here rather than left to a
 * reader's judgement of a wall-clock number.
 */
export function twinSources(state: DigitalTwinState): TwinSourceView[] {
  const kinds: TwinInputKind[] = ["observation", "weather", "weather_forecast"];
  return kinds.map((kind) => {
    const input = twinInputFor(state, kind);
    return {
      kind,
      label: TWIN_INPUT_LABELS[kind],
      // describeTwinSource already resolves the source vocabulary for the kind and
      // falls back to the raw string, so there is no second lookup to keep in step.
      sourceLabel: describeTwinSource(kind, input?.source ?? null),
      id: input?.id ?? null,
      timestamp: input?.timestamp ?? null,
      formatted: formatTwinTimestamp(input?.timestamp ?? null),
      age: formatAge(input?.age_seconds ?? null),
      freshness: describeFreshness(input?.age_seconds ?? null),
    };
  });
}

/** How old the evidence behind the twin is, in words, for the card header. */
export function describeTwinCurrency(state: DigitalTwinState): {
  headline: string;
  tone: Freshness["tone"];
} {
  const age = oldestInputAge(state);
  if (age === null) {
    return { headline: "No record has contributed to this state.", tone: "danger" };
  }
  const { label } = describeFreshness(age);
  return { headline: `Oldest contributing reading is ${formatAge(age)} — ${label}.`, tone: label === "Current" ? "active" : label === "Ageing" ? "caution" : "danger" };
}

// -- the simulation request ------------------------------------------------

/**
 * The three figures the mass balance needs, or the reason it cannot be asked.
 *
 * A discriminated union rather than a nullable input, because the difference
 * between "here are the inputs" and "here is why the curve is not drawn" is the
 * difference between a figure and an invention. The dashboard does not guess an
 * area, a depth or a salinity: a curve drawn from an assumed starting point would
 * be a simulation of a pan that does not exist.
 */
export type SimulationRequest =
  | { ok: true; inputs: SimulationInputs }
  | { ok: false; missing: string[]; reason: string };

export function simulationRequest(
  pan: SaltPan,
  twin: DigitalTwinState,
): SimulationRequest {
  const missing: string[] = [];
  if (twin.salinity_g_l === null) missing.push("a measured salinity");
  if (twin.water_depth_cm === null) missing.push("a measured water depth");
  if (!pan.area_m2 || pan.area_m2 <= 0) missing.push("a registered pan area");

  if (missing.length > 0) {
    return {
      ok: false,
      missing,
      reason: `The mass balance needs ${missing.join(", ")}. Without ${missing.length === 1 ? "it" : "them"} the curve would start from an assumed pan, so it is not drawn.`,
    };
  }

  return {
    ok: true,
    inputs: {
      area_m2: pan.area_m2 as number,
      // cm -> m, the one unit conversion on this screen. It happens here so the
      // boundary is visible in one place rather than spread across a chart.
      initial_depth_m: (twin.water_depth_cm as number) / 100,
      initial_salinity_g_l: twin.salinity_g_l as number,
    },
  };
}

// -- explainability --------------------------------------------------------

/**
 * Plain-language names for the model's features.
 *
 * The model is given machine names; an operator reading a factor list needs to know
 * that `physics_predicted_post_rain_salinity_g_l` is the mass balance's answer.
 * Keyed by the feature catalogue's own names, so a model and this map cannot
 * drift apart silently, and falling back to a readable form of the name rather
 * than to the raw string.
 */
const FEATURE_LABELS: Record<string, string> = {
  current_salinity_g_l: "Brine salinity",
  water_depth_cm: "Water depth",
  brine_temperature_c: "Brine temperature",
  observation_ambient_temperature_c: "Ambient temperature (observation)",
  observation_humidity_percent: "Humidity (observation)",
  weather_ambient_temperature_c: "Ambient temperature (weather)",
  weather_humidity_percent: "Humidity (weather)",
  forecast_rainfall_mm: "Forecast rainfall",
  forecast_rainfall_probability_percent: "Rainfall probability",
  recent_rainfall_mm: "Recent rainfall",
  recent_rainfall_record_count: "Recent rainfall records",
  salinity_change_g_l: "Salinity change",
  depth_change_cm: "Depth change",
  hours_since_previous_observation: "Hours since last reading",
  pan_area_m2: "Pan area",
  physics_predicted_post_rain_salinity_g_l: "Simulated post-rain salinity",
  physics_predicted_dilution_percent: "Simulated dilution",
  physics_prediction_rainfall_source: "Rainfall source used",
  hour_of_day_utc: "Hour of day (UTC)",
  day_of_year: "Day of year",
  season: "Season",
};

export function humanizeFeature(feature: string): string {
  const known = FEATURE_LABELS[feature];
  if (known) return known;
  const words = feature.split("_");
  return words
    .map((word, index) =>
      index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word,
    )
    .join(" ");
}

export type ShapFactor = {
  feature: string;
  label: string;
  /** Signed, in probability points. Never a probability. */
  value: number;
  display: string;
  direction: FeatureAttribution["direction"];
  /** True when the model was given an imputed value rather than a real one. */
  imputed: boolean;
  /** True when the figure came from the mass balance rather than a reading. */
  simulated: boolean;
};

/**
 * The features that moved the estimate most, biggest absolute contribution first.
 *
 * Ranked by |contribution| rather than by the server's two directional lists,
 * because an operator asking "what drove this number" wants the strongest
 * influence whichever way it pushed, and splitting the answer into an
 * increases-list and a decreases-list hides the fact that a large negative can
 * matter more than a large positive.
 *
 * The unit stays probability points throughout. A contribution is a change, not a
 * level, and is formatted by {@link formatContribution} so it can never be read
 * beside the model's probability as though the two were the same quantity.
 */
export function topShapFactors(
  explanation: LocalExplanation,
  count = 6,
): ShapFactor[] {
  return [...explanation.attributions]
    .sort((a, b) => Math.abs(b.shap_value) - Math.abs(a.shap_value))
    .slice(0, count)
    .map((attribution) => ({
      feature: attribution.feature,
      label: humanizeFeature(attribution.feature),
      value: attribution.shap_value,
      display: formatContribution(attribution.shap_value),
      direction: attribution.direction,
      imputed: attribution.imputed,
      simulated: attribution.source_kind === "physical_simulation",
    }));
}

/** Whether a set of factors moves the estimate at all. */
export function shapFactorsAreFlat(factors: ShapFactor[]): boolean {
  return factors.every((factor) => factor.value === 0);
}

// -- the recommendation ----------------------------------------------------

/**
 * What each action asks an operator to do.
 *
 * A glossary of the four actions and nothing more. It states what the action means
 * operationally and carries no threshold, no severity and no ordering, because
 * the rule is what decides the action and the dashboard has no standing to imply a
 * second opinion. A reader who wants the reasoning has it in the rule's own words.
 */
export const ACTION_GLOSSARY: Record<DecisionAction, string> = {
  "HARVEST NOW": "Collect the brine. The rule asks for a harvest from this pan.",
  "TRANSFER BRINE":
    "Move brine out of this pan, to another, to protect what it holds.",
  "PROTECT / DRAIN":
    "Protect the pan's contents — hold the level, or draw water away so none spills.",
  "WAIT AND RECHECK":
    "Take no action yet, and look again when the state or the forecast has moved.",
};

export function describeAction(action: DecisionAction): string {
  return ACTION_GLOSSARY[action];
}

/**
 * The urgency the server recorded, as a colour.
 *
 * Read from the recommendation's own `priority` and never derived here. The engine
 * holds no ranking of its own — by design, because a threshold written into the
 * dashboard could not be revised without silently disagreeing with the rule — so
 * this maps a value the server sent and invents nothing.
 */
export function priorityTone(priority: string): "active" | "caution" | "danger" | "neutral" {
  switch (priority) {
    case "high":
      return "danger";
    case "medium":
      return "caution";
    case "low":
      return "active";
    default:
      return "neutral";
  }
}

export function priorityLabel(priority: string): string {
  switch (priority) {
    case "high":
      return "High priority";
    case "medium":
      return "Medium priority";
    case "low":
      return "Low priority";
    default:
      return priority;
  }
}

/**
 * Whether a recommendation rests on a model's estimate.
 *
 * The one question asked about how strong the advice is, and it is about
 * provenance rather than degree. There is deliberately no confidence figure for the
 * action anywhere in this module: a rule either decided or it did not, and a number
 * in that position would be compared against the model's own and imply a
 * likelihood nobody computed.
 */
export function adviceIsModelInformed(advice: DecisionAdvice): boolean {
  return advice.basis === "physics_and_ml";
}
