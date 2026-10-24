/**
 * WhatsApp rules, tested without React or `fetch`.
 *
 * These assertions are about what the panel is *allowed to say*. A send record is
 * the easiest thing in this application to overstate: it is one click, and a click
 * that gets rendered as "the farmer was told" is a false claim about a real person's
 * access to advice. So the vocabulary has one member, the outcome wording has no
 * `delivered` case to write, and the button refuses to be enabled in states the
 * server has already refused.
 */

import { describe, expect, it } from "vitest";
import {
  DELIVERY_ACTIONS,
  DELIVERY_STATUSES,
  SEND_BUTTON_LABELS,
  WHATSAPP_REFUSALS,
  canSend,
  deliveryClaimsOnlyInitiation,
  describeDelivery,
  refusalIsConfigurable,
  sendButtonState,
  sendOutcomeMessage,
  type WhatsAppDelivery,
  type WhatsAppPreview,
} from "@/lib/whatsapp";

const PREVIEW: WhatsAppPreview = {
  recommendation_id: "rec-1",
  pan_id: "kzn-2026-a01",
  recipient_contact_reference: "+919•••••••10",
  message: "SaltLens DSS - Salt-pan recommendation",
  message_sha256: `sha256:${"a".repeat(64)}`,
  message_template_version: "whatsapp-message-v1",
  recommended_action: "HARVEST NOW",
  priority: "medium",
  rule_name: "harvest-at-target",
  rule_version: 3,
  farmer_name: "Ravi Patel",
};

const DELIVERY: WhatsAppDelivery = {
  delivery_id: "del-1",
  recommendation_id: "rec-1",
  pan_id: "kzn-2026-a01",
  recommended_action: "HARVEST NOW",
  priority: "medium",
  rule_name: "harvest-at-target",
  rule_version: 3,
  specification_hash: `sha256:${"a".repeat(64)}`,
  farmer_contact_reference: "+919•••••••10",
  farmer_name: "Ravi Patel",
  channel: "whatsapp",
  action: "SEND_INITIATED",
  delivery_status: "not_confirmed",
  delivery_confirmed: false,
  farmer_response_recorded: false,
  physical_action_recorded: false,
  message_template_version: "whatsapp-message-v1",
  message_sha256: `sha256:${"b".repeat(64)}`,
  message_character_count: 445,
  operator_id: null,
  initiated_at: "2026-03-13T09:00:00Z",
  created_at: "2026-03-13T09:00:00Z",
};

describe("the vocabulary of a send record", () => {
  it("has exactly one action", () => {
    // "Delivered" and "read" are not refusals to implement later — they are facts
    // this system cannot observe, so the vocabulary must not have room for them.
    expect(DELIVERY_ACTIONS).toEqual(["SEND_INITIATED"]);
  });

  it("has exactly one delivery status, and it is the unconfirmed one", () => {
    expect(DELIVERY_STATUSES).toEqual(["not_confirmed"]);
  });

  it("carries the three booleans that cannot be flipped", () => {
    expect(DELIVERY.delivery_confirmed).toBe(false);
    expect(DELIVERY.farmer_response_recorded).toBe(false);
    expect(DELIVERY.physical_action_recorded).toBe(false);
  });
});

describe("deliveryClaimsOnlyInitiation", () => {
  it("accepts an honest record", () => {
    expect(deliveryClaimsOnlyInitiation(DELIVERY)).toBe(true);
  });

  it.each([
    ["delivery_confirmed", { delivery_confirmed: true }],
    ["farmer_response_recorded", { farmer_response_recorded: true }],
    ["physical_action_recorded", { physical_action_recorded: true }],
    ["delivery_status", { delivery_status: "delivered" as never }],
    ["action", { action: "DELIVERED" as never }],
  ])("rejects a record where %s claims more than a click", (_field, change) => {
    // If any of these ever came back true, the panel must fall back to reading the
    // record rather than rendering its own success wording.
    expect(deliveryClaimsOnlyInitiation({ ...DELIVERY, ...change })).toBe(false);
  });
});

describe("describeDelivery", () => {
  it("describes the press and the moment, not an arrival", () => {
    const text = describeDelivery(DELIVERY);

    expect(text).toContain("Send initiated");
    expect(text).not.toMatch(/deliver|arriv|receiv/i);
  });
});

describe("sendOutcomeMessage", () => {
  it("says what the operator has to do next", () => {
    const message = sendOutcomeMessage(
      {
        delivery: DELIVERY,
        click_to_chat_url: "https://wa.me/919876543210?text=SaltLens",
        message: PREVIEW.message,
        recipient_contact_reference: "+919•••••••10",
        delivery_confirmed: false,
      },
      false,
    );

    expect(message).toContain("press send there");
    expect(message).toContain("only that you started a send");
    // No word that could be read as a receipt. The instruction to press send inside
    // WhatsApp is phrased so that it cannot be skimmed as "it has been sent".
    expect(message.toLowerCase()).not.toMatch(/deliver|arriv|receiv|was sent|has been sent/);
  });

  it("says nothing was sent and nothing recorded when the send failed", () => {
    const message = sendOutcomeMessage(null, true);

    expect(message).toContain("Nothing was sent");
    expect(message).toContain("Nothing has been recorded");
  });

  it("has no wording for a delivered message to fall into", () => {
    // Enumerated rather than asserted with `not.toContain`, because the failure this
    // guards against is someone adding a fourth branch later without noticing.
    const outcomes = [sendOutcomeMessage(null, true), sendOutcomeMessage(null, false)];

    for (const outcome of outcomes) {
      expect(outcome.toLowerCase()).not.toContain("delivered to the farmer");
      expect(outcome.toLowerCase()).not.toContain("message sent");
    }
  });
});

describe("canSend", () => {
  it("is true for a preview with a recipient and text", () => {
    expect(canSend(PREVIEW)).toBe(true);
  });

  it("is false before the preview has loaded", () => {
    expect(canSend(null)).toBe(false);
  });

  it("is false when no recipient could be resolved", () => {
    expect(canSend({ ...PREVIEW, recipient_contact_reference: null })).toBe(false);
  });

  it("is false for an empty message", () => {
    expect(canSend({ ...PREVIEW, message: "   " })).toBe(false);
  });
});

describe("sendButtonState", () => {
  it("is ready once a preview has loaded", () => {
    expect(sendButtonState(PREVIEW, false, false, null)).toEqual({ kind: "ready" });
  });

  it("is sending while a send is in flight, whatever else is true", () => {
    // The reason this exists: without it a second press could record a second
    // initiation of a message the operator has not seen the first of.
    expect(sendButtonState(PREVIEW, false, true, null)).toEqual({ kind: "sending" });
    expect(sendButtonState(null, true, true, null)).toEqual({ kind: "sending" });
  });

  it("is unavailable when the server has refused, and says why", () => {
    const state = sendButtonState(null, false, false, "farmer_contact_not_configured");

    expect(state.kind).toBe("unavailable");
    if (state.kind === "unavailable") {
      expect(state.reason).toContain("no farmer WhatsApp number");
    }
  });

  it("names demonstration advice as never sent", () => {
    const state = sendButtonState(
      null,
      false,
      false,
      "demonstration_recommendation_not_sendable",
    );

    expect(state.kind).toBe("unavailable");
    if (state.kind === "unavailable") {
      expect(state.reason.toLowerCase()).toContain("never sent");
    }
  });

  it("is unavailable for a preview with nothing to send", () => {
    const state = sendButtonState(
      { ...PREVIEW, message: "" },
      false,
      false,
      null,
    );

    expect(state.kind).toBe("unavailable");
  });

  it("has a distinct label for every state", () => {
    const labels = Object.values(SEND_BUTTON_LABELS);
    expect(new Set(labels).size).toBe(labels.length);
  });

  it("never labels the button with a word implying delivery", () => {
    for (const label of Object.values(SEND_BUTTON_LABELS)) {
      expect(label.toLowerCase()).not.toMatch(/deliver|sent successfully|confirmed/);
    }
  });
});

describe("refusalIsConfigurable", () => {
  it("is true for the two the operator can fix on the pan", () => {
    expect(refusalIsConfigurable("farmer_contact_not_configured")).toBe(true);
    expect(refusalIsConfigurable("farmer_contact_invalid")).toBe(true);
  });

  it("is false for a record that cannot be sent whatever the operator does", () => {
    expect(refusalIsConfigurable("recommendation_unknown")).toBe(false);
    expect(refusalIsConfigurable("recommendation_not_a_decision")).toBe(false);
    expect(refusalIsConfigurable("pan_unknown")).toBe(false);
    expect(refusalIsConfigurable("demonstration_recommendation_not_sendable")).toBe(
      false,
    );
  });

  it("classifies every refusal this client models", () => {
    // A refusal added to the vocabulary without a decision here would fall through
    // to "not configurable" and quietly point an operator at the wrong place.
    for (const refusal of WHATSAPP_REFUSALS) {
      expect(typeof refusalIsConfigurable(refusal)).toBe("boolean");
    }
  });
});
