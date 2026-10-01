/**
 * Dataset-readiness shaping tests.
 *
 * The audit is the honest surface of an ML system with no labelled rows, so the
 * shaping tests are about the two numbers an operator can weigh the verdict
 * against (genuine labelled rows, distinct pans) and the fact that the verdict
 * comes from the report, never from a count computed in the browser.
 */

import { describe, expect, it } from "vitest";
import {
  countRows,
  headlineFigures,
  isReady,
  type DatasetReadiness,
} from "@/lib/datasetReadiness";

function report(overrides: Partial<DatasetReadiness> = {}): DatasetReadiness {
  return {
    verdict: "not_ready",
    message: "Insufficient labelled data for reliable model training.",
    reasons: ["only 0 genuine labelled row(s) are on file; at least 80 are required."],
    genuine_labelled_rows: 0,
    distinct_pans_in_labels: 0,
    counts: {
      registered_pans: 2,
      stored_observations: 3,
      stored_weather: 3,
      outcomes_on_file: 2,
      datasets_on_file: 0,
      model_versions_on_file: 0,
      experiments_on_file: 0,
    },
    thresholds: {
      min_total_labelled_rows: 80,
      min_rows_per_split: 96,
      min_classes: 2,
      min_rows_per_class: 20,
      min_train_rows_per_class: 40,
      min_distinct_pans: 3,
    },
    note: "Operational ML prediction stays unavailable while the gate is shut.",
    ...overrides,
  };
}

describe("isReady", () => {
  it("defers to the backend verdict", () => {
    expect(isReady(report())).toBe(false);
    expect(isReady(report({ verdict: "ready" }))).toBe(true);
  });
});

describe("headlineFigures", () => {
  it("pairs each figure with its required threshold", () => {
    const figures = headlineFigures(report());
    expect(figures).toEqual([
      { label: "Genuine labelled rows", value: 0, required: 80 },
      { label: "Distinct pans in labels", value: 0, required: 3 },
    ]);
  });
});

describe("countRows", () => {
  it("exposes the stored counts in display order", () => {
    const labels = countRows().map((row) => row.label);
    expect(labels[0]).toBe("Registered salt pans");
    expect(labels).toContain("Outcomes on file (incl. demos)");
  });
});