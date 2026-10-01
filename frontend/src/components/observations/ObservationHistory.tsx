"use client";

import { Badge, type TONE_STYLES } from "@/components/ui/Primitives";
import { EmptyState } from "@/components/ui/Feedback";
import {
  DATA_SOURCE_LABELS,
  describeSource,
  formatFullTimestamp,
  type DataSource,
  type PanObservation,
} from "@/lib/observations";

/**
 * Badge tone per provenance.
 *
 * `demonstration` gets the project's demonstration tone rather than a severity,
 * because a demonstration is not a warning about a record — it is a statement
 * about what the record is. Filing it as `caution` would suggest a measurement
 * had gone wrong, and filing it as `active` would file it alongside the real
 * readings it is the whole point of not being confused with.
 */
const SOURCE_TONE: Record<DataSource, keyof typeof TONE_STYLES> = {
  manual: "active",
  sensor: "neutral",
  import: "neutral",
  demonstration: "demo",
};

function measurementSummary(observation: PanObservation): string {
  const parts: string[] = [];
  if (observation.salinity_g_l !== null) {
    parts.push(`${observation.salinity_g_l} g/L salinity`);
  }
  if (observation.water_depth_cm !== null) {
    parts.push(`${observation.water_depth_cm} cm deep`);
  }
  if (observation.brine_temperature_c !== null) {
    parts.push(`${observation.brine_temperature_c} °C brine`);
  }
  if (observation.humidity_percent !== null) {
    parts.push(`${observation.humidity_percent}% RH`);
  }
  if (observation.ambient_temperature_c !== null) {
    parts.push(`${observation.ambient_temperature_c} °C air`);
  }
  return parts.join(" · ");
}

/**
 * Every stored reading for the pan, newest first.
 *
 * Rows show only what each reading actually contains, so a sparse reading
 * looks sparse instead of being padded with values from its neighbours.
 */
export default function ObservationHistory({
  observations,
}: {
  observations: readonly PanObservation[];
}) {
  if (observations.length === 0) {
    return (
      <EmptyState
        title="No observations recorded"
        description="Readings recorded for this pan will be listed here with their provenance, so any entry in the record can be traced to how it was captured."
      />
    );
  }

  return (
    <ul className="divide-y divide-line">
      {observations.map((observation) => (
        <li key={observation.observation_id} className="px-5 py-4">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1.5">
            <p className="tabular text-[13px] text-ink">
              {formatFullTimestamp(observation.timestamp)}
            </p>
            <Badge tone={SOURCE_TONE[observation.data_source]}>
              {describeSource(observation.data_source) ??
                DATA_SOURCE_LABELS[observation.data_source]}
            </Badge>
          </div>
          <p className="tabular mt-1.5 text-[13px] text-ink-soft">
            {measurementSummary(observation) || "No measurements in this reading."}
          </p>
          {observation.note ? (
            <p className="mt-1.5 text-xs leading-relaxed text-muted">
              {observation.note}
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
