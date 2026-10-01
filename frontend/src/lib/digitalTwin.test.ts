import { describe, expect, it } from "vitest";
import {
  FRESHNESS,
  OBSERVATION_SOURCE_LABELS,
  TWIN_DISPLAY_QUANTITIES,
  TWIN_INPUT_LABELS,
  TWIN_WEATHER_SOURCE_LABELS,
  buildQuantityViews,
  describeFreshness,
  describeTwinSource,
  formatAge,
  formatQuantity,
  formatTwinTimestamp,
  isTwinCurrent,
  oldestInputAge,
  summarizeAvailability,
  type DigitalTwinState,
  type MissingTwinInput,
} from "@/lib/digitalTwin";

function missing(field: string, expected_from: string): MissingTwinInput {
  return {
    field,
    reason: `No ${expected_from} has been recorded, so ${field} is unavailable.`,
    expected_from,
  };
}

function makeTwin(overrides: Partial<DigitalTwinState> = {}): DigitalTwinState {
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
    weather: {
      id: "wx-1",
      timestamp: "2026-03-14T11:30:00Z",
      source: "manual",
      age_seconds: 1800,
    },
    weather_forecast: {
      id: "wx-2",
      timestamp: "2026-03-14T10:00:00Z",
      source: "manual",
      age_seconds: 7200,
    },
    is_complete: true,
    missing_inputs: [],
    missing_fields: [],
    quantities: [...TWIN_DISPLAY_QUANTITIES],
    ...overrides,
  };
}

// -- the twin reports rather than fills ----------------------------------

describe("a quantity the twin could not source", () => {
  it("is reported as unavailable rather than as a number", () => {
    const views = buildQuantityViews(
      makeTwin({
        water_depth_cm: null,
        is_complete: false,
        missing_fields: ["water_depth_cm"],
        missing_inputs: [missing("water_depth_cm", "observation")],
      }),
    );
    const depth = views.find((view) => view.field === "water_depth_cm");

    expect(depth?.available).toBe(false);
    expect(depth?.display).toBeNull();
    expect(depth?.reason).toContain("No observation has been recorded");
  });

  it("keeps a measured zero distinct from a missing figure", () => {
    const zeroed = buildQuantityViews(makeTwin({ salinity_g_l: 0 }));
    const absent = buildQuantityViews(
      makeTwin({
        salinity_g_l: null,
        is_complete: false,
        missing_fields: ["salinity_g_l"],
        missing_inputs: [missing("salinity_g_l", "observation")],
      }),
    );

    expect(zeroed.find((v) => v.field === "salinity_g_l")?.available).toBe(true);
    expect(zeroed.find((v) => v.field === "salinity_g_l")?.display).toBe("0.0");
    expect(absent.find((v) => v.field === "salinity_g_l")?.available).toBe(false);
    expect(absent.find((v) => v.field === "salinity_g_l")?.display).toBeNull();
  });

  it("names every quantity it attempts to supply", () => {
    expect(buildQuantityViews(makeTwin())).toHaveLength(
      TWIN_DISPLAY_QUANTITIES.length,
    );
  });

  it("says which record supplies each quantity", () => {
    const views = buildQuantityViews(makeTwin());

    expect(views.find((v) => v.field === "salinity_g_l")?.from).toBe("observation");
    expect(views.find((v) => v.field === "ambient_temperature_c")?.from).toBe(
      "weather",
    );
    expect(views.find((v) => v.field === "forecast_rainfall_mm")?.from).toBe(
      "weather_forecast",
    );
  });

  it("falls back to a plain reason when the server sent none", () => {
    const views = buildQuantityViews(
      makeTwin({ salinity_g_l: null, missing_inputs: [], missing_fields: [] }),
    );

    expect(views.find((v) => v.field === "salinity_g_l")?.reason).toBe(
      "Not available.",
    );
  });
});

describe("summarizing how far the twin can be trusted", () => {
  it("leads with the first gap rather than a count", () => {
    const summary = summarizeAvailability(
      makeTwin({
        is_complete: false,
        missing_fields: ["salinity_g_l", "water_depth_cm"],
        missing_inputs: [
          missing("salinity_g_l", "observation"),
          missing("water_depth_cm", "observation"),
        ],
      }),
    );

    expect(summary.headline).toContain("observation");
    expect(summary.detail).toBe("1 further quantity is unavailable.");
  });

  it("reports a complete twin plainly", () => {
    const summary = summarizeAvailability(makeTwin());

    expect(summary.headline).toBe("Every quantity is sourced from a stored record.");
    expect(summary.detail).toBeNull();
  });

  it("uses the singular for a single remaining gap", () => {
    const summary = summarizeAvailability(
      makeTwin({
        is_complete: false,
        missing_fields: ["salinity_g_l"],
        missing_inputs: [missing("salinity_g_l", "observation")],
      }),
    );

    expect(summary.detail).toBeNull();
  });
});

// -- ages are evidence ---------------------------------------------------

describe("formatting an age", () => {
  it("reads as just now for the last minute", () => {
    expect(formatAge(0)).toBe("just now");
    expect(formatAge(45)).toBe("just now");
  });

  it("counts whole minutes", () => {
    expect(formatAge(60)).toBe("1 minute ago");
    expect(formatAge(600)).toBe("10 minutes ago");
  });

  it("counts whole hours", () => {
    expect(formatAge(3600)).toBe("1 hour ago");
    expect(formatAge(7200)).toBe("2 hours ago");
  });

  it("counts whole days", () => {
    expect(formatAge(86_400)).toBe("1 day ago");
    expect(formatAge(172_800)).toBe("2 days ago");
  });

  it("reports an absent age as unknown rather than as zero", () => {
    expect(formatAge(null)).toBe("—");
  });

  it("refuses to render a negative age from clock skew", () => {
    expect(formatAge(-30)).toBe("—");
  });

  it("reports a non-finite age as unknown", () => {
    expect(formatAge(Number.NaN)).toBe("—");
  });
});

describe("grading how current the evidence is", () => {
  it("calls a reading from today current", () => {
    expect(describeFreshness(3600).tone).toBe("active");
    expect(describeFreshness(3600).label).toBe("Current");
  });

  it("calls a reading from this week ageing", () => {
    expect(describeFreshness(3 * 86_400).tone).toBe("caution");
    expect(describeFreshness(3 * 86_400).label).toBe("Ageing");
  });

  it("calls a reading from last month stale", () => {
    expect(describeFreshness(30 * 86_400).tone).toBe("danger");
    expect(describeFreshness(30 * 86_400).label).toBe("Stale");
  });

  it("reports a missing reading as a missing reading, not as fresh", () => {
    expect(describeFreshness(null)).toEqual({
      tone: "danger",
      label: "No reading",
    });
  });

  it("changes tone exactly at each stated threshold", () => {
    expect(describeFreshness(FRESHNESS.fresh - 1).tone).toBe("active");
    expect(describeFreshness(FRESHNESS.fresh).tone).toBe("caution");
    expect(describeFreshness(FRESHNESS.ageing).tone).toBe("danger");
  });
});

describe("the currency of a twin", () => {
  it("is limited by its oldest input", () => {
    expect(
      oldestInputAge(
        makeTwin({
          observation: { id: "o", timestamp: null, source: null, age_seconds: 900 },
          weather: { id: "w", timestamp: null, source: null, age_seconds: 300 },
          weather_forecast: {
            id: "f",
            timestamp: null,
            source: null,
            age_seconds: 7200,
          },
        }),
      ),
    ).toBe(7200);
  });

  it("is unknown when no input reports an age", () => {
    expect(oldestInputAge(makeTwin({ observation: null, weather: null }))).toBe(
      7200,
    );
    expect(
      oldestInputAge(makeTwin({ observation: null, weather: null, weather_forecast: null })),
    ).toBeNull();
  });

  it("is not current when any quantity is missing", () => {
    expect(
      isTwinCurrent(
        makeTwin({
          is_complete: false,
          missing_fields: ["salinity_g_l"],
          missing_inputs: [missing("salinity_g_l", "observation")],
        }),
      ),
    ).toBe(false);
  });

  it("is not current when the evidence is a week old, even if complete", () => {
    expect(
      isTwinCurrent(
        makeTwin({
          observation: {
            id: "o",
            timestamp: null,
            source: null,
            age_seconds: 30 * 86_400,
          },
        }),
      ),
    ).toBe(false);
  });

  it("is current when every quantity is present and the evidence is recent", () => {
    expect(isTwinCurrent(makeTwin())).toBe(true);
  });

  it("is not current when nothing at all has been recorded", () => {
    expect(
      isTwinCurrent(
        makeTwin({
          is_complete: false,
          observation: null,
          weather: null,
          weather_forecast: null,
          missing_fields: ["salinity_g_l"],
          missing_inputs: [missing("salinity_g_l", "observation")],
        }),
      ),
    ).toBe(false);
  });
});

// -- provenance ----------------------------------------------------------

describe("describing where an input came from", () => {
  it("labels a hand-entered observation", () => {
    expect(describeTwinSource("observation", "manual")).toBe("Entered by hand");
  });

  it("labels a weather API record", () => {
    expect(describeTwinSource("weather", "weather_api")).toBe("Weather API");
  });

  it("distinguishes a sensor record from a hand-entered one", () => {
    expect(describeTwinSource("weather_forecast", "sensor")).toBe("Sensor gateway");
  });

  it("falls back to the raw value for an unrecognised source", () => {
    expect(describeTwinSource("weather", "satellite")).toBe("satellite");
  });

  it("names the record when it carries no source", () => {
    expect(describeTwinSource("weather_forecast", null)).toBe(
      TWIN_INPUT_LABELS.weather_forecast,
    );
  });

  it("names all three sources for both record kinds", () => {
    expect(Object.keys(OBSERVATION_SOURCE_LABELS)).toEqual([
      "manual",
      "sensor",
      "import",
    ]);
    expect(Object.keys(TWIN_WEATHER_SOURCE_LABELS)).toEqual([
      "manual",
      "weather_api",
      "sensor",
    ]);
  });
});

// -- formatting ----------------------------------------------------------

describe("formatting twin values", () => {
  it("renders a figure with its stated precision", () => {
    expect(formatQuantity(285.44, 1)).toBe("285.4");
    expect(formatQuantity(64, 0)).toBe("64");
    expect(formatQuantity(0, 1)).toBe("0.0");
  });

  it("reports a non-finite figure as unknown", () => {
    expect(formatQuantity(Number.POSITIVE_INFINITY)).toBe("—");
  });

  it("renders an instant in UTC and marks an absent one", () => {
    expect(formatTwinTimestamp("2026-03-14T12:00:00Z")).toContain("UTC");
    expect(formatTwinTimestamp(null)).toBe("—");
  });

  it("reports an unreadable instant rather than guessing one", () => {
    expect(formatTwinTimestamp("not-a-date")).toBe("—");
  });
});
