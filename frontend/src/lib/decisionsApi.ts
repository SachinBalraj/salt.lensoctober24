/**
 * HTTP client for Module 12 operational recommendations.
 *
 * Two properties are deliberate.
 *
 * **There is no path by which the browser supplies an input.** The body is a pan
 * and, optionally, an instant, a named rule and an explicit rainfall depth. The
 * features, the action and the model are the server's to read: a recommendation
 * assembled from values the caller chose would look exactly like one assembled from
 * the twin, the stored model and the written rule, and nothing downstream could
 * tell them apart. The one number a caller may send is a what-if rainfall depth,
 * and it is sent as `rainfall_mm` so it is never confused with a forecast.
 *
 * **A refusal to advise is not an error to display verbatim.** The server explains
 * each refusal in detail for a log or a support conversation; the panel shows the
 * operator one fixed sentence per refusal. {@link adviceUnavailableMessage} maps
 * the code to that sentence, and a code it does not recognise is passed through
 * rather than flattened, because an unrecognised refusal is a fact about the
 * system this client does not yet model.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import type { DecisionAction, DecisionAdvice, DecisionLatest } from "@/lib/decisions";
import { ADVICE_REFUSAL_MESSAGES, type AdviceRefusal } from "@/lib/decisionsRefusals";

const DECISIONS_PATH = "/api/decisions";

export type AdviseParams = {
  panId: string;
  /** Omit for the pan's current twin state. */
  asOf?: string;
  /** Name the rule version to apply, rather than letting the engine choose. */
  ruleId?: string;
  /** A what-if rainfall depth in mm, used instead of the twin's forecast. */
  rainfallMm?: number;
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
    const code = (body as { code?: unknown } | null)?.code;
    throw new ApiError(
      messageFromDetail(detail, `Request failed with status ${response.status}.`),
      {
        status: response.status,
        code: typeof code === "string" ? (code as AdviceRefusal) : null,
        fields: {},
      },
    );
  }

  return body as T;
}

/**
 * The operational recommendation for one pan, with the evidence behind it.
 *
 * Throws an {@link ApiError} whose `code` is the backend's refusal code, so a
 * caller can tell "no rule has been written" from "the twin has no state" without
 * matching on prose.
 */
export function advise({
  panId,
  asOf,
  ruleId,
  rainfallMm,
}: AdviseParams): Promise<DecisionAdvice> {
  const body: Record<string, string | number> = { pan_id: panId };
  if (asOf) body.as_of = asOf;
  if (ruleId) body.rule_id = ruleId;
  if (typeof rainfallMm === "number") body.rainfall_mm = rainfallMm;
  return request<DecisionAdvice>(`${DECISIONS_PATH}/advise`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * The most recent recommendation for a pan.
 *
 * Returns the server's `found` flag alongside the advice, and a pan nobody has
 * asked about is `{ found: false, advice: null }` rather than an error.
 */
export function latestAdvice(panId: string): Promise<DecisionLatest> {
  return request<DecisionLatest>(
    `${DECISIONS_PATH}/${encodeURIComponent(panId)}/latest`,
  );
}

/** The advice log, newest first, optionally narrowed to a pan and an action. */
export function adviceLog(
  params: { panId?: string; action?: DecisionAction; limit?: number } = {},
): Promise<{ count: number; advice: DecisionAdvice[] }> {
  const query = new URLSearchParams();
  if (params.panId) query.set("pan_id", params.panId);
  if (params.action) query.set("action", params.action);
  if (typeof params.limit === "number") query.set("limit", String(params.limit));
  const suffix = query.toString();
  return request<{ count: number; advice: DecisionAdvice[] }>(
    `${DECISIONS_PATH}/log${suffix ? `?${suffix}` : ""}`,
  );
}

/**
 * What to show when a recommendation could not be issued.
 *
 * Returns the fixed sentence for the refusals that are a normal state of the
 * system, and the server's own explanation for anything else. Each of those
 * sentences is a statement about the system rather than about the request, so no
 * retry is offered for them — asking again cannot change an answer that depends on
 * a rule nobody has written yet.
 */
export function adviceUnavailableMessage(error: ApiError): string {
  const known = ADVICE_REFUSAL_MESSAGES[error.code as AdviceRefusal];
  return known ?? error.message;
}

/**
 * Whether asking again could change the answer.
 *
 * False for every refusal this client models, and false for a connectivity
 * failure only if it is not retryable. A rule that has not been written will not
 * appear by waiting, so a retry button on that state invites an operator to keep
 * pressing it.
 */
export function adviceCanRetry(error: ApiError): boolean {
  if (error.code !== null && error.code in ADVICE_REFUSAL_MESSAGES) return false;
  return error.status === 0 || error.status >= 500;
}
