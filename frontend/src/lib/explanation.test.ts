/**
 * Module 11's formatting and labelling rules, tested directly.
 *
 * The properties here are the ones a reader relies on without noticing: that a
 * contribution is never formatted as a probability, that an imputed column has no
 * value to print, that a direction is derived from the sign of the number rather
 * than from its magnitude, and that the features left out of the two capped lists
 * are still reachable.
 */

import { describe, expect, it } from "vitest";
import {
  describeAdditivity,
  describeDirection,
  describeGlobalScope,
  describeMethod,
  formatContribution,
  formatFeatureValue,
  leadingFeatures,
  unattributedFeatures,
  type FeatureAttribution,
  type GlobalImportance,
  type LocalExplanation,
} from "@/lib/explanation";

function attribution(over: Partial<FeatureAttribution> = {}): FeatureAttribution {
  return {
    feature: "current_salinity_g_l",
    feature_value: 280.4,
    shap_value: 0.25,
    direction: "increases_risk",
    source: "twin.estimated_salinity_g_l",
    source_kind: "digital_twin_state",
    imputed: false,
    ...over,
  };
}

function local(over: Partial<LocalExplanation> = {}): LocalExplanation {
  return {
    explanation_id: "exp-1",
    prediction_id: "pred-1",
    pan_id: "kzn-2026-a01",
    model_id: "model-1",
    model_version: "v1.0.0",
    model_name: null,
    algorithm: "logistic_regression",
    kind: "local",
    created_at: "2026-09-27T05:41:00.331992Z",
    target_class: "act now",
    input_snapshot_id: "sha256:abc",
    twin_id: "twin-1",
    twin_as_of: "2026-09-27T05:41:00.324955+00:00",
    quantities: {
      model_probability: {
        label: "Model probability",
        value: 0.75,
        unit: "probability",
        note: "The model's own output.",
      },
      shap_contribution: {
        label: "SHAP contribution",
        unit: "probability points",
        base_value: 0.5,
        explained_output: "probability",
        target_class: "act now",
        note: "How far one feature moved the output.",
      },
      physical_simulation: {
        label: "Physical simulation result",
        unit: "as named by each quantity",
        results: {},
        note: "Module 06's mass balance, held in the input row.",
      },
    },
    model_probability: 0.75,
    base_value: 0.5,
    attribution_error: 0,
    method: "tree_explainer",
    approximate: false,
    shap_version: "0.52.0",
    background: {
      source: "training_split_of_the_fitted_dataset",
      rows: 96,
      width: 4,
      train_rows: 96,
      holdout_rows: 24,
      selection: "first_rows_of_each_split_in_split_order",
    },
    attributions: [],
    top_increasing: [],
    top_decreasing: [],
    ...over,
  };
}

function globalImportance(
  over: Partial<GlobalImportance> = {},
): GlobalImportance {
  return {
    explanation_id: "exp-global-1",
    model_id: "model-1",
    model_version: "v1.0.0",
    model_name: null,
    algorithm: "logistic_regression",
    kind: "global",
    created_at: "2026-09-27T05:41:48.450787Z",
    explained_output: "probability",
    rows_explained: 24,
    explained_classes: { "act now": 16, watch: 8 },
    method: "permutation_explainer",
    approximate: true,
    shap_version: "0.52.0",
    background: {
      source: "held_out_split_scored_by_module_09",
      rows: 24,
      width: 4,
      train_rows: 96,
      holdout_rows: 24,
      selection: "first_rows_of_each_split_in_split_order",
    },
    measure: {
      label: "Mean absolute SHAP contribution",
      value: null,
      unit: "probability points",
      note: "Averaged in absolute value across the rows explained.",
    },
    features: [
      { feature: "current_salinity_g_l", mean_abs_shap: 0.236, rank: 1 },
      { feature: "water_depth_cm", mean_abs_shap: 0.141, rank: 2 },
    ],
    ...over,
  };
}

describe("formatContribution", () => {
  it("states the unit, because a change is not a level", () => {
    expect(formatContribution(0.25)).toBe("+25.0 pp");
  });

  it("keeps the sign, which is what orders the two factor lists", () => {
    expect(formatContribution(0.248)).toMatch(/^\+/);
    expect(formatContribution(-0.0763)).toMatch(/^-/);
  });

  it("reads a zero as zero rather than as a direction", () => {
    // A signed zero would be reported as "+0.00 pp", which is a claim that a
    // feature nudged the prediction up when it did nothing.
    expect(formatContribution(0)).toBe("0.00");
  });

  it("is more precise on a small contribution than on a large one", () => {
    expect(formatContribution(0.0004)).toBe("+0.04 pp");
    expect(formatContribution(0.2)).toBe("+20.0 pp");
  });

  it("never produces a bare percentage", () => {
    // The failure this guards: 0.25 rendered as "25%" reads as a probability.
    for (const value of [0.25, -0.25, 0.5, -1]) {
      expect(formatContribution(value)).not.toMatch(/%/);
    }
  });
});

describe("formatFeatureValue", () => {
  it("prints the value the model was given", () => {
    expect(formatFeatureValue(attribution({ feature_value: 280.4 }))).toBe("280.4");
  });

  it("says imputed rather than printing the preprocessor's median", () => {
    // The median is a statistic of the training rows. Printing it in this column
    // would read as a measurement of this pan.
    expect(formatFeatureValue(attribution({ feature_value: null, imputed: true }))).toBe(
      "imputed",
    );
  });
});

describe("describeDirection", () => {
  it("names the direction as a sentence about the risk", () => {
    expect(describeDirection("increases_risk")).toBe("pushed the risk up");
    expect(describeDirection("decreases_risk")).toBe("pushed the risk down");
    expect(describeDirection("neutral")).toBe("did not move the risk");
  });
});

describe("describeAdditivity", () => {
  it("says so plainly when the contributions account for the probability", () => {
    expect(describeAdditivity(local({ attribution_error: 0 }))).toMatch(/exactly/);
  });

  it("does not claim exactness for a floating-point residual", () => {
    expect(describeAdditivity(local({ attribution_error: 1.1e-15 }))).toMatch(
      /rounding/,
    );
  });

  it("reports a residual too large to trust rather than hiding it", () => {
    const message = describeAdditivity(local({ attribution_error: 0.02 }));
    expect(message).toContain("2.000");
    expect(message).toMatch(/miss/);
  });
});

describe("describeMethod", () => {
  it("distinguishes a computed value from an estimated one", () => {
    // Both are genuine SHAP. Presenting the estimated one as computed would be the
    // misrepresentation this module is built to avoid.
    expect(
      describeMethod({ method: "tree_explainer", approximate: false, shap_version: "0.52.0" }),
    ).toContain("computed exactly");
    expect(
      describeMethod({
        method: "permutation_explainer",
        approximate: true,
        shap_version: "0.52.0",
      }),
    ).toContain("estimated");
  });

  it("names the shap version the numbers came from", () => {
    expect(
      describeMethod({ method: "tree_explainer", approximate: false, shap_version: "0.46.0" }),
    ).toContain("0.46.0");
  });
});

describe("unattributedFeatures", () => {
  it("returns what the two capped lists left out", () => {
    const explanation = local({
      attributions: [attribution(), attribution({ feature: "b" }), attribution({ feature: "c" })],
      top_increasing: [attribution()],
      top_decreasing: [attribution({ feature: "b" })],
    });
    expect(unattributedFeatures(explanation).map((item) => item.feature)).toEqual(["c"]);
  });

  it("returns nothing when the lists cover every feature", () => {
    const explanation = local({
      attributions: [attribution(), attribution({ feature: "b" })],
      top_increasing: [attribution()],
      top_decreasing: [attribution({ feature: "b" })],
    });
    expect(unattributedFeatures(explanation)).toEqual([]);
  });

  it("keeps a zero contribution reachable", () => {
    // A feature absent from the lists might have contributed nothing. The panel
    // shows it, so "not shown" and "did not matter" stay different claims.
    const zero = attribution({ feature: "quiet", shap_value: 0, direction: "neutral" });
    const explanation = local({
      attributions: [attribution(), zero],
      top_increasing: [attribution()],
      top_decreasing: [],
    });
    expect(unattributedFeatures(explanation)).toEqual([zero]);
  });
});

describe("leadingFeatures", () => {
  it("takes the top of the server's ranking rather than re-ranking it", () => {
    const importance = globalImportance();
    expect(leadingFeatures(importance, 1)).toEqual([importance.features[0]]);
  });
});

describe("describeGlobalScope", () => {
  it("says the summary is over held-out rows", () => {
    // "Most important" without this is ambiguous between influence on unseen rows
    // and a model coefficient, and the two can disagree.
    expect(describeGlobalScope(globalImportance())).toContain("held-out");
  });

  it("reports the rows actually explained, not those requested", () => {
    expect(describeGlobalScope(globalImportance({ rows_explained: 7 }))).toContain("7 ");
  });

  it("says when the contributions were estimated", () => {
    expect(describeGlobalScope(globalImportance())).toContain("estimated");
  });
});
