"use client";

import { Badge, DataPoint, Eyebrow } from "@/components/ui/Primitives";
import {
  buildQuantityViews,
  describeFreshness,
  describeTwinSource,
  formatAge,
  formatTwinTimestamp,
  type DigitalTwinState,
  type TwinInput,
  type TwinInputKind,
} from "@/lib/digitalTwin";

/**
 * One contributing record, with its own age and provenance.
 *
 * The age is the point of this block. A twin is reassembled on every read, so its
 * own timestamp is always seconds old and says nothing about the pan. Showing
 * "2 days ago" next to the observation is what stops a stale value from reading
 * as a current one.
 */
function InputRow({
  kind,
  input,
}: {
  kind: TwinInputKind;
  input: TwinInput | null;
}) {
  const freshness = describeFreshness(input?.age_seconds ?? null);

  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1.5">
      <div className="min-w-0">
        <Eyebrow>
          {kind === "observation"
            ? "Pan observation"
            : kind === "weather"
              ? "Weather observation"
              : "Weather forecast"}
        </Eyebrow>
        {input ? (
          <>
            <p className="tabular mt-1 text-[13px] text-ink">
              {formatAge(input.age_seconds)}
            </p>
            <p className="tabular mt-0.5 text-[11px] text-muted">
              Recorded {formatTwinTimestamp(input.timestamp)}
            </p>
            <p className="mt-0.5 text-[11px] text-muted">
              Source: {describeTwinSource(kind, input.source)}
            </p>
          </>
        ) : (
          <p className="mt-1 text-[13px] leading-relaxed text-muted">
            No {kind === "observation" ? "observation" : kind === "weather" ? "weather observation" : "weather forecast"}{" "}
            has been recorded for this pan. Nothing is substituted in its place.
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Badge tone={freshness.tone}>{freshness.label}</Badge>
      </div>
    </div>
  );
}

/**
 * The pan's current digital twin.
 *
 * Every figure is shown with the record it came from, and a quantity the server
 * could not source is rendered as an explicit "Unavailable" with the reason
 * attached rather than as a dash. A dash next to a number would leave a reader
 * guessing whether the pan is at zero or simply unmeasured, and those two facts
 * call for opposite responses.
 */
export default function TwinStateCard({
  twin,
  isDemo = false,
}: {
  twin: DigitalTwinState;
  isDemo?: boolean;
}) {
  const views = buildQuantityViews(twin);
  const availability = twin.missing_inputs.length;

  return (
    <div>
      <dl className="grid grid-cols-2 gap-x-5 gap-y-5 p-5 sm:grid-cols-3">
        {views.map((view) => (
          <div key={view.field} className="min-w-0">
            <dt className="text-[11px] font-medium uppercase tracking-wide text-faint">
              {view.label}
            </dt>
            <dd className="mt-1">
              {view.available ? (
                <span className="tabular text-sm text-ink">
                  {view.display}
                  {view.unit ? (
                    <span className="ml-1 text-xs font-normal text-muted">
                      {view.unit}
                    </span>
                  ) : null}
                </span>
              ) : (
                <>
                  <span className="text-sm text-faint">Unavailable</span>
                  <span className="mt-0.5 block text-[11px] leading-snug text-muted">
                    {view.reason}
                  </span>
                </>
              )}
            </dd>
          </div>
        ))}
      </dl>

      <div className="border-t border-line px-5 py-4">
        <Eyebrow>Pan properties</Eyebrow>
        <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-3">
          <DataPoint
            label="Pan area"
            value={twin.pan_area_m2 === null ? "—" : String(twin.pan_area_m2)}
            unit={twin.pan_area_m2 === null ? undefined : "m²"}
          />
          <DataPoint label="Location" value={twin.location ?? "—"} />
          <DataPoint label="Twin version" value={twin.twin_version} mono />
        </dl>
      </div>

      <div className="space-y-4 border-t border-line px-5 py-4">
        <Eyebrow>Evidence this state is built from</Eyebrow>
        <InputRow kind="observation" input={twin.observation} />
        <InputRow kind="weather" input={twin.weather} />
        <InputRow kind="weather_forecast" input={twin.weather_forecast} />
      </div>

      <div className="border-t border-line px-5 py-4">
        <Eyebrow>Last updated</Eyebrow>
        <p className="tabular mt-1.5 text-[13px] text-ink">
          {formatTwinTimestamp(twin.timestamp)}
        </p>
        <p className="mt-1 text-[11px] leading-relaxed text-muted">
          This is when the state was assembled, not how old the readings behind it
          are. The ages above describe the evidence.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Badge tone={availability === 0 ? "active" : "caution"}>
            {availability === 0
              ? "Complete"
              : `${availability} ${availability === 1 ? "quantity" : "quantities"} unavailable`}
          </Badge>
          {isDemo ? <Badge tone="demo">Demo — not research data</Badge> : null}
        </div>
        <p className="mt-2.5 text-[11px] leading-relaxed text-muted">
          Each value is copied from the newest record that supplies it. Nothing is
          averaged, interpolated or carried forward from an older reading, so a
          quantity the newest record omits is reported as unavailable rather than
          filled in.
        </p>
      </div>
    </div>
  );
}
