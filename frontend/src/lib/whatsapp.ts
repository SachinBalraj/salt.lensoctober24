/**
 * Farmer communication over WhatsApp — types, labels and formatting.
 *
 * Free of React and of `fetch`, so the rules the send panel depends on can be unit
 * tested directly.
 *
 * The idea this module enforces is that **a send is not a delivery, and a delivery
 * would not be an outcome.** The backend can only learn that an operator pressed a
 * button: click-to-chat hands the message to WhatsApp on the operator's own device
 * and this system is not on the conversation afterwards, so there is no receipt, no
 * reply and no way to learn that a farmer read anything. Every type here therefore
 * carries `delivery_status`, `delivery_confirmed`, `farmer_response_recorded` and
 * `physical_action_recorded` as explicit fields rather than leaving them to be
 * inferred from their absence — a client that forgot to check would then have
 * nothing to forget.
 *
 * **A recorded send supplies no message and no recipient.** The only field a send
 * request carries is an operator identifier; the pan, the farmer, the number and the
 * text are read server-side from the stored recommendation. {@link WhatsAppSendRequest}
 * has exactly one optional field, and that is the guarantee rather than a limitation
 * of the current UI.
 *
 * **A link is the one place text is supplied, and it records nothing.**
 * A panel may compose a message from live recommendation state — more than the
 * stored recommendation carries — and ask the client for {@link WhatsAppLink}, which
 * the server builds from that text and the pan's stored number. It writes no
 * document and stores no message, and the recipient is still the pan on the path
 * rather than a number the caller names.
 *
 * **A send is not a decision.** Accepting, modifying or rejecting the advice is a
 * separate, explicit act (Module 14) and pressing this button does none of it. A
 * record of a send says the operator reached for the button, nothing about whether
 * they agreed with the recommendation.
 */

import type { DecisionAction } from "@/lib/decisions";

/** The only communication channel this project sends over. */
export const NOTIFICATION_CHANNELS = ["whatsapp"] as const;

export type NotificationChannel = (typeof NOTIFICATION_CHANNELS)[number];

/**
 * What a record of a send can say happened.
 *
 * One member, and that is the point. `SEND_INITIATED` describes a click; a value
 * meaning delivered or read would describe something this system cannot observe, so
 * the vocabulary does not have one.
 */
export const DELIVERY_ACTIONS = ["SEND_INITIATED"] as const;

export type DeliveryAction = (typeof DELIVERY_ACTIONS)[number];

/**
 * Whether the message arrived. Always `not_confirmed`, for the same reason: the
 * system learns nothing about the conversation after the URL is opened.
 */
export const DELIVERY_STATUSES = ["not_confirmed"] as const;

export type DeliveryStatus = (typeof DELIVERY_STATUSES)[number];

/** Plain words for the one action a record can carry. */
export const DELIVERY_ACTION_LABELS: Record<DeliveryAction, string> = {
  SEND_INITIATED: "Send initiated",
};

/**
 * The mask shown instead of a farmer's number.
 *
 * The unmasked number exists only on the pan document, where the send needs it.
 * Every type here carries this rather than the number, so a value read off the
 * screen and into a ticket or a screenshot stays useless for reaching the farmer.
 */
export type MaskedContact = string;

/** The message and recipient the server would send, previewed without sending. */
export type WhatsAppPreview = {
  recommendation_id: string;
  pan_id: string;
  recipient_contact_reference: MaskedContact | null;
  message: string;
  message_sha256: string;
  message_template_version: string;
  recommended_action: DecisionAction;
  /** A plain string, not a ranking this client defines — the rule set it. */
  priority: string;
  rule_name: string | null;
  rule_version: number | null;
  /** The farmer as recorded on the pan, when one was. Never required. */
  farmer_name: string | null;
};

/** One recorded press of the send button. */
export type WhatsAppDelivery = {
  delivery_id: string;
  recommendation_id: string;
  pan_id: string;
  recommended_action: DecisionAction;
  priority: string;
  rule_name: string | null;
  rule_version: number | null;
  specification_hash: string | null;
  farmer_contact_reference: MaskedContact;
  farmer_name: string | null;
  channel: NotificationChannel;
  action: DeliveryAction;
  delivery_status: DeliveryStatus;
  /** Always false. No integration exists that could observe delivery. */
  delivery_confirmed: boolean;
  /** Always false. A reply arrives in WhatsApp, not here. */
  farmer_response_recorded: boolean;
  /** Always false. Physical actions are recorded against the pan, not by a send. */
  physical_action_recorded: boolean;
  message_template_version: string;
  message_sha256: string;
  message_character_count: number;
  operator_id: string | null;
  initiated_at: string;
  created_at: string;
};

/**
 * The result of one press: the record, and the link the operator's browser opens.
 *
 * `click_to_chat_url` is returned rather than fetched. Nothing is transmitted from
 * the browser to a farmer by this application; the browser navigates to WhatsApp and
 * the operator presses send there.
 */
export type WhatsAppSendResult = {
  delivery: WhatsAppDelivery;
  click_to_chat_url: string;
  message: string;
  recipient_contact_reference: MaskedContact;
  delivery_confirmed: boolean;
};

/** Every send recorded for one recommendation. Empty is a normal state. */
export type WhatsAppDeliveryListResponse = {
  recommendation_id: string;
  found: boolean;
  deliveries: WhatsAppDelivery[];
};

/**
 * A `wa.me` link for text this panel composed, plus the masked recipient.
 *
 * The number is never returned as its own field: it exists only inside the link,
 * which is what the browser needs to open the chat and no more than the recorded-send
 * response already hands back. `message_character_count` is the server's own count of
 * the text it encoded, so a panel can check the message it composed is the one the
 * link carries without holding the URL open.
 */
export type WhatsAppLink = {
  pan_id: string;
  recipient_contact_reference: MaskedContact;
  click_to_chat_url: string;
  message_character_count: number;
};

/**
 * The whole body a send request may carry.
 *
 * Deliberately a single optional field. There is no message, no recipient, no pan
 * and no action to override, because any of those would let a record be written for
 * a communication the stored recommendation does not support.
 */
export type WhatsAppSendRequest = {
  /** Self-reported: this application has no authentication, so it is not a verified identity. */
  operatorId?: string;
};

/** Every way a send can be refused, each needing a different thing to be true first. */
export const WHATSAPP_REFUSALS = [
  "recommendation_unknown",
  "recommendation_not_a_decision",
  "pan_unknown",
  "farmer_contact_not_configured",
  "farmer_contact_invalid",
  "demonstration_recommendation_not_sendable",
] as const;

export type WhatsAppRefusal = (typeof WHATSAPP_REFUSALS)[number];

/**
 * One fixed sentence per refusal.
 *
 * The server's `detail` is written for a log or a support conversation. These are
 * written for an operator looking at a panel, and each names what would have to be
 * true to avoid the refusal.
 */
export const WHATSAPP_REFUSAL_MESSAGES: Record<WhatsAppRefusal, string> = {
  recommendation_unknown:
    "Nothing was sent — this recommendation is no longer on the server. Refresh to see the current advice.",
  recommendation_not_a_decision:
    "Nothing was sent — that record is an advisory note and named no action to communicate.",
  pan_unknown:
    "Nothing was sent — the pan this recommendation was made for is not registered.",
  farmer_contact_not_configured:
    "Nothing was sent — no farmer WhatsApp number is recorded for this pan. Add one on the pan, then send.",
  farmer_contact_invalid:
    "Nothing was sent — the number recorded for this pan is not a usable international format. Correct it on the pan, then send.",
  demonstration_recommendation_not_sendable:
    "Nothing was sent — that recommendation was built from demonstration data and is never sent to a farmer.",
};

/**
 * The refusals an operator can do something about.
 *
 * The first three are not the operator's to fix: the record they are looking at is
 * not one that can be sent, whatever they do. The last two are a missing or
 * malformed entry they can correct, and the message for both points at the pan.
 */
const CONFIGURABLE_REFUSALS: readonly WhatsAppRefusal[] = [
  "farmer_contact_not_configured",
  "farmer_contact_invalid",
];

/** Whether the refusal is something the operator can fix on the pan. */
export function refusalIsConfigurable(refusal: WhatsAppRefusal): boolean {
  return CONFIGURABLE_REFUSALS.includes(refusal);
}

/**
 * Whether a send can be attempted at all.
 *
 * False for a record that named no action and false for a masked recipient the
 * preview could not produce. Showing an enabled button in either case would promise
 * an outcome the server has already said it cannot deliver.
 */
export function canSend(preview: WhatsAppPreview | null): boolean {
  if (preview === null) return false;
  if (preview.recipient_contact_reference === null) return false;
  return preview.message.trim().length > 0;
}

/**
 * The sentence describing what a recorded send did.
 *
 * Always about the press, never about arrival. The wording is deliberately past
 * tense of the button rather than of the message: "Send opened in WhatsApp" is
 * checkable against what happened on this machine, "Message sent to the farmer" is
 * not.
 */
export function describeDelivery(delivery: WhatsAppDelivery): string {
  return `${DELIVERY_ACTION_LABELS[delivery.action]} at ${new Date(
    delivery.initiated_at,
  ).toLocaleString()}`;
}

/**
 * Whether the backend's own flags agree that nothing is known beyond the press.
 *
 * A defensive check, not a type assertion. If a future response ever carried
 * `delivery_confirmed: true`, a client that trusted the type would render "delivered"
 * — so this asks the data, and {@link deliveryClaimsOnlyInitiation} is what the UI
 * branches on.
 */
export function deliveryClaimsOnlyInitiation(delivery: WhatsAppDelivery): boolean {
  return (
    delivery.action === "SEND_INITIATED" &&
    delivery.delivery_status === "not_confirmed" &&
    delivery.delivery_confirmed === false &&
    delivery.farmer_response_recorded === false &&
    delivery.physical_action_recorded === false
  );
}

/**
 * What the panel says after a send, in every case including the honest ones.
 *
 * Kept as one function so the success wording and the "nothing known beyond this"
 * wording cannot drift apart in two components. There is deliberately no
 * `delivered` case to write: the situation cannot be detected, so it must not be
 * renderable.
 */
export function sendOutcomeMessage(
  result: WhatsAppSendResult | null,
  failed: boolean,
): string {
  if (failed) {
    return "Nothing was sent. Nothing has been recorded either.";
  }
  if (result === null) return "";
  if (!deliveryClaimsOnlyInitiation(result.delivery)) {
    // Unreachable against this backend, and deliberately phrased as a fact about
    // the record rather than a claim about the message.
    return "A send was recorded. See the record for what it states.";
  }
  return (
    `WhatsApp is opening with the message pre-filled to ${result.recipient_contact_reference}. ` +
    "The message goes out when you press send there. This system has recorded only " +
    "that you started a send."
  );
}

/** The word to put on the button, which changes with what the panel is doing. */
export type SendButtonState =
  | { kind: "loading" }
  | { kind: "sending" }
  | { kind: "ready" }
  | { kind: "unavailable"; reason: string };

/**
 * The button's state, from the preview and whether a send is in flight.
 *
 * A send in flight disables the button regardless of anything else. Without that,
 * a second press could record a second initiation of a message the operator has not
 * seen the first of — and the audit trail would show two presses for one intention.
 */
export function sendButtonState(
  preview: WhatsAppPreview | null,
  loading: boolean,
  sending: boolean,
  refusal: WhatsAppRefusal | null,
): SendButtonState {
  if (sending) return { kind: "sending" };
  if (loading) return { kind: "loading" };
  if (refusal !== null) {
    return { kind: "unavailable", reason: WHATSAPP_REFUSAL_MESSAGES[refusal] };
  }
  if (preview === null) return { kind: "loading" };
  if (!canSend(preview)) {
    return {
      kind: "unavailable",
      reason:
        "No message could be prepared for this recommendation, so there is nothing to send.",
    };
  }
  return { kind: "ready" };
}

/** The label on the button for each state. */
export const SEND_BUTTON_LABELS: Record<SendButtonState["kind"], string> = {
  loading: "Preparing message…",
  sending: "Opening WhatsApp…",
  ready: "Send to farmer via WhatsApp",
  unavailable: "Send unavailable",
};
