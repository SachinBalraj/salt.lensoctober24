/**
 * HTTP client for operational risk prediction.
 *
 * Two properties are deliberate.
 *
 * **There is no path by which the browser supplies a feature.** The request body
 * is a pan and, optionally, an instant. Anything else is refused by the server,
 * and this client does not offer a way to send it: a prediction assembled from
 * values the caller chose would look exactly like a prediction assembled from the
 * twin, and nothing downstream could tell them apart.
 *
 * **A refusal to predict is not an error to display verbatim.** The server
 * explains the refusal in detail for a log or a support conversation; the panel
 * shows the operator one fixed sentence. {@link predictionUnavailableMessage}
 * maps the refusal to that sentence, and a refusal it does not recognise is
 * passed through rather than flattened, because an unrecognised refusal is a
 * fact about the system this client does not yet model.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import {
  NO_DEPLOYED_MODEL_MESSAGE,
  NO_DEPLOYED_MODEL_REFUSAL,
  type RiskPrediction,
} from "@/lib/riskPrediction";

const RISK_PATH = "/api/risk";

export type PredictRiskParams = {
  panId: string;
  /** Omit for the current twin state. */
  asOf?: string;
};

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
 * The deployed model's prediction for one pan, from that pan's current twin state.
 *
 * Throws an {@link ApiError} whose `code` is the backend's refusal code, so a
 * caller can tell "no model is deployed" from "the twin has no state" without
 * matching on prose.
 */
export function predictRisk({
  panId,
  asOf,
}: PredictRiskParams): Promise<RiskPrediction> {
  const body: Record<string, string> = { pan_id: panId };
  if (asOf) body.as_of = asOf;
  return request<RiskPrediction>(`${RISK_PATH}/predict`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * What to show when a prediction could not be made.
 *
 * Returns the fixed sentence for the one refusal that is a normal state of the
 * system, and the server's own explanation for anything else. A connectivity
 * failure is not a refusal at all and keeps its own wording, because the fix for
 * it is not a deployment decision.
 */
export function predictionUnavailableMessage(error: ApiError): string {
  if (error.code === NO_DEPLOYED_MODEL_REFUSAL) {
    return NO_DEPLOYED_MODEL_MESSAGE;
  }
  return error.message;
}

/**
 * What the risk module reports about itself.
 *
 * Only the fields this client needs. A structural type rather than `unknown` so
 * that a backend change to a field this client reads is a compile error, and so
 * that a field it does not read cannot tempt a caller to display it.
 */
export type RiskModuleStatus = {
  trained: boolean;
  implemented: boolean;
  capabilities: Record<string, string>;
};

/**
 * Whether a trained model is in service, read without asking for a prediction.
 *
 * **This is a status read, not a prediction.** It writes nothing, stores nothing,
 * and does not score the twin, so asking it on mount does not breach the rule
 * that a prediction is a deliberate act. It exists so the card can say a model is
 * absent when the operator arrives, instead of requiring a click to discover
 * that the button has nothing behind it — an operator who presses "Predict risk"
 * and is refused has learned something, but one reading a dashboard should not
 * have to be told twice.
 *
 * **A failed read is `null`, not `false`.** The distinction is the whole point:
 * `null` means this client does not know, and the card must then fall back to
 * asking rather than assert that no model exists. Reporting a connectivity
 * failure as an untrained model would put a false claim on the screen in exactly
 * the situation where the operator can least afford one.
 */
export async function readRiskModuleStatus(): Promise<RiskModuleStatus | null> {
  try {
    const res = await fetch(`${getApiBaseUrl()}${RISK_PATH}/`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as Partial<RiskModuleStatus>;
    if (typeof body.trained !== "boolean") return null;
    return {
      trained: body.trained,
      implemented: body.implemented === true,
      capabilities: body.capabilities ?? {},
    };
  } catch {
    return null;
  }
}
