/**
 * Evaluation API client tests.
 *
 * Two things are worth locking down here.
 *
 * The URLs, because `/compare/versions` is a literal path sitting in front of
 * `/{model_id}`, and a client that asked for the wrong one would be reading a
 * single model's figures as a comparison across every version.
 *
 * The shape of what is posted. The backend forbids unknown keys, and that refusal
 * is the only thing stopping a client from submitting its own accuracy alongside
 * a request to be graded -- so a test asserts the payload has no metric in it,
 * rather than trusting that nobody added one.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  compareVersions,
  evaluationExportUrl,
  getComparison,
  getEvaluation,
  listEvaluations,
  runEvaluation,
} from "@/lib/evaluationApi";

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

/** Runs `promise` and returns the ApiError it rejects with. */
async function captureApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (caught) {
    if (caught instanceof ApiError) return caught;
    throw caught;
  }
  throw new Error("Expected the request to fail, but it resolved.");
}

describe("runEvaluation", () => {
  it("posts only the model id", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ evaluation_id: "eval-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await runEvaluation("model-1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE}/api/evaluation/run`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ model_id: "model-1" });
  });

  it("sends no metric, in any form", async () => {
    // The client offers nowhere to put one. A caller that wanted to grade its own
    // model would have to reach past this function and edit the payload.
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ evaluation_id: "eval-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await runEvaluation("model-1", { crossValidationSplits: 5 });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(Object.keys(body).sort()).toEqual(["cross_validation_splits", "model_id"]);
    expect(JSON.stringify(body)).not.toMatch(
      /accuracy|precision|recall|f1|auc|score|confusion/,
    );
  });

  it("omits a null fold count rather than sending one", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ evaluation_id: "eval-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await runEvaluation("model-1", { crossValidationSplits: null });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ model_id: "model-1" });
  });

  it("carries a dataset-drift refusal code on the error", async () => {
    // 409 is split from the other failures in the UI: a drifted dataset is fixed by
    // re-registering, not by trying again, so the client has to know which it is.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { detail: "The records no longer match the fitted dataset.", refusal: "dataset_drift" },
          409,
        ),
      ),
    );

    const error = await captureApiError(runEvaluation("model-1"));
    expect(error.status).toBe(409);
    expect(error.code).toBe("dataset_drift");
    expect(error.message).toBe("The records no longer match the fitted dataset.");
  });

  it("carries an artifact-unavailable refusal code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          { detail: "The fitted model is not on this host.", refusal: "artifact_unavailable" },
          409,
        ),
      ),
    );

    const error = await captureApiError(runEvaluation("model-1"));
    expect(error.code).toBe("artifact_unavailable");
  });

  it("reports an unreachable backend as connectivity, not as a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    const error = await captureApiError(runEvaluation("model-1"));
    expect(error.isConnectivity).toBe(true);
    expect(error.code).toBeNull();
    expect(error.message).toContain("Could not reach");
  });
});

describe("getComparison", () => {
  it("passes the model id as a query parameter, not a path segment", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ rows: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await getComparison("model 1");

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/api/evaluation/compare?model_id=model+1`,
    );
  });
});

describe("compareVersions", () => {
  it("asks for the literal path rather than a model id", async () => {
    // `/compare/versions` and `/{model_id}` are siblings under the same prefix, so
    // the order of the two routes is what keeps this from resolving to a model
    // called "compare".
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ evaluations: [], count: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await compareVersions();

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/evaluation/compare/versions`);
  });
});

describe("getEvaluation", () => {
  it("escapes the model id into the path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ evaluation_id: "eval-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await getEvaluation("model/1");

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/api/evaluation/model%2F1/metrics`,
    );
  });

  it("surfaces a not-evaluable 404 with its code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "This model has no stored report.", refusal: "not_evaluable" }, 404),
      ),
    );

    const error = await captureApiError(getEvaluation("model-1"));
    expect(error.status).toBe(404);
    expect(error.code).toBe("not_evaluable");
  });
});

describe("listEvaluations", () => {
  it("reads the listing endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ evaluations: [], count: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await listEvaluations();

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/evaluation/metrics`);
  });
});

describe("evaluationExportUrl", () => {
  it("builds a URL the browser can download directly", async () => {
    // A URL rather than a fetch, so the browser handles the download and the
    // filename the backend chose is honoured; nothing is re-serialised on the way.
    expect(evaluationExportUrl("model-1", "csv")).toBe(
      `${BASE}/api/evaluation/model-1/export?format=csv`,
    );
    expect(evaluationExportUrl("model-1", "json")).toBe(
      `${BASE}/api/evaluation/model-1/export?format=json`,
    );
  });

  it("escapes the model id", () => {
    expect(evaluationExportUrl("model 1", "json")).toBe(
      `${BASE}/api/evaluation/model%201/export?format=json`,
    );
  });
});
