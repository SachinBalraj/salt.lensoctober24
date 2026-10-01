"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  COMPARISON_COLUMNS,
  buildChartSeries,
  columnTitle,
  columnCoverage,
  formatReading,
  readingTone,
  type ChartPoint,
  type ComparisonColumn,
  type ComparisonResponse,
} from "@/lib/evaluation";
import { Card, CardHeader } from "@/components/ui/Primitives";

/**
 * A score is between 0 and 1, so the axis is pinned to that range.
 *
 * Not left to auto-scale. An auto-scaled axis on four bars would start at whatever
 * the lowest bar happened to be, which exaggerates the gap between candidates and
 * invites the reading that one model is several times better than another. A score
 * chart that does not start at zero and end at one is not a score chart.
 */
const SCORE_DOMAIN: [number, number] = [0, 1];

const TICK_FORMAT = (value: number) => value.toFixed(1);

/**
 * The bar's fill.
 *
 * Three colours for three states, and the absent one is not a colour at all --
 * see {@link AbsentMarker} for why a zero-height bar is not used.
 */
const FILL = {
  measured: "var(--color-accent, #2563eb)",
  convention: "var(--color-caution, #b45309)",
} as const;

/**
 * The shape drawn where a metric was never calculated.
 *
 * A ring with a dash through it, sitting on the baseline. The alternative -- a bar
 * of height zero -- is the specific thing this module must not draw, because a
 * zero-height bar is not obviously a zero: it reads as a gap, and a reader scanning
 * six bars takes a gap for a low score. A ring reads as a symbol, which is what it
 * is, and it is the only mark on the chart that means "nothing was measured here".
 */
function AbsentMarkerShape(props: {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  payload?: ChartPoint;
}) {
  const { x, y, width = 0, payload } = props;
  if (x === undefined || y === undefined) return null;
  const cx = x + width / 2;
  const cy = y;
  return (
    <g
      fill="none"
      stroke="var(--color-faint, #94a3b8)"
      strokeWidth={1.3}
      opacity={0.75}
    >
      <title>
        {payload?.label
          ? `${payload.label}: not calculated${payload.reason ? `. ${payload.reason}` : ""}`
          : "Not calculated"}
      </title>
      <circle cx={cx} cy={cy - 5} r={3.5} />
      <line x1={cx - 4} y1={cy - 5} x2={cx + 4} y2={cy - 5} />
    </g>
  );
}

/**
 * The fill for one bar.
 *
 * The selected candidate is drawn in the accent colour and the rest in the neutral
 * one, so "which row did the rule pick" is answerable at a glance without reading
 * the table. A figure that rests on a declared convention is drawn in the caution
 * colour instead, because the number is real and its meaning is narrower than the
 * number alone suggests -- the colour is the chart's way of saying so.
 */
function fillFor(point: ChartPoint, anySelected: boolean): string {
  if (point.absent) return "none";
  if (anySelected && point.selected) return "var(--color-active, #0f766e)";
  const tone = readingTone({
    value: point.value,
    computed: true,
    variant: null,
    reason: point.reason,
  });
  return tone === "convention" ? FILL.convention : FILL.measured;
}

function TooltipContent({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ payload: ChartPoint }>;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  return (
    <div className="max-w-xs rounded-lg border border-line bg-surface px-3 py-2 text-[12px] shadow-sm">
      <p className="font-semibold text-ink">{point.label}</p>
      {point.absent ? (
        <p className="mt-1 text-caution">Not calculated</p>
      ) : (
        <p className="tabular mt-1 text-ink">
          {point.value !== null ? point.value.toFixed(3) : "—"}
        </p>
      )}
      {point.selected ? (
        <p className="mt-1 text-accent">Selected by the stated rule</p>
      ) : null}
      {!point.autoSelectable ? (
        <p className="mt-0.5 text-muted">Fitted and scored, never auto-selected</p>
      ) : null}
      {point.reason ? (
        <p className="mt-1 leading-snug text-muted">{point.reason}</p>
      ) : null}
    </div>
  );
}

/**
 * One column of the table, as a chart.
 *
 * The six columns are drawn separately rather than as one grouped chart with six
 * series, for a reason that is about honesty rather than layout: each column has its
 * own coverage, and a reader who cannot see which rows are missing a column at a
 * glance is being asked to cross-read two places at once. One chart per column puts
 * the gaps in the same field of view as the bars.
 */
function ScoreChart({
  comparison,
  column,
}: {
  comparison: ComparisonResponse;
  column: ComparisonColumn;
}) {
  const series = buildChartSeries(comparison, column);
  const coverage = columnCoverage(comparison, column);
  const missing = coverage.total - coverage.present;
  const anySelected = series.some((point) => point.selected);

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2 px-5 pt-4">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
          {columnTitle(comparison, column)}
        </p>
        <p
          className={`tabular text-[11px] ${missing > 0 ? "text-caution" : "text-faint"}`}
        >
          {coverage.present} of {coverage.total} computed
        </p>
      </div>

      <div className="h-44 w-full px-2 pb-1 pt-3">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={series}
            margin={{ top: 4, right: 8, bottom: 4, left: -18 }}
          >
            <CartesianGrid strokeDasharray="2 4" stroke="var(--color-line, #e2e8f0)" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: "var(--color-muted, #64748b)" }}
              interval={0}
              angle={-18}
              textAnchor="end"
              height={44}
            />
            <YAxis
              domain={SCORE_DOMAIN}
              ticks={[0, 0.25, 0.5, 0.75, 1]}
              tickFormatter={TICK_FORMAT}
              tick={{ fontSize: 10, fill: "var(--color-faint, #94a3b8)" }}
              width={44}
            />
            <Tooltip
              cursor={{ fill: "var(--color-inset, #f8fafc)" }}
              content={<TooltipContent />}
            />
            <ReferenceLine y={1} stroke="var(--color-line, #e2e8f0)" />
            <Bar dataKey="value" isAnimationActive={false} radius={[3, 3, 0, 0]}>
              {series.map((point) =>
                point.value === null ? (
                  <Cell key={point.algorithm} fill="none" />
                ) : (
                  <Cell key={point.algorithm} fill={fillFor(point, anySelected)} />
                ),
              )}
              <LabelList
                dataKey="value"
                position="top"
                formatter={(value: unknown) =>
                  typeof value === "number" ? value.toFixed(2) : "—"
                }
                style={{ fontSize: 9, fill: "var(--color-faint, #94a3b8)" }}
              />
            </Bar>
            {/*
              The absent-metric marker, as a second series rather than as part of the
              first.

              A bar of height zero is the failure this whole module exists to avoid,
              so an uncomputed metric is never given one. It gets a hollow ring and a
              dash sitting on the baseline instead: something that is visibly *not* a
              bar, so a reader scanning the chart cannot mistake the gap for a low
              score. `marker` is 0 for an absent metric and null for a computed one,
              which is the inverse of `value` -- Recharts draws nothing for a null,
              so exactly the absent entries get the marker.
            */}
            <Bar
              dataKey="marker"
              isAnimationActive={false}
              cursor="pointer"
              shape={<AbsentMarkerShape />}
            />
          </BarChart>
        </ResponsiveContainer>
      </div>

      {missing > 0 ? (
        <p className="px-5 pb-4 text-[11px] leading-snug text-caution">
          A hollow marker on the baseline means the metric was not calculated for
          that candidate, which is not the same as a score of zero. Hover it for the
          reason.
        </p>
      ) : null}
    </div>
  );
}

/**
 * The six columns as six charts.
 *
 * The scale is identical across all six, deliberately. A reader comparing a
 * precision bar against a PR-AUC bar needs them on the same axis to be comparing at
 * all, and per-chart auto-scaling would make a metric that happened to score low
 * look as though it had failed.
 */
export default function ComparisonCharts({
  comparison,
}: {
  comparison: ComparisonResponse;
}) {
  const columns = (comparison.columns.length > 0
    ? comparison.columns
    : [...COMPARISON_COLUMNS]) as ComparisonColumn[];

  return (
    <Card>
      <CardHeader
        title="Every column, drawn"
        description="One chart per metric, all on the same 0–1 scale. Rings mark a metric that was not calculated."
      />
      <div className="grid gap-x-2 divide-y divide-line sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-3">
        {columns.map((column) => (
          <ScoreChart
            key={column}
            comparison={comparison}
            column={column}
          />
        ))}
      </div>
    </Card>
  );
}

/**
 * The per-class figures for one candidate, as a small table.
 *
 * Not a chart. Per-class precision and recall sit on the same 0–1 scale as the
 * headline figures, so a reader comparing them should be able to put them side by
 * side with the same precision, and a third of a dozen bars in a small panel makes
 * that harder rather than easier. The support column is not optional: precision on
 * a class with three rows in the holdout is a very different claim from precision
 * on a class with three thousand, and the number of rows is what tells them apart.
 */
export function PerClassTable({
  evaluation,
  title,
}: {
  evaluation: import("@/lib/evaluation").ClassificationEvaluation;
  title?: string;
}) {
  if (evaluation.per_class.length === 0) {
    return (
      <p className="px-5 py-4 text-[13px] text-muted">
        No per-class figures: the evaluation had no rows.
      </p>
    );
  }
  return (
    <div>
      {title ? (
        <p className="px-5 pt-4 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
          {title}
        </p>
      ) : null}
      <div className="overflow-x-auto px-5 pb-4 pt-3">
        <table className="w-full min-w-[34rem] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-line">
              {["Class", "Support", "Predicted", "Precision", "Recall", "F1", "Avg precision"].map(
                (heading) => (
                  <th
                    key={heading}
                    scope="col"
                    className="px-2 py-2 text-right text-[11px] font-semibold uppercase tracking-wide text-faint first:text-left"
                  >
                    {heading}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {evaluation.per_class.map((entry) => {
              const absent = entry.support === 0;
              return (
                <tr key={entry.label} className="border-b border-line last:border-0">
                  <th scope="row" className="px-2 py-2 text-left font-normal">
                    <span className="text-ink">{entry.label}</span>
                    {absent ? (
                      <span className="ml-1.5 text-[10px] uppercase tracking-wide text-faint">
                        no rows
                      </span>
                    ) : null}
                  </th>
                  <td className="tabular px-2 py-2 text-right text-ink-soft">
                    {entry.support}
                  </td>
                  <td className="tabular px-2 py-2 text-right text-ink-soft">
                    {entry.predicted}
                  </td>
                  {(
                    [
                      entry.precision,
                      entry.recall,
                      entry.f1,
                      entry.average_precision,
                    ] as const
                  ).map((reading, index) => (
                    <td
                      key={index}
                      className={`px-2 py-2 text-right ${
                        readingTone(reading) === "absent"
                          ? "tabular text-faint"
                          : "tabular text-ink"
                      }`}
                      title={reading.reason ?? undefined}
                    >
                      {/* The shared shaper, not a local one. A per-class cell that
                          formatted its own numbers would be free to disagree with a
                          headline cell about what a missing figure looks like, and
                          the two would sit on the same screen. */}
                      {formatReading(reading)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** The confusion matrix, drawn as a grid of counts. */
export function ConfusionMatrix({
  evaluation,
}: {
  evaluation: import("@/lib/evaluation").ClassificationEvaluation;
}) {
  const matrix = evaluation.confusion_matrix;
  if (!matrix) {
    return (
      <p className="px-5 py-4 text-[13px] leading-relaxed text-muted">
        No confusion matrix. It is left out rather than shown as an empty grid,
        because a grid of the right shape full of zeros would read as a model that
        made no errors.
      </p>
    );
  }

  const total = matrix.matrix.reduce(
    (sum, row) => sum + row.reduce((inner, value) => inner + value, 0),
    0,
  );
  const max = Math.max(...matrix.matrix.flat(), 1);
  const absentRows = new Set(matrix.classes_absent_from_evaluation_rows);
  const absentEverywhere = new Set(matrix.classes_absent_from_evaluation);

  return (
    <div className="px-5 pb-5 pt-4">
      <p className="text-[11px] leading-relaxed text-muted">
        Rows are what was really there, columns are what the model predicted. The
        figures are counts of rows, not rates, so they total{" "}
        <span className="tabular">{total}</span> — the{" "}
        <span className="tabular">{evaluation.evaluated_rows}</span> rows this
        evaluation scored.
      </p>

      <div className="mt-3 overflow-x-auto">
        <table className="border-collapse text-[12px]">
          <thead>
            <tr>
              <th scope="col" className="px-2 py-1.5 text-[10px] uppercase tracking-wide text-faint">
                <span className="sr-only">Actual</span>
                Actual ↓ / predicted →
              </th>
              {matrix.labels.map((label) => (
                <th
                  key={label}
                  scope="col"
                  className="px-2 py-1.5 text-center text-[10px] uppercase tracking-wide text-faint"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.matrix.map((row, rowIndex) => (
              <tr key={matrix.labels[rowIndex]}>
                <th
                  scope="row"
                  className="px-2 py-1.5 text-left text-[11px] font-medium text-faint"
                >
                  {matrix.labels[rowIndex]}
                </th>
                {row.map((value, columnIndex) => {
                  const onDiagonal = rowIndex === columnIndex;
                  const absent =
                    absentRows.has(matrix.labels[rowIndex]) ||
                    absentEverywhere.has(matrix.labels[columnIndex]);
                  // Opacity scaled by the count, so a large count is visibly darker.
                  // A count of zero is rendered as an explicit "0" rather than as
                  // blank space, because a zero here is a real fact about the model
                  // and is the opposite of an absent metric.
                  const intensity = value === 0 ? 0 : 0.12 + (value / max) * 0.55;
                  return (
                    <td
                      key={`${rowIndex}-${columnIndex}`}
                      className={`tabular px-2.5 py-1.5 text-center ${
                        onDiagonal ? "font-semibold text-ink" : "text-ink-soft"
                      } ${absent ? "opacity-60" : ""}`}
                      style={{
                        backgroundColor:
                          intensity > 0
                            ? `rgba(37, 99, 235, ${intensity.toFixed(3)})`
                            : "transparent",
                      }}
                      title={
                        absent
                          ? `${matrix.labels[rowIndex]} rows predicted as ${matrix.labels[columnIndex]}: ${value}. This class is absent from part or all of the evaluation, so the zero is structural.`
                          : `${matrix.labels[rowIndex]} rows predicted as ${matrix.labels[columnIndex]}: ${value}`
                      }
                    >
                      {value}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-3 space-y-1 text-[11px] leading-relaxed text-muted">
        {/*
          Defaulted rather than read straight off the report. A payload that omits
          one of these arrays should cost the reader one sentence, not the whole
          matrix: this view renders whatever the backend managed to send rather
          than rendering nothing because part of it did not arrive.
        */}
        {(matrix.classes_absent_from_evaluation_rows ?? []).length > 0 ? (
          <p>
            No evaluated row carried{" "}
            {formatAlgorithmList(matrix.classes_absent_from_evaluation_rows)}: those
            rows are zero because the class is not in the data, not because the
            model missed it.
          </p>
        ) : null}
        {(matrix.classes_absent_from_evaluation ?? []).length > 0 ? (
          <p>
            {formatAlgorithmList(matrix.classes_absent_from_evaluation)} appear
            neither in the data nor in any prediction, which is a different fact
            again.
          </p>
        ) : null}
        {matrix.most_confused_pair && matrix.most_confused_pair.count > 0 ? (
          <p>
            The most common single mistake was{" "}
            <span className="text-ink-soft">
              {matrix.most_confused_pair.actual} read as{" "}
              {matrix.most_confused_pair.predicted}
            </span>{" "}
            <span className="tabular">{matrix.most_confused_pair.count}</span>{" "}
            {matrix.most_confused_pair.count === 1 ? "time" : "times"}.
          </p>
        ) : null}
      </div>
    </div>
  );
}

function formatAlgorithmList(labels: string[]): string {
  if (labels.length === 0) return "";
  if (labels.length === 1) return `class ${labels[0]}`;
  return `classes ${labels.slice(0, -1).join(", ")} and ${labels[labels.length - 1]}`;
}
