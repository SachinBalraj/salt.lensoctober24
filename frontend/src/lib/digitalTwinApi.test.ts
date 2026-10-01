import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  getTwinState,
  listTwinHistory,
  refreshTwinState,
} from "@/lib/digitalTwinApi";
import type { DigitalTwinState } from "@/lib/digitalTwin";

function twin(overrides: Partial<DigitalTwinState> = {}): DigitalTwinState {
  return {
    twin_id: "twin-1",
    pan_id: "kzn-2026-a01",
    pan_name: "Northern Evaporation Pan",
    timestamp: "2026-03-14T12:00:00Z",
    provenance: "observed",
    twin_version: "0.1.0",
    salinity_g_l: 285.4,
    water_depth_cm: 12.5,
    brine_temperature_c: 31.2,
    ambient_temperature_c: 26.5,
    humidity_percent: 64,
    pan_area_m2: 12500,
    location: "Port Shepstone",
    forecast_rainfall_mm: 18,
    rainfall_probability_percent: 80,
    observation: {
      id: "obs-1",
      timestamp: "2026-03-14T11:00:00Z",
      source: "manual",
      age_seconds: 3600,
    },
    weather: null,
    weather_forecast: null,
    is_complete: false,
    missing_inputs: [
      {
        field: "forecast_rainfall_mm",
        reason: "No weather forecast has been recorded.",
        expected_from: "weather_forecast",
      },
    ],
    missing_fields: ["forecast_rainfall_mm", "rainfall_probability_percent"],
    quantities: ["salinity_g_l"],
    ...overrides,
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
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("reading a twin", () => {
  it("asks for the state of one pan", async () => {
    const fetchMock = mockFetch(twin());

    await getTwinState("kzn-2026-a01");

    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.test/api/twin/kzn-2026-a01/state",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("returns a body for a pan with nothing recorded", async () => {
    mockFetch(
      twin({
        salinity_g_l: null,
        is_complete: false,
        missing_inputs: [],
        missing_fields: [],
      }),
    );

    const state = await getTwinState("kzn-2026-a01");

    expect(state.salinity_g_l).toBeNull();
    expect(state.is_complete).toBe(false);
  });

  it("escapes an identifier that would otherwise change the path", async () => {
    const fetchMock = mockFetch(twin());

    await getTwinState("pan/../admin");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/api/twin/pan%2F..%2Fadmin/state",
    );
  });

  it("explains an unknown pan rather than failing silently", async () => {
    mockFetch({ detail: "No registered pan has the ID “nope”." }, { status: 404 });

    await expect(getTwinState("nope")).rejects.toThrow(
      /No registered pan has the ID/,
    );
  });

  it("explains an unreachable backend", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("connection refused")),
    );

    await expect(getTwinState("kzn-2026-a01")).rejects.toThrow(/Could not reach/);
  });

  it("carries a 503 through as a connectivity failure", async () => {
    mockFetch({ detail: "database unavailable" }, { status: 503 });

    const error = await getTwinState("kzn-2026-a01").catch((caught) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).isConnectivity).toBe(true);
  });
});

describe("listing twin snapshots", () => {
  it("asks for the history of one pan", async () => {
    const fetchMock = mockFetch({
      pan_id: "kzn-2026-a01",
      items: [twin()],
      total: 1,
      limit: 50,
      offset: 0,
      earliest_timestamp: "2026-03-14T12:00:00Z",
      latest_timestamp: "2026-03-14T12:00:00Z",
    });

    await listTwinHistory("kzn-2026-a01");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/api/twin/kzn-2026-a01/history",
    );
  });

  it("passes a window and paging through as query parameters", async () => {
    const fetchMock = mockFetch({
      pan_id: "kzn-2026-a01",
      items: [],
      total: 0,
      limit: 10,
      offset: 20,
      earliest_timestamp: null,
      latest_timestamp: null,
    });

    await listTwinHistory("kzn-2026-a01", {
      start: "2026-03-01T00:00:00Z",
      limit: 10,
      offset: 20,
    });

    const url = new URL(String(fetchMock.mock.calls[0][0]));
    expect(url.searchParams.get("start")).toBe("2026-03-01T00:00:00Z");
    expect(url.searchParams.get("limit")).toBe("10");
    expect(url.searchParams.get("offset")).toBe("20");
  });

  it("reports an empty history as empty rather than as a failure", async () => {
    mockFetch({
      pan_id: "kzn-2026-a01",
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
      earliest_timestamp: null,
      latest_timestamp: null,
    });

    const history = await listTwinHistory("kzn-2026-a01");

    expect(history.items).toEqual([]);
    expect(history.total).toBe(0);
  });

  it("surfaces a rejected window", async () => {
    mockFetch({ detail: "start must not be later than end." }, { status: 422 });

    await expect(
      listTwinHistory("kzn-2026-a01", {
        start: "2026-03-14T00:00:00Z",
        end: "2026-03-01T00:00:00Z",
      }),
    ).rejects.toThrow(/start must not be later than end/);
  });
});

describe("refreshing a twin", () => {
  it("posts to the refresh route and reports what changed", async () => {
    const fetchMock = mockFetch({
      state: twin(),
      stored: true,
      changed: false,
      previous_twin_id: "twin-0",
    });

    const result = await refreshTwinState("kzn-2026-a01");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/api/twin/kzn-2026-a01/refresh",
    );
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST" });
    expect(result.changed).toBe(false);
    expect(result.previous_twin_id).toBe("twin-0");
  });

  it("sends no state of its own, so the server owns the join", async () => {
    const fetchMock = mockFetch({
      state: twin(),
      stored: true,
      changed: true,
      previous_twin_id: null,
    });

    await refreshTwinState("kzn-2026-a01");

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.body).toBeUndefined();
  });

  it("reports the first snapshot as having no predecessor", async () => {
    mockFetch({
      state: twin(),
      stored: true,
      changed: true,
      previous_twin_id: null,
    });

    const result = await refreshTwinState("kzn-2026-a01");

    expect(result.previous_twin_id).toBeNull();
    expect(result.changed).toBe(true);
  });

  it("explains a refresh for an unknown pan", async () => {
    mockFetch({ detail: "No registered pan has the ID “nope”." }, { status: 404 });

    await expect(refreshTwinState("nope")).rejects.toThrow(
      /No registered pan has the ID/,
    );
  });
});
