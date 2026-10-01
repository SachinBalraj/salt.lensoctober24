/**
 * Weather domain types, operator-facing validation and chart shaping.
 *
 * Free of React and of `fetch`, so the rules an operator depends on can be
 * unit-tested directly.
 *
 * Two principles run through this module.
 *
 * Absence stays absence. A blank input validates to `null`, `null` is sent to
 * the API, `null` comes back, and the chart draws a gap. Nothing is defaulted,
 * averaged or carried forward. A forecast of `0` mm is the exception that proves
 * the rule: zero is a real prediction of no rain, so it is stored as `0` and is
 * never confused with not knowing.
 *
 * Provenance is part of the record, not a display hint. `weather_api` is
 * deliberately absent from the form's selectable sources, so a hand-entered
 * record cannot be filed as something a service retrieved. The type still
 * includes it because stored records genuinely can carry it once a provider is
 * connected.
 *
 * A demonstration is a third thing again, and it is a forecast that nobody
 * issued. `manual` says a person entered a forecast somebody else published;
 * `demonstration` says the rain in it was typed in to show the workflow, and
 * the backend keeps such records out of every research dataset. Without that
 * member the only way to demonstrate a forecast is to file it as a real one.
 */

import {
  DEMONSTRATION_LABEL,
  DEMONSTRATION_NOTICE,
  DEMONSTRATION_SOURCE,
  isDemonstrationSource,
} from "@/lib/demonstration";

/** How a weather record reached SaltLens. Mirrors the backend's WeatherSource. */
export type WeatherSource =
  | "manual"
  | "weather_api"
  | "sensor"
  | "demonstration";

export const WEATHER_SOURCE_LABELS: Record<WeatherSource, string> = {
  manual: "Entered by hand",
  weather_api: "Weather API",
  sensor: "Sensor gateway",
  demonstration: DEMONSTRATION_LABEL,
};

/**
 * Plain-language explanation of a source, shown wherever a record's origin is
 * displayed so its provenance is never in doubt.
 */
export const WEATHER_SOURCE_DESCRIPTIONS: Record<WeatherSource, string> = {
  manual:
    "Entered by an operator. No automated service supplied this reading.",
  weather_api:
    "Retrieved automatically from a connected weather API.",
  sensor: "Reported by a connected weather sensor.",
  demonstration: DEMONSTRATION_NOTICE,
};

/** A stored weather record, exactly as the API returns it. */
export type WeatherObservation = {
  weather_id: string;
  pan_id: string;
  /** When the reading was taken, or when the forecast was retrieved. */
  timestamp: string;
  /** The instant a forecast applies to, or `null` for what actually happened. */
  forecast_for: string | null;
  rainfall_mm: number | null;
  rainfall_probability_percent: number | null;
  ambient_temperature_c: number | null;
  humidity_percent: number | null;
  source: WeatherSource;
  is_forecast: boolean;
  created_at: string;
};

export type WeatherObservationListResponse = {
  items: WeatherObservation[];
  total: number;
  limit: number;
  offset: number;
  earliest_timestamp: string | null;
  latest_timestamp: string | null;
};

export type WeatherProvider = {
  name: string;
  source: WeatherSource;
};

export type WeatherProvidersResponse = {
  registered: WeatherProvider[];
  count: number;
  manual_entry_available: boolean;
  detail: string;
};

export type WeatherIngestResponse = {
  provider: string;
  source: WeatherSource;
  pan_id: string;
  requested_start: string;
  requested_end: string;
  stored: number;
  rejected: number;
  observations: WeatherObservation[];
};

/**
 * Payload accepted by `POST /api/weather/`.
 *
 * `weather_api` is not part of this type on purpose. The manual-entry form has
 * no way to produce it, which mirrors the backend, where only the forecast
 * ingest route may set that provenance.
 */
export type WeatherRegistration = {
  pan_id: string;
  timestamp?: string;
  forecast_for?: string | null;
  rainfall_mm: number | null;
  rainfall_probability_percent: number | null;
  ambient_temperature_c: number | null;
  humidity_percent: number | null;
  source: Exclude<WeatherSource, "weather_api">;
};

/** Sources the manual-entry form is allowed to submit. */
export const MANUALLY_SELECTABLE_SOURCES = [
  "manual",
  "sensor",
] as const satisfies readonly WeatherSource[];

export type ManualWeatherSource = (typeof MANUALLY_SELECTABLE_SOURCES)[number];

/**
 * The two kinds of thing this form records.
 *
 * `demonstration` is a member of `WeatherSource` and the mode is that member,
 * so a record cannot be a demonstration while claiming a provenance somebody
 * else issued.
 */
export type WeatherMode = "actual" | "demonstration";

/** True when the form is recording a demonstration rather than a real reading. */
export function isDemonstrationMode(values: { source: WeatherSource }): boolean {
  return isDemonstrationSource(values.source);
}

/**
 * The demonstration forecast's values.
 *
 * A forecast needs an instant, and an instant cannot be a constant: a fixed
 * timestamp would be in the past within a week and the backend would refuse it
 * as a forecast aimed before it was made. So the values are constant and the
 * instant is computed, `DEMONSTRATION_FORECAST_LEAD_HOURS` ahead of the moment
 * of saving. Everything the operator would otherwise have to type is typed; the
 * one thing that has to be a real time is a real time.
 */
export const DEMONSTRATION_FORECAST_LEAD_HOURS = 6;

export const DEMONSTRATION_WEATHER = {
  rainfall_mm: 25,
  rainfall_probability_percent: 70,
  ambient_temperature_c: 27,
  humidity_percent: 60,
} as const;

/**
 * Plausibility bounds per quantity.
 *
 * Deliberately wide: these catch a typed digit, not an unusual day. The rainfall
 * ceiling sits far above any plausible event so that centimetres or inches typed
 * into a millimetre field is caught.
 */
export const WEATHER_RANGES = {
  rainfall_mm: {
    label: "Rainfall",
    unit: "mm",
    min: 0,
    max: 1000,
  },
  rainfall_probability_percent: {
    label: "Rain probability",
    unit: "%",
    min: 0,
    max: 100,
  },
  ambient_temperature_c: {
    label: "Ambient temperature",
    unit: "°C",
    min: -50,
    max: 80,
  },
  humidity_percent: {
    label: "Humidity",
    unit: "%",
    min: 0,
    max: 100,
  },
} as const;

export type WeatherRangeKey = keyof typeof WEATHER_RANGES;

export const WEATHER_RANGE_KEYS = Object.keys(
  WEATHER_RANGES,
) as WeatherRangeKey[];

/** Quantities offered in the form, in the order an operator reads them. */
export const CHARTED_WEATHER_METRICS = [
  "rainfall_mm",
  "rainfall_probability_percent",
  "ambient_temperature_c",
  "humidity_percent",
] as const satisfies readonly WeatherRangeKey[];

export type ChartedWeatherMetric = (typeof CHARTED_WEATHER_METRICS)[number];

/** Client clock tolerance, matching the backend's MAX_FUTURE_SKEW. */
export const MAX_FUTURE_SKEW_MINUTES = 5;

/** Mirrors the backend's MAX_FORECAST_HORIZON. */
export const MAX_FORECAST_HORIZON_DAYS = 14;

export type WeatherFormValues = {
  /** `datetime-local` text. Blank records the moment of saving. */
  timestamp: string;
  /** `datetime-local` text. Blank means this record reports what happened. */
  forecast_for: string;
  rainfall_mm: string;
  rainfall_probability_percent: string;
  ambient_temperature_c: string;
  humidity_percent: string;
  /**
   * The provenance this record will be filed under.
   *
   * Wider than {@link ManualWeatherSource} on purpose: the mode toggle sets this
   * to `demonstration`, and the form must be able to represent that even though
   * it is not a choice from the provenance list. `weather_api` still cannot
   * appear here, and `validateWeather` refuses to pass it on.
   */
  source: Exclude<WeatherSource, "weather_api">;
};

export const EMPTY_WEATHER_FORM: WeatherFormValues = {
  timestamp: "",
  forecast_for: "",
  rainfall_mm: "",
  rainfall_probability_percent: "",
  ambient_temperature_c: "",
  humidity_percent: "",
  source: "manual",
};

export type WeatherFormErrors = Partial<
  Record<keyof WeatherFormValues, string>
> & { form?: string };

const NUMERIC_PATTERN = /^-?\d*\.?\d+$/;

/**
 * Validates one optional quantity.
 *
 * Blank is not an error: it means "not measured" or "not forecast". A present
 * value must be a finite number inside the plausibility range.
 */
export function validateWeatherMeasurement(
  raw: string,
  key: WeatherRangeKey,
): { value: number | null; error: string | null } {
  const { label, unit, min, max } = WEATHER_RANGES[key];
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
 * Reads a `datetime-local` value as the operator's local wall clock.
 *
 * The input carries no timezone, so it is interpreted where the operator is and
 * converted. Blank yields `null`, which means "now" for an observation and
 * "not a forecast" for `forecast_for`.
 */
function readLocalDateTime(
  raw: string,
): { value: Date | null; error: string | null } {
  const trimmed = raw.trim();
  if (trimmed === "") return { value: null, error: null };
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) {
    return { value: null, error: "Timestamp could not be read." };
  }
  return { value: parsed, error: null };
}

export function validateWeatherTimestamp(
  raw: string,
  { now = new Date() }: { now?: Date } = {},
): { value: string | null; error: string | null } {
  const { value, error } = readLocalDateTime(raw);
  if (error || value === null) return { value: null, error };
  const latestAcceptable = now.getTime() + MAX_FUTURE_SKEW_MINUTES * 60_000;
  if (value.getTime() > latestAcceptable) {
    return {
      value: null,
      error:
        "Timestamp is in the future. A reading describes a moment that has already happened.",
    };
  }
  return { value: value.toISOString(), error: null };
}

export function validateForecastFor(
  raw: string,
  {
    observedAt,
    now = new Date(),
  }: { observedAt: string | null; now?: Date },
): { value: string | null; error: string | null } {
  const { value, error } = readLocalDateTime(raw);
  if (error || value === null) return { value: null, error };

  const issuedAt = observedAt ? new Date(observedAt) : now;
  if (value.getTime() < issuedAt.getTime()) {
    return {
      value: null,
      error:
        "A forecast cannot be aimed at an earlier time than the moment it was made.",
    };
  }
  const horizon = now.getTime() + MAX_FORECAST_HORIZON_DAYS * 86_400_000;
  if (value.getTime() > horizon) {
    return {
      value: null,
      error: `A forecast cannot be more than ${MAX_FORECAST_HORIZON_DAYS} days ahead. Check the year.`,
    };
  }
  return { value: value.toISOString(), error: null };
}

/**
 * Full-form validation for recording weather.
 *
 * A record with nothing measured or forecast is refused: it says nothing about
 * the weather and cannot be charted.
 *
 * A demonstration is forced to the demonstration source here, at the last point
 * before the payload exists, for the same reason the observation form does the
 * same: a component bug must not be able to file a typed-in forecast as one
 * somebody published. A demonstration is also required to be a forecast, because
 * "what the sky is doing now" is not something worth demonstrating.
 */
export function validateWeather(
  values: WeatherFormValues,
  { panId, now }: { panId: string; now?: Date },
): {
  payload: WeatherRegistration | null;
  errors: WeatherFormErrors;
} {
  const errors: WeatherFormErrors = {};
  const demonstration = isDemonstrationMode(values);

  // `weather_api` is excluded from `WeatherFormValues["source"]` by type, and the
  // compiler is right that the comparison below is dead under those types. It is
  // here anyway: a type is erased at runtime, and this is the last point before
  // the payload exists. A record filed as coming from a service that never sent it
  // is a claim about where a number came from, which is the one claim on this
  // screen that cannot be reconstructed from the record later, so it is not left
  // to the compiler alone. The read is widened to `string` on purpose, so that
  // deleting the type exclusion later does not quietly delete this guard with it.
  // The backend refuses the value on this route too; this is the nearer check.
  const declaredSource: string = values.source;
  if (declaredSource === "weather_api") {
    errors.source =
      "A weather-API reading is recorded by the forecast ingest, not typed in here.";
    return { payload: null, errors };
  }

  if (demonstration && values.forecast_for.trim() === "" && !errors.forecast_for) {
    errors.forecast_for =
      "A demonstration has to be a forecast. Enter the instant it applies to.";
  }

  const timestamp = validateWeatherTimestamp(values.timestamp, { now });
  if (timestamp.error) errors.timestamp = timestamp.error;

  const forecastFor = validateForecastFor(values.forecast_for, {
    observedAt: timestamp.value,
    now,
  });
  if (forecastFor.error) errors.forecast_for = forecastFor.error;

  const measurements: Record<WeatherRangeKey, number | null> = {
    rainfall_mm: null,
    rainfall_probability_percent: null,
    ambient_temperature_c: null,
    humidity_percent: null,
  };
  for (const key of WEATHER_RANGE_KEYS) {
    const result = validateWeatherMeasurement(values[key], key);
    if (result.error) errors[key] = result.error;
    measurements[key] = result.value;
  }

  const suppliedAny = WEATHER_RANGE_KEYS.some(
    (key) => measurements[key] !== null,
  );
  if (!suppliedAny && Object.keys(errors).length === 0) {
    errors.form = demonstration
      ? "A demonstration needs at least one value. These are the fixed demonstration readings; record them as they stand."
      : "Record at least one measured or forecast value. A weather record with nothing in it says nothing about the weather.";
  }

  if (Object.keys(errors).length > 0) {
    return { payload: null, errors };
  }

  const payload: WeatherRegistration = {
    pan_id: panId,
    ...measurements,
    // Forced, not passed through, so the mode is the only thing that decides the
    // provenance. `weather_api` was already turned away above, so it cannot
    // reach this line by any route.
    source: demonstration ? DEMONSTRATION_SOURCE : values.source,
  };
  if (timestamp.value !== null) payload.timestamp = timestamp.value;
  payload.forecast_for = forecastFor.value;
  return { payload, errors: {} };
}

/**
 * Seeds the form for a fresh record, in one of two modes.
 *
 * `demonstration` prefills the fixed values and aims the forecast
 * `DEMONSTRATION_FORECAST_LEAD_HOURS` ahead of now; `actual` starts blank. The
 * default is `actual`, because the weather form is used to record what the sky
 * is doing, and opening it on forecast numbers would be the wrong default for
 * the common case. The demonstration mode is one click away and announces itself.
 */
export function emptyWeatherForm({
  mode = "actual",
  now = new Date(),
}: { mode?: WeatherMode; now?: Date } = {}): WeatherFormValues {
  if (mode === "actual") return { ...EMPTY_WEATHER_FORM };
  const issuedAt = now;
  const forecastFor = new Date(
    issuedAt.getTime() + DEMONSTRATION_FORECAST_LEAD_HOURS * 3_600_000,
  );
  return {
    ...EMPTY_WEATHER_FORM,
    timestamp: toLocalDateTimeInput(issuedAt),
    forecast_for: toLocalDateTimeInput(forecastFor),
    rainfall_mm: String(DEMONSTRATION_WEATHER.rainfall_mm),
    rainfall_probability_percent: String(
      DEMONSTRATION_WEATHER.rainfall_probability_percent,
    ),
    ambient_temperature_c: String(DEMONSTRATION_WEATHER.ambient_temperature_c),
    humidity_percent: String(DEMONSTRATION_WEATHER.humidity_percent),
    source: DEMONSTRATION_SOURCE,
  };
}

/**
 * Clears the form after a save.
 *
 * A real record returns to blank values but keeps its provenance, for the reason
 * the observation form does: an operator logging a run of readings from one
 * gateway should not re-pick the same source on every row.
 *
 * A demonstration re-seeds completely, and its forecast instant is recomputed,
 * because a demonstration aimed at a moment six hours ago is not the same
 * demonstration as one aimed six hours from now.
 */
export function resetWeatherForm(
  values: WeatherFormValues,
  { now }: { now?: Date } = {},
): WeatherFormValues {
  if (isDemonstrationMode(values)) {
    return emptyWeatherForm({ mode: "demonstration", now });
  }
  return { ...EMPTY_WEATHER_FORM, source: values.source };
}

/**
 * Renders a `Date` in the `YYYY-MM-DDTHH:mm` shape a `datetime-local` input
 * takes, in the reader's own timezone.
 *
 * `toISOString` would be wrong here: it converts to UTC, and the form reads the
 * value back as local wall-clock time, so a demonstration seeded in UTC would
 * land hours away from the instant it names once the operator is in another
 * zone. Both the write and the read have to agree on which clock they mean.
 */
export function toLocalDateTimeInput(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

// -- formatting ------------------------------------------------------------

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

export function formatFullTimestamp(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return `${FULL_TIMESTAMP.format(parsed)} UTC`;
}

export function formatAxisTimestamp(value: string | number | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return `${AXIS_LABEL.format(date)} UTC`;
}

/** Short source label, shown as a badge beside every record. */
export function describeWeatherSource(source: WeatherSource | string): string {
  return WEATHER_SOURCE_LABELS[source as WeatherSource] ?? String(source);
}

/** Sentence explaining where a record's values came from. */
export function explainWeatherSource(source: WeatherSource | string): string {
  return (
    WEATHER_SOURCE_DESCRIPTIONS[source as WeatherSource] ??
    `Unrecognised source: ${String(source)}`
  );
}

/** True when the record was retrieved by a service rather than a person. */
export function isAutomatedSource(source: WeatherSource | string): boolean {
  return source === "weather_api" || source === "sensor";
}

// -- chart shaping ---------------------------------------------------------

export type WeatherChartPoint = {
  /** Epoch milliseconds, for a proportional time axis. */
  at: number;
  value: number | null;
  /** True when a record exists at this instant but omitted this quantity. */
  missing: boolean;
  source: WeatherSource;
  is_forecast: boolean;
  forecast_for: string | null;
  /**
   * True when this record is a typed-in demonstration.
   *
   * Carried on the point so a chart can mark it as it draws. A demonstration
   * forecast on the same line as a fetched one is otherwise indistinguishable,
   * and the two imply very different things about the sky over a pan.
   */
  demonstration: boolean;
};

export type WeatherChartSeries = {
  key: ChartedWeatherMetric;
  label: string;
  unit: string;
  points: WeatherChartPoint[];
  measuredCount: number;
  recordCount: number;
  /** How many of the points are demonstrations. */
  demonstrationCount: number;
};

/**
 * Builds one chart series from the stored records.
 *
 * Every record occupies an x position, whether or not it carried this quantity,
 * which is what makes a missing value a break in the line rather than a straight
 * segment running through a hole. Values are never interpolated or averaged, and
 * the series is ordered oldest first so time reads left to right.
 */
export function buildWeatherChartSeries(
  observations: readonly WeatherObservation[],
  key: ChartedWeatherMetric,
): WeatherChartSeries {
  const ordered = [...observations].sort(
    (left, right) =>
      new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime(),
  );

  const points: WeatherChartPoint[] = ordered.map((observation) => {
    const at = new Date(observation.timestamp).getTime();
    const value = observation[key];
    return {
      at,
      value: value === null || Number.isNaN(at) ? null : value,
      missing: value === null,
      source: observation.source,
      is_forecast: observation.is_forecast,
      forecast_for: observation.forecast_for,
      demonstration: isDemonstrationSource(observation.source),
    };
  });

  return {
    key,
    label: WEATHER_RANGES[key].label,
    unit: WEATHER_RANGES[key].unit,
    points,
    measuredCount: points.filter((point) => point.value !== null).length,
    recordCount: points.length,
    demonstrationCount: points.filter((point) => point.demonstration).length,
  };
}

/**
 * The warning a series carries when it is drawn partly from demonstrations.
 *
 * Returned rather than a boolean so the chart can say which records and how
 * many, and so the wording lives with the domain rules.
 */
export function demonstrationWeatherSeriesWarning(
  series: WeatherChartSeries,
): string | null {
  if (series.demonstrationCount === 0) return null;
  if (series.demonstrationCount === series.recordCount) {
    return "Every record on this chart is a demonstration. Nothing here was forecast by a service or measured on site.";
  }
  const word = series.demonstrationCount === 1 ? "record" : "records";
  return `${series.demonstrationCount} of ${series.recordCount} ${word} on this chart are demonstrations and are drawn hollow. Only the solid points came from a real reading or forecast.`;
}

/** A single record is a value, not a trend, so no line is drawn from one. */
export function needsTwoWeatherRecords(series: WeatherChartSeries): boolean {
  return series.measuredCount < 2;
}
