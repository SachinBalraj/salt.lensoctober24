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
 * shows the operator one sentence per state. {@link predictionState} says which
 * state a refusal is in — from the backend's own `stage` field, not from its
 * prose — and a refusal it does not recognise is passed through rather than
 * flattened, because an unrecognised refusal is a fact about the system this
 * client does not yet model.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import {
  NO_DEPLOYED_MODEL_MESSAGE,
  NO_DEPLOYED_MODEL_REFUSAL,
  type RiskPrediction,
  type RiskUnavailableState,
  type TrainingGate,
} from "@/lib/riskPrediction";

const RISK_PATH = "/api/risk";

/** Refusals where the request reached the model and the inputs were short. */
const MISSING_INPUT_REFUSALS = new Set(["no_observed_features", "no_twin_state"]);

/** The backend's names for which step is outstanding. */
const STAGES = new Set(["no_model", "not_evaluated", "not_deployed"]);

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

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function asTrainingGate(value: unknown): TrainingGate | null {
  if (typeof value !== "object" || value === null) return null;
  const gate = value as Partial<TrainingGate>;
  if (typeof gate.ready !== "boolean") return null;
  return {
    ready: gate.ready,
    genuine_labelled_rows: Number(gate.genuine_labelled_rows ?? 0),
    distinct_pans_in_labels: Number(gate.distinct_pans_in_labels ?? 0),
    min_total_labelled_rows:
      typeof gate.min_total_labelled_rows === "number"
        ? gate.min_total_labelled_rows
        : null,
    min_distinct_pans:
      typeof gate.min_distinct_pans === "number" ? gate.min_distinct_pans : null,
  };
}

/**
 * A refusal of one prediction, with everything the backend said about its state.
 *
 * Extends {@link ApiError} rather than replacing it, so `instanceof ApiError`,
 * `canRetry` and any caller that only reads `status`, `code` or `message` keep
 * working exactly as before. The three extra fields are what let the panel tell
 * "nothing has been trained" from "nobody deployed what was trained" — prose
 * that would otherwise have to be matched on, and matched on differently every
 * time the backend's sentence was reworded.
 */
export class RiskApiError extends ApiError {
  /** Which step is outstanding, for a `no_deployed_model` refusal. */
  readonly stage: "no_model" | "not_evaluated" | "not_deployed" | null;
  /** The named inputs that could not be read, when the refusal is about inputs. */
  readonly missing: string[];
  /** The sufficiency gate's counts, when training is what is blocking. */
  readonly trainingGate: TrainingGate | null;

  constructor(
    message: string,
    options: {
      status: number;
      code?: string | null;
      stage?: unknown;
      missing?: unknown;
      training_gate?: unknown;
    },
  ) {
    super(message, { status: options.status, code: options.code ?? null });
    this.name = "RiskApiError";
    const stage = options.stage;
    this.stage =
      typeof stage === "string" && STAGES.has(stage)
        ? (stage as "no_model" | "not_evaluated" | "not_deployed")
        : null;
    this.missing = asStringList(options.missing);
    this.trainingGate = asTrainingGate(options.training_gate);
  }
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
    const payload = body as Record<string, unknown> | null;
    const detail = payload?.detail;
    const refusal = payload?.refusal;
    throw new RiskApiError(
      messageFromDetail(detail, `Request failed with status ${response.status}.`),
      {
        status: response.status,
        code: typeof refusal === "string" ? refusal : null,
        stage: payload?.stage,
        missing: payload?.missing,
        training_gate: payload?.training_gate,
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
 * Which of the six states this refusal is in.
 *
 * Read from the backend's `stage` and `code`, never from its prose. An error
 * that carries no stage — one constructed by a test, or an older backend —
 * falls back to `no_model`, which is the state the fixed sentence describes and
 * therefore the safe claim: it asserts an absence rather than a deployment
 * decision somebody may have already made.
 *
 * A connectivity failure has no code at all and lands in `failed`, which is
 * right: the registry was never reached, so nothing about it is known and no
 * sentence about models belongs on screen.
 */
export function predictionState(error: ApiError): RiskUnavailableState {
  if (error.code === NO_DEPLOYED_MODEL_REFUSAL) {
    const stage = error instanceof RiskApiError ? error.stage : null;
    return stage ?? "no_model";
  }
  if (error.code !== null && MISSING_INPUT_REFUSALS.has(error.code)) {
    return "missing_inputs";
  }
  return "failed";
}

/** The inputs the model could not read, or nothing when they were not named. */
export function missingInputs(error: ApiError): string[] {
  return error instanceof RiskApiError ? error.missing : [];
}

/** The sufficiency gate's counts, when they travelled with the refusal. */
export function trainingGateOf(error: ApiError): TrainingGate | null {
  return error instanceof RiskApiError ? error.trainingGate : null;
}

/**
 * Where the operational_risk model actually got to, read without predicting.
 *
 * `unknown` is a real answer and distinct from `no_model`: it means the
 * registry could not be read, and the card must then ask rather than assert.
 * A refusal is a state of the registry; a connectivity failure is not, and the
 * two are kept apart here for the same reason they are kept apart everywhere
 * else in this client.
 */
export type RiskModelState =
  | "deployed"
  | "no_model"
  | "not_evaluated"
  | "not_deployed"
  | "unknown";

/**
 * What the risk module reports about itself.
 *
 * Only the fields this client needs. A structural type rather than `unknown` so
 * that a backend change to a field this client reads is a compile error, and so
 * that a field it does not read cannot tempt a caller to display it.
 */
export type RiskModuleStatus = {
  trained: boolean;
  model_state: RiskModelState | null;
  training_gate: TrainingGate | null;
  implemented: boolean;
  capabilities: Record<string, string>;
};

const MODEL_STATES = new Set([
  "deployed",
  "no_model",
  "not_evaluated",
  "not_deployed",
  "unknown",
]);

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
 * **`model_state` says which step is missing, not just that one is.** `no_model`,
 * `not_evaluated` and `not_deployed` have three different remedies in three
 * different modules, and a card that showed one sentence for all three would be
 * sending the operator to the wrong one.
 *
 * **A failed read is `null`, not `false`.** The distinction is the whole point:
 * `null` means this client does not know, and the card must then fall back to
 * asking rather than assert that no model exists. Reporting a connectivity
 * failure as an untrained model would put a false claim on the screen in exactly
 * the situation where the operator can least afford one. The backend returns
 * `trained: null` when its own registry cannot be read, and the `typeof` check
 * below turns that into the same `null` for the same reason.
 */
export async function readRiskModuleStatus(): Promise<RiskModuleStatus | null> {
  try {
    const res = await fetch(`${getApiBaseUrl()}${RISK_PATH}/`, {
      cache: "no-store",
    });
    if (!res.ok) return null;
    const body = (await res.json()) as Partial<RiskModuleStatus>;
    if (typeof body.trained !== "boolean") return null;
    const modelState =
      typeof body.model_state === "string" && MODEL_STATES.has(body.model_state)
        ? (body.model_state as RiskModelState)
        : null;
    return {
      trained: body.trained,
      model_state: modelState,
      training_gate: asTrainingGate(body.training_gate),
      implemented: body.implemented === true,
      capabilities: body.capabilities ?? {},
    };
  } catch {
    return null;
  }
}
