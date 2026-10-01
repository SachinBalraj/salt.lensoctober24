/**
 * Research-validation display-shaping tests.
 *
 * The behaviours locked down here are the ones that could quietly turn a
 * validation report into a flattering one:
 *
 * - a metric the backend withheld renders as the dash, never `0`, `0.0` or `0%`;
 * - a classification rate renders with a `%`, so a perfect agree reads as "100%"
 *   rather than on the wrong scale;
 * - every block's headline states how many of how many events produced it;
 * - excluded events are summarised with their reasons, never just a count;
 * - the heading naming the rule a risk figure was graded under appears only for
 *   the risk block and only when the backend actually identified the rule.
 */

import { describe, expect, it } from "vitest";
import { MISSING_DISPLAY } from "@/lib/evaluation";
import {
  COMPARISON_KEYS,
  blockSampleCounts,
  comparisonDescription,
  comparisonKind,
  comparisonTitle,
  comparisonUnit,
  describeComparableReading,
  describeExcluded,
  describeRiskDefinition,
  describeSample,
  formatComparableReading,
  formatRate,
  isContinuousKey,
  type ClassificationComparison,
  type ContinuousComparison,
  type MetricReading,
} from "@/lib/validation";

function measured(value: number): MetricReading {
  return { value, computed: true, variant: null, reason: null };
}

function absent(reason: string): MetricReading {
  return { value: null, computed: false, variant: null, reason };
}

function continuous(overrides: Partial<ContinuousComparison> = {}): ContinuousComparison {
  return {
    key: "forecast_vs_actual_rainfall",
    title: "Forecast rainfall vs actual rainfall",
    unit: "mm",
    description: "The forecast's own error.",
    n_compared: 2,
    n_excluded: 2,
    excluded_reasons: [
      { reason: "No rainfall figure was recorded for the event.", count: 2 },
    ],
    mean_absolute_error: measured(12.35),
    root_mean_squared_error: measured(13.9),
    r_squared: absent("R-squared is only reported when the measured values actually vary."),
    mean_bias_error: measured(-4.6),
    notes: [],
    ...overrides,
  };
}

function classification(
  overrides: Partial<ClassificationComparison> = {},
): ClassificationComparison {
  return {
    key: "predicted_risk_vs_outcome",
    title: "Predicted risk vs recorded outcome",
    description: "The risk rule re-applied to what actually happened.",
    classes: ["low", "high"],
    n_compared: 2,
    n_excluded: 1,
    excluded_reasons: [
      { reason: "No recommendation was in force for this event.", count: 1 },
    ],
    predicted_distribution: { low: 0, high: 2 },
    actual_distribution: { low: 1, high: 1 },
    accuracy: measured(100),
    precision: measured(100),
    recall: measured(100),
    f1: measured(100),
    roc_auc: measured(1),
    pr_auc: measured(1),
    per_class: [],
    confusion_matrix: null,
    notes: [],
    definition_id: "risk-9001",
    definition_name: "salinity-loss-risk",
    definition_version: 1,
    ...overrides,
  };
}

describe("the five comparison families", () => {
  it("names every key the backend reports", () => {
    expect(COMPARISON_KEYS).toEqual([
      "forecast_vs_actual_rainfall",
      "simulated_vs_actual_salinity",
      "forecast_simulated_vs_actual_salinity",
      "predicted_risk_vs_outcome",
      "recommended_vs_operator_action",
    ]);
  });

  it("tags the two grading families as classification", () => {
    expect(comparisonKind("forecast_vs_actual_rainfall")).toBe("continuous");
    expect(comparisonKind("simulated_vs_actual_salinity")).toBe("continuous");
    expect(comparisonKind("forecast_simulated_vs_actual_salinity")).toBe("continuous");
    expect(comparisonKind("predicted_risk_vs_outcome")).toBe("classification");
    expect(comparisonKind("recommended_vs_operator_action")).toBe("classification");
    expect(isContinuousKey("predicted_risk_vs_outcome")).toBe(false);
    expect(isContinuousKey("simulated_vs_actual_salinity")).toBe(true);
  });

  it("keeps the rainfall block in mm and the salinity blocks in g/L", () => {
    expect(comparisonUnit("forecast_vs_actual_rainfall")).toBe("mm");
    expect(comparisonUnit("simulated_vs_actual_salinity")).toBe("g/L");
    expect(comparisonUnit("recommended_vs_operator_action")).toBeNull();
  });

  it("describes each family in words", () => {
    expect(comparisonTitle("forecast_simulated_vs_actual_salinity")).toContain(
      "salinity",
    );
    expect(comparisonDescription("predicted_risk_vs_outcome")).toContain(
      "risk rule re-applied",
    );
  });
});

describe("blockSampleCounts / describeSample", () => {
  it("states how many of how many events produced the figures", () => {
    expect(blockSampleCounts(continuous())).toEqual({ compared: 2, excluded: 2 });
    expect(describeSample(continuous())).toBe("Based on 2 of 4 recorded events.");
  });

  it("uses the singular for a single event", () => {
    const block = continuous({ n_compared: 1, n_excluded: 0 });
    expect(describeSample(block)).toBe("Based on 1 of 1 recorded event.");
  });

  it("does not invent a sample where there is none", () => {
    const block = continuous({ n_compared: 0, n_excluded: 0 });
    expect(describeSample(block)).toBe(
      "No recorded events in scope for this comparison yet.",
    );
  });
});

describe("describeExcluded", () => {
  it("returns nothing when nothing was excluded", () => {
    expect(describeExcluded(continuous({ n_excluded: 0, excluded_reasons: [] }))).toBeNull();
  });

  it("names the reasons with their counts", () => {
    const clause = describeExcluded(continuous());
    expect(clause).toContain("2 events — No rainfall figure was recorded");
    expect(clause?.startsWith("Excluded:")).toBe(true);
  });

  it("lists the big reason first", () => {
    const block = continuous({
      n_excluded: 3,
      excluded_reasons: [
        { reason: "A lone event.", count: 1 },
        { reason: "The big gap.", count: 2 },
      ],
    });
    const clause = describeExcluded(block);
    expect(clause?.indexOf("The big gap")).toBeLessThan(
      clause?.indexOf("A lone event") ?? -1,
    );
  });
});

describe("formatComparableReading", () => {
  it("shows a measured continuous figure with its unit", () => {
    expect(formatComparableReading(measured(12.35), "mm")).toBe("12.350 mm");
  });

  it("omits the unit when the block has none", () => {
    expect(formatComparableReading(measured(0.5), null)).toBe("0.500");
  });

  it("renders an absent metric as the dash, not as zero", () => {
    expect(formatComparableReading(absent("gated"), "mm")).toBe(MISSING_DISPLAY);
    expect(formatComparableReading(absent("gated"), "mm")).not.toMatch(/0/);
  });
});

describe("formatRate", () => {
  it("renders a classification rate as a percentage", () => {
    expect(formatRate(measured(100))).toBe("100.000%");
  });

  it("renders an absent rate as the dash, never as 0%", () => {
    expect(formatRate(absent("withheld"))).toBe(MISSING_DISPLAY);
    expect(formatRate(absent("withheld"))).not.toMatch(/0/);
  });
});

describe("describeRiskDefinition", () => {
  it("names the rule and version for the risk block", () => {
    expect(describeRiskDefinition(classification())).toBe(
      "salinity-loss-risk · version 1",
    );
  });

  it("stays silent for the action block", () => {
    const action = classification({ key: "recommended_vs_operator_action" });
    expect(describeRiskDefinition(action)).toBeNull();
  });

  it("stays silent when the backend could not identify a rule", () => {
    const unnamed = classification({ definition_name: null, definition_version: null });
    expect(describeRiskDefinition(unnamed)).toBeNull();
  });
});

describe("describeComparableReading", () => {
  it("names the metric and the figure when computed", () => {
    expect(describeComparableReading(measured(12.35), "mean_absolute_error")).toContain(
      "12.350",
    );
  });

  it("gives the backend's reason when withheld", () => {
    const words = describeComparableReading(
      absent("RMSE needs at least two comparable events."),
      "root_mean_squared_error",
    );
    expect(words).toContain("Not calculated");
    expect(words).toContain("two comparable events");
  });
})