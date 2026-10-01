/**
 * HTTP client for Module 11's SHAP explanations.
 *
 * Three properties are deliberate.
 *
 * **The local read is by prediction, and the caller has nothing to say about the
 * model.** The prediction knows which model version answered it and which input row
 * it was given; the request names only the prediction. A client that could name a
 * model would be able to ask "explain this row with that model", and the answer
 * would be an attribution of a number the model did not produce.
 *
 * **A refusal is not an error to display verbatim.** The server's `detail` is
 * written for a log or a support conversation. {@link explanationUnavailableMessage}
 * turns the one refusal that is a normal state of the system into a fixed sentence
 * and passes anything else through, because an unrecognised refusal is a fact about
 * the system this client does not yet model, and flattening it would hide that.
 *
 * **The global summary is fetched on request, never on mount.** It explains up to
 * fifty held-out rows, which is real work, and nobody asked for it by opening the
 * page. The panel asks when someone presses the button.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type {
  GlobalImportance,
  LocalExplanation,
  ModelExplanationList,
} from "@/lib/explanation";

const EXPLAIN_PATH = "/api/explain";

/** The refusal when a prediction has not been explained and cannot be asked for. */
export const EXPLANATION_NOT_STORED_REFUSAL = "explanation_not_stored";

/** The refusal for a model with no probability to decompose. */
export const NO_PROBABILITY_REFUSAL = "model_reports_no_probability";

/** The refusal for an artifact that is not on this host. */
export const ARTIFACT_UNAVAILABLE_REFUSAL = "artifact_unavailable";

/** The fixed sentence for a prediction that is explainable in principle. */
export const NO_EXPLANATION_AVAILABLE_MESSAGE =
  "No SHAP explanation is available for this prediction.";

/** The fixed sentence for a model that reports no probability. */
export const NO_PROBABILITY_MESSAGE =
  "SHAP explanation unavailable — this model reports no class probabilities, so there is no probability to attribute.";

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
    const refusal = (body as { refusal?: unknown } | null)?.refusal;
    throw new ApiError(
      messageFromDetail(detail, `Request failed with status ${response.status}.`),
      {
        status: response.status,
        code: typeof refusal === "string" ? refusal : null,
        fields: {},
      },
    );
  }

  return body as T;
}

/**
 * The stored explanation of one prediction, computed on first read.
 *
 * A 201 here means the explanation did not exist and was just computed; a 200 means
 * the stored one was returned, or refreshed. Both are successes and the caller does
 * not need to tell them apart to render it.
 */
export function explainPrediction(predictionId: string): Promise<LocalExplanation> {
  return request<LocalExplanation>(
    `${EXPLAIN_PATH}/predictions/${encodeURIComponent(predictionId)}`,
    { method: "POST" },
  );
}

/** The explanation already stored for a prediction, without computing one. */
export function readPredictionExplanation(
  predictionId: string,
): Promise<LocalExplanation> {
  return request<LocalExplanation>(
    `${EXPLAIN_PATH}/predictions/${encodeURIComponent(predictionId)}`,
  );
}

export type GlobalImportanceParams = {
  modelId: string;
  version: string;
  /** Held-out rows to explain. The response reports how many it actually used. */
  rows?: number;
};

/**
 * The model's feature importance over its held-out rows.
 *
 * Computed on the server and stored, so a second call returns the same numbers --
 * a figure that changed on every page load could not be quoted or compared.
 */
export function fetchGlobalImportance({
  modelId,
  version,
  rows,
}: GlobalImportanceParams): Promise<GlobalImportance> {
  const query = rows === undefined ? "" : `?rows=${encodeURIComponent(String(rows))}`;
  return request<GlobalImportance>(
    `${EXPLAIN_PATH}/models/${encodeURIComponent(modelId)}/versions/${encodeURIComponent(
      version,
    )}/importance${query}`,
  );
}

/** Every explanation stored against one model version, local and global. */
export function fetchModelExplanations(
  modelId: string,
  version: string,
): Promise<ModelExplanationList> {
  return request<ModelExplanationList>(
    `${EXPLAIN_PATH}/models/${encodeURIComponent(modelId)}/versions/${encodeURIComponent(
      version,
    )}/explanations`,
  );
}

/**
 * What to show when no explanation could be produced.
 *
 * The one refusal that describes a model rather than a fault gets a fixed sentence,
 * because a class-only model is a legitimate thing for a registry to hold and an
 * operator should not have to read a paragraph to learn that. Everything else keeps
 * the server's own wording: those are facts about this deployment -- a missing
 * artifact, a snapshot that no longer hashes -- and the detail names the model or
 * the id needed to act on it.
 */
export function explanationUnavailableMessage(error: ApiError): string {
  if (error.code === NO_PROBABILITY_REFUSAL) return NO_PROBABILITY_MESSAGE;
  if (error.code === EXPLANATION_NOT_STORED_REFUSAL) {
    return NO_EXPLANATION_AVAILABLE_MESSAGE;
  }
  return error.message;
}
