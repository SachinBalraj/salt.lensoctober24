/**
 * HTTP client for the research experiment ledger.
 *
 * One rule for every request here: **a result is a fact already recorded, and
 * nothing this client sends can quietly rewrite one.** The create payload is the
 * full record of a finished run; the backend computes the identity hash itself
 * and never accepts one from a client, so a client cannot file a run under an
 * identity that does not describe it. Re-posting the identical experiment
 * returns the stored one (`created=false`) rather than overwriting anything.
 *
 * Exports are asked for as URLs rather than fetched, so the browser handles the
 * download natively and the `Content-Disposition` filename the backend chose is
 * honoured; nothing is re-serialised on the way.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type {
  Experiment,
  ExperimentCreate,
  ExperimentCreateResponse,
  ExperimentListResponse,
  ExperimentManifest,
  ExperimentStatus,
} from "@/lib/experiment";

const EXPERIMENTS_PATH = "/api/experiments";

function collectFieldErrors(detail: unknown): Record<string, string> {
  if (!Array.isArray(detail)) return {};
  const fields: Record<string, string> = {};
  for (const item of detail) {
    if (typeof item !== "object" || item === null) continue;
    const entry = item as { loc?: unknown; msg?: unknown };
    const location = Array.isArray(entry.loc) ? entry.loc : [];
    const name = location
      .map((part) => String(part))
      .filter((part) => part.length > 0 && part !== "body")
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

export type ListExperimentsParams = {
  model?: string;
  target?: string;
  dataset_hash?: string;
  limit?: number;
  offset?: number;
};

/** What this module does, and the live record totals it reads from. */
export function getExperimentStatus(): Promise<ExperimentStatus> {
  return request<ExperimentStatus>(`${EXPERIMENTS_PATH}/status`);
}

/**
 * A page of the ledger, newest first.
 *
 * The backend narrows `model` and `target` loosely and `dataset_hash` exactly —
 * a content hash is an identity, not a search term.
 */
export function listExperiments(
  params: ListExperimentsParams = {},
): Promise<ExperimentListResponse> {
  const query = new URLSearchParams();
  if (params.model?.trim()) query.set("model", params.model.trim());
  if (params.target?.trim()) query.set("target", params.target.trim());
  if (params.dataset_hash?.trim()) query.set("dataset_hash", params.dataset_hash.trim());
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const encoded = query.toString();
  return request<ExperimentListResponse>(
    `${EXPERIMENTS_PATH}/?${encoded}`,
  );
}

/** One stored experiment, exactly as it will be reproduced. */
export function getExperiment(experimentId: string): Promise<Experiment> {
  return request<Experiment>(
    `${EXPERIMENTS_PATH}/${encodeURIComponent(experimentId)}`,
  );
}

/** The self-contained reproducibility manifest for one experiment. */
export function getExperimentManifest(
  experimentId: string,
): Promise<ExperimentManifest> {
  return request<ExperimentManifest>(
    `${EXPERIMENTS_PATH}/${encodeURIComponent(experimentId)}/manifest`,
  );
}

/**
 * Record one completed experiment.
 *
 * 201 means a new row was written. 200 with `created=false` means the identical
 * experiment was already there — that is the ledger refusing to record a second
 * row that would look like an independent reproduction, not an error.
 */
export function createExperiment(
  payload: ExperimentCreate,
): Promise<ExperimentCreateResponse> {
  return request<ExperimentCreateResponse>(`${EXPERIMENTS_PATH}/`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** The download URL for one experiment's reproducibility manifest. */
export function experimentManifestUrl(experimentId: string): string {
  return `${getApiBaseUrl()}${EXPERIMENTS_PATH}/${encodeURIComponent(experimentId)}/manifest`;
}

/**
 * The download URL for one experiment's results.
 *
 * JSON is the reproducibility manifest itself; CSV is the metrics table. A URL
 * rather than a fetch, so the browser honours the filename the backend wrote.
 */
export function experimentExportUrl(
  experimentId: string,
  format: "csv" | "json",
): string {
  return `${getApiBaseUrl()}${EXPERIMENTS_PATH}/${encodeURIComponent(
    experimentId,
  )}/export?output_format=${format}`;
}

/** The download URL for the whole ledger, narrowed the same way the list is. */
export function ledgerExportUrl(
  format: "csv" | "json",
  params: ListExperimentsParams = {},
): string {
  const query = new URLSearchParams({ output_format: format });
  if (params.model?.trim()) query.set("model", params.model.trim());
  if (params.target?.trim()) query.set("target", params.target.trim());
  if (params.dataset_hash?.trim()) query.set("dataset_hash", params.dataset_hash.trim());
  return `${getApiBaseUrl()}${EXPERIMENTS_PATH}/export?${query.toString()}`;
}