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
import { ProvenanceCard } from "@/components/dashboard/Provenance";
import { Alert, EmptyState } from "@/components/ui/Feedback";
import { Badge } from "@/components/ui/Primitives";
import type { DigitalTwinState } from "@/lib/digitalTwin";
import {
  MASS_BALANCE_EQUATIONS,
  buildDilutionSeries,
  findScenario,
  formatDilution,
  formatRainfall,
  formatSalinity,
  needsTwoScenarios,
  salinityAxis,
  salinityTickLabel,
  type DilutionPoint,
  type RainfallSweep,
} from "@/lib/simulation";
import { simulationRequest } from "@/lib/dashboard";
import type { SaltPan } from "@/lib/pans";

/**
 * What the rain is expected to do to this pan, from the mass balance.
 *
 * **The curve is the card.** One salinity figure is a claim; a curve across
 * rainfall depths shows the operator how the pan responds and where the forecast
 * actually sits on that response, which is the thing a person deciding whether to
 * open a gate needs. The forecast depth is swept as an extra scenario so the
 * marker on the line is a point the server genuinely evaluated rather than an
 * interpolation of the presets.
 *
 * **Nothing is drawn without a starting brine.** Area, depth and salinity all come
 * from records, and if any is absent the card says which and draws nothing. A curve
 * from an assumed starting point would be a dilution curve for a pan that does not
 * exist, and it would look exactly as authoritative as a real one.
 *
 * The assumptions travel with the numbers. A mass balance that ignores evaporation,
 * drainage and overflow is answering a narrower question than "what will happen to
 * my salt", and a reader who has not seen the list has been told something firmer
 * than the model supports.
 */
export function SimulationCard({
  pan,
  twin,
  sweep,
}: {
  pan: SaltPan;
  twin: DigitalTwinState;
  sweep: RainfallSweep | null;
}) {
  const request = simulationRequest(pan, twin);
  const forecastMm = twin.forecast_rainfall_mm;
  const forecastScenario = sweep ? findScenario(sweep.scenarios, forecastMm) : null;

  return (
    <ProvenanceCard
      kind="simulated"
      title="Rainfall simulation"
      description="The mass balance, run on the recorded state. A deterministic result of stated equations."
    >
      {!request.ok ? (
        <EmptyState
          title="No simulation can be run for this pan yet"
          description={request.reason}
        />
      ) : !sweep ? (
        <div className="py-2 text-[13px] text-muted">Resolving the balance…</div>
      ) : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Outcome
              label="Post-rain salinity at the forecast depth"
              value={forecastScenario ? formatSalinity(forecastScenario.post_rain_salinity_g_l) : "—"}
              unit={forecastScenario ? "g/L" : null}
              hint={
                forecastScenario
                  ? `From ${formatSalinity(sweep.initial_salinity_g_l)} g/L before any rain.`
                  : "No forecast rainfall is in force, so no depth is being applied."
              }
            />
            <Outcome
              label="Expected dilution"
              value={
                forecastScenario
                  ? forecastScenario.dilution_percent === null
                    ? "—"
                    : formatDilution(forecastScenario.dilution_percent)
                  : "—"
              }
              unit={forecastScenario?.dilution_percent != null ? "%" : null}
              hint={
                forecastScenario?.dilution_percent == null
                  ? (forecastScenario?.dilution_percent_reason ??
                    "No forecast rainfall is in force, so no dilution is computed.")
                  : "The pan keeps the same salt in a larger volume."
              }
            />
          </div>

          <DilutionCurve
            sweep={sweep}
            forecastMm={forecastMm}
            forecastSalinity={forecastScenario?.post_rain_salinity_g_l ?? null}
          />

          <Alert tone="info" title="What this model leaves out">
            <ul className="mt-1 space-y-0.5">
              {sweep.assumptions.slice(0, 4).map((assumption) => (
                <li key={assumption} className="text-[11px] leading-relaxed">
                  {assumption}
                </li>
              ))}
            </ul>
            {sweep.assumptions.length > 4 ? (
              <details className="mt-1.5">
                <summary className="cursor-pointer text-[11px]">
                  {sweep.assumptions.length - 4} further assumptions
                </summary>
                <ul className="mt-1 space-y-0.5">
                  {sweep.assumptions.slice(4).map((assumption) => (
                    <li key={assumption} className="text-[11px] leading-relaxed">
                      {assumption}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </Alert>

          <details>
            <summary className="cursor-pointer text-[11px] text-muted">
              The balance being plotted
            </summary>
            <ul className="mt-1.5 space-y-0.5">
              {MASS_BALANCE_EQUATIONS.map((equation) => (
                <li key={equation} className="font-mono text-[11px] text-muted">
                  {equation}
                </li>
              ))}
            </ul>
          </details>
        </div>
      )}
    </ProvenanceCard>
  );
}

function Outcome({
  label,
  value,
  unit,
  hint,
}: {
  label: string;
  value: string;
  unit: string | null;
  hint: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-inset px-3.5 py-3">
      <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-faint">
        {label}
      </p>
      <p className="tabular mt-1 text-2xl font-semibold text-ink">
        {value}
        {unit ? <span className="ml-1 text-xs font-normal text-muted">{unit}</span> : null}
      </p>
      <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{hint}</p>
    </div>
  );
}

/**
 * Rainfall against salinity, with the forecast depth ringed.
 *
 * Every point is a depth the balance evaluated, so a reader can point at any
 * position and name the scenario behind it; the x axis is ticked at those depths
 * so a smooth line cannot imply the gaps were computed too. The forecast marker is
 * the one point on the curve an operator is actually being asked to act on, which
 * is why it is ringed rather than merely coloured.
 */
function DilutionCurve({
  sweep,
  forecastMm,
  forecastSalinity,
}: {
  sweep: RainfallSweep;
  forecastMm: number | null;
  forecastSalinity: number | null;
}) {
  const series = buildDilutionSeries(sweep.scenarios, forecastMm);
  const sparse = needsTwoScenarios(series);
  const axis = salinityAxis(series.map((point) => point.salinity));

  if (sparse) {
    return (
      <p className="py-2 text-[12px] leading-relaxed text-muted">
        One scenario is a value, not a curve. More rainfall depths are needed before
        the relationship can be drawn.
      </p>
    );
  }

  if (axis === null) {
    return (
      <p className="py-2 text-[12px] leading-relaxed text-muted">
        The balance returned no salinity to plot against, so the curve is not drawn
        rather than drawn from an invented range.
      </p>
    );
  }

  return (
    <figure>
      <figcaption className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <span className="text-[10px] font-semibold uppercase tracking-[0.1em] text-faint">
          Rainfall against predicted salinity
        </span>
        {forecastMm !== null && forecastSalinity !== null ? (
          <Badge tone="active">
            Forecast {formatRainfall(forecastMm)} mm ringed
          </Badge>
        ) : (
          <Badge tone="neutral">No forecast in force</Badge>
        )}
      </figcaption>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={series} margin={{ top: 8, right: 16, bottom: 4, left: -12 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-line)" />
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
            <Tooltip content={<CurveTooltip />} cursor={{ stroke: "var(--color-line-strong)" }} />
            <Line
              type="linear"
              dataKey="salinity"
              stroke="var(--color-prov-simulated)"
              strokeWidth={2}
              dot={{ r: 3, fill: "var(--color-prov-simulated)" }}
              activeDot={{ r: 5 }}
              isAnimationActive={false}
            />
            {forecastMm !== null && forecastSalinity !== null ? (
              <ReferenceDot
                x={forecastMm}
                y={forecastSalinity}
                r={8}
                fill="none"
                stroke="var(--color-prov-recommended)"
                strokeWidth={2}
              />
            ) : null}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-1 text-[10px] leading-relaxed text-faint">
        {sweep.model_name}. Each point is one rainfall depth the balance evaluated;
        the curve is not fitted between them.
      </p>
    </figure>
  );
}

function CurveTooltip({
  active,
  payload,
}: {
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
        {point.salinity.toFixed(1)} <span className="font-normal text-muted">g/L</span>
      </p>
      <p className="tabular mt-0.5 text-muted">Dilution {point.dilution}%</p>
      <p className="tabular mt-0.5 text-muted">Post-rain volume {point.volume} m³</p>
      {point.selected ? (
        <p className="mt-1 font-medium text-ink">The depth currently forecast</p>
      ) : null}
      {!point.is_standard_preset ? (
        <p className="mt-1 text-muted">Custom depth, same balance.</p>
      ) : null}
    </div>
  );
}
