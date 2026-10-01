"use client";

import Link from "next/link";
import { useState } from "react";
import OutcomeErrorTermsView from "@/components/outcomes/OutcomeErrorTermsView";
import { Badge } from "@/components/ui/Primitives";
import {
  HARVEST_LABELS,
  LINK_KINDS,
  LINK_LABELS,
  formatEventTimestamp,
  formatOutcomeValue,
  formatSignedError,
  readLink,
  type EventOutcome,
} from "@/lib/outcomes";

/**
 * The recorded events for a pan, newest first, each expandable.
 *
 * Rows are collapsed to the one line a reader scans for: when it happened, what
 * the pan was measured at, and how far off the model was. The full three-way
 * comparison and the four links are one click away, because they are what you
 * need when a number looks wrong and not what you need when you are skimming.
 */
export default function OutcomeHistory({
  outcomes,
  hrefFor,
}: {
  outcomes: readonly EventOutcome[];
  hrefFor?: (outcome: EventOutcome) => string;
}) {
  if (outcomes.length === 0) {
    return (
      <p className="px-5 py-6 text-[13px] text-muted">No outcomes recorded.</p>
    );
  }

  return (
    <div className="divide-y divide-line">
      {outcomes.map((outcome) => (
        <OutcomeRow key={outcome.outcome_id} outcome={outcome} hrefFor={hrefFor} />
      ))}
    </div>
  );
}

function OutcomeRow({
  outcome,
  hrefFor,
}: {
  outcome: EventOutcome;
  hrefFor?: (outcome: EventOutcome) => string;
}) {
  const [open, setOpen] = useState(false);
  const panelId = `outcome-${outcome.outcome_id}`;
  const terms = outcome.simulation_error;
  const href = hrefFor?.(outcome);

  return (
    <div className="px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[13px] font-medium text-ink">
              {outcome.event_id}
            </span>
            {outcome.is_comparable ? (
              <Badge tone="active">Comparable</Badge>
            ) : (
              <Badge tone="caution">Not comparable</Badge>
            )}
            {outcome.harvest_outcome ? (
              <Badge tone="neutral">
                {HARVEST_LABELS[outcome.harvest_outcome]}
              </Badge>
            ) : null}
          </div>
          <p className="mt-1 text-[11px] text-muted">
            {formatEventTimestamp(outcome.timestamp)}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <Compact
            label="Actual salinity"
            value={formatOutcomeValue(outcome.post_event_salinity_g_l, "g/L")}
            missing={outcome.post_event_salinity_g_l === null}
          />
          <Compact
            label="Signed error"
            value={formatSignedError(terms.signed_error, { unit: "g/L" })}
            missing={!terms.is_comparable}
          />
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-controls={panelId}
          className="text-xs font-medium text-ink-soft hover:text-accent hover:underline"
        >
          {open ? "Hide comparison and links" : "Show comparison and links"}
          <span className="sr-only"> for event {outcome.event_id}</span>
        </button>
        {href ? (
          <Link
            href={href}
            className="text-xs font-medium text-ink-soft hover:text-accent hover:underline"
          >
            Open pan
          </Link>
        ) : null}
      </div>

      {open ? (
        <div id={panelId} className="mt-4 space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <OutcomeErrorTermsView
              terms={outcome.simulation_error}
              title="Simulation, from the rainfall that fell"
            />
            <OutcomeErrorTermsView
              terms={outcome.forecast_simulation_error}
              title="Forecast, from the rainfall that was predicted"
            />
          </div>
          <OutcomeErrorTermsView
            terms={outcome.rainfall_forecast_error}
            title="The forecast rainfall, against the rain that fell"
          />

          <div>
            <h3 className="text-[13px] font-semibold text-ink">
              What the event was recorded against
            </h3>
            <dl className="mt-2 space-y-2">
              {LINK_KINDS.map((slot) => {
                const link = readLink(outcome.links, slot);
                if (!link) return null;
                return (
                  <div key={slot} className="flex flex-wrap items-baseline gap-x-2">
                    <dt className="text-[11px] font-medium uppercase tracking-wide text-faint">
                      {LINK_LABELS[slot]}
                    </dt>
                    <dd className="min-w-0 flex-1 text-[13px] text-ink-soft">
                      {link.summary}
                      {link.record_id ? (
                        <span className="ml-1.5 font-mono text-[11px] text-muted">
                          {link.record_id}
                        </span>
                      ) : null}
                      {link.salinity_g_l !== null ? (
                        <span className="ml-1.5 text-[11px] text-muted">
                          salinity {formatOutcomeValue(link.salinity_g_l, "g/L")}
                        </span>
                      ) : null}
                      {link.rainfall_mm !== null ? (
                        <span className="ml-1.5 text-[11px] text-muted">
                          rainfall {formatOutcomeValue(link.rainfall_mm, "mm")}
                        </span>
                      ) : null}
                    </dd>
                  </div>
                );
              })}
            </dl>
          </div>

          {outcome.operator_action ? (
            <div>
              <h3 className="text-[13px] font-semibold text-ink">
                What the operator did
              </h3>
              <p className="mt-1 text-[13px] leading-relaxed text-ink-soft">
                {outcome.operator_action}
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Compact({
  label,
  value,
  missing,
}: {
  label: string;
  value: string;
  missing: boolean;
}) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium uppercase tracking-wide text-faint">
        {label}
      </p>
      <p
        className={`tabular mt-0.5 text-[13px] ${
          missing ? "text-faint" : "text-ink"
        }`}
      >
        {value}
        {missing ? (
          <span className="sr-only"> — not recorded, so there is no error to show</span>
        ) : null}
      </p>
    </div>
  );
}
