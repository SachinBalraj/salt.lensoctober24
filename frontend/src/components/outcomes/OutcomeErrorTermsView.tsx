"use client";

import { Badge } from "@/components/ui/Primitives";
import {
  MISSING_DISPLAY,
  describeMissing,
  formatOutcomeNumber,
  formatOutcomeValue,
  formatPercentage,
  formatSignedError,
  type OutcomeErrorTerms,
} from "@/lib/outcomes";

/**
 * One predicted-against-actual comparison.
 *
 * This is where the module's central rule is made visible, so the three states
 * are rendered as three visibly different things:
 *
 * - both figures present: the numbers, with the error signed;
 * - the measurement missing: "Not comparable" in words, with the reason the
 *   backend attached, and the predicted figure still shown so the operator can
 *   see the model did make a call;
 * - the prediction missing: the reason the mass balance could not run.
 *
 * In no case is an absent figure rendered as `0`, because the one number this
 * project must never invent is a measurement.
 */
export default function OutcomeErrorTermsView({
  terms,
  title,
}: {
  terms: OutcomeErrorTerms;
  title: string;
}) {
  const predictedMissing = terms.predicted === null;
  const actualMissing = terms.actual === null;

  return (
    <div className="min-w-0 rounded-lg border border-line bg-inset/40 p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h3 className="text-[13px] font-semibold text-ink">{title}</h3>
        {terms.is_comparable ? (
          <Badge tone="active">Comparable</Badge>
        ) : (
          <Badge tone="caution">Not comparable</Badge>
        )}
      </div>
      <p className="mt-0.5 text-[11px] text-muted">
        Predicted from {terms.predicted_from_label}
      </p>

      {terms.is_comparable ? (
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
          <Term
            label="Predicted"
            value={formatOutcomeNumber(terms.predicted)}
            unit={terms.unit}
          />
          <Term label="Actual" value={formatOutcomeNumber(terms.actual)} unit={terms.unit} />
          <Term
            label="Signed error"
            value={formatSignedError(terms.signed_error)}
            unit={terms.unit}
            emphasis
          />
          <Term
            label="Percentage"
            value={
              terms.percentage_error_percent === null &&
              terms.percentage_error_reason
                ? "undefined"
                : formatPercentage(terms.percentage_error_percent)
            }
          />
        </dl>
      ) : (
        <div className="mt-3 space-y-2">
          {predictedMissing ? (
            <p className="text-[13px] text-muted">
              No prediction was computed for this event.
            </p>
          ) : (
            <p className="tabular text-[13px] text-ink-soft">
              The model predicted{" "}
              <span className="font-mono">
                {formatOutcomeValue(terms.predicted, terms.unit)}
              </span>
              . The measurement it should be compared against is{" "}
              <span className="font-medium text-caution">not recorded</span>.
            </p>
          )}
          <p className="text-[13px] leading-relaxed text-muted">
            {describeMissing(terms.missing_inputs)}
          </p>
          {actualMissing ? (
            <p className="text-[11px] leading-relaxed text-faint">
              This event is excluded from the mean absolute error. It is not
              counted as a zero error.
            </p>
          ) : null}
        </div>
      )}

      {terms.percentage_error_reason && terms.is_comparable ? (
        <p className="mt-3 text-[11px] leading-relaxed text-muted">
          {terms.percentage_error_reason}
        </p>
      ) : null}
    </div>
  );
}

function Term({
  label,
  value,
  unit,
  emphasis = false,
}: {
  label: string;
  value: string;
  unit?: string;
  emphasis?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-faint">
        {label}
      </dt>
      <dd
        className={`tabular mt-0.5 text-[15px] font-semibold ${
          value === MISSING_DISPLAY ? "text-faint" : emphasis ? "text-ink" : "text-ink-soft"
        }`}
      >
        {value}
        {value !== MISSING_DISPLAY && unit ? (
          <span className="ml-1 text-[11px] font-normal text-muted">{unit}</span>
        ) : null}
      </dd>
    </div>
  );
}
