/**
 * Actual-outcome domain types, operator-facing validation and display shaping.
 *
 * Like `observations.ts`, this module is free of React and of `fetch` so the
 * rules an operator depends on can be unit-tested directly.
 *
 * One principle runs through it, and it is stronger here than anywhere else in
 * the project: **an unmeasured figure stays unmeasured, and is never rendered as
 * a number.** Module 15 exists so the model's accuracy can be measured against
 * reality, and a blank cell quietly displayed as `0`, or a missing comparison
 * summarised as `0%`, would feed straight into a validation report as though the
 * model had been right. So there are three distinct display states, and the
 * components are built to keep them apart:
 *
 * - a plain absent figure renders as `—`;
 * - a comparison that could not be made renders as "Unavailable" *with the
 *   reason the backend attached*, which is never the same as a figure of zero;
 * - an aggregate that has no comparable events renders as `—` with the count of
 *   events that were excluded from it.
 *
 * Nothing here computes a prediction or an error. The mass balance runs on the
 * server and this module only formats what it is told, so the numbers on screen
 * cannot drift from the arithmetic that produced them.
 */

/** The result of any harvest. Mirrors the backend's HarvestOutcome enum. */
export type HarvestOutcome = "none" | "partial" | "full" | "lost";

export const HARVEST_LABELS: Record<HarvestOutcome, string> = {
  none: "Nothing harvested",
  partial: "Partial harvest",
  full: "Full harvest",
  lost: "Harvest lost",
};

/**
 * The five records an outcome is graded against.
 *
 * The union is mirrored from the backend's `OutcomeLinkKind`, so a link kind
 * added there without a matching label here is a type error rather than a blank
 * heading.
 */
export type OutcomeLinkKind =
  | "pan_state"
  | "weather_forecast"
  | "simulation_run"
  | "recommendation"
  | "operator_decision";

export const LINK_LABELS: Record<OutcomeLinkKind, string> = {
  pan_state: "Pan state before the event",
  weather_forecast: "Forecast covering the event",
  simulation_run: "Mass-balance run behind the prediction",
  recommendation: "Recommendation in force",
  operator_decision: "Operator decision recorded",
};

/**
 * One figure a comparison is still waiting on, and why.
 *
 * The reason is the important half. A null error with no explanation is
 * indistinguishable from a bug, and an operator who cannot tell the difference
 * has no reason to go back and measure.
 */
export type MissingOutcomeInput = {
  field: string;
  reason: string;
};

/**
 * One predicted-against-actual comparison for a single event.
 *
 * `is_comparable` is false for a comparison that could not be made. That is not
 * the same as an error of zero, and the components must not render it as one.
 */
export type OutcomeErrorTerms = {
  predicted: number | null;
  /** The measured figure, which the backend reads from the outcome itself. */
  actual: number | null;
  predicted_from: string;
  predicted_from_label: string;
  unit: string;
  signed_error: number | null;
  absolute_error: number | null;
  percentage_error_percent: number | null;
  percentage_error_reason: string | null;
  is_comparable: boolean;
  missing_inputs: MissingOutcomeInput[];
  missing_fields: string[];
};

/** A pointer to a record an outcome was graded against, plus its details. */
export type OutcomeLink = {
  kind: OutcomeLinkKind;
  record_id: string | null;
  timestamp: string | null;
  summary: string;
  is_resolved: boolean;
  salinity_g_l: number | null;
  water_depth_cm: number | null;
  rainfall_mm: number | null;
  detail: string | null;
};

/** All five links, always all five, whether or not a record was found. */
export type OutcomeLinkSet = {
  pan_state: OutcomeLink | null;
  weather_forecast: OutcomeLink | null;
  simulation_run: OutcomeLink | null;
  recommendation: OutcomeLink | null;
  operator_decision: OutcomeLink | null;
};

export type OutcomeLinkKindSlot = keyof OutcomeLinkSet;

/** The link slots, in the order a reader wants to see them. */
export const LINK_KINDS: OutcomeLinkKindSlot[] = [
  "pan_state",
  "weather_forecast",
  "simulation_run",
  "recommendation",
  "operator_decision",
];

/** A recorded event, exactly as the API returns it. */
export type EventOutcome = {
  outcome_id: string;
  pan_id: string;
  event_id: string;
  timestamp: string;
  created_at: string;
  forecast_rainfall_mm: number | null;
  actual_rainfall_mm: number | null;
  pre_event_salinity_g_l: number | null;
  pre_event_depth_cm: number | null;
  post_event_salinity_g_l: number | null;
  post_event_depth_cm: number | null;
  operator_action: string | null;
  harvest_outcome: HarvestOutcome | null;
  simulation_error: OutcomeErrorTerms;
  forecast_simulation_error: OutcomeErrorTerms;
  rainfall_forecast_error: OutcomeErrorTerms;
  links: OutcomeLinkSet;
  has_actual_measurement: boolean;
  is_comparable: boolean;
};

export type EventOutcomeListResponse = {
  items: EventOutcome[];
  total: number;
  limit: number;
  offset: number;
  earliest_timestamp: string | null;
  latest_timestamp: string | null;
};

/**
 * Aggregate error over the events that could be compared.
 *
 * `compared` and `not_comparable` are read as a pair. A mean absolute error over
 * two of twenty events is meaningless on its own, and the only way to keep it
 * from being quoted on its own is to always show what it excluded.
 */
export type ErrorStatistics = {
  label: string;
  unit: string;
  compared: number;
  not_comparable: number;
  coverage_percent: number | null;
  mean_absolute_error: number | null;
  mean_absolute_error_reason: string | null;
  bias: number | null;
  bias_reason: string | null;
  largest_absolute_error: number | null;
};

export type OutcomeComparisonSummary = {
  pan_id: string | null;
  total_outcomes: number;
  simulation_error: ErrorStatistics;
  forecast_simulation_error: ErrorStatistics;
  rainfall_forecast_error: ErrorStatistics;
  events_missing_actual_salinity: number;
  events_with_operator_action: number;
};

/**
 * Payload accepted by `POST /api/outcomes/`.
 *
 * There is deliberately no field for a predicted salinity, an error or a mean
 * absolute error. The backend forbids unknown keys, and that refusal is the
 * mechanism by which a client cannot grade the model on its own account.
 */
export type EventOutcomeRegistration = {
  pan_id: string;
  event_id: string;
  timestamp?: string;
  forecast_rainfall_mm: number | null;
  actual_rainfall_mm: number | null;
  pre_event_salinity_g_l: number | null;
  pre_event_depth_cm: number | null;
  post_event_salinity_g_l: number | null;
  post_event_depth_cm: number | null;
  operator_action?: string | null;
  harvest_outcome?: HarvestOutcome;
};

/**
 * Plausibility bounds for the figures an operator types.
 *
 * These match the backend's ranges exactly, so a value the form accepts is a
 * value the API will not reject on a range. They are wide on purpose: the point
 * is to catch a typed digit, not to rule out an unusual but real pan.
 */
export const OUTCOME_RANGES = {
  rainfall_mm: { label: "Rainfall", unit: "mm", min: 0, max: 1000 },
  salinity_g_l: { label: "Salinity", unit: "g/L", min: 0, max: 500 },
  depth_cm: { label: "Water depth", unit: "cm", min: 0, max: 1000 },
} as const;

export type OutcomeRangeKey = keyof typeof OUTCOME_RANGES;

/**
 * Maps a form field onto the quantity it measures.
 *
 * The form asks for "actual rainfall" and "forecast rainfall"; both are rainfall.
 * Keeping the mapping in one place means the range check cannot drift between
 * them, and a hint about a zero reading can be written once. The literal keys are
 * preserved so `values[field]` stays type-checked against the form.
 */
export const OUTCOME_FIELD_RANGES = {
  forecast_rainfall_mm: {
    range: "rainfall_mm",
    label: "Forecast rainfall",
    zeroIsMeasurement: true,
  },
  actual_rainfall_mm: {
    range: "rainfall_mm",
    label: "Rainfall that fell",
    zeroIsMeasurement: true,
  },
  pre_event_salinity_g_l: {
    range: "salinity_g_l",
    label: "Salinity before the event",
    zeroIsMeasurement: false,
  },
  post_event_salinity_g_l: {
    range: "salinity_g_l",
    label: "Salinity after the event",
    zeroIsMeasurement: false,
  },
  pre_event_depth_cm: {
    range: "depth_cm",
    label: "Water depth before the event",
    zeroIsMeasurement: false,
  },
  post_event_depth_cm: {
    range: "depth_cm",
    label: "Water depth after the event",
    zeroIsMeasurement: false,
  },
} as const satisfies Record<
  string,
  { range: OutcomeRangeKey; label: string; zeroIsMeasurement: boolean }
>;

export type OutcomeMeasurementField = keyof typeof OUTCOME_FIELD_RANGES;

export const OUTCOME_MEASUREMENT_FIELDS = Object.keys(
  OUTCOME_FIELD_RANGES,
) as OutcomeMeasurementField[];

/**
 * The fields that make an event worth recording.
 *
 * Mirrors the backend's `OUTCOME_ACTUAL_FIELDS`. An event where all of these are
 * absent describes nothing that happened and cannot be compared with anything,
 * so the form refuses it rather than storing an empty row.
 */
export const OUTCOME_ACTUAL_FIELDS: OutcomeMeasurementField[] = [
  "actual_rainfall_mm",
  "post_event_salinity_g_l",
  "post_event_depth_cm",
];

export const MAX_EVENT_ID_LENGTH = 64;
export const MAX_OPERATOR_ACTION_LENGTH = 2000;

/** Client clock tolerance, matching the backend's MAX_FUTURE_SKEW. */
export const MAX_FUTURE_SKEW_MINUTES = 5;

export type OutcomeFormValues = {
  /** `datetime-local` text. Blank records the moment of saving. */
  timestamp: string;
  event_id: string;
  forecast_rainfall_mm: string;
  actual_rainfall_mm: string;
  pre_event_salinity_g_l: string;
  pre_event_depth_cm: string;
  post_event_salinity_g_l: string;
  post_event_depth_cm: string;
  /** "" means "not reported", which is distinct from the "none" harvest. */
  harvest_outcome: "" | HarvestOutcome;
  operator_action: string;
};

export const EMPTY_OUTCOME_FORM: OutcomeFormValues = {
  timestamp: "",
  event_id: "",
  forecast_rainfall_mm: "",
  actual_rainfall_mm: "",
  pre_event_salinity_g_l: "",
  pre_event_depth_cm: "",
  post_event_salinity_g_l: "",
  post_event_depth_cm: "",
  harvest_outcome: "",
  operator_action: "",
};

export type OutcomeFormErrors = Partial<
  Record<keyof OutcomeFormValues, string>
> & { form?: string };

const NUMERIC_PATTERN = /^-?\d*\.?\d+$/;

/**
 * Validates one optional figure.
 *
 * Blank is not an error: it means "not measured". A present value must be a
 * finite number inside the plausibility range for that quantity. Zero passes,
 * because a gauge reading of zero and a dry pan measuring zero depth are
 * measurements, not gaps.
 */
export function validateOutcomeMeasurement(
  raw: string,
  range: OutcomeRangeKey,
): { value: number | null; error: string | null } {
  const { label, unit, min, max } = OUTCOME_RANGES[range];
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

/** Validates a `datetime-local` value and returns it as an ISO instant. */
export function validateOutcomeTimestamp(
  raw: string,
  { now = new Date() }: { now?: Date } = {},
): { value: string | null; error: string | null } {
  const trimmed = raw.trim();
  if (trimmed === "") return { value: null, error: null };
  const parsed = new Date(trimmed);
  if (Number.isNaN(parsed.getTime())) {
    return { value: null, error: "Event time could not be read." };
  }
  const latestAcceptable = now.getTime() + MAX_FUTURE_SKEW_MINUTES * 60_000;
  if (parsed.getTime() > latestAcceptable) {
    return {
      value: null,
      error:
        "Event time is in the future. An outcome describes something that has already happened.",
    };
  }
  return { value: parsed.toISOString(), error: null };
}

export function validateEventId(
  raw: string,
): { value: string | null; error: string | null } {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return {
      value: null,
      error:
        "Name this event. Without a name the outcome cannot be grouped with the other events of the same storm.",
    };
  }
  if (trimmed.length > MAX_EVENT_ID_LENGTH) {
    return {
      value: null,
      error: `Event name must be ${MAX_EVENT_ID_LENGTH} characters or fewer.`,
    };
  }
  return { value: trimmed, error: null };
}

export function validateOperatorAction(
  raw: string,
): { value: string | null; error: string | null } {
  const trimmed = raw.trim();
  if (trimmed === "") return { value: null, error: null };
  if (trimmed.length > MAX_OPERATOR_ACTION_LENGTH) {
    return {
      value: null,
      error: `Operator action must be ${MAX_OPERATOR_ACTION_LENGTH} characters or fewer.`,
    };
  }
  return { value: trimmed, error: null };
}

/**
 * Full-form validation for recording an event.
 *
 * The prediction is not validated here and cannot be supplied here. Everything
 * this returns is something the operator measured or did.
 */
export function validateOutcome(
  values: OutcomeFormValues,
  { panId, now }: { panId: string; now?: Date },
): { payload: EventOutcomeRegistration | null; errors: OutcomeFormErrors } {
  const errors: OutcomeFormErrors = {};

  const timestamp = validateOutcomeTimestamp(values.timestamp, { now });
  if (timestamp.error) errors.timestamp = timestamp.error;

  const eventId = validateEventId(values.event_id);
  if (eventId.error) errors.event_id = eventId.error;

  // Every measurement field is always present in the payload, as a number or as
  // an explicit null. Partial would let one be dropped entirely, and a dropped
  // measurement is indistinguishable from one that was never asked for.
  const measurements = {} as Record<OutcomeMeasurementField, number | null>;
  for (const field of OUTCOME_MEASUREMENT_FIELDS) {
    const result = validateOutcomeMeasurement(
      values[field],
      OUTCOME_FIELD_RANGES[field].range,
    );
    if (result.error) errors[field] = result.error;
    measurements[field] = result.value;
  }

  const action = validateOperatorAction(values.operator_action);
  if (action.error) errors.operator_action = action.error;

  /**
   * An event has to say something about what happened.
   *
   * A measured figure, a harvest result or a description of what the operator
   * did will all do. An event with none of the three records nothing, and a row
   * of nulls in the accuracy statistics would be a way to inflate the count of
   * events without contributing any evidence to it.
   */
  const recordedSomething =
    OUTCOME_ACTUAL_FIELDS.some((field) => measurements[field] !== null) ||
    values.harvest_outcome !== "" ||
    action.value !== null;

  if (!recordedSomething && Object.keys(errors).length === 0) {
    errors.form =
      "Record at least one measured figure, a harvest result, or what you did about the event. An event that says nothing about what happened cannot be compared with anything.";
  }

  if (Object.keys(errors).length > 0) {
    return { payload: null, errors };
  }

  const payload: EventOutcomeRegistration = {
    pan_id: panId,
    event_id: eventId.value as string,
    ...measurements,
  };
  if (timestamp.value !== null) payload.timestamp = timestamp.value;
  if (values.harvest_outcome !== "") {
    payload.harvest_outcome = values.harvest_outcome;
  }
  if (action.value !== null) payload.operator_action = action.value;
  return { payload, errors: {} };
}

/** Seeds the form for a new event. */
export function emptyOutcomeForm(): OutcomeFormValues {
  return { ...EMPTY_OUTCOME_FORM };
}

/**
 * Clears the form after a save, keeping the event name.
 *
 * The name survives because a storm is usually recorded against several pans in
 * sequence, and retyping it invites a typo that silently splits one storm's
 * events into two groups. Every measured figure is dropped, so the next entry
 * starts from a blank record rather than from the previous one, which would let
 * a duplicated entry look deliberate.
 */
export function resetOutcomeForm(values: OutcomeFormValues): OutcomeFormValues {
  return { ...EMPTY_OUTCOME_FORM, event_id: values.event_id };
}

// -- display shaping ------------------------------------------------------

const NUMBER_FORMAT = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
});

const FULL_TIMESTAMP = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "UTC",
});

/** The dash stands for "there is no figure here", never for zero. */
export const MISSING_DISPLAY = "—";

/** A measurement with its unit, or the dash when it was never measured. */
export function formatOutcomeValue(
  value: number | null,
  unit: string,
): string {
  if (value === null) return MISSING_DISPLAY;
  return `${NUMBER_FORMAT.format(value)} ${unit}`;
}

/** A bare number, for use inside a table cell that names the unit in its header. */
export function formatOutcomeNumber(value: number | null): string {
  if (value === null) return MISSING_DISPLAY;
  return NUMBER_FORMAT.format(value);
}

/**
 * A signed error, so the direction of the mistake is visible.
 *
 * The sign is always shown, including on a value that rounds to zero, because
 * "+0.00" and "−0.00" mean a model that was very slightly high and one that was
 * very slightly low, and the distinction is the entire point of keeping the
 * error signed.
 */
export function formatSignedError(
  value: number | null,
  { unit = "" }: { unit?: string } = {},
): string {
  if (value === null) return MISSING_DISPLAY;
  const formatted = NUMBER_FORMAT.format(value);
  const signed = value > 0 ? `+${formatted}` : formatted;
  return unit ? `${signed} ${unit}` : signed;
}

export function formatPercentage(value: number | null): string {
  if (value === null) return MISSING_DISPLAY;
  return `${NUMBER_FORMAT.format(value)}%`;
}

/**
 * The one-line explanation of a comparison that could not be made.
 *
 * Prefers the reasons the backend attached, because those name the specific
 * record or entry that would supply the missing figure. Falls back to the names
 * of the absent fields so the line is never empty.
 */
export function describeMissing(inputs: readonly MissingOutcomeInput[]): string {
  if (inputs.length === 0) return "Not measured, so there is nothing to compare against.";
  return inputs.map((entry) => entry.reason).join(" ");
}

/** Reads a link out of the set by its slot name. */
export function readLink(
  links: OutcomeLinkSet,
  slot: OutcomeLinkKindSlot,
): OutcomeLink | null {
  return links[slot] ?? null;
}

export function formatEventTimestamp(value: string | null): string {
  if (!value) return MISSING_DISPLAY;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return MISSING_DISPLAY;
  return `${FULL_TIMESTAMP.format(parsed)} UTC`;
}

/**
 * The coverage phrase that must accompany every mean.
 *
 * "MAE 2.4 g/L" on its own invites the reading that the model was within 2.4 g/L
 * of the pan on every event, which is not what a mean says. The excluded count
 * is what stops that, so it is generated here rather than left to each component
 * to remember.
 */
export function describeCoverage(statistics: ErrorStatistics): string {
  if (statistics.compared === 0) {
    return statistics.not_comparable === 1
      ? "No event in scope could be compared. The single event is excluded rather than counted as a zero error."
      : `No event in scope could be compared. All ${statistics.not_comparable} events are excluded rather than counted as zero errors.`;
  }
  const excluded =
    statistics.not_comparable === 0
      ? "every event in scope was compared"
      : statistics.not_comparable === 1
        ? "1 event is excluded because it has no measurement"
        : `${statistics.not_comparable} events are excluded because they have no measurement`;
  return `Mean over ${statistics.compared} compared ${statistics.compared === 1 ? "event" : "events"} — ${excluded}.`;
}

/**
 * A readable headline for one statistic, for the summary tiles.
 *
 * Returns the dash when the mean does not exist, because there is no honest way
 * to compress "no event in scope was comparable" into a tile-sized number.
 */
export function formatMeanAbsoluteError(statistics: ErrorStatistics): string {
  if (statistics.mean_absolute_error === null) return MISSING_DISPLAY;
  return NUMBER_FORMAT.format(statistics.mean_absolute_error);
}
