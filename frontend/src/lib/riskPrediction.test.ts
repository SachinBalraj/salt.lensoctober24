import { describe, expect, it } from "vitest";
import {
  classProbabilityRows,
  DEFAULT_TRAINING_REQUIREMENT,
  describeFeatureCoverage,
  formatModel,
  formatProbability,
  GATED_BY_DEPLOYMENT_MESSAGE,
  hasProbability,
  MODEL_DEPLOYMENT_REQUIRED_MESSAGE,
  MODEL_EVALUATION_REQUIRED_MESSAGE,
  NO_DEPLOYED_MODEL_MESSAGE,
  reportsNoProbability,
  trainingProgress,
  trainingRequirement,
  unavailableMessage,
  type RiskPrediction,
  type TrainingGate,
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

const GATE: TrainingGate = {
  ready: false,
  genuine_labelled_rows: 0,
  distinct_pans_in_labels: 0,
  min_total_labelled_rows: 80,
  min_distinct_pans: 3,
};

describe("unavailableMessage", () => {
  it("gives each state its own sentence", () => {
    expect(unavailableMessage("no_model", "server prose")).toBe(
      NO_DEPLOYED_MODEL_MESSAGE,
    );
    expect(unavailableMessage("not_evaluated", "server prose")).toBe(
      MODEL_EVALUATION_REQUIRED_MESSAGE,
    );
    expect(unavailableMessage("not_deployed", "server prose")).toBe(
      MODEL_DEPLOYMENT_REQUIRED_MESSAGE,
    );
    // The missing-inputs state names the fields that arrived empty...
    expect(
      unavailableMessage("missing_inputs", "server prose", [
        "current_salinity_g_l",
        "water_depth_cm",
      ]),
    ).toBe(
      "Prediction unavailable — missing: current_salinity_g_l, water_depth_cm",
    );
    // ...and falls back to the server's explanation rather than showing a
    // sentence with an empty list after it.
    expect(unavailableMessage("missing_inputs", "Pan has no twin state.")).toBe(
      "Pan has no twin state.",
    );
    // A refusal this client does not model is passed through, not flattened
    // into a sentence that means a different state.
    expect(unavailableMessage("failed", "something unmodelled")).toBe(
      "something unmodelled",
    );
  });

  it("never renders one lifecycle state as another", () => {
    // The three are three different next steps in three different modules. If
    // two produced the same sentence an operator could not tell whether to go
    // and train, evaluate or deploy.
    const sentences = [
      unavailableMessage("no_model", "x"),
      unavailableMessage("not_evaluated", "x"),
      unavailableMessage("not_deployed", "x"),
    ];
    expect(new Set(sentences).size).toBe(3);
  });

  it("never puts a percentage in any refused state", () => {
    // No refusal carries a figure, so none can be mistaken for a score.
    for (const state of [
      "no_model",
      "not_evaluated",
      "not_deployed",
      "missing_inputs",
      "failed",
    ] as const) {
      expect(unavailableMessage(state, "prose", ["a"])).not.toMatch(/%/);
    }
  });
});

describe("trainingRequirement", () => {
  it("is built from the gate's own thresholds, not from literals here", () => {
    expect(
      trainingRequirement({
        ...GATE,
        min_total_labelled_rows: 120,
        min_distinct_pans: 5,
      }),
    ).toBe(
      "Collect at least 120 genuine labelled outcomes across at least 5 distinct pans before training.",
    );
  });

  it("falls back to the documented wording when the gate could not be read", () => {
    expect(trainingRequirement(null)).toBe(DEFAULT_TRAINING_REQUIREMENT);
    expect(
      trainingRequirement({
        ...GATE,
        min_total_labelled_rows: null,
        min_distinct_pans: null,
      }),
    ).toBe(DEFAULT_TRAINING_REQUIREMENT);
    // The fallback still states numbers a reader would act on, and is itself
    // asserted against the backend's thresholds in the backend suite.
    expect(DEFAULT_TRAINING_REQUIREMENT).toContain("80");
    expect(DEFAULT_TRAINING_REQUIREMENT).toContain("3 distinct pans");
  });
});

describe("trainingProgress", () => {
  it("reports counts rather than a percentage", () => {
    expect(trainingProgress(GATE)).toBe(
      "0 of 80 genuine labelled outcomes on file, across 0 of 3 distinct pans.",
    );
    expect(
      trainingProgress({
        ...GATE,
        genuine_labelled_rows: 42,
        distinct_pans_in_labels: 2,
      }),
    ).toBe(
      "42 of 80 genuine labelled outcomes on file, across 2 of 3 distinct pans.",
    );
  });

  it("says nothing once the gate is met, or when there is nothing to report", () => {
    expect(trainingProgress({ ...GATE, ready: true })).toBeNull();
    expect(trainingProgress(null)).toBeNull();
  });
});

describe("why there is no figure", () => {
  it("states the cause before the requirement, and neither carries a number", () => {
    expect(GATED_BY_DEPLOYMENT_MESSAGE).toContain(
      "validated model has not yet been deployed",
    );
    // Neither sentence could be read as a risk score.
    expect(GATED_BY_DEPLOYMENT_MESSAGE).not.toMatch(/%/);
    expect(DEFAULT_TRAINING_REQUIREMENT).not.toMatch(/%/);
  });
});
