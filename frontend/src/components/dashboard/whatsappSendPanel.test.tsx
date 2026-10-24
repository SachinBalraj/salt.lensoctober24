/**
 * WhatsApp send panel tests.
 *
 * The failure this screen exists to prevent is an operator believing a farmer was
 * reached. Everything here is therefore about what the panel is *allowed to imply*:
 *
 * - nothing is sent on mount, and the full number never reaches the browser at all;
 * - the button is disabled until a message exists, and disabled again while a send
 *   is in flight, so one intention cannot be recorded twice;
 * - a refusal is one fixed sentence naming what would have to be true, with no retry
 *   offered, because pressing again cannot change a missing contact;
 * - the success wording says WhatsApp is *opening* and that only the press was
 *   recorded -- never that a message was delivered, read or answered;
 * - the panel records a send and nothing else: no decision, no outcome, no
 *   validation, and no second record when the operator presses twice.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WhatsAppSendPanel } from "@/components/dashboard/WhatsAppSendPanel";
import type { DecisionAdvice } from "@/lib/decisions";
import { ApiError } from "@/lib/api";
import { sendWhatsApp, whatsappPreview, whatsappSendsFor } from "@/lib/whatsappApi";
import type { WhatsAppDelivery, WhatsAppPreview } from "@/lib/whatsapp";

vi.mock("@/lib/whatsappApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/whatsappApi")>(
    "@/lib/whatsappApi",
  );
  return {
    ...actual,
    whatsappPreview: vi.fn(),
    whatsappSendsFor: vi.fn(),
    sendWhatsApp: vi.fn(),
  };
});

const ADVICE = {
  recommendation_id: "rec-1",
  pan_id: "kzn-2026-a01",
  recommendation: "HARVEST NOW",
  priority: "medium",
  reason: "The brine is at or below the harvest salinity.",
  notice: "Physics/rule-based recommendation - ML prediction unavailable.",
  timestamp: "2026-03-13T09:00:00Z",
  twin: { as_of: "2026-03-13T09:00:00Z" },
  rule_name: "harvest-at-target",
  rule_version_number: 3,
  specification_hash: `sha256:${"a".repeat(64)}`,
  matched_criteria: "",
  criteria: [],
} as unknown as DecisionAdvice;

const PREVIEW: WhatsAppPreview = {
  recommendation_id: "rec-1",
  pan_id: "kzn-2026-a01",
  recipient_contact_reference: "+919•••••••10",
  message:
    "SaltLens DSS - Salt-pan recommendation\n\nPan: North Pan\n\nRecommendation: HARVEST NOW",
  message_sha256: `sha256:${"a".repeat(64)}`,
  message_template_version: "whatsapp-message-v1",
  recommended_action: "HARVEST NOW",
  priority: "medium",
  rule_name: "harvest-at-target",
  rule_version: 3,
  farmer_name: "Ravi Patel",
};

function delivery(overrides: Partial<WhatsAppDelivery> = {}): WhatsAppDelivery {
  return {
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
    ...overrides,
  };
}

const SEND_RESULT = {
  delivery: delivery(),
  click_to_chat_url: "https://wa.me/919876543210?text=SaltLens",
  message: PREVIEW.message,
  recipient_contact_reference: "+919•••••••10",
  delivery_confirmed: false,
};

let openSpy: ReturnType<typeof vi.fn>;

beforeEach(() => {
  // Call counts are cleared explicitly: `restoreMocks` restores implementations of
  // spies but leaves a `vi.fn()` from the module factory holding the calls an earlier
  // test made, which would make "one record per press" read as many.
  vi.mocked(whatsappPreview).mockReset();
  vi.mocked(whatsappSendsFor).mockReset();
  vi.mocked(sendWhatsApp).mockReset();

  openSpy = vi.fn();
  vi.stubGlobal("open", openSpy);
  vi.mocked(whatsappPreview).mockResolvedValue(PREVIEW);
  vi.mocked(whatsappSendsFor).mockResolvedValue({
    recommendation_id: "rec-1",
    found: false,
    deliveries: [],
  });
  vi.mocked(sendWhatsApp).mockResolvedValue(SEND_RESULT);
});

describe("nothing happens until the operator presses", () => {
  it("writes nothing on mount", async () => {
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");

    expect(sendWhatsApp).not.toHaveBeenCalled();
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("opens nothing on mount", async () => {
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");

    expect(openSpy).not.toHaveBeenCalled();
  });
});

describe("the message and the recipient", () => {
  it("shows the exact text the server prepared", async () => {
    render(<WhatsAppSendPanel advice={ADVICE} />);

    const message = await screen.findByTestId("whatsapp-message");
    expect(message).toHaveTextContent("Recommendation: HARVEST NOW");
  });

  it("shows the recipient masked, and the name when there is one", async () => {
    render(<WhatsAppSendPanel advice={ADVICE} />);

    const recipient = await screen.findByTestId("whatsapp-recipient");
    expect(recipient).toHaveTextContent("+919•••••••10");
    expect(screen.getByText(/Ravi Patel/)).toBeInTheDocument();
  });

  it("never asks the API for the unmasked number", async () => {
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");

    // The client is given only the recommendation id; there is no parameter through
    // which a full number could be requested even if a caller wanted one.
    expect(whatsappPreview).toHaveBeenCalledWith("rec-1");
    for (const call of vi.mocked(whatsappPreview).mock.calls) {
      expect(call).toEqual(["rec-1"]);
    }
    expect(JSON.stringify(PREVIEW)).not.toContain("919876543210");
  });
});

describe("the send", () => {
  it("records a send and opens WhatsApp on an explicit press", async () => {
    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");

    await user.click(screen.getByTestId("whatsapp-send"));

    await waitFor(() => expect(sendWhatsApp).toHaveBeenCalledWith("rec-1", {}));
    expect(openSpy).toHaveBeenCalledWith(
      SEND_RESULT.click_to_chat_url,
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("sends the operator identifier when one is supplied", async () => {
    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} operatorId="op-team-3" />);
    await screen.findByTestId("whatsapp-message");

    await user.click(screen.getByTestId("whatsapp-send"));

    await waitFor(() =>
      expect(sendWhatsApp).toHaveBeenCalledWith("rec-1", { operatorId: "op-team-3" }),
    );
  });

  it("records the send before opening WhatsApp", async () => {
    // A send whose record was never written could not be found later, and a window
    // opened first could be closed without anything having happened at all.
    const order: string[] = [];
    vi.mocked(sendWhatsApp).mockImplementation(async () => {
      order.push("recorded");
      return SEND_RESULT;
    });
    openSpy.mockImplementation(() => {
      order.push("opened");
      return null;
    });

    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");
    await user.click(screen.getByTestId("whatsapp-send"));

    await waitFor(() => expect(order).toEqual(["recorded", "opened"]));
  });

  it("says WhatsApp is opening and that only the press was recorded", async () => {
    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");

    await user.click(screen.getByTestId("whatsapp-send"));

    const outcome = await screen.findByTestId("whatsapp-outcome-message");
    expect(outcome).toHaveTextContent(/press send there/i);
    expect(outcome).toHaveTextContent(/only that you started a send/i);
  });

  it("never claims the message was delivered, read or answered", async () => {
    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");
    await user.click(screen.getByTestId("whatsapp-send"));

    await screen.findByTestId("whatsapp-outcome-message");
    const body = document.body.textContent ?? "";
    expect(body).not.toMatch(/delivered to the farmer|message was sent|farmer has been notified/i);
  });

  it("shows the message text to send by hand if WhatsApp did not open", async () => {
    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");

    await user.click(screen.getByTestId("whatsapp-send"));

    expect(await screen.findByTestId("whatsapp-message-copy")).toHaveTextContent(
      "Recommendation: HARVEST NOW",
    );
  });
});

describe("the button's states", () => {
  it("is disabled while the message is being prepared", async () => {
    render(<WhatsAppSendPanel advice={ADVICE} />);

    expect(screen.getByTestId("whatsapp-send")).toBeDisabled();
    expect(await screen.findByTestId("whatsapp-message")).toBeInTheDocument();
  });

  it("is enabled once a message exists", async () => {
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");

    expect(screen.getByTestId("whatsapp-send")).toBeEnabled();
  });

  it("is disabled again while a send is in flight", async () => {
    // A promise the test holds open, so the button can be observed mid-send rather
    // than after it finished.
    let release = (): void => {};
    const held = new Promise<typeof SEND_RESULT>((resolve) => {
      release = () => resolve(SEND_RESULT);
    });
    vi.mocked(sendWhatsApp).mockReturnValue(held);

    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");

    await user.click(screen.getByTestId("whatsapp-send"));

    expect(await screen.findByRole("button", { name: /Opening WhatsApp/ })).toBeDisabled();
    release();
    await screen.findByTestId("whatsapp-outcome-message");
  });

  it("records one send per press and appends each to the history", async () => {
    // Re-sending is legitimate, and two presses must be two records. Collapsing them
    // into one would report one reach-out for two attempts.
    vi.mocked(sendWhatsApp).mockImplementation(async () => ({
      ...SEND_RESULT,
      delivery: delivery({ delivery_id: `del-${Math.random()}` }),
    }));

    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");

    await user.click(screen.getByTestId("whatsapp-send"));
    await screen.findByTestId("whatsapp-history");
    await user.click(screen.getByTestId("whatsapp-send"));

    await waitFor(() => expect(sendWhatsApp).toHaveBeenCalledTimes(2));
    expect(await screen.findByTestId("whatsapp-history")).toHaveTextContent("Sends started (2)");
  });
});

describe("refusals", () => {
  it("names what would have to be true, and offers no retry", async () => {
    vi.mocked(whatsappPreview).mockRejectedValue(
      new ApiError("server prose", {
        status: 409,
        code: "farmer_contact_not_configured",
      }),
    );

    render(<WhatsAppSendPanel advice={ADVICE} />);

    const error = await screen.findByTestId("whatsapp-preview-error");
    expect(error).toHaveTextContent(/no farmer WhatsApp number is recorded/i);
    // Pressing again cannot create a contact, so no retry is offered.
    expect(screen.queryByTestId("whatsapp-retry")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Try again/ })).not.toBeInTheDocument();
  });

  it("disables the button rather than offering a send it knows will fail", async () => {
    vi.mocked(whatsappPreview).mockRejectedValue(
      new ApiError("server prose", {
        status: 409,
        code: "farmer_contact_invalid",
      }),
    );

    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-preview-error");

    expect(screen.getByTestId("whatsapp-send")).toBeDisabled();
  });

  it("offers a way to the pan when the contact is what is missing", async () => {
    vi.mocked(whatsappPreview).mockRejectedValue(
      new ApiError("server prose", {
        status: 409,
        code: "farmer_contact_not_configured",
      }),
    );

    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-preview-error");

    expect(screen.getByTestId("whatsapp-open-pan")).toBeInTheDocument();
  });

  it("does not offer a way to the pan when the record is the problem", async () => {
    // Pointing an operator at a pan cannot help when the record they are looking at
    // named no action in the first place.
    vi.mocked(whatsappPreview).mockRejectedValue(
      new ApiError("server prose", {
        status: 409,
        code: "recommendation_not_a_decision",
      }),
    );

    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-preview-error");

    expect(screen.queryByTestId("whatsapp-open-pan")).not.toBeInTheDocument();
  });

  it("passes an unrecognised refusal through instead of hiding it", async () => {
    vi.mocked(whatsappPreview).mockRejectedValue(
      new ApiError("a refusal this client has never seen", {
        status: 409,
        code: "farmer_contact_blocked",
      }),
    );

    render(<WhatsAppSendPanel advice={ADVICE} />);

    expect(await screen.findByTestId("whatsapp-preview-error")).toHaveTextContent(
      "a refusal this client has never seen",
    );
  });
});

describe("a send that was refused", () => {
  it("says nothing was sent and nothing recorded", async () => {
    vi.mocked(sendWhatsApp).mockRejectedValue(
      new ApiError("server prose", {
        status: 409,
        code: "farmer_contact_not_configured",
      }),
    );

    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");
    await user.click(screen.getByTestId("whatsapp-send"));

    const error = await screen.findByTestId("whatsapp-send-error");
    expect(error).toHaveTextContent(/nothing was sent/i);
  });

  it("opens no WhatsApp window", async () => {
    vi.mocked(sendWhatsApp).mockRejectedValue(new ApiError("boom", { status: 500 }));

    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");
    await user.click(screen.getByTestId("whatsapp-send"));

    await screen.findByTestId("whatsapp-send-error");
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("shows no outcome message, because nothing was started", async () => {
    vi.mocked(sendWhatsApp).mockRejectedValue(new ApiError("boom", { status: 500 }));

    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");
    await user.click(screen.getByTestId("whatsapp-send"));

    await screen.findByTestId("whatsapp-send-error");
    expect(screen.queryByTestId("whatsapp-outcome-message")).not.toBeInTheDocument();
  });

  it("offers a retry for a fault that could be transient", async () => {
    vi.mocked(sendWhatsApp).mockRejectedValue(new ApiError("boom", { status: 503 }));

    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");
    await user.click(screen.getByTestId("whatsapp-send"));

    expect(await screen.findByTestId("whatsapp-retry")).toBeInTheDocument();
  });
});

describe("the history", () => {
  it("says plainly that nothing has been sent yet", async () => {
    render(<WhatsAppSendPanel advice={ADVICE} />);

    expect(await screen.findByTestId("whatsapp-history-empty")).toHaveTextContent(
      /no send has been started/i,
    );
  });

  it("lists sends already recorded for this recommendation", async () => {
    vi.mocked(whatsappSendsFor).mockResolvedValue({
      recommendation_id: "rec-1",
      found: true,
      deliveries: [delivery({ delivery_id: "del-earlier" })],
    });

    render(<WhatsAppSendPanel advice={ADVICE} />);

    const history = await screen.findByTestId("whatsapp-history");
    expect(history).toHaveTextContent("Sends started (1)");
    expect(history).toHaveTextContent("+919•••••••10");
  });

  it("states that no row records an arrival", async () => {
    vi.mocked(whatsappSendsFor).mockResolvedValue({
      recommendation_id: "rec-1",
      found: true,
      deliveries: [delivery()],
    });

    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-history");

    expect(document.body.textContent ?? "").toMatch(
      /none of them records that the message was delivered, read, answered or acted on/i,
    );
  });

  it("still offers the button when only the history read fails", async () => {
    // The history is a record of what happened before. Blocking a first send because
    // an old list could not be read would be the wrong trade.
    vi.mocked(whatsappSendsFor).mockRejectedValue(new ApiError("boom", { status: 500 }));

    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");

    expect(screen.getByTestId("whatsapp-send")).toBeEnabled();
  });
});

describe("what the panel does not do", () => {
  it("records no operator decision", async () => {
    // Accepting or rejecting the advice is the card below, filed by a person.
    // Telling a farmer what to do is not agreement with the advice.
    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");
    await user.click(screen.getByTestId("whatsapp-send"));
    await screen.findByTestId("whatsapp-outcome-message");

    expect(document.body.textContent ?? "").not.toMatch(
      /accept|reject|modify the recommendation/i,
    );
  });

  it("names no delivery date, because none is knowable", async () => {
    const user = userEvent.setup();
    render(<WhatsAppSendPanel advice={ADVICE} />);
    await screen.findByTestId("whatsapp-message");
    await user.click(screen.getByTestId("whatsapp-send"));
    await screen.findByTestId("whatsapp-outcome-message");

    expect(document.body.textContent ?? "").not.toMatch(/delivered at|read at|opened by the farmer/i);
  });
});
