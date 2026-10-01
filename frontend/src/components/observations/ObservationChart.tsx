"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  CHARTED_METRICS,
  buildChartSeries,
  demonstrationSeriesWarning,
  describeSource,
  formatFullTimestamp,
  needsTwoReadings,
  type ChartPoint,
  type ChartSeries,
  type PanObservation,
} from "@/lib/observations";

/**
 * The four charted quantities share one x domain so the panels can be read
 * against each other, and one tooltip shape so a gap looks the same wherever
 * it appears.
 */

function axisTick(value: number): string {
  const date = new Date(value);
  return new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZone: "UTC",
  }).format(date);
}

function TooltipContent({
  active,
  payload,
  unit,
}: {
  active?: boolean;
  payload?: Array<{ payload: ChartPoint }>;
  unit: string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-[12px] shadow-sm">
      <p className="font-mono text-muted">
        {formatFullTimestamp(new Date(point.at).toISOString())}
      </p>
      {point.value === null ? (
        <p className="mt-1 text-muted">Not measured at this reading</p>
      ) : (
        <p className="tabular mt-1 font-semibold text-ink">
          {point.value} {unit}
        </p>
      )}
      <p className="mt-1 text-muted">{describeSource(point.source)}</p>
      {point.note ? (
        <p className="mt-1 max-w-56 text-muted">{point.note}</p>
      ) : null}
    </div>
  );
}

/**
 * Draws a demonstration hollow and a measurement solid.
 *
 * The line itself is one colour either way, because a demonstration is part of
 * the same series and removing it would change the shape a reader sees. What
 * changes is the point: a hollow ring reads as "not filled in" at a glance,
 * before any tooltip is opened, and it is the only cue on the chart that a
 * figure was typed rather than measured. The demonstration colour is kept off
 * the stroke of the line so the trend stays a trend and only the point is
 * called out.
 */
type DotProps = { cx?: number; cy?: number; payload?: ChartPoint };

/** Recharts hands over the dot's own geometry with the reading in `payload`. */
function renderDot({ cx = 0, cy = 0, payload }: DotProps) {
  if (!payload || payload.value === null) {
    return <circle cx={cx} cy={cy} r={0} fill="none" stroke="none" />;
  }
  if (payload.demonstration) {
    return (
      <circle
        cx={cx}
        cy={cy}
        r={3.5}
        fill="var(--color-surface)"
        stroke="var(--color-demo)"
        strokeWidth={2}
      />
    );
  }
  return <circle cx={cx} cy={cy} r={2.5} fill="var(--color-accent)" />;
}

function renderActiveDot({ cx = 0, cy = 0, payload }: DotProps) {
  if (!payload || payload.value === null) {
    return <circle cx={cx} cy={cy} r={0} fill="none" stroke="none" />;
  }
  return payload.demonstration ? (
    <circle
      cx={cx}
      cy={cy}
      r={5}
      fill="var(--color-surface)"
      stroke="var(--color-demo)"
      strokeWidth={2.5}
    />
  ) : (
    <circle cx={cx} cy={cy} r={4} fill="var(--color-accent)" />
  );
}

function SeriesPanel({
  series,
  domain,
}: {
  series: ChartSeries;
  domain: [number, number] | undefined;
}) {
  const sparse = needsTwoReadings(series);
  const warning = demonstrationSeriesWarning(series);

  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-[13px] font-semibold text-ink-soft">{series.label}</h3>
        <span className="tabular text-[11px] text-muted">
          {series.measuredCount} of {series.readingCount}{" "}
          {series.readingCount === 1 ? "reading" : "readings"}
        </span>
      </div>
      {warning ? (
        <p className="mt-1.5 text-[11px] leading-relaxed text-demo">{warning}</p>
      ) : null}

      {sparse ? (
        <div className="mt-2 flex h-32 items-center rounded-lg border border-dashed border-line bg-inset/50 px-3">
          <p className="text-[12px] leading-relaxed text-muted">
            {series.readingCount === 0
              ? "No readings yet."
              : series.measuredCount === 0
                ? `No reading records ${series.label.toLowerCase()}.`
                : `Only ${series.measuredCount} reading records ${series.label.toLowerCase()}. A trend needs at least two.`}
          </p>
        </div>
      ) : (
        <div className="mt-2 h-32 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={series.points}
              margin={{ top: 6, right: 10, bottom: 0, left: -22 }}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-line)" />
              <XAxis
                dataKey="at"
                type="number"
                scale="time"
                domain={domain}
                tickFormatter={axisTick}
                tick={{ fontSize: 10 }}
                tickLine={false}
                axisLine={{ stroke: "var(--color-line)" }}
                minTickGap={16}
              />
              <YAxis
                tick={{ fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                width={46}
                unit={` ${series.unit}`}
              />
              <Tooltip
                content={<TooltipContent unit={series.unit} />}
                cursor={{ stroke: "var(--color-line-strong)" }}
              />
              <Line
                type="linear"
                dataKey="value"
                stroke="var(--color-accent)"
                strokeWidth={2}
                dot={renderDot}
                activeDot={renderActiveDot}
                connectNulls={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

/**
 * Renders the four charted quantities as small multiples.
 *
 * Gaps are preserved: every reading occupies an x position even when it did
 * not measure the quantity, and `connectNulls` is off, so an unmeasured value
 * breaks the line instead of being bridged. Nothing is averaged, smoothed or
 * extrapolated, and a quantity with fewer than two readings shows why rather
 * than a misleading flat line.
 */
export default function ObservationChart({
  observations,
}: {
  observations: readonly PanObservation[];
}) {
  const series = CHARTED_METRICS.map((key) => buildChartSeries(observations, key));

  const timestamps = observations
    .map((observation) => new Date(observation.timestamp).getTime())
    .filter((value) => !Number.isNaN(value));
  const domain: [number, number] | undefined =
    timestamps.length > 0
      ? [Math.min(...timestamps), Math.max(...timestamps)]
      : undefined;

  return (
    <div className="grid gap-x-6 gap-y-7 p-5 sm:grid-cols-2">
      {series.map((entry) => (
        <SeriesPanel key={entry.key} series={entry} domain={domain} />
      ))}
    </div>
  );
}
