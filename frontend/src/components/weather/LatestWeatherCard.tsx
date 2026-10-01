"use client";

import { DataPoint, Eyebrow } from "@/components/ui/Primitives";
import { Alert } from "@/components/ui/Feedback";
import { WeatherSourceBadge } from "@/components/weather/WeatherSourceBadge";
import { isDemonstrationSource } from "@/lib/demonstration";
import {
  WEATHER_RANGES,
  formatFullTimestamp,
  type WeatherObservation,
} from "@/lib/weather";

/**
 * The pan's newest weather record.
 *
 * When no record exists the card says so rather than falling back to anything
 * invented. A forecast is shown as a forecast, not as current conditions,
 * because the most recent record is not automatically the most recent
 * observation.
 *
 * A demonstration gets a banner as well as its badge, for the reason the newest
 * record matters more here than elsewhere: a demonstration forecast is what the
 * twin's current forecast column will read from, so a reader who takes these
 * rainfall figures for the sky's own would carry the mistake into the
 * simulation and the recommendation below.
 */
export default function LatestWeatherCard({
  latest,
}: {
  latest: WeatherObservation | null;
}) {
  if (!latest) {
    return (
      <div className="p-5">
        <p className="text-[13px] leading-relaxed text-muted">
          No weather has been recorded for this pan. The conditions above it are
          unknown rather than zero, and nothing here is filled in from a design
          value or a neighbouring pan.
        </p>
      </div>
    );
  }

  const demonstration = isDemonstrationSource(latest.source);

  return (
    <div>
      {demonstration ? (
        <div className="px-5 pt-5">
          <Alert tone="demo" title="This forecast is a demonstration">
            <p>
              The rain below was typed in to show the workflow. It was not
              forecast by any service, and no research dataset is built from it.
            </p>
          </Alert>
        </div>
      ) : null}
      <dl className="grid grid-cols-2 gap-x-5 gap-y-5 p-5">
        {(Object.keys(WEATHER_RANGES) as Array<
          keyof typeof WEATHER_RANGES
        >).map((key) => (
          <DataPoint
            key={key}
            label={WEATHER_RANGES[key].label}
            value={latest[key] === null ? "—" : String(latest[key])}
            unit={latest[key] === null ? undefined : WEATHER_RANGES[key].unit}
          />
        ))}
      </dl>
      <div className="border-t border-line px-5 py-3">
        <Eyebrow>Newest record</Eyebrow>
        <p className="tabular mt-1.5 text-[13px] text-ink">
          {formatFullTimestamp(latest.timestamp)}
        </p>
        {latest.is_forecast && latest.forecast_for ? (
          <p className="tabular mt-1 text-xs text-caution">
            A forecast for {formatFullTimestamp(latest.forecast_for)}, not an
            observation of it.
          </p>
        ) : null}
        <div className="mt-2.5">
          <WeatherSourceBadge source={latest.source} />
        </div>
        <p className="mt-2.5 text-[11px] leading-relaxed text-muted">
          This is a copy of the most recent record, not an average. Earlier
          records are kept in the history below and do not change it.
        </p>
      </div>
    </div>
  );
}
