/**
 * Feature importance card tests.
 *
 * The claims under test are about scope and provenance rather than arithmetic --
 * `tests/test_explain.py` on the backend recomputes the SHAP values from the
 * registered artifact. What the panel must not do is present a mean absolute
 * contribution as though it were a model coefficient, or show a ranking for a
 * model whose estimator has no probabilities to attribute.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FeatureImportanceCard from "@/components/evaluation/FeatureImportanceCard";
import { ApiError } from "@/lib/api";
import type { GlobalImportance } from "@/lib/explanation";
import { fetchGlobalImportance } from "@/lib/explanationApi";

vi.mock("@/lib/explanationApi", () => ({
  fetchGlobalImportance: vi.fn(),
  explanationUnavailableMessage: (error: ApiError) => error.message,
}));

const fetchMock = vi.mocked(fetchGlobalImportance);

function importance(over: Partial<GlobalImportance> = {}): GlobalImportance {
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
      note: "Averaged in absolute value across the rows explained. Not a causal claim, and not a model coefficient.",
    },
    features: [
      { feature: "current_salinity_g_l", mean_abs_shap: 0.2368, rank: 1 },
      { feature: "water_depth_cm", mean_abs_shap: 0.1411, rank: 2 },
    ],
    ...over,
  };
}

beforeEach(() => {
  fetchMock.mockReset();
});

describe("FeatureImportanceCard", () => {
  it("computes nothing on mount, because explaining held-out rows is real work", async () => {
    render(<FeatureImportanceCard modelId="model-1" version="v1.0.0" />);
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("asks for the model version when the button is pressed", async () => {
    fetchMock.mockResolvedValue(importance());
    render(<FeatureImportanceCard modelId="model-1" version="v1.0.0" />);
    await userEvent.click(screen.getByTestId("importance-ask"));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith({
      modelId: "model-1",
      version: "v1.0.0",
      rows: 25,
    }));
  });

  it("shows the ranking with the server's ranks, and does not re-rank it", async () => {
    fetchMock.mockResolvedValue(importance());
    render(<FeatureImportanceCard modelId="model-1" version="v1.0.0" />);
    await userEvent.click(screen.getByTestId("importance-ask"));
    await waitFor(() =>
      expect(screen.getByTestId("importance-current_salinity_g_l")).toBeTruthy(),
    );
    const first = screen.getByTestId("importance-current_salinity_g_l");
    expect(first.textContent).toContain("current_salinity_g_l");
    expect(first.textContent).toContain("23.7");
  });

  it("states that these are mean absolute SHAP contributions, not coefficients", async () => {
    // "Most important" is ambiguous between this model's influence on unseen rows
    // and a model coefficient. The two can disagree, and a reader comparing the
    // two needs to be told.
    fetchMock.mockResolvedValue(importance());
    render(<FeatureImportanceCard modelId="model-1" version="v1.0.0" />);
    await userEvent.click(screen.getByTestId("importance-ask"));
    expect(await screen.findByText("Mean absolute SHAP contribution")).toBeTruthy();
    expect(screen.getByText(/not a model coefficient/)).toBeTruthy();
  });

  it("says the summary is over held-out rows", async () => {
    fetchMock.mockResolvedValue(importance());
    render(<FeatureImportanceCard modelId="model-1" version="v1.0.0" />);
    await userEvent.click(screen.getByTestId("importance-ask"));
    // Anchored on the count, so the card's subtitle cannot satisfy it.
    const scope = await screen.findByText(/^24 held-out rows/);
    expect(scope.textContent).toContain("estimated");
  });

  it("shows no importance for a model with no probability, and no substitute for one", async () => {
    // The tempting fallback is a coefficient or a correlation. Either would be a
    // ranking of influence that no Shapley value was computed for.
    fetchMock.mockRejectedValue(
      new ApiError("model_reports_no_probability This model has no predict_proba.", {
        status: 409,
        code: "model_reports_no_probability",
      }),
    );
    render(<FeatureImportanceCard modelId="model-1" version="v1.0.0" />);
    await userEvent.click(screen.getByTestId("importance-ask"));

    expect(await screen.findByText(/model_reports_no_probability/)).toBeTruthy();
    expect(screen.queryByTestId("importance-current_salinity_g_l")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("offers a retry for a server error and not for a refusal", async () => {
    fetchMock.mockRejectedValue(new ApiError("boom", { status: 500 }));
    render(<FeatureImportanceCard modelId="model-1" version="v1.0.0" />);
    await userEvent.click(screen.getByTestId("importance-ask"));
    await screen.findByText("boom");
    expect(screen.getByText("Try again")).toBeTruthy();
  });

  it("computes nothing in demo mode", async () => {
    render(<FeatureImportanceCard modelId="model-1" version="v1.0.0" isDemo />);
    expect(screen.getByText("No importance in demo mode")).toBeTruthy();
    expect(screen.queryByTestId("importance-ask")).toBeNull();
  });
});
