"use client";

import { useState } from "react";
import { Button, Spinner } from "@/components/ui/Controls";
import { Alert } from "@/components/ui/Feedback";
import { Card, CardHeader, Eyebrow } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import { WEATHER_SOURCE_LABELS, type WeatherProvidersResponse } from "@/lib/weather";
import { ingestForecast } from "@/lib/weatherApi";

/**
 * What this deployment can fetch weather from.
 *
 * With no provider connected the card says exactly that, in plain words, and
 * explains the consequence for the record: everything stored here was typed by
 * a person. That sentence is the whole reason the card exists, because the
 * alternative — a forecast button that quietly returns nothing, or a plausible
 * placeholder forecast — would be worse than an honest absence.
 *
 * Once a provider is registered the fetch button appears and the backend is
 * what stamps the stored records as API-sourced.
 */
export default function WeatherProvidersCard({
  panId,
  providers,
  loading = false,
  error,
  disabled = false,
  isDemo = false,
  onFetched,
}: {
  panId: string;
  providers: WeatherProvidersResponse | null;
  loading?: boolean;
  error: ApiError | null;
  disabled?: boolean;
  isDemo?: boolean;
  onFetched: () => void;
}) {
  const [fetching, setFetching] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  async function handleFetch(name: string) {
    setFetching(name);
    setFetchError(null);
    setResult(null);
    try {
      const response = await ingestForecast(name, { pan_id: panId });
      setResult(
        response.stored === 0
          ? `${name} returned no forecast for this window. Nothing was stored.`
          : `Fetched ${response.stored} forecast ${response.stored === 1 ? "point" : "points"} from ${name}, stored as ${WEATHER_SOURCE_LABELS[response.source]}.${
              response.rejected > 0
                ? ` ${response.rejected} could not be stored and were left out.`
                : ""
            }`,
      );
      onFetched();
    } catch (caught) {
      setFetchError(
        caught instanceof ApiError
          ? caught.message
          : `Could not fetch a forecast from ${name}.`,
      );
    } finally {
      setFetching(null);
    }
  }

  const connected = providers?.registered ?? [];

  return (
    <Card>
      <CardHeader
        title="Weather data sources"
        description="Which forecasts SaltLens can retrieve, and where every stored record came from."
      />
      <div className="space-y-4 p-5">
        {loading ? (
          <p className="text-[13px] text-muted">Checking for connected weather APIs…</p>
        ) : isDemo ? (
          <Alert tone="demo" title="Demo record — not research data">
            <p>
              This record exists only in your browser, so which weather APIs the
              backend can reach is not reported against it. No forecast can be
              fetched and nothing entered here would be stored.
            </p>
          </Alert>
        ) : error ? (
          <Alert tone="warning" title="The connected sources could not be checked">
            <p>{error.message}</p>
          </Alert>
        ) : connected.length === 0 ? (
          <Alert tone="info" title="No weather API is connected">
            <div className="space-y-2">
              <p>
                {providers?.detail ??
                  "No weather API is connected to this deployment."}
              </p>
              <p>
                Every weather record for this pan was therefore entered by hand
                and is labelled <strong className="font-semibold">Entered by
                hand</strong>. SaltLens will not label a typed figure as a
                fetched one.
              </p>
            </div>
          </Alert>
        ) : (
          <div className="space-y-3">
            <div>
              <Eyebrow>Connected</Eyebrow>
              <ul className="mt-2 space-y-1.5">
                {connected.map((provider) => (
                  <li
                    key={provider.name}
                    className="flex flex-wrap items-center justify-between gap-2"
                  >
                    <span className="text-[13px] text-ink">
                      {provider.name}
                    </span>
                    <Button
                      variant="secondary"
                      disabled={disabled || fetching !== null}
                      onClick={() => void handleFetch(provider.name)}
                    >
                      {fetching === provider.name ? (
                        <Spinner label="Fetching…" />
                      ) : (
                        "Fetch forecast"
                      )}
                    </Button>
                  </li>
                ))}
              </ul>
            </div>
            <p className="text-[11px] leading-relaxed text-muted">
              {providers?.detail} Fetched points are stored with the source{" "}
              {WEATHER_SOURCE_LABELS.weather_api}, applied by the server once a
              provider has actually returned data.
            </p>
          </div>
        )}

        {fetchError ? (
          <Alert tone="danger" title="The forecast was not fetched">
            {fetchError}
          </Alert>
        ) : null}
        {result ? (
          <Alert tone="success" title="Forecast fetch complete">
            {result}
          </Alert>
        ) : null}
      </div>
    </Card>
  );
}
