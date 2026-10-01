"use client";

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardHeader } from "@/components/ui/Primitives";
import {
  MASS_BALANCE_EQUATIONS,
  buildDilutionSeries,
  needsTwoScenarios,
  salinityAxis,
  salinityTickLabel,
  type DilutionPoint,
  type RainfallScenario,
} from "@/lib/simulation";

/**
 * Rainfall against predicted post-rain salinity, plotted straight from the
 * scenarios the mass balance evaluated.
 *
 * Every point on the line is a rainfall depth that was actually computed, so a
 * reader can put a finger on any position and name the scenario behind it. The
 * curve is not fitted, smoothed or interpolated between samples, and a custom
 * depth sits on the same line as the presets because it is the same
 * calculation — drawing it differently would imply a different kind of result.
 *
 * The selected scenario is ringed rather than merely highlighted, so it stays
 * findable when several points are close together at low rainfall.
 *
 * The x axis is ticked at the depths that were actually evaluated, so a smooth
 * line cannot suggest that the gaps between the samples were computed too. They
 * were not.
 */

function TooltipContent({ active, payload }: {
  active?: boolean;
  payload?: Array<{ payload: DilutionPoint }>;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const point = payload[0]?.payload;
  if (!point) return null;
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-[12px] shadow-sm">
      <p className="font-semibold text-ink">{point.label}</p>
      <p className="tabular mt-1 text-ink">
        {point.salinity.toFixed(1)}{" "}
        <span className="font-normal text-muted">g/L</span>
      </p>
      <p className="tabular mt-0.5 text-muted">
        Dilution {point.dilution}%
      </p>
      <p className="tabular mt-0.5 text-muted">
        Post-rain volume {point.volume} m³
      </p>
      {!point.is_standard_preset ? (
        <p className="mt-1 text-muted">Custom depth, same balance.</p>
      ) : null}
    </div>
  );
}

export default function SimulationChart({
  scenarios,
  selectedRainfallMm,
}: {
  scenarios: readonly RainfallScenario[];
  selectedRainfallMm: number | null;
}) {
  const series = buildDilutionSeries(scenarios, selectedRainfallMm);
  const selected = series.find((point) => point.selected) ?? null;
  const sparse = needsTwoScenarios(series);
  const axis = salinityAxis(series.map((point) => point.salinity));

  return (
    <Card>
      <CardHeader
        title="Rainfall against predicted post-rain salinity"
        description="Each point is one rainfall depth the mass balance evaluated. The curve is not fitted between them."
      />
      {sparse ? (
        <div className="px-5 py-8">
          <p className="text-[13px] leading-relaxed text-muted">
            One scenario is a value, not a curve. Sweep at least two rainfall
            depths and the relationship between rainfall and salinity can be
            drawn.
          </p>
        </div>
      ) : axis === null ? (
        <div className="px-5 py-8">
          <p className="text-[13px] leading-relaxed text-muted">
            The balance returned no salinity to plot against, so the curve is not
            drawn rather than drawn from an invented range.
          </p>
        </div>
      ) : (
        <>
          <div className="h-72 w-full px-2 py-4">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={series}
                margin={{ top: 8, right: 16, bottom: 4, left: -12 }}
              >
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--color-line)"
                />
                <XAxis
                  dataKey="rainfall"
                  type="number"
                  scale="linear"
                  domain={["dataMin", "dataMax"]}
                  ticks={series.map((point) => point.rainfall)}
                  interval={0}
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  axisLine={{ stroke: "var(--color-line)" }}
                  unit=" mm"
                />
                <YAxis
                  tick={{ fontSize: 10 }}
                  tickLine={false}
                  axisLine={false}
                  width={58}
                  unit=" g/L"
                  domain={axis.domain}
                  ticks={axis.ticks}
                  tickFormatter={(value: number) => salinityTickLabel(value, axis.step)}
                />
                <Tooltip
                  content={<TooltipContent />}
                  cursor={{ stroke: "var(--color-line-strong)" }}
                />
                <Line
                  type="linear"
                  dataKey="salinity"
                  stroke="var(--color-accent)"
                  strokeWidth={2}
                  dot={{ r: 3, fill: "var(--color-accent)" }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}
                />
                {selected ? (
                  <ReferenceDot
                    x={selected.rainfall}
                    y={selected.salinity}
                    r={8}
                    fill="none"
                    stroke="var(--color-accent)"
                    strokeWidth={2}
                  />
                ) : null}
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="border-t border-line px-5 py-3">
            <p className="text-[11px] font-medium uppercase tracking-wide text-faint">
              The balance being plotted
            </p>
            <ul className="mt-1.5 space-y-0.5">
              {MASS_BALANCE_EQUATIONS.map((equation) => (
                <li key={equation} className="font-mono text-[11px] text-muted">
                  {equation}
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </Card>
  );
}
