/**
 * HTTP client for the actual-outcome endpoints.
 *
 * As with `observationsApi.ts`, there is no path by which a comparison can be
 * submitted: the client only ever sends what the operator measured or did. The
 * prediction, the error terms and the aggregate statistics are all computed by
 * the backend, and a payload carrying any of them would be rejected as an
 * unknown field.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type {
  EventOutcome,
  EventOutcomeListResponse,
  EventOutcomeRegistration,
  OutcomeComparisonSummary,
} from "@/lib/outcomes";

const OUTCOMES_PATH = "/api/outcomes";

/** Maps FastAPI's 422 `loc` tuples onto form field names. */
const FIELD_ALIASES: Record<string, string> = {
  body: "",
};

function collectFieldErrors(detail: unknown): Record<string, string> {
  if (!Array.isArray(detail)) return {};
  const fields: Record<string, string> = {};
  for (const item of detail) {
    if (typeof item !== "object" || item === null) continue;
    const entry = item as { loc?: unknown; msg?: unknown };
    const location = Array.isArray(entry.loc) ? entry.loc : [];
    const name = location
      .map((part) => FIELD_ALIASES[String(part)] ?? String(part))
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

  if (response.status === 204) return undefined as T;

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

export type ListOutcomesParams = {
  pan_id?: string;
  event_id?: string;
  start?: string;
  end?: string;
  limit?: number;
  offset?: number;
};

export function listOutcomes(
  params: ListOutcomesParams = {},
): Promise<EventOutcomeListResponse> {
  const query = new URLSearchParams();
  if (params.pan_id) query.set("pan_id", params.pan_id);
  if (params.event_id) query.set("event_id", params.event_id);
  if (params.start) query.set("start", params.start);
  if (params.end) query.set("end", params.end);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  return request<EventOutcomeListResponse>(
    `${OUTCOMES_PATH}/?${query.toString()}`,
  );
}

export function getOutcome(outcomeId: string): Promise<EventOutcome> {
  return request<EventOutcome>(
    `${OUTCOMES_PATH}/${encodeURIComponent(outcomeId)}`,
  );
}

/**
 * Files one event.
 *
 * The body is serialised from the registration exactly as validated, so no
 * comparison figure can reach the API even by accident: the type has no field to
 * put one in.
 */
export function recordOutcome(
  payload: EventOutcomeRegistration,
): Promise<EventOutcome> {
  return request<EventOutcome>(`${OUTCOMES_PATH}/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/**
 * Fetches the predicted-against-actual aggregates.
 *
 * Declared before `getOutcome` in the router, and asked for with an explicit
 * `comparison` path here, so the literal segment is never read as an outcome
 * identifier.
 */
export function getOutcomeComparison(params: {
  pan_id?: string;
  limit?: number;
} = {}): Promise<OutcomeComparisonSummary> {
  const query = new URLSearchParams();
  if (params.pan_id) query.set("pan_id", params.pan_id);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  return request<OutcomeComparisonSummary>(
    `${OUTCOMES_PATH}/comparison?${query.toString()}`,
  );
}
