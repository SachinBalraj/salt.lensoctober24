/**
 * HTTP client for the rainfall-simulator endpoints.
 *
 * The client sends the four inputs and nothing else. It has no path by which a
 * caller can supply a predicted salinity, a dilution figure or a curve, so the
 * only numbers this panel can display are numbers the server's mass balance
 * produced. That is the same rule the other modules follow, and it is what keeps
 * a what-if tool honest: a scenario the operator picked and a scenario somebody
 * typed in are indistinguishable at the API, and the second one cannot happen.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type {
  MassBalanceResult,
  RainfallSweep,
  SimulationInputs,
} from "@/lib/simulation";

const SIMULATOR_PATH = "/api/simulator";

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
      messageFromDetail(
        detail,
        `The simulator could not be reached. Request failed with status ${response.status}.`,
      ),
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

export type SweepParams = SimulationInputs & {
  /** Extra rainfall depths in millimetres, swept alongside the six presets. */
  rainfallScenariosMm?: readonly number[];
};

/**
 * Resolves the six preset rainfall depths, plus any custom depths, against one
 * starting brine.
 *
 * The response drives both the result table and the chart, so one call covers
 * the whole panel: the curve has no points the table has not also shown.
 */
export function sweepRainfallScenarios({
  area_m2,
  initial_depth_m,
  initial_salinity_g_l,
  rainfallScenariosMm,
}: SweepParams): Promise<RainfallSweep> {
  const payload: Record<string, unknown> = {
    area_m2,
    initial_depth_m,
    initial_salinity_g_l,
  };
  if (rainfallScenariosMm && rainfallScenariosMm.length > 0) {
    payload.rainfall_scenarios_mm = [...rainfallScenariosMm];
  }
  return request<RainfallSweep>(`${SIMULATOR_PATH}/rainfall-scenarios`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** Resolves one rainfall event, for a single what-if figure. */
export function simulateMassBalance(
  inputs: SimulationInputs,
  rainfallMm: number,
): Promise<MassBalanceResult> {
  return request<MassBalanceResult>(`${SIMULATOR_PATH}/mass-balance`, {
    method: "POST",
    body: JSON.stringify({ ...inputs, rainfall_mm: rainfallMm }),
  });
}
