"use client";

import { useState } from "react";
import { Button, Spinner } from "@/components/ui/Controls";
import { TextField } from "@/components/ui/Fields";
import { Alert } from "@/components/ui/Feedback";
import { Badge, Card, CardHeader } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import { formatFullTimestamp } from "@/lib/weather";
import {
  fetchWeatherFromOpenMeteo,
  saveFetchedWeather,
  type WeatherFetchResult,
} from "@/lib/weatherApi";

/**
 * Fetch one instant of Open-Meteo weather over a pan, review it, save it.
 *
 * Nothing is written until the operator presses Save, and what gets written is
 * decided by the server: the save call carries only the fetch token, so the
 * values stored are exactly the ones Open-Meteo returned. An edited preview
 * could not become a record claiming an API origin, because the preview is
 * never itself submitted.
 */
export default function WeatherFetchPanel({
  panId,
  panName,
  hasCoordinates,
  coordinates,
  disabled = false,
  onSaved,
}: {
  panId: string;
  panName?: string;
  hasCoordinates: boolean;
  coordinates?: { latitude: number; longitude: number } | null;
  disabled?: boolean;
  onSaved: () => void;
}) {
  const today = new Date().toISOString().split("T")[0];
  const [date, setDate] = useState(today);
  const [time, setTime] = useState(new Date().toTimeString().slice(0, 5));
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [result, setResult] = useState<WeatherFetchResult | null>(null);
  const [fetchToken, setFetchToken] = useState<string | null>(null);

  async function handleFetch() {
    setLoading(true);
    setError(null);
    setSaved(null);
    setResult(null);
    setFetchToken(null);
    try {
      const res = await fetchWeatherFromOpenMeteo({ pan_id: panId, date, time });
      if (res.success && res.data) {
        setResult(res.data);
        setFetchToken(res.fetch_token ?? null);
      } else {
        setError(res.message || "Weather could not be retrieved from Open-Meteo.");
      }
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : "Weather could not be retrieved from Open-Meteo.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    if (!fetchToken) return;
    setSaving(true);
    setError(null);
    try {
      const response = await saveFetchedWeather(fetchToken);
      setSaved(response.message);
      setFetchToken(null);
      onSaved();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.message
          : "The weather record could not be saved.",
      );
    } finally {
      setSaving(false);
    }
  }

  if (!hasCoordinates) {
    return (
      <Card>
        <CardHeader
          title="Fetch weather from Open-Meteo"
          description="Use registered pan coordinates"
        />
        <div className="p-5">
          <Alert tone="warning">
            Weather cannot be fetched because this pan has no registered
            coordinates.
          </Alert>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Fetch weather from Open-Meteo"
        description="Retrieve weather using the pan's registered coordinates, review it, then save it."
      />
      <div className="space-y-4 p-5">
        <div className="text-sm text-ink-soft">
          <div>
            <strong>Pan:</strong> {panName || panId}
          </div>
          <div>
            <strong>Coordinates:</strong> {coordinates?.latitude.toFixed(4)},{" "}
            {coordinates?.longitude.toFixed(4)}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4">
          <TextField label="Date" value={date} onChange={setDate} />
          <TextField label="Time" value={time} onChange={setTime} />
        </div>
        <Button onClick={() => void handleFetch()} disabled={disabled || loading}>
          {loading ? <Spinner label="Fetching..." /> : "Fetch Weather"}
        </Button>
        {error ? <Alert tone="danger">{error}</Alert> : null}
        {saved ? <Alert tone="success">{saved}</Alert> : null}
        {result ? (
          <div className="space-y-3 rounded border border-line bg-inset p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-semibold text-ink">
                Weather retrieved successfully
              </span>
              <Badge tone="neutral">Weather API</Badge>
            </div>
            <div className="grid grid-cols-2 gap-2 text-sm tabular text-ink-soft">
              <div>Rainfall: {result.rainfall_mm ?? "N/A"} mm</div>
              <div>Temperature: {result.ambient_temperature_c ?? "N/A"} °C</div>
              <div>Humidity: {result.humidity_percent ?? "N/A"} %</div>
              <div>
                Rain probability: {result.rain_probability_percent ?? "N/A"} %
              </div>
            </div>
            <div className="space-y-1 text-xs text-muted">
              <div>
                Source: {result.provider} (
                {result.weather_type === "historical"
                  ? "historical archive"
                  : "forecast"}
                )
              </div>
              <div>
                Coordinates: {result.latitude.toFixed(4)},{" "}
                {result.longitude.toFixed(4)}
              </div>
              <div>
                Requested: {formatFullTimestamp(result.requested_at)} (local{" "}
                {result.requested_date} {result.requested_time})
              </div>
              <div>Retrieved at: {formatFullTimestamp(result.retrieved_at)}</div>
            </div>
            <p className="text-xs leading-relaxed text-muted">
              Saving stores this record with source <strong>Weather API</strong>.
              The values sent are the ones the server fetched — nothing typed
              here is written.
            </p>
            <Button
              variant="primary"
              onClick={() => void handleSave()}
              disabled={disabled || saving || fetchToken === null}
            >
              {saving ? <Spinner label="Saving..." /> : "Save Weather Record"}
            </Button>
          </div>
        ) : null}
      </div>
    </Card>
  );
}
