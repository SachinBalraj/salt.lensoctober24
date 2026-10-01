/**
 * Pan-observation domain types, operator-facing validation and chart shaping.
 *
 * Like `pans.ts`, this module is free of React and of `fetch` so the rules an
 * operator depends on can be unit-tested directly.
 *
 * One principle runs through it: a value the operator did not measure stays
 * absent. Blank inputs validate to `null`, `null` is sent to the API, `null`
 * comes back, and the chart draws a gap. Nothing is defaulted, averaged or
 * carried forward, because a plausible-looking number that nobody measured is
 * worse than a visible hole in the record.
 *
 * A demonstration is the one case where values are filled in, and it is the
 * reason the provenance vocabulary exists. Those values are constants rather
 * than defaults, they are read-only, and they are filed under their own source
 * so the backend keeps them out of every research dataset. The distinction is
 * carried in `data_source` and this module refuses to submit demonstration
 * values under any other label.
 */

import {
  DEMONSTRATION_NOTE_PREFIX,
  DEMONSTRATION_OBSERVATION,
  DEMONSTRATION_SOURCE,
  isDemonstrationSource,
  markDemonstrationNote,
} from "@/lib/demonstration";

/** How a reading reached SaltLens. Mirrors the backend's ResearchDataSource. */
export type DataSource = "manual" | "sensor" | "import" | "demonstration";

export const DATA_SOURCE_LABELS: Record<DataSource, string> = {
  manual: "Manual entry",
  sensor: "Sensor gateway",
  import: "Imported dataset",
  demonstration: "Demonstration (not measured)",
};

/** A stored reading, exactly as the API returns it. */
export type PanObservation = {
  observation_id: string;
  pan_id: string;
  timestamp: string;
  salinity_g_l: number | null;
  water_depth_cm: number | null;
  brine_temperature_c: number | null;
  humidity_percent: number | null;
  ambient_temperature_c: number | null;
  data_source: DataSource;
  note: string | null;
  created_at: string;
};

export type PanObservationListResponse = {
  items: PanObservation[];
  total: number;
  limit: number;
  offset: number;
  earliest_timestamp: string | null;
  latest_timestamp: string | null;
};

/**
 * Payload accepted by `POST /api/observations/`.
 *
 * Measurements are optional individually but at least one must be present.
 * `timestamp` is omitted to record the moment of saving, or supplied to
 * backdate an entry.
 */
export type ObservationRegistration = {
  pan_id: string;
  timestamp?: string;
  salinity_g_l: number | null;
  water_depth_cm: number | null;
  brine_temperature_c: number | null;
  humidity_percent: number | null;
  ambient_temperature_c: number | null;
  data_source: DataSource;
  note?: string | null;
};

/**
 * Plausibility bounds per measurement.
 *
 * These are deliberately wide: they are here to catch a typed digit, not to
 * rule out an unusual but real pan. Note that zero is a valid reading for
 * depth, salinity and humidity — a dry pan measures zero depth, and that is
 * information rather than a missing value.
 */
export const MEASUREMENT_RANGES = {
  salinity_g_l: { label: "Salinity", unit: "g/L", min: 0, max: 500 },
  water_depth_cm: { label: "Water depth", unit: "cm", min: 0, max: 1000 },
  brine_temperature_c: { label: "Brine temperature", unit: "°C", min: -20, max: 120 },
  humidity_percent: { label: "Humidity", unit: "%", min: 0, max: 100 },
  ambient_temperature_c: { label: "Ambient temperature", unit: "°C", min: -50, max: 80 },
} as const;

export type MeasurementKey = keyof typeof MEASUREMENT_RANGES;

export const MEASUREMENT_KEYS = Object.keys(
  MEASUREMENT_RANGES,
) as MeasurementKey[];

/** Fields offered in the form, in the order an operator reads them. */
export const CHARTED_METRICS = [
  "salinity_g_l",
  "water_depth_cm",
  "brine_temperature_c",
  "humidity_percent",
] as const satisfies readonly MeasurementKey[];

export type ChartedMetric = (typeof CHARTED_METRICS)[number];

export const MAX_NOTE_LENGTH = 500;

/** Client clock tolerance, matching the backend's MAX_FUTURE_SKEW. */
export const MAX_FUTURE_SKEW_MINUTES = 5;

export type ObservationFormValues = {
  /** `datetime-local` text. Blank records the moment of saving. */
  timestamp: string;
  salinity_g_l: string;
  water_depth_cm: string;
  brine_temperature_c: string;
  humidity_percent: string;
  ambient_temperature_c: string;
  data_source: DataSource;
  note: string;
};

export const EMPTY_OBSERVATION_FORM: ObservationFormValues = {
  timestamp: "",
  salinity_g_l: "",
  water_depth_cm: "",
  brine_temperature_c: "",
  humidity_percent: "",
  ambient_temperature_c: "",
  data_source: "manual",
  note: "",
};

/**
 * The two kinds of thing this form records.
 *
 * Not a third source: `demonstration` is itself a member of `DataSource`, and
 * the mode is that member. Deriving one from the other is what stops a form
 * from being able to hold contradictory state, such as demonstration values
 * alongside a provenance of manual entry.
 */
export type ObservationMode = "measured" | "demonstration";

/** True when the form is recording a demonstration rather than a reading. */
export function isDemonstrationMode(values: {
  data_source: DataSource;
}): boolean {
  return isDemonstrationSource(values.data_source);
}

/** The demonstration's fixed values, as the form holds them: text in, text out. */
export const DEMONSTRATION_OBSERVATION_FORM: ObservationFormValues = {
  timestamp: "",
  salinity_g_l: String(DEMONSTRATION_OBSERVATION.salinity_g_l),
  water_depth_cm: String(DEMONSTRATION_OBSERVATION.water_depth_cm),
  brine_temperature_c: String(DEMONSTRATION_OBSERVATION.brine_temperature_c),
  humidity_percent: String(DEMONSTRATION_OBSERVATION.humidity_percent),
  ambient_temperature_c: String(DEMONSTRATION_OBSERVATION.ambient_temperature_c),
  data_source: DEMONSTRATION_SOURCE,
  note: DEMONSTRATION_NOTE_PREFIX,
};

/**
 * Provenance choices a form may submit, in the order they read.
 *
 * `demonstration` is absent because it is reached by the mode toggle rather than
 * picked from a list, and `sensor` is only offered when a gateway exists. The
 * two lists cannot drift, because both come from this one.
 */
export const SELECTABLE_DATA_SOURCES = [
  "manual",
  "import",
  "sensor",
] as const satisfies readonly DataSource[];

export type SelectableDataSource = (typeof SELECTABLE_DATA_SOURCES)[number];

export type ObservationFormErrors = Partial<
  Record<keyof ObservationFormValues, string>
> & { form?: string };

const NUMERIC_PATTERN = /^-?\d*\.?\d+$/;

/**
 * Validates one optional measurement.
 *
 * Blank is not an error: it means "not measured". A present value must be a
 * finite number inside the plausibility range for that quantity.
 */
export function validateMeasurement(
  raw: string,
  key: MeasurementKey,
): { value: number | null; error: string | null } {
  const { label, unit, min, max } = MEASUREMENT_RANGES[key];
  const trimmed = raw.trim();
  if (trimmed === "") return { value: null, error: null };
  if (!NUMERIC_PATTERN.test(trimmed)) {
    return { value: null, error: `${label} must be a number.` };
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed)) {
    return { value: null, error: `${label} must be a finite number.` };
  }
  if (parsed < min || parsed > max) {
    return {
      value: null,
      error: `${label} must be between ${min} and ${max} ${unit}.`,
    };
  }
  return { value: parsed, error: null };
}

/**
 * Validates a `datetime-local` value and returns it as an ISO instant.
 *
 * The input carries no timezone, so it is read as the operator's local wall
 * clock and converted. Blank means "now" and yields `null`.
 */
export function validateObservationTimestamp(
  raw: string,
  { now = new Date() }: { now?: Date } = {},
): { value: string | null; error: string | null } {
  const trimmed = raw.trim();
  if (trimmed === "") return { value: null, error: null };
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) {
    return { value: null, error: "Timestamp could not be read." };
  }
  const latestAcceptable = now.getTime() + MAX_FUTURE_SKEW_MINUTES * 60_000;
  if (parsed.getTime() > latestAcceptable) {
    return {
      value: null,
      error:
        "Timestamp is in the future. A reading describes a moment that has already happened.",
    };
  }
  return { value: parsed.toISOString(), error: null };
}

export function validateNote(
  raw: string,
): { value: string | null; error: string | null } {
  const trimmed = raw.trim();
  if (trimmed === "") return { value: null, error: null };
  if (trimmed.length > MAX_NOTE_LENGTH) {
    return {
      value: null,
      error: `Note must be ${MAX_NOTE_LENGTH} characters or fewer.`,
    };
  }
  return { value: trimmed, error: null };
}

/**
 * Full-form validation for recording an observation.
 *
 * A reading with nothing measured is refused: it cannot be charted and it is
 * not evidence of anything about the pan.
 *
 * The demonstration case is handled here rather than in the component, because
 * this is the last point before the payload exists and it is the only place that
 * can guarantee the two claims the rest of the system relies on: that a
 * demonstration is filed as a demonstration, and that its note carries the
 * marker. A component bug that set the wrong source would otherwise be enough to
 * write demonstration values into the research record.
 */
export function validateObservation(
  values: ObservationFormValues,
  { panId, now }: { panId: string; now?: Date },
): { payload: ObservationRegistration | null; errors: ObservationFormErrors } {
  const errors: ObservationFormErrors = {};
  const demonstration = isDemonstrationMode(values);

  const timestamp = validateObservationTimestamp(values.timestamp, { now });
  if (timestamp.error) errors.timestamp = timestamp.error;

  const measurements: Record<MeasurementKey, number | null> = {
    salinity_g_l: null,
    water_depth_cm: null,
    brine_temperature_c: null,
    humidity_percent: null,
    ambient_temperature_c: null,
  };
  for (const key of MEASUREMENT_KEYS) {
    const result = validateMeasurement(values[key], key);
    if (result.error) errors[key] = result.error;
    measurements[key] = result.value;
  }

  const measuredAny = MEASUREMENT_KEYS.some(
    (key) => measurements[key] !== null,
  );
  if (!measuredAny && Object.keys(errors).length === 0) {
    errors.form = demonstration
      ? "A demonstration needs at least one value. These are the fixed demonstration readings; record them as they stand."
      : "Record at least one measured value. A reading with no measurements cannot be charted.";
  }

  // The marker is added before the length check, because the marker is what makes
  // the note mean anything; a note that is one character too long once marked is
  // still one character too long, and the error should name the limit rather than
  // suggest deleting the proof of provenance.
  const note = validateNote(
    demonstration ? markDemonstrationNote(values.note) : values.note,
  );
  if (note.error) errors.note = note.error;

  if (Object.keys(errors).length > 0) {
    return { payload: null, errors };
  }

  const payload: ObservationRegistration = {
    pan_id: panId,
    ...measurements,
    // Forced rather than passed through. The mode is the source, so a
    // demonstration cannot be submitted under a measured label even if the
    // component is changed to try.
    data_source: demonstration ? DEMONSTRATION_SOURCE : values.data_source,
  };
  if (timestamp.value !== null) payload.timestamp = timestamp.value;
  if (note.value !== null) payload.note = note.value;
  return { payload, errors: {} };
}

/**
 * Seeds the form for a fresh reading, in one of two modes.
 *
 * `measured` starts blank; `demonstration` fills the fixed demonstration values.
 *
 * The default is `measured`, and that is deliberate rather than incidental. This
 * function's blank form is what the existing suite holds up as the project's
 * central rule — a value nobody measured stays absent, and a field is never
 * filled in on the operator's behalf. A seeding helper that defaulted to
 * prefilled numbers would put that rule one argument away from being false, and
 * the form component is a better place to decide that a particular screen opens
 * on a demonstration, because that is a statement about the screen.
 */
export function emptyObservationForm({
  mode = "measured",
}: { mode?: ObservationMode } = {}): ObservationFormValues {
  if (mode === "measured") return { ...EMPTY_OBSERVATION_FORM };
  return {
    ...EMPTY_OBSERVATION_FORM,
    ...DEMONSTRATION_OBSERVATION_FORM,
    data_source: DEMONSTRATION_SOURCE,
  };
}


/**
 * Clears the form after a save, keeping the provenance the operator chose.
 *
 * A measured form returns to blank values but keeps its source, because an
 * operator working through a batch of readings from one instrument should not
 * have to re-pick the same provenance on every row, and a reset that forgets it
 * invites a whole batch to be filed as manual entry by repetition rather than by
 * decision.
 *
 * A demonstration re-seeds completely, because recording one twice is how an
 * operator checks the second joins the history behind the first.
 */
export function resetObservationForm(
  values: ObservationFormValues,
): ObservationFormValues {
  if (isDemonstrationMode(values)) {
    return emptyObservationForm({ mode: "demonstration" });
  }
  return { ...EMPTY_OBSERVATION_FORM, data_source: values.data_source };
}

// -- chart shaping --------------------------------------------------------

export type ChartPoint = {
  /** Epoch milliseconds, for a proportional time axis. */
  at: number;
  /** Short axis label. */
  label: string;
  /** The measured value, or `null` when this reading did not record it. */
  value: number | null;
  /** True when a reading exists at this instant but omitted this quantity. */
  missing: boolean;
  source: DataSource;
  note: string | null;
  /**
   * True when this point is a demonstration rather than a reading.
   *
   * Carried on the point so a chart can mark it as it draws. A demonstration
   * that is only labelled on hover is one nobody hovering will see, and it sits
   * on the same axis as real readings where a reader has no reason to suspect it.
   */
  demonstration: boolean;
};

export type ChartSeries = {
  key: ChartedMetric;
  label: string;
  unit: string;
  points: ChartPoint[];
  /** How many readings actually carry this measurement. */
  measuredCount: number;
  /** Total readings in the window, including those that omitted it. */
  readingCount: number;
  /** How many of the points are demonstrations. */
  demonstrationCount: number;
};

const AXIS_LABEL = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
  timeZone: "UTC",
});

const FULL_TIMESTAMP = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "UTC",
});

export function formatAxisTimestamp(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return `${AXIS_LABEL.format(date)} UTC`;
}

export function formatFullTimestamp(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return `${FULL_TIMESTAMP.format(parsed)} UTC`;
}

/** Provenance is shown alongside a reading so its origin is never in doubt. */
export function describeSource(source: DataSource | string): string {
  return DATA_SOURCE_LABELS[source as DataSource] ?? String(source);
}

/**
 * Builds one chart series from the stored readings.
 *
 * Every reading becomes an x position, whether or not it measured this
 * quantity. That is what makes a missing value visible as a break in the line
 * instead of a straight segment running through a hole in the record. Values
 * are never interpolated, smoothed or averaged, and the series is ordered
 * oldest first so the line reads left to right in time.
 */
export function buildChartSeries(
  observations: readonly PanObservation[],
  key: ChartedMetric,
): ChartSeries {
  const ordered = [...observations].sort(
    (left, right) =>
      new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime(),
  );

  const points: ChartPoint[] = ordered.map((observation) => {
    const at = new Date(observation.timestamp).getTime();
    const value = observation[key];
    return {
      at,
      label: Number.isNaN(at) ? "—" : AXIS_LABEL.format(new Date(at)),
      value: value === null || Number.isNaN(at) ? null : value,
      missing: value === null,
      source: observation.data_source,
      note: observation.note,
      demonstration: isDemonstrationSource(observation.data_source),
    };
  });

  return {
    key,
    label: MEASUREMENT_RANGES[key].label,
    unit: MEASUREMENT_RANGES[key].unit,
    points,
    measuredCount: points.filter((point) => point.value !== null).length,
    readingCount: points.length,
    demonstrationCount: points.filter((point) => point.demonstration).length,
  };
}

/** A single reading is a value, not a trend, so no line is drawn from one. */
export function needsTwoReadings(series: ChartSeries): boolean {
  return series.measuredCount < 2;
}

/**
 * The warning a series carries when it is drawn partly from demonstrations.
 *
 * Returned rather than a bare boolean so the chart says which points and how
 * many, and so the wording lives with the other domain rules instead of being
 * retyped in a component.
 */
export function demonstrationSeriesWarning(series: ChartSeries): string | null {
  if (series.demonstrationCount === 0) return null;
  if (series.demonstrationCount === series.readingCount) {
    return "Every reading on this chart is a demonstration. The shape is what the recorded workflow produces; it is not a record of this pan.";
  }
  const pointWord = series.demonstrationCount === 1 ? "point" : "points";
  return `${series.demonstrationCount} of ${series.readingCount} readings on this chart are demonstrations and are drawn as hollow ${pointWord}. Only the solid ${pointWord} are measurements.`;
}
