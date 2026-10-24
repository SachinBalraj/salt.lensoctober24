"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Button, Spinner } from "@/components/ui/Controls";
import { Alert } from "@/components/ui/Feedback";
import { ProvenanceCard } from "@/components/dashboard/Provenance";
import { ApiError } from "@/lib/api";
import type { DecisionAdvice } from "@/lib/decisions";
import {
  sendWhatsApp,
  whatsappCanRetry,
  whatsappPreview,
  whatsappSendsFor,
  whatsappUnavailableMessage,
} from "@/lib/whatsappApi";
import {
  SEND_BUTTON_LABELS,
  describeDelivery,
  deliveryClaimsOnlyInitiation,
  refusalIsConfigurable,
  sendButtonState,
  sendOutcomeMessage,
  type WhatsAppDelivery,
  type WhatsAppPreview,
  type WhatsAppRefusal,
  type WhatsAppSendResult,
} from "@/lib/whatsapp";

/**
 * Send the stored recommendation to the farmer on WhatsApp.
 *
 * ## What it does
 *
 * It builds the message from the stored recommendation — server-side, from the same
 * document the rule wrote — shows the operator exactly what a farmer will receive
 * and which number it is going to, and on an explicit press opens WhatsApp with that
 * text pre-filled.
 *
 * ## What it is not
 *
 * **It does not send anything.** The panel navigates the browser to WhatsApp's own
 * `wa.me` link. WhatsApp opens on the operator's device, the operator presses send
 * there, and this application is not on the conversation afterwards. There is no
 * Business API account, token or webhook behind it and nothing to configure, which
 * is also why the panel can never show that a message was delivered: the information
 * does not exist here to show.
 *
 * **It does not record a decision.** Accepting, modifying or rejecting the advice is
 * the card below, filed by a person and stored separately. A farmer being told what
 * to do is not the operator agreeing with it, and the two records are kept apart for
 * that reason.
 *
 * **It does not record an outcome.** Nothing here is a measurement, an
 * intervention or a validation. The record written is a communication event, and it
 * carries `delivery_status: not_confirmed` with nothing able to change it.
 *
 * ## Why the message is previewed first
 *
 * The farmer is a person who will act on this text. The panel therefore reads the
 * message before offering the button, so what an operator approves is what gets
 * sent — the backend renders both from one code path, so they cannot differ. The
 * number is shown masked: the panel has no need of the full one, and a screen is a
 * poor place to keep it.
 *
 * ## Why the button is explicit
 *
 * Nothing is sent on mount, on opening the card, or on re-render. A press opens a
 * chat window with a message in it that a person must still send, and a second press
 * while the first is in flight is blocked so one intention cannot be recorded twice.
 */

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(fallback, { status: -1 });
}

/**
 * The sends already recorded for one recommendation.
 *
 * A read that fails is not fatal to the panel. The button's availability comes from
 * the preview, and the history is a record of what happened before — an operator
 * about to send for the first time needs the button more than they need an old
 * list. So a failed read leaves the list empty rather than blocking the panel, and
 * the empty list says "no send has been started", which for a first send is the
 * truth even though it is not what the server answered.
 */
async function readSends(recommendationId: string): Promise<WhatsAppDelivery[]> {
  try {
    const response = await whatsappSendsFor(recommendationId);
    return response.deliveries;
  } catch {
    return [];
  }
}

/** The refusal code of an error, when it is one this client models. */
function refusalOf(error: ApiError | null): WhatsAppRefusal | null {
  return error === null ? null : (error.code as WhatsAppRefusal | null);
}

export function WhatsAppSendPanel({
  advice,
  operatorId,
  onSent,
}: {
  advice: DecisionAdvice;
  /** Carried through from the decision panel's operator field when there is one. */
  operatorId?: string;
  /** Called with the record so a parent can show the history without a second read. */
  onSent?: (delivery: WhatsAppDelivery) => void;
}) {
  const [preview, setPreview] = useState<WhatsAppPreview | null>(null);
  const [result, setResult] = useState<WhatsAppSendResult | null>(null);
  const [sends, setSends] = useState<WhatsAppDelivery[]>([]);
  const [loading, setLoading] = useState(true);
  /** Bumped to re-run the read. A retry in the panel, not a full page reload. */
  const [attempt, setAttempt] = useState(0);
  const [sending, setSending] = useState(false);
  const [previewError, setPreviewError] = useState<ApiError | null>(null);
  const [sendError, setSendError] = useState<ApiError | null>(null);

  const recommendationId = advice.recommendation_id;

  useEffect(() => {
    // The panel is keyed on the recommendation by its parent, so this effect runs
    // once per recommendation with every piece of state already fresh. Resetting
    // inside the effect instead would mean showing one farmer's masked number beside
    // another pan's advice for a frame — a privacy mistake rather than a stale-state
    // annoyance — and would cost a cascading render on every change.
    let current = true;

    void Promise.all([whatsappPreview(recommendationId), readSends(recommendationId)])
      .then(([next, history]) => {
        if (!current) return;
        setPreview(next);
        setSends(history);
      })
      .catch((caught: unknown) => {
        if (!current) return;
        setPreviewError(asApiError(caught, "The message could not be prepared."));
      })
      .finally(() => {
        if (current) setLoading(false);
      });

    return () => {
      current = false;
    };
  }, [recommendationId, attempt]);

  const onRetry = useCallback(() => {
    // Only offered for a fault worth retrying — a missing contact stays missing
    // until somebody enters it, and the panel does not offer that button at all.
    setPreviewError(null);
    setLoading(true);
    setAttempt((current) => current + 1);
  }, []);

  const handleSend = useCallback(async () => {
    if (sending) return;
    setSending(true);
    setSendError(null);
    try {
      const sent = await sendWhatsApp(
        recommendationId,
        operatorId ? { operatorId } : {},
      );
      setResult(sent);
      setSends((current) => [sent.delivery, ...current]);
      onSent?.(sent.delivery);

      // The navigation happens after the record exists, so a send that was
      // recorded is always a send whose WhatsApp window is opened. If the browser
      // blocks it — a popup blocker, a locked-down kiosk — the record still stands
      // and the message text is shown below so the operator can send it by hand.
      window.open(sent.click_to_chat_url, "_blank", "noopener,noreferrer");
    } catch (caught) {
      setSendError(asApiError(caught, "The send could not be started."));
    } finally {
      setSending(false);
    }
  }, [onSent, operatorId, recommendationId, sending]);

  const button = sendButtonState(preview, loading, sending, refusalOf(previewError));

  return (
    <ProvenanceCard
      kind="operator_decision"
      title="Send to farmer via WhatsApp"
      description="The recommendation above, as a message for the farmer. WhatsApp opens on your own device with the text pre-filled, and you press send there. Nothing is transmitted from this application, and nothing here records that the message arrived."
      actions={
        <Button
          type="button"
          variant="primary"
          onClick={() => void handleSend()}
          disabled={button.kind !== "ready"}
          title={button.kind === "unavailable" ? button.reason : undefined}
          data-testid="whatsapp-send"
        >
          {button.kind === "sending" ? (
            <Spinner label={SEND_BUTTON_LABELS.sending} />
          ) : (
            SEND_BUTTON_LABELS[button.kind]
          )}
        </Button>
      }
    >
      <div className="space-y-4">
        <p className="text-[11px] leading-relaxed text-muted">
          The message is written by the server from the stored recommendation — the
          action, the simulated salinity, the configured threshold and the rule that
          chose it. Nothing on this panel can change it, and the farmer&apos;s number
          is read from the pan rather than chosen here.
        </p>

        {loading ? (
          <p
            className="flex items-center gap-2 text-[12px] text-muted"
            data-testid="whatsapp-loading"
          >
            <Spinner /> Preparing the message…
          </p>
        ) : previewError ? (
          <Alert
            tone="warning"
            title="No message to send"
            action={
              refusalOf(previewError) !== null &&
              refusalIsConfigurable(refusalOf(previewError) as WhatsAppRefusal) ? (
                // A Link rather than a router call so it is a real anchor: it can be
                // opened in a new tab, and it works as ordinary navigation, which a
                // programmatic push in an onClick handler does not.
                <Link
                  href={`/pans/${encodeURIComponent(advice.pan_id)}`}
                  className="inline-flex items-center rounded-md border border-line px-3 py-1.5 text-[13px] font-medium text-ink hover:bg-inset"
                  data-testid="whatsapp-open-pan"
                >
                  Open the pan
                </Link>
              ) : whatsappCanRetry(previewError) ? (
                <Button type="button" variant="secondary" onClick={onRetry}>
                  Try again
                </Button>
              ) : undefined
            }
          >
            <span title={previewError.message} data-testid="whatsapp-preview-error">
              {whatsappUnavailableMessage(previewError)}
            </span>
          </Alert>
        ) : preview ? (
          <>
            <Recipient preview={preview} />
            <MessageText message={preview.message} />
          </>
        ) : null}

        {sendError ? (
          <Alert
            tone="danger"
            title="The send was not started"
            action={
              whatsappCanRetry(sendError) ? (
                <Button
                  type="button"
                  variant="secondary"
                  onClick={() => void handleSend()}
                  disabled={sending}
                  data-testid="whatsapp-retry"
                >
                  Try again
                </Button>
              ) : undefined
            }
          >
            <span title={sendError.message} data-testid="whatsapp-send-error">
              {whatsappUnavailableMessage(sendError)}
            </span>
            <p className="mt-1 text-[11px]">
              Nothing has been recorded. A refused send leaves no trace, so the
              history below is unchanged.
            </p>
          </Alert>
        ) : null}

        {result ? (
          <Alert tone="info" title="WhatsApp is opening">
            <span data-testid="whatsapp-outcome-message">
              {sendOutcomeMessage(result, false)}
            </span>
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px]">
                The message text, to send by hand if WhatsApp did not open
              </summary>
              <pre
                className="mt-1.5 overflow-x-auto whitespace-pre-wrap rounded border border-line bg-surface-soft p-2 font-mono text-[11px] text-ink-soft"
                data-testid="whatsapp-message-copy"
              >
                {result.message}
              </pre>
            </details>
          </Alert>
        ) : null}

        <History sends={sends} />
      </div>
    </ProvenanceCard>
  );
}

/**
 * Who the message is for, masked.
 *
 * Shown before the press rather than after, because the question "who am I about to
 * message?" is answerable here and only here — the full number is never sent to the
 * browser, so this mask is the same one the audit record carries.
 */
function Recipient({ preview }: { preview: WhatsAppPreview }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[12px]">
      <span className="font-medium text-ink">
        To {preview.farmer_name ?? "the farmer"}
      </span>
      <span className="font-mono text-muted" data-testid="whatsapp-recipient">
        {preview.recipient_contact_reference ?? "no number recorded"}
      </span>
    </div>
  );
}

/**
 * The message, in full, exactly as it will be pre-filled.
 *
 * Shown in a fixed-width block with whitespace preserved because the message has
 * line breaks and an operator comparing it against what WhatsApp opens needs them to
 * survive.
 */
function MessageText({ message }: { message: string }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-faint">
        The message
      </p>
      <pre
        className="mt-1.5 overflow-x-auto whitespace-pre-wrap rounded border border-line bg-surface-soft p-3 font-mono text-[11px] leading-relaxed text-ink-soft"
        data-testid="whatsapp-message"
      >
        {message}
      </pre>
    </div>
  );
}

/**
 * Every send recorded for this recommendation.
 *
 * More than one row is normal: re-sending is legitimate, and the point of showing
 * the list is that repeated presses stay visible as repeated presses. Every row
 * says a send was initiated; none of them says anything arrived.
 */
function History({ sends }: { sends: WhatsAppDelivery[] }) {
  if (sends.length === 0) {
    return (
      <p className="border-t border-line pt-3 text-[11px] text-muted" data-testid="whatsapp-history-empty">
        No send has been started for this recommendation.
      </p>
    );
  }

  return (
    <div className="border-t border-line pt-3" data-testid="whatsapp-history">
      <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-faint">
        Sends started ({sends.length})
      </p>
      <ul className="mt-1.5 space-y-1">
        {sends.map((send) => (
          <li key={send.delivery_id} className="text-[11px] text-muted">
            {describeDelivery(send)} · to {send.farmer_contact_reference}
            {deliveryClaimsOnlyInitiation(send) ? null : (
              <span className="text-caution"> · see record</span>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-1.5 text-[11px] leading-relaxed text-muted">
        Each row records that a send was started. None of them records that the
        message was delivered, read, answered or acted on.
      </p>
    </div>
  );
}
