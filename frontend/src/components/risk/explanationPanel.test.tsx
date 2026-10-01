/**
 * Explanation panel tests.
 *
 * A list of features is the easiest thing in this application to fabricate by
 * accident: the endpoint returns numbers, and a panel that sorts them, scales them
 * or falls back to the feature list is three lines away from showing an influence
 * ranking that no Shapley value was ever computed for. So the assertions here are
 * mostly about what the panel refuses to do.
 *
 * - a contribution is never rendered as a percentage;
 * - a refused explanation shows the server's reason and no features at all;
 * - an imputed feature shows no value;
 * - an estimated explanation says so;
 * - the features left out of the two capped lists are still on screen.
 *
 * The API is mocked, so every number below is one the test itself wrote. What is
 * being checked is the panel's handling of them, not the service's arithmetic --
 * `tests/test_explain.py` on the backend recomputes the attributions from the
 * registered artifact with shap called directly, and that is the test that would
 * fail if the numbers were invented.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExplanationPanel from "@/components/risk/ExplanationPanel";
import { ApiError } from "@/lib/api";
import type { LocalExplanation } from "@/lib/explanation";
import { explainPrediction } from "@/lib/explanationApi";

vi.mock("@/lib/explanationApi", () => ({
  explainPrediction: vi.fn(),
  explanationUnavailableMessage: (error: ApiError) => error.message,
}));

const explainMock = vi.mocked(explainPrediction);

function attribution(over: Record<string, unknown> = {}) {
  return {
    feature: "current_salinity_g_l",
    feature_value: 280.4,
    shap_value: 0.2488,
    direction: "increases_risk",
    source: "twin.estimated_salinity_g_l",
    source_kind: "digital_twin_state",
    imputed: false,
    ...over,
  };
}

function explanation(over: Partial<LocalExplanation> = {}): LocalExplanation {
  const increasing = attribution();
  const decreasing = attribution({
    feature: "physics_predicted_post_rain_salinity_g_l",
    feature_value: 231.87,
    shap_value: -0.0763,
    direction: "decreases_risk",
    source: "module_06.mass_balance",
    source_kind: "physical_simulation",
  });
  return {
    explanation_id: "exp-1",
    prediction_id: "pred-1",
    pan_id: "kzn-2026-a01",
    model_id: "model-1",
    model_version: "v1.0.0",
    model_name: "Salt stress classifier",
    algorithm: "logistic_regression",
    kind: "local",
    created_at: "2026-09-27T05:41:00.331992Z",
    target_class: "act now",
    input_snapshot_id: "sha256:abc123",
    twin_id: "twin-1",
    twin_as_of: "2026-09-27T05:41:00.324955+00:00",
    quantities: {
      model_probability: {
        label: "Model probability",
        value: 0.9973,
        unit: "probability",
        note: "The model's own output for the predicted class.",
      },
      shap_contribution: {
        label: "SHAP contribution",
        unit: "probability points",
        base_value: 0.6667,
        explained_output: "probability",
        target_class: "act now",
        note: "How far one feature moved the output.",
      },
      physical_simulation: {
        label: "Physical simulation result",
        unit: "as named by each quantity",
        results: { physics_predicted_post_rain_salinity_g_l: 231.87 },
        note: "Module 06's mass balance, held in the input row as an input.",
      },
    },
    model_probability: 0.9973,
    base_value: 0.6667,
    attribution_error: 1.1e-15,
    method: "permutation_explainer",
    approximate: true,
    shap_version: "0.52.0",
    background: {
      source: "training_split_of_the_fitted_dataset",
      rows: 96,
      width: 4,
      train_rows: 96,
      holdout_rows: 24,
      selection: "first_rows_of_each_split_in_split_order",
    },
    attributions: [increasing, decreasing, attribution({ feature: "quiet_feature", shap_value: 0 })],
    top_increasing: [increasing],
    top_decreasing: [decreasing],
    ...over,
  } as LocalExplanation;
}

beforeEach(() => {
  explainMock.mockReset();
});

describe("ExplanationPanel", () => {
  it("does not compute anything on mount", async () => {
    // The prediction was already explained when it was made. Opening the pan page
    // should not ask the server to explain it again.
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId="exp-1"
        status="available"
        refusalDetail={null}
      />,
    );
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(explainMock).not.toHaveBeenCalled();
  });

  it("shows what pushed the risk up and what pushed it down, with the values", async () => {
    explainMock.mockResolvedValue(explanation());
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId="exp-1"
        status="available"
        refusalDetail={null}
      />,
    );

    await userEvent.click(screen.getByTestId("explain-ask"));

    await waitFor(() => {
      expect(screen.getByText("Pushed the risk up")).toBeTruthy();
    });
    expect(screen.getByText("Pushed the risk down")).toBeTruthy();
    const up = screen.getByTestId("attribution-current_salinity_g_l");
    expect(up.textContent).toContain("+24.9 pp");
    const down = screen.getByTestId(
      "attribution-physics_predicted_post_rain_salinity_g_l",
    );
    expect(down.textContent).toContain("-7.63 pp");
  });

  it("never renders a contribution as a percentage", async () => {
    // "+24.9 pp" and "24.9%" are different claims. Rendering the first as the
    // second would say the pan is 25% more likely, which is not what it means.
    explainMock.mockResolvedValue(explanation());
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId="exp-1"
        status="available"
        refusalDetail={null}
      />,
    );
    await userEvent.click(screen.getByTestId("explain-ask"));
    await waitFor(() => expect(explainMock).toHaveBeenCalled());

    const row = screen.getByTestId("attribution-current_salinity_g_l");
    expect(row.textContent).not.toContain("%");
  });

  it("shows the probability separately, in percent, because it is a level", async () => {
    explainMock.mockResolvedValue(explanation());
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId="exp-1"
        status="available"
        refusalDetail={null}
      />,
    );
    await userEvent.click(screen.getByTestId("explain-ask"));
    await waitFor(() => expect(screen.getByText("99.7%")).toBeTruthy());
  });

  it("says a feature was imputed instead of printing the value that was imputed", async () => {
    // The preprocessor's median is a statistic of the training rows. Printing it in
    // the value's place would read as a measurement of this pan.
    const imputed = attribution({
      feature: "recent_rainfall_mm",
      feature_value: null,
      imputed: true,
      source: null,
      source_kind: "imputed",
    });
    explainMock.mockResolvedValue(
      explanation({
        attributions: [imputed],
        top_increasing: [imputed],
        top_decreasing: [],
      } as Partial<LocalExplanation>),
    );
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId="exp-1"
        status="available"
        refusalDetail={null}
      />,
    );
    await userEvent.click(screen.getByTestId("explain-ask"));
    const row = await screen.findByTestId("attribution-recent_rainfall_mm");
    expect(row.textContent).toContain("imputed");
  });

  it("shows the features the capped lists left out", async () => {
    // Otherwise "this feature contributed nothing" and "this feature was not shown"
    // are the same thing on screen, and only one of them is true.
    explainMock.mockResolvedValue(explanation());
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId="exp-1"
        status="available"
        refusalDetail={null}
      />,
    );
    await userEvent.click(screen.getByTestId("explain-ask"));
    expect(await screen.findByText("Every other feature")).toBeTruthy();
    expect(screen.getByText("quiet_feature")).toBeTruthy();
  });

  it("marks a mass-balance input as a simulation, not a model output", async () => {
    explainMock.mockResolvedValue(explanation());
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId="exp-1"
        status="available"
        refusalDetail={null}
      />,
    );
    await userEvent.click(screen.getByTestId("explain-ask"));
    expect(await screen.findByText("Physical simulation inputs")).toBeTruthy();
    const row = screen.getByTestId("attribution-physics_predicted_post_rain_salinity_g_l");
    expect(row.textContent).toContain("from mass balance");
  });

  it("says the values were estimated, because the sampled explainer estimates them", async () => {
    explainMock.mockResolvedValue(explanation());
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId="exp-1"
        status="available"
        refusalDetail={null}
      />,
    );
    await userEvent.click(screen.getByTestId("explain-ask"));
    await waitFor(() => expect(screen.getByText("Estimated SHAP")).toBeTruthy());
    expect(screen.getByText(/estimated by shap/)).toBeTruthy();
  });

  it("says when the values were computed exactly", async () => {
    explainMock.mockResolvedValue(
      explanation({ approximate: false, method: "tree_explainer" } as Partial<LocalExplanation>),
    );
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId="exp-1"
        status="available"
        refusalDetail={null}
      />,
    );
    await userEvent.click(screen.getByTestId("explain-ask"));
    await waitFor(() => expect(screen.getByText("Exact SHAP")).toBeTruthy());
  });

  it("shows the reason an explanation could not be produced, and no features", async () => {
    // The failure this guards: showing a plausible ranked list because the real
    // numbers could not be computed. A refusal must look like a refusal.
    explainMock.mockRejectedValue(
      new ApiError("model_reports_no_probability This model has no predict_proba.", {
        status: 409,
        code: "model_reports_no_probability",
      }),
    );
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId={null}
        status="unavailable"
        refusalDetail="The model reports no class probabilities."
      />,
    );
    await userEvent.click(screen.getByTestId("explain-ask"));

    expect(
      await screen.findByText(/model_reports_no_probability/),
    ).toBeTruthy();
    expect(screen.queryByText("Pushed the risk up")).toBeNull();
    expect(screen.queryByText("Pushed the risk down")).toBeNull();
    expect(screen.queryByText("Model probability")).toBeNull();
  });

  it("offers no retry for a refused explanation, since asking again cannot help", async () => {
    // What is missing is a deployment decision or a restored artifact. A retry
    // button would invite an operator to keep pressing it.
    explainMock.mockRejectedValue(
      new ApiError("artifact_unavailable model-1 is not on this host.", {
        status: 409,
        code: "artifact_unavailable",
      }),
    );
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId={null}
        status="unavailable"
        refusalDetail={null}
      />,
    );
    await userEvent.click(screen.getByTestId("explain-ask"));
    await screen.findByText(/artifact_unavailable/);
    expect(screen.queryByText("Try again")).toBeNull();
  });

  it("offers a retry for a connectivity failure, which is a different thing", async () => {
    explainMock.mockRejectedValue(
      new ApiError("Could not reach the SaltLens API.", { status: 0 }),
    );
    render(
      <ExplanationPanel
        predictionId="pred-1"
        explanationId={null}
        status="unavailable"
        refusalDetail={null}
      />,
    );
    await userEvent.click(screen.getByTestId("explain-ask"));
    await screen.findByText(/Could not reach/);
    expect(screen.getByText("Try again")).toBeTruthy();
  });

  it("explains nothing in demo mode, because there is no model to explain with", async () => {
    render(
      <ExplanationPanel
        predictionId="pan-1"
        explanationId={null}
        status="unavailable"
        refusalDetail={null}
        isDemo
      />,
    );
    expect(screen.getByText("No explanation in demo mode")).toBeTruthy();
    expect(screen.queryByTestId("explain-ask")).toBeNull();
  });
});
