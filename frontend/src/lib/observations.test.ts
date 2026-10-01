/**
 * Observation validation, form shaping and chart-series tests.
 *
 * The behaviours locked down here are the ones that keep the record honest:
 * a blank input stays blank, zero stays a real measurement, a backdated
 * timestamp is accepted, and the chart never bridges a gap in the data.
 */

import { describe, expect, it } from "vitest";
import {
  CHARTED_METRICS,
  MAX_NOTE_LENGTH,
  MEASUREMENT_RANGES,
  buildChartSeries,
  demonstrationSeriesWarning,
  describeSource,
  emptyObservationForm,
  isDemonstrationMode,
  needsTwoReadings,
  resetObservationForm,
  validateMeasurement,
  validateNote,
  validateObservation,
  validateObservationTimestamp,
  type DataSource,
  type ObservationFormValues,
  type PanObservation,
} from "@/lib/observations";
import {
  DEMONSTRATION_OBSERVATION,
  DEMONSTRATION_SOURCE,
  isDemonstrationNote,
} from "@/lib/demonstration";

const PAN_ID = "kzn-2026-a01";

function form(overrides: Partial<ObservationFormValues> = {}): ObservationFormValues {
  return { ...emptyObservationForm(), ...overrides };
}

function observation(
  overrides: Partial<PanObservation> = {},
): PanObservation {
  return {
    observation_id: `obs-${Math.random().toString(16).slice(2, 10)}`,
    pan_id: PAN_ID,
    timestamp: "2026-03-14T06:00:00Z",
    salinity_g_l: 280,
    water_depth_cm: 12.5,
    brine_temperature_c: 31,
    humidity_percent: 62,
    ambient_temperature_c: 27,
    data_source: "manual",
    note: null,
    created_at: "2026-03-14T06:00:05Z",
    ...overrides,
  };
}

// -- blank means unmeasured ---------------------------------------------

describe("blank measurements", () => {
  it("treats an empty field as not measured rather than invalid", () => {
    for (const key of Object.keys(MEASUREMENT_RANGES) as Array<
      keyof typeof MEASUREMENT_RANGES
    >) {
      expect(validateMeasurement("", key)).toEqual({ value: null, error: null });
      expect(validateMeasurement("   ", key)).toEqual({ value: null, error: null });
    }
  });

  it("sends unmeasured fields as null", () => {
    const { payload } = validateObservation(form({ salinity_g_l: "280" }), {
      panId: PAN_ID,
    });
    expect(payload).not.toBeNull();
    expect(payload?.salinity_g_l).toBe(280);
    expect(payload?.water_depth_cm).toBeNull();
    expect(payload?.brine_temperature_c).toBeNull();
    expect(payload?.humidity_percent).toBeNull();
    expect(payload?.ambient_temperature_c).toBeNull();
  });

  it("never defaults an unmeasured field to zero", () => {
    const { payload } = validateObservation(form({ salinity_g_l: "280" }), {
      panId: PAN_ID,
    });
    expect(payload?.water_depth_cm).toBeNull();
    expect(payload?.water_depth_cm).not.toBe(0);
  });

  it("refuses a reading with nothing measured", () => {
    const { payload, errors } = validateObservation(form({ note: "Nothing." }), {
      panId: PAN_ID,
    });
    expect(payload).toBeNull();
    expect(errors.form).toMatch(/at least one measured value/i);
  });

  it("refuses a completely empty reading", () => {
    const { payload, errors } = validateObservation(form(), { panId: PAN_ID });
    expect(payload).toBeNull();
    expect(errors.form).toBeTruthy();
  });
});

// -- zero is a measurement ------------------------------------------------

describe("zero as a real reading", () => {
  it("accepts zero depth, which is what a dry pan measures", () => {
    const result = validateMeasurement("0", "water_depth_cm");
    expect(result).toEqual({ value: 0, error: null });
  });

  it("accepts zero for the other quantities that can legitimately be zero", () => {
    expect(validateMeasurement("0", "salinity_g_l").value).toBe(0);
    expect(validateMeasurement("0", "humidity_percent").value).toBe(0);
  });

  it("records an explicit zero as a measurement rather than as a blank", () => {
    const { payload } = validateObservation(form({ water_depth_cm: "0" }), {
      panId: PAN_ID,
    });
    expect(payload).not.toBeNull();
    expect(payload?.water_depth_cm).toBe(0);
  });
});

// -- numeric validation ---------------------------------------------------

describe("measurement validation", () => {
  it("rejects values that are not numbers", () => {
    for (const raw of ["abc", "1,5", "1.2.3", "--3", "1e5", "NaN", "Infinity"]) {
      const result = validateMeasurement(raw, "salinity_g_l");
      expect(result.error, raw).toBeTruthy();
      expect(result.value).toBeNull();
    }
  });

  it("rejects values outside the plausibility range", () => {
    expect(validateMeasurement("-1", "salinity_g_l").error).toBeTruthy();
    expect(validateMeasurement("501", "salinity_g_l").error).toBeTruthy();
    expect(validateMeasurement("1001", "water_depth_cm").error).toBeTruthy();
    expect(validateMeasurement("-21", "brine_temperature_c").error).toBeTruthy();
    expect(validateMeasurement("101", "humidity_percent").error).toBeTruthy();
  });

  it("accepts the exact range boundaries", () => {
    expect(validateMeasurement("0", "salinity_g_l").value).toBe(0);
    expect(validateMeasurement("500", "salinity_g_l").value).toBe(500);
    expect(validateMeasurement("-20", "brine_temperature_c").value).toBe(-20);
    expect(validateMeasurement("120", "brine_temperature_c").value).toBe(120);
    expect(validateMeasurement("-50", "ambient_temperature_c").value).toBe(-50);
    expect(validateMeasurement("80", "ambient_temperature_c").value).toBe(80);
  });

  it("accepts negative temperatures, which are plausible in winter", () => {
    expect(validateMeasurement("-3.5", "brine_temperature_c").error).toBeNull();
    expect(validateMeasurement("-3.5", "brine_temperature_c").value).toBe(-3.5);
  });

  it("names the quantity and its bounds in the error", () => {
    expect(validateMeasurement("600", "salinity_g_l").error).toBe(
      "Salinity must be between 0 and 500 g/L.",
    );
  });

  it("reports every bad field at once rather than one at a time", () => {
    const { errors } = validateObservation(
      form({ salinity_g_l: "999", humidity_percent: "999" }),
      { panId: PAN_ID },
    );
    expect(errors.salinity_g_l).toBeTruthy();
    expect(errors.humidity_percent).toBeTruthy();
  });
});

// -- timestamps -----------------------------------------------------------

describe("observation timestamps", () => {
  // A `datetime-local` value carries no timezone, so it is read as the
  // operator's local wall clock. These tests build their instants locally too,
  // so they hold in any timezone rather than assuming UTC.
  const now = new Date(2026, 2, 14, 12, 0, 0);
  const isoOf = (y: number, m: number, d: number, h: number, min = 0) =>
    new Date(y, m, d, h, min, 0, 0).toISOString();

  it("treats a blank timestamp as record-now", () => {
    expect(validateObservationTimestamp("", { now })).toEqual({
      value: null,
      error: null,
    });
  });

  it("accepts a backdated reading", () => {
    const result = validateObservationTimestamp("2026-03-01T06:00", { now });
    expect(result.error).toBeNull();
    expect(result.value).toBe(isoOf(2026, 2, 1, 6, 0));
  });

  it("reads the entered value as local time rather than UTC", () => {
    // The stored instant must correspond to the wall clock the operator typed,
    // so a reading cannot silently shift by the browser's timezone offset.
    expect(validateObservationTimestamp("2026-03-01T06:00", { now }).value).toBe(
      isoOf(2026, 2, 1, 6, 0),
    );
  });

  it("rejects a reading dated in the future", () => {
    const result = validateObservationTimestamp("2026-03-15T06:00", { now });
    expect(result.error).toMatch(/future/i);
    expect(result.value).toBeNull();
  });

  it("tolerates a few minutes of client clock skew", () => {
    expect(
      validateObservationTimestamp("2026-03-14T12:03", { now }).error,
    ).toBeNull();
  });

  it("rejects a skew beyond the tolerance", () => {
    expect(
      validateObservationTimestamp("2026-03-14T12:30", { now }).error,
    ).toBeTruthy();
  });

  it("rejects an unreadable timestamp", () => {
    expect(validateObservationTimestamp("last tuesday", { now }).error).toBeTruthy();
  });

  it("omits the timestamp from the payload when left blank", () => {
    const { payload } = validateObservation(form({ salinity_g_l: "280" }), {
      panId: PAN_ID,
      now,
    });
    expect(payload).not.toHaveProperty("timestamp");
  });

  it("includes a supplied timestamp in the payload", () => {
    const { payload } = validateObservation(
      form({ salinity_g_l: "280", timestamp: "2026-03-01T06:00" }),
      { panId: PAN_ID, now },
    );
    expect(payload?.timestamp).toBe(isoOf(2026, 2, 1, 6, 0));
  });
});

// -- notes and provenance -------------------------------------------------

describe("notes and provenance", () => {
  it("treats a blank note as absent", () => {
    expect(validateNote("")).toEqual({ value: null, error: null });
  });

  it("trims a note", () => {
    expect(validateNote("  after rain  ").value).toBe("after rain");
  });

  it("accepts a note at the maximum length", () => {
    expect(validateNote("x".repeat(MAX_NOTE_LENGTH)).error).toBeNull();
  });

  it("rejects a note beyond the maximum length", () => {
    expect(validateNote("x".repeat(MAX_NOTE_LENGTH + 1)).error).toBeTruthy();
  });

  it("omits an absent note from the payload", () => {
    const { payload } = validateObservation(form({ salinity_g_l: "280" }), {
      panId: PAN_ID,
    });
    expect(payload).not.toHaveProperty("note");
  });

  it("defaults provenance to manual entry", () => {
    expect(emptyObservationForm().data_source).toBe("manual");
  });

  it("allows an import provenance", () => {
    const { payload } = validateObservation(
      form({ salinity_g_l: "280", data_source: "import" }),
      { panId: PAN_ID },
    );
    expect(payload?.data_source).toBe("import");
  });

  it("describes every provenance in words", () => {
    expect(describeSource("manual")).toBe("Manual entry");
    expect(describeSource("import")).toBe("Imported dataset");
    expect(describeSource("sensor")).toBe("Sensor gateway");
  });
});

// -- form reset -----------------------------------------------------------

describe("form reset", () => {
  it("clears the measurements after a save", () => {
    const dirty = form({
      salinity_g_l: "280",
      water_depth_cm: "12",
      note: "after rain",
    });
    const clean = resetObservationForm(dirty);
    expect(clean.salinity_g_l).toBe("");
    expect(clean.water_depth_cm).toBe("");
    expect(clean.note).toBe("");
  });

  it("keeps the provenance the operator selected", () => {
    const clean = resetObservationForm(form({ data_source: "import" }));
    expect(clean.data_source).toBe("import");
  });

  it("does not share state between forms", () => {
    const first = form({ salinity_g_l: "280" });
    const second = emptyObservationForm();
    first.salinity_g_l = "300";
    expect(second.salinity_g_l).toBe("");
  });
});

// -- chart series ---------------------------------------------------------

describe("chart series", () => {
  it("orders readings oldest first so the line reads left to right", () => {
    const series = buildChartSeries(
      [
        observation({ timestamp: "2026-03-14T12:00:00Z", salinity_g_l: 300 }),
        observation({ timestamp: "2026-03-01T06:00:00Z", salinity_g_l: 250 }),
        observation({ timestamp: "2026-03-07T06:00:00Z", salinity_g_l: 275 }),
      ],
      "salinity_g_l",
    );
    expect(series.points.map((point) => point.value)).toEqual([250, 275, 300]);
  });

  it("places a null at a reading that omitted the measurement", () => {
    const series = buildChartSeries(
      [
        observation({ timestamp: "2026-03-01T06:00:00Z", salinity_g_l: 250 }),
        observation({ timestamp: "2026-03-07T06:00:00Z", salinity_g_l: null }),
        observation({ timestamp: "2026-03-14T06:00:00Z", salinity_g_l: 300 }),
      ],
      "salinity_g_l",
    );
    expect(series.points.map((point) => point.value)).toEqual([250, null, 300]);
    expect(series.points[1]?.missing).toBe(true);
  });

  it("counts measured and total readings separately", () => {
    const series = buildChartSeries(
      [
        observation({ timestamp: "2026-03-01T06:00:00Z", salinity_g_l: 250 }),
        observation({ timestamp: "2026-03-07T06:00:00Z", salinity_g_l: null }),
        observation({ timestamp: "2026-03-14T06:00:00Z", salinity_g_l: 300 }),
      ],
      "salinity_g_l",
    );
    expect(series.measuredCount).toBe(2);
    expect(series.readingCount).toBe(3);
  });

  it("keeps the pan's other measurements out of the series", () => {
    const series = buildChartSeries(
      [observation({ water_depth_cm: 14, salinity_g_l: 280 })],
      "salinity_g_l",
    );
    expect(series.points[0]?.value).toBe(280);
  });

  it("carries provenance and note through to the point", () => {
    const series = buildChartSeries(
      [observation({ data_source: "import", note: "from logger" })],
      "salinity_g_l",
    );
    expect(series.points[0]?.source).toBe("import");
    expect(series.points[0]?.note).toBe("from logger");
  });

  it("is empty when there are no readings", () => {
    const series = buildChartSeries([], "salinity_g_l");
    expect(series.points).toEqual([]);
    expect(series.measuredCount).toBe(0);
    expect(series.readingCount).toBe(0);
  });

  it("does not claim a trend from a single reading", () => {
    expect(needsTwoReadings(buildChartSeries([observation()], "salinity_g_l"))).toBe(
      true,
    );
  });

  it("does not claim a trend when the quantity was never measured", () => {
    const series = buildChartSeries(
      [
        observation({ salinity_g_l: null }),
        observation({ timestamp: "2026-03-15T06:00:00Z", salinity_g_l: null }),
      ],
      "salinity_g_l",
    );
    expect(needsTwoReadings(series)).toBe(true);
  });

  it("accepts a trend once two readings carry the quantity", () => {
    const series = buildChartSeries(
      [
        observation({ timestamp: "2026-03-01T06:00:00Z" }),
        observation({ timestamp: "2026-03-14T06:00:00Z" }),
      ],
      "salinity_g_l",
    );
    expect(needsTwoReadings(series)).toBe(false);
  });

  it("charts the four quantities named in the module", () => {
    expect([...CHARTED_METRICS]).toEqual([
      "salinity_g_l",
      "water_depth_cm",
      "brine_temperature_c",
      "humidity_percent",
    ]);
  });

  it("spaces points by their real timestamps", () => {
    const series = buildChartSeries(
      [
        observation({ timestamp: "2026-03-01T00:00:00Z" }),
        observation({ timestamp: "2026-03-11T00:00:00Z" }),
      ],
      "salinity_g_l",
    );
    const gap =
      (series.points[1]?.at ?? 0) - (series.points[0]?.at ?? 0);
    expect(gap).toBe(10 * 24 * 60 * 60 * 1000);
  });
});

describe("demonstration observations", () => {
  it("starts measured and blank, because that is the honest default", () => {
    const values = emptyObservationForm();
    expect(isDemonstrationMode(values)).toBe(false);
    expect(values.salinity_g_l).toBe("");
    // A form that opens on the fixed figures would make a demonstration look like
    // the normal case, and a real operator would save them believing they had
    // measured the pan.
    expect(values.salinity_g_l).not.toBe(String(DEMONSTRATION_OBSERVATION.salinity_g_l));
  });

  it("fills the fixed values when the mode asks for them", () => {
    const values = emptyObservationForm({ mode: "demonstration" });
    expect(isDemonstrationMode(values)).toBe(true);
    expect(values.data_source).toBe(DEMONSTRATION_SOURCE);
    expect(values.salinity_g_l).toBe(String(DEMONSTRATION_OBSERVATION.salinity_g_l));
    expect(values.water_depth_cm).toBe(String(DEMONSTRATION_OBSERVATION.water_depth_cm));
    expect(values.brine_temperature_c).toBe(
      String(DEMONSTRATION_OBSERVATION.brine_temperature_c),
    );
  });

  it("keeps its provenance through validation rather than re-deciding it", () => {
    // The mode is derived from `data_source`, not the other way round, so that a
    // form cannot hold a demonstration alongside a measured label. Validation
    // therefore has nothing to arbitrate: it carries the source through and marks
    // the note to match. The invariant worth locking down is that neither
    // direction can drift — a demonstration is never downgraded to a real source,
    // and a real reading is never silently promoted to one.
    const demo = validateObservation(emptyObservationForm({ mode: "demonstration" }), {
      panId: PAN_ID,
    });
    expect(demo.payload?.data_source).toBe(DEMONSTRATION_SOURCE);

    const real = validateObservation(
      { ...emptyObservationForm(), data_source: "sensor", salinity_g_l: "280" },
      { panId: PAN_ID },
    );
    expect(real.errors).toEqual({});
    expect(real.payload?.data_source).toBe("sensor");
    // A real reading must not acquire the marker, or the note would misdescribe
    // an instrument reading in every history and export.
    expect(isDemonstrationNote(real.payload?.note ?? null)).toBe(false);
  });

  it("marks its note as a demonstration", () => {
    const result = validateObservation(
      { ...emptyObservationForm({ mode: "demonstration" }), note: "For the walkthrough." },
      { panId: PAN_ID },
    );
    expect(isDemonstrationNote(result.payload?.note)).toBe(true);
    expect(result.payload?.note).toContain("For the walkthrough.");
  });

  it("re-seeds a demonstration on reset, dropping the previous note", () => {
    // Deliberate, and the opposite of the measured case: recording a
    // demonstration twice is how an operator checks the second one joins the
    // history behind the first, so a reset back to a measured form would defeat
    // the thing they are doing. The note is cleared either way, so a walkthrough
    // note is never carried onto the next record.
    const reset = resetObservationForm({
      ...emptyObservationForm({ mode: "demonstration" }),
      note: "First run of the walkthrough.",
    });
    expect(isDemonstrationMode(reset)).toBe(true);
    expect(reset.salinity_g_l).toBe(String(DEMONSTRATION_OBSERVATION.salinity_g_l));
    expect(reset.note).not.toContain("First run");
  });

  it("keeps a measured form's provenance on reset, because the operator chose it", () => {
    // An operator working through a batch from one instrument should not re-pick
    // the same source on every row, and a reset that forgot it would file the
    // batch by repetition rather than by decision.
    const reset = resetObservationForm({
      ...emptyObservationForm(),
      data_source: "sensor" as DataSource,
      salinity_g_l: "280",
    });
    expect(reset.data_source).toBe("sensor");
    expect(reset.salinity_g_l).toBe("");
  });

  it("labels a demonstration differently from every real source", () => {
    expect(describeSource(DEMONSTRATION_SOURCE)).toMatch(/not measured/i);
    expect(describeSource(DEMONSTRATION_SOURCE)).not.toBe(describeSource("manual"));
    expect(describeSource(DEMONSTRATION_SOURCE)).not.toBe(describeSource("sensor"));
  });

  it("flags demonstration points on the chart rather than drawing them as readings", () => {
    const series = buildChartSeries(
      [
        observation({ timestamp: "2026-03-01T06:00:00Z", data_source: "manual" }),
        observation({ timestamp: "2026-03-14T06:00:00Z", data_source: DEMONSTRATION_SOURCE }),
      ],
      "salinity_g_l",
    );
    // The value is still plotted, so the shape of the series is not altered. What
    // changes is that the point is marked, because a chart that silently mixed
    // typed-in figures with readings would plot both as the same kind of thing.
    expect(series.points.map((point) => point.demonstration)).toEqual([false, true]);
    expect(series.demonstrationCount).toBe(1);
  });

  it("says on the chart that a demonstration is present, and how many", () => {
    const mixed = buildChartSeries(
      [
        observation({ timestamp: "2026-03-01T06:00:00Z", data_source: "manual" }),
        observation({ timestamp: "2026-03-07T06:00:00Z", data_source: DEMONSTRATION_SOURCE }),
        observation({ timestamp: "2026-03-14T06:00:00Z", data_source: "manual" }),
      ],
      "salinity_g_l",
    );
    // The count has to reach the reader: a hollow point nobody is told about is
    // the same as a solid one as far as the chart is concerned.
    expect(demonstrationSeriesWarning(mixed)).toContain("1 of 3");
  });

  it("says so more plainly when the whole series is demonstration", () => {
    const all = buildChartSeries(
      [
        observation({ timestamp: "2026-03-01T06:00:00Z", data_source: DEMONSTRATION_SOURCE }),
        observation({ timestamp: "2026-03-14T06:00:00Z", data_source: DEMONSTRATION_SOURCE }),
      ],
      "salinity_g_l",
    );
    expect(demonstrationSeriesWarning(all)).toMatch(/every reading/i);
  });

  it("leaves a real-only series unmarked", () => {
    const series = buildChartSeries(
      [observation({ data_source: "sensor" })],
      "salinity_g_l",
    );
    expect(series.demonstrationCount).toBe(0);
    expect(demonstrationSeriesWarning(series)).toBeNull();
  });
});
