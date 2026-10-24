/**
 * WhatsApp client tests.
 *
 * The assertions that matter are the two this client was written around: the request
 * carries no message, no recipient and no pan — the server reads all three from the
 * stored recommendation — and a refusal to send is one fixed sentence naming what
 * would have to be true, not the server's prose passed through to an operator.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  farmerWhatsAppLink,
  sendWhatsApp,
  whatsappCanRetry,
  whatsappPreview,
  whatsappSendsFor,
  whatsappUnavailableMessage,
} from "@/lib/whatsappApi";
import { WHATSAPP_REFUSAL_MESSAGES } from "@/lib/whatsapp";

type FetchFn = (input: string, init: RequestInit) => Promise<unknown>;

const PREVIEW = {
  recommendation_id: "rec-1",
  pan_id: "kzn-2026-a01",
  recipient_contact_reference: "+919•••••••10",
  message: "SaltLens DSS - Salt-pan recommendation\n\nRecommendation: HARVEST NOW",
  message_sha256: `sha256:${"a".repeat(64)}`,
  message_template_version: "whatsapp-message-v1",
  recommended_action: "HARVEST NOW",
  priority: "medium",
  rule_name: "harvest-at-target",
  rule_version: 3,
  farmer_name: "Ravi Patel",
};

const SEND_RESULT = {
  delivery: {
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
  },
  click_to_chat_url: "https://wa.me/919876543210?text=SaltLens",
  message: PREVIEW.message,
  recipient_contact_reference: "+919•••••••10",
  delivery_confirmed: false,
};

const LINK_RESULT = {
  pan_id: "kzn-2026-b01",
  recipient_contact_reference: "+919•••••••10",
  click_to_chat_url: "https://wa.me/919876543210?text=A%20message",
  message_character_count: 5,
};

function mockFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn<FetchFn>(async () => ({
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(body),
  }));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall(fetchMock: ReturnType<typeof mockFetch>): [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1);
  if (!call) throw new Error("no request was made");
  return call;
}

function lastBody(fetchMock: ReturnType<typeof mockFetch>): Record<string, unknown> {
  return JSON.parse(lastCall(fetchMock)[1].body as string);
}

describe("sendWhatsApp", () => {
  it("sends no message, recipient, pan or action", async () => {
    // The whole body is an operator identifier. A message, a number or a pan here
    // would be a way to record a communication the stored recommendation does not
    // support, so the body is asserted field by field rather than by count.
    const fetchMock = mockFetch(SEND_RESULT);

    await sendWhatsApp("rec-1");

    const [url, init] = lastCall(fetchMock);
    expect(url).toContain("/api/decisions/rec-1/whatsapp-send");
    expect(init.method).toBe("POST");
    expect(Object.keys(lastBody(fetchMock))).toEqual([]);
  });

  it("carries an operator identifier when one is given", async () => {
    const fetchMock = mockFetch(SEND_RESULT);

    await sendWhatsApp("rec-1", { operatorId: "op-team-3" });

    expect(lastBody(fetchMock)).toEqual({ operator_id: "op-team-3" });
  });

  it("escapes the identifier in the path", async () => {
    const fetchMock = mockFetch(SEND_RESULT);

    await sendWhatsApp("rec/../secret");

    expect(lastCall(fetchMock)[0]).toContain("rec%2F..%2Fsecret");
  });

  it("returns the record and the link to open", async () => {
    mockFetch(SEND_RESULT);

    const result = await sendWhatsApp("rec-1");

    expect(result.delivery.action).toBe("SEND_INITIATED");
    expect(result.click_to_chat_url).toContain("https://wa.me/");
    expect(result.delivery.delivery_confirmed).toBe(false);
  });

  it("raises the refusal code so a caller can branch on it", async () => {
    mockFetch({ code: "farmer_contact_not_configured", detail: "server prose" }, 409);

    await expect(sendWhatsApp("rec-1")).rejects.toMatchObject({
      code: "farmer_contact_not_configured",
      status: 409,
    });
  });

  it("reports an unreachable API rather than a refusal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<FetchFn>(async () => {
        throw new Error("network down");
      }),
    );

    await expect(sendWhatsApp("rec-1")).rejects.toMatchObject({ status: 0 });
  });
});

describe("whatsappPreview", () => {
  it("reads the message and the masked recipient without a body", async () => {
    const fetchMock = mockFetch(PREVIEW);

    const preview = await whatsappPreview("rec-1");

    const [url, init] = lastCall(fetchMock);
    expect(url).toContain("/api/decisions/rec-1/whatsapp-preview");
    expect(init.method).toBeUndefined();
    expect(preview.recipient_contact_reference).toBe("+919•••••••10");
  });

  it("never asks the server for the full number", async () => {
    const fetchMock = mockFetch(PREVIEW);

    await whatsappPreview("rec-1");

    expect(lastCall(fetchMock)[0]).not.toContain("unmasked");
    expect(lastCall(fetchMock)[0]).not.toContain("reveal");
  });
});

describe("whatsappSendsFor", () => {
  it("reads the history", async () => {
    const fetchMock = mockFetch({
      recommendation_id: "rec-1",
      found: true,
      deliveries: [SEND_RESULT.delivery],
    });

    const response = await whatsappSendsFor("rec-1");

    expect(lastCall(fetchMock)[0]).toContain("/api/decisions/rec-1/whatsapp-sends");
    expect(response.deliveries).toHaveLength(1);
  });

  it("treats an empty history as a normal state", async () => {
    mockFetch({ recommendation_id: "rec-1", found: false, deliveries: [] });

    const response = await whatsappSendsFor("rec-1");

    expect(response.found).toBe(false);
    expect(response.deliveries).toEqual([]);
  });
});

describe("farmerWhatsAppLink", () => {
  it("posts the composed message and no recipient", async () => {
    // The message is the whole body. A number or a recipient here would be a way to
    // aim a link at a pan this request is not about, so the body is asserted by
    // field rather than by count.
    const fetchMock = mockFetch(LINK_RESULT);

    await farmerWhatsAppLink("kzn-2026-b01", "A message for the farmer.");

    const [url, init] = lastCall(fetchMock);
    expect(url).toContain("/api/pans/kzn-2026-b01/whatsapp-link");
    expect(init.method).toBe("POST");
    expect(lastBody(fetchMock)).toEqual({ message: "A message for the farmer." });
  });

  it("sends the message unencoded, so the server owns the URL encoding", async () => {
    const fetchMock = mockFetch(LINK_RESULT);
    const message = "Line one\nLine two & more";

    await farmerWhatsAppLink("kzn-2026-b01", message);

    expect(lastBody(fetchMock)).toEqual({ message });
  });

  it("escapes the pan identifier in the path", async () => {
    const fetchMock = mockFetch(LINK_RESULT);

    await farmerWhatsAppLink("kzn/../secret", "Hello");

    expect(lastCall(fetchMock)[0]).toContain("kzn%2F..%2Fsecret");
  });

  it("returns the link and the masked recipient", async () => {
    mockFetch(LINK_RESULT);

    const link = await farmerWhatsAppLink("kzn-2026-b01", "Hello");

    expect(link.click_to_chat_url).toContain("https://wa.me/");
    expect(link.recipient_contact_reference).toBe("+919•••••••10");
    expect(link.message_character_count).toBe(5);
    expect(link.pan_id).toBe("kzn-2026-b01");
  });

  it("raises the contact refusal code so a caller can branch on it", async () => {
    mockFetch({ code: "farmer_contact_not_configured", detail: "server prose" }, 409);

    await expect(farmerWhatsAppLink("kzn-2026-b01", "Hello")).rejects.toMatchObject({
      code: "farmer_contact_not_configured",
      status: 409,
    });
  });
});

describe("whatsappUnavailableMessage", () => {
  it("shows the fixed sentence for every refusal this client models", () => {
    for (const [code, sentence] of Object.entries(WHATSAPP_REFUSAL_MESSAGES)) {
      const message = whatsappUnavailableMessage(
        new ApiError("server prose", { status: 409, code }),
      );
      expect(message).toBe(sentence);
    }
  });

  it("does not tell an operator that nothing was sent when a record was written", () => {
    // Every refusal message opens by saying what did not happen. If one changed to
    // be reassuring, the operator could believe a message went out when none did.
    for (const sentence of Object.values(WHATSAPP_REFUSAL_MESSAGES)) {
      expect(sentence).toMatch(/Nothing was sent/);
    }
  });

  it("passes an unrecognised refusal through rather than flattening it", () => {
    // An unrecognised code is a fact about the system this client does not model.
    // Replacing it with a generic sentence would hide that.
    const message = whatsappUnavailableMessage(
      new ApiError("the server said something new", {
        status: 409,
        code: "farmer_contact_blocked",
      }),
    );

    expect(message).toBe("the server said something new");
  });

  it("falls back to the server message when there is no code", () => {
    const message = whatsappUnavailableMessage(
      new ApiError("Internal Server Error", { status: 500 }),
    );

    expect(message).toBe("Internal Server Error");
  });
});

describe("whatsappCanRetry", () => {
  it("offers no retry for any modelled refusal", () => {
    // A missing contact stays missing until somebody enters it, and a record that
    // named no action will not start naming one. Pressing again cannot help.
    for (const code of Object.keys(WHATSAPP_REFUSAL_MESSAGES)) {
      expect(
        whatsappCanRetry(new ApiError("x", { status: 409, code })),
      ).toBe(false);
    }
  });

  it("offers a retry when the API was unreachable", () => {
    expect(whatsappCanRetry(new ApiError("x", { status: 0 }))).toBe(true);
  });

  it("offers a retry for a server fault", () => {
    expect(whatsappCanRetry(new ApiError("x", { status: 503 }))).toBe(true);
  });

  it("does not offer a retry for a malformed request", () => {
    expect(whatsappCanRetry(new ApiError("x", { status: 422 }))).toBe(false);
  });
});
