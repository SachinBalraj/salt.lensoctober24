import type { ReactNode } from "react";
import { PROVENANCE, PROVENANCE_ORDER, type DashboardReading, type ProvenanceKind } from "@/lib/dashboard";

/**
 * The dashboard's provenance system: the tag that names a category, the card that
 * wears it, and the legend that teaches all six.
 *
 * The separation this exists to enforce is the reason a salt-pan dashboard is
 * hard. An observed salinity, a forecast rainfall, a simulated salinity and a
 * model's probability all carry units, all look authoritative, and all point at
 * the same pan. A reader who sees them without knowing which is which will take
 * the one they trust to be the other — and the mistake is not recoverable from the
 * screen, because every one of the four is a real, correct number.
 *
 * So the category is stated, not implied. Every card declares which kind of
 * number it holds, in a word, in a colour, and in a shape. Three channels rather
 * than one, because colour alone fails for a reader who cannot distinguish them,
 * on a bad projector, or in a printed screenshot of an academic demo.
 *
 * The colours are deliberately not the status scale. `danger` and `caution` mean
 * severity, and a forecast is not severe; a simulation is never "safe". Lending a
 * status colour to a provenance would put a severity on a figure that has none.
 */

/**
 * Names a category. The word is the point — the colour is the second cue.
 *
 * A square dot rather than a round one, and a squarer container than the status
 * `Badge` uses, so a provenance pill and a severity pill are not confusable at a
 * glance even before the words are read.
 */
export function ProvenanceTag({
  kind,
  emphasis = false,
}: {
  kind: ProvenanceKind;
  /** For a card header, where the tag is the card's headline claim. */
  emphasis?: boolean;
}) {
  const provenance = PROVENANCE[kind];
  return (
    <span
      title={provenance.meaning}
      data-provenance={kind}
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-md border font-semibold uppercase tracking-[0.1em] ${
        emphasis ? "px-2.5 py-1 text-[10px]" : "px-2 py-0.5 text-[10px]"
      }`}
      style={{
        color: provenance.color,
        backgroundColor: provenance.soft,
        borderColor: provenance.line,
      }}
    >
      <span
        aria-hidden
        className="h-1.5 w-1.5 shrink-0 rounded-[1px]"
        style={{ backgroundColor: provenance.color }}
      />
      {provenance.label}
    </span>
  );
}

/**
 * A card that holds exactly one category of number, and says so on its face.
 *
 * The coloured rail runs down the left edge and the tag sits in the header, so a
 * reader scanning the grid sees the category before they read any figure. The two
 * together are deliberate: the rail is findable from across a room and in a
 * thumbnail, the tag is legible up close and carries the word.
 */
export function ProvenanceCard({
  kind,
  title,
  description,
  actions,
  children,
  className = "",
  bodyClassName = "px-5 py-4",
}: {
  kind: ProvenanceKind;
  title: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  const provenance = PROVENANCE[kind];
  return (
    <section
      data-provenance={kind}
      className={`overflow-hidden rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(15,23,42,0.04)] ${className}`}
      style={{ borderLeftWidth: 3, borderLeftColor: provenance.color }}
    >
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            <ProvenanceTag kind={kind} />
          </div>
          {description ? (
            <p className="mt-1 text-xs leading-relaxed text-muted">{description}</p>
          ) : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

/**
 * The six categories, in the order they are earned.
 *
 * Record, then a forecast about it, then a calculation over both, then a model,
 * then a rule, then the decision a person records in answer to the rule. Teaching
 * the order teaches the hierarchy: each step is a weaker or more inferential claim
 * than the one before, and a reader who has internalised the order knows how much
 * to trust a number before they have read it. The operator decision belongs at the
 * end because it is not a figure at all — it is a recorded act in answer to the
 * rule — and listing it with the figures would suggest it is comparable to them.
 */
export function ProvenanceLegend() {
  return (
    <section
      aria-label="What each kind of number on this screen means"
      className="rounded-xl border border-line bg-surface px-5 py-4"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-ink">
          Every figure on this screen says what produced it
        </h2>
        <p className="text-xs text-muted">
          These six are never mixed. A computed salinity is not a measured one, and
          a recorded decision is not a figure.
        </p>
      </div>
      <ol className="mt-3 grid gap-x-6 gap-y-2 sm:grid-cols-2 xl:grid-cols-3">
        {PROVENANCE_ORDER.map((kind) => {
          const provenance = PROVENANCE[kind];
          return (
            <li key={kind} className="flex min-w-0 items-start gap-2.5">
              <ProvenanceTag kind={kind} />
              <p className="min-w-0 text-[11px] leading-relaxed text-muted">
                {provenance.meaning}
              </p>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/**
 * One figure, on a card that has already declared its category.
 *
 * A figure that could not be sourced keeps its place and states why. Dropping the
 * tile would turn "we do not know the brine temperature" into a card that looks
 * complete, which is the same failure as rendering the gap as a zero — a reader
 * would take the absence of a warning as a reading.
 */
export function ReadingTile({ reading }: { reading: DashboardReading }) {
  return (
    <div
      data-reading={reading.key}
      className="min-w-0 rounded-lg border border-line bg-inset px-3.5 py-3"
    >
      <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-faint">
        {reading.label}
      </p>
      {reading.available ? (
        <>
          <p className="tabular mt-1 text-xl font-semibold text-ink">
            {reading.value}
            {reading.unit ? (
              <span className="ml-1 text-xs font-normal text-muted">{reading.unit}</span>
            ) : null}
          </p>
          {reading.fromLabel ? (
            <p className="mt-1 text-[10px] text-faint">
              {reading.fromLabel}
              {reading.fromTimestamp ? (
                <>
                  {" · "}
                  {formatShortTime(reading.fromTimestamp)}
                </>
              ) : null}
            </p>
          ) : null}
        </>
      ) : (
        <>
          <p className="mt-1 text-sm font-medium text-muted">Unavailable</p>
          <p className="mt-1 text-[10px] leading-relaxed text-faint">{reading.value}</p>
        </>
      )}
    </div>
  );
}

/** Readings laid out as a responsive tile grid. */
export function ReadingGrid({
  readings,
  columns = 2,
}: {
  readings: DashboardReading[];
  columns?: 2 | 3 | 4;
}) {
  const grid =
    columns === 4
      ? "grid-cols-2 xl:grid-cols-4"
      : columns === 3
        ? "grid-cols-1 sm:grid-cols-3"
        : "grid-cols-1 sm:grid-cols-2";
  return (
    <div className={`grid gap-3 ${grid}`}>
      {readings.map((reading) => (
        <ReadingTile key={reading.key} reading={reading} />
      ))}
    </div>
  );
}

const SHORT_TIME = new Intl.DateTimeFormat("en-GB", {
  dateStyle: "short",
  timeStyle: "short",
  timeZone: "UTC",
});

/** A timestamp for a tile's footnote, where the full form would crowd it out. */
function formatShortTime(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `${SHORT_TIME.format(parsed)} UTC`;
}
