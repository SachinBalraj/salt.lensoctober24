"use client";

import { useEffect, useState } from "react";
import ObservationChart from "@/components/observations/ObservationChart";
import ObservationForm from "@/components/observations/ObservationForm";
import ObservationHistory from "@/components/observations/ObservationHistory";
import LatestObservationCard from "@/components/observations/LatestObservationCard";
import { Alert, Skeleton } from "@/components/ui/Feedback";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import type { PanObservation } from "@/lib/observations";
import { listObservations } from "@/lib/observationsApi";
import type { LatestObservation, SaltPan } from "@/lib/pans";
import { getPan } from "@/lib/pansApi";

/** Readings requested per load. The total is always reported alongside them. */
const HISTORY_LIMIT = 200;

interface HistoryState {
  key: string;
  observations: PanObservation[];
  total: number;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError(fallback, { status: -1 });
}

/**
 * Module 03 on the pan detail page: record a reading, see the pan's current
 * observed state, review the history and chart the stored readings.
 *
 * For a demo record nothing here runs. The form is inert, no request is made,
 * and the history and charts stay empty rather than being filled with
 * fabricated readings.
 */
export default function ObservationPanel({
  pan,
  isDemo = false,
  onEvidenceRecorded,
}: {
  pan: SaltPan;
  isDemo?: boolean;
  /**
   * Called after a reading is stored, so the digital twin below can re-read the
   * join against it. The twin is a view over this record, not a copy of it.
   */
  onEvidenceRecorded?: () => void;
}) {
  const [reloadToken, setReloadToken] = useState(0);
  const [history, setHistory] = useState<HistoryState | null>(null);
  const [latest, setLatest] = useState<LatestObservation | null>(
    pan.latest_observation,
  );

  const requestKey = `${pan.pan_id}|${reloadToken}`;
  const loading = !isDemo && history?.key !== requestKey;
  const observations = isDemo ? [] : (history?.observations ?? []);
  const total = isDemo ? 0 : (history?.total ?? 0);
  const truncated = total > observations.length;

  useEffect(() => {
    if (isDemo) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await listObservations({
          pan_id: pan.pan_id,
          limit: HISTORY_LIMIT,
        });
        if (cancelled) return;
        setHistory({
          key: requestKey,
          observations: response.items,
          total: response.total,
          error: null,
        });
      } catch (caught) {
        if (cancelled) return;
        setHistory({
          key: requestKey,
          observations: [],
          total: 0,
          error: asApiError(caught, "Unexpected error loading observations."),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestKey, isDemo, pan.pan_id]);

  /**
   * After a save the pan is re-read rather than patched locally.
   *
   * A backdated reading is stored but does not become the pan's current state,
   * so only the server knows which reading is newest.
   */
  async function handleRecorded() {
    setReloadToken((token) => token + 1);
    onEvidenceRecorded?.();
    if (isDemo) return;
    try {
      const refreshed = await getPan(pan.pan_id);
      setLatest(refreshed.latest_observation);
    } catch {
      // The reading is already stored. Leaving the current state visible is
      // better than clearing it after a failed refresh.
    }
  }

  const historyError = isDemo ? null : (history?.error ?? null);
  const connectivity = historyError?.isConnectivity ?? false;

  return (
    <div className="mt-4 space-y-4">
      <Card>
        <CardHeader
          title="Record an observation"
          description="Manual data entry. Values are stored exactly as entered."
        />
        <ObservationForm
          panId={pan.pan_id}
          disabled={isDemo}
          onRecorded={() => void handleRecorded()}
        />
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Observed trend"
            description="Stored readings only. Gaps are preserved and nothing is interpolated."
          />
          {loading ? (
            <div className="space-y-3 p-5">
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : historyError && !connectivity ? (
            <div className="p-5">
              <Alert tone="danger" title="The history could not be loaded">
                {historyError.message}
              </Alert>
            </div>
          ) : (
            <>
              {truncated ? (
                <div className="px-5 pt-4">
                  <Alert tone="warning">
                    Showing the {observations.length} most recent of {total}{" "}
                    stored readings. Earlier readings exist but are not plotted
                    here.
                  </Alert>
                </div>
              ) : null}
              <ObservationChart observations={observations} />
            </>
          )}
        </Card>

        <Card>
          <CardHeader title="Current observed state" />
          {historyError && connectivity ? (
            <div className="p-5">
              <Alert tone="info" title="Observations are unavailable">
                {historyError.message}
              </Alert>
            </div>
          ) : (
            <LatestObservationCard latest={latest} />
          )}
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Observation history"
          description="Every stored reading for this pan, newest first."
          actions={
            <span className="tabular text-xs text-muted">
              {loading ? "—" : `${total} ${total === 1 ? "reading" : "readings"}`}
            </span>
          }
        />
        {loading ? (
          <div className="space-y-3 p-5">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        ) : historyError && !connectivity ? (
          <div className="p-5">
            <Alert
              tone="danger"
              title="The history could not be loaded"
              action={
                <button
                  type="button"
                  onClick={() => setReloadToken((token) => token + 1)}
                  className="text-xs font-medium text-ink-soft hover:underline"
                >
                  Try again
                </button>
              }
            >
              {historyError.message}
            </Alert>
          </div>
        ) : (
          <ObservationHistory observations={observations} />
        )}
      </Card>
    </div>
  );
}
