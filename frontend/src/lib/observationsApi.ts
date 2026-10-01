/**
 * HTTP client for the pan-observation endpoints.
 *
 * As with `pansApi.ts`, there is no path by which demonstration data can be
 * submitted: the client only ever sends what the operator typed.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type {
  ObservationRegistration,
  PanObservation,
  PanObservationListResponse,
} from "@/lib/observations";

const OBSERVATIONS_PATH = "/api/observations";

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

export type ListObservationsParams = {
  pan_id?: string;
  start?: string;
  end?: string;
  limit?: number;
  offset?: number;
};

export function listObservations(
  params: ListObservationsParams = {},
): Promise<PanObservationListResponse> {
  const query = new URLSearchParams();
  if (params.pan_id) query.set("pan_id", params.pan_id);
  if (params.start) query.set("start", params.start);
  if (params.end) query.set("end", params.end);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  return request<PanObservationListResponse>(
    `${OBSERVATIONS_PATH}/?${query.toString()}`,
  );
}

export function getObservation(
  observationId: string,
): Promise<PanObservation> {
  return request<PanObservation>(
    `${OBSERVATIONS_PATH}/${encodeURIComponent(observationId)}`,
  );
}

export function recordObservation(
  payload: ObservationRegistration,
): Promise<PanObservation> {
  return request<PanObservation>(`${OBSERVATIONS_PATH}/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
