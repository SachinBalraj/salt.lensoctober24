import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  getObservation,
  listObservations,
  recordObservation,
} from "@/lib/observationsApi";
import type {
  ObservationRegistration,
  PanObservationListResponse,
} from "@/lib/observations";

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

const EMPTY_PAGE: PanObservationListResponse = {
  items: [],
  total: 0,
  limit: 200,
  offset: 0,
  earliest_timestamp: null,
  latest_timestamp: null,
};

const VALID_PAYLOAD: ObservationRegistration = {
  pan_id: "kzn-2026-a01",
  salinity_g_l: 281.4,
  water_depth_cm: 12.5,
  brine_temperature_c: 31.2,
  humidity_percent: 62,
  ambient_temperature_c: null,
  data_source: "manual",
};

describe("listObservations", () => {
  it("queries the collection endpoint", async () => {
    const mock = stubFetch(() => jsonResponse(EMPTY_PAGE));

    await listObservations();

    const url = new URL(callOf(mock).url);
    expect(url.pathname).toBe("/api/observations/");
  });

  it("narrows to one pan", async () => {
    const mock = stubFetch(() => jsonResponse(EMPTY_PAGE));

    await listObservations({ pan_id: "kzn-2026-a01" });

    const url = new URL(callOf(mock).url);
    expect(url.searchParams.get("pan_id")).toBe("kzn-2026-a01");
  });

  it("passes through a time window and paging", async () => {
    const mock = stubFetch(() => jsonResponse(EMPTY_PAGE));

    await listObservations({
      pan_id: "kzn-2026-a01",
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

    await listObservations({ pan_id: "kzn-2026-a01" });

    const url = new URL(callOf(mock).url);
    expect(url.searchParams.has("start")).toBe(false);
    expect(url.searchParams.has("end")).toBe(false);
  });
});

describe("recordObservation", () => {
  it("posts the payload to the collection endpoint", async () => {
    const mock = stubFetch(() =>
      jsonResponse({ observation_id: "obs-1" }, 201),
    );

    await recordObservation(VALID_PAYLOAD);

    const { url, init } = callOf(mock);
    expect(url).toBe(`${BASE}/api/observations/`);
    expect(init.method).toBe("POST");
    expect(bodyOf(init).pan_id).toBe("kzn-2026-a01");
  });

  it("sends an unmeasured field as null rather than omitting it", async () => {
    const mock = stubFetch(() => jsonResponse({ observation_id: "obs-1" }, 201));

    await recordObservation(VALID_PAYLOAD);

    const body = bodyOf(callOf(mock).init);
    expect(body).toHaveProperty("ambient_temperature_c", null);
  });

  it("omits the timestamp when the reading is stamped at save time", async () => {
    const mock = stubFetch(() => jsonResponse({ observation_id: "obs-1" }, 201));

    await recordObservation(VALID_PAYLOAD);

    expect(bodyOf(callOf(mock).init)).not.toHaveProperty("timestamp");
  });

  it("includes a backdated timestamp when one was supplied", async () => {
    const mock = stubFetch(() => jsonResponse({ observation_id: "obs-1" }, 201));

    await recordObservation({
      ...VALID_PAYLOAD,
      timestamp: "2026-03-01T06:00:00.000Z",
    });

    expect(bodyOf(callOf(mock).init).timestamp).toBe("2026-03-01T06:00:00.000Z");
  });

  it("surfaces a refused sensor reading as a 409", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          detail:
            "Sensor ingest is not available: no sensor gateway is connected to this deployment.",
        },
        409,
      ),
    );

    const error = await captureApiError(
      recordObservation({ ...VALID_PAYLOAD, data_source: "sensor" }),
    );

    expect(error.status).toBe(409);
    expect(error.isConnectivity).toBe(false);
    expect(error.message).toMatch(/sensor gateway is not connected|no sensor gateway/i);
  });

  it("surfaces an unregistered pan as a 404", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          detail: "No registered pan has the ID “ghost”. Register the pan first.",
          error_code: "document_not_found",
        },
        404,
      ),
    );

    const error = await captureApiError(recordObservation(VALID_PAYLOAD));

    expect(error.status).toBe(404);
    expect(error.isConnectivity).toBe(false);
  });
});

describe("getObservation", () => {
  it("encodes the identifier so odd characters cannot break the path", async () => {
    const mock = stubFetch(() => jsonResponse({ observation_id: "a/b" }));

    await getObservation("a/b c");

    expect(callOf(mock).url).toBe(`${BASE}/api/observations/a%2Fb%20c`);
  });
});

describe("validation errors are mapped onto form fields", () => {
  it("extracts one message per measurement from a 422 body", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          detail: [
            {
              loc: ["body", "humidity_percent"],
              msg: "Input should be less than or equal to 100",
              type: "less_than_equal",
            },
          ],
        },
        422,
      ),
    );

    const error = await captureApiError(recordObservation(VALID_PAYLOAD));

    expect(error.fields.humidity_percent).toBe(
      "Input should be less than or equal to 100",
    );
    expect(error.fields).not.toHaveProperty("body");
  });

  it("keeps a model-level rejection as a single message with no field", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          detail: [
            {
              loc: ["body"],
              msg: "Value error, Record at least one measured value.",
              type: "value_error",
            },
          ],
        },
        422,
      ),
    );

    const error = await captureApiError(recordObservation(VALID_PAYLOAD));

    expect(error.fields).toEqual({});
    expect(error.message).toContain("at least one measured value");
  });
});

describe("connectivity failures", () => {
  it("reports an unreachable API as a connectivity error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );

    const error = await captureApiError(listObservations({ pan_id: "a" }));

    expect(error.isConnectivity).toBe(true);
    expect(error.message).toContain(BASE);
  });

  it("treats a 503 from an unconfigured database as a connectivity error", async () => {
    stubFetch(() =>
      jsonResponse(
        { detail: "Database not configured. Set MONGODB_URI in backend/.env." },
        503,
      ),
    );

    const error = await captureApiError(listObservations({ pan_id: "a" }));

    expect(error.status).toBe(503);
    expect(error.isConnectivity).toBe(true);
  });
});

describe("malformed responses", () => {
  it("falls back to a status message when the body is not JSON", async () => {
    stubFetch(
      () =>
        new Response("<html>gateway error</html>", {
          status: 502,
          headers: { "Content-Type": "text/html" },
        }),
    );

    const error = await captureApiError(listObservations());

    expect(error.status).toBe(502);
    expect(error.message).toBe("Request failed with status 502.");
  });
});