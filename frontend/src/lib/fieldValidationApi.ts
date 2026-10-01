/**
 * HTTP client for the field-validation endpoints (Module 20).
 *
 * Everything sent here is a user report — an event or an intervention — and
 * everything received is computed by the backend from the stored reports. The
 * client never sends a metric, because a validation number a caller could post
 * would be worth nothing: the replay is recomputed server-side from the frozen
 * event inputs, and this client only shapes the request and reads the result.
 *
 * The export endpoints are asked for as URLs rather than fetched, so the
 * browser handles the download natively and the `Content-Disposition` filename
 * the backend chose is honoured.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type {
  EventCorrectionDraft,
  FieldValidationComparison,
  FieldValidationStatus,
  FieldValidationSummary,
  PreEventLinkDraft,
  RainfallConflictDraft,
  RainfallEvent,
  RainfallEventDraft,
  RainfallResolutionDraft,
} from "@/lib/fieldValidation";

const FIELD_VALIDATION_PATH = "/api/field-validation";

function collectFieldErrors(detail: unknown): Record<string, string> {
  if (!Array.isArray(detail)) return {};
  const fields: Record<string, string> = {};
  for (const item of detail) {
    if (typeof item !== "object" || item === null) continue;
    const entry = item as { loc?: unknown; msg?: unknown };
    // FastAPI's `loc` is like ["body", "pre_event", "salinity_g_l"]; the
    // "body" part is just the container, not a field a form can address.
    const location = Array.isArray(entry.loc) ? entry.loc.slice(1) : [];
    const name = location
      .map((part) => String(part))
      .filter((part) => part.length > 0)
      .join(".");
    const message = typeof entry.msg === "string" ? entry.msg : "Invalid value.";
    if (name) fields[name] = message;
  }
  return fields;
}

function messageFromDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: unknown };
    if (typeof first?.msg === "string") return first.msg;
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(
      `Could not reach the SaltLens API at ${getApiBaseUrl()}. Check that the backend is running.`,
      { status: 0 },
    );
  }

  const raw = await response.text();
  let body: unknown = null;
  if (raw) {
    try {
      body = JSON.parse(raw);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new ApiError(
      messageFromDetail(detail, `Request failed with status ${response.status}.`),
      {
        status: response.status,
        code: (body as { error_code?: string } | null)?.error_code ?? null,
        fields: collectFieldErrors(detail),
      },
    );
  }

  return body as T;
}

export type FieldValidationParams = {
  pan_id?: string;
  limit?: number;
};

function withQuery(path: string, params: FieldValidationParams = {}): string {
  const query = new URLSearchParams();
  if (params.pan_id) query.set("pan_id", params.pan_id);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const suffix = query.toString();
  return suffix ? `${path}?${suffix}` : path;
}

export function getFieldValidationStatus(pan_id?: string): Promise<FieldValidationStatus> {
  return request<FieldValidationStatus>(
    withQuery(`${FIELD_VALIDATION_PATH}/status`, pan_id ? { pan_id } : {}),
  );
}

export function listFieldValidationEvents(
  params: FieldValidationParams = {},
): Promise<RainfallEvent[]> {
  return request<RainfallEvent[]>(
    withQuery(`${FIELD_VALIDATION_PATH}/events`, { limit: 200, ...params }),
  );
}

export function listFieldValidationComparisons(
  params: FieldValidationParams = {},
): Promise<FieldValidationComparison[]> {
  return request<FieldValidationComparison[]>(
    withQuery(`${FIELD_VALIDATION_PATH}/comparisons`, { limit: 200, ...params }),
  );
}

export function getFieldValidationComparison(event_id: string): Promise<FieldValidationComparison> {
  return request<FieldValidationComparison>(
    `${FIELD_VALIDATION_PATH}/events/${encodeURIComponent(event_id)}`,
  );
}

export function getFieldValidationSummary(
  params: FieldValidationParams = {},
): Promise<FieldValidationSummary> {
  return request<FieldValidationSummary>(
    withQuery(`${FIELD_VALIDATION_PATH}/summary`, { limit: 1000, ...params }),
  );
}

/** Files one reported event; nothing here verifies it. */
export function createFieldValidationEvent(
  draft: RainfallEventDraft,
): Promise<FieldValidationComparison> {
  const payload = {
    pan_id: draft.pan_id,
    name: draft.name || null,
    event_timestamp: draft.event_timestamp,
    event_milestone: draft.event_milestone,
    reported_by: draft.reported_by || null,
    actual_rainfall_mm: draft.actual_rainfall_mm,
    rainfall_timestamp: draft.rainfall_timestamp,
    rainfall_source: draft.rainfall_source || null,
    forecast_rainfall_mm: draft.forecast_rainfall_mm,
    pre_event: {
      salinity_g_l: draft.pre_salinity_g_l,
      water_depth_cm: draft.pre_depth_cm,
      source: "manual",
    },
    post_event: {
      salinity_g_l: draft.post_salinity_g_l,
      water_depth_cm: draft.post_depth_cm,
      source: "manual",
    },
    interventions: draft.interventions.map((intervention) => ({
      kind: intervention.kind,
      direction: intervention.direction,
      performed_at: intervention.performed_at,
      volume_m3: intervention.volume_m3,
      description: intervention.description || null,
    })),
    notes: draft.notes || null,
  };
  return request<FieldValidationComparison>(`${FIELD_VALIDATION_PATH}/events`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Appends one intervention to an existing event, then returns the new replay. */
export function addFieldValidationIntervention(input: {
  event_id: string;
  kind: string;
  direction: string;
  performed_at: string;
  volume_m3: number | null;
  description: string;
}): Promise<FieldValidationComparison> {
  return request<FieldValidationComparison>(
    `${FIELD_VALIDATION_PATH}/events/${encodeURIComponent(input.event_id)}/interventions`,
    {
      method: "POST",
      body: JSON.stringify({
        kind: input.kind,
        direction: input.direction,
        performed_at: input.performed_at,
        volume_m3: input.volume_m3,
        description: input.description || null,
      }),
    },
  );
}

export function fieldValidationExportUrl(format: "csv" | "pdf", pan_id?: string): string {
  const query = new URLSearchParams({ format });
  if (pan_id) query.set("pan_id", pan_id);
  return `${getApiBaseUrl()}${FIELD_VALIDATION_PATH}/export?${query.toString()}`;
}

/**
 * Appends one reconciliation step to an event's audit history.
 *
 * The previous value is recorded server-side from what is stored, so this client
 * never sends what the field used to say — only the new value, the reason and the
 * evidence. Nothing here can set `independently_verified`.
 */
export function recordFieldValidationCorrection(input: {
  event_id: string;
  draft: EventCorrectionDraft;
}): Promise<FieldValidationComparison> {
  const { draft } = input;
  return request<FieldValidationComparison>(
    `${FIELD_VALIDATION_PATH}/events/${encodeURIComponent(input.event_id)}/corrections`,
    {
      method: "POST",
      body: JSON.stringify({
        field: draft.field,
        reason: draft.reason,
        event_milestone: draft.event_milestone,
        timestamp: draft.timestamp,
        recorded_timezone: draft.recorded_timezone || null,
        observer: draft.observer || null,
        evidence_source: draft.evidence_source || null,
        corrected_by: draft.corrected_by || null,
      }),
    },
  );
}

/**
 * Reconciles a pre-event snapshot against a stored observation.
 *
 * Every field is sent, including the original record's own local time and zone,
 * because the backend checks them against the stored instant and refuses a link
 * that does not agree — including one that would attach a later observation to an
 * earlier event.
 */
export function linkPreEventObservation(input: {
  event_id: string;
  draft: PreEventLinkDraft;
}): Promise<FieldValidationComparison> {
  const { draft } = input;
  return request<FieldValidationComparison>(
    `${FIELD_VALIDATION_PATH}/events/${encodeURIComponent(input.event_id)}/pre-event-link`,
    {
      method: "POST",
      body: JSON.stringify({
        observation_id: draft.observation_id.trim(),
        measured_at_local: draft.measured_at_local.trim(),
        measured_timezone: draft.measured_timezone.trim(),
        observer: draft.observer.trim(),
        evidence_source: draft.evidence_source.trim(),
        reason: draft.reason.trim(),
        corrected_by: draft.corrected_by.trim() || null,
      }),
    },
  );
}
/**
 * Records a second account of when the rain fell, without changing the one in
 * force.
 *
 * This is deliberately not a correction: the event keeps the rainfall timestamp
 * it was filed with, because that instant is what the stored replay was run
 * against. The reported instant, its source and the size of the disagreement go
 * onto the record beside it, and the event reports itself as blocked from being
 * reconciled until the disagreement is resolved against evidence.
 */
export function recordRainfallTimestampConflict(input: {
  event_id: string;
  draft: RainfallConflictDraft;
}): Promise<FieldValidationComparison> {
  const { draft } = input;
  return request<FieldValidationComparison>(
    `${FIELD_VALIDATION_PATH}/events/${encodeURIComponent(input.event_id)}/rainfall-timestamp-conflict`,
    {
      method: "POST",
      body: JSON.stringify({
        reported_timestamp: draft.reported_timestamp.trim(),
        reported_timezone: draft.reported_timezone.trim() || null,
        reported_source: draft.reported_source.trim(),
        reason: draft.reason.trim(),
        recorded_by: draft.recorded_by.trim() || null,
      }),
    },
  );
}

/**
 * Move the rainfall instant onto evidence, superseding the value it replaces.
 *
 * The displaced value is never overwritten: the API retains it with the origin
 * supplied here, so this is how a correction stays auditable instead of
 * destructive. Two things are deliberately absent. There is no verification
 * field, because a document that records a time cannot make it a measured one
 * and the API would refuse the claim; and the local reading is sent unresolved,
 * with its declared zone, so no browser ever performs a zone conversion.
 */
export function resolveRainfallTimestamp(input: {
  event_id: string;
  draft: RainfallResolutionDraft;
}): Promise<FieldValidationComparison> {
  const { draft } = input;
  return request<FieldValidationComparison>(
    `${FIELD_VALIDATION_PATH}/events/${encodeURIComponent(input.event_id)}/rainfall-timestamp-resolution`,
    {
      method: "POST",
      body: JSON.stringify({
        timestamp_local: draft.timestamp.trim(),
        timestamp_timezone: draft.timezone.trim(),
        evidence_source: draft.evidence_source.trim(),
        superseded_origin: draft.superseded_origin.trim(),
        superseded_source_claim: draft.superseded_source_claim.trim() || null,
        superseded_supporting_record:
          draft.superseded_supporting_record.trim() || null,
        reason: draft.reason.trim(),
        corrected_by: draft.corrected_by.trim() || null,
      }),
    },
  );
}
