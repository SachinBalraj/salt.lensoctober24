"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import OutcomeHistory from "@/components/outcomes/OutcomeHistory";
import { Button } from "@/components/ui/Controls";
import { Alert, EmptyState, Skeleton } from "@/components/ui/Feedback";
import { Card, CardHeader, StatTile } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import {
  describeCoverage,
  formatMeanAbsoluteError,
  formatOutcomeNumber,
  formatSignedError,
  MISSING_DISPLAY,
  type EventOutcome,
  type OutcomeComparisonSummary,
} from "@/lib/outcomes";
import { getOutcomeComparison, listOutcomes } from "@/lib/outcomesApi";

const PAGE_LIMIT = 100;

interface LoadedState {
  key: string;
  summary: OutcomeComparisonSummary | null;
  outcomes: EventOutcome[];
  total: number;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError(fallback, { status: -1 });
}

/**
 * The three error families the module reports, as a row of tiles.
 *
 * Each tile carries its own coverage sentence. That is not decoration: a mean
 * absolute error over two of twenty events is the single easiest number in this
 * project to quote out of context, and the only defence is that the number
 * never appears without the count it was drawn from.
 *
 * The family name goes into the tile's own label rather than relying on the card
 * heading above it, because three tiles that all read "Mean absolute error" are
 * indistinguishable to anyone navigating by label.
 */
function StatisticsTiles({ summary }: { summary: OutcomeComparisonSummary }) {
  const families: Array<{
    title: string;
    statistics: OutcomeComparisonSummary["simulation_error"];
  }> = [
    { title: "Simulation", statistics: summary.simulation_error },
    { title: "Forecast + simulation", statistics: summary.forecast_simulation_error },
    { title: "Rainfall forecast", statistics: summary.rainfall_forecast_error },
  ];

  return (
    <div className="grid gap-3 lg:grid-cols-3">
      {families.map(({ title, statistics }) => (
        <Card key={title} className="flex flex-col p-4">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
            {title}
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-muted">
            {statistics.label}
          </p>

          <div className="mt-3">
            <StatTile
              label={`${title} mean absolute error`}
              value={
                statistics.mean_absolute_error === null
                  ? MISSING_DISPLAY
                  : `${formatMeanAbsoluteError(statistics)} ${statistics.unit}`
              }
              hint={
                statistics.mean_absolute_error === null
                  ? "No event in scope could be compared"
                  : describeCoverage(statistics)
              }
            />
          </div>

          <dl className="mt-3 space-y-2">
            <Row
              label="Bias"
              value={formatSignedError(statistics.bias)}
              hint={
                statistics.bias === null
                  ? "No signed mean exists without a comparable event."
                  : statistics.bias > 0
                    ? "The model ran high on average."
                    : "The model ran low on average."
              }
            />
            <Row
              label="Worst event"
              value={formatOutcomeNumber(statistics.largest_absolute_error)}
            />
          </dl>

          {statistics.mean_absolute_error_reason ? (
            <p className="mt-3 rounded-md border border-caution-line bg-caution-soft px-3 py-2 text-[11px] leading-relaxed text-caution">
              {statistics.mean_absolute_error_reason}
            </p>
          ) : null}
        </Card>
      ))}
    </div>
  );
}

function Row({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div>
      <dt className="text-[11px] font-medium uppercase tracking-wide text-faint">
        {label}
      </dt>
      <dd
        className={`text-[13px] leading-relaxed ${
          value === MISSING_DISPLAY ? "text-faint" : "tabular text-ink"
        }`}
      >
        {value}
      </dd>
      {hint ? <p className="text-[11px] text-muted">{hint}</p> : null}
    </div>
  );
}

/**
 * The predicted-against-actual view.
 *
 * Aggregates across the recorded events, then the events themselves, so a
 * reader can move from "the model is off by about this much" to the individual
 * event that shows why without leaving the page.
 *
 * No sample data is ever substituted. If the backend is unreachable the figures
 * are absent and say so, because the one thing this view must never do is show a
 * plausible error for a model that has not been measured.
 */
export default function PredictedVsActualView({
  panId,
}: {
  /** Restricts the view to one pan. Omit for every registered pan. */
  panId?: string;
}) {
  const [reloadToken, setReloadToken] = useState(0);
  const [state, setState] = useState<LoadedState | null>(null);

  const requestKey = `${panId ?? "all"}|${reloadToken}`;
  const loading = state?.key !== requestKey;
  const summary = state?.summary ?? null;
  const outcomes = state?.outcomes ?? [];
  const total = state?.total ?? 0;
  const error = state?.error ?? null;
  const connectivity = error?.isConnectivity ?? false;

  const load = useCallback(async () => {
    try {
      const [comparison, events] = await Promise.all([
        getOutcomeComparison(panId ? { pan_id: panId } : {}),
        listOutcomes(panId ? { pan_id: panId, limit: PAGE_LIMIT } : { limit: PAGE_LIMIT }),
      ]);
      setState({
        key: requestKey,
        summary: comparison,
        outcomes: events.items,
        total: events.total,
        error: null,
      });
    } catch (caught) {
      setState({
        key: requestKey,
        summary: null,
        outcomes: [],
        total: 0,
        error: asApiError(caught, "Unexpected error loading the comparison."),
      });
    }
  }, [requestKey, panId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await load();
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  return (
    <div className="space-y-5">
      {error ? (
        <Alert
          tone={connectivity ? "warning" : "danger"}
          title={
            connectivity
              ? "The comparison is unavailable"
              : "The comparison could not be loaded"
          }
          action={
            <Button variant="secondary" onClick={() => setReloadToken((t) => t + 1)}>
              Try again
            </Button>
          }
        >
          <p>{error.message}</p>
          {connectivity ? (
            <p className="mt-1 text-xs">
              Errors are measured against outcomes recorded in the database.
              Without a reachable backend no accuracy figure exists, and none is
              shown here in its place.
            </p>
          ) : null}
        </Alert>
      ) : null}

      {loading ? (
        <div className="space-y-3">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      ) : summary ? (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile
              label="Events recorded"
              value={summary.total_outcomes}
              hint="Every recorded event, comparable or not"
            />
            <StatTile
              label="Events comparable"
              value={summary.simulation_error.compared}
              hint="With both a prediction and a measurement"
            />
            <StatTile
              label="Awaiting measurement"
              value={summary.events_missing_actual_salinity}
              hint="Excluded from the mean, never zero-filled"
            />
            <StatTile
              label="With operator action"
              value={summary.events_with_operator_action}
              hint="Events explained by what a person did"
            />
          </div>

          {summary.total_outcomes === 0 ? (
            <Card>
              <EmptyState
                title="No outcomes recorded yet"
                description="Nothing has been measured against a prediction yet, so there is no accuracy to report. Record an event from a pan's page and its error will appear here."
                action={
                  <Button variant="primary" onClick={() => setReloadToken((t) => t + 1)}>
                    Refresh
                  </Button>
                }
              />
            </Card>
          ) : (
            <StatisticsTiles summary={summary} />
          )}
        </>
      ) : null}

      {!loading && !error && outcomes.length > 0 ? (
        <Card>
          <CardHeader
            title="Event by event"
            description="Every comparison, with the figures each one is waiting on."
            actions={
              <span className="tabular text-xs text-muted">
                {total} {total === 1 ? "event" : "events"}
              </span>
            }
          />
          <OutcomeHistory
            outcomes={outcomes}
            hrefFor={(outcome) => `/pans/${encodeURIComponent(outcome.pan_id)}`}
          />
          {total > outcomes.length ? (
            <p className="border-t border-line px-5 py-3 text-[11px] text-muted">
              Showing the {outcomes.length} most recent of {total} recorded
              events.{" "}
              <Link href="/pans" className="underline hover:text-ink-soft">
                Open a pan
              </Link>{" "}
              to record more.
            </p>
          ) : null}
        </Card>
      ) : null}
    </div>
  );
}
