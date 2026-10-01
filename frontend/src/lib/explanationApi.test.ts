/**
 * Module 11 API client tests.
 *
 * Three things are worth locking down here.
 *
 * **The local request names only the prediction.** The prediction already knows
 * which model version answered it and which input row it read. A client that could
 * name a model would be able to ask "explain this row with that model", and the
 * answer would be an attribution of a number that model never produced -- so the
 * path is asserted, and asserted to carry nothing else.
 *
 * **A refusal keeps its code.** "No probability to attribute" and "the artifact is
 * not on this host" are different facts with different fixes, so a client that
 * flattened them into one message would leave an operator guessing. The codes are
 * checked, not just the prose.
 *
 * **The global summary is a GET with an explicit row budget.** It is real work
 * over held-out rows, so the request that triggers it is visible in the test rather
 * than hidden in a mount effect.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  ARTIFACT_UNAVAILABLE_REFUSAL,
  EXPLANATION_NOT_STORED_REFUSAL,
  explanationUnavailableMessage,
  explainPrediction,
  fetchGlobalImportance,
  fetchModelExplanations,
  NO_PROBABILITY_MESSAGE,
  NO_PROBABILITY_REFUSAL,
  readPredictionExplanation,
} from "@/lib/explanationApi";

const BASE = "http://localhost:8000";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

async function captureApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (caught) {
    if (caught instanceof ApiError) return caught;
    throw caught;
  }
  throw new Error("Expected the request to fail, but it resolved.");
}

describe("explainPrediction", () => {
  it("asks about a prediction and sends nothing else", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ explanation_id: "exp-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await explainPrediction("pred-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE}/api/explain/predictions/pred-1`);
    expect(init.method).toBe("POST");
    // The model and the row are the server's to know. A body here would be a place
    // for a caller to name a model that did not produce the number.
    expect(init.body).toBeUndefined();
  });

  it("encodes an id rather than concatenating it into the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);

    await explainPrediction("pred/../models");

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/api/explain/predictions/pred%2F..%2Fmodels`,
    );
  });
});

describe("readPredictionExplanation", () => {
  it("reads the stored explanation without asking for a new one", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ explanation_id: "exp-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await readPredictionExplanation("pred-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE}/api/explain/predictions/pred-1`);
    // A read that computed as a side effect would store an attribution nobody
    // asked for, on a page merely opened.
    expect(init.method).toBeUndefined();
  });
});

describe("fetchGlobalImportance", () => {
  it("asks for a model version's importance, with the row budget stated", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ features: [], rows_explained: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchGlobalImportance({ modelId: "model-1", version: "v1.0.0", rows: 25 });

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/api/explain/models/model-1/versions/v1.0.0/importance?rows=25`,
    );
  });

  it("omits the parameter when no budget was asked for, so the server decides", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ features: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchGlobalImportance({ modelId: "model-1", version: "v1.0.0" });

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/api/explain/models/model-1/versions/v1.0.0/importance`,
    );
  });
});

describe("fetchModelExplanations", () => {
  it("asks for the version's explanation history", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ count: 0, explanations: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchModelExplanations("model-1", "v1.0.0");

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/api/explain/models/model-1/versions/v1.0.0/explanations`,
    );
  });
});

describe("refusals", () => {
  it("keeps the refusal code so the caller can tell the cases apart", async () => {
    // The server sends `refusal`; the client must surface it rather than only prose.
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse(
        { detail: "The model has no predict_proba.", refusal: NO_PROBABILITY_REFUSAL },
        409,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await captureApiError(explainPrediction("pred-1"));
    expect(error.code).toBe(NO_PROBABILITY_REFUSAL);
    expect(error.message).toContain("predict_proba");
  });

  it("gives the class-only refusal a fixed sentence, since it is a fact about the model", async () => {
    const error = new ApiError("A long paragraph about margins.", {
      status: 409,
      code: NO_PROBABILITY_REFUSAL,
    });
    expect(explanationUnavailableMessage(error)).toBe(NO_PROBABILITY_MESSAGE);
  });

  it("says an unstored explanation is different from an uncomputable one", async () => {
    const error = new ApiError("Nothing stored yet.", {
      status: 409,
      code: EXPLANATION_NOT_STORED_REFUSAL,
    });
    expect(explanationUnavailableMessage(error)).toMatch(/No SHAP explanation is available/);
  });

  it("keeps the server's own wording for a fact about this deployment", async () => {
    // The detail names the model whose file is missing, which is what an operator
    // needs in order to act. Replacing it with a generic sentence would throw that
    // away.
    const error = new ApiError("artifact_unavailable model-abc v1.0.0 is not on this host.", {
      status: 409,
      code: ARTIFACT_UNAVAILABLE_REFUSAL,
    });
    expect(explanationUnavailableMessage(error)).toContain("model-abc");
  });

  it("passes an unrecognised refusal through rather than flattening it", async () => {
    // A refusal this client does not model is new information about the system,
    // not something to paper over with a sentence that hides the code.
    const error = new ApiError("something new happened", {
      status: 409,
      code: "a_refusal_this_client_does_not_know",
    });
    expect(explanationUnavailableMessage(error)).toBe("something new happened");
  });

  it("reports a connectivity failure in its own words, because it is not a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network down")));

    const error = await captureApiError(explainPrediction("pred-1"));
    expect(error.isConnectivity).toBe(true);
    expect(explanationUnavailableMessage(error)).toContain("Could not reach");
  });

  it("reads a validation error's detail array rather than printing [object Object]", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({ detail: [{ msg: "rows must be <= 50" }] }, 422),
    );
    vi.stubGlobal("fetch", fetchMock);

    const error = await captureApiError(
      fetchGlobalImportance({ modelId: "m", version: "v", rows: 5000 }),
    );
    expect(error.message).toBe("rows must be <= 50");
  });
});
