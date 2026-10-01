/**
 * HTTP client for the salt-pan registry endpoints.
 *
 * Nothing here ever sends or receives demonstration data: demo records exist
 * only in the browser for layout previews and are never posted to the API.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type {
  PanStatusFilter,
  SaltPan,
  SaltPanListResponse,
  SaltPanRegistration,
  SaltPanUpdate,
} from "@/lib/pans";

const PANS_PATH = "/api/pans";

/** Maps FastAPI's 422 `loc` tuples onto form field names. */
const FIELD_ALIASES: Record<string, string> = {
  location_name: "location",
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
    if (name) {
      fields[name] = message;
    }
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

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
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

  if (response.status === 204) {
    return undefined as T;
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

export type ListPansParams = {
  pan_status?: PanStatusFilter;
  search?: string;
  limit?: number;
  offset?: number;
};

export function listPans(params: ListPansParams = {}): Promise<SaltPanListResponse> {
  const query = new URLSearchParams();
  query.set("pan_status", params.pan_status ?? "active");
  if (params.search?.trim()) query.set("search", params.search.trim());
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  return request<SaltPanListResponse>(`${PANS_PATH}/?${query.toString()}`);
}

export function getPan(panId: string): Promise<SaltPan> {
  return request<SaltPan>(`${PANS_PATH}/${encodeURIComponent(panId)}`);
}

export function registerPan(
  payload: SaltPanRegistration,
): Promise<SaltPan> {
  return request<SaltPan>(`${PANS_PATH}/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updatePan(
  panId: string,
  payload: SaltPanUpdate,
): Promise<SaltPan> {
  return request<SaltPan>(`${PANS_PATH}/${encodeURIComponent(panId)}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deactivatePan(panId: string): Promise<SaltPan> {
  return request<SaltPan>(
    `${PANS_PATH}/${encodeURIComponent(panId)}/deactivate`,
    { method: "POST" },
  );
}
