/**
 * Operational risk prediction: domain types, labels and formatting.
 *
 * Free of React and of `fetch`, so the rules the panel depends on can be unit
 * tested directly.
 *
 * Three things are treated as load-bearing rather than cosmetic.
 *
 * **The unavailable message is one fixed sentence.** When no model is deployed
 * there is no risk figure, and the panel says exactly
 * {@link NO_DEPLOYED_MODEL_MESSAGE} rather than composing a message from whatever
 * the server happened to send. A client that invents its own wording for a
 * missing model produces a different sentence on every surface, and an operator
 * reading two of them cannot tell they describe the same state.
 *
 * **A missing probability is not a low one.** A model that reports no
 * per-class probabilities still has a class to give, and the panel shows that
 * class with the absence stated. Rendering `null` as `0%` would be the single
 * most damaging thing this panel could do: it would assert a number the model
 * never produced, and an operator acting on it would be acting on a fabrication.
 *
 * **The model and the input are shown next to the answer.** A risk class with no
 * model version and no input snapshot id attached is a number with nothing behind
 * it. Both are on screen for the same reason the class is: so that a reader can
 * say which model said it, and from what.
 */

/**
 * What the operator sees when no trained model is in service.
 *
 * Exactly one sentence, fixed here and in the panel, because a missing model is
 * a state of the system rather than an error in the request: nothing was asked
 * wrongly and there is nothing to retry.
 *
 * The wording names the two facts an operator needs — that no model is trained,
 * and that therefore there is nothing to predict with — and nothing else. It
 * deliberately does not apologise, suggest a workaround, or imply the deployment
 * is broken, because a missing model is an ordinary state of a project that has
 * not trained one yet and phrasing it as a fault invites a support ticket about
 * a system that is behaving exactly as specified.
 */
export const NO_DEPLOYED_MODEL_MESSAGE =
  "ML model not trained — prediction unavailable";

/** The backend's refusal code for that state. */
export const NO_DEPLOYED_MODEL_REFUSAL = "no_deployed_model";

/**
 * What the operator is told, once per state, when the model did not answer.
 *
 * Each sentence names one state and one remedy. They are fixed here rather than
 * composed at the point of display because a state that is worded differently
 * on two screens is a state an operator cannot compare: "no model exists" and
 * "nobody has deployed it" are different facts about the project, and a client
 * that flattened them into one generic absence would be hiding the difference
 * that decides whether the next step belongs to Module 08, Module 09 or Module
 * 17.
 *
 * None of them is phrased as a failure. A project that has not trained a model
 * yet is behaving normally, and telling an operator that something failed
 * invites them to retry a button whose answer cannot change.
 */
export const MODEL_EVALUATION_REQUIRED_MESSAGE = "Model evaluation required";

export const MODEL_DEPLOYMENT_REQUIRED_MESSAGE = "Model deployment required";

/** Prefixed to the field names a deployed model could not read. */
export const MISSING_INPUTS_MESSAGE_PREFIX = "Prediction unavailable — missing: ";

/**
 * Why no risk figure exists, stated before the requirement to fix it.
 *
 * Separate from {@link NO_DEPLOYED_MODEL_MESSAGE}, which says what the operator
 * is not getting. This says why, in the deployment rule itself, so the two
 * together leave nothing for an operator to infer.
 */
export const GATED_BY_DEPLOYMENT_MESSAGE =
  "ML prediction unavailable because a validated model has not yet been deployed.";

/**
 * What is required before the gate opens, when the backend could not be asked.
 *
 * Only a fallback: the real sentence is built from
 * {@link trainingRequirement} with the thresholds the sufficiency gate actually
 * enforces, so a threshold raised in the backend cannot leave this text behind.
 * The values below match the backend's `MIN_TOTAL_LABELLED_ROWS` and
 * `MIN_DISTINCT_PANS` as of this writing, and the backend test that quotes them
 * is what would notice a divergence.
 */
export const DEFAULT_TRAINING_REQUIREMENT =
  "Collect at least 80 genuine labelled outcomes across at least 3 distinct pans before training.";

/**
 * Which of the six states a refused prediction is in.
 *
 * `no_model`, `not_evaluated` and `not_deployed` come from the backend's own
 * `stage` field — this client does not infer them from prose. `missing_inputs`
 * is a deployed model whose inputs the twin could not supply, `failed` is
 * anything this client does not yet model (an unrecognised refusal, or a
 * transport failure), and the success state has no refusal at all so it is not
 * part of this union.
 */
export type RiskUnavailableState =
  | "no_model"
  | "not_evaluated"
  | "not_deployed"
  | "missing_inputs"
  | "failed";

/**
 * The sufficiency gate's own counts, as carried by a refusal or the status read.
 *
 * Mirrors the backend's projection of `MLService.dataset_readiness`. The
 * thresholds are nullable because a client that cannot read them must fall back
 * to {@link DEFAULT_TRAINING_REQUIREMENT} rather than assume a number it made up.
 */
export type TrainingGate = {
  ready: boolean;
  genuine_labelled_rows: number;
  distinct_pans_in_labels: number;
  min_total_labelled_rows: number | null;
  min_distinct_pans: number | null;
};

/**
 * The requirement, with the thresholds the gate enforces.
 *
 * Built from the gate rather than quoted from a constant so that the sentence
 * an operator reads and the rule the backend applies are the same two numbers.
 */
export function trainingRequirement(gate: TrainingGate | null): string {
  const rows = gate?.min_total_labelled_rows;
  const pans = gate?.min_distinct_pans;
  if (rows === null || rows === undefined || pans === null || pans === undefined) {
    return DEFAULT_TRAINING_REQUIREMENT;
  }
  return `Collect at least ${rows} genuine labelled outcomes across at least ${pans} distinct pans before training.`;
}

/**
 * How far from the requirement the project actually is.
 *
 * The counts, not a percentage: a gate is met or it is not, and "0 of 80" tells
 * an operator what to go and do in a way that "0%" does not. Returns `null`
 * when there is nothing to report — the gate already says it is met, or it
 * could not be read.
 */
export function trainingProgress(gate: TrainingGate | null): string | null {
  if (!gate || gate.ready) return null;
  return `${gate.genuine_labelled_rows} of ${
    gate.min_total_labelled_rows ?? "?"
  } genuine labelled outcomes on file, across ${
    gate.distinct_pans_in_labels
  } of ${gate.min_distinct_pans ?? "?"} distinct pans.`;
}

/**
 * The sentence for one state, with the server's own prose as the fallback.
 *
 * `fallback` is used only for `failed`, which is the state this client does not
 * model: flattening an unrecognised refusal into a sentence that means a
 * different state would hide a fact about the system rather than report it.
 * `missing` is the field list for `missing_inputs`, and when the backend
 * supplied none the server's explanation is used rather than a sentence with an
 * empty list after it.
 */
export function unavailableMessage(
  state: RiskUnavailableState,
  fallback: string,
  missing: string[] = [],
): string {
  switch (state) {
    case "no_model":
      return NO_DEPLOYED_MODEL_MESSAGE;
    case "not_evaluated":
      return MODEL_EVALUATION_REQUIRED_MESSAGE;
    case "not_deployed":
      return MODEL_DEPLOYMENT_REQUIRED_MESSAGE;
    case "missing_inputs":
      return missing.length > 0
        ? `${MISSING_INPUTS_MESSAGE_PREFIX}${missing.join(", ")}`
        : fallback;
    default:
      return fallback;
  }
}

/** Mirrors the backend's ResearchTarget for this endpoint. */
export type RiskTarget = "operational_risk";

/**
 * Where the features came from.
 *
 * One value on purpose. The endpoint reads the current digital twin state and
 * has no other source, and a union with other sources would invite a reader to
 * wonder which one produced a given figure.
 */
export type RiskInputSource = "current_digital_twin_state";

export type RiskPrediction = {
  prediction_id: string;
  pan_id: string;
  /** When the model was run. */
  predicted_at: string;
  /** The instant of the twin state the features were read from. */
  as_of: string;
  risk_class: string;
  /** The model's own probability, or null when it reports none. */
  probability: number | null;
  /** Every class the model could have emitted. Empty when it reports none. */
  class_probabilities: Record<string, number>;
  defined_classes: string[];
  model_id: string;
  model_name: string | null;
  model_version: string;
  algorithm: string | null;
  deployment_id: string | null;
  deployed_at: string | null;
  input_snapshot_id: string;
  twin_id: string;
  twin_as_of: string;
  risk_definition_id: string;
  risk_definition_version: number;
  dataset_id: string | null;
  dataset_version: number | null;
  selection_criterion: string | null;
  features_used: string[];
  features_absent: string[];
  features_imputed: string[];
  used_physics_prediction: boolean;
  input_source: RiskInputSource;
  /**
   * Whether Module 11 attributed this prediction, and the id of that attribution.
   *
   * On the response rather than fetched separately, because every prediction is
   * explained as it is made and the two facts belong together: a probability with
   * no attribution beside it is a number an operator is being asked to act on
   * without being told why. `unavailable` with a reason is a legitimate state, and
   * the reason is what the panel shows.
   */
  explanation_status: "available" | "unavailable";
  explanation_id: string | null;
  explanation_refusal: string | null;
  explanation_refusal_detail: string | null;
};

/** True when the model reported a probability for its class. */
export function hasProbability(
  prediction: RiskPrediction,
): prediction is RiskPrediction & { probability: number } {
  return typeof prediction.probability === "number";
}

/** True when the model declined to attach a number to its class. */
export function reportsNoProbability(prediction: RiskPrediction): boolean {
  return prediction.probability === null;
}

/**
 * The class probabilities as rows, widest first.
 *
 * Sorted so the class the model chose is not merely first by accident of
 * insertion order, and paired with the rule's own ordering where the two agree
 * so an operator can see the scale they declared. Ties keep the rule's order,
 * which is the reader's own ordering of severity.
 */
export function classProbabilityRows(
  prediction: RiskPrediction,
): Array<{ className: string; probability: number | null; isPredicted: boolean }> {
  const ruleOrder = new Map(
    prediction.defined_classes.map((name, index) => [name, index]),
  );
  return Object.entries(prediction.class_probabilities)
    .map(([className, probability]) => ({
      className,
      probability,
      isPredicted: className === prediction.risk_class,
    }))
    .sort((a, b) => {
      const left = a.probability ?? -1;
      const right = b.probability ?? -1;
      if (left !== right) return right - left;
      return (ruleOrder.get(a.className) ?? 99) - (ruleOrder.get(b.className) ?? 99);
    });
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
  // One decimal below ten, none above: a probability of 0.0 and one of 0.04 are
  // both worth showing precisely, and 84.7% is not meaningfully different from
  // 84.74%.
  return `${percent >= 10 ? percent.toFixed(1) : percent.toFixed(2)}%`;
}

/** The model as one line: the researcher's label, with the identity beside it. */
export function formatModel(prediction: RiskPrediction): string {
  const version = `${prediction.model_id} v${prediction.model_version}`;
  return prediction.model_name ? `${prediction.model_name} (${version})` : version;
}

/**
 * The feature row as a sentence about its own completeness.
 *
 * A row where every declared column arrived from the twin, and a row where half
 * of it was imputed, are different pieces of evidence about the same model. The
 * panel shows which one this was rather than a bare list.
 */
export function describeFeatureCoverage(prediction: RiskPrediction): string {
  const { features_used: used, features_absent: absent } = prediction;
  if (absent.length === 0) {
    return `All ${used.length} features came from the twin state.`;
  }
  return `${used.length} of ${used.length + absent.length} features came from the twin state; ${absent.length} were imputed by the model.`;
}
