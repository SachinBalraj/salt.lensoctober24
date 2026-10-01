/**
 * Field-validation API client tests.
 *
 * The client exists to file a reported event or read a comparison, so the paths
 * and one POST body are the contract. The POST body is worth locking down
 * because the payload the client sends is what a report is made of: pre/post
 * snapshots go in with a source of "manual", the actual rainfall travels with
 * its own timestamp, and an optional forecast is carried as context and must
 * never be the value the replay runs on. A missing intervention volume is sent
 * as null so the backend can flag it as unknown rather than invent one.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  addFieldValidationIntervention,
  createFieldValidationEvent,
  fieldValidationExportUrl,
  getFieldValidationStatus,
  listFieldValidationComparisons,
} from "@/lib/fieldValidationApi";

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

describe("reads", () => {
  it("reads the status endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ module: "field-validation" }));
    vi.stubGlobal("fetch", fetchMock);

    await getFieldValidationStatus();

    expect(String(fetchMock.mock.calls[0][0])).toBe(`${BASE}/api/field-validation/status`);
  });

  it("scopes a pan through the query string", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await listFieldValidationComparisons({ pan_id: "pan_orginal" });

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `${BASE}/api/field-validation/comparisons?pan_id=pan_orginal&limit=200`,
    );
  });

  it("reports an unreachable backend as connectivity", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const error = await captureApiError(getFieldValidationStatus());
    expect(error.status).toBe(0);
  });
});

describe("createFieldValidationEvent", () => {
  it("posts the event under the collection path", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ event_id: "ev-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await createFieldValidationEvent({
      pan_id: "pan_orginal",
      name: "North Pan rainfall event",
      event_timestamp: "2026-03-14T06:00:00Z",
      event_milestone: "unknown",
      reported_by: "A. Operator",
      actual_rainfall_mm: 90,
      rainfall_timestamp: "2026-03-14T06:00:00Z",
      rainfall_source: "gauge",
      forecast_rainfall_mm: 70,
      pre_salinity_g_l: 250,
      pre_depth_cm: 20,
      post_salinity_g_l: 20,
      post_depth_cm: 50,
      interventions: [
        {
          kind: "brine_transfer_out",
          direction: "out",
          performed_at: "2026-03-14T09:00:00Z",
          volume_m3: null,
          description: "Drew brine to the reserve pump.",
        },
      ],
      notes: "Reported by the field crew.",
    });

    expect(String(fetchMock.mock.calls[0][0])).toBe(`${BASE}/api/field-validation/events`);
    const method = fetchMock.mock.calls[0][1]?.method;
    const sent = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(method).toBe("POST");
    expect(sent.pan_id).toBe("pan_orginal");
    expect(sent.pre_event).toEqual({ salinity_g_l: 250, water_depth_cm: 20, source: "manual" });
    expect(sent.post_event).toEqual({ salinity_g_l: 20, water_depth_cm: 50, source: "manual" });
    expect(sent.actual_rainfall_mm).toBe(90);
    expect(sent.forecast_rainfall_mm).toBe(70);
    expect(sent.interventions[0].volume_m3).toBeNull();
  });

  it("surfaces a field error under the nested field name", async () => {
    const detail = [
      {
        loc: ["body", "pre_event", "salinity_g_l"],
        msg: "Input should be less than or equal to 999",
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail }, 422)),
    );

    const error = await captureApiError(
      createFieldValidationEvent({
        pan_id: "pan_orginal",
        name: "",
        event_timestamp: "2026-03-14T06:00:00Z",
      event_milestone: "unknown",
        reported_by: "",
        actual_rainfall_mm: 90,
        rainfall_timestamp: "2026-03-14T06:00:00Z",
        rainfall_source: "",
        forecast_rainfall_mm: null,
        pre_salinity_g_l: 1000,
        pre_depth_cm: 20,
        post_salinity_g_l: 20,
        post_depth_cm: 50,
        interventions: [],
        notes: "",
      }),
    );

    expect(error.fields["pre_event.salinity_g_l"]).toBe(
      "Input should be less than or equal to 999",
    );
  });
});

describe("addFieldValidationIntervention", () => {
  it("appends to the event's interventions path", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ event_id: "ev-1" }));
    vi.stubGlobal("fetch", fetchMock);

    await addFieldValidationIntervention({
      event_id: "ev-1",
      kind: "harvest",
      direction: "none",
      performed_at: "2026-03-15T08:00:00Z",
      volume_m3: null,
      description: "Crop lifted.",
    });

    expect(String(fetchMock.mock.calls[0][0])).toBe(
      `${BASE}/api/field-validation/events/ev-1/interventions`,
    );
    const sent = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(sent.kind).toBe("harvest");
    expect(sent.direction).toBe("none");
    expect(sent.volume_m3).toBeNull();
  });
});

describe("fieldValidationExportUrl", () => {
  it("builds the CSV and PDF export URLs", () => {
    expect(fieldValidationExportUrl("csv")).toBe(
      `${BASE}/api/field-validation/export?format=csv`,
    );
    expect(fieldValidationExportUrl("pdf", "pan_orginal")).toBe(
      `${BASE}/api/field-validation/export?format=pdf&pan_id=pan_orginal`,
    );
  });
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