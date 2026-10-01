/**
 * Module 12: operational recommendation types, labels and formatting.
 *
 * Free of React and of `fetch`, so the rules the panel depends on can be unit
 * tested directly.
 *
 * The panel's job is to show an operator *why* a pan was given an action, and four
 * things are treated as load-bearing rather than cosmetic.
 *
 * **The action is a closed vocabulary of four.** The recommendation is a call to
 * act — open a gate, pump brine, take a harvest — and a client that could receive a
 * fifth string would be a client with no way to render an action it has never seen.
 * The union below is the whole list, so an unknown value from a newer server is a
 * type error rather than a blank space on screen.
 *
 * **Four kinds of evidence stay four kinds.** The twin state, the mass balance, the
 * model estimate and the rule's own matching are separate fields, and the panel
 * renders each under its own heading with its own provenance. They are never
 * merged into one "inputs" table, because a simulated salinity sitting in the same
 * list as a measured one is exactly how a computed figure comes to be read as an
 * observation.
 *
 * **A missing model is a label, not an error.** When no estimate was read the
 * response carries the project's exact notice, and the panel renders that string
 * rather than composing one. Inventing a second wording for the same state means an
 * operator reading two surfaces cannot tell they describe the same thing.
 *
 * **There is no confidence to render.** The recommendation carries no
 * recommendation-level confidence, and the panel offers no percentage, bar or
 * wording that would stand in for one. A rule either decided or it did not; a
 * number there would be compared against the model's own and imply a likelihood
 * nobody computed. {@link adviceHasModelEstimate} is the only question asked about
 * how strong the advice is, and it is about provenance rather than degree.
 */

/**
 * What the operator is told when no model estimate was read.
 *
 * Exactly one string, fixed by the project and asserted verbatim on both sides.
 * The panel shows this rather than a message it builds, because a missing model is
 * a state of the system rather than an error in the request: nothing was asked
 * wrongly and there is nothing to retry.
 */
export const ML_UNAVAILABLE_LABEL =
  "Physics/rule-based recommendation — ML prediction unavailable.";

/** Every action this module can recommend. Nothing else is a recommendation. */
export const DECISION_ACTIONS = [
  "HARVEST NOW",
  "TRANSFER BRINE",
  "PROTECT / DRAIN",
  "WAIT AND RECHECK",
] as const;

export type DecisionAction = (typeof DECISION_ACTIONS)[number];

/**
 * How the advice was reached.
 *
 * `physics_and_rules` means no model estimate was read. `physics_and_ml` means one
 * was, and `ml_result` is present — the two are checked against each other by the
 * server, so a client can trust the pair rather than re-deriving it.
 */
export type DecisionBasis = "physics_and_ml" | "physics_and_rules";

/** Where a single decision input's value came from. */
export type DecisionVariableSource =
  | "twin"
  | "simulation"
  | "prediction"
  | "pan_registration";

/** One variable the rule read, and where the value found actually came from. */
export type DecisionInputReading = {
  variable: string;
  /** Null when the variable could not be read; read `reason` then. */
  value: number | string | boolean | null;
  unit: string | null;
  source: DecisionVariableSource;
  available: boolean;
  /** Why there is no value. Null when there is one. */
  reason: string | null;
};

/** The mass balance's result for this pan, at the rainfall it was run with. */
export type DecisionSimulationResult = {
  run_id: string | null;
  model_name: string;
  /** Always false. Present so a consumer can assert on it rather than infer it. */
  is_ml: boolean;
  rainfall_mm: number;
  rainfall_source: string;
  initial_salinity_g_l: number;
  initial_depth_cm: number;
  post_rain_salinity_g_l: number;
  post_rain_depth_cm: number;
  salinity_drop_g_l: number;
  /** Null when the starting salinity left the ratio undefined. */
  dilution_percent: number | null;
  dilution_percent_reason: string | null;
  assumptions: string[];
  disclaimer: string;
};

/**
 * A stored model estimate, exactly as the model reported it.
 *
 * `probability` and `confidence` are null for a class-only model, and are never
 * replaced by the rule's agreement with the action. A number in either field is
 * read as a likelihood the model produced, so the panel shows the absence instead.
 */
export type DecisionMlResult = {
  prediction_id: string;
  model_id: string | null;
  model_version: string;
  target: string;
  predicted_class: string | null;
  /** The model's own probability, or null when it reported none. */
  probability: number | null;
  confidence: number | null;
  probability_is_null_reason: string | null;
  /** When the model was run. */
  predicted_at: string;
  /** The instant of the twin state the model was given, as its snapshot recorded. */
  twin_as_of: string | null;
  input_snapshot_id: string | null;
  /**
   * The Module 11 SHAP attribution of this exact prediction, when one was stored.
   *
   * Null is a normal state: the model may simply not have been explained, and that
   * is no reason to withhold the estimate. The id is carried rather than the
   * attribution itself so the panel cannot present a feature ranking it did not
   * read from the server.
   */
  explanation_id: string | null;
  /**
   * Whether the estimate was made from the same twin state this recommendation
   * used. False means the advice is built on a newer state than the estimate.
   */
  matches_recommendation_snapshot: boolean;
};

/** The recorded state the recommendation was made from. */
export type DecisionTwin = {
  twin_id: string;
  as_of: string;
  provenance: string;
  salinity_g_l: number | null;
  water_depth_cm: number | null;
  forecast_rainfall_mm: number | null;
  observation_id: string | null;
  observation_timestamp: string | null;
  weather_forecast_id: string | null;
  weather_forecast_timestamp: string | null;
  missing_fields: string[];
  /** True when any record behind this recommendation was entered to demonstrate
   * the workflow. The panel labels the recommendation from this flag rather than
   * from the individual readings, so the label cannot be forgotten by a client
   * that displayed only some of them. */
  is_demonstration: boolean;
  /** Which inputs were demonstrations, by the twin field carrying the marker.
   * Lets the label name the specific records rather than the whole pan. */
  demonstration_sources: string[];
};

/**
 * One criterion that fired, as structured fields.
 *
 * `matched_criteria` on the advice is a sentence for a reader; this is what lets a
 * client render an actual threshold. The distinction that matters on an audit is
 * between a bound the rule asserted and one the operator configured, and only
 * `bound_variable` can tell them apart -- the resolved `threshold` is a number
 * either way.
 */
export type DecisionCriterionFired = {
  variable: string;
  operator: string;
  comparator: string;
  action: DecisionAction;
  rationale: string | null;
  value: number | string | null;
  threshold: number | string | null;
  bound_variable: string | null;
  lower_bound: number | null;
  upper_bound: number | null;
  source: string | null;
};

/**
 * True when a threshold came from the operator's configuration rather than from
 * the rule. Such a figure is a property of the pan, not of the rule, and the
 * panel says so where the number is shown.
 */
export function boundIsOperatorConfigured(
  criterion: DecisionCriterionFired,
): boolean {
  return criterion.bound_variable !== null;
}

/**
 * One operational recommendation, with the evidence behind it.
 *
 * `ml_result` is null and `notice` carries the required label when no model was
 * read; that is a complete answer, not a partial one.
 */
export type DecisionAdvice = {
  /** The action. */
  recommendation: DecisionAction;
  /** The rule's own argument, naming the criterion that decided. */
  reason: string;
  inputs_used: DecisionInputReading[];
  simulation_result: DecisionSimulationResult;
  ml_result: DecisionMlResult | null;
  /** 'name vN' plus the content hash, as a human reads it. */
  rule_version: string;
  timestamp: string;
  basis: DecisionBasis;
  /** Exactly what this recommendation was built from. */
  notice: string;
  recommendation_id: string;
  pan_id: string;
  priority: string;
  rule_id: string;
  rule_name: string;
  rule_version_number: number;
  specification_hash: string;
  /** Every criterion that fired, for the audit line. */
  matched_criteria: string;
  /** The same criteria as fields, each with the bound resolved for this pan. */
  criteria: DecisionCriterionFired[];
  twin: DecisionTwin;
  title: string;
  body: string;
  expires_at: string | null;
};

/**
 * The answer to "has this pan been advised on?", which is not the same question as
 * "what was the advice?".
 *
 * The server states the two separately so that a pan nobody has asked about is a
 * normal fact with a documented shape, rather than an empty body this client has to
 * interpret. Collapsing the pair to a bare `DecisionAdvice | null` would lose that
 * distinction: `found: false` is a statement, and dropping it would make an
 * unadvised pan indistinguishable from a failed request.
 */
export type DecisionLatest = {
  found: boolean;
  advice: DecisionAdvice | null;
};

/**
 * The exact label for a recommendation built from demonstration data.
 *
 * Held as a constant because it is a claim about what the reader is looking at,
 * and a second surface rendering the same state in different words is how a
 * demonstration gets acted on. The server also says so in `notice`; this is the
 * client-side belt to that, for a client that renders the panel from the flag
 * alone.
 */
export const DEMONSTRATION_ONLY_LABEL = "DEMONSTRATION ONLY";

/** True when a model estimate was read, and is attached. */
export function adviceHasModelEstimate(advice: DecisionAdvice): boolean {
  return advice.ml_result !== null;
}

/**
 * True when the model reported a probability.
 *
 * The distinction this preserves is between "no likelihood" and "likelihood of
 * zero". A class-only model has still estimated a class; collapsing the two would
 * assert a number the model never produced.
 */
export function reportsNoProbability(ml: DecisionMlResult): boolean {
  return ml.probability === null;
}

/** True when the estimate is from an older twin state than the advice is built on. */
export function estimateIsStale(advice: DecisionAdvice): boolean {
  const ml = advice.ml_result;
  return ml !== null && !ml.matches_recommendation_snapshot;
}

/**
 * The probability as a percentage, or the reason there is none.
 *
 * Returns a string rather than a number so the absence is rendered from here
 * rather than at the call site, where it would eventually be rendered as a zero.
 */
export function formatProbability(value: number | null): string {
  if (value === null) return "not reported by the model";
  const percent = value * 100;
  return `${percent >= 10 ? percent.toFixed(1) : percent.toFixed(2)}%`;
}

/**
 * The dilution as a percentage, or the reason it is not available.
 *
 * Same reasoning as {@link formatProbability}: a null ratio is a gap in the
 * arithmetic, and rendering it as 0% would assert that no dilution happened.
 */
export function formatDilution(simulation: DecisionSimulationResult): string {
  if (simulation.dilution_percent === null) {
    return simulation.dilution_percent_reason ?? "not available";
  }
  return `${simulation.dilution_percent.toFixed(1)}%`;
}

/** Where a decision input's value came from, in words an operator can act on. */
export function describeSource(source: DecisionVariableSource): string {
  switch (source) {
    case "twin":
      return "Recorded twin state";
    case "simulation":
      return "Mass balance";
    case "prediction":
      return "Stored model estimate";
    case "pan_registration":
      return "Pan registration";
  }
}

/**
 * One decision input, rendered for the table.
 *
 * An unreadable variable keeps its row and says why it is empty. Dropping the row
 * would hide the very fact the operator needs: a criterion that could not be
 * checked is a gap in the evidence, and a table of only the values that happened to
 * be present reads as a complete account.
 */
export function inputRows(advice: DecisionAdvice): Array<{
  variable: string;
  display: string;
  source: DecisionVariableSource;
  sourceLabel: string;
  available: boolean;
  reason: string | null;
}> {
  return advice.inputs_used.map((reading) => ({
    variable: reading.variable,
    display: formatReading(reading),
    source: reading.source,
    sourceLabel: describeSource(reading.source),
    available: reading.available,
    reason: reading.reason,
  }));
}

function formatReading(reading: DecisionInputReading): string {
  if (!reading.available || reading.value === null) {
    return reading.reason ?? "not available";
  }
  const { value, unit } = reading;
  const shown =
    typeof value === "number"
      ? `${value}`
      : typeof value === "boolean"
        ? (value ? "yes" : "no")
        : value;
  return unit ? `${shown} ${unit}` : shown;
}

/**
 * A one-line description of how the advice was reached.
 *
 * Built from the basis and the notice rather than from the action, so the summary
 * an operator reads first cannot overstate what was consulted.
 */
export function describeBasis(advice: DecisionAdvice): string {
  if (!adviceHasModelEstimate(advice)) {
    return "Physics and the written rule only — no model estimate was read.";
  }
  return `Physics, the written rule, and the stored model estimate (${advice.ml_result?.model_version}).`;
}
