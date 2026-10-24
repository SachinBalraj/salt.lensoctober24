/**
 * MlRiskCard tests.
 *
 * The card is the dashboard's half of Module 10, and it speaks before any
 * button is pressed: the status probe read on mount decides what an operator is
 * told when they arrive. So these tests are about the three states a probe can
 * report and the claim it must never make — that a registry it could not read
 * is an empty one.
 *
 * The property that matters most: `no_model`, `not_evaluated` and
 * `not_deployed` are three sentences, because they are three different next
 * steps in three different modules. A card that showed one for all three would
 * be sending the operator to train something that is already in the registry.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MlRiskCard } from "@/components/dashboard/MlRiskCard";
import { ApiError } from "@/lib/api";
import {
  MODEL_DEPLOYMENT_REQUIRED_MESSAGE,
  MODEL_EVALUATION_REQUIRED_MESSAGE,
  NO_DEPLOYED_MODEL_MESSAGE,
  type RiskPrediction,
} from "@/lib/riskPrediction";
import {
  RiskApiError,
  type RiskModuleStatus,
} from "@/lib/riskPredictionApi";

function status(overrides: Partial<RiskModuleStatus> = {}): RiskModuleStatus {
  return {
    trained: false,
    model_state: "no_model",
    training_gate: {
      ready: false,
      genuine_labelled_rows: 7,
      distinct_pans_in_labels: 1,
      min_total_labelled_rows: 250,
      min_distinct_pans: 6,
    },
    implemented: true,
    capabilities: { risk_prediction: "implemented_gated_on_deployment" },
    ...overrides,
  };
}

function prediction(): RiskPrediction {
  return {
    prediction_id: "pred-1",
    pan_id: "kzn-2026-a01",
    predicted_at: "2026-03-14T12:05:00Z",
    as_of: "2026-03-14T12:00:00Z",
    risk_class: "act now",
    probability: 0.72,
    class_probabilities: { watch: 0.28, "act now": 0.72 },
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
    features_used: ["current_salinity_g_l"],
    features_absent: [],
    features_imputed: [],
    used_physics_prediction: true,
    input_source: "current_digital_twin_state",
    explanation_status: "unavailable",
    explanation_id: null,
    explanation_refusal: "model_reports_no_probability",
    explanation_refusal_detail: null,
  };
}

function renderCard(overrides: Partial<Parameters<typeof MlRiskCard>[0]> = {}) {
  return render(
    <MlRiskCard
      prediction={null}
      error={null}
      loading={false}
      onPredict={vi.fn()}
      hasRun={false}
      {...overrides}
    />,
  );
}

describe("what the status probe says before anything is pressed", () => {
  it("states a project that has never trained a model, with the gate's own numbers", () => {
    renderCard({ modelStatus: status() });

    expect(screen.getByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeTruthy();
    expect(
      screen.getByText(/validated model has not yet been deployed/),
    ).toBeTruthy();
    expect(
      screen.getByText(
        "Collect at least 250 genuine labelled outcomes across at least 6 distinct pans before training.",
      ),
    ).toBeTruthy();
    // The counts come from the backend's audit, not from a hardcoded zero.
    expect(
      screen.getByText(
        "7 of 250 genuine labelled outcomes on file, across 1 of 6 distinct pans.",
      ),
    ).toBeTruthy();
  });

  it("names evaluation as the missing step, not an absence", () => {
    renderCard({ modelStatus: status({ model_state: "not_evaluated" }) });

    expect(screen.getByText(MODEL_EVALUATION_REQUIRED_MESSAGE)).toBeTruthy();
    expect(screen.queryByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeNull();
    expect(screen.getByText(/Module 09 grades it/)).toBeTruthy();
  });

  it("names deployment as the missing step, not an absence", () => {
    renderCard({
      modelStatus: status({ model_state: "not_deployed", trained: true }),
    });

    expect(screen.getByText(MODEL_DEPLOYMENT_REQUIRED_MESSAGE)).toBeTruthy();
    expect(screen.queryByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeNull();
    expect(screen.getByText(/nobody has put it in service/)).toBeTruthy();
  });

  it("says nothing at all when the read did not answer", () => {
    // `null` means this client does not know. Asserting an absence here would
    // put a claim on the dashboard that nobody made, in the one situation where
    // the operator cannot tell the difference.
    renderCard({ modelStatus: null });

    expect(screen.queryByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeNull();
    expect(screen.queryByText(MODEL_EVALUATION_REQUIRED_MESSAGE)).toBeNull();
    expect(screen.getByText(/No prediction run for this pan/)).toBeTruthy();
  });

  it("says nothing when the backend could not read its own registry", () => {
    renderCard({
      modelStatus: status({ model_state: "unknown", trained: false }),
    });

    expect(screen.queryByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeNull();
    expect(screen.getByText(/No prediction run for this pan/)).toBeTruthy();
  });
});

describe("a stored prediction", () => {
  it("outranks a claim that no model exists", () => {
    // The probe describes the present; a stored prediction is a record of
    // something that happened. Hiding the record behind the claim would be
    // destroying evidence the dashboard was asked to display.
    renderCard({ prediction: prediction(), modelStatus: status() });

    expect(screen.getByTestId("ml-risk-body")).toBeTruthy();
    expect(screen.getAllByText("72.0%").length).toBeGreaterThan(0);
    expect(screen.queryByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeNull();
  });
});

describe("a refusal after the button is pressed", () => {
  it("reads the stage rather than every refusal being an absence", () => {
    renderCard({
      error: new RiskApiError("no_deployed_model nobody deployed it", {
        status: 409,
        code: "no_deployed_model",
        stage: "not_deployed",
      }),
      modelStatus: status({ model_state: "deployed", trained: true }),
    });

    expect(screen.getByText(MODEL_DEPLOYMENT_REQUIRED_MESSAGE)).toBeTruthy();
    expect(screen.queryByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeNull();
  });

  it("keeps a connectivity failure's own message, and reads no state from it", () => {
    // The registry was never reached, so nothing about it is known. Telling an
    // operator that no model exists would be the one claim this card must not
    // make in that situation.
    renderCard({
      error: new ApiError("Could not reach the SaltLens API at http://api.test.", {
        status: 0,
      }),
    });

    expect(
      screen.getByText("Could not reach the SaltLens API at http://api.test."),
    ).toBeTruthy();
    expect(screen.queryByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeNull();
  });
});
