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
  CHARTED_WEATHER_METRICS,
  buildWeatherChartSeries,
  demonstrationWeatherSeriesWarning,
  describeWeatherSource,
  formatFullTimestamp,
  needsTwoWeatherRecords,
  type WeatherChartPoint,
  type WeatherChartSeries,
  type WeatherObservation,
} from "@/lib/weather";

/**
 * The four charted quantities share one x domain so the panels can be read
 * against each other, and one tooltip shape so a gap looks the same wherever it
 * appears.
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
  payload?: Array<{ payload: WeatherChartPoint }>;
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
        <p className="mt-1 text-muted">Not recorded at this entry</p>
      ) : (
        <p className="tabular mt-1 font-semibold text-ink">
          {point.value} {unit}
        </p>
      )}
      <p className="mt-1 text-muted">
        {point.is_forecast ? "Forecast" : "Observed"} ·{" "}
        {describeWeatherSource(point.source)}
      </p>
      {point.is_forecast && point.forecast_for ? (
        <p className="mt-1 font-mono text-muted">
          for {formatFullTimestamp(point.forecast_for)}
        </p>
      ) : null}
    </div>
  );
}

type DotProps = { cx?: number; cy?: number; payload?: WeatherChartPoint };

/** Recharts hands over the dot's own geometry with the record in `payload`. */
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

/**
 * A demonstration is drawn hollow, a real record solid.
 *
 * The same reasoning as the observation chart, and the same reason the line
 * keeps one colour: a demonstration belongs to the series and dropping it would
 * change the trend a reader sees. Only the point is marked, so that a typed-in
 * rainfall figure is distinguishable from one a service sent before any tooltip
 * is opened.
 */
function SeriesPanel({
  series,
  domain,
}: {
  series: WeatherChartSeries;
  domain: [number, number] | undefined;
}) {
  const sparse = needsTwoWeatherRecords(series);
  const warning = demonstrationWeatherSeriesWarning(series);

  return (
    <div className="min-w-0">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-[13px] font-semibold text-ink-soft">{series.label}</h3>
        <span className="tabular text-[11px] text-muted">
          {series.measuredCount} of {series.recordCount}{" "}
          {series.recordCount === 1 ? "record" : "records"}
        </span>
      </div>
      {warning ? (
        <p className="mt-1.5 text-[11px] leading-relaxed text-demo">{warning}</p>
      ) : null}

      {sparse ? (
        <div className="mt-2 flex h-32 items-center rounded-lg border border-dashed border-line bg-inset/50 px-3">
          <p className="text-[12px] leading-relaxed text-muted">
            {series.recordCount === 0
              ? "No records yet."
              : series.measuredCount === 0
                ? `No record holds ${series.label.toLowerCase()}.`
                : `Only ${series.measuredCount} record holds ${series.label.toLowerCase()}. A trend needs at least two.`}
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
 * The four charted quantities as small multiples.
 *
 * Gaps are preserved: every record occupies an x position even when it did not
 * hold that quantity, and `connectNulls` is off, so an absent value breaks the
 * line instead of being bridged. Nothing is averaged, smoothed or extrapolated,
 * and a quantity with fewer than two records shows why rather than a
 * misleading flat line.
 */
export default function WeatherChart({
  observations,
}: {
  observations: readonly WeatherObservation[];
}) {
  const series = CHARTED_WEATHER_METRICS.map((key) =>
    buildWeatherChartSeries(observations, key),
  );

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
