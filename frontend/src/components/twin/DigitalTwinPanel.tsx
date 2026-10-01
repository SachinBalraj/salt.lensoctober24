"use client";

import { useEffect, useState } from "react";
import TwinSnapshotTable from "@/components/twin/TwinSnapshotTable";
import TwinStateCard from "@/components/twin/TwinStateCard";
import { Button, Spinner } from "@/components/ui/Controls";
import { Alert, Skeleton } from "@/components/ui/Feedback";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import {
  formatTwinTimestamp,
  isTwinCurrent,
  summarizeAvailability,
  type DigitalTwinState,
} from "@/lib/digitalTwin";
import {
  getTwinState,
  listTwinHistory,
  refreshTwinState,
} from "@/lib/digitalTwinApi";
import type { SaltPan } from "@/lib/pans";

/** Snapshots requested per load. The true total is reported alongside. */
const HISTORY_LIMIT = 50;

interface TwinState {
  key: string;
  twin: DigitalTwinState | null;
  error: ApiError | null;
}

interface HistoryState {
  key: string;
  snapshots: DigitalTwinState[];
  total: number;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError(fallback, { status: -1 });
}

/**
 * Module 05 on the pan detail page: the pan's current known state, the records it
 * rests on, and the stored snapshots that let a change be analysed later.
 *
 * The panel is built to be honest under three conditions rather than to look
 * complete. A pan with nothing recorded still renders, with every quantity marked
 * unavailable and the reason attached. A pan whose newest reading is a week old
 * says so, because the assembly time is always seconds old and would otherwise
 * imply a currency the evidence does not have. And when the API cannot be
 * reached, the state is withheld rather than filled from a previous render or a
 * design value.
 *
 * Storing a snapshot is an explicit button press. Reading the twin is free, so
 * simply opening this page never manufactures history.
 */
export default function DigitalTwinPanel({
  pan,
  isDemo = false,
}: {
  pan: SaltPan;
  isDemo?: boolean;
}) {
  const [reloadToken, setReloadToken] = useState(0);
  const [state, setState] = useState<TwinState | null>(null);
  const [history, setHistory] = useState<HistoryState | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const requestKey = `${pan.pan_id}|${reloadToken}`;
  const loading = !isDemo && state?.key !== requestKey;
  const historyLoading = !isDemo && history?.key !== requestKey;

  useEffect(() => {
    if (isDemo) return;
    let cancelled = false;
    void (async () => {
      try {
        const twin = await getTwinState(pan.pan_id);
        if (cancelled) return;
        setState({ key: requestKey, twin, error: null });
      } catch (caught) {
        if (cancelled) return;
        setState({
          key: requestKey,
          twin: null,
          error: asApiError(caught, "Unexpected error reading the digital twin."),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestKey, isDemo, pan.pan_id]);

  useEffect(() => {
    if (isDemo) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await listTwinHistory(pan.pan_id, {
          limit: HISTORY_LIMIT,
        });
        if (cancelled) return;
        setHistory({
          key: requestKey,
          snapshots: response.items,
          total: response.total,
          error: null,
        });
      } catch (caught) {
        if (cancelled) return;
        setHistory({
          key: requestKey,
          snapshots: [],
          total: 0,
          error: asApiError(caught, "Unexpected error loading twin snapshots."),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestKey, isDemo, pan.pan_id]);

  async function handleRefresh() {
    setRefreshing(true);
    setNotice(null);
    try {
      const result = await refreshTwinState(pan.pan_id);
      setState({ key: requestKey, twin: result.state, error: null });
      setNotice(
        result.changed
          ? "Snapshot stored. The pan's state has changed since the previous snapshot."
          : "Snapshot stored. Nothing has changed since the previous snapshot.",
      );
      setReloadToken((token) => token + 1);
    } catch (caught) {
      setNotice(asApiError(caught, "Unexpected error storing a snapshot.").message);
    } finally {
      setRefreshing(false);
    }
  }

  const twin = isDemo ? null : (state?.twin ?? null);
  const error = isDemo ? null : (state?.error ?? null);
  const connectivity = error?.isConnectivity ?? false;
  const availability = twin ? summarizeAvailability(twin) : null;
  const current = twin ? isTwinCurrent(twin) : false;
  const snapshots = isDemo ? [] : (history?.snapshots ?? []);
  const total = isDemo ? 0 : (history?.total ?? 0);

  const title = `Current digital twin — pan ${pan.pan_id}`;

  return (
    <div className="mt-4 space-y-4">
      <header>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink">
          {title}
        </h2>
        <p className="mt-1 text-xs text-muted">
          The latest known state of this pan, joined from the newest observation,
          the newest relevant weather record and the pan&apos;s registered
          properties. Values are copied, never inferred.
        </p>
      </header>

      {isDemo ? (
        <Card>
          <Alert tone="demo" title="No digital twin for a demo record">
            <p>
              This pan exists only in your browser. A twin is assembled from stored
              records, so there is nothing here to join and no snapshot to store.
              Register the pan and record real readings to see one.
            </p>
          </Alert>
        </Card>
      ) : (
        <>
          {error ? (
            <Alert
              tone={connectivity ? "warning" : "danger"}
              title={
                connectivity
                  ? "The digital twin is unavailable"
                  : "The digital twin could not be read"
              }
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
              {error.message}
            </Alert>
          ) : null}

          {loading ? (
            <Card>
              <div className="space-y-3 p-5">
                <Skeleton className="h-24 w-full" />
                <Skeleton className="h-24 w-full" />
              </div>
            </Card>
          ) : twin ? (
            <>
              {!twin.is_complete && availability ? (
                <Alert tone="warning" title="This twin is incomplete">
                  <p>{availability.headline}</p>
                  {availability.detail ? <p className="mt-1">{availability.detail}</p> : null}
                </Alert>
              ) : null}
              {twin.is_complete && !current ? (
                <Alert tone="warning" title="Complete, but the evidence is ageing">
                  <p>
                    Every quantity is present, but at least one contributing record
                    is more than a week old. The figures below describe the pan as
                    it was then, not as it is now.
                  </p>
                </Alert>
              ) : null}

              <Card>
                <CardHeader
                  title={title}
                  description={`Assembled ${formatTwinTimestamp(twin.timestamp)} from stored records`}
                />
                <TwinStateCard twin={twin} />
              </Card>
            </>
          ) : null}

          {notice ? (
            <Alert tone="info" title="Snapshot">
              {notice}
            </Alert>
          ) : null}

          <Card>
            <CardHeader
              title="Snapshot history"
              description="Stored states for this pan, newest first, so changes can be analysed later."
              actions={
                <div className="flex items-center gap-3">
                  <span className="tabular text-xs text-muted">
                    {historyLoading ? "—" : `${total} ${total === 1 ? "snapshot" : "snapshots"}`}
                  </span>
                  <Button
                    variant="secondary"
                    onClick={() => void handleRefresh()}
                    disabled={refreshing}
                  >
                    {refreshing ? <Spinner label="Storing" /> : "Store snapshot"}
                  </Button>
                </div>
              }
            />
            {historyLoading ? (
              <div className="space-y-3 p-5">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : (
              <TwinSnapshotTable snapshots={snapshots} />
            )}
          </Card>
        </>
      )}
    </div>
  );
}
