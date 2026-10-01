/**
 * Digital-twin domain types, labels and formatting.
 *
 * Free of React and of `fetch`, so the rules the panel depends on can be unit
 * tested directly and reused by the state card and the snapshot table.
 *
 * The organising rule is that the twin reports rather than fills. A quantity the
 * server could not source arrives as `null`, and this module keeps that visible
 * as an explicit "unavailable" rather than collapsing it into a dash that looks
 * the same as a zero. A measured `0` and a never-measured quantity are different
 * facts and are rendered differently, because only one of them is evidence.
 *
 * Ages are treated as evidence in their own right. A twin assembled moments ago
 * from last month's readings is not current, so the age of each input is shown
 * next to the value it produced and never folded into the assembly time.
 */

/** Mirrors the backend's ResearchDataSource. */
export type ObservationSource = "manual" | "sensor" | "import";

/** Mirrors the backend's WeatherSource. */
export type TwinWeatherSource = "manual" | "weather_api" | "sensor";

/** Which kind of record would supply a quantity the twin is missing. */
export type TwinInputKind = "observation" | "weather" | "weather_forecast";

export const OBSERVATION_SOURCE_LABELS: Record<ObservationSource, string> = {
  manual: "Entered by hand",
  sensor: "Sensor gateway",
  import: "Imported file",
};

export const TWIN_WEATHER_SOURCE_LABELS: Record<TwinWeatherSource, string> = {
  manual: "Entered by hand",
  weather_api: "Weather API",
  sensor: "Sensor gateway",
};

/** One record the twin was assembled from, with its own age. */
export type TwinInput = {
  id: string | null;
  timestamp: string | null;
  source: string | null;
  age_seconds: number | null;
};

/** A quantity the twin could not source, and why. */
export type MissingTwinInput = {
  field: string;
  reason: string;
  expected_from: string;
};

/** A pan's current known state, exactly as the API returns it. */
export type DigitalTwinState = {
  twin_id: string;
  pan_id: string;
  pan_name: string | null;
  /** When this state was assembled. Not the age of the evidence. */
  timestamp: string;
  provenance: string;
  twin_version: string;

  salinity_g_l: number | null;
  water_depth_cm: number | null;
  brine_temperature_c: number | null;
  ambient_temperature_c: number | null;
  humidity_percent: number | null;
  pan_area_m2: number | null;
  location: string | null;
  forecast_rainfall_mm: number | null;
  rainfall_probability_percent: number | null;

  observation: TwinInput | null;
  weather: TwinInput | null;
  weather_forecast: TwinInput | null;

  is_complete: boolean;
  missing_inputs: MissingTwinInput[];
  missing_fields: string[];
  quantities: string[];
};

export type DigitalTwinHistoryResponse = {
  pan_id: string;
  items: DigitalTwinState[];
  total: number;
  limit: number;
  offset: number;
  earliest_timestamp: string | null;
  latest_timestamp: string | null;
};

export type DigitalTwinRefreshResponse = {
  state: DigitalTwinState;
  stored: boolean;
  changed: boolean;
  previous_twin_id: string | null;
};

/** The quantities the twin reports, in the order they are displayed. */
export const TWIN_DISPLAY_QUANTITIES = [
  "salinity_g_l",
  "water_depth_cm",
  "brine_temperature_c",
  "ambient_temperature_c",
  "humidity_percent",
  "forecast_rainfall_mm",
  "rainfall_probability_percent",
] as const;

export type TwinDisplayQuantity = (typeof TWIN_DISPLAY_QUANTITIES)[number];

type QuantityMeta = {
  label: string;
  unit?: string;
  digits?: number;
  /** Which contributing record supplies it, shown as a tooltip-ish hint. */
  from: TwinInputKind;
};

export const TWIN_QUANTITY_META: Record<TwinDisplayQuantity, QuantityMeta> = {
  salinity_g_l: {
    label: "Brine salinity",
    unit: "g/L",
    digits: 1,
    from: "observation",
  },
  water_depth_cm: {
    label: "Water depth",
    unit: "cm",
    digits: 1,
    from: "observation",
  },
  brine_temperature_c: {
    label: "Brine temperature",
    unit: "°C",
    digits: 1,
    from: "observation",
  },
  ambient_temperature_c: {
    label: "Ambient temperature",
    unit: "°C",
    digits: 1,
    from: "weather",
  },
  humidity_percent: {
    label: "Humidity",
    unit: "%",
    digits: 0,
    from: "weather",
  },
  forecast_rainfall_mm: {
    label: "Forecast rainfall",
    unit: "mm",
    digits: 1,
    from: "weather_forecast",
  },
  rainfall_probability_percent: {
    label: "Rainfall probability",
    unit: "%",
    digits: 0,
    from: "weather_forecast",
  },
};

/** Shown wherever an input's origin is displayed. */
export const TWIN_INPUT_LABELS: Record<TwinInputKind, string> = {
  observation: "Pan observation",
  weather: "Weather observation",
  weather_forecast: "Weather forecast",
};

/**
 * A quantity as the UI needs it: the number if one exists, otherwise the reason
 * it is missing. Keeping the two apart here means no component has to re-derive
 * whether a dash means zero or means nothing.
 */
export type TwinQuantityView = {
  field: TwinDisplayQuantity;
  label: string;
  unit?: string;
  /** True only when a real value was recorded. A measured zero is true. */
  available: boolean;
  /** The formatted figure, or null when unavailable. */
  display: string | null;
  reason: string | null;
  from: TwinInputKind;
};

export function buildQuantityViews(
  state: DigitalTwinState,
): TwinQuantityView[] {
  return TWIN_DISPLAY_QUANTITIES.map((field) => {
    const meta = TWIN_QUANTITY_META[field];
    const value = state[field];
    const available = value !== null && value !== undefined;
    const missing = state.missing_inputs.find((entry) => entry.field === field);
    return {
      field,
      label: meta.label,
      unit: meta.unit,
      available,
      display: available ? formatQuantity(value as number, meta.digits ?? 1) : null,
      reason: available ? null : (missing?.reason ?? "Not available."),
      from: meta.from,
    };
  });
}

/** Format a figure, keeping a real zero as "0" rather than blanking it. */
export function formatQuantity(value: number, digits = 1): string {
  if (!Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}

const FULL_TIMESTAMP = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "UTC",
});

export function formatTwinTimestamp(value: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  return `${FULL_TIMESTAMP.format(parsed)} UTC`;
}

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/**
 * An age in words, coarse on purpose.
 *
 * A twin is judged against the question "is this current?", and "46 hours ago"
 * answers that better than "165,600 seconds". Sub-minute ages read as "just now"
 * because the exact figure carries no meaning to an operator.
 */
export function formatAge(seconds: number | null): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) {
    return "—";
  }
  if (seconds < 0) return "—";
  if (seconds < MINUTE) return "just now";
  if (seconds < HOUR) {
    const minutes = Math.floor(seconds / MINUTE);
    return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  }
  if (seconds < DAY) {
    const hours = Math.floor(seconds / HOUR);
    return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  }
  const days = Math.floor(seconds / DAY);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

/** Thresholds at which an input stops being treated as current evidence. */
export const FRESHNESS = {
  /** Under a day: current enough to act on. */
  fresh: DAY,
  /** Under a week: ageing, still usable but no longer current. */
  ageing: 7 * DAY,
} as const;

export type Freshness = {
  tone: "active" | "caution" | "danger";
  label: string;
};

/**
 * Grade an input's age.
 *
 * The twin is assembled on every read, so its own timestamp says nothing about
 * whether the pan's condition is known. This grades the *evidence*, and the
 * thresholds are stated rather than implied so a reader can disagree with them.
 */
export function describeFreshness(ageSeconds: number | null): Freshness {
  if (ageSeconds === null || ageSeconds === undefined) {
    return { tone: "danger", label: "No reading" };
  }
  if (ageSeconds < FRESHNESS.fresh) {
    return { tone: "active", label: "Current" };
  }
  if (ageSeconds < FRESHNESS.ageing) {
    return { tone: "caution", label: "Ageing" };
  }
  return { tone: "danger", label: "Stale" };
}

/** Plain-language label for an input's provenance. */
export function describeTwinSource(
  kind: TwinInputKind,
  source: string | null,
): string {
  if (!source) return TWIN_INPUT_LABELS[kind];
  if (kind === "observation") {
    return OBSERVATION_SOURCE_LABELS[source as ObservationSource] ?? source;
  }
  return TWIN_WEATHER_SOURCE_LABELS[source as TwinWeatherSource] ?? source;
}

/**
 * A one-line account of how far the twin can be trusted, for the card header.
 *
 * Leads with the weakest link rather than a count, because "3 of 7 quantities
 * unavailable" is a number to interpret while "no observation recorded" is a
 * statement about the pan.
 */
export function summarizeAvailability(
  state: DigitalTwinState,
): { headline: string; detail: string | null } {
  if (state.missing_inputs.length === 0) {
    return {
      headline: "Every quantity is sourced from a stored record.",
      detail: null,
    };
  }
  const [first] = state.missing_inputs;
  const remaining = state.missing_inputs.length - 1;
  const detail =
    remaining > 0
      ? `${remaining} further ${remaining === 1 ? "quantity is" : "quantities are"} unavailable.`
      : null;
  return { headline: first.reason, detail };
}

/** The oldest contributing input, which is what limits the twin's currency. */
export function oldestInputAge(state: DigitalTwinState): number | null {
  const ages = [state.observation, state.weather, state.weather_forecast]
    .map((input) => input?.age_seconds)
    .filter((age): age is number => typeof age === "number");
  return ages.length === 0 ? null : Math.max(...ages);
}

/**
 * Whether this twin may be described as current.
 *
 * Requires every quantity to be present *and* the evidence behind it to be
 * within the ageing window, so a complete twin built on last month's readings
 * does not quietly pass as up to date.
 */
export function isTwinCurrent(state: DigitalTwinState): boolean {
  if (!state.is_complete) return false;
  const age = oldestInputAge(state);
  return age !== null && age < FRESHNESS.ageing;
}
