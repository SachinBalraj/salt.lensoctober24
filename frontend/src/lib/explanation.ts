/**
 * Module 11 - SHAP explainability: domain types, labels and formatting.
 *
 * Free of React and of `fetch`, so the rules the panels depend on can be unit
 * tested directly.
 *
 * Four things are treated as load-bearing rather than cosmetic.
 *
 * **A contribution is not a probability.** A SHAP value answers "how far did this
 * one feature move the output away from what the model would have said about a
 * typical row", and is measured in probability *points*. The probability itself is
 * a level, not a change. Rendering a contribution in the same column as the
 * probability, with the same `%` sign, would assert that a +0.25 contribution
 * makes a pan 25% more likely, which is a different and wrong claim. The
 * formatter here is the only place either number becomes a string, and the two
 * never share it.
 *
 * **An approximation is labelled as one.** The sampled explainer estimates
 * Shapley values by permutation; the tree explainer computes them. Both are
 * genuine SHAP and both are reported, but `approximate` travels with the numbers
 * and is rendered, because a reader choosing between two explanations deserves to
 * know which one was estimated.
 *
 * **"No explanation" is not "an explanation of zero".** A model with no
 * probabilities, an artifact not on this host, a snapshot that no longer matches
 * its hash: each produces a named reason and no attributions. {@link
 * explanationUnavailableMessage} keeps those apart from a connectivity failure,
 * which is not a refusal at all and has a different fix.
 *
 * **A simulation result is not a contribution.** The mass-balance figure the row
 * carried is one of the *inputs* the model read. It appears in
 * {@link FeatureAttribution} like any other feature -- the model did read it, and
 * its contribution is exactly as real as any other's -- and it also appears in
 * `quantities.physical_simulation` under its own unit. Showing the two together is
 * the point: a reader can see that a physics figure and a contribution about that
 * same feature are two numbers in two different units.
 */

/** Mirrors the backend's Explanation.kind. */
export type ExplanationKind = "local" | "global";

/**
 * Where a feature's value in the input row came from.
 *
 * `physical_simulation` is its own case rather than a variant of the twin: the
 * number was produced by Module 06's mass balance, not measured or estimated by
 * the twin, and an operator reasoning about it needs to know that.
 */
export type FeatureSourceKind =
  | "digital_twin_state"
  | "physical_simulation"
  | "imputed"
  | "unknown";

/** Mirrors the backend's FeatureAttribution.direction. */
export type ContributionDirection = "increases_risk" | "decreases_risk" | "neutral";

/** Mirrors the backend's FeatureAttribution.source_kind. */
export type FeatureAttribution = {
  feature: string;
  /**
   * The value the model was given, or null when the preprocessor imputed it.
   *
   * Null is the honest answer for an imputed column: the number in the matrix was
   * the training median, a statistic of the training rows and not a measurement of
   * this pan, so there is no value to report.
   */
  feature_value: number | null;
  /** Signed, in probability points. Never a probability. */
  shap_value: number;
  direction: ContributionDirection;
  /** The twin field or Module 06 that supplied the value, when it did. */
  source: string | null;
  source_kind: FeatureSourceKind;
  imputed: boolean;
};

/** A labelled number with its unit, so two quantities are never shown as one. */
export type QuantityReading = {
  label: string;
  value?: number | null;
  unit: string;
  note: string;
};

export type ReferenceDistribution = {
  /**
   * Which rows the contributions are measured against.
   *
   * "training_split..." for a single prediction -- its expected value is the mean
   * over the training rows. "held_out_split..." for a global summary, whose rows
   * the model had not seen.
   */
  source: string;
  rows: number;
  width: number;
  train_rows: number | null;
  holdout_rows: number | null;
  selection: string | null;
};

export type ShapContributionReading = {
  label: string;
  unit: string;
  base_value: number;
  explained_output: string;
  target_class: string;
  note: string;
};

export type PhysicalSimulationReading = {
  label: string;
  unit: string;
  results: Record<string, number>;
  note: string;
};

/**
 * The three quantities, kept apart.
 *
 * The field names are the separation: nothing downstream has to infer from a unit
 * string which of the three it is holding.
 */
export type ExplanationQuantities = {
  model_probability: QuantityReading;
  shap_contribution: ShapContributionReading;
  physical_simulation: PhysicalSimulationReading;
};

export type LocalExplanation = {
  explanation_id: string;
  prediction_id: string;
  pan_id: string;
  model_id: string;
  model_version: string;
  model_name: string | null;
  algorithm: string | null;
  kind: "local";
  created_at: string;
  target_class: string;
  input_snapshot_id: string;
  twin_id: string;
  twin_as_of: string;
  quantities: ExplanationQuantities;
  model_probability: number;
  /** The model's expected value over the reference rows. */
  base_value: number;
  /** How far the contributions miss the probability. Small means they account for it. */
  attribution_error: number;
  method: "tree_explainer" | "permutation_explainer";
  approximate: boolean;
  shap_version: string;
  background: ReferenceDistribution;
  /** Every declared feature, in the model's own order. */
  attributions: FeatureAttribution[];
  top_increasing: FeatureAttribution[];
  top_decreasing: FeatureAttribution[];
};

export type GlobalFeatureImportance = {
  feature: string;
  /** Mean |contribution| across the rows explained, in probability points. */
  mean_abs_shap: number;
  rank: number;
};

export type GlobalImportance = {
  explanation_id: string;
  model_id: string;
  model_version: string;
  model_name: string | null;
  algorithm: string | null;
  kind: "global";
  created_at: string;
  explained_output: string;
  /** The rows actually explained, which may be fewer than were asked for. */
  rows_explained: number;
  /** How many of those rows each class was predicted as. */
  explained_classes: Record<string, number>;
  method: "tree_explainer" | "permutation_explainer";
  approximate: boolean;
  shap_version: string;
  background: ReferenceDistribution;
  measure: QuantityReading;
  features: GlobalFeatureImportance[];
};

/** One row of a model version's explanation history. */
export type ExplanationSummary = {
  explanation_id: string;
  kind: ExplanationKind;
  prediction_id: string | null;
  pan_id: string | null;
  target_class: string | null;
  explained_output: string | null;
  model_output: number | null;
  base_value: number | null;
  attribution_error: number | null;
  method: string;
  approximate: boolean;
  shap_version: string;
  rows_explained: number | null;
  created_at: string;
  is_simulated: boolean;
};

export type ModelExplanationList = {
  model_id: string;
  model_version: string;
  count: number;
  explanations: ExplanationSummary[];
};

/** A contribution as a sentence about direction, for the factor list. */
export function describeDirection(direction: ContributionDirection): string {
  if (direction === "increases_risk") return "pushed the risk up";
  if (direction === "decreases_risk") return "pushed the risk down";
  return "did not move the risk";
}

/**
 * A contribution in probability points, with its sign kept.
 *
 * The `+` on a positive value is not decoration: it is the difference between
 * "moved the risk up" and "is 0.25", and the two lists this feeds are ordered by
 * that sign. The unit is stated rather than assumed, and a contribution of exactly
 * zero reads as zero rather than as a sign.
 */
export function formatContribution(value: number): string {
  if (value === 0) return "0.00";
  const points = Math.abs(value) * 100;
  const digits = points >= 10 ? 1 : 2;
  const magnitude = points.toFixed(digits);
  return `${value > 0 ? "+" : "-"}${magnitude} pp`;
}

/**
 * A contribution together with the feature's own value where there is one.
 *
 * "imputed" in place of the number, because the preprocessor's median is not a
 * reading of this pan and showing it next to the others would make it look like
 * one.
 */
export function formatFeatureValue(attribution: FeatureAttribution): string {
  if (attribution.feature_value === null) return "imputed";
  return String(attribution.feature_value);
}

/**
 * Whether the contributions account for the probability, in words.
 *
 * The residual is a real number that a reader could be shown; this says whether it
 * is small enough to trust, because a number with no scale attached is not
 * checkable.
 */
export function describeAdditivity(explanation: LocalExplanation): string {
  const points = explanation.attribution_error * 100;
  if (explanation.attribution_error === 0) {
    return "The contributions add up to the probability exactly.";
  }
  if (points < 0.005) {
    return "The contributions add up to the probability to within rounding.";
  }
  return `The contributions miss the probability by ${points.toFixed(3)} pp.`;
}

/**
 * How the values were produced, in one line, including whether they were estimated.
 */
export function describeMethod(explanation: {
  method: string;
  approximate: boolean;
  shap_version: string;
}): string {
  const how =
    explanation.method === "tree_explainer"
      ? "computed exactly by shap's tree explainer"
      : "estimated by shap's permutation explainer";
  return `SHAP values ${how} (shap ${explanation.shap_version}).`;
}

/**
 * The features worth showing, when there are more than the two factor lists cover.
 *
 * Both lists are capped at five, so a fourteen-feature model leaves contributions
 * unlisted. They are not dropped: the panel shows the full table, because "this
 * feature contributed nothing" and "this feature was not shown" are different
 * claims and only the table can make the first one.
 */
export function unattributedFeatures(
  explanation: LocalExplanation,
): FeatureAttribution[] {
  const listed = new Set([
    ...explanation.top_increasing.map((item) => item.feature),
    ...explanation.top_decreasing.map((item) => item.feature),
  ]);
  return explanation.attributions.filter((item) => !listed.has(item.feature));
}

/** The most influential features, by mean absolute contribution. */
export function leadingFeatures(
  importance: GlobalImportance,
  count = 5,
): GlobalFeatureImportance[] {
  return importance.features.slice(0, count);
}

/**
 * What the summary is a summary *of*, in one sentence.
 *
 * "Most important" is otherwise ambiguous between this model's probabilities on
 * rows it had not seen and its coefficients, and the two can disagree.
 */
export function describeGlobalScope(importance: GlobalImportance): string {
  const { rows_explained: rows, background } = importance;
  const held = background.source.startsWith("held_out");
  return `${rows} ${held ? "held-out" : "reference"} rows${
    importance.approximate ? ", contributions estimated" : ""
  }.`;
}
