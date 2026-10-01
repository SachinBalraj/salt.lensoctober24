/**
 * Module 16: predicted-vs-actual research validation — types and display shaping.
 *
 * Like `outcomes.ts` and `evaluation.ts`, this module is free of React and of
 * `fetch`, so the rules a reader depends on can be unit-tested directly.
 *
 * The one idea this module enforces is that **a validation figure is only ever
 * shown with the sample that produced it**. The backend withholds any metric the
 * data could not support (a single event withholds RMSE and R-squared, a set
 * whose measured values never vary withholds R-squared, a one-class set withholds
 * every rate), and every figure travels beside `n_compared` and `n_excluded` with
 * the reasons. This file's job is to keep that promise on screen:
 *
 * - a metric the backend did not compute renders as the dash, never as `0`;
 * - a classification rate (accuracy, and the macro precision/recall/F1) is a
 *   percentage, and is rendered with a `%`;
 * - the headline of every block states "2 of 4 events produced this", so a
 *   figure built from part of the set cannot be read as the whole set;
 * - nobody formats a reading without going through {@link formatComparableReading}.
 */

import {
  MISSING_DISPLAY,
  describeReading,
  formatReading,
} from "@/lib/evaluation";
import type { MetricReading } from "@/lib/evaluation";

// Re-exported so consumers of this module name one import.
export type { MetricReading } from "@/lib/evaluation";

/** The five comparison families, in the order the backend reports them. */
export const COMPARISON_KEYS = [
  "forecast_vs_actual_rainfall",
  "simulated_vs_actual_salinity",
  "forecast_simulated_vs_actual_salinity",
  "predicted_risk_vs_outcome",
  "recommended_vs_operator_action",
] as const;

export type ComparisonKey = (typeof COMPARISON_KEYS)[number];

export type ComparisonKind = "continuous" | "classification";

const COMPARISON_META: Record<
  ComparisonKey,
  { kind: ComparisonKind; unit: string | null; title: string; description: string }
> = {
  forecast_vs_actual_rainfall: {
    kind: "continuous",
    unit: "mm",
    title: "Forecast rainfall vs actual rainfall",
    description:
      "The rainfall the forecast expected against the rain that actually fell. The forecast's own error, in the units a forecast is read in.",
  },
  simulated_vs_actual_salinity: {
    kind: "continuous",
    unit: "g/L",
    title: "Simulated salinity vs actual salinity",
    description:
      "The mass-balance prediction made from the rain that actually fell against the post-event salinity that was measured. The simulator's own error.",
  },
  forecast_simulated_vs_actual_salinity: {
    kind: "continuous",
    unit: "g/L",
    title: "Forecast-simulated salinity vs actual salinity",
    description:
      "The same balance started from the recorded forecast rainfall, graded against the same measurement. Its error is the forecast's and the simulator's together.",
  },
  predicted_risk_vs_outcome: {
    kind: "classification",
    unit: null,
    title: "Predicted risk vs recorded outcome",
    description:
      "The risk class a stored model estimate cited, against the same risk rule re-applied to what actually happened. Graded under the exact definition version the prediction was made under.",
  },
  recommended_vs_operator_action: {
    kind: "classification",
    unit: null,
    title: "Recommended action vs operator action",
    description:
      "The action the recommendation in force advised against the action the operator recorded taking. Advice, disposition and outcome in one graded chain.",
  },
};

export function comparisonKind(key: ComparisonKey): ComparisonKind {
  return COMPARISON_META[key].kind;
}

export function comparisonTitle(key: ComparisonKey): string {
  return COMPARISON_META[key].title;
}

export function comparisonDescription(key: ComparisonKey): string {
  return COMPARISON_META[key].description;
}

export function comparisonUnit(key: ComparisonKey): string | null {
  return COMPARISON_META[key].unit;
}

export function isContinuousKey(key: ComparisonKey): boolean {
  return COMPARISON_META[key].kind === "continuous";
}

/** Why events could not be compared, counted by reason. */
export type ExcludedReason = {
  reason: string;
  count: number;
};

/** A metric the backend withheld, or a measurement. */
export type ComparisonMetricReading = MetricReading;

export type ContinuousComparison = {
  key: string;
  title: string;
  unit: string;
  description: string;
  n_compared: number;
  n_excluded: number;
  excluded_reasons: ExcludedReason[];
  mean_absolute_error: MetricReading;
  root_mean_squared_error: MetricReading;
  r_squared: MetricReading;
  mean_bias_error: MetricReading;
  notes: string[];
};

export type PerClassScore = {
  label: string;
  description: string;
  support: number;
  predicted: number;
  precision: MetricReading;
  recall: MetricReading;
  f1: MetricReading;
};

export type ConfusionMatrix = {
  labels: string[];
  matrix: number[][];
  classes_absent_from_evaluation: string[];
};

export type ClassificationComparison = {
  key: string;
  title: string;
  description: string;
  classes: string[];
  n_compared: number;
  n_excluded: number;
  excluded_reasons: ExcludedReason[];
  predicted_distribution: Record<string, number>;
  actual_distribution: Record<string, number>;
  accuracy: MetricReading;
  precision: MetricReading;
  recall: MetricReading;
  f1: MetricReading;
  roc_auc: MetricReading;
  pr_auc: MetricReading;
  per_class: PerClassScore[];
  confusion_matrix: ConfusionMatrix | null;
  notes: string[];
  definition_id: string | null;
  definition_name: string | null;
  definition_version: number | null;
};

export type ValidationScope = {
  pan_id: string | null;
  total_outcomes: number;
};

export type ValidationOverview = {
  scope: ValidationScope;
  forecast_vs_actual_rainfall: ContinuousComparison;
  simulated_vs_actual_salinity: ContinuousComparison;
  forecast_simulated_vs_actual_salinity: ContinuousComparison;
  predicted_risk_vs_outcome: ClassificationComparison;
  recommended_vs_operator_action: ClassificationComparison;
};

/** One event in one comparison, whether or not it could be compared. */
export type ValidationPairRow = {
  outcome_id: string;
  pan_id: string;
  event_id: string;
  timestamp: string;
  compared: boolean;
  reason: string | null;
  predicted_number: number | null;
  actual_number: number | null;
  signed_error: number | null;
  predicted_class: string | null;
  actual_class: string | null;
  probability: number | null;
  risk_definition_id: string | null;
  risk_definition_name: string | null;
  risk_definition_version: number | null;
};

export type ValidationPairsResponse = {
  comparison: string;
  kind: ComparisonKind;
  unit: string | null;
  pan_id: string | null;
  total_outcomes: number;
  items: ValidationPairRow[];
};

/** A continuous or a classification block, whichever the key resolves to. */
export type ComparisonBlock =
  | ContinuousComparison
  | ClassificationComparison;

/** The live record totals the status endpoint reports. */
export type ValidationStatus = {
  module: string;
  module_number: number;
  status: string;
  description: string;
  endpoints: string[];
  validation: {
    available: boolean;
    detail?: string;
    total_outcomes?: number;
    comparable_outcomes?: number;
    forecast_errors?: number;
    risk_predictions?: number;
    operator_decisions?: number;
  };
};

// -- display shaping ------------------------------------------------------

/** How many events produced a block and how many could not. */
export type SampleCounts = {
  compared: number;
  excluded: number;
};

export function blockSampleCounts(block: ComparisonBlock): SampleCounts {
  return { compared: block.n_compared, excluded: block.n_excluded };
}

/**
 * The sentence every block must show beside its figures.
 *
 * "Based on 2 of 4 recorded events." A figure built from part of the set is only
 * interpretable with that sentence, and a figure built from the whole set says so
 * too.
 */
export function describeSample(block: ComparisonBlock): string {
  const total = block.n_compared + block.n_excluded;
  if (total === 0) return "No recorded events in scope for this comparison yet.";
  const noun = total === 1 ? "event" : "events";
  return `Based on ${block.n_compared} of ${total} recorded ${noun}.`;
}

/**
 * Why the block could not grade everything, as one readable clause.
 *
 * Reads "2 events excluded — no post-event salinity was recorded" rather than
 * dropping the reasons or smothering them under a count.
 */
export function describeExcluded(
  block: ComparisonBlock,
): string | null {
  if (block.n_excluded === 0) return null;
  const grouped = [...block.excluded_reasons]
    .sort((left, right) => right.count - left.count)
    .map((entry) => `${entry.count} ${entry.count === 1 ? "event" : "events"} — ${entry.reason}`)
    .join("; ");
  return grouped ? `Excluded: ${grouped}.` : `${block.n_excluded} events excluded.`;
}

/**
 * A continuous figure as it reads in a tile.
 *
 * Returns the dash for a metric the data could not support — never `0` and never
 * a bare number pretending a unit the backend did not give it.
 */
export function formatComparableReading(
  reading: MetricReading | null | undefined,
  unit: string | null | undefined,
): string {
  const formatted = formatReading(reading);
  if (formatted === MISSING_DISPLAY) return formatted;
  return unit ? `${formatted} ${unit}` : formatted;
}

/**
 * A classification rate as a percentage.
 *
 * The backend reports these rates already scaled by 100, so "100" is a perfect
 * agree — the opposite of the `0..1` fractions a classifier page might use. The
 * `%` here is what stops "accuracy 100" from being read on the wrong scale.
 */
export function formatRate(reading: MetricReading | null | undefined): string {
  const formatted = formatReading(reading);
  return formatted === MISSING_DISPLAY ? formatted : `${formatted}%`;
}

/** The definition the risk block was graded under, when one is identifiable. */
export function describeRiskDefinition(
  block: ClassificationComparison,
): string | null {
  if (block.key !== "predicted_risk_vs_outcome") return null;
  if (!block.definition_name || block.definition_version === null) return null;
  return `${block.definition_name} · version ${block.definition_version}`;
}

/**
 * A reading in words, suited to a tooltip or a screen reader.
 *
 * Delegates to `describeReading` so the prose a reader hears for a dashboard
 * figure is the same prose the evaluation screens produce.
 */
export function describeComparableReading(
  reading: MetricReading | null | undefined,
  name?: string,
): string {
  return describeReading(reading, name);
}