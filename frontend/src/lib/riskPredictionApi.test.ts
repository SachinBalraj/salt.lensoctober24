import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  predictRisk,
  predictionUnavailableMessage,
} from "@/lib/riskPredictionApi";
import {
  NO_DEPLOYED_MODEL_MESSAGE,
  type RiskPrediction,
} from "@/lib/riskPrediction";

function prediction(): RiskPrediction {
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
    model_name: null,
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
    selection_criterion: null,
    features_used: ["current_salinity_g_l"],
    features_absent: [],
    features_imputed: [],
    used_physics_prediction: true,
    input_source: "current_digital_twin_state",
    explanation_status: "available",
    explanation_id: "exp-0123456789abcdef",
    explanation_refusal: null,
    explanation_refusal_detail: null,
  };
}

function mockFetch(body: unknown, init: { status?: number } = {}) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: (init.status ?? 200) < 400,
    status: init.status ?? 200,
    text: async () => (body === undefined ? "" : JSON.stringify(body)),
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("predictRisk", () => {
  it("asks about a pan and nothing else", async () => {
    const fetchMock = mockFetch(prediction());
    await predictRisk({ panId: "KZN-2026-A01" });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/risk/predict");
    expect(init.method).toBe("POST");
    // The body is a question, not an argument: no features, no model, no window.
    expect(JSON.parse(init.body)).toEqual({ pan_id: "KZN-2026-A01" });
  });

  it("sends an anchor only when one is asked for", async () => {
    const fetchMock = mockFetch(prediction());
    await predictRisk({ panId: "KZN-2026-A01", asOf: "2026-03-14T12:00:00Z" });
    const [, init] = fetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({
      pan_id: "KZN-2026-A01",
      as_of: "2026-03-14T12:00:00Z",
    });
  });

  it("returns the prediction as sent", async () => {
    mockFetch(prediction());
    await expect(predictRisk({ panId: "KZN-2026-A01" })).resolves.toMatchObject({
      risk_class: "act now",
      probability: 0.82,
      model_version: "1.0.0",
      input_snapshot_id: "sha256:abc123",
    });
  });

  it("carries the refusal code through, so no prose has to be matched", async () => {
    mockFetch(
      { detail: "no_deployed_model No model version is deployed...", refusal: "no_deployed_model" },
      { status: 409 },
    );
    const error = await predictRisk({ panId: "KZN-2026-A01" }).catch((caught) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("no_deployed_model");
    expect((error as ApiError).status).toBe(409);
  });

  it("reports an unreachable API as a connectivity failure, not a refusal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("network down")),
    );
    const error = await predictRisk({ panId: "KZN-2026-A01" }).catch((caught) => caught);
    expect((error as ApiError).isConnectivity).toBe(true);
    expect((error as ApiError).code).toBeNull();
  });
});

describe("predictionUnavailableMessage", () => {
  it("gives the one fixed sentence when no model is deployed", () => {
    const error = new ApiError("a long server explanation", {
      status: 409,
      code: "no_deployed_model",
    });
    expect(predictionUnavailableMessage(error)).toBe(NO_DEPLOYED_MODEL_MESSAGE);
  });

  it("passes an unrecognised refusal through rather than flattening it", () => {
    // An unmodelled refusal is a fact about the system, not something to hide
    // behind a sentence that means a different state.
    const error = new ApiError("Pan has no digital twin state.", {
      status: 409,
      code: "no_twin_state",
    });
    expect(predictionUnavailableMessage(error)).toBe(
      "Pan has no digital twin state.",
    );
  });

  it("keeps a connectivity failure's own wording", () => {
    const error = new ApiError("Could not reach the SaltLens API.", { status: 0 });
    expect(predictionUnavailableMessage(error)).toBe(
      "Could not reach the SaltLens API.",
    );
  });
});
