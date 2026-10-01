import { describe, expect, it } from "vitest";
import {
  CHARTED_WEATHER_METRICS,
  DEMONSTRATION_FORECAST_LEAD_HOURS,
  DEMONSTRATION_WEATHER,
  EMPTY_WEATHER_FORM,
  MANUALLY_SELECTABLE_SOURCES,
  MAX_FORECAST_HORIZON_DAYS,
  WEATHER_RANGES,
  WEATHER_SOURCE_LABELS,
  buildWeatherChartSeries,
  demonstrationWeatherSeriesWarning,
  describeWeatherSource,
  emptyWeatherForm,
  explainWeatherSource,
  formatFullTimestamp,
  isAutomatedSource,
  isDemonstrationMode,
  needsTwoWeatherRecords,
  resetWeatherForm,
  validateForecastFor,
  validateWeather,
  validateWeatherMeasurement,
  validateWeatherTimestamp,
  type WeatherFormValues,
  type WeatherObservation,
} from "@/lib/weather";

/** Builds a `datetime-local` string for the operator's own wall clock. */
function localDateTimeValue(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function daysAgo(days: number, from = new Date()): Date {
  return new Date(from.getTime() - days * 86_400_000);
}

function form(overrides: Partial<WeatherFormValues> = {}): WeatherFormValues {
  return { ...EMPTY_WEATHER_FORM, ...overrides };
}

function record(overrides: Partial<WeatherObservation> = {}): WeatherObservation {
  return {
    weather_id: "wx-1",
    pan_id: "kzn-2026-a01",
    timestamp: "2026-03-14T06:30:00Z",
    forecast_for: null,
    rainfall_mm: 12.4,
    rainfall_probability_percent: 70,
    ambient_temperature_c: 24.5,
    humidity_percent: 82,
    source: "manual",
    is_forecast: false,
    created_at: "2026-03-14T06:31:00Z",
    ...overrides,
  };
}

describe("weather source vocabulary", () => {
  it("names every source an operator, a service or a demonstration can produce", () => {
    // Asserted exhaustively, because a source added to the enum and missed
    // here would render as its raw stored value beside a record.
    expect(Object.keys(WEATHER_SOURCE_LABELS).sort()).toEqual([
      "demonstration",
      "manual",
      "sensor",
      "weather_api",
    ]);
  });

  it("does not file a demonstration as a hand-entered forecast", () => {
    // `manual` means a person entered a forecast somebody else published. A
    // demonstration is a rainfall figure nobody published, and collapsing the two
    // would put a fabricated rain in the history as evidence.
    expect(WEATHER_SOURCE_LABELS.demonstration).not.toBe(
      WEATHER_SOURCE_LABELS.manual,
    );
    expect(explainWeatherSource("demonstration")).toContain("not a measurement");
  });

  it("labels a hand-entered record in plain language", () => {
    expect(describeWeatherSource("manual")).toBe("Entered by hand");
    expect(explainWeatherSource("manual")).toContain("operator");
  });

  it("distinguishes an API record from a typed one", () => {
    expect(describeWeatherSource("weather_api")).toBe("Weather API");
    expect(isAutomatedSource("weather_api")).toBe(true);
    expect(isAutomatedSource("manual")).toBe(false);
    expect(isAutomatedSource("sensor")).toBe(true);
  });

  it("falls back to the raw value for an unrecognised source", () => {
    expect(describeWeatherSource("mystery")).toBe("mystery");
    expect(explainWeatherSource("mystery")).toContain("mystery");
  });
});

describe("validateWeatherMeasurement", () => {
  it("treats a blank input as not measured rather than zero", () => {
    expect(validateWeatherMeasurement("", "rainfall_mm")).toEqual({
      value: null,
      error: null,
    });
    expect(validateWeatherMeasurement("   ", "rainfall_mm")).toEqual({
      value: null,
      error: null,
    });
  });

  it("keeps a real zero distinct from a blank", () => {
    expect(validateWeatherMeasurement("0", "rainfall_mm").value).toBe(0);
    expect(validateWeatherMeasurement("0", "rainfall_mm").error).toBeNull();
  });

  it("rejects text that is not a number", () => {
    const result = validateWeatherMeasurement("heavy", "rainfall_mm");
    expect(result.value).toBeNull();
    expect(result.error).toContain("must be a number");
  });

  it("accepts the boundaries of each range", () => {
    for (const [key, range] of Object.entries(WEATHER_RANGES)) {
      const typed = key as keyof typeof WEATHER_RANGES;
      expect(validateWeatherMeasurement(String(range.min), typed).error).toBeNull();
      expect(validateWeatherMeasurement(String(range.max), typed).error).toBeNull();
    }
  });

  it("rejects values just outside each range", () => {
    expect(
      validateWeatherMeasurement("-0.1", "rainfall_mm").error,
    ).toContain("between");
    expect(
      validateWeatherMeasurement("1000.1", "rainfall_mm").error,
    ).toContain("between");
    expect(
      validateWeatherMeasurement("101", "humidity_percent").error,
    ).toContain("between");
    expect(
      validateWeatherMeasurement("-51", "ambient_temperature_c").error,
    ).toContain("between");
  });
});

describe("validateWeatherTimestamp", () => {
  const now = new Date("2026-03-14T12:00:00Z");

  it("returns null for a blank so the server stamps the moment of saving", () => {
    expect(validateWeatherTimestamp("", { now })).toEqual({
      value: null,
      error: null,
    });
  });

  it("reads a datetime-local value as local wall clock", () => {
    const local = new Date(2026, 2, 14, 6, 30);
    const result = validateWeatherTimestamp(localDateTimeValue(local), { now });
    expect(result.error).toBeNull();
    expect(result.value).toBe(local.toISOString());
  });

  it("preserves a backdated instant exactly", () => {
    const past = daysAgo(9, now);
    const result = validateWeatherTimestamp(localDateTimeValue(past), { now });
    expect(result.value).toBe(past.toISOString());
  });

  it("refuses an instant that has not happened yet", () => {
    const future = new Date(now.getTime() + 48 * 3_600_000);
    const result = validateWeatherTimestamp(localDateTimeValue(future), { now });
    expect(result.value).toBeNull();
    expect(result.error).toContain("future");
  });

  it("tolerates ordinary client clock skew", () => {
    const slightly = new Date(now.getTime() + 60_000);
    const result = validateWeatherTimestamp(localDateTimeValue(slightly), { now });
    expect(result.error).toBeNull();
  });

  it("reports an unreadable value rather than guessing one", () => {
    const result = validateWeatherTimestamp("not-a-date", { now });
    expect(result.value).toBeNull();
    expect(result.error).toContain("could not be read");
  });
});

describe("validateForecastFor", () => {
  const now = new Date("2026-03-14T12:00:00Z");

  it("returns null when blank, meaning this record is an observation", () => {
    expect(validateForecastFor("", { observedAt: null, now })).toEqual({
      value: null,
      error: null,
    });
  });

  it("accepts a forecast aimed at a later instant", () => {
    const target = new Date(now.getTime() + 12 * 3_600_000);
    const result = validateForecastFor(localDateTimeValue(target), {
      observedAt: null,
      now,
    });
    expect(result.error).toBeNull();
    expect(result.value).toBe(target.toISOString());
  });

  it("refuses a forecast aimed before the moment it was made", () => {
    const result = validateForecastFor(
      localDateTimeValue(new Date(now.getTime() - 6 * 3_600_000)),
      { observedAt: null, now },
    );
    expect(result.value).toBeNull();
    expect(result.error).toContain("earlier");
  });

  it("catches a mistyped year", () => {
    const far = new Date(now.getTime() + 400 * 86_400_000);
    const result = validateForecastFor(localDateTimeValue(far), {
      observedAt: null,
      now,
    });
    expect(result.value).toBeNull();
    expect(result.error).toContain(String(MAX_FORECAST_HORIZON_DAYS));
  });
});

describe("validateWeather", () => {
  const now = new Date("2026-03-14T12:00:00Z");

  it("builds a payload from a hand-entered record", () => {
    const result = validateWeather(
      form({
        timestamp: localDateTimeValue(daysAgo(1, now)),
        rainfall_mm: "12.4",
        ambient_temperature_c: "24.5",
      }),
      { panId: "kzn-2026-a01", now },
    );

    expect(result.errors).toEqual({});
    expect(result.payload).toEqual({
      pan_id: "kzn-2026-a01",
      rainfall_mm: 12.4,
      rainfall_probability_percent: null,
      ambient_temperature_c: 24.5,
      humidity_percent: null,
      source: "manual",
      forecast_for: null,
      timestamp: daysAgo(1, now).toISOString(),
    });
  });

  it("omits the timestamp when blank so the server decides the instant", () => {
    const result = validateWeather(form({ rainfall_mm: "4" }), {
      panId: "kzn-2026-a01",
      now,
    });
    expect(result.payload).not.toHaveProperty("timestamp");
  });

  it("refuses a record with nothing in it", () => {
    const result = validateWeather(form(), { panId: "kzn-2026-a01", now });
    expect(result.payload).toBeNull();
    expect(result.errors.form).toContain("at least one");
  });

  it("accepts a single value", () => {
    const result = validateWeather(form({ rainfall_probability_percent: "80" }), {
      panId: "kzn-2026-a01",
      now,
    });
    expect(result.errors).toEqual({});
    expect(result.payload?.rainfall_probability_percent).toBe(80);
  });

  it("sends every blank as null rather than omitting or defaulting it", () => {
    const result = validateWeather(form({ rainfall_mm: "2" }), {
      panId: "kzn-2026-a01",
      now,
    });
    expect(result.payload?.humidity_percent).toBeNull();
    expect(result.payload?.rainfall_probability_percent).toBeNull();
    expect(result.payload?.ambient_temperature_c).toBeNull();
  });

  it("marks a record with a forecast time by carrying it through", () => {
    const result = validateWeather(
      form({
        forecast_for: localDateTimeValue(new Date(now.getTime() + 6 * 3_600_000)),
        rainfall_mm: "0",
      }),
      { panId: "kzn-2026-a01", now },
    );
    expect(result.errors).toEqual({});
    expect(result.payload?.forecast_for).not.toBeNull();
    expect(result.payload?.rainfall_mm).toBe(0);
  });

  it("collects every field error rather than stopping at the first", () => {
    const result = validateWeather(
      form({ rainfall_mm: "heavy", humidity_percent: "900" }),
      { panId: "kzn-2026-a01", now },
    );
    expect(result.payload).toBeNull();
    expect(result.errors.rainfall_mm).toBeTruthy();
    expect(result.errors.humidity_percent).toBeTruthy();
  });

  it("has no way to produce a weather_api payload from the form", () => {
    const result = validateWeather(form({ rainfall_mm: "5" }), {
      panId: "kzn-2026-a01",
      now,
    });
    expect(result.payload?.source).toBe("manual");
    expect(MANUALLY_SELECTABLE_SOURCES).not.toContain("weather_api");
    expect(WEATHER_SOURCE_LABELS).toHaveProperty("weather_api");
  });
});

describe("weather form lifecycle", () => {
  it("starts blank with hand entry as the provenance", () => {
    const values = emptyWeatherForm();
    expect(values).toEqual(EMPTY_WEATHER_FORM);
    expect(values.source).toBe("manual");
    expect(values.forecast_for).toBe("");
  });

  it("clears values after a save but keeps the chosen provenance", () => {
    const values = form({ source: "sensor", rainfall_mm: "9" });
    const reset = resetWeatherForm(values);
    expect(reset.rainfall_mm).toBe("");
    expect(reset.forecast_for).toBe("");
    expect(reset.source).toBe("sensor");
  });
});

describe("buildWeatherChartSeries", () => {
  it("orders records oldest first so time reads left to right", () => {
    const series = buildWeatherChartSeries(
      [
        record({ weather_id: "wx-3", timestamp: "2026-03-12T00:00:00Z" }),
        record({ weather_id: "wx-1", timestamp: "2026-03-10T00:00:00Z" }),
        record({ weather_id: "wx-2", timestamp: "2026-03-11T00:00:00Z" }),
      ],
      "rainfall_mm",
    );
    expect(series.points.map((point) => point.at)).toEqual([
      Date.parse("2026-03-10T00:00:00Z"),
      Date.parse("2026-03-11T00:00:00Z"),
      Date.parse("2026-03-12T00:00:00Z"),
    ]);
  });

  it("keeps a gap rather than bridging a value that was never recorded", () => {
    const series = buildWeatherChartSeries(
      [
        record({ weather_id: "wx-1", rainfall_mm: 10 }),
        record({ weather_id: "wx-2", rainfall_mm: null }),
        record({ weather_id: "wx-3", rainfall_mm: 30 }),
      ],
      "rainfall_mm",
    );
    expect(series.points.map((point) => point.value)).toEqual([10, null, 30]);
    expect(series.points[1]?.missing).toBe(true);
  });

  it("counts how many records actually carried the quantity", () => {
    const series = buildWeatherChartSeries(
      [
        record({ weather_id: "wx-1", humidity_percent: 50 }),
        record({ weather_id: "wx-2", humidity_percent: null }),
      ],
      "humidity_percent",
    );
    expect(series.measuredCount).toBe(1);
    expect(series.recordCount).toBe(2);
  });

  it("carries provenance onto every plotted point", () => {
    const series = buildWeatherChartSeries(
      [record({ source: "weather_api", is_forecast: true })],
      "rainfall_mm",
    );
    expect(series.points[0]?.source).toBe("weather_api");
    expect(series.points[0]?.is_forecast).toBe(true);
  });

  it("plots a real zero as a measurement, not a gap", () => {
    const series = buildWeatherChartSeries(
      [record({ rainfall_mm: 0 }), record({ rainfall_mm: 5 })],
      "rainfall_mm",
    );
    expect(series.points[0]?.value).toBe(0);
    expect(series.points[0]?.missing).toBe(false);
  });

  it("reports an empty series rather than inventing a domain", () => {
    const series = buildWeatherChartSeries([], "rainfall_mm");
    expect(series.points).toEqual([]);
    expect(series.measuredCount).toBe(0);
    expect(series.recordCount).toBe(0);
  });

  it("refuses to draw a trend from a single measurement", () => {
    expect(
      needsTwoWeatherRecords(buildWeatherChartSeries([record()], "rainfall_mm")),
    ).toBe(true);
    expect(
      needsTwoWeatherRecords(
        buildWeatherChartSeries(
          [record({ weather_id: "wx-1" }), record({ weather_id: "wx-2" })],
          "rainfall_mm",
        ),
      ),
    ).toBe(false);
  });
});

describe("weather charting surface", () => {
  it("charts the four quantities a researcher compares", () => {
    expect([...CHARTED_WEATHER_METRICS]).toEqual([
      "rainfall_mm",
      "rainfall_probability_percent",
      "ambient_temperature_c",
      "humidity_percent",
    ]);
  });

  it("labels every range with a unit so the axis is self-describing", () => {
    for (const range of Object.values(WEATHER_RANGES)) {
      expect(range.label.length).toBeGreaterThan(0);
      expect(range.unit.length).toBeGreaterThan(0);
    }
  });
});

describe("formatFullTimestamp", () => {
  it("renders an instant in UTC and marks an absent one", () => {
    expect(formatFullTimestamp("2026-03-14T06:30:00Z")).toContain("UTC");
    expect(formatFullTimestamp(null)).toBe("—");
    expect(formatFullTimestamp("nonsense")).toBe("—");
  });
});

describe("demonstration weather", () => {
  it("starts blank and real, because that is the common case", () => {
    const values = emptyWeatherForm();
    expect(isDemonstrationMode(values)).toBe(false);
    // A forecast form that opened on invented rainfall would put a fabricated
    // number in the field an operator reaches for first, and the simulation below
    // reads whatever is there.
    expect(values.rainfall_mm).toBe("");
  });

  it("is not offered as a hand-picked provenance", () => {
    // It is reached by the mode toggle, not the source menu, so it cannot be
    // chosen as though it were a place a number came from.
    expect(MANUALLY_SELECTABLE_SOURCES).not.toContain("demonstration");
  });

  it("seeds the fixed forecast, aimed ahead of the present", () => {
    const now = new Date("2026-03-14T06:00:00Z");
    const values = emptyWeatherForm({ mode: "demonstration", now });
    expect(isDemonstrationMode(values)).toBe(true);
    expect(values.source).toBe("demonstration");
    expect(values.rainfall_mm).toBe(String(DEMONSTRATION_WEATHER.rainfall_mm));
    // Six hours out, not now: a demonstration claiming to describe the present
    // would be indistinguishable from a real reading of it. `forecast_for` is a
    // `datetime-local` string in the operator's own wall clock, so the comparison
    // is made through the same helper the form uses to write it.
    expect(values.forecast_for).toBe(
      localDateTimeValue(new Date(now.getTime() + DEMONSTRATION_FORECAST_LEAD_HOURS * 3_600_000)),
    );
  });

  it("keeps a demonstration a demonstration through validation", () => {
    // The mode is derived from `source` rather than tracked beside it, so a form
    // cannot hold a demonstration alongside a real provenance. Validation
    // therefore has nothing to arbitrate — it carries the source through. The
    // invariant worth holding is in both directions: a demonstration is never
    // filed as a real reading, and a real one is never promoted to a
    // demonstration, because either would put invented rain into or out of the
    // research record on a technicality.
    const demo = validateWeather(emptyWeatherForm({ mode: "demonstration" }), {
      panId: "kzn-2026-a01",
    });
    expect(demo.errors).toEqual({});
    expect(demo.payload?.source).toBe("demonstration");

    const real = validateWeather(
      { ...emptyWeatherForm(), source: "manual", rainfall_mm: "12" },
      { panId: "kzn-2026-a01" },
    );
    expect(real.errors).toEqual({});
    expect(real.payload?.source).toBe("manual");
  });

  it("cannot be filed as a weather-API reading", () => {
    // The forecast-ingest route is the only thing allowed to set this, so a hand
    // filled form claiming it would attribute a number to a service that never
    // sent it. The form's own type already excludes the value; this is the
    // runtime backstop for the same rule, and the cast is how a test can reach a
    // state the compiler forbids.
    const result = validateWeather(
      {
        ...EMPTY_WEATHER_FORM,
        rainfall_mm: "12",
        source: "weather_api",
      } as unknown as WeatherFormValues,
      { panId: "kzn-2026-a01" },
    );
    expect(result.payload).toBeNull();
    expect(result.errors.source).toMatch(/ingest/i);
  });

  it("will not be stored without a forecast to apply to", () => {
    // A demonstration aimed at nothing is not a demonstration of anything, and
    // the simulator cannot use rain with no instant attached.
    const result = validateWeather(
      { ...emptyWeatherForm({ mode: "demonstration" }), forecast_for: "" },
      { panId: "kzn-2026-a01" },
    );
    expect(result.errors.forecast_for).toBeTruthy();
  });

  it("describes itself as a demonstration in both its label and its sentence", () => {
    expect(describeWeatherSource("demonstration")).toBe("Demonstration");
    // "Manual entry" beside invented rain would be the failure these exist to
    // stop, and the sentence under a badge is what settles it.
    expect(explainWeatherSource("demonstration")).toMatch(/not|no |never/i);
    expect(isAutomatedSource("demonstration")).toBe(false);
  });

  it("re-seeds on reset with a fresh forecast instant", () => {
    const first = emptyWeatherForm({
      mode: "demonstration",
      now: new Date("2026-03-14T06:00:00Z"),
    });
    const now = new Date("2026-03-14T18:00:00Z");
    const later = resetWeatherForm(first, { now });
    expect(isDemonstrationMode(later)).toBe(true);
    // Six hours from the new now, not the old one: a demonstration aimed at a
    // moment that has passed is a different demonstration, and the simulator reads
    // whichever instant is on the record.
    expect(later.forecast_for).not.toBe(first.forecast_for);
    expect(later.forecast_for).toBe(
      localDateTimeValue(new Date(now.getTime() + DEMONSTRATION_FORECAST_LEAD_HOURS * 3_600_000)),
    );
  });

  it("marks its points and counts them on the chart", () => {
    const series = buildWeatherChartSeries(
      [
        record({ timestamp: "2026-03-14T06:00:00Z", source: "manual" }),
        record({ timestamp: "2026-03-14T12:00:00Z", source: "demonstration" }),
      ],
      "rainfall_mm",
    );
    expect(series.points.map((point) => point.demonstration)).toEqual([false, true]);
    expect(demonstrationWeatherSeriesWarning(series)).toContain("1 of 2");
  });
});
