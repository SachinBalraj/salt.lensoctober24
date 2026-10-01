/**
 * HTTP client for the research-validation endpoints.
 *
 * Everything this client sends is a filter or a download preference. There is no
 * payload with a metric in it and no route that accepts one, because a validation
 * report whose numbers a caller could post would be worth nothing: every figure
 * here is computed by the backend from recorded outcomes.
 *
 * The export endpoints are asked for as URLs rather than fetched, so the browser
 * handles the download natively and the `Content-Disposition` filename the
 * backend chose is honoured; nothing is re-serialised on the way.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type { ComparisonKey } from "@/lib/validation";
import type {
  ValidationOverview,
  ValidationPairsResponse,
  ValidationStatus,
} from "@/lib/validation";

const VALIDATION_PATH = "/api/validation";

function messageFromBody(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim().length > 0) return detail;
  }
  return fallback;
}

async function request<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${getApiBaseUrl()}${path}`, {
      headers: { "Content-Type": "application/json" },
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
    throw new ApiError(messageFromBody(body, `Request failed with status ${response.status}.`), {
      status: response.status,
    });
  }

  return body as T;
}

/** What this module does, and the live record totals it reads from. */
export function getValidationStatus(): Promise<ValidationStatus> {
  return request<ValidationStatus>(`${VALIDATION_PATH}/status`);
}

export type ValidationOverviewParams = {
  pan_id?: string;
  limit?: number;
};

/** Renders a URL query string, or nothing at all when there are no filters. */
function toQuery(params: ValidationOverviewParams): string {
  const query = new URLSearchParams();
  if (params.pan_id) query.set("pan_id", params.pan_id);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const encoded = query.toString();
  return encoded ? `?${encoded}` : "";
}

/**
 * The five comparison blocks over recorded outcomes.
 *
 * `pan_id` narrows the report to one pan; omitted, it covers every recorded
 * event. No figure here is ever invented: a metric the data could not support
 * arrives as `computed=false` with a reason, and the client renders it as the
 * dash.
 */
export function getValidationOverview(
  params: ValidationOverviewParams = {},
): Promise<ValidationOverview> {
  return request<ValidationOverview>(
    `${VALIDATION_PATH}/overview${toQuery(params)}`,
  );
}

/** The event-by-event view for one comparison family. */
export async function getValidationPairs(
  comparison: ComparisonKey,
  params: ValidationOverviewParams = {},
): Promise<ValidationPairsResponse> {
  const query = new URLSearchParams({ comparison });
  if (params.pan_id) query.set("pan_id", params.pan_id);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  const encoded = query.toString();
  return request<ValidationPairsResponse>(
    `${VALIDATION_PATH}/pairs?${encoded}`,
  );
}

/**
 * The download URL for one comparison's raw rows.
 *
 * A URL rather than a fetch, so the browser handles the download natively and
 * the filename the backend wrote is honoured. The rows a researcher saves are
 * exactly the rows the aggregates were built from.
 */
export function validationExportUrl(
  comparison: ComparisonKey,
  format: "csv" | "json",
  params: ValidationOverviewParams = {},
): string {
  const query = new URLSearchParams({ comparison, output_format: format });
  if (params.pan_id) query.set("pan_id", params.pan_id);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  return `${getApiBaseUrl()}${VALIDATION_PATH}/export?${query.toString()}`;
}