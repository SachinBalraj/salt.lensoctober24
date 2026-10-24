"use client";

import { useEffect, useState } from "react";
import LatestWeatherCard from "@/components/weather/LatestWeatherCard";
import WeatherChart from "@/components/weather/WeatherChart";
import WeatherForm from "@/components/weather/WeatherForm";
import WeatherHistory from "@/components/weather/WeatherHistory";
import WeatherProvidersCard from "@/components/weather/WeatherProvidersCard";
import WeatherFetchPanel from "@/components/weather/WeatherFetchPanel";
import { Alert, Skeleton } from "@/components/ui/Feedback";
import { Card, CardHeader } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import type { WeatherObservation, WeatherProvidersResponse } from "@/lib/weather";
import { listWeather, listWeatherProviders } from "@/lib/weatherApi";
import type { SaltPan } from "@/lib/pans";

/** Records requested per load. The true total is always reported alongside. */
const HISTORY_LIMIT = 200;

interface HistoryState {
  key: string;
  observations: WeatherObservation[];
  total: number;
  error: ApiError | null;
}

interface ProvidersState {
  key: string;
  providers: WeatherProvidersResponse | null;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError(fallback, { status: -1 });
}

/**
 * Module 04 on the pan detail page: record what the weather did over a pan,
 * see which sources are connected, review the full history and chart it.
 *
 * Two things are deliberately always visible. The source of every record, and
 * whether a weather API is actually connected. When none is, that is stated
 * plainly rather than hidden behind a disabled control, because the honest
 * answer to "where did this number come from" is part of the record.
 *
 * For a demo record nothing here runs. The form is inert, no request is made,
 * and the history and charts stay empty rather than being filled with
 * fabricated readings.
 */
export default function WeatherPanel({
  pan,
  isDemo = false,
  onEvidenceRecorded,
}: {
  pan: SaltPan;
  isDemo?: boolean;
  /**
   * Called after a weather record is stored, so the digital twin below can
   * re-read the join against it. The twin cites this record as evidence and must
   * not keep showing a state assembled before it existed.
   */
  onEvidenceRecorded?: () => void;
}) {
  const [reloadToken, setReloadToken] = useState(0);
  const [history, setHistory] = useState<HistoryState | null>(null);
  const [providersState, setProvidersState] = useState<ProvidersState | null>(null);

  const reload = () => {
    setReloadToken((token) => token + 1);
    onEvidenceRecorded?.();
  };

  const requestKey = `${pan.pan_id}|${reloadToken}`;
  const loading = !isDemo && history?.key !== requestKey;
  const providersLoading =
    !isDemo && providersState?.key !== requestKey;
  const observations = isDemo ? [] : (history?.observations ?? []);
  const total = isDemo ? 0 : (history?.total ?? 0);
  const truncated = total > observations.length;
  const providers = isDemo ? null : (providersState?.providers ?? null);
  const providerCount = providers?.registered.length ?? 0;

  useEffect(() => {
    if (isDemo) return;
    let cancelled = false;
    void (async () => {
      try {
        const response = await listWeather({
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
          error: asApiError(caught, "Unexpected error loading weather records."),
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
        const response = await listWeatherProviders();
        if (cancelled) return;
        setProvidersState({ key: requestKey, providers: response, error: null });
      } catch (caught) {
        if (cancelled) return;
        setProvidersState({
          key: requestKey,
          providers: null,
          error: asApiError(caught, "Unexpected error checking weather sources."),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestKey, isDemo]);

  const historyError = isDemo ? null : (history?.error ?? null);
  const connectivity = historyError?.isConnectivity ?? false;
  const latest = observations[0] ?? null;

  return (
    <div className="mt-4 space-y-4">
      <Card>
        <CardHeader
          title="Record weather"
          description="Manual data entry. Stored exactly as entered, and labelled as hand-entered."
        />
        <WeatherForm
          panId={pan.pan_id}
          disabled={isDemo}
          providersConnected={providerCount}
          onRecorded={reload}
        />
      </Card>

      {!isDemo ? (
        <WeatherFetchPanel
          panId={pan.pan_id}
          panName={pan.pan_name}
          hasCoordinates={
            pan.latitude !== null &&
            pan.latitude !== undefined &&
            pan.longitude !== null &&
            pan.longitude !== undefined
          }
          coordinates={
            pan.latitude !== null &&
            pan.latitude !== undefined &&
            pan.longitude !== null &&
            pan.longitude !== undefined
              ? { latitude: pan.latitude, longitude: pan.longitude }
              : null
          }
          onSaved={reload}
        />
      ) : null}

      <WeatherProvidersCard
        panId={pan.pan_id}
        providers={providers}
        loading={providersLoading}
        error={isDemo ? null : (providersState?.error ?? null)}
        disabled={isDemo}
        isDemo={isDemo}
        onFetched={reload}
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Weather trend"
            description="Stored records only. Gaps are preserved and nothing is interpolated."
          />
          {loading ? (
            <div className="space-y-3 p-5">
              <Skeleton className="h-32 w-full" />
              <Skeleton className="h-32 w-full" />
            </div>
          ) : historyError && !connectivity ? (
            <div className="p-5">
              <Alert tone="danger" title="The weather history could not be loaded">
                {historyError.message}
              </Alert>
            </div>
          ) : (
            <>
              {truncated ? (
                <div className="px-5 pt-4">
                  <Alert tone="warning">
                    Showing the {observations.length} most recent of {total}{" "}
                    stored records. Earlier records exist but are not plotted
                    here.
                  </Alert>
                </div>
              ) : null}
              <WeatherChart observations={observations} />
            </>
          )}
        </Card>

        <Card>
          <CardHeader title="Newest weather record" />
          {historyError && connectivity ? (
            <div className="p-5">
              <Alert tone="info" title="Weather records are unavailable">
                {historyError.message}
              </Alert>
            </div>
          ) : (
            <LatestWeatherCard latest={latest} />
          )}
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Weather history"
          description="Every stored record for this pan, newest first, with its source on each row."
          actions={
            <span className="tabular text-xs text-muted">
              {loading ? "—" : `${total} ${total === 1 ? "record" : "records"}`}
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
              title="The weather history could not be loaded"
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
          <WeatherHistory observations={observations} />
        )}
      </Card>
    </div>
  );
}
