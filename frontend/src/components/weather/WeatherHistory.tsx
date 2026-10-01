"use client";

import { EmptyState } from "@/components/ui/Feedback";
import { Badge } from "@/components/ui/Primitives";
import { WeatherSourceBadge } from "@/components/weather/WeatherSourceBadge";
import {
  formatFullTimestamp,
  type WeatherObservation,
} from "@/lib/weather";

function valueSummary(observation: WeatherObservation): string {
  const parts: string[] = [];
  if (observation.rainfall_mm !== null) {
    parts.push(`${observation.rainfall_mm} mm rain`);
  }
  if (observation.rainfall_probability_percent !== null) {
    parts.push(`${observation.rainfall_probability_percent}% chance of rain`);
  }
  if (observation.ambient_temperature_c !== null) {
    parts.push(`${observation.ambient_temperature_c} °C air`);
  }
  if (observation.humidity_percent !== null) {
    parts.push(`${observation.humidity_percent}% RH`);
  }
  return parts.join(" · ");
}

/**
 * Every stored weather record for the pan, newest first.
 *
 * Forecasts and observations are interleaved in one history because comparing
 * them is the point, and each row is labelled with which it is. Rows show only
 * what a record actually contains, so a sparse record looks sparse instead of
 * being padded with values from its neighbours.
 */
export default function WeatherHistory({
  observations,
}: {
  observations: readonly WeatherObservation[];
}) {
  if (observations.length === 0) {
    return (
      <EmptyState
        title="No weather recorded"
        description="Weather recorded for this pan will be listed here with its source on every row, so any value in the research record can be traced to the person or service that supplied it."
      />
    );
  }

  return (
    <ul className="divide-y divide-line">
      {observations.map((observation) => (
        <li key={observation.weather_id} className="px-5 py-4">
          <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
            <div className="min-w-0">
              <p className="tabular text-[13px] text-ink">
                {formatFullTimestamp(observation.timestamp)}
              </p>
              {observation.is_forecast && observation.forecast_for ? (
                <p className="tabular mt-1 text-xs text-caution">
                  Forecast for {formatFullTimestamp(observation.forecast_for)}
                </p>
              ) : null}
            </div>
            <div className="flex items-center gap-2">
              {observation.is_forecast ? (
                <Badge tone="caution">Forecast</Badge>
              ) : (
                <Badge tone="inactive">Observed</Badge>
              )}
              <WeatherSourceBadge source={observation.source} />
            </div>
          </div>
          <p className="tabular mt-2 text-[13px] text-ink-soft">
            {valueSummary(observation) || "No values in this record."}
          </p>
        </li>
      ))}
    </ul>
  );
}
