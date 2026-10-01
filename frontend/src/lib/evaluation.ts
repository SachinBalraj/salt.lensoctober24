/**
 * Model-evaluation domain types and display shaping.
 *
 * Like `outcomes.ts`, this module is free of React and of `fetch`, so the rules a
 * reader depends on can be unit-tested directly. And like `outcomes.ts`, its
 * organising principle is a single one, which here is sharper than anywhere else in
 * the project:
 *
 * **a metric that was not calculated is never rendered as a number.**
 *
 * Module 09 exists to say how well a classifier did, and the number most likely to
 * be quoted out of context is a score. The dangerous case is not a missing metric
 * shown as a dash -- that is merely unhelpful -- but a missing metric shown as `0`,
 * or as a zero-height bar in a chart, because `0` is a *score*. A model with a
 * ROC-AUC of zero is a model that ranks every pair backwards. A model whose
 * ROC-AUC was never computed is in an entirely different situation, and the two
 * must never be drawn the same way.
 *
 * So every figure in this project is a {@link MetricReading}, and the three states
 * it can be in are kept apart all the way to the pixel:
 *
 * - **computed** -- the value, formatted, and nothing added;
 * - **computed by convention** -- the value *and* the convention named in
 *   `reason`. A `0.0` produced by `zero_division=0` is a real reported number, and
 *   hiding the convention would let it be read as a measured rate;
 * - **not computed** -- the dash, with `reason` attached. Never `0`, never a bar of
 *   zero height, never a blank that could equally be a rendering failure.
 *
 * Nothing here computes a metric. The scoring happens on the server and this module
 * only formats what it is told, so the numbers on screen cannot drift from the
 * arithmetic that produced them.
 */

/**
 * One figure, with the facts that make it interpretable.
 *
 * `computed: false` is the only state in which `value` is null, and the backend
 * enforces that pairing, so a client cannot receive a null it might read as zero.
 * `reason` is populated whenever the figure is absent, and also -- less often --
 * when a real value rests on a declared convention.
 */
export type MetricReading = {
  value: number | null;
  computed: boolean;
  variant: string | null;
  reason: string | null;
};

/** One class's own figures, with the counts they are rates of. */
export type ClassScore = {
  label: string;
  /** Rows in the evaluated set that actually carried this class. */
  support: number;
  /** Rows the model predicted as this class. */
  predicted: number;
  precision: MetricReading;
  recall: MetricReading;
  f1: MetricReading;
  average_precision: MetricReading;
};

/**
 * The confusion matrix, with every flavour of zero named.
 *
 * The three absent-class lists are the reason this is a type and not a
 * `number[][]`. A matrix of zeros means different things depending on which rows
 * and columns are zero, and "this class was never predicted" is a different
 * statement from "this class was never in the data" and from "this class is
 * missing because the code dropped it".
 */
export type ConfusionMatrixReport = {
  labels: string[];
  matrix: number[][];
  /** Neither present in the data nor predicted: the model knows of no such class. */
  classes_absent_from_evaluation: string[];
  /** Present in the label space, but no evaluated row carried this class. */
  classes_absent_from_evaluation_rows: string[];
  most_confused_pair: {
    actual: string;
    predicted: string;
    count: number;
  } | null;
};

/** The whole evaluation of one classifier on one set of rows. */
export type ClassificationEvaluation = {
  evaluated_rows: number;
  /** Prose naming the rows. Never blank -- a figure with no denominator is not published. */
  evaluation_set: string;
  classes: string[];
  class_distribution: Record<string, number>;
  predicted_distribution: Record<string, number>;
  accuracy: MetricReading;
  balanced_accuracy: MetricReading;
  precision: MetricReading;
  recall: MetricReading;
  f1: MetricReading;
  roc_auc: MetricReading;
  pr_auc: MetricReading;
  brier_score: MetricReading;
  log_loss: MetricReading;
  per_class: ClassScore[];
  confusion_matrix: ConfusionMatrixReport | null;
  notes: string[];
};

/** One resampled fold, for a reader checking a headline figure against its row. */
export type CrossValidationFold = {
  fold: number;
  train_rows: number;
  test_rows: number;
  train_pans?: string[];
  test_pans?: string[];
  metrics?: Record<string, MetricReading>;
  note?: string | null;
};

/** Whether the resampling ran, and if not, why. */
export type CrossValidationReport = {
  performed: boolean;
  design?: string | null;
  requested_splits?: number | null;
  actual_splits?: number | null;
  reason?: string | null;
  notes?: string[];
  folds?: CrossValidationFold[];
  per_algorithm?: Record<string, unknown>;
  aggregate?: Record<string, MetricReading>;
};

/**
 * The six columns the comparison asks for, in the order it asks for them.
 *
 * Mirrored from the backend's `COMPARISON_COLUMNS`. The frontend renders its
 * header from this list rather than from a hand-written row, so a column added
 * there without a title here shows up as a missing header instead of silently
 * aligning the wrong figures under the wrong heading.
 */
export const COMPARISON_COLUMNS = [
  "accuracy",
  "precision",
  "recall",
  "f1",
  "roc_auc",
  "pr_auc",
] as const;

export type ComparisonColumn = (typeof COMPARISON_COLUMNS)[number];

/**
 * The header a reader sees, which is not the key the readings are looked up by.
 *
 * A *fallback* only. The response carries its own `column_titles`, and that is what
 * gets rendered whenever it is present: the backend calls the third column "F1
 * (macro)" and this file's fallback says "F1", and a table whose header quietly
 * drops "(macro)" invites exactly the read the backend went to the trouble of
 * preventing. One source of truth for what a column is called, with a local map
 * for the case where the server sends nothing.
 */
export const COMPARISON_COLUMN_TITLES: Record<ComparisonColumn, string> = {
  accuracy: "Accuracy",
  precision: "Precision",
  recall: "Recall",
  f1: "F1",
  roc_auc: "ROC-AUC",
  pr_auc: "PR-AUC",
};

/** The heading for a column, preferring the one the response declared. */
export function columnTitle(
  comparison: Pick<ComparisonResponse, "column_titles"> | null | undefined,
  column: string,
): string {
  return (
    comparison?.column_titles?.[column] ??
    COMPARISON_COLUMN_TITLES[column as ComparisonColumn] ??
    column
  );
}

/** One row of the comparison table: a classifier and its six readings. */
export type ComparisonRow = {
  model: string;
  algorithm: string;
  model_id: string | null;
  version: string | null;
  /** Whether the documented rule chose this row. Never the word "best". */
  selected: boolean;
  auto_selectable: boolean;
  selection_note: string;
  evaluated_rows: number;
  evaluation_set: string;
  classes: string[];
  class_distribution: Record<string, number>;
  metrics: Partial<Record<ComparisonColumn, MetricReading>>;
  cross_validation: Record<string, unknown> | null;
};

/**
 * The comparison table, with the criterion and the comparability verdict.
 *
 * The last three blocks are why this is worth reading rather than just looking at.
 * A table of six numbers per row with one row highlighted is decorative; a table
 * that also states the rule that highlighted it, whether the rows share a
 * denominator, and what it declines to claim can be disagreed with.
 */
export type ComparisonResponse = {
  columns: string[];
  column_titles: Record<string, string>;
  rows: ComparisonRow[];
  row_count: number;
  selected_algorithm: string | null;
  selection_criterion: string;
  average_precision_note: string | null;
  cross_validation_performed: boolean;
  cross_validation_note: string | null;
  comparable: {
    same_row_count?: boolean;
    same_evaluation_set?: boolean;
    evaluated_rows?: number;
    caveats?: string[];
    [key: string]: unknown;
  };
  conventions_in_force: string[];
  does_not: string;
};

/** The stored report, as the list and compare views need it. */
export type EvaluationSummary = {
  evaluation_id: string;
  model_id: string;
  version: string;
  target: string;
  algorithm: string | null;
  evaluation_set: string;
  evaluated_rows: number;
  metrics: Partial<Record<ComparisonColumn, MetricReading>>;
  comparison: ComparisonResponse;
  cross_validation: Record<string, unknown>;
  dataset_id: string | null;
  dataset_version: number | null;
  dataset_content_hash: string | null;
  random_seed: number | null;
  library_versions: Record<string, string>;
  trained_at: string | null;
  evaluated_at: string;
  notes: string[];
};

/** Everything stored for one model version, provenance included. */
export type EvaluationDetail = EvaluationSummary & {
  evaluation: ClassificationEvaluation;
  candidate_evaluations: Record<string, ClassificationEvaluation>;
  cross_validation: CrossValidationReport;
  provenance: {
    dataset_id: string | null;
    dataset_version: number | null;
    dataset_content_hash: string | null;
    artifact_path: string | null;
    feature_names: string[];
    split_strategy: string;
    split_detail: Record<string, unknown>;
    random_seed: number | null;
    library_versions: Record<string, string>;
  };
  recomputed: boolean;
};

/** A registered model, whether or not it has been scored. */
export type EvaluableModel = {
  model_id: string;
  version: string;
  target: string;
  algorithm: string;
  status: string;
  dataset_id: string | null;
  trained_at: string | null;
  evaluated: boolean;
};

export type EvaluationListResponse = {
  evaluations: EvaluationSummary[];
  count: number;
  evaluable_models: EvaluableModel[];
};

export type CompareVersionsResponse = {
  evaluations: EvaluationSummary[];
  count: number;
  comparison: ComparisonResponse | null;
  not_comparable: string[];
};

/**
 * A refusal, as the backend returns it.
 *
 * The `reasons` array is the machine-readable half and the `detail` sentence is
 * for a person. Both travel, because an operator needs to know what to do next and
 * a client needs to branch without matching on prose.
 */
export type EvaluationRefusal = {
  detail: string;
  refusal: string;
  model_id?: string | null;
  reasons?: string[];
  [key: string]: unknown;
};

export type ExportFormat = "json" | "csv";

/** What the status endpoint reports, so the page can state its own contract. */
export type EvaluationModuleStatus = {
  module: string;
  number: number;
  implemented: boolean;
  description: string;
  evaluated: boolean;
  comparison_columns: string[];
  selection_rule: string;
  metric_representation: string;
  note: string;
};

// -- display shaping ------------------------------------------------------

const NUMBER_FORMAT = new Intl.NumberFormat("en-US", {
  minimumFractionDigits: 3,
  maximumFractionDigits: 3,
});

/** The dash stands for "there is no figure here", never for zero. */
export const MISSING_DISPLAY = "—";

/**
 * Whether a reading's value came from the `zero_division=0` convention.
 *
 * Detected from the reason text rather than from a flag, because the backend
 * expresses the convention in prose and the prose is what a reader sees. A false
 * positive here costs a reader a sentence they did not need; a false negative would
 * let a declared convention pass as a measurement, which is the worse error.
 */
export function isConventionReading(reading: MetricReading): boolean {
  return reading.computed && (reading.reason?.includes("zero_division") ?? false);
}

/**
 * A reading as it appears in a table cell.
 *
 * The one function every figure on this page passes through, so that no component
 * can reach a decision about it of its own. `computed: false` returns the dash
 * unconditionally -- it does not consult `value`, because a client that trusted
 * `value` here would be trusting exactly the field the contract says is null
 * precisely when the metric is absent.
 */
export function formatReading(reading: MetricReading | null | undefined): string {
  if (!reading || !reading.computed || reading.value === null) {
    return MISSING_DISPLAY;
  }
  // Three fixed decimals rather than the minimum the number happens to need. Six
  // figures sit side by side in this table, and a column reading "1", "0.87",
  // "0.5" asks the reader to do the alignment that a fixed width would have done.
  // It also makes an exact zero read as exact -- "0.000" is a finding, where "0.0"
  // looks like a figure that ran out of precision.
  return NUMBER_FORMAT.format(reading.value);
}

/**
 * The same figure as a share of 1, for readers who think in percentages.
 *
 * Returns the dash rather than `0%` when absent, for the same reason as everywhere
 * else: `0%` is a score.
 */
export function formatReadingPercent(
  reading: MetricReading | null | undefined,
): string {
  const formatted = formatReading(reading);
  return formatted === MISSING_DISPLAY ? formatted : `${formatted}%`;
}

/**
 * How a reading should read to a screen reader and to a tooltip.
 *
 * A cell that renders as a dash is ambiguous on its own -- it could be a metric
 * that does not exist, a class with no rows, or a CSS failure. This says which, in
 * words, and always gives the reason the backend attached rather than a generic
 * apology.
 */
/** Best-effort column title for a reading, used only in prose. */
const READING_LABELS: Record<string, string> = {
  ...COMPARISON_COLUMN_TITLES,
  brier_score: "Brier score",
  log_loss: "Log loss",
  balanced_accuracy: "Balanced accuracy",
  precision_weighted: "Weighted precision",
  recall_weighted: "Weighted recall",
  f1_weighted: "Weighted F1",
  matthews_corrcoef: "Matthews correlation",
  average_precision: "Average precision",
};

/**
 * How a reading should read to a screen reader and to a tooltip.
 *
 * A cell that renders as a dash is ambiguous on its own -- it could be a metric
 * that does not exist, a class with no rows, or a CSS failure. This says which, in
 * words, and always gives the reason the backend attached rather than a generic
 * apology.
 *
 * `column` is passed separately because a reading does not know what it is a
 * reading *of*: the same shape is used for a headline metric, a per-class figure
 * and a fold's score, and naming the wrong one would be worse than naming none.
 */
export function describeReading(
  reading: MetricReading | null | undefined,
  column?: string,
): string {
  if (!reading) return "No reading was recorded for this metric.";
  const name = column ? (READING_LABELS[column] ?? column) : "This metric";
  if (reading.computed) {
    const base = `${name} is ${formatReading(reading)}.`;
    if (reading.reason) return `${base} ${reading.reason}`;
    return base;
  }
  return reading.reason
    ? `Not calculated. ${reading.reason}`
    : "Not calculated, and the backend attached no reason.";
}

/**
 * The tone a cell is drawn in.
 *
 * Three states, and the absent one is deliberately the quietest: a table where
 * refusals shout is a table where a reader stops seeing the refusals.
 */
export function readingTone(
  reading: MetricReading | null | undefined,
): "measured" | "convention" | "absent" {
  if (!reading || !reading.computed || reading.value === null) return "absent";
  if (isConventionReading(reading)) return "convention";
  return "measured";
}

/**
 * Whether a metric may appear in a chart at all.
 *
 * Charts are where the absent-as-zero failure is most likely and least visible: a
 * zero-height bar is not obviously a zero, it is obviously a gap, and a reader
 * scanning six bars will take the gap for a low score. So the answer is `null`
 * rather than `0`, and the chart is built to leave a visible marker instead of a
 * bar.
 */
export function readingForChart(
  reading: MetricReading | null | undefined,
): number | null {
  if (!reading || !reading.computed || reading.value === null) return null;
  return reading.value;
}

/** A human label for a registered algorithm name. */
export const ALGORITHM_LABELS: Record<string, string> = {
  logistic_regression: "Logistic regression",
  decision_tree: "Decision tree",
  random_forest: "Random forest",
  gradient_boosting: "Gradient boosting",
};

export function formatAlgorithm(algorithm: string | null | undefined): string {
  if (!algorithm) return "Unnamed classifier";
  return ALGORITHM_LABELS[algorithm] ?? algorithm;
}

/** `model@version`, the identity an evaluation is stored under. */
export function formatModelIdentity(
  modelId: string,
  version: string | null | undefined,
): string {
  return version ? `${modelId} @ ${version}` : modelId;
}

const TIMESTAMP = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "UTC",
});

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return MISSING_DISPLAY;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return MISSING_DISPLAY;
  return `${TIMESTAMP.format(parsed)} UTC`;
}

/** A short, readable form of a content hash, for citing a run. */
export function formatContentHash(
  hash: string | null | undefined,
): string {
  if (!hash) return MISSING_DISPLAY;
  return hash.length > 12 ? `${hash.slice(0, 12)}…` : hash;
}

/**
 * The phrase that must accompany a headline figure.
 *
 * "Accuracy 0.87" on its own is a number with no denominator, and the denominator
 * is what decides whether it means anything. Four rows scored on nine rows is not
 * the same claim as four rows scored on nine thousand, so the row count and the
 * named row set travel with every figure that is shown without them.
 */
export function describeDenominator(
  evaluatedRows: number,
  evaluationSet: string,
): string {
  const rows = `${evaluatedRows} ${evaluatedRows === 1 ? "row" : "rows"}`;
  return evaluationSet ? `${rows} — ${evaluationSet}` : rows;
}

/**
 * One sentence per column saying whether it is on screen at all.
 *
 * A table that quietly omits two of its six columns is a table whose remaining
 * four have lost their context: PR-AUC is only comparable with itself when it is
 * computed the same way, and a reader cannot tell that from four tidy numbers.
 */
export function countAbsentColumns(
  rows: readonly ComparisonRow[],
  columns: readonly string[] = COMPARISON_COLUMNS,
): { computed: number; absent: number; absentColumns: string[] } {
  let computed = 0;
  const absentColumns: string[] = [];
  for (const column of columns) {
    const key = column as ComparisonColumn;
    const everyRowHasIt = rows.every((row) => row.metrics[key]?.computed === true);
    if (everyRowHasIt) computed += 1;
    else absentColumns.push(COMPARISON_COLUMN_TITLES[key] ?? column);
  }
  return { computed, absent: absentColumns.length, absentColumns };
}

/**
 * The chart's series for one column across the candidates.
 *
 * `value` is `null` for an uncomputed metric rather than `0`, and `absent` carries
 * the reason so the chart can draw a marker with a tooltip instead of a bar. The
 * two fields are separate on purpose: a chart that only had `value` would have to
 * choose between a gap that looks like a low score and a bar that *is* one.
 */
export type ChartPoint = {
  algorithm: string;
  label: string;
  value: number | null;
  /**
   * The inverse of `value`, for drawing an absent metric as a marker.
   *
   * `0` when the metric was not calculated, `null` when it was. Recharts draws
   * nothing for a null, so exactly the absent entries produce a bar to shape --
   * which keeps the marker and the bar from ever being drawn for the same entry,
   * the mistake that would put a ring on top of a real score.
   */
  marker: number | null;
  absent: boolean;
  reason: string | null;
  selected: boolean;
  autoSelectable: boolean;
};

export function buildChartSeries(
  comparison: ComparisonResponse,
  column: string,
): ChartPoint[] {
  const key = column as ComparisonColumn;
  return comparison.rows.map((row) => {
    const reading = row.metrics[key];
    const absent = !reading || !reading.computed || reading.value === null;
    return {
      algorithm: row.algorithm,
      label: formatAlgorithm(row.algorithm),
      value: absent ? null : reading.value,
      marker: absent ? 0 : null,
      absent,
      reason: reading?.reason ?? null,
      selected: row.selected,
      autoSelectable: row.auto_selectable,
    };
  });
}

/**
 * How many of a column's figures are missing across the table.
 *
 * Shown beside the column header. A reader who sees "4 of 4" knows the column is
 * complete; one who sees "2 of 4" knows to ask why before quoting either number.
 */
export function columnCoverage(
  comparison: ComparisonResponse,
  column: string,
): { present: number; total: number } {
  const key = column as ComparisonColumn;
  const present = comparison.rows.filter(
    (row) => row.metrics[key]?.computed === true,
  ).length;
  return { present, total: comparison.rows.length };
}

/**
 * The sentence that says what a table of these numbers is not.
 *
 * The backend states its own limits, and this passes them through rather than
 * writing a softer version. A table that says "these are the scores" and omits
 * what the scores are not is the thing this project keeps refusing to ship.
 */
export function describeWhatThisTableIsNot(comparison: ComparisonResponse): string {
  return comparison.does_not;
}

/**
 * The caveats that apply to reading these rows against each other.
 *
 * Always rendered, even when the list is empty, because "no caveats" is itself
 * information: it is the answer to whether the rows share a denominator.
 */
export function comparabilityCaveats(
  comparison: ComparisonResponse,
): string[] {
  const comparable = comparison.comparable ?? {};
  const caveats = [...(comparable.caveats ?? [])];
  if (comparable.same_row_count === false) {
    caveats.push(
      "These rows were scored on different numbers of rows, so their figures are not on a common denominator.",
    );
  }
  if (comparable.same_evaluation_set === false) {
    caveats.push(
      "These rows describe different sets of rows, so a difference between them may be a difference in the data rather than in the models.",
    );
  }
  return caveats;
}
