/**
 * T2 — the dataset audit behind the training gate.
 *
 * The ML service admits, in its own readiness report, whether genuine labelled
 * rows exist. It almost always reports zero, and the honest thing to render
 * then is the audit itself, not a model: the numbers that say why the gate is
 * shut are the numbers an operator should see, together with the thresholds the
 * gate is measured against.
 *
 * This module is pure typing and display shaping for that report. The verdict
 * and the counts come from the backend; nothing here estimates a missing row or
 * predicts when the gate will open.
 */

export type DatasetReadinessCounts = {
  registered_pans: number;
  stored_observations: number;
  stored_weather: number;
  outcomes_on_file: number;
  datasets_on_file: number;
  model_versions_on_file: number;
  experiments_on_file: number;
};

export type DatasetReadinessThresholds = {
  min_total_labelled_rows: number;
  min_rows_per_split: number;
  min_classes: number;
  min_rows_per_class: number;
  min_train_rows_per_class: number;
  min_distinct_pans: number;
};

export type DatasetReadiness = {
  verdict: "ready" | "not_ready";
  message: string;
  reasons: string[];
  genuine_labelled_rows: number;
  distinct_pans_in_labels: number;
  counts: DatasetReadinessCounts;
  thresholds: DatasetReadinessThresholds;
  note: string;
};

export function isReady(report: DatasetReadiness): boolean {
  return report.verdict === "ready";
}

/** The two figures a reader can weigh the verdict against. */
export function headlineFigures(report: DatasetReadiness): Array<{
  label: string;
  value: number;
  required: number;
}> {
  return [
    {
      label: "Genuine labelled rows",
      value: report.genuine_labelled_rows,
      required: report.thresholds.min_total_labelled_rows,
    },
    {
      label: "Distinct pans in labels",
      value: report.distinct_pans_in_labels,
      required: report.thresholds.min_distinct_pans,
    },
  ];
}

/** The stored-count rows of the audit table, in the order they are rendered. */
export function countRows(): Array<{
  key: keyof DatasetReadinessCounts;
  label: string;
}> {
  return [
    { key: "registered_pans", label: "Registered salt pans" },
    { key: "stored_observations", label: "Stored observations" },
    { key: "stored_weather", label: "Stored weather records" },
    { key: "outcomes_on_file", label: "Outcomes on file (incl. demos)" },
    { key: "datasets_on_file", label: "Research datasets on file" },
    { key: "model_versions_on_file", label: "Registered model versions" },
    { key: "experiments_on_file", label: "Completed experiments" },
  ];
}

export function thresholdRows(): Array<{
  key: keyof DatasetReadinessThresholds;
  label: string;
}> {
  return [
    { key: "min_total_labelled_rows", label: "Min. genuine labelled rows" },
    { key: "min_rows_per_split", label: "Min. rows per split" },
    { key: "min_classes", label: "Min. classes" },
    { key: "min_rows_per_class", label: "Min. rows per class" },
    { key: "min_train_rows_per_class", label: "Min. training rows per class" },
    { key: "min_distinct_pans", label: "Min. distinct pans" },
  ];
}