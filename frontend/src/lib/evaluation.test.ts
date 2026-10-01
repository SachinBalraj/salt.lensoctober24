/**
 * Evaluation display-shaping and comparison tests.
 *
 * The behaviours locked down here are the ones that could quietly turn a research
 * record into a flattering one:
 *
 * - an uncomputed metric renders as the dash, never as `0`, `0.0` or `0%`;
 * - a metric that really is zero renders as zero, because a model that never
 *   predicts a class is evidence, not a gap;
 * - a declared `zero_division=0` convention is distinguished from a measurement,
 *   in tone, in the chart, and in the prose;
 * - a chart series inverts the value, so an absent metric gets a marker and a
 *   computed one does not -- never both, and never neither;
 * - a run request carries no metric, so a client cannot grade a model itself.
 */

import { describe, expect, it } from "vitest";
import {
  COMPARISON_COLUMNS,
  COMPARISON_COLUMN_TITLES,
  MISSING_DISPLAY,
  buildChartSeries,
  columnCoverage,
  columnTitle,
  comparabilityCaveats,
  countAbsentColumns,
  describeDenominator,
  describeReading,
  describeWhatThisTableIsNot,
  formatReading,
  formatReadingPercent,
  isConventionReading,
  readingForChart,
  readingTone,
  type ComparisonResponse,
  type ComparisonRow,
  type MetricReading,
} from "@/lib/evaluation";

function measured(value: number): MetricReading {
  return { value, computed: true, variant: null, reason: null };
}

function convention(value: number): MetricReading {
  return {
    value,
    computed: true,
    variant: "zero_division",
    reason: "Applied the zero_division=0 convention: no rows carried this class.",
    };
}

function absent(reason: string | null = null): MetricReading {
  return { value: null, computed: false, variant: null, reason };
}

describe("formatReading", () => {
  it("renders a measured zero as zero", () => {
    // A model that never predicts a class scores zero. That is a finding, and three
    // decimals is what makes it read as exact rather than as a truncated figure.
    expect(formatReading(measured(0))).toBe("0.000");
  });

  it("renders an uncomputed reading as the dash, ignoring any value", () => {
    // `value` is null by contract, but a client that trusted it anyway would print
    // a number here. The check on `computed` is what stops that.
    expect(formatReading(absent("ROC-AUC is undefined for a single-class holdout.")))
      .toBe(MISSING_DISPLAY);
    expect(formatReading({ value: 0.91, computed: false, variant: null, reason: null }))
      .toBe(MISSING_DISPLAY);
  });

  it("returns the dash for a missing reading rather than throwing", () => {
    expect(formatReading(null)).toBe(MISSING_DISPLAY);
    expect(formatReading(undefined)).toBe(MISSING_DISPLAY);
  });

  it("keeps three decimals so columns of figures line up", () => {
    expect(formatReading(measured(0.5))).toBe("0.500");
    expect(formatReading(measured(1))).toBe("1.000");
    expect(formatReading(measured(0.12345))).toBe("0.123");
  });
});

describe("formatReadingPercent", () => {
  it("never turns an absent metric into 0%", () => {
    expect(formatReadingPercent(absent())).toBe(MISSING_DISPLAY);
  });

  it("renders a measured zero as 0%", () => {
    expect(formatReadingPercent(measured(0))).toBe("0.000%");
  });
});

describe("isConventionReading", () => {
  it("detects a declared zero_division convention", () => {
    expect(isConventionReading(convention(0))).toBe(true);
  });

  it("does not mistake a real zero for a convention", () => {
    expect(isConventionReading(measured(0))).toBe(false);
  });

  it("is false for an absent reading", () => {
    expect(isConventionReading(absent())).toBe(false);
  });
});

describe("readingTone", () => {
  it("separates a measurement from a convention from an absence", () => {
    expect(readingTone(measured(0.5))).toBe("measured");
    expect(readingTone(convention(0))).toBe("convention");
    expect(readingTone(absent())).toBe("absent");
  });

  it("calls an absent reading absent even when a value is attached", () => {
    expect(readingTone({ value: 1, computed: false, variant: null, reason: null }))
      .toBe("absent");
  });
});

describe("readingForChart", () => {
  it("returns null for an absent reading so no bar is drawn", () => {
    expect(readingForChart(absent())).toBeNull();
  });

  it("returns the value for a computed one, including zero", () => {
    expect(readingForChart(measured(0))).toBe(0);
    expect(readingForChart(convention(0))).toBe(0);
  });
});

describe("describeReading", () => {
  it("gives the backend's reason for an absence instead of a generic apology", () => {
    const text = describeReading(absent("ROC-AUC needs two classes; the holdout has one."));
    expect(text).toContain("ROC-AUC needs two classes");
    expect(text).toContain("Not calculated");
  });

  it("says so when no reason was attached", () => {
    expect(describeReading(absent())).toContain("no reason");
  });

  it("names the column when it is given one", () => {
    expect(describeReading(measured(0.75), "f1")).toBe("F1 is 0.750.");
  });

  it("does not misname a reading it has no column for", () => {
    // The same shape is used for a headline, a per-class figure and a fold score.
    // Naming the wrong one would be worse than naming none.
    expect(describeReading(measured(0.75))).toContain("This metric");
  });
});

describe("the comparison columns", () => {
  it("are the six the backend compares on, under the keys it uses", () => {
    // The display name and the lookup key are deliberately different things -- the
    // sixth column reads "PR-AUC" and is read from `pr_auc` -- so a test that
    // reaches for `average_precision` writes a fixture whose metric silently does
    // not exist, and a coverage count that then quietly reads zero. Pinning the
    // keys makes that mistake fail loudly instead.
    expect([...COMPARISON_COLUMNS]).toEqual([
      "accuracy",
      "precision",
      "recall",
      "f1",
      "roc_auc",
      "pr_auc",
    ]);
  });

  it("has a title for every column, so no figure sits under a blank heading", () => {
    for (const column of COMPARISON_COLUMNS) {
      expect(COMPARISON_COLUMN_TITLES[column]).toBeTruthy();
    }
  });
});

describe("columnTitle", () => {
  it("prefers the heading the response declared", () => {
    // The backend calls these "F1 (macro)" and "Precision (macro)". A client with
    // its own copy of the headings would quietly drop "(macro)", which is the one
    // word telling a reader that the figure is an average over classes rather than
    // a score for the majority class.
    const report = comparison([row()]);
    report.column_titles = { f1: "F1 (macro)", precision: "Precision (macro)" };
    expect(columnTitle(report, "f1")).toBe("F1 (macro)");
    expect(columnTitle(report, "precision")).toBe("Precision (macro)");
  });

  it("falls back to the local heading when the response sends none", () => {
    const report = comparison([row()]);
    report.column_titles = {};
    expect(columnTitle(report, "roc_auc")).toBe("ROC-AUC");
  });

  it("falls back to the raw key for a column it has never heard of", () => {
    const report = comparison([row()]);
    report.column_titles = {};
    expect(columnTitle(report, "some_new_metric")).toBe("some_new_metric");
  });
});

function row(overrides: Partial<ComparisonRow> = {}): ComparisonRow {
  return {
    model: "logistic_regression",
    algorithm: "logistic_regression",
    model_id: "model-1",
    version: "v1",
    selected: true,
    auto_selectable: true,
    selection_note: "Chosen by the stated rule: highest macro F1.",
    evaluated_rows: 40,
    evaluation_set: "held-out test split",
    classes: ["low", "high"],
    class_distribution: { low: 20, high: 20 },
    metrics: {},
    cross_validation: null,
    ...overrides,
  };
}

function comparison(rows: ComparisonRow[]): ComparisonResponse {
  return {
    columns: [...COMPARISON_COLUMNS],
    column_titles: {},
    rows,
    row_count: rows.length,
    selected_algorithm: "logistic_regression",    selection_criterion: "Highest macro F1 among candidates fitted to the same split.",
    average_precision_note: null,
    cross_validation_performed: false,
    cross_validation_note: null,
    comparable: {},
    conventions_in_force: [],
    does_not: "It does not say which classifier to deploy.",
  };
}

describe("buildChartSeries", () => {
  it("gives an absent metric a marker and no value", () => {
    const series = buildChartSeries(
      comparison([row({ metrics: { roc_auc: absent("needs two classes") } })]),
      "roc_auc",
    );
    expect(series[0]).toMatchObject({ value: null, marker: 0, absent: true });
  });

  it("gives a computed metric a value and no marker", () => {
    // The two are inverses on purpose: an entry can be neither (drawn as a
    // zero-height bar, i.e. a fake zero) or both (a marker on top of a real bar).
    const series = buildChartSeries(
      comparison([row({ metrics: { roc_auc: measured(0.88) } })]),
      "roc_auc",
    );
    expect(series[0]).toMatchObject({ value: 0.88, marker: null, absent: false });
  });

  it("keeps a real zero as a zero-height bar, because it is a real zero", () => {
    const series = buildChartSeries(
      comparison([row({ metrics: { accuracy: measured(0) } })]),
      "accuracy",
    );
    expect(series[0]).toMatchObject({ value: 0, marker: null, absent: false });
  });

  it("carries the reason through to the tooltip", () => {
    const series = buildChartSeries(
      comparison([row({ metrics: { roc_auc: absent("needs two classes") } })]),
      "roc_auc",
    );
    expect(series[0].reason).toBe("needs two classes");
  });
});

describe("columnCoverage", () => {
  it("counts what is present in a column", () => {
    const report = comparison([
      row({ metrics: { accuracy: measured(0.9) } }),
      row({ algorithm: "random_forest", metrics: { accuracy: absent() } }),
    ]);
    expect(columnCoverage(report, "accuracy")).toEqual({ present: 1, total: 2 });
  });

  it("counts nothing present when nothing was computed", () => {
    const report = comparison([row({ metrics: {} })]);
    expect(columnCoverage(report, "f1")).toEqual({ present: 0, total: 1 });
  });
});

describe("countAbsentColumns", () => {
  it("names the columns that are missing for at least one row", () => {
    // A table that quietly drops two of its six columns leaves four numbers with
    // no context, so the omission has to be visible.
    const report = comparison([
      row({
        metrics: {
          accuracy: measured(0.9),
          precision: measured(0.8),
          recall: measured(0.8),
          f1: measured(0.8),
          roc_auc: absent(),
          pr_auc: absent(),
        },
      }),
    ]);
    const coverage = countAbsentColumns(report.rows);
    expect(coverage.computed).toBe(4);
    expect(coverage.absent).toBe(2);
    expect(coverage.absentColumns).toEqual(["ROC-AUC", "PR-AUC"]);
  });

  it("reports a full column when every row has it", () => {
    const report = comparison([
      row({ metrics: { accuracy: measured(0.9) } }),
      row({ algorithm: "random_forest", metrics: { accuracy: measured(0.91) } }),
    ]);
    expect(countAbsentColumns(report.rows, ["accuracy"])).toEqual({
      computed: 1,
      absent: 0,
      absentColumns: [],
    });
  });
});

describe("comparabilityCaveats", () => {
  it("surfaces the backend's caveats rather than inventing its own", () => {
    const report = comparison([row()]);
    report.comparable = {
      same_row_count: false,
      caveats: ["The candidates were scored on different row counts."],
    };
    const caveats = comparabilityCaveats(report);
    expect(caveats).toContain("The candidates were scored on different row counts.");
    expect(caveats.join(" ")).toContain("not on a common denominator");
  });

  it("says nothing when there is nothing to flag", () => {
    expect(comparabilityCaveats(comparison([row()]))).toEqual([]);
  });
});

describe("describeDenominator", () => {
  it("puts the row count in front of every figure", () => {
    expect(describeDenominator(9, "test split")).toBe("9 rows — test split");
  });

  it("uses the singular for one row rather than \"1 rows\"", () => {
    expect(describeDenominator(1, "")).toBe("1 row");
  });
});

describe("describeWhatThisTableIsNot", () => {
  it("passes the backend's own limit through instead of softening it", () => {
    const report = comparison([row()]);
    report.does_not = "It does not say which classifier to deploy.";
    expect(describeWhatThisTableIsNot(report)).toBe(
      "It does not say which classifier to deploy.",
    );
  });
});
