/**
 * HTTP client for the weather endpoints.
 *
 * As with `observationsApi.ts`, there is no path by which demonstration data can
 * be submitted: the client only ever sends what the operator typed, and the
 * manual-entry functions refuse to carry an automated provenance.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type {
  WeatherIngestResponse,
  WeatherObservation,
  WeatherObservationListResponse,
  WeatherProvider,
  WeatherProvidersResponse,
  WeatherRegistration,
} from "@/lib/weather";

const WEATHER_PATH = "/api/weather";

const FIELD_ALIASES: Record<string, string> = {
  body: "",
  query: "",
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

export type ListWeatherParams = {
  pan_id?: string;
  start?: string;
  end?: string;
  limit?: number;
  offset?: number;
};

export function listWeather(
  params: ListWeatherParams = {},
): Promise<WeatherObservationListResponse> {
  const query = new URLSearchParams();
  if (params.pan_id) query.set("pan_id", params.pan_id);
  if (params.start) query.set("start", params.start);
  if (params.end) query.set("end", params.end);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const suffix = query.toString();
  return request<WeatherObservationListResponse>(
    suffix ? `${WEATHER_PATH}/?${suffix}` : `${WEATHER_PATH}/`,
  );
}

export function getWeatherRecord(
  weatherId: string,
): Promise<WeatherObservation> {
  return request<WeatherObservation>(
    `${WEATHER_PATH}/${encodeURIComponent(weatherId)}`,
  );
}

/**
 * Records a hand-entered weather observation.
 *
 * The payload type already excludes `weather_api`, and this guard makes the
 * intent impossible to defeat from the call site: a record that claims an
 * automated origin is rejected before a request is made.
 */
export async function recordWeather(
  payload: WeatherRegistration,
): Promise<WeatherObservation> {
  if ((payload.source as string) === "weather_api") {
    throw new ApiError(
      "A record entered by hand cannot be labelled as a weather API result. Use the forecast fetch instead.",
      { status: 0 },
    );
  }
  return request<WeatherObservation>(`${WEATHER_PATH}/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/**
 * Which weather APIs this deployment can reach.
 *
 * Treated as a deployment fact rather than stored data, so it is worth reading
 * before telling an operator why a record is labelled the way it is.
 */
export function listWeatherProviders(): Promise<WeatherProvidersResponse> {
  return request<WeatherProvidersResponse>(`${WEATHER_PATH}/providers`);
}

export type IngestForecastParams = {
  pan_id: string;
  start?: string;
  end?: string;
};

/**
 * Fetches a forecast from a registered provider.
 *
 * The server decides what provenance the stored records carry; nothing here can
 * influence it.
 */
export function ingestForecast(
  provider: string,
  params: IngestForecastParams,
): Promise<WeatherIngestResponse> {
  const query = new URLSearchParams();
  query.set("pan_id", params.pan_id);
  if (params.start) query.set("start", params.start);
  if (params.end) query.set("end", params.end);
  return request<WeatherIngestResponse>(
    `${WEATHER_PATH}/ingest/${encodeURIComponent(provider)}?${query.toString()}`,
    { method: "POST" },
  );
}

export type { WeatherProvider };
