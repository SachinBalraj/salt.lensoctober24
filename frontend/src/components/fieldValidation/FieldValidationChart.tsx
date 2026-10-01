"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  eventChartLabel,
  warrantsSingleEventNote,
  SINGLE_EVENT_DIFFERENCE_NOTE,
  type FieldValidationComparison,
} from "@/lib/fieldValidation";
import { Card, CardHeader } from "@/components/ui/Primitives";

/**
 * The predicted-against-reported drawings for one comparison.
 *
 * Predicted is the target the model would have produced from the frozen event
 * inputs at record time; reported is what the pan says was actually there. The
 * two are drawn as a pair of bars per event, so a reader sees at a glance how
 * far the reported figures sit from the balance the rainfall alone describes.
 * Every comparison arrives already carrying the absolute differences the backend
 * computed, so this chart only shapes values that were computed from the stored
 * reports; there is nothing to invent here.
 *
 * **The axis names the place and the day, not the record.** A 32-character event
 * id is a key, not a caption: it is unreadable at figure size, it is identical for
 * every event on a pan, and it says nothing about which experiment is on screen.
 * The label is built per event from the pan name and the event date, so a second
 * pan or a second day captions itself. The id is still the row's identity and
 * still travels to the database, the API, the audit history and the exports —
 * only the text under the bars changed.
 */

type Row = {
  /** The caption drawn under the bars. Derived, never stored. */
  label: string;
  /** The full event id. The chart's internal key, unchanged by the caption. */
  id: string;
  predictedSalinity: number | null;
  reportedSalinity: number | null;
  predictedDepth: number | null;
  reportedDepth: number | null;
};

function rowsFor(
  comparisons: FieldValidationComparison[],
  panNames: Record<string, string>,
): Row[] {
  return comparisons.map((comparison) => ({
    label: eventChartLabel(
      // The registry's own pan name, when the caller could resolve it. The
      // comparison carries one too, but as the pan's location rather than its
      // name, so it is only a last resort when the registry has no entry.
      panNames[comparison.pan_id] ?? comparison.pan_name,
      comparison.event_timestamp,
    ),
    id: comparison.event_id,
    predictedSalinity: comparison.predicted_salinity_g_l,
    reportedSalinity: comparison.reported_salinity_g_l,
    predictedDepth: comparison.predicted_depth_cm,
    reportedDepth: comparison.reported_depth_cm,
  }));
}

function formatFigure(value: number | null): string {
  return value === null ? "not comparable" : `${value.toFixed(2)}`;
}

/** The caption under the bars, as axis text at figure size. */
const AXIS_TICK = { fontSize: 11, fill: "var(--color-muted, #64748b)" } as const;
const VALUE_TICK = { fontSize: 11, fill: "var(--color-faint, #94a3b8)" } as const;
const AXIS_UNIT = {
  style: { fontSize: 11, fill: "var(--color-faint, #94a3b8)" },
} as const;

function unitLabel(value: string) {
  return { value, angle: -90, position: "insideLeft" as const, offset: 10, ...AXIS_UNIT };
}

function SalinityChart({ rows }: { rows: Row[] }) {
  return (
    <div className="h-64 w-full px-2 pb-2 pt-4">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 10, right: 14, bottom: 4, left: -14 }}>
          <CartesianGrid
            strokeDasharray="2 4"
            stroke="var(--color-line, #e2e8f0)"
            vertical={false}
          />
          <XAxis
            dataKey="label"
            tick={AXIS_TICK}
            interval={0}
            // A caption set at an angle stays inside its own column when a second
            // event is added; upright text of this length would collide with it.
            angle={-12}
            textAnchor="end"
            height={48}
          />
          <YAxis
            tick={VALUE_TICK}
            width={54}
            label={unitLabel("g/L")}
          />
          <Tooltip
            cursor={{ fill: "var(--color-inset, #f8fafc)" }}
            content={({ active, payload }) => {
              if (!active || !payload || payload.length === 0) return null;
              const point = payload[0]?.payload as Row | undefined;
              if (!point) return null;
              return (
                <div className="max-w-xs rounded-lg border border-line bg-surface px-3 py-2 text-[12px] shadow-sm">
                  <p className="font-semibold text-ink">{point.label}</p>
                  <p className="tabular mt-1 text-ink">
                    Predicted: {formatFigure(point.predictedSalinity)} g/L
                  </p>
                  <p className="tabular text-ink-soft">
                    Reported: {formatFigure(point.reportedSalinity)} g/L
                  </p>
                </div>
              );
            }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar
            dataKey="predictedSalinity"
            name="Predicted"
            isAnimationActive={false}
            radius={[3, 3, 0, 0]}
            fill="var(--color-caution, #b45309)"
          />
          <Bar
            dataKey="reportedSalinity"
            name="Reported"
            isAnimationActive={false}
            radius={[3, 3, 0, 0]}
            fill="var(--color-active, #0f766e)"
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function DepthChart({ rows }: { rows: Row[] }) {
  return (
    <div className="h-64 w-full px-2 pb-2 pt-4">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 10, right: 14, bottom: 4, left: -14 }}>
          <CartesianGrid
            strokeDasharray="2 4"
            stroke="var(--color-line, #e2e8f0)"
            vertical={false}
          />
          <XAxis
            dataKey="label"
            tick={AXIS_TICK}
            interval={0}
            angle={-12}
            textAnchor="end"
            height={48}
          />
          <YAxis
            tick={VALUE_TICK}
            width={54}
            label={unitLabel("cm")}
          />
          <Tooltip
            cursor={{ fill: "var(--color-inset, #f8fafc)" }}
            content={({ active, payload }) => {
              if (!active || !payload || payload.length === 0) return null;
              const point = payload[0]?.payload as Row | undefined;
              if (!point) return null;
              return (
                <div className="max-w-xs rounded-lg border border-line bg-surface px-3 py-2 text-[12px] shadow-sm">
                  <p className="font-semibold text-ink">{point.label}</p>
                  <p className="tabular mt-1 text-ink">
                    Predicted: {formatFigure(point.predictedDepth)} cm
                  </p>
                  <p className="tabular text-ink-soft">
                    Reported: {formatFigure(point.reportedDepth)} cm
                  </p>
                </div>
              );
            }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar
            dataKey="predictedDepth"
            name="Predicted"
            isAnimationActive={false}
            radius={[3, 3, 0, 0]}
            fill="var(--color-caution, #b45309)"
          />
          <Bar
            dataKey="reportedDepth"
            name="Reported"
            isAnimationActive={false}
            radius={[3, 3, 0, 0]}
            fill="var(--color-active, #0f766e)"
          />
          <ReferenceLine y={0} stroke="var(--color-line, #e2e8f0)" />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function FieldValidationChart({
  comparisons,
  panNames = {},
}: {
  comparisons: FieldValidationComparison[];
  /** Pan id to pan name, resolved by the caller from the pan registry. */
  panNames?: Record<string, string>;
}) {
  const rows = rowsFor(comparisons, panNames);
  if (rows.length === 0) return null;
  const showScopeNote = warrantsSingleEventNote(comparisons);
  return (
    <div>
      <Card>
        <CardHeader
          title="Predicted against reported"
          description="The rainfall-dilution mass balance predicted from the event's frozen inputs, drawn beside what the pan reported. A gap between the two is a gap a validation number is supposed to explain."
        />
        <p className="px-5 pt-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
          Salinity
        </p>
        <SalinityChart rows={rows} />
      </Card>
      <Card className="mt-4">
        <CardHeader
          title="Water depth"
          description="The same comparison for pond depth. Depth is predicted alongside salinity, so the two charts share one set of assumptions."
        />
        <p className="px-5 pt-3 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
          Water depth
        </p>
        <DepthChart rows={rows} />
      </Card>
      {showScopeNote ? (
        <p className="mt-3 text-[11px] leading-relaxed text-muted">
          {SINGLE_EVENT_DIFFERENCE_NOTE}
        </p>
      ) : null}
    </div>
  );
}