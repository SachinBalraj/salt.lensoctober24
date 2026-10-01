/**
 * HTTP client for the digital-twin endpoints.
 *
 * As with the other API modules, there is no path by which demonstration data can
 * be submitted: this client only ever reads the twin the server assembled, and
 * the refresh call stores whatever the server computed rather than accepting a
 * caller-supplied state. A twin that looked complete but was built in the
 * browser would be exactly the fabrication this project refuses.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type {
  DigitalTwinHistoryResponse,
  DigitalTwinRefreshResponse,
  DigitalTwinState,
} from "@/lib/digitalTwin";

const TWIN_PATH = "/api/twin";

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
        code: (body as { error_code?: unknown } | null)?.error_code
          ? String((body as { error_code: unknown }).error_code)
          : null,
        fields: {},
      },
    );
  }

  return body as T;
}

/**
 * The pan's current twin.
 *
 * A registered pan always yields a body, including when nothing has been
 * measured; a 404 means the pan itself is unknown.
 */
export function getTwinState(panId: string): Promise<DigitalTwinState> {
  return request<DigitalTwinState>(
    `${TWIN_PATH}/${encodeURIComponent(panId)}/state`,
  );
}

export type ListTwinHistoryParams = {
  start?: string;
  end?: string;
  limit?: number;
  offset?: number;
};

/** Stored snapshots for a pan, newest first. */
export function listTwinHistory(
  panId: string,
  params: ListTwinHistoryParams = {},
): Promise<DigitalTwinHistoryResponse> {
  const query = new URLSearchParams();
  if (params.start) query.set("start", params.start);
  if (params.end) query.set("end", params.end);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const suffix = query.toString();
  return request<DigitalTwinHistoryResponse>(
    `${TWIN_PATH}/${encodeURIComponent(panId)}/history${suffix ? `?${suffix}` : ""}`,
  );
}

/**
 * Reassembles the twin server-side and stores a snapshot.
 *
 * No state is sent: the server owns the join, so a caller cannot post a
 * plausible-looking twin that no record supports.
 */
export function refreshTwinState(
  panId: string,
): Promise<DigitalTwinRefreshResponse> {
  return request<DigitalTwinRefreshResponse>(
    `${TWIN_PATH}/${encodeURIComponent(panId)}/refresh`,
    { method: "POST" },
  );
}
