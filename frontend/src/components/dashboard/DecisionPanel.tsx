"use client";

import { useState } from "react";
import { Button, Spinner } from "@/components/ui/Controls";
import { Alert } from "@/components/ui/Feedback";
import { ProvenanceCard, ProvenanceTag } from "@/components/dashboard/Provenance";
import {
  SelectField,
  TextAreaField,
  TextField,
} from "@/components/ui/Fields";
import { ApiError } from "@/lib/api";
import { describeAction } from "@/lib/dashboard";
import { DECISION_ACTIONS, type DecisionAdvice } from "@/lib/decisions";
import {
  decisionFollowedRecommendation,
  describeDecision,
  describeRecordedAction,
  type FeedbackSubmission,
  type OperatorDecision,
  type OperatorFeedback,
} from "@/lib/operatorFeedback";
import { feedbackCanRetry, feedbackUnavailableMessage } from "@/lib/operatorFeedbackApi";
import { formatDateTime } from "@/lib/pans";

/**
 * The operator's recorded decision about one recommendation.
 *
 * ## What it is
 *
 * A decision is the answer to the advice: accept it, modify it, or reject it, and
 * say — in the closed vocabulary — what was actually done. Module 14 stores it so
 * that later research can check the advice against what followed, so the record is
 * made here, read-only afterwards, and every loading state is honest.
 *
 * ## What it is not
 *
 * A decision is not a measurement. It carries no figure, no probability and no
 * severity, and it never appears on the cards above that hold numbers. It shares a
 * provenance category (`operator_decision`) with the taxonomy so the trace keeps
 * the step, but the card says plainly that what was recorded is what a person did,
 * not what is true about the pan.
 *
 * ## Disposition and action are separate facts
 *
 * Accepting records how the advice was received. What was actually done is a
 * second field: leave it blank and the record says no physical action followed;
 * name an action and the record says you took it. Nothing is filed on the
 * operator's behalf, because writing the recommended action into a record where
 * nothing was done would claim an intervention nobody performed.
 *
 * ## Writes nothing on mount
 *
 * The form is an act, like every act on this dashboard. The read of an
 * already-recorded decision happens in the parent; opening this panel files
 * nothing.
 */

/** The choice an operator makes, in imperative form for the form's options. */
const DISPOSITION_OPTIONS: Array<{ value: OperatorDecision; label: string }> = [
  { value: "accepted", label: "Accept" },
  { value: "modified", label: "Modify" },
  { value: "rejected", label: "Reject" },
];

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(fallback, { status: -1 });
}

export function OperatorDecisionPanel({
  advice,
  decision,
  onRecord,
}: {
  advice: DecisionAdvice;
  decision: OperatorFeedback | null;
  onRecord: (submission: FeedbackSubmission) => Promise<void>;
}) {
  return (
    <ProvenanceCard
      kind="operator_decision"
      title="Operator decision"
      description="What the operator did with the recommendation, recorded. A decision is not a figure, and it is filed by a person — it stays in its own card for that reason."
    >
      {decision ? (
        <Recorded decision={decision} advice={advice} />
      ) : (
        <Form advice={advice} onRecord={onRecord} />
      )}
    </ProvenanceCard>
  );
}

function Form({
  advice,
  onRecord,
}: {
  advice: DecisionAdvice;
  onRecord: (submission: FeedbackSubmission) => Promise<void>;
}) {
  const [disposition, setDisposition] = useState<OperatorDecision>("accepted");
  const [actualAction, setActualAction] = useState<string>("");
  const [operatorId, setOperatorId] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ApiError | null>(null);

  const requiresAction = disposition === "modified" || disposition === "rejected";
  const canSubmit = !requiresAction || actualAction !== "";

  async function handleSubmit() {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await onRecord({
        recommendationId: advice.recommendation_id,
        decision: disposition,
        actualAction: actualAction === "" ? undefined : (actualAction as FeedbackSubmission["actualAction"]),
        operatorId: operatorId.trim() || undefined,
        notes: notes.trim() || undefined,
      });
    } catch (caught) {
      setSubmitError(asApiError(caught, "The decision could not be recorded."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-4" data-testid="decision-form">
      <p className="text-[11px] leading-relaxed text-muted">
        The recommendation above stands until a person files a decision about it.
        Record this one’s disposition and the action actually taken. The pan and the
        recommended action are read from the stored advice, so this record can never
        be filed for the wrong pan.
      </p>

      <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        <SelectField
          name="decision-disposition"
          label="Disposition"
          value={disposition}
          onChange={(value) => setDisposition(value as OperatorDecision)}
          options={DISPOSITION_OPTIONS}
          hint={
            disposition === "accepted"
              ? "Accepting records the disposition. Leave the action blank to record that no physical action followed, or name what was actually done."
              : "Modified or rejected records need the action actually taken."
          }
        />
        <SelectField
          name="decision-action"
          label="Action actually taken"
          value={actualAction}
          onChange={setActualAction}
          options={[
            { value: "", label: "Choose the action actually taken", disabled: true },
            ...DECISION_ACTIONS.map((action) => ({
              value: action,
              label: `${action} — ${describeAction(action)}`,
            })),
          ]}
          error={
            requiresAction && actualAction === "" && submitError === null
              ? "A modified or rejected decision must name the action taken."
              : undefined
          }
          hint={
            disposition === "accepted" && actualAction === ""
              ? "Leave this on “Choose the action actually taken” to record that the acceptance was followed by no physical action."
              : "The four closed actions only — the same vocabulary the rule recommends in."
          }
        />
      </div>

      <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
        <TextField
          name="decision-operator"
          label="Operator"
          value={operatorId}
          onChange={setOperatorId}
          placeholder="e.g. M. Ndlovu"
          autoComplete="off"
        />
        <TextAreaField
          name="decision-notes"
          label="Note"
          value={notes}
          onChange={setNotes}
          placeholder="Why — for the record. Optional."
          rows={2}
        />
      </div>

      {submitError ? (
        <Alert
          tone="danger"
          title="The decision could not be recorded"
          action={
            feedbackCanRetry(submitError) ? (
              <Button type="button" variant="secondary" onClick={handleSubmit} disabled={submitting}>
                Try again
              </Button>
            ) : undefined
          }
        >
          <span data-testid="decision-submit-error" title={submitError.message}>
            {feedbackUnavailableMessage(submitError)}
          </span>
        </Alert>
      ) : null}

      <div>
        <Button
          type="button"
          variant="primary"
          onClick={handleSubmit}
          disabled={!canSubmit || submitting}
          data-testid="decision-submit"
        >
          {submitting ? <Spinner label="Recording…" /> : "Record decision"}
        </Button>
        {!canSubmit ? (
          <p className="mt-1.5 text-[11px] text-caution" data-testid="decision-action-required">
            Choose the action actually taken first — a modified or rejected decision
            must record what was done.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function Recorded({
  decision,
  advice,
}: {
  decision: OperatorFeedback;
  advice: DecisionAdvice;
}) {
  const followed = decisionFollowedRecommendation(decision);
  return (
    <div className="space-y-4" data-testid="decision-recorded">
      <div
        className="rounded-lg border px-4 py-4"
        style={{
          borderColor: "var(--color-prov-decision-line)",
          backgroundColor: "var(--color-prov-decision-soft)",
        }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-2xl font-semibold tracking-tight text-ink">
            {describeDecision(decision.decision)}
          </p>
          <ProvenanceTag kind="operator_decision" />
        </div>
        <p className="mt-1.5 text-[12px] leading-relaxed text-ink-soft">
          {describeRecordedAction(decision)}
        </p>
        {followed ? (
          <p className="mt-1 text-[11px] text-muted">
            The operator recorded the recommended action itself, so what was done is
            on the record as done.
          </p>
        ) : null}
      </div>

      {decision.notes ? (
        <p className="rounded-lg border border-line bg-inset px-3.5 py-2.5 text-[12px] leading-relaxed text-ink-soft">
          {decision.notes}
        </p>
      ) : null}

      {decision.is_demonstration === null ? (
        <Alert tone="warning" title="Provenance not recorded for this advice">
          <p className="text-[12px] leading-relaxed">
            This decision does not say whether the recommendation it answers was
            built from field records or worked examples, and the recommendation is
            no longer available to check. The decision and the action recorded are
            unaffected — but do not read it as a field observation, because nothing
            here establishes that it is one.
          </p>
        </Alert>
      ) : null}

      {decision.is_demonstration ? (
        <Alert tone="demo" title="A decision on demonstration advice">
          <p className="text-[12px] leading-relaxed">
            The recommendation this answers was built from demonstration data
            {decision.demonstration_sources.length > 0
              ? ` (${decision.demonstration_sources.join(", ")})`
              : ""}
            . The decision is a real record of what the operator chose to do, but it
            is not a reading of this pan and must not be read as evidence about the
            rule from field results.
          </p>
        </Alert>
      ) : null}

      <div className="grid gap-x-6 gap-y-2 border-t border-line pt-3 sm:grid-cols-2">
        <Row label="Decision id" value={decision.decision_id} mono />
        <Row label="Decided" value={formatDateTime(decision.timestamp)} />
        <Row label="Recommendation id" value={advice.recommendation_id} mono />
        {/* The rule that produced the advice, read from the decision record itself
            rather than the live recommendation, so it still names the rule version
            even when that recommendation has been superseded and removed. */}
        <Row
          label="Answered"
          value={
            decision.rule_name && decision.rule_version
              ? `${decision.rule_name} v${decision.rule_version}`
              : (decision.recommendation?.rule_name ?? "—")
          }
        />
        <Row label="Operator" value={decision.operator_id ?? "—"} />
        <div className="sm:col-span-2">
          <p className="text-[11px] leading-relaxed text-muted">
            One decision per recommendation is enforced, and the record carries the
            identifiers of the evidence it answers — the full chain is rebuilt in the
            trace below.
          </p>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-[10px] font-medium uppercase tracking-[0.1em] text-faint">
        {label}
      </dt>
      <dd
        className={`truncate text-[11px] text-ink-soft ${mono ? "font-mono" : "tabular"}`}
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}