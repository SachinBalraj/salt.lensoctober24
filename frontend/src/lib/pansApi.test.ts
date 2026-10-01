import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  deactivatePan,
  getPan,
  listPans,
  registerPan,
  updatePan,
} from "@/lib/pansApi";
import type { SaltPanListResponse } from "@/lib/pans";

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
type FetchMock = ReturnType<typeof vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>>;

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

describe("listPans", () => {
  it("defaults to the active lifecycle filter", async () => {
    const body: SaltPanListResponse = {
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
      total_active: 0,
      total_inactive: 0,
    };
    const mock = stubFetch(() => jsonResponse(body));

    await listPans();

    const url = new URL(callOf(mock).url);
    expect(url.pathname).toBe("/api/pans/");
    expect(url.searchParams.get("pan_status")).toBe("active");
  });

  it("passes through the status filter, search and paging", async () => {
    const mock = stubFetch(() =>
      jsonResponse({
        items: [],
        total: 0,
        limit: 25,
        offset: 50,
        total_active: 0,
        total_inactive: 0,
      }),
    );

    await listPans({ pan_status: "all", search: "  durban  ", limit: 25, offset: 50 });

    const url = new URL(callOf(mock).url);
    expect(url.searchParams.get("pan_status")).toBe("all");
    expect(url.searchParams.get("search")).toBe("durban");
    expect(url.searchParams.get("limit")).toBe("25");
    expect(url.searchParams.get("offset")).toBe("50");
  });

  it("omits an empty search rather than sending a blank filter", async () => {
    const mock = stubFetch(() =>
      jsonResponse({
        items: [],
        total: 0,
        limit: 50,
        offset: 0,
        total_active: 0,
        total_inactive: 0,
      }),
    );

    await listPans({ search: "   " });

    const url = new URL(callOf(mock).url);
    expect(url.searchParams.has("search")).toBe(false);
  });
});

describe("registerPan", () => {
  it("posts the payload to the collection endpoint", async () => {
    const mock = stubFetch(() => jsonResponse({ pan_id: "pans-2026-a01" }, 201));

    await registerPan({
      pan_id: "pans-2026-a01",
      pan_name: "Northern pan",
      location: "Port Shepstone",
      latitude: null,
      longitude: null,
      area_m2: 12500,
      normal_depth_cm: 15.5,
      target_harvest_salinity_g_l: 285,
    });

    const { url, init } = callOf(mock);
    expect(url).toBe(`${BASE}/api/pans/`);
    expect(init.method).toBe("POST");
    expect(bodyOf(init).pan_id).toBe("pans-2026-a01");
  });

  it("surfaces a duplicate identifier as a 409", async () => {
    stubFetch(() =>
      jsonResponse(
        { detail: "A document with the same natural key already exists.", error_code: "duplicate_document" },
        409,
      ),
    );

    const error = await captureApiError(
      registerPan({
        pan_id: "pans-2026-a01",
        pan_name: "Northern pan",
        location: "Port Shepstone",
        latitude: null,
        longitude: null,
        area_m2: 12500,
        normal_depth_cm: 15.5,
        target_harvest_salinity_g_l: 285,
      }),
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(409);
    expect(error.code).toBe("duplicate_document");
    expect(error.isConnectivity).toBe(false);
  });
});

describe("updatePan", () => {
  it("patches the pan resource and never sends a pan ID", async () => {
    const mock = stubFetch(() => jsonResponse({ pan_id: "pans-2026-a01" }));

    await updatePan("pans-2026-a01", { pan_name: "Renamed pan" });

    const { url, init } = callOf(mock);
    expect(url).toBe(`${BASE}/api/pans/pans-2026-a01`);
    expect(init.method).toBe("PATCH");
    expect(bodyOf(init)).not.toHaveProperty("pan_id");
  });
});

describe("deactivatePan", () => {
  it("posts to the deactivation action", async () => {
    const mock = stubFetch(() => jsonResponse({ pan_id: "pans-2026-a01" }));

    await deactivatePan("pans-2026-a01");

    const { url, init } = callOf(mock);
    expect(url).toBe(`${BASE}/api/pans/pans-2026-a01/deactivate`);
    expect(init.method).toBe("POST");
  });
});

describe("getPan", () => {
  it("encodes the identifier so odd characters cannot break the path", async () => {
    const mock = stubFetch(() => jsonResponse({ pan_id: "a/b" }));

    await getPan("a/b c");

    expect(callOf(mock).url).toBe(`${BASE}/api/pans/a%2Fb%20c`);
  });
});

describe("validation errors are mapped onto form fields", () => {
  it("extracts one message per field from a 422 body", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          detail: [
            { loc: ["body", "area_m2"], msg: "Input should be greater than 0", type: "greater_than" },
            { loc: ["body", "normal_depth_cm"], msg: "Input should be greater than 0", type: "greater_than" },
          ],
        },
        422,
      ),
    );

    const error = await captureApiError(
      updatePan("pans-2026-a01", { area_m2: -1 }),
    );

    expect(error).toBeInstanceOf(ApiError);
    expect(error.fields.area_m2).toBe("Input should be greater than 0");
    expect(error.fields.normal_depth_cm).toBe("Input should be greater than 0");
    expect(error.fields).not.toHaveProperty("body");
  });

  it("renames the persistence field name onto the operator-facing one", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          detail: [
            {
              loc: ["body", "location_name"],
              msg: "String should have at least 1 character",
              type: "string_too_short",
            },
          ],
        },
        422,
      ),
    );

    const error = await captureApiError(updatePan("pans-2026-a01", {}));

    expect(error.fields.location).toBe("String should have at least 1 character");
  });

  it("handles a cross-field model validator error that names no field", async () => {
    stubFetch(() =>
      jsonResponse(
        {
          detail: [
            {
              loc: ["body"],
              msg: "Value error, latitude and longitude must be provided together",
              type: "value_error",
            },
          ],
        },
        422,
      ),
    );

    const error = await captureApiError(
      updatePan("pans-2026-a01", { latitude: -30 }),
    );

    expect(error.fields).toEqual({});
    expect(error.message).toBe(
      "Value error, latitude and longitude must be provided together",
    );
  });
});

describe("connectivity failures", () => {
  it("reports an unreachable API as a connectivity error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );

    const error = await captureApiError(listPans());

    expect(error).toBeInstanceOf(ApiError);
    expect(error.isConnectivity).toBe(true);
    expect(error.message).toContain(BASE);
  });

  it("treats a 503 from an unconfigured database as a connectivity error", async () => {
    stubFetch(() =>
      jsonResponse({ detail: "Database not configured. Set MONGODB_URI." }, 503),
    );

    const error = await captureApiError(listPans());

    expect(error.status).toBe(503);
    expect(error.isConnectivity).toBe(true);
    expect(error.message).toContain("MONGODB_URI");
  });

  it("does not treat a 404 as a connectivity error", async () => {
    stubFetch(() => jsonResponse({ detail: "not found", error_code: "document_not_found" }, 404));

    const error = await captureApiError(getPan("missing"));

    expect(error.status).toBe(404);
    expect(error.isConnectivity).toBe(false);
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

    const error = await captureApiError(listPans());

    expect(error.status).toBe(502);
    expect(error.message).toBe("Request failed with status 502.");
  });
});
