"use client";

import { EmptyState } from "@/components/ui/Feedback";
import { Badge } from "@/components/ui/Primitives";
import {
  describeFreshness,
  formatTwinTimestamp,
  oldestInputAge,
  type DigitalTwinState,
} from "@/lib/digitalTwin";

/**
 * Stored twin snapshots, newest first.
 *
 * Each row records what the twin looked like at the moment it was taken,
 * including which record it was built from, so a later analysis of how the pan
 * moved does not have to re-derive that from the live tables — which will have
 * moved on by then.
 */
export default function TwinSnapshotTable({
  snapshots,
}: {
  snapshots: readonly DigitalTwinState[];
}) {
  if (snapshots.length === 0) {
    return (
      <EmptyState
        title="No snapshots stored"
        description="Reading the twin does not store a snapshot. Use “Store snapshot” to record the current state, so the sequence of states can be analysed later."
      />
    );
  }

  return (
    <ul className="divide-y divide-line">
      {snapshots.map((snapshot) => {
        const age = oldestInputAge(snapshot);
        const freshness = describeFreshness(age);
        return (
          <li key={snapshot.twin_id} className="px-5 py-4">
            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
              <div className="min-w-0">
                <p className="tabular text-[13px] text-ink">
                  {formatTwinTimestamp(snapshot.timestamp)}
                </p>
                <p className="mt-1 font-mono text-[11px] text-faint">
                  {snapshot.twin_id}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={snapshot.is_complete ? "active" : "caution"}>
                  {snapshot.is_complete
                    ? "Complete"
                    : `${snapshot.missing_fields.length} unavailable`}
                </Badge>
                <Badge tone={freshness.tone}>
                  {age === null ? "No evidence" : `Evidence ${freshness.label.toLowerCase()}`}
                </Badge>
              </div>
            </div>
            <p className="tabular mt-2 text-[13px] text-ink-soft">
              {snapshot.salinity_g_l === null
                ? "Salinity unavailable"
                : `Salinity ${snapshot.salinity_g_l} g/L`}
              {" · "}
              {snapshot.water_depth_cm === null
                ? "Depth unavailable"
                : `Depth ${snapshot.water_depth_cm} cm`}
            </p>
            <p className="mt-1 text-[11px] text-muted">
              Built from observation{" "}
              <span className="font-mono">{snapshot.observation?.id ?? "none"}</span>
            </p>
          </li>
        );
      })}
    </ul>
  );
}
