/**
 * HTTP client for operator feedback (Module 14).
 *
 * Two properties mirror the decisions client, for the same reasons.
 *
 * **There is no path by which the browser supplies the pan or the recommended
 * action.** The body names a recommendation and a disposition; the pan and the
 * action recommended are the server's to read from the stored advice, so a record
 * can never be filed for the wrong pan or against advice that was never issued.
 * The one figure the operator may supply is the action actually taken, sent as
 * `actual_action` so it is never confused with the recommended one.
 *
 * **A refusal to record is not an error to display verbatim.** The server explains
 * a refusal for a log or a support conversation; the panel shows one fixed
 * sentence per code. {@link feedbackUnavailableMessage} maps the code to that
 * sentence, and a code it does not recognise is passed through rather than
 * flattened, because an unrecognised refusal is a fact about the system this
 * client does not yet model.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import { FEEDBACK_REFUSALS, type FeedbackRefusal, type OperatorFeedback, type OperatorFeedbackListResponse, type FeedbackSubmission } from "@/lib/operatorFeedback";

const FEEDBACK_PATH = "/api/feedback";

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
        code: typeof code === "string" ? (code as FeedbackRefusal) : null,
        fields: {},
      },
    );
  }

  return body as T;
}

/**
 * Files one operator decision for a stored recommendation.
 *
 * Throws an {@link ApiError} whose `code` is the backend's refusal code, so a
 * caller can tell an already-recorded decision from a recommendation that never
 * named an action without matching on prose. On success the full stored record is
 * returned, ready to be shown as the recorded state.
 */
export function recordFeedback(submission: FeedbackSubmission): Promise<OperatorFeedback> {
  const body: Record<string, string> = {
    recommendation_id: submission.recommendationId,
    decision: submission.decision,
  };
  if (submission.actualAction) body.actual_action = submission.actualAction;
  if (submission.operatorId) body.operator_id = submission.operatorId;
  if (submission.notes) body.notes = submission.notes;
  return request<OperatorFeedback>(`${FEEDBACK_PATH}/`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/**
 * The recorded decision for one recommendation, when one exists.
 *
 * The backend enforces one decision per recommendation, so the answer is either
 * a single record or an empty list — never many. A recommendation nobody has
 * answered is `count: 0`, not an error.
 */
export function feedbackForRecommendation(
  recommendationId: string,
): Promise<OperatorFeedbackListResponse> {
  const query = new URLSearchParams();
  query.set("recommendation_id", recommendationId);
  return request<OperatorFeedbackListResponse>(`${FEEDBACK_PATH}/?${query.toString()}`);
}

/** One fixed sentence per refusal, and what would have to be true to avoid it. */
export const FEEDBACK_REFUSAL_MESSAGES: Record<FeedbackRefusal, string> = {
  recommendation_unknown:
    "No decision was recorded — the recommendation no longer exists on the server.",
  recommendation_not_a_decision:
    "No decision was recorded — that recommendation named no action to accept, modify or reject.",
  decision_already_recorded:
    "No change was filed — that recommendation already has a recorded decision.",
  unknown_decision_vocabulary:
    "No decision was recorded — only accepted, modified or rejected can be filed.",
};

/**
 * What to show when a decision could not be recorded.
 *
 * Returns the fixed sentence for the refusals that are a normal state of the
 * system, and the server's own explanation for anything else. Each of those
 * sentences is a statement about the system rather than about the request, so no
 * retry is offered for them — asking again cannot change a decision that has
 * already been recorded.
 */
export function feedbackUnavailableMessage(error: ApiError): string {
  const known = FEEDBACK_REFUSAL_MESSAGES[error.code as FeedbackRefusal];
  return known ?? error.message;
}

/**
 * Whether asking again could change the answer.
 *
 * False for every refusal this client models, and for a connectivity failure
 * only if it is not retryable. A decision that has already been recorded will not
 * unrecord itself by pressing the button again.
 */
export function feedbackCanRetry(error: ApiError): boolean {
  if (error.code !== null && (FEEDBACK_REFUSALS as readonly string[]).includes(error.code)) {
    return false;
  }
  return error.status === 0 || error.status >= 500;
}