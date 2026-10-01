/**
 * Outcome API client tests.
 *
 * Two things are worth locking down here. The URLs, because `/comparison` is a
 * literal path sitting in front of `/{outcome_id}` and a client that asked for
 * the wrong one would be silently reading a single event as a summary. And the
 * shape of what is posted, because the backend forbids unknown keys and that
 * refusal is the only thing stopping a client from submitting its own error
 * terms.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  getOutcome,
  getOutcomeComparison,
  listOutcomes,
  recordOutcome,
} from "@/lib/outcomesApi";
import type {
  EventOutcomeListResponse,
  EventOutcomeRegistration,
  OutcomeComparisonSummary,
} from "@/lib/outcomes";

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
    expect(caught).toBeInstanceOf(ApiError);
    return caught as ApiError;
  }
  throw new Error("Expected the request to reject, but it resolved.");
}

type FetchArgs = [input: RequestInfo | URL, init?: RequestInit];
type FetchMock = ReturnType<
  typeof vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>
>;

function stubFetch(response: Response | (() => Response | Promise<Response>)) {
  const produce = () =>
    typeof response === "function" ? response() : Promise.resolve(response);
  const mock = vi.fn<(...args: FetchArgs) => Promise<Response>>(
    async (input, init) => {
      void input;
      void init;
      return produce();
    },
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

function callOf(mock: FetchMock, index = 0): { url: string; init: RequestInit } {
  const call = mock.mock.calls[index] as FetchArgs;
  return { url: String(call[0]), init: call[1] as RequestInit };
}

function bodyOf(init: RequestInit): Record<string, unknown> {
  return JSON.parse(String(init.body)) as Record<string, unknown>;
}

const EMPTY_PAGE: EventOutcomeListResponse = {
  items: [],
  total: 0,
  limit: 200,
  offset: 0,
  earliest_timestamp: null,
  latest_timestamp: null,
};

const VALID_PAYLOAD: EventOutcomeRegistration = {
  pan_id: "kzn-2026-a01",
  event_id: "storm-2026-03-14",
  forecast_rainfall_mm: 30,
  actual_rainfall_mm: 40,
  pre_event_salinity_g_l: 280,
  pre_event_depth_cm: 20,
  post_event_salinity_g_l: 255,
  post_event_depth_cm: 24,
};

describe("listOutcomes", () => {
  it("queries the collection endpoint", async () => {
    const mock = stubFetch(() => jsonResponse(EMPTY_PAGE));

    await listOutcomes();

    expect(new URL(callOf(mock).url).pathname).toBe("/api/outcomes/");
  });

  it("narrows to one pan and one event name", async () => {
    const mock = stubFetch(() => jsonResponse(EMPTY_PAGE));

    await listOutcomes({ pan_id: "kzn-2026-a01", event_id: "storm-2026-03-14" });

    const url = new URL(callOf(mock).url);
    expect(url.searchParams.get("pan_id")).toBe("kzn-2026-a01");
    expect(url.searchParams.get("event_id")).toBe("storm-2026-03-14");
  });

  it("passes through a time window and paging", async () => {
    const mock = stubFetch(() => jsonResponse(EMPTY_PAGE));

    await listOutcomes({
      start: "2026-03-01T00:00:00Z",
      end: "2026-03-14T00:00:00Z",
      limit: 50,
      offset: 10,
    });

    const url = new URL(callOf(mock).url);
    expect(url.searchParams.get("start")).toBe("2026-03-01T00:00:00Z");
    expect(url.searchParams.get("end")).toBe("2026-03-14T00:00:00Z");
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.get("offset")).toBe("10");
  });

  it("omits an absent window rather than sending empty bounds", async () => {
    const mock = stubFetch(() => jsonResponse(EMPTY_PAGE));

    await listOutcomes({ pan_id: "kzn-2026-a01" });

    const url = new URL(callOf(mock).url);
    expect(url.searchParams.has("start")).toBe(false);
    expect(url.searchParams.has("end")).toBe(false);
  });
});

describe("getOutcome", () => {
  it("reads one outcome by its identifier", async () => {
    const mock = stubFetch(() => jsonResponse({ outcome_id: "outcome-1" }));

    await getOutcome("outcome-1");

    expect(callOf(mock).url).toBe(`${BASE}/api/outcomes/outcome-1`);
  });

  it("escapes the identifier rather than letting it reach the path", async () => {
    const mock = stubFetch(() => jsonResponse({ outcome_id: "odd/id" }));

    await getOutcome("odd/id");

    expect(callOf(mock).url).toBe(`${BASE}/api/outcomes/odd%2Fid`);
  });
});

describe("getOutcomeComparison", () => {
  const SUMMARY: OutcomeComparisonSummary = {
    pan_id: null,
    total_outcomes: 0,
    simulation_error: {
      label: "Salinity error",
      unit: "g/L",
      compared: 0,
      not_comparable: 0,
      coverage_percent: null,
      mean_absolute_error: null,
      mean_absolute_error_reason: "No event in scope.",
      bias: null,
      bias_reason: null,
      largest_absolute_error: null,
    },
    forecast_simulation_error: {
      label: "Salinity error",
      unit: "g/L",
      compared: 0,
      not_comparable: 0,
      coverage_percent: null,
      mean_absolute_error: null,
      mean_absolute_error_reason: null,
      bias: null,
      bias_reason: null,
      largest_absolute_error: null,
    },
    rainfall_forecast_error: {
      label: "Rainfall error",
      unit: "mm",
      compared: 0,
      not_comparable: 0,
      coverage_percent: null,
      mean_absolute_error: null,
      mean_absolute_error_reason: null,
      bias: null,
      bias_reason: null,
      largest_absolute_error: null,
    },
    events_missing_actual_salinity: 0,
    events_with_operator_action: 0,
  };

  it("asks for the literal comparison path, not an outcome identifier", async () => {
    // The router declares /comparison ahead of /{outcome_id}. Asking for the
    // wrong path would return one event shaped like a summary.
    const mock = stubFetch(() => jsonResponse(SUMMARY));

    await getOutcomeComparison();

    expect(new URL(callOf(mock).url).pathname).toBe("/api/outcomes/comparison");
  });

  it("can be narrowed to one pan", async () => {
    const mock = stubFetch(() => jsonResponse(SUMMARY));

    await getOutcomeComparison({ pan_id: "kzn-2026-a01", limit: 500 });

    const url = new URL(callOf(mock).url);
    expect(url.searchParams.get("pan_id")).toBe("kzn-2026-a01");
    expect(url.searchParams.get("limit")).toBe("500");
  });

  it("omits the pan when the summary covers every pan", async () => {
    const mock = stubFetch(() => jsonResponse(SUMMARY));

    await getOutcomeComparison();

    expect(new URL(callOf(mock).url).searchParams.has("pan_id")).toBe(false);
  });
});

describe("recordOutcome", () => {
  it("posts the payload to the collection endpoint", async () => {
    const mock = stubFetch(() => jsonResponse({ outcome_id: "outcome-1" }, 201));

    await recordOutcome(VALID_PAYLOAD);

    const { url, init } = callOf(mock);
    expect(url).toBe(`${BASE}/api/outcomes/`);
    expect(init.method).toBe("POST");
    expect(bodyOf(init).event_id).toBe("storm-2026-03-14");
  });

  it("sends an unmeasured figure as null rather than as zero", async () => {
    // This is the one that matters. Zero would be a fabricated measurement, and
    // the backend would store it as though somebody had read the gauge.
    const mock = stubFetch(() => jsonResponse({ outcome_id: "outcome-1" }, 201));

    await recordOutcome({ ...VALID_PAYLOAD, post_event_depth_cm: null });

    expect(bodyOf(callOf(mock).init)).toHaveProperty("post_event_depth_cm", null);
  });

  it("sends nothing the operator did not measure or do", async () => {
    const mock = stubFetch(() => jsonResponse({ outcome_id: "outcome-1" }, 201));

    await recordOutcome(VALID_PAYLOAD);

    // No operator action and no harvest result were given, so neither key is
    // sent rather than being sent as an empty string or a null.
    const body = bodyOf(callOf(mock).init);
    expect(body).not.toHaveProperty("operator_action");
    expect(body).not.toHaveProperty("harvest_outcome");
  });

  it("carries no predicted figure or error, because it has nowhere to put one", async () => {
    const mock = stubFetch(() => jsonResponse({ outcome_id: "outcome-1" }, 201));

    await recordOutcome(VALID_PAYLOAD);

    // The backend forbids unknown keys, and that refusal is the mechanism by
    // which a client cannot grade the model on its own account.
    const keys = Object.keys(bodyOf(callOf(mock).init)).sort();
    for (const forbidden of [
      "predicted",
      "signed_error",
      "absolute_error",
      "simulation_error",
      "mean_absolute_error",
    ]) {
      expect(keys).not.toContain(forbidden);
    }
  });

  it("omits the timestamp when the event is stamped at save time", async () => {
    const mock = stubFetch(() => jsonResponse({ outcome_id: "outcome-1" }, 201));

    await recordOutcome(VALID_PAYLOAD);

    expect(bodyOf(callOf(mock).init)).not.toHaveProperty("timestamp");
  });

  it("includes a backdated timestamp when one was supplied", async () => {
    const mock = stubFetch(() => jsonResponse({ outcome_id: "outcome-1" }, 201));

    await recordOutcome({
      ...VALID_PAYLOAD,
      timestamp: "2026-03-14T18:00:00.000Z",
    });

    expect(bodyOf(callOf(mock).init).timestamp).toBe("2026-03-14T18:00:00.000Z");
  });

  it("surfaces an unregistered pan as a 404", async () => {
    stubFetch(() =>
      jsonResponse(
        { detail: "No registered pan has the ID “ghost”.", error_code: "document_not_found" },
        404,
      ),
    );

    const error = await captureApiError(
      recordOutcome({ ...VALID_PAYLOAD, pan_id: "ghost" }),
    );

    expect(error.status).toBe(404);
    expect(error.isConnectivity).toBe(false);
    expect(error.message).toMatch(/No registered pan/);
  });

  it("attaches a rejected value to its own field", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          detail: [
            {
              loc: ["body", "post_event_salinity_g_l"],
              msg: "Input should be less than or equal to 500",
            },
          ],
        },
        422,
      ),
    );

    const error = await captureApiError(recordOutcome(VALID_PAYLOAD));

    expect(error.status).toBe(422);
    // The form can put this message on the salinity input rather than showing
    // one opaque banner.
    expect(error.fields.post_event_salinity_g_l).toMatch(/less than or equal/);
  });

  it("reports an unreachable backend as a connectivity failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new Error("network down"))),
    );

    const error = await captureApiError(recordOutcome(VALID_PAYLOAD));

    expect(error.status).toBe(0);
    expect(error.isConnectivity).toBe(true);
    expect(error.message).toMatch(/Could not reach the SaltLens API/);
  });
});
