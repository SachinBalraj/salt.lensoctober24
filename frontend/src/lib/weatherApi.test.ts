import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  getWeatherRecord,
  ingestForecast,
  listWeather,
  listWeatherProviders,
  recordWeather,
} from "@/lib/weatherApi";
import type {
  WeatherIngestResponse,
  WeatherObservation,
  WeatherObservationListResponse,
  WeatherProvidersResponse,
  WeatherRegistration,
} from "@/lib/weather";

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

const EMPTY_PAGE: WeatherObservationListResponse = {
  items: [],
  total: 0,
  limit: 200,
  offset: 0,
  earliest_timestamp: null,
  latest_timestamp: null,
};

const NO_PROVIDERS: WeatherProvidersResponse = {
  registered: [],
  count: 0,
  manual_entry_available: true,
  detail: "No weather API is connected.",
};

const VALID_PAYLOAD: WeatherRegistration = {
  pan_id: "kzn-2026-a01",
  rainfall_mm: 12.4,
  rainfall_probability_percent: 70,
  ambient_temperature_c: 24.5,
  humidity_percent: null,
  source: "manual",
};

function stored(overrides: Partial<WeatherObservation> = {}): WeatherObservation {
  return {
    weather_id: "wx-1",
    pan_id: "kzn-2026-a01",
    timestamp: "2026-03-14T06:30:00Z",
    forecast_for: null,
    rainfall_mm: 12.4,
    rainfall_probability_percent: 70,
    ambient_temperature_c: 24.5,
    humidity_percent: null,
    source: "manual",
    is_forecast: false,
    created_at: "2026-03-14T06:31:00Z",
    ...overrides,
  };
}

describe("listWeather", () => {
  it("queries the collection endpoint", async () => {
    const mock = stubFetch(() => jsonResponse(EMPTY_PAGE));

    await listWeather();

    expect(new URL(callOf(mock).url).pathname).toBe("/api/weather/");
  });

  it("narrows to one pan and a window", async () => {
    const mock = stubFetch(() => jsonResponse(EMPTY_PAGE));

    await listWeather({
      pan_id: "kzn-2026-a01",
      start: "2026-03-01T00:00:00Z",
      end: "2026-03-31T00:00:00Z",
      limit: 50,
      offset: 10,
    });

    const url = new URL(callOf(mock).url);
    expect(url.searchParams.get("pan_id")).toBe("kzn-2026-a01");
    expect(url.searchParams.get("start")).toBe("2026-03-01T00:00:00Z");
    expect(url.searchParams.get("end")).toBe("2026-03-31T00:00:00Z");
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.get("offset")).toBe("10");
  });

  it("surfaces a server error as an ApiError", async () => {
    stubFetch(() => jsonResponse({ detail: "No registered pan." }, 404));
    const error = await captureApiError(listWeather({ pan_id: "nope" }));
    expect(error.status).toBe(404);
    expect(error.message).toContain("No registered pan");
  });

  it("explains an unreachable backend rather than failing silently", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("network down");
      }),
    );
    const error = await captureApiError(listWeather());
    expect(error.status).toBe(0);
    expect(error.message).toContain(BASE);
  });
});

describe("getWeatherRecord", () => {
  it("reads one record by its identifier", async () => {
    const mock = stubFetch(() => jsonResponse(stored()));

    await getWeatherRecord("wx-1");

    expect(new URL(callOf(mock).url).pathname).toBe("/api/weather/wx-1");
  });

  it("escapes an identifier that would otherwise change the path", async () => {
    const mock = stubFetch(() => jsonResponse(stored()));

    await getWeatherRecord("wx 1/2");

    expect(callOf(mock).url).toContain("wx%201%2F2");
  });
});

describe("recordWeather", () => {
  it("posts a hand-entered record", async () => {
    const mock = stubFetch(() => jsonResponse(stored(), 201));

    const result = await recordWeather(VALID_PAYLOAD);

    expect(callOf(mock).init.method).toBe("POST");
    expect(bodyOf(callOf(mock).init)).toEqual(VALID_PAYLOAD);
    expect(result.source).toBe("manual");
  });

  it("sends a blank value as null rather than dropping it", async () => {
    const mock = stubFetch(() => jsonResponse(stored(), 201));

    await recordWeather({ ...VALID_PAYLOAD, humidity_percent: null });

    expect(bodyOf(callOf(mock).init)).toHaveProperty("humidity_percent", null);
  });

  it("refuses to send an API provenance, so no request is made", async () => {
    const mock = stubFetch(() => jsonResponse(stored(), 201));

    const forged = { ...VALID_PAYLOAD, source: "weather_api" } as unknown as
      WeatherRegistration;
    const error = await captureApiError(recordWeather(forged));

    expect(error.message).toContain("cannot be labelled as a weather API");
    expect(mock).not.toHaveBeenCalled();
  });

  it("maps a 422 onto the offending form field", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          detail: [
            {
              loc: ["body", "humidity_percent"],
              msg: "Humidity must be between 0 and 100 %.",
            },
          ],
        },
        422,
      ),
    );

    const error = await captureApiError(
      recordWeather({ ...VALID_PAYLOAD, humidity_percent: 500 }),
    );
    expect(error.status).toBe(422);
    expect(error.fields.humidity_percent).toContain("between 0 and 100");
  });

  it("keeps the server's refusal of a weather_api source intact", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          detail: [
            {
              loc: ["body", "source"],
              msg: "source cannot be weather_api on this endpoint.",
            },
          ],
        },
        422,
      ),
    );

    const error = await captureApiError(recordWeather(VALID_PAYLOAD));
    expect(error.fields.source).toContain("weather_api");
  });
});

describe("listWeatherProviders", () => {
  it("reads the connected sources", async () => {
    const mock = stubFetch(() => jsonResponse(NO_PROVIDERS));

    const result = await listWeatherProviders();

    expect(new URL(callOf(mock).url).pathname).toBe("/api/weather/providers");
    expect(result.count).toBe(0);
    expect(result.manual_entry_available).toBe(true);
  });

  it("reports an empty registry as such rather than as a failure", async () => {
    stubFetch(() =>
      jsonResponse({
        registered: [{ name: "metoffice", source: "weather_api" }],
        count: 1,
        manual_entry_available: true,
        detail: "1 weather provider registered.",
      }),
    );

    const result = await listWeatherProviders();
    expect(result.registered.map((provider) => provider.name)).toEqual([
      "metoffice",
    ]);
  });
});

describe("ingestForecast", () => {
  const INGESTED: WeatherIngestResponse = {
    provider: "metoffice",
    source: "weather_api",
    pan_id: "kzn-2026-a01",
    requested_start: "2026-03-14T12:00:00Z",
    requested_end: "2026-03-16T12:00:00Z",
    stored: 1,
    rejected: 0,
    observations: [stored({ source: "weather_api", is_forecast: true })],
  };

  it("asks a named provider for a forecast", async () => {
    const mock = stubFetch(() => jsonResponse(INGESTED, 201));

    const result = await ingestForecast("metoffice", {
      pan_id: "kzn-2026-a01",
    });

    const url = new URL(callOf(mock).url);
    expect(url.pathname).toBe("/api/weather/ingest/metoffice");
    expect(url.searchParams.get("pan_id")).toBe("kzn-2026-a01");
    expect(callOf(mock).init.method).toBe("POST");
    expect(result.observations[0]?.source).toBe("weather_api");
  });

  it("lets the server choose an omitted window", async () => {
    const mock = stubFetch(() => jsonResponse(INGESTED, 201));

    await ingestForecast("metoffice", { pan_id: "kzn-2026-a01" });

    const url = new URL(callOf(mock).url);
    expect(url.searchParams.has("start")).toBe(false);
    expect(url.searchParams.has("end")).toBe(false);
  });

  it("reports an unregistered provider as a 404 with no records claimed", async () => {
    stubFetch(() =>
      jsonResponse(
        { detail: "No weather provider named “openweather” is registered." },
        404,
      ),
    );

    const error = await captureApiError(
      ingestForecast("openweather", { pan_id: "kzn-2026-a01" }),
    );
    expect(error.status).toBe(404);
    expect(error.message).toContain("openweather");
  });

  it("reports an unavailable provider as a 503", async () => {
    stubFetch(() =>
      jsonResponse(
        { detail: "Weather provider “metoffice” is registered but unavailable." },
        503,
      ),
    );

    const error = await captureApiError(
      ingestForecast("metoffice", { pan_id: "kzn-2026-a01" }),
    );
    expect(error.status).toBe(503);
  });

  it("reports a provider fault as a 502 rather than as missing data", async () => {
    stubFetch(() =>
      jsonResponse({ detail: "upstream timeout" }, 502),
    );

    const error = await captureApiError(
      ingestForecast("metoffice", { pan_id: "kzn-2026-a01" }),
    );
    expect(error.status).toBe(502);
    expect(error.message).toContain("upstream timeout");
  });

  it("surfaces how many points could not be stored", async () => {
    stubFetch(() => jsonResponse({ ...INGESTED, stored: 3, rejected: 1 }, 201));

    const result = await ingestForecast("metoffice", {
      pan_id: "kzn-2026-a01",
    });
    expect(result.stored).toBe(3);
    expect(result.rejected).toBe(1);
  });
});
