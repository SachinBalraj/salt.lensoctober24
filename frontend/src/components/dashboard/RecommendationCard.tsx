"use client";

import { Button, Spinner } from "@/components/ui/Controls";
import { Alert, EmptyState } from "@/components/ui/Feedback";
import { Badge } from "@/components/ui/Primitives";
import { ProvenanceCard, ProvenanceTag } from "@/components/dashboard/Provenance";
import { CriteriaTable } from "@/components/decisions/CriteriaTable";
import { OperatorDecisionPanel } from "@/components/dashboard/DecisionPanel";
import { ApiError } from "@/lib/api";
import { adviceCanRetry, adviceUnavailableMessage } from "@/lib/decisionsApi";
import { ADVICE_REFUSAL_REMEDIES } from "@/lib/decisionsRefusals";
import { ML_UNAVAILABLE_LABEL, type DecisionAdvice } from "@/lib/decisions";
import type { FeedbackSubmission, OperatorFeedback } from "@/lib/operatorFeedback";
import {
  adviceIsModelInformed,
  describeAction,
  priorityLabel,
  priorityTone,
} from "@/lib/dashboard";
import { formatDateTime } from "@/lib/pans";

/**
 * The operational action, and the rule that chose it.
 *
 * **The action is the conclusion of everything above it, and this card is the only
 * place one appears.** Simulation, prediction and the twin are evidence; none of
 * them picks an action, and this card does not let one be read as though it had.
 * The rule is named and versioned at the foot of the card, because "HARVEST NOW"
 * without the rule that produced it is an instruction rather than a
 * recommendation, and the two are not the same thing to act on.
 *
 * **A recommendation with no model in it is labelled, in the project's required
 * words.** Not reworded, not softened, and not accompanied by a number that stands
 * in for a model's confidence. There is no confidence figure anywhere on this
 * card: a rule either decided or it did not, and a percentage here would be read
 * beside the model's probability and imply a likelihood nobody computed.
 *
 * **The glossary explains the action and does not grade it.** The line under the
 * action says what the operator is being asked to do. It carries no threshold and
 * no severity, because the rule already made that judgement and a second opinion
 * from the dashboard would be the screen quietly grading the rule.
 */
export function RecommendationCard({
  advice,
  error,
  loading,
  onRun,
  hasRun,
  decision,
  onRecord,
}: {
  advice: DecisionAdvice | null;
  error: ApiError | null;
  loading: boolean;
  onRun: () => void;
  hasRun: boolean;
  /** The recorded operator decision for this advice, when one exists. */
  decision: OperatorFeedback | null;
  onRecord: (submission: FeedbackSubmission) => Promise<void>;
}) {
  return (
    <>
      <ProvenanceCard
        kind="recommended"
        title="Recommendation"
        description="The action a versioned written rule selected. The rule decided; neither the model nor the simulation chose it."
        actions={
          <Button type="button" onClick={onRun} disabled={loading} data-testid="decision-run">
            {loading ? "Working…" : hasRun ? "Re-run analysis" : "Run analysis"}
          </Button>
        }
      >
        {loading ? (
          <div className="flex items-center gap-2 py-2 text-[13px] text-muted">
            <Spinner /> Running the mass balance and applying the rule…
          </div>
        ) : error ? (
          <Refused error={error} onRetry={onRun} />
        ) : !advice ? (
          <EmptyState
            title="No analysis run for this pan"
            description="A recommendation is made on request from the recorded state, the mass balance and a written rule, and is stored so it can be checked against what happened later. Opening this page does not create one."
          />
        ) : (
          <Action advice={advice} />
        )}
      </ProvenanceCard>

      {advice ? (
        <OperatorDecisionPanel
          advice={advice}
          decision={decision}
          onRecord={onRecord}
        />
      ) : null}
    </>
  );
}

/**
 * The refused case.
 *
 * Every refusal the engine can issue is a state of the system rather than a mistake
 * in the request — a pan with no twin, a twin with no salinity, a rule nobody has
 * written. Asking again cannot change any of them, so the card names what has to
 * exist and offers no retry button, which would only invite an operator to keep
 * pressing it. The server's own explanation is kept in the title attribute where a
 * developer can read it without an operator being shown a refusal as an error.
 */
function Refused({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const remedy =
    error.code && error.code in ADVICE_REFUSAL_REMEDIES
      ? ADVICE_REFUSAL_REMEDIES[error.code as keyof typeof ADVICE_REFUSAL_REMEDIES]
      : null;

  return (
    <Alert
      tone="warning"
      title="No recommendation"
      action={
        !adviceCanRetry(error) ? undefined : (
          <Button type="button" onClick={onRetry} variant="secondary">
            Try again
          </Button>
        )
      }
    >
      <span title={error.message}>{adviceUnavailableMessage(error)}</span>
      {remedy ? <p className="mt-1 text-[11px] text-muted">{remedy}</p> : null}
    </Alert>
  );
}

function Action({ advice }: { advice: DecisionAdvice }) {
  const modelInformed = adviceIsModelInformed(advice);
  const labelled = advice.notice.startsWith(ML_UNAVAILABLE_LABEL);

  return (
    <div className="space-y-4" data-testid="recommendation-action">
      <div
        className="rounded-lg border px-4 py-4"
        style={{
          borderColor: "var(--color-prov-recommended-line)",
          backgroundColor: "var(--color-prov-recommended-soft)",
        }}
      >
        <div className="flex flex-wrap items-start justify-between gap-2">
          <p className="text-2xl font-semibold tracking-tight text-ink">
            {advice.recommendation}
          </p>
          <Badge tone={priorityTone(advice.priority)}>
            {priorityLabel(advice.priority)}
          </Badge>
        </div>
        <p className="mt-1.5 text-[12px] leading-relaxed text-ink-soft">
          {describeAction(advice.recommendation)}
        </p>
      </div>

      {modelInformed ? (
        <p className="text-[11px] leading-relaxed text-muted" data-testid="recommendation-notice">
          {advice.notice}
        </p>
      ) : (
        <Alert tone="info" title="No model estimate was read">
          <p className="text-[11px] leading-relaxed" data-testid="recommendation-notice">
            {advice.notice}
          </p>
          {labelled ? null : (
            <p className="mt-1 text-[11px]">
              This response did not carry the project&apos;s required label, which is
              &ldquo;{ML_UNAVAILABLE_LABEL}&rdquo;
            </p>
          )}
        </Alert>
      )}

      <div>
        <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-faint">
          Why
        </p>
        <p className="mt-1 text-[12px] leading-relaxed text-ink-soft">
          {advice.reason}
        </p>
        {advice.matched_criteria ? (
          <ul className="mt-1.5 space-y-0.5">
            {advice.matched_criteria.split("; ").map((line) => (
              <li key={line} className="font-mono text-[11px] text-muted">
                {line}
              </li>
            ))}
          </ul>
        ) : null}
        {/* The prose above names the rule; this shows the figures it decided on.
            The dashboard is where an operator reads the action, so the resolved
            thresholds and the field each was read from belong here as well as on
            the pan page — a recommendation whose numbers can only be found on one
            of the two pages cannot be checked from the other. */}
        {advice.criteria.length > 0 ? (
          <CriteriaTable criteria={advice.criteria} />
        ) : null}
      </div>

      <div className="border-t border-line pt-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-faint">
          What it was decided from
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <ProvenanceTag kind="observed" />
          <ProvenanceTag kind="simulated" />
          {modelInformed ? <ProvenanceTag kind="ml" /> : <ProvenanceTag kind="forecast" />}
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-muted">
          {adviceIsModelInformed(advice)
            ? "The rule read the recorded state, the mass balance and the stored model estimate."
            : "The rule read the recorded state and the mass balance. No model estimate was read, so none is shown as though it had been."}
        </p>
      </div>

      <div className="grid gap-x-6 gap-y-2 border-t border-line pt-3 sm:grid-cols-2">
        <Row label="Rule" value={`${advice.rule_name} v${advice.rule_version_number}`} />
        <Row label="Issued" value={formatDateTime(advice.timestamp)} />
        <Row label="Twin state as of" value={formatDateTime(advice.twin.as_of)} />
        <Row
          label="Rule content"
          value={advice.specification_hash.replace(/^sha256:/, "").slice(0, 12)}
          mono
        />
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
