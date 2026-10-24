/**
 * HTTP client for farmer communication over WhatsApp.
 *
 * Two properties mirror the operator-feedback client, for the same reasons.
 *
 * **A recorded send has no path by which the browser supplies the message or the
 * recipient.** The body carries an operator identifier and nothing else; the pan,
 * the farmer, the number and the text are the server's to read from the stored
 * recommendation. A page that wanted to send something the stored advice does not
 * say would have to add a field here first, and {@link WhatsAppSendRequest} is the
 * whole body.
 *
 * **{@link farmerWhatsAppLink} is the one deliberate exception, and it writes
 * nothing.** Module 12 summarises the live recommendation — which carries more than
 * the stored document does — so the panel composes that text and asks the server for
 * a link. The text is encoded into a `wa.me` URL against the pan's own stored number
 * and no document is recorded, which keeps it a click the operator reviews in
 * WhatsApp rather than a send this application can claim. No field exists for a
 * number: the recipient is the pan named on the path.
 *
 * **A refusal is not an error to display verbatim.** The server explains a refusal
 * for a log; the panel shows one fixed sentence per code via
 * {@link whatsappUnavailableMessage}, passing an unrecognised code through rather
 * than flattening it, because an unrecognised refusal is a fact about the system
 * this client does not yet model.
 */

import { ApiError, getApiBaseUrl } from "@/lib/api";
import {
  WHATSAPP_REFUSALS,
  WHATSAPP_REFUSAL_MESSAGES,
  type WhatsAppDelivery,
  type WhatsAppDeliveryListResponse,
  type WhatsAppLink,
  type WhatsAppPreview,
  type WhatsAppRefusal,
  type WhatsAppSendRequest,
  type WhatsAppSendResult,
} from "@/lib/whatsapp";

const DECISIONS_PATH = "/api/decisions";
const PANS_PATH = "/api/pans";

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
        code: typeof code === "string" ? (code as WhatsAppRefusal) : null,
        fields: {},
      },
    );
  }

  return body as T;
}

/**
 * The message that would be sent, and the masked recipient it would go to.
 *
 * Read before sending so an operator can see the exact text a farmer will receive.
 * Writes nothing — the backend shares one code path between this and the send, so
 * what is previewed is what is sent.
 */
export function whatsappPreview(
  recommendationId: string,
): Promise<WhatsAppPreview> {
  return request<WhatsAppPreview>(
    `${DECISIONS_PATH}/${encodeURIComponent(recommendationId)}/whatsapp-preview`,
  );
}

/**
 * Initiate a WhatsApp send of one stored recommendation.
 *
 * Returns the audit record and the link to open. Nothing is transmitted to a farmer
 * by this call: it records that the operator started a send, and hands back
 * WhatsApp's own click-to-chat URL for their browser to follow.
 */
export function sendWhatsApp(
  recommendationId: string,
  submission: WhatsAppSendRequest = {},
): Promise<WhatsAppSendResult> {
  const body: Record<string, string> = {};
  if (submission.operatorId) body.operator_id = submission.operatorId;
  return request<WhatsAppSendResult>(
    `${DECISIONS_PATH}/${encodeURIComponent(recommendationId)}/whatsapp-send`,
    { method: "POST", body: JSON.stringify(body) },
  );
}

/** Every send recorded for one recommendation. Empty is a normal state, not an error. */
export function whatsappSendsFor(
  recommendationId: string,
): Promise<WhatsAppDeliveryListResponse> {
  return request<WhatsAppDeliveryListResponse>(
    `${DECISIONS_PATH}/${encodeURIComponent(recommendationId)}/whatsapp-sends`,
  );
}

/**
 * A `wa.me` link for text this panel composed, addressed to one pan's farmer.
 *
 * The message is the whole body. The server reads the number from the pan named on
 * the path, percent-encodes the text and returns the link; nothing is stored and
 * nothing is transmitted by this call, so what the operator reviews on screen is
 * still what they must press send on inside WhatsApp. A refusal about the contact
 * arrives as a 409 with a code, and is surfaced through
 * {@link whatsappUnavailableMessage} like any other.
 */
export function farmerWhatsAppLink(
  panId: string,
  message: string,
): Promise<WhatsAppLink> {
  return request<WhatsAppLink>(
    `${PANS_PATH}/${encodeURIComponent(panId)}/whatsapp-link`,
    { method: "POST", body: JSON.stringify({ message }) },
  );
}

/** One fixed sentence per refusal, and what would have to be true to avoid it. */
export const WHATSAPP_REFUSAL_SENTENCES = WHATSAPP_REFUSAL_MESSAGES;

/**
 * What to show when a send could not be initiated.
 *
 * The fixed sentence for the refusals this client models, and the server's own
 * explanation for anything else. Each of those sentences is a statement about the
 * state of the system rather than about the request, so no retry is offered — the
 * fix is a missing or malformed contact on the pan, not another press.
 */
export function whatsappUnavailableMessage(error: ApiError): string {
  const known = WHATSAPP_REFUSAL_SENTENCES[error.code as WhatsAppRefusal];
  return known ?? error.message;
}

/**
 * Whether asking again could change the answer.
 *
 * False for every refusal this client models. A missing contact stays missing until
 * it is entered, and a record that names no action will not start naming one. Only
 * a connectivity failure or a server-side fault is worth retrying.
 */
export function whatsappCanRetry(error: ApiError): boolean {
  if (
    error.code !== null &&
    (WHATSAPP_REFUSALS as readonly string[]).includes(error.code)
  ) {
    return false;
  }
  return error.status === 0 || error.status >= 500;
}

export type { WhatsAppDelivery };
