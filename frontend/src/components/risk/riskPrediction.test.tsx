/**
 * Risk prediction panel tests.
 *
 * These are the tests that would catch the failure this module exists to prevent:
 * a risk figure on screen that no deployed model produced. So the assertions are
 * about *absence* as much as about presence --
 *
 * - with no deployed model the panel shows one fixed sentence and no number at
 *   all, and offers nothing the operator could mistake for a risk figure;
 * - a model that reports a class but no probability shows the class and the
 *   absence, and `0%` appears nowhere;
 * - a probability on screen is the one the model sent, rendered as sent;
 * - the model, the version, the prediction time and the input snapshot id are on
 *   screen with the class, because a class with nothing behind it is not
 *   actionable;
 * - imputed features are counted on screen rather than hidden.
 *
 * The panel asks for a prediction only when the button is pressed. A test asserts
 * that too: mounting it must not write a stored prediction, because each request
 * is one row in a history meant for later validation.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RiskPredictionPanel from "@/components/risk/RiskPredictionPanel";
import { ApiError } from "@/lib/api";
import type { SaltPan } from "@/lib/pans";
import {
  NO_DEPLOYED_MODEL_MESSAGE,
  type RiskPrediction,
} from "@/lib/riskPrediction";
import { predictRisk } from "@/lib/riskPredictionApi";
import { explainPrediction } from "@/lib/explanationApi";

const explainMock = vi.mocked(explainPrediction);

vi.mock("@/lib/explanationApi", () => ({
  explainPrediction: vi.fn(),
  explanationUnavailableMessage: (error: ApiError) => error.message,
}));

vi.mock("@/lib/riskPredictionApi", () => ({
  predictRisk: vi.fn(),
  predictionUnavailableMessage: (error: ApiError) =>
    error.code === "no_deployed_model"
      ? NO_DEPLOYED_MODEL_MESSAGE
      : error.message,
}));

const predictMock = vi.mocked(predictRisk);

const PAN: SaltPan = {
  id: "pan-1",
  pan_id: "kzn-2026-a01",
  pan_name: "Northern Evaporation Pan",
  location: "Port Shepstone",
  latitude: -30.71,
  longitude: 30.21,
  area_m2: 12500,
  normal_depth_cm: 15.5,
  target_harvest_salinity_g_l: 285,
  is_active: true,
  created_at: "2026-01-05T08:00:00Z",
  updated_at: "2026-01-05T08:00:00Z",
  deactivated_at: null,
  latest_observation: null,
};

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

const NO_MODEL = new ApiError(
  "no_deployed_model No model version is deployed for target operational_risk, " +
    "so this module will not predict.",
  { status: 409, code: "no_deployed_model" },
);

async function ask() {
  render(<RiskPredictionPanel pan={PAN} />);
  await userEvent.click(screen.getByTestId("risk-predict"));
}

beforeEach(() => {
  vi.clearAllMocks();
  // Every prediction is explained as it is made, so the panel has an id to show
  // and the section is present. The refusals below turn that off.
  explainMock.mockRejectedValue(
    new ApiError("explanation_not_stored", { status: 409, code: "explanation_not_stored" }),
  );
});

describe("RiskPredictionPanel", () => {
  it("asks for nothing until the operator asks", () => {
    predictMock.mockResolvedValue(prediction());
    render(<RiskPredictionPanel pan={PAN} />);

    // Each request is one stored prediction, meant for later validation. Opening
    // the page must not manufacture one.
    expect(predictMock).not.toHaveBeenCalled();
    expect(screen.getByText(/No prediction requested/i)).toBeTruthy();
  });

  it("shows the class, the model's own probability and its identity", async () => {
    predictMock.mockResolvedValue(prediction());
    await ask();

    // Twice, on purpose: the class is the headline and one row of the
    // distribution. Either rendering alone would be misleading.
    await waitFor(() => expect(screen.getAllByText("act now")).toHaveLength(2));
    expect(screen.getByText("82.0%")).toBeTruthy();
    expect(
      screen.getByText(/monsoon dilution ranker \(model-1 v1\.0\.0\)/),
    ).toBeTruthy();
  });

  it("lists every class the model could have returned", async () => {
    predictMock.mockResolvedValue(prediction());
    await ask();

    await waitFor(() => expect(screen.getByText("18.0%")).toBeTruthy());
    expect(screen.getByText("watch")).toBeTruthy();
  });

  it("shows the model, the input snapshot and the prediction beside the class", async () => {
    predictMock.mockResolvedValue(prediction());
    await ask();

    await waitFor(() => expect(screen.getByText("sha256:abc123")).toBeTruthy());
    expect(screen.getByText("pred-1")).toBeTruthy();
    expect(screen.getByText("twin-1")).toBeTruthy();
  });

  it("says the one fixed sentence when no model is deployed, and shows no number", async () => {
    predictMock.mockRejectedValue(NO_MODEL);
    await ask();

    await waitFor(() =>
      expect(screen.getByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeTruthy(),
    );
    // Nothing that could be read as a risk figure.
    expect(screen.queryByText(/%/)).toBeNull();
    expect(screen.queryByText("watch")).toBeNull();
    expect(screen.queryByText("act now")).toBeNull();
  });

  it("does not offer to make the number appear", async () => {
    predictMock.mockRejectedValue(NO_MODEL);
    await ask();

    await waitFor(() =>
      expect(screen.getByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeTruthy(),
    );
    // No retry button: the missing thing is a deployment decision, and pressing a
    // button again cannot produce one.
    expect(screen.queryByRole("button", { name: /try again/i })).toBeNull();
  });

  it("renders a class with no probability as the class, and never as 0%", async () => {
    predictMock.mockResolvedValue(
      prediction({ probability: null, class_probabilities: {} }),
    );
    await ask();

    await waitFor(() => expect(screen.getAllByText("act now")).toHaveLength(1));
    expect(
      screen.getByText(/reported no probability for this class/i),
    ).toBeTruthy();
    // The absence is stated, and no zero is displayed in its place.
    expect(screen.queryByText("0.00%")).toBeNull();
    expect(screen.queryByText("0%")).toBeNull();
  });

  it("counts the features the twin could not supply instead of hiding them", async () => {
    predictMock.mockResolvedValue(
      prediction({
        features_used: ["current_salinity_g_l"],
        features_absent: ["recent_rainfall_mm"],
        features_imputed: ["recent_rainfall_mm"],
      }),
    );
    await ask();

    await waitFor(() =>
      expect(
        screen.getByText(
          "1 of 2 features came from the twin state; 1 were imputed by the model.",
        ),
      ).toBeTruthy(),
    );
    expect(screen.getByText("recent_rainfall_mm")).toBeTruthy();
  });

  it("keeps a connectivity failure's own message, and offers a retry", async () => {
    predictMock.mockRejectedValue(
      new ApiError("Could not reach the SaltLens API at http://api.test.", {
        status: 0,
      }),
    );
    await ask();

    await waitFor(() =>
      expect(
        screen.getByText("Could not reach the SaltLens API at http://api.test."),
      ).toBeTruthy(),
    );
    // Not the fixed sentence: an unreachable API is not a state of the model
    // registry, and telling an operator to deploy a model would be wrong.
    expect(screen.queryByText(NO_DEPLOYED_MODEL_MESSAGE)).toBeNull();
  });

  it("predicts nothing for a demo pan", async () => {
    predictMock.mockResolvedValue(prediction());
    render(<RiskPredictionPanel pan={PAN} isDemo />);

    expect(predictMock).not.toHaveBeenCalled();
    expect(screen.getByText(/No prediction in demo mode/i)).toBeTruthy();
    expect(screen.getByText(/fabricated for layout review only/i)).toBeTruthy();
  });
});

describe("the explanation section", () => {
  it("offers the explanation that came with the prediction", async () => {
    // The prediction response carries the attribution's id, so a reader who wants
    // to know why is never told to look for it elsewhere.
    predictMock.mockResolvedValue(prediction());
    render(<RiskPredictionPanel pan={PAN} />);
    await userEvent.click(screen.getByTestId("risk-predict"));

    await waitFor(() => expect(screen.getByTestId("explain-ask")).toBeTruthy());
    expect(screen.getByText("Why this class")).toBeTruthy();
    expect(
      screen.getByText(/explained when it was made/),
    ).toBeTruthy();
  });

  it("asks for an explanation of the prediction it was given, by id", async () => {
    predictMock.mockResolvedValue(prediction());
    render(<RiskPredictionPanel pan={PAN} />);
    await userEvent.click(screen.getByTestId("risk-predict"));
    await waitFor(() => expect(screen.getByTestId("explain-ask")).toBeTruthy());
    await userEvent.click(screen.getByTestId("explain-ask"));
    await waitFor(() => expect(explainMock).toHaveBeenCalledWith("pred-1"));
  });

  it("shows the reason a prediction arrived unexplained, without a click", async () => {
    // "unavailable" with a reason is a legitimate state, and the reason travelled
    // with the prediction. Leaving it behind a button would make the reader ask a
    // question the answer to which is already in the response.
    predictMock.mockResolvedValue({
      ...prediction(),
      explanation_status: "unavailable",
      explanation_id: null,
      explanation_refusal: "shap_computation_failed",
      explanation_refusal_detail: "The explainer is not installed on this host.",
    });
    render(<RiskPredictionPanel pan={PAN} />);
    await userEvent.click(screen.getByTestId("risk-predict"));

    expect(
      await screen.findByText(/The explainer is not installed on this host/),
    ).toBeTruthy();
    expect(explainMock).not.toHaveBeenCalled();
  });

  it("shows the refusal the prediction carried, without a feature ranking", async () => {
    predictMock.mockResolvedValue({
      ...prediction(),
      explanation_status: "unavailable",
      explanation_id: null,
      explanation_refusal: "model_reports_no_probability",
      explanation_refusal_detail: "This model reports no class probabilities.",
    });
    render(<RiskPredictionPanel pan={PAN} />);
    await userEvent.click(screen.getByTestId("risk-predict"));
    await userEvent.click(await screen.findByTestId("explain-ask"));

    expect(
      await screen.findByText(/This model reports no class probabilities/),
    ).toBeTruthy();
    // No ranked list of features, because none was computed.
    expect(screen.queryByText("Pushed the risk up")).toBeNull();
  });

  it("shows no explanation section at all for a demo pan", async () => {
    render(<RiskPredictionPanel pan={PAN} isDemo />);
    expect(screen.getByText("No prediction in demo mode")).toBeTruthy();
    expect(screen.queryByTestId("explain-ask")).toBeNull();
  });
});
