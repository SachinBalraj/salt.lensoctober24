/**
 * HTTP client for the model-evaluation endpoints.
 *
 * Two things are worth noting about what this client is allowed to send.
 *
 * **It cannot send a metric.** `POST /api/evaluation/run` takes a model id and
 * optionally a fold count. There is no field for an accuracy, a confusion matrix or
 * a per-class figure, so a client cannot grade a model on its own account even by
 * accident. The backend forbids unknown keys, and the absence of those fields here
 * is the same refusal from the other direction.
 *
 * **It cannot ask for a split strategy.** The cross-validation design is derived
 * from the split the model was trained with. A caller who could name the design
 * could shuffle a time-ordered dataset until the score improved, which is the one
 * thing a held-out evaluation exists to prevent. The fold count is a preference and
 * may be sent; the design is not the caller's to choose.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type {
  CompareVersionsResponse,
  EvaluationDetail,
  EvaluationListResponse,
  EvaluationModuleStatus,
  EvaluationRefusal,
  ExportFormat,
} from "@/lib/evaluation";

const EVALUATION_PATH = "/api/evaluation";

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

/**
 * The message a reader sees for a failed request.
 *
 * A refusal body carries a `detail` sentence written to be shown, so it is used
 * verbatim. The `refusal` code is attached to the error rather than matched on
 * here, because a client that branched on prose would break the moment the prose
 * was reworded.
 */
function messageFromBody(body: unknown, fallback: string): string {
  if (typeof body === "object" && body !== null) {
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail.trim().length > 0) return detail;
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
    // A refusal is an error the operator can act on, so its code travels on the
    // ApiError. `dataset_drift` and `artifact_unavailable` are fixed by rebuilding
    // or re-registering rather than by trying again, and the UI needs to say which
    // it is rather than showing one generic failure for both.
    const refusal = (body ?? {}) as Partial<EvaluationRefusal>;
    throw new ApiError(
      messageFromBody(body, `Request failed with status ${response.status}.`),
      {
        status: response.status,
        code: typeof refusal.refusal === "string" ? refusal.refusal : null,
        fields: collectFieldErrors((body as { detail?: unknown } | null)?.detail),
      },
    );
  }

  return body as T;
}

/** What this module does and refuses to do, in its own words. */
export function getEvaluationStatus(): Promise<EvaluationModuleStatus> {
  return request<EvaluationModuleStatus>(`${EVALUATION_PATH}/`);
}

/**
 * Every stored evaluation, plus every registered model.
 *
 * The two lists differ on purpose and the client keeps them separate rather than
 * merging them: a picker built from the evaluated list only would hide a trainable
 * model behind a decision to score it.
 */
export function listEvaluations(): Promise<EvaluationListResponse> {
  return request<EvaluationListResponse>(`${EVALUATION_PATH}/metrics`);
}

/**
 * Scores a registered model and stores the report.
 *
 * Returns 201 for a first run and 200 when an existing report was replaced, so the
 * `recomputed` flag on the response is the authoritative answer and the status code
 * is a convenience.
 */
export function runEvaluation(
  modelId: string,
  options: {
    crossValidationSplits?: number | null;
  } = {},
): Promise<EvaluationDetail> {
  const payload: { model_id: string; cross_validation_splits?: number } = {
    model_id: modelId,
  };
  if (options.crossValidationSplits !== undefined && options.crossValidationSplits !== null) {
    payload.cross_validation_splits = options.crossValidationSplits;
  }
  return request<EvaluationDetail>(`${EVALUATION_PATH}/run`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

/** One stored report in full, provenance included. */
export function getEvaluation(modelId: string): Promise<EvaluationDetail> {
  return request<EvaluationDetail>(
    `${EVALUATION_PATH}/${encodeURIComponent(modelId)}/metrics`,
  );
}

/**
 * The stored comparison table for one model version.
 *
 * Taken as a query parameter rather than a path segment, matching the backend:
 * the registry's key is `model_id`, and the version is on the record, so a caller
 * holding an id is not also asked to know which version it resolved to.
 */
export function getComparison(modelId: string) {
  const query = new URLSearchParams({ model_id: modelId });
  return request<import("@/lib/evaluation").ComparisonResponse>(
    `${EVALUATION_PATH}/compare?${query.toString()}`,
  );
}

/**
 * The same six columns across model versions.
 *
 * `comparison` is null when the rows do not share a row set and a row count, and
 * `not_comparable` then says why. The client does not substitute a table of its own
 * in that case: ranking figures from different held-out sets would report the
 * difference between the datasets as a difference between the models.
 */
export function compareVersions(): Promise<CompareVersionsResponse> {
  return request<CompareVersionsResponse>(`${EVALUATION_PATH}/compare/versions`);
}

/**
 * The download URL for one model version's record.
 *
 * A URL rather than a fetch, so the browser handles the download natively and the
 * `Content-Disposition` filename the backend chose is honoured. Nothing is
 * re-serialised on the way, so the file a reader saves is the file the backend
 * wrote.
 */
export function evaluationExportUrl(
  modelId: string,
  format: ExportFormat = "json",
): string {
  const query = new URLSearchParams({ format });
  return `${getApiBaseUrl()}${EVALUATION_PATH}/${encodeURIComponent(modelId)}/export?${query.toString()}`;
}
