"use client";

import { useEffect, useState } from "react";
import OutcomeForm from "@/components/outcomes/OutcomeForm";
import OutcomeHistory from "@/components/outcomes/OutcomeHistory";
import { Button } from "@/components/ui/Controls";
import { Alert, EmptyState, Skeleton } from "@/components/ui/Feedback";
import { Card, CardHeader, StatTile } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import { formatOutcomeNumber, MISSING_DISPLAY, type EventOutcome } from "@/lib/outcomes";
import { getOutcomeComparison, listOutcomes } from "@/lib/outcomesApi";
import type { SaltPan } from "@/lib/pans";

/** Outcomes requested per load. The total is always reported alongside them. */
const HISTORY_LIMIT = 100;

interface HistoryState {
  key: string;
  outcomes: EventOutcome[];
  total: number;
  error: ApiError | null;
}

interface SummaryState {
  key: string;
  mean: number | null;
  compared: number;
  notComparable: number;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError(fallback, { status: -1 });
}

/**
 * Module 15 on the pan detail page: record what happened, and see how far the
 * prediction was off.
 *
 * For a demo record nothing here runs. The form is inert, no request is made, and
 * the history and the error figures stay empty rather than being filled with
 * fabricated events, because a fabricated outcome would put invented errors into
 * the one place that is supposed to measure the model.
 */
export default function OutcomePanel({
  pan,
  isDemo = false,
}: {
  pan: SaltPan;
  isDemo?: boolean;
}) {
  const [reloadToken, setReloadToken] = useState(0);
  const [history, setHistory] = useState<HistoryState | null>(null);
  const [summary, setSummary] = useState<SummaryState | null>(null);

  const requestKey = `${pan.pan_id}|${reloadToken}`;
  const loading = !isDemo && history?.key !== requestKey;
  const outcomes = isDemo ? [] : (history?.outcomes ?? []);
  const total = isDemo ? 0 : (history?.total ?? 0);
  const truncated = total > outcomes.length;
  const comparable = outcomes.filter((outcome) => outcome.is_comparable).length;
  const waiting = outcomes.length - comparable;

  useEffect(() => {
    if (isDemo) return;
    let cancelled = false;
    void (async () => {
      try {
        const [response, comparison] = await Promise.all([
          listOutcomes({ pan_id: pan.pan_id, limit: HISTORY_LIMIT }),
          getOutcomeComparison({ pan_id: pan.pan_id }),
        ]);
        if (cancelled) return;
        setHistory({
          key: requestKey,
          outcomes: response.items,
          total: response.total,
          error: null,
        });
        setSummary({
          key: requestKey,
          mean: comparison.simulation_error.mean_absolute_error,
          compared: comparison.simulation_error.compared,
          notComparable: comparison.simulation_error.not_comparable,
          error: null,
        });
      } catch (caught) {
        if (cancelled) return;
        const error = asApiError(caught, "Unexpected error loading outcomes.");
        setHistory({
          key: requestKey,
          outcomes: [],
          total: 0,
          error,
        });
        setSummary({
          key: requestKey,
          mean: null,
          compared: 0,
          notComparable: 0,
          error,
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestKey, isDemo, pan.pan_id]);

  const historyError = isDemo ? null : (history?.error ?? null);
  const connectivity = historyError?.isConnectivity ?? false;
  const mean = isDemo ? null : (summary?.mean ?? null);

  return (
    <div className="mt-4 space-y-4">
      <Card>
        <CardHeader
          title="Record what actually happened"
          description="Ground truth for this pan. The prediction and the error are computed on the server from the figures you enter."
        />
        <OutcomeForm
          panId={pan.pan_id}
          disabled={isDemo}
          onRecorded={() => setReloadToken((token) => token + 1)}
        />
      </Card>

      {isDemo || historyError ? null : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            label="Mean absolute error"
            value={
              mean === null
                ? MISSING_DISPLAY
                : `${formatOutcomeNumber(mean)} g/L`
            }
            hint={
              summary && summary.compared > 0
                ? `Over ${summary.compared} compared ${summary.compared === 1 ? "event" : "events"}`
                : "No event could be compared yet"
            }
          />
          <StatTile
            label="Comparable"
            value={summary?.compared ?? 0}
            hint="Events with both a prediction and a measurement"
          />
          <StatTile
            label="Waiting"
            value={summary?.notComparable ?? 0}
            hint="Excluded from the mean, not counted as zero"
          />
          <StatTile
            label="Recorded"
            value={total}
            hint="All outcomes for this pan"
          />
        </div>
      )}

      <Card>
        <CardHeader
          title="Recorded outcomes"
          description="Newest first. Each event can be expanded to see the three comparisons and what it was recorded against."
          actions={
            <span className="tabular text-xs text-muted">
              {loading ? "—" : `${total} ${total === 1 ? "event" : "events"}`}
            </span>
          }
        />
        {loading ? (
          <div className="space-y-3 p-5">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : historyError ? (
          <div className="p-5">
            <Alert
              tone={connectivity ? "warning" : "danger"}
              title={
                connectivity
                  ? "The recorded outcomes could not be reached"
                  : "The outcomes could not be loaded"
              }
              action={
                <Button
                  variant="secondary"
                  onClick={() => setReloadToken((token) => token + 1)}
                >
                  Try again
                </Button>
              }
            >
              <p>{historyError.message}</p>
              {connectivity ? (
                <p className="mt-1 text-xs">
                  Whether this pan has any recorded outcomes is unknown, so no
                  history and no error figures are shown. Nothing here is being
                  reported as a zero.
                </p>
              ) : null}
            </Alert>
          </div>
        ) : outcomes.length === 0 ? (
          <EmptyState
            title="Nothing recorded for this pan yet"
            description="Record an event above and the mass-balance prediction will be compared against what the pan actually did. Events where a measurement is missing stay out of the mean rather than being counted as perfect."
          />
        ) : (
          <>
            {truncated ? (
              <div className="px-5 pt-4">
                <Alert tone="warning">
                  Showing the {outcomes.length} most recent of {total} recorded
                  events. Earlier events exist but are not listed here.
                </Alert>
              </div>
            ) : null}
            {waiting > 0 ? (
              <div className="px-5 pt-4">
                <Alert tone="info" title="Some events are still waiting on a measurement">
                  {waiting} of {outcomes.length} listed{" "}
                  {waiting === 1 ? "event has" : "events have"} no recorded
                  measurement, so {waiting === 1 ? "it is" : "they are"} left out
                  of the mean absolute error rather than counted as zero.
                </Alert>
              </div>
            ) : null}
            <OutcomeHistory outcomes={outcomes} />
          </>
        )}
      </Card>
    </div>
  );
}
