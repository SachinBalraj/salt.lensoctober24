/**
 * Research-validation API client tests.
 *
 * Two things are worth locking down here.
 *
 * The URLs, because the export and pairs endpoints live beside the overview and
 * each chooses its rows from a `comparison` query, and a client that mixed the
 * keys up would download one comparison's rows under another's name.
 *
 * The absence of any POST. This module has nothing to write: a validation report
 * whose figures a caller could post would be worth nothing, so the client offers
 * no route to send one.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  getValidationOverview,
  getValidationPairs,
  getValidationStatus,
  validationExportUrl,
} from "@/lib/validationApi";

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

describe("getValidationStatus", () => {
  it("reads the status endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ module: "predicted-vs-actual" }));
    vi.stubGlobal("fetch", fetchMock);

    await getValidationStatus();

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/validation/status`);
  });

  it("reports an unreachable backend as connectivity", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    const error = await captureApiError(getValidationStatus());
    expect(error.status).toBe(0);
    expect(error.isConnectivity).toBe(true);
    expect(error.message).toContain("Could not reach");
  });
});

describe("getValidationOverview", () => {
  it("asks for the overview with no filters by default", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ scope: {} }));
    vi.stubGlobal("fetch", fetchMock);

    await getValidationOverview();

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/validation/overview`);
  });

  it("passes a pan filter and a limit as query parameters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ scope: {} }));
    vi.stubGlobal("fetch", fetchMock);

    await getValidationOverview({ pan_id: "KZN-2026-A01", limit: 50 });

    const [url] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE}/api/validation/overview?pan_id=KZN-2026-A01&limit=50`);
  });

  it("surfaces a refused request with the backend's message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse({ detail: "No such pan is registered." }, 404),
      ),
    );

    const error = await captureApiError(getValidationOverview());
    expect(error.status).toBe(404);
    expect(error.message).toBe("No such pan is registered.");
  });
});

describe("getValidationPairs", () => {
  it("names the comparison family in the query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ comparison: "simulated_vs_actual_salinity", items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await getValidationPairs("simulated_vs_actual_salinity");

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/api/validation/pairs?comparison=simulated_vs_actual_salinity`,
    );
  });

  it("keeps the pan filter on the comparison's own rows", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await getValidationPairs("forecast_vs_actual_rainfall", { pan_id: "pans-2026-a01" });

    expect(fetchMock.mock.calls[0][0]).toBe(
      `${BASE}/api/validation/pairs?comparison=forecast_vs_actual_rainfall&pan_id=pans-2026-a01`,
    );
  });
});

describe("validationExportUrl", () => {
  it("builds a download URL the browser can fetch natively", () => {
    expect(validationExportUrl("forecast_vs_actual_rainfall", "csv")).toBe(
      `${BASE}/api/validation/export?comparison=forecast_vs_actual_rainfall&output_format=csv`,
    );
    expect(validationExportUrl("predicted_risk_vs_outcome", "json")).toBe(
      `${BASE}/api/validation/export?comparison=predicted_risk_vs_outcome&output_format=json`,
    );
  });

  it("carries a pan filter onto the exported rows", () => {
    expect(
      validationExportUrl("recommended_vs_operator_action", "csv", {
        pan_id: "kzn-2026-a01",
      }),
    ).toBe(
      `${BASE}/api/validation/export?comparison=recommended_vs_operator_action&output_format=csv&pan_id=kzn-2026-a01`,
    );
  });
})