import { describe, expect, it } from "vitest";
import {
  classProbabilityRows,
  describeFeatureCoverage,
  formatModel,
  formatProbability,
  hasProbability,
  NO_DEPLOYED_MODEL_MESSAGE,
  reportsNoProbability,
  type RiskPrediction,
} from "@/lib/riskPrediction";

function prediction(
  overrides: Partial<RiskPrediction> = {},
): RiskPrediction {
  return {
    prediction_id: "pred-1",
    pan_id: "kzn-2026-a01",
    predicted_at: "2026-03-14T12:05:00Z",
    as_of: "2026-03-14T12:00:00Z",
    risk_class: "act now",
    probability: 0.82,
    class_probabilities: { watch: 0.18, "act now": 0.82 },
    defined_classes: ["watch", "act now"],
    model_id: "model-1",
    model_name: "monsoon dilution ranker",
    model_version: "1.0.0",
    algorithm: "logistic_regression",
    deployment_id: "dep-1",
    deployed_at: "2026-03-14T11:00:00Z",
    input_snapshot_id: "sha256:abc123",
    twin_id: "twin-1",
    twin_as_of: "2026-03-14T12:00:00Z",
    risk_definition_id: "rd-1",
    risk_definition_version: 1,
    dataset_id: "ds-1",
    dataset_version: 1,
    selection_criterion: "macro_f1",
    features_used: ["current_salinity_g_l", "water_depth_cm"],
    features_absent: [],
    features_imputed: [],
    used_physics_prediction: true,
    input_source: "current_digital_twin_state",
    explanation_status: "available",
    explanation_id: "exp-0123456789abcdef",
    explanation_refusal: null,
    explanation_refusal_detail: null,
    ...overrides,
  };
}

describe("the unavailable message", () => {
  it("is one fixed sentence", () => {
    // Asserted as a literal, not against the constant it is, because the point of
    // pinning this string is that every surface reads the same words to an
    // operator. A test that compared the constant to itself would pass if the
    // sentence were rewritten to say the opposite.
    expect(NO_DEPLOYED_MODEL_MESSAGE).toBe(
      "ML model not trained — prediction unavailable",
    );
  });

  it("names the cause and the consequence without calling it a fault", () => {
    expect(NO_DEPLOYED_MODEL_MESSAGE).toContain("not trained");
    expect(NO_DEPLOYED_MODEL_MESSAGE).toContain("unavailable");
    expect(NO_DEPLOYED_MODEL_MESSAGE).not.toMatch(/error|fail|broken/i);
  });
});

describe("formatProbability", () => {
  it("renders a probability the model reported", () => {
    expect(formatProbability(0.82)).toBe("82.0%");
    expect(formatProbability(0.045)).toBe("4.50%");
    expect(formatProbability(1)).toBe("100.0%");
    expect(formatProbability(0)).toBe("0.00%");
  });

  it("never renders an absent probability as a number", () => {
    // The single most damaging rendering this panel could do: a null shown as
    // 0% asserts a number the model never produced.
    expect(formatProbability(null)).toBe("not reported by the model");
    expect(formatProbability(null)).not.toContain("%");
    expect(formatProbability(null)).not.toContain("0");
  });
});

describe("probability presence", () => {
  it("tells a reported probability from an absent one", () => {
    expect(hasProbability(prediction())).toBe(true);
    expect(reportsNoProbability(prediction())).toBe(false);
    expect(
      reportsNoProbability(prediction({ probability: null, class_probabilities: {} })),
    ).toBe(true);
  });
});

describe("classProbabilityRows", () => {
  it("orders widest first and marks the predicted class", () => {
    const rows = classProbabilityRows(
      prediction({
        class_probabilities: { watch: 0.18, "act now": 0.82 },
      }),
    );
    expect(rows.map((row) => row.className)).toEqual(["act now", "watch"]);
    expect(rows[0].isPredicted).toBe(true);
    expect(rows[1].isPredicted).toBe(false);
  });

  it("falls back to the rule's own order when probabilities tie", () => {
    const rows = classProbabilityRows(
      prediction({
        risk_class: "watch",
        class_probabilities: { "act now": 0.5, watch: 0.5 },
        defined_classes: ["watch", "act now"],
      }),
    );
    // The researcher's ordering of severity, not insertion order.
    expect(rows.map((row) => row.className)).toEqual(["watch", "act now"]);
  });

  it("is empty when the model reported no probabilities", () => {
    expect(
      classProbabilityRows(prediction({ probability: null, class_probabilities: {} })),
    ).toEqual([]);
  });
});

describe("formatModel", () => {
  it("shows the identity whether or not a label was given", () => {
    expect(formatModel(prediction())).toBe("monsoon dilution ranker (model-1 v1.0.0)");
    expect(formatModel(prediction({ model_name: null }))).toBe("model-1 v1.0.0");
  });

  it("always contains the model id, so a label cannot replace it", () => {
    expect(formatModel(prediction())).toContain("model-1");
  });
});

describe("describeFeatureCoverage", () => {
  it("says so when every feature came from the twin", () => {
    expect(describeFeatureCoverage(prediction())).toBe(
      "All 2 features came from the twin state.",
    );
  });

  it("counts the imputed ones rather than hiding them", () => {
    expect(
      describeFeatureCoverage(
        prediction({
          features_used: ["current_salinity_g_l"],
          features_absent: ["recent_rainfall_mm", "hours_since_previous_observation"],
        }),
      ),
    ).toBe(
      "1 of 3 features came from the twin state; 2 were imputed by the model.",
    );
  });
});
