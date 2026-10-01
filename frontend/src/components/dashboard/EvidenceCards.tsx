import { Badge } from "@/components/ui/Primitives";
import { ReadingGrid, ProvenanceCard } from "@/components/dashboard/Provenance";
import { conditionReadings, describeTwinCurrency, twinSources, weatherReadings } from "@/lib/dashboard";
import { isTwinCurrent, type DigitalTwinState } from "@/lib/digitalTwin";
import { formatDateTime } from "@/lib/pans";

/**
 * The three cards that carry what is *known* about a pan: its measured condition,
 * what the weather is expected to do, and the record both were read from.
 *
 * They are kept as three cards rather than one, because they are three different
 * claims with three different ages. A salinity read this morning, a forecast
 * issued two hours ago and the state assembled from them are not the same kind of
 * fact, and a panel that put them in one list would be inviting a reader to
 * compare them. Each card is tagged with what produced it, and the differences
 * between the cards are the differences between their categories.
 */

export function ConditionCard({ twin }: { twin: DigitalTwinState }) {
  return (
    <ProvenanceCard
      kind="observed"
      title="Current pan condition"
      description="Measured readings on record for this pan. Humidity comes from the site's weather observation, not the pan."
    >
      <ReadingGrid readings={conditionReadings(twin)} columns={2} />
    </ProvenanceCard>
  );
}

export function WeatherCard({ twin }: { twin: DigitalTwinState }) {
  return (
    <ProvenanceCard
      kind="forecast"
      title="Weather forecast"
      description="What the weather service expects. None of this has fallen yet, and none of it has been measured."
    >
      <ReadingGrid readings={weatherReadings(twin)} columns={2} />
    </ProvenanceCard>
  );
}

/**
 * The digital twin, and the records behind it.
 *
 * Tagged `observed` because the state it describes is a record — but the card's
 * real content is provenance: which stored records were read, how old each one is,
 * and what is missing. An operator deciding whether to trust a salinity needs to
 * know the reading behind it is a day old, and that is the fact this card exists
 * to put on screen.
 */
export function TwinStateCard({ twin }: { twin: DigitalTwinState }) {
  const sources = twinSources(twin);
  const currency = describeTwinCurrency(twin);
  const complete = isTwinCurrent(twin);

  return (
    <ProvenanceCard
      kind="observed"
      title="Digital twin"
      description="The state every other card on this screen was resolved against."
      actions={
        <Badge tone={complete ? "active" : "caution"}>
          {complete ? "Current" : "Check age"}
        </Badge>
      }
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-lg border border-line bg-inset px-3.5 py-3">
          <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-faint">
            Assembled from
          </p>
          <p className="mt-1 text-sm font-semibold capitalize text-ink">
            {twin.provenance}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-inset px-3.5 py-3">
          <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-faint">
            Last updated
          </p>
          <p className="mt-1 text-sm font-semibold text-ink">
            {formatDateTime(twin.timestamp)}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-inset px-3.5 py-3">
          <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-faint">
            Twin version
          </p>
          <p className="mt-1 font-mono text-sm font-semibold text-ink">
            {twin.twin_version}
          </p>
        </div>
      </div>

      <div className="mt-4">
        <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-faint">
          Data sources
        </p>
        <ul className="mt-2 space-y-1.5">
          {sources.map((source) => (
            <li
              key={source.kind}
              data-source={source.kind}
              className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 rounded-lg border border-line bg-inset px-3.5 py-2"
            >
              <div className="min-w-0">
                <p className="text-[12px] font-medium text-ink">{source.label}</p>
                <p className="text-[10px] text-faint">
                  {source.sourceLabel}
                  {source.id ? ` · ${source.id}` : ""}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <span className="text-[10px] text-muted">
                  {source.formatted} · {source.age}
                </span>
                <Badge tone={source.freshness.tone}>{source.freshness.label}</Badge>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <p className="mt-3 text-[11px] leading-relaxed text-muted">{currency.headline}</p>

      {twin.missing_inputs.length > 0 ? (
        <div className="mt-3 rounded-lg border border-caution-line bg-caution-soft px-3.5 py-2.5">
          <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-caution">
            {twin.missing_inputs.length}{" "}
            {twin.missing_inputs.length === 1 ? "quantity is" : "quantities are"}{" "}
            unavailable
          </p>
          <ul className="mt-1 space-y-0.5">
            {twin.missing_inputs.map((entry) => (
              <li key={entry.field} className="text-[11px] leading-relaxed text-ink-soft">
                <span className="font-mono">{entry.field}</span> — {entry.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </ProvenanceCard>
  );
}
