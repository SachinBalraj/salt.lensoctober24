"use client";

import { DataPoint, Eyebrow } from "@/components/ui/Primitives";
import { Alert } from "@/components/ui/Feedback";
import { isDemonstrationSource } from "@/lib/demonstration";
import { MEASUREMENT_RANGES, describeSource, formatFullTimestamp } from "@/lib/observations";
import type { LatestObservation } from "@/lib/pans";

/**
 * The pan's current observed state, copied from its newest reading.
 *
 * When no reading exists the card says so. It does not fall back to the pan's
 * declared design parameters, because a design value is an input and a reading
 * is evidence; presenting one as the other would misrepresent the pan.
 *
 * The same reasoning applies one level up: if the newest reading is a
 * demonstration, the card is not showing this pan's observed state at all, and
 * says so in a banner rather than only in the provenance line at the foot. The
 * figures are rendered identically in both cases on purpose — restyling them
 * would imply a measurement was missing rather than substituted, and a reader
 * would look for the number that ought to be there.
 */
export default function LatestObservationCard({
  latest,
}: {
  latest: LatestObservation | null;
}) {
  if (!latest) {
    return (
      <div className="p-5">
        <p className="text-[13px] leading-relaxed text-muted">
          No observation has been recorded for this pan yet. Its current
          condition is unknown: the values in{" "}
          <span className="text-ink-soft">Registered parameters</span> are
          declared design inputs, not measurements.
        </p>
      </div>
    );
  }

  const demonstration = isDemonstrationSource(latest.data_source);

  return (
    <div>
      {demonstration ? (
        <div className="px-5 pt-5">
          <Alert tone="demo" title="This is a demonstration, not a measurement">
            <p>
              The newest reading on this pan was typed in to show the workflow.
              The figures below are real records in the database, and no
              research dataset is built from them.
            </p>
          </Alert>
        </div>
      ) : null}
      <dl className="grid grid-cols-2 gap-x-5 gap-y-5 p-5">
        {(Object.keys(MEASUREMENT_RANGES) as Array<
          keyof typeof MEASUREMENT_RANGES
        >).map((key) => (
          <DataPoint
            key={key}
            label={MEASUREMENT_RANGES[key].label}
            value={
              latest[key] === null ? "—" : String(latest[key])
            }
            unit={latest[key] === null ? undefined : MEASUREMENT_RANGES[key].unit}
          />
        ))}
      </dl>
      <div className="border-t border-line px-5 py-3">
        <Eyebrow>Newest reading</Eyebrow>
        <p className="tabular mt-1.5 text-[13px] text-ink">
          {formatFullTimestamp(latest.timestamp)}{" "}
          <span className="text-muted">· {describeSource(latest.data_source)}</span>
        </p>
        <p className="mt-1.5 text-[11px] leading-relaxed text-muted">
          This is a copy of the most recent reading, not an average. Readings
          recorded with an earlier timestamp are kept in the history below and
          do not change it.
          {demonstration
            ? " A demonstration still becomes the newest reading, so recording one moves this card — it just does not make the pan any better known."
            : ""}
        </p>
      </div>
    </div>
  );
}
