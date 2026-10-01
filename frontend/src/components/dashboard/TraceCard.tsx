"use client";

import { ProvenanceTag } from "@/components/dashboard/Provenance";
import { trace } from "@/lib/operatorFeedback";
import type { DecisionAdvice } from "@/lib/decisions";
import type { OperatorFeedback } from "@/lib/operatorFeedback";
import type { EventOutcome } from "@/lib/outcomes";
import { formatDateTime } from "@/lib/pans";

/**
 * The whole chain, teased apart into its eight records.
 *
 * Every card above holds one category of number and says so. This card is the one
 * place the categories are shown together — but standing next to each other, not
 * mixed: each step keeps its own tag, its own record id and its own instant, so a
 * reader sees eight shows of provenance instead of one claim that swallowed seven
 * of them.
 *
 * **A step that has no record keeps its place and says why.** A missing model
 * estimate, an unattributed estimate or a pan nobody has fished for yet are
 * ordinary states, not errors, and each has its own sentence. Dropping a step
 * would let the chain look complete when the research record is not.
 *
 * The chain is built by {@link trace}, which is kept in the library so a test can
 * assert on every step without rendering the screen.
 */
export function TraceCard({
  advice,
  decision,
  outcome,
}: {
  advice: DecisionAdvice;
  decision: OperatorFeedback | null;
  outcome: EventOutcome | null;
}) {
  const steps = trace(advice, decision, outcome);

  return (
    <section
      data-testid="trace-card"
      aria-label="One event chain, record by record"
      className="overflow-hidden rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)]"
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">One chain, eight records</h2>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            How this event is reconstructed for research. Every stage keeps its own
            provenance tag, id and instant; a stage with no record yet says so in its
            own place.
          </p>
        </div>
      </div>

      <ol className="space-y-3 px-5 py-4">
        {steps.map((step) => (
          <li
            key={step.key}
            data-testid={`trace-${step.key}`}
            className="flex flex-wrap items-baseline gap-x-3 gap-y-1"
          >
            <ProvenanceTag kind={step.provenance} />
            <span className="text-[13px] font-medium text-ink">{step.label}</span>
            {step.id ? (
              <code className="rounded bg-inset px-1.5 py-0.5 font-mono text-[11px] text-muted">
                {step.id}
              </code>
            ) : (
              <span className="text-[11px] italic text-faint">no record yet</span>
            )}
            {step.timestamp ? (
              <span className="ml-auto text-[11px] tabular text-faint">
                {formatDateTime(step.timestamp)}
              </span>
            ) : null}
            <span className="w-full text-[11px] leading-relaxed text-muted">
              {step.detail}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}