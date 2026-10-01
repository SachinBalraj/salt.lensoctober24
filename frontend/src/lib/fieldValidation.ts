/**
 * Module 20: field validation of reported rainfall events — types and display shaping.
 *
 * Like the other `lib/*` modules, this file is free of React and of `fetch`, so
 * the rules a reader depends on can be unit-tested directly.
 *
 * The one idea this module enforces is that **a reported figure is never shown
 * as proof**. Every comparison carries three flags that a reader must see next
 * to the numbers:
 *
 * - `intervention_affected` — an action moved the pan outside the rainfall-only
 *   model, so a mismatch is expected and is not explained by the rain alone;
 * - `unknown_intervention_volumes` — a transfer quantity was not recorded, so
 *   the balance cannot even estimate the action's effect;
 * - `independently_verified` — a person confirmed the figures against a second
 *   source. It is false by default and is never set by the API.
 *
 * Two more facts a reader needs before trusting any number are carried here as
 * well. First, instants are published twice — in UTC and in the site's local zone,
 * each labelled — so a reader never has to convert a converted number, and the
 * module never re-times an instant to "help". Second, provenance is explicit:
 * `event_milestone` says what the event instant marks (`unknown` until an
 * operator states it), `pre_event_observation_id` names the stored record a
 * snapshot was reconciled against, and the `*_timestamp_verified` flags say
 * whether an instant is corroborated by a record or only asserted. `corrections`
 * is the append-only audit history of every reconciliation applied.
 */

export type VerificationState = "reported" | "verified";

/** What the event instant is asserted to mark. Never inferred by the API. */
export type EventMilestone =
  | "rainfall_start"
  | "rainfall_end"
  | "event_logged"
  | "unknown";

export type InterventionKind =
  | "brine_transfer_out"
  | "brine_transfer_in"
  | "fresh_water_added"
  | "harvest"
  | "other";

export type InterventionDirection = "in" | "out" | "none";

export type FieldMeasurement = {
  salinity_g_l: number | null;
  water_depth_cm: number | null;
  timestamp: string | null;
  recorded_timezone: string | null;
  source: string | null;
  observation_id: string | null;
  observer: string | null;
  evidence_source: string | null;
  timestamp_verified: boolean;
  note: string | null;
};

/** One entry in an event's append-only reconciliation history. */
export type CorrectionRecord = {
  correction_id: string;
  field: string;
  previous_value: string | null;
  new_value: string | null;
  reason: string;
  corrected_at: string;
  corrected_by: string | null;
  evidence_source: string | null;
  evidence_record_id: string | null;
};

export type FieldIntervention = {
  intervention_id: string;
  kind: InterventionKind;
  direction: InterventionDirection;
  performed_at: string;
  volume_m3: number | null;
  volume_unit: "m3";
  description: string | null;
  source: string | null;
};

export type RainfallEvent = {
  event_id: string;
  pan_id: string;
  name: string | null;
  event_timestamp: string;
  event_milestone: EventMilestone;
  site_timezone: string | null;
  reported_at: string;
  reported_by: string | null;
  verification_state: VerificationState;
  actual_rainfall_mm: number;
  rainfall_timestamp: string;
  rainfall_source: string | null;
  rainfall_timestamp_evidence: string | null;
  rainfall_timestamp_verification: RainfallTimestampVerification;
  superseded_rainfall_timestamps: SupersededRainfallTimestamp[];
  weather_observation_id: string | null;
  forecast_rainfall_mm: number | null;
  pre_event: FieldMeasurement;
  post_event: FieldMeasurement;
  interventions: FieldIntervention[];
  corrections: CorrectionRecord[];
  notes: string | null;
  created_at: string;
};

/**
 * One preserved disagreement between two accounts of when the rain fell.
 */
export type RainfallTimestampConflict = {
  conflict_id: string;
  stored_timestamp_utc: string | null;
  stored_timestamp_ist: string | null;
  reported_timestamp_utc: string | null;
  reported_timestamp_ist: string | null;
  reported_source: string;
  offset_seconds: number;
  offset_human: string;
  status: "unresolved" | "resolved";
  detected_at: string;
  recorded_by: string | null;
  reason: string;
  resolution_note: string | null;
  resolved_at: string | null;
};

/**
 * How far the event's rainfall *instant* is backed.
 *
 * Separate from `VerificationState` because they answer different questions.
 * `not_recorded` means nobody has sourced the instant; `operator_reported` means
 * a named document records the time but nothing measured it. `record_backed`
 * would be the only state in which the instant is an observation rather than an
 * assertion, and nothing in the API can produce it.
 */
export type RainfallTimestampVerification =
  | "not_recorded"
  | "operator_reported"
  | "record_backed";

export const RAINFALL_TIMESTAMP_VERIFICATION_LABELS: Record<
  RainfallTimestampVerification,
  string
> = {
  not_recorded: "No source recorded",
  operator_reported: "Operator-reported",
  record_backed: "Backed by a stored record",
};

/**
 * A rainfall instant the event used to carry.
 *
 * `meaning` is only ever `unknown`: the API does not guess what a displaced time
 * was meant to mark, and a non-null `supporting_record` is the exception that
 * proves the field is real. Rendered as history, never as a measurement.
 */
export type SupersededRainfallTimestamp = {
  superseded_id: string;
  timestamp_utc: string | null;
  timestamp_ist: string | null;
  timezone_label: string | null;
  source_claim: string | null;
  origin: string;
  supporting_record: string | null;
  meaning: "unknown";
  superseded_at: string;
  superseded_by: string | null;
  reason: string;
  conflict_id: string | null;
};

/**
 * How far an event is from being honestly called reconciled.
 *
 * `blocked_by_evidence` outranks everything: while two records disagree about
 * when the rain fell and only one is in force, the event is blocked whatever
 * else happens to be in place.
 */
export type ReconciliationState =
  | "complete"
  | "incomplete"
  | "blocked_by_evidence";

export const RECONCILIATION_LABELS: Record<ReconciliationState, string> = {
  complete: "Reconciled",
  incomplete: "Not reconciled",
  blocked_by_evidence: "Blocked by evidence",
};

/**
 * A second account of the rain's timing, which the API records rather than
 * adopts. The event keeps the timestamp it was filed with.
 */
export type RainfallConflictDraft = {
  reported_timestamp: string;
  reported_timezone: string;
  reported_source: string;
  reason: string;
  recorded_by: string;
};

/**
 * A controlled replacement of the event's rainfall instant.
 *
 * Shaped by what the caller has to account for: the new instant and the document
 * that dates it, plus the origin of the value being displaced. There is no
 * `verification` field on purpose — a document that records a time cannot make it
 * a measured one, so the draft does not offer a claim the API would refuse.
 */
export type RainfallResolutionDraft = {
  timestamp: string;
  timezone: string;
  evidence_source: string;
  superseded_origin: string;
  superseded_source_claim: string;
  superseded_supporting_record: string;
  reason: string;
  corrected_by: string;
};

export type FieldValidationComparison = {
  event_id: string;
  pan_id: string;
  pan_name: string | null;
  event_timestamp: string | null;
  event_milestone: EventMilestone;
  site_timezone_label: string;
  event_timestamp_utc: string | null;
  event_timestamp_ist: string | null;
  rainfall_timestamp_utc: string | null;
  rainfall_timestamp_ist: string | null;
  /** The document that dates the instant in force, when one is named. */
  rainfall_timestamp_evidence: string | null;
  rainfall_timestamp_verification: RainfallTimestampVerification;
  /**
   * True only when a stored instrument record backs the instant. A well-sourced
   * operator report leaves this false, which is the point: a notebook entry is
   * not a rain-gauge reading.
   */
  rainfall_timestamp_observed: boolean;
  superseded_rainfall_timestamps: SupersededRainfallTimestamp[];
  reported_by: string | null;
  notes: string | null;
  model_name: string;
  model_disclaimer: string;
  actual_rainfall_mm: number;
  forecast_rainfall_mm: number | null;
  predicted_salinity_g_l: number | null;
  predicted_depth_cm: number | null;
  reported_salinity_g_l: number | null;
  reported_depth_cm: number | null;
  salinity_abs_diff_g_l: number | null;
  depth_abs_diff_cm: number | null;
  pre_event_salinity_g_l: number | null;
  pre_event_depth_cm: number | null;
  pre_event_timestamp_utc: string | null;
  pre_event_timestamp_ist: string | null;
  pre_event_observation_id: string | null;
  pre_event_observer: string | null;
  pre_event_timestamp_verified: boolean;
  post_event_timestamp_utc: string | null;
  post_event_timestamp_ist: string | null;
  post_event_observation_id: string | null;
  post_event_observer: string | null;
  post_event_timestamp_verified: boolean;
  weather_observation_id: string | null;
  corrections: CorrectionRecord[];
  correction_count: number;
  rainfall_timestamp_conflicts: RainfallTimestampConflict[];
  unresolved_conflict_count: number;
  reconciliation_state: ReconciliationState;
  intervention_affected: boolean;
  unknown_intervention_volumes: boolean;
  independently_verified: boolean;
  verification_state: VerificationState;
  caveats: string[];
  assumptions: string[];
};

export type FieldValidationSummary = {
  total_events: number;
  compared_events: number;
  events_with_salinity: number;
  events_with_depth: number;
  intervention_affected_count: number;
  unknown_volume_count: number;
  verified_count: number;
  events_with_unknown_milestone: number;
  events_with_unlinked_pre_event: number;
  uncorroborated_post_event_timestamps: number;
  events_blocked_on_evidence: number;
  fully_reconciled_events: number;
  mean_abs_salinity_diff_g_l: number | null;
  mean_abs_depth_diff_cm: number | null;
  caveats: string[];
};

export type FieldValidationStatus = {
  module: string;
  event_count: number;
  ready: boolean;
  message: string;
};

/** A rainfall-event form payload, ready to post. */
export type RainfallEventDraft = {
  pan_id: string;
  name: string;
  event_timestamp: string;
  event_milestone: EventMilestone;
  reported_by: string;
  actual_rainfall_mm: number;
  rainfall_timestamp: string;
  rainfall_source: string;
  forecast_rainfall_mm: number | null;
  pre_salinity_g_l: number | null;
  pre_depth_cm: number | null;
  post_salinity_g_l: number | null;
  post_depth_cm: number | null;
  interventions: Array<{
    kind: InterventionKind;
    direction: InterventionDirection;
    performed_at: string;
    volume_m3: number | null;
    description: string;
  }>;
  notes: string;
};

/** The document paths a correction may reconcile, as the form offers them. */
export const CORRECTABLE_FIELDS = [
  "event_milestone",
  "pre_event_timestamp",
  "pre_event_observer",
  "pre_event_evidence",
  "post_event_timestamp",
  "post_event_observer",
  "post_event_evidence",
] as const;

export type CorrectableField = (typeof CORRECTABLE_FIELDS)[number];

/** A correction, as the reconciliation form collects it. */
export type EventCorrectionDraft = {
  field: CorrectableField;
  reason: string;
  event_milestone: EventMilestone | null;
  timestamp: string | null;
  recorded_timezone: string;
  observer: string;
  evidence_source: string;
  corrected_by: string;
};

/**
 * A pre-event snapshot being reconciled against a stored observation.
 *
 * Every field is required on purpose. The original record's own local time, the
 * zone it was written in, the observer and where the evidence lives are what turn
 * a reported figure into a traceable one; the module refuses the link without
 * them rather than accepting a bare observation id.
 */
export type PreEventLinkDraft = {
  observation_id: string;
  measured_at_local: string;
  measured_timezone: string;
  observer: string;
  evidence_source: string;
  reason: string;
  corrected_by: string;
};

/** A human label for what an event instant is asserted to mark. */
export const EVENT_MILESTONE_LABELS: Record<EventMilestone, string> = {
  rainfall_start: "Rainfall start",
  rainfall_end: "Rainfall end",
  event_logged: "When the report was logged",
  unknown: "Not established",
};

/** A human label for each correctable document path. */
export const CORRECTABLE_FIELD_LABELS: Record<CorrectableField, string> = {
  event_milestone: "What the event instant marks",
  pre_event_timestamp: "Pre-event measurement instant",
  pre_event_observer: "Pre-event observer",
  pre_event_evidence: "Pre-event evidence source",
  post_event_timestamp: "Post-event measurement instant",
  post_event_observer: "Post-event observer",
  post_event_evidence: "Post-event evidence source",
};

/** Whether a correction to this field must carry an evidence source. */
export function correctionNeedsEvidence(field: CorrectableField): boolean {
  return field === "pre_event_timestamp" || field === "post_event_timestamp";
}

/**
 * Whether a comparison's prediction is usable: a stackable bar can only show a
 * matched pair when both the predicted and the reported figure exist.
 */
export function isComparableSalinity(c: FieldValidationComparison): boolean {
  return c.predicted_salinity_g_l !== null && c.reported_salinity_g_l !== null;
}

export function isComparableDepth(c: FieldValidationComparison): boolean {
  return c.predicted_depth_cm !== null && c.reported_depth_cm !== null;
}

/** Absolute difference, rounded the way a dashboard reads it. */
export function displayDifference(value: number | null): string {
  if (value === null) return "—";
  return `${value.toFixed(2)}`;
}

// -- naming an event on a chart ---------------------------------------------

const EVENT_DATE_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "long",
  year: "numeric",
  // The instant is read in UTC so the calendar date cannot drift by a day for a
  // reader in another zone. This module never re-times an instant to "help".
  timeZone: "UTC",
});

/**
 * The calendar date an event falls on, as a reader would write it.
 *
 * `null` for a missing or unparseable instant rather than a guessed date: a chart
 * label is not the place to invent a day, and an event with no date still deserves
 * to be named.
 */
export function eventDateLabel(timestamp: string | null | undefined): string | null {
  if (!timestamp) return null;
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return null;
  return EVENT_DATE_FORMAT.format(parsed);
}

/**
 * A pan name with each word's first letter capitalised, and nothing else changed.
 *
 * Pan names are typed by operators, and the registry holds "north pan" where a
 * caption wants "North Pan". Upper-casing the first letter of each word is the
 * smallest edit that reads as a caption: it leaves "KZN Salt Works" and "Pan 3"
 * exactly as they were, and it never lower-cases anything the operator capitalised.
 */
export function captionCasePanName(panName: string | null | undefined): string | null {
  if (!panName) return null;
  const trimmed = panName.trim();
  if (trimmed === "") return null;
  return trimmed
    .split(/\s+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

/** Used when neither a pan name nor a date can be read for an event. */
const UNNAMED_EVENT = "Unnamed event";

/**
 * The label an event carries on a chart: its pan, and the day it happened.
 *
 * Built from the event's own record rather than written into the code, so a second
 * pan or a second day labels itself correctly instead of repeating the first. An
 * em dash separates the two halves because it stays a caption when a figure is
 * set two columns wide, where a hyphen reads as a minus sign.
 *
 * Whatever half is missing is simply left out, so a partial record still produces
 * a caption naming what it can rather than a caption with a hole in it.
 */
export function eventChartLabel(
  panName: string | null | undefined,
  eventTimestamp: string | null | undefined,
): string {
  const pan = captionCasePanName(panName);
  const date = eventDateLabel(eventTimestamp);
  if (pan && date) return `${pan} — ${date}`;
  return pan ?? date ?? UNNAMED_EVENT;
}

/**
 * The label naming what a set of differences is a mean of.
 *
 * One event is not a mean of one, and calling it one overstates it: "Mean |Δ|"
 * over a single sample implies an aggregate that was never computed. The
 * single-event label therefore names the figure for what it is — the difference
 * between two readings — and the multi-event label keeps the mean and carries the
 * sample count, so a reader can see how much the average rests on.
 */
export function differenceMetricLabel(
  metric: "salinity" | "depth",
  comparedEvents: number,
): { label: string; sampleCount: number | null } {
  if (comparedEvents > 1) {
    return {
      label: metric === "salinity" ? "Mean |Δ| salinity" : "Mean |Δ| depth",
      sampleCount: comparedEvents,
    };
  }
  if (comparedEvents === 1) {
    return {
      label:
        metric === "salinity"
          ? "Absolute salinity difference"
          : "Absolute depth difference",
      sampleCount: null,
    };
  }
  // Nothing to average, so the label makes no claim about an aggregate at all.
  return {
    label: metric === "salinity" ? "Salinity difference" : "Depth difference",
    sampleCount: null,
  };
}

/**
 * The note that must sit beneath the charts when one unverified,
 * intervention-affected event is the whole of the evidence.
 *
 * The wording is fixed because it is a claim about a specific state, and a claim
 * is only allowed where the state holds. `warrantsSingleEventNote` is what keeps
 * that honest: this text says an outbound transfer happened with an unrecorded
 * volume, so it is rendered only when exactly one event is on screen, it is not
 * independently verified, and an intervention's volume is missing. Every one of
 * those has to be true, or the note stays off and the ordinary caveats stand.
 */
export const SINGLE_EVENT_DIFFERENCE_NOTE =
  "Single operator-reported rainfall event. An outbound brine transfer occurred, but its volume is unknown. The displayed differences are descriptive and do not establish independently validated model accuracy.";

/** Whether :data:`SINGLE_EVENT_DIFFERENCE_NOTE` is true of this set of events. */
export function warrantsSingleEventNote(
  comparisons: readonly FieldValidationComparison[],
): boolean {
  if (comparisons.length !== 1) return false;
  const only = comparisons[0];
  return (
    !only.independently_verified &&
    only.intervention_affected &&
    only.unknown_intervention_volumes
  );
}

/** A human label for an intervention kind. */
export const INTERVENTION_KIND_LABELS: Record<InterventionKind, string> = {
  brine_transfer_out: "Brine transferred out",
  brine_transfer_in: "Brine transferred in",
  fresh_water_added: "Fresh water added",
  harvest: "Harvest",
  other: "Other",
};

/** A human label for an intervention direction. */
export const INTERVENTION_DIRECTION_LABELS: Record<InterventionDirection, string> = {
  in: "into the pan",
  out: "out of the pan",
  none: "no transfer",
};

export function interventionLabel(kind: InterventionKind, direction: InterventionDirection): string {
  return `${INTERVENTION_KIND_LABELS[kind]} (${INTERVENTION_DIRECTION_LABELS[direction]})`;
}

/** One instant as the two labelled lines a reader needs: UTC and local time. */
export type InstantPair = {
  utc: string;
  local: string;
  /** The zone the local column is rendered in, named so it is never assumed. */
  zoneLabel: string;
};

/**
 * Present an instant in both zones, or say plainly that there is none.
 *
 * The browser is never asked to convert the stored value: the backend publishes
 * both renderings from the one instant it holds, so a client-side timezone
 * conversion could only ever disagree with the record.
 */
export function instantPair(
  utc: string | null,
  local: string | null,
  zoneLabel: string,
): InstantPair | null {
  if (!utc && !local) return null;
  return { utc: utc ?? "—", local: local ?? "—", zoneLabel };
}

/** How a snapshot's instant stands: corroborated by a record, or only asserted. */
export function timestampStanding(
  verified: boolean,
  observationId: string | null,
): string {
  if (verified && observationId) return `corroborated by ${observationId}`;
  if (verified) return "corroborated by a stored record";
  return "retained as reported, not corroborated";
}

/** Whether a pre-event snapshot traces back to a stored observation. */
export function isPreEventTraceable(c: FieldValidationComparison): boolean {
  return c.pre_event_observation_id !== null;
}

/**
 * How the rainfall instant in force should be described beside its two zones.
 *
 * Kept as a function so the wording is testable and so every surface says the
 * same thing. The important property: an instant that a document reports is
 * labelled as reported, never as observed, and an unsourced one is labelled as
 * unsourced rather than being passed off with a bare timestamp.
 */
export function rainfallStanding(c: FieldValidationComparison): string {
  const label = RAINFALL_TIMESTAMP_VERIFICATION_LABELS[
    c.rainfall_timestamp_verification
  ].toLowerCase();
  const evidence = c.rainfall_timestamp_evidence
    ? c.rainfall_timestamp_evidence
    : "no source recorded";
  if (c.rainfall_timestamp_observed) {
    return `${label} (${evidence})`;
  }
  return `${label} (${evidence}) - not a measured reading`;
}

/**
 * The caveats that must ride beside any comparison on screen, chosen by the
 * flags the backend set — never inferred here.
 */
export function headlineCaveats(c: FieldValidationComparison): string[] {
  const caveats: string[] = [];
  if (c.intervention_affected) {
    caveats.push(
      "This event recorded an intervention, so the rainfall-only model is not expected to match.",
    );
  }
  if (c.unknown_intervention_volumes) {
    caveats.push(
      "At least one intervention has no recorded volume, so its effect cannot be estimated.",
    );
  }
  if (!c.independently_verified) {
    caveats.push(
      "The reported figures were not independently verified; this comparison is not proof.",
    );
  }
  if (c.event_milestone === "unknown") {
    caveats.push(
      "What the event time marks — rain start, rain end, or logging — has not been established.",
    );
  }
  if (!isPreEventTraceable(c)) {
    caveats.push(
      "The pre-event snapshot is not linked to a stored observation, so the starting brine is untraceable.",
    );
  }
  if (c.post_event_timestamp_utc && !c.post_event_timestamp_verified) {
    caveats.push("The post-event time has not been corroborated by any record.");
  }
  return caveats;
}