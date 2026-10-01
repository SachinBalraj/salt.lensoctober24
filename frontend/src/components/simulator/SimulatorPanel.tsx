"use client";

import { useCallback, useMemo, useState } from "react";
import SimulationChart from "@/components/simulator/SimulationChart";
import { Button, Spinner } from "@/components/ui/Controls";
import { Alert, Skeleton } from "@/components/ui/Feedback";
import { Badge, Card, CardHeader, DataPoint, Eyebrow } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import type { SaltPan } from "@/lib/pans";
import {
  SIMULATOR_DISCLAIMER,
  SIMULATION_ASSUMPTIONS,
  STANDARD_RAINFALL_SCENARIOS_MM,
  buildSimulationRequest,
  checkSweepConsistency,
  findScenario,
  formatDilution,
  formatRainfall,
  formatSalinity,
  formatSaltMass,
  formatVolume,
  parseCustomRainfall,
  saltMassKg,
  saltMassUnit,
  seedSimulationForm,
  summarizeScenario,
  type RainfallSweep,
  type SimulationFormErrors,
  type SimulationFormValues,
} from "@/lib/simulation";
import { sweepRainfallScenarios } from "@/lib/simulationApi";

/**
 * Module 06: the what-if rainfall simulator.
 *
 * The panel holds no arithmetic of its own. It collects four inputs, hands them
 * to the server's mass balance, and shows what comes back. That is why every
 * figure on screen is either an input the operator supplied or a number the
 * balance produced, and why there is no path here by which a result could be
 * typed in.
 *
 * Three things are deliberately never hidden. The disclaimer sits above the
 * results, not in a footer. The assumptions are listed in full, next to the
 * numbers they qualify. And the salt the pan started with is shown beside the
 * salt it ends with, because a reader who can see that the two are the same
 * knows the drop in salinity is dilution and not salt lost — which is the whole
 * claim the module makes.
 *
 * A pan with no recorded salinity is not simulated. The starting salinity is an
 * input to the balance and there is no defensible default for it, so the panel
 * asks for one rather than borrowing a design target and presenting the result
 * as if it described the pan.
 */

interface SweepState {
  key: string;
  sweep: RainfallSweep | null;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError(fallback, { status: -1 });
}

function ScenarioButton({
  rainfallMm,
  selected,
  onSelect,
}: {
  rainfallMm: number;
  selected: boolean;
  onSelect: (rainfallMm: number) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(rainfallMm)}
      aria-pressed={selected}
      className={`tabular rounded-lg border px-3 py-2 text-[13px] font-semibold transition-colors ${
        selected
          ? "border-accent-line bg-accent-soft text-accent"
          : "border-line bg-inset text-ink-soft hover:bg-accent-soft/60"
      }`}
    >
      {rainfallMm}
      <span className="ml-0.5 text-[11px] font-normal">mm</span>
    </button>
  );
}

function NumberField({
  id,
  label,
  unit,
  hint,
  value,
  error,
  onChange,
}: {
  id: string;
  label: string;
  unit: string;
  hint: string;
  value: string;
  error: string | undefined;
  onChange: (value: string) => void;
}) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="block text-[11px] font-medium uppercase tracking-wide text-faint">
        {label}
      </label>
      <div className="mt-1 flex items-baseline gap-1.5">
        <input
          id={id}
          type="text"
          inputMode="decimal"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : `${id}-hint`}
          className={`tabular w-full min-w-0 rounded-lg border bg-surface px-2.5 py-1.5 text-sm text-ink ${
            error ? "border-danger-line" : "border-line-strong"
          }`}
        />
        <span className="shrink-0 text-xs text-muted">{unit}</span>
      </div>
      {error ? (
        <p id={`${id}-error`} className="mt-1 text-[11px] leading-snug text-danger">
          {error}
        </p>
      ) : (
        <p id={`${id}-hint`} className="mt-1 text-[11px] leading-snug text-muted">
          {hint}
        </p>
      )}
    </div>
  );
}

export default function SimulatorPanel({
  pan,
  isDemo = false,
}: {
  pan: SaltPan;
  isDemo?: boolean;
}) {
  const [edits, setEdits] = useState<Partial<SimulationFormValues>>({});
  const [errors, setErrors] = useState<SimulationFormErrors>({});
  const [custom, setCustom] = useState("");
  const [customError, setCustomError] = useState<string | null>(null);
  const [customDepths, setCustomDepths] = useState<number[]>([]);
  const [selected, setSelected] = useState<number>(20);
  const [requestKey, setRequestKey] = useState<string | null>(null);
  const [state, setState] = useState<SweepState | null>(null);

  const salinitySource = pan.latest_observation?.salinity_g_l ?? null;

  // Seeded from the pan's registered geometry and its newest recorded salinity.
  // A salinity nobody measured stays blank rather than being filled from the
  // harvest target, which is a design value and not a reading.
  //
  // Derived during render rather than copied into state by an effect, so a
  // salinity that arrives after the first paint still seeds the form. An
  // operator's own edit takes precedence over the seed and is not overwritten.
  const seeded = useMemo(
    () => seedSimulationForm(pan.area_m2, pan.normal_depth_cm, salinitySource),
    [pan.area_m2, pan.normal_depth_cm, salinitySource],
  );
  const form: SimulationFormValues = {
    area_m2: edits.area_m2 ?? seeded.area_m2,
    initial_depth_cm: edits.initial_depth_cm ?? seeded.initial_depth_cm,
    initial_salinity_g_l: edits.initial_salinity_g_l ?? seeded.initial_salinity_g_l,
  };

  const run = useCallback(
    (next: SimulationFormValues, extras: number[]) => {
      if (isDemo) return;
      const { payload, errors: found } = buildSimulationRequest(next);
      setErrors(found);
      if (Object.keys(found).length > 0) return;

      const key = `${payload.area_m2}|${payload.initial_depth_m}|${payload.initial_salinity_g_l}|${extras.join(",")}`;
      setRequestKey(key);
      setState(null);

      void (async () => {
        try {
          const sweep = await sweepRainfallScenarios({
            ...payload,
            rainfallScenariosMm: extras,
          });
          setState({ key, sweep, error: null });
        } catch (caught) {
          setState({
            key,
            sweep: null,
            error: asApiError(caught, "Unexpected error running the mass balance."),
          });
        }
      })();
    },
    [isDemo],
  );

  const loading = requestKey !== null && state?.key !== requestKey;
  const sweep = state?.sweep ?? null;
  const error = state?.error ?? null;
  const connectivity = error?.isConnectivity ?? false;
  const active = findScenario(sweep?.scenarios ?? [], selected);
  const summary = useMemo(
    () => summarizeScenario(active, sweep?.initial_salinity_g_l ?? null),
    [active, sweep],
  );
  const consistency = sweep ? checkSweepConsistency(sweep) : null;

  // The server's own copy of the assumptions governs once a sweep is in hand, so
  // the disclosure can never drift from the model that produced the numbers.
  const assumptions = sweep?.assumptions ?? SIMULATION_ASSUMPTIONS;
  const disclaimer = sweep?.disclaimer ?? SIMULATOR_DISCLAIMER;

  function update(field: keyof SimulationFormValues, value: string) {
    setEdits((current) => ({ ...current, [field]: value }));
  }

  function handleRun() {
    run(form, customDepths);
  }

  function handleAddCustom() {
    const { value, error: found } = parseCustomRainfall(custom);
    if (value === null) {
      setCustomError(found);
      return;
    }
    setCustomError(null);
    setCustom("");
    const extras = customDepths.includes(value)
      ? customDepths
      : [...customDepths, value].sort((left, right) => left - right);
    setCustomDepths(extras);
    run(form, extras);
  }

  return (
    <div className="mt-4 space-y-4">
      <header>
        <h2 className="text-sm font-semibold uppercase tracking-wide text-ink">
          What-if rainfall simulator — pan {pan.pan_id}
        </h2>
        <p className="mt-1 text-xs text-muted">
          A water and salt balance on this pan&apos;s area and brine depth. The
          result is arithmetic on the inputs you supply, not a forecast of the
          weather.
        </p>
      </header>

      <Alert tone="info" title="This is a simulation, not a prediction">
        <p className="font-semibold">{disclaimer}</p>
        <p className="mt-1">
          The salinity figures below come from conservation of volume and
          conservation of salt under the assumptions listed with the results. No
          machine-learned model is involved, and no forecast of rainfall is made
          or implied.
        </p>
      </Alert>

      {isDemo ? (
        <Card>
          <Alert tone="demo" title="No mass balance for a demo record">
            <p>
              This pan exists only in your browser, and the balance runs on the
              server. Nothing about a demonstration record should be presented as
              a computed result, so no scenario is solved here. Register the pan
              and record a salinity reading to run a real what-if.
            </p>
          </Alert>
        </Card>
      ) : null}

      <Card>
        <CardHeader
          title="Starting conditions"
          description="Seeded from this pan's registered geometry and its newest recorded salinity. Edit any of them to run a different what-if."
        />
        <div className="space-y-4 p-5">
          <div className="grid gap-4 sm:grid-cols-3">
            <NumberField
              id="sim-area"
              label="Pan area (A)"
              unit="m²"
              hint="Registered design value."
              value={form.area_m2}
              error={errors.area_m2}
              onChange={(value) => update("area_m2", value)}
            />
            <NumberField
              id="sim-depth"
              label="Brine depth (D0)"
              unit="cm"
              hint="Registered normal depth, entered in cm."
              value={form.initial_depth_cm}
              error={errors.initial_depth_cm}
              onChange={(value) => update("initial_depth_cm", value)}
            />
            <NumberField
              id="sim-salinity"
              label="Current salinity (C0)"
              unit="g/L"
              hint={
                salinitySource === null
                  ? "No reading recorded — enter a value to run a what-if."
                  : "From the newest recorded observation."
              }
              value={form.initial_salinity_g_l}
              error={errors.initial_salinity_g_l}
              onChange={(value) => update("initial_salinity_g_l", value)}
            />
          </div>

          {salinitySource === null ? (
            <Alert tone="warning" title="No salinity has been measured for this pan">
              <p>
                The balance needs a starting salinity, and there is no defensible
                default for one. Record an observation for this pan, or type a
                figure above to explore a what-if. Nothing is assumed on your
                behalf.
              </p>
            </Alert>
          ) : null}

          <div className="flex flex-wrap items-end gap-3">
            <Button
              variant="primary"
              onClick={handleRun}
              disabled={isDemo}
              title={
                isDemo
                  ? "The mass balance runs on the server, so it cannot run for a demo record"
                  : undefined
              }
            >
              {loading ? <Spinner label="Solving" /> : "Run mass balance"}
            </Button>
            <span className="text-[11px] leading-relaxed text-muted">
              Sweeps 0, 5, 10, 20, 50 and 100 mm, plus any custom depth you add.
            </span>
          </div>
        </div>
      </Card>

      {error ? (
        <Alert
          tone={connectivity ? "warning" : "danger"}
          title={
            connectivity
              ? "The mass balance could not be reached"
              : "The mass balance could not be run"
          }
          action={
            <button
              type="button"
              onClick={handleRun}
              className="text-xs font-medium text-ink-soft hover:underline"
            >
              Try again
            </button>
          }
        >
          {error.message}
        </Alert>
      ) : null}

      {loading ? (
        <Card>
          <div className="space-y-3 p-5">
            <Skeleton className="h-20 w-full" />
            <Skeleton className="h-64 w-full" />
          </div>
        </Card>
      ) : null}

      {sweep ? (
        <>
          {consistency && !consistency.consistent ? (
            <Alert tone="danger" title="The returned sweep is not self-consistent">
              <p>
                A volume balance must dilute, never concentrate. Treat these
                figures as a bug to report rather than as a result.
              </p>
              <ul className="mt-1 list-disc pl-4">
                {consistency.problems.map((problem) => (
                  <li key={problem}>{problem}</li>
                ))}
              </ul>
            </Alert>
          ) : null}

          <Card>
            <CardHeader
              title="Rainfall scenario"
              description="Presets, plus any custom depth you added to this sweep."
              actions={<Badge tone="demo">Simulated</Badge>}
            />
            <div className="space-y-4 p-5">
              <div className="flex flex-wrap gap-2">
                {STANDARD_RAINFALL_SCENARIOS_MM.map((depth) => (
                  <ScenarioButton
                    key={depth}
                    rainfallMm={depth}
                    selected={selected === depth}
                    onSelect={setSelected}
                  />
                ))}
                {customDepths.map((depth) => (
                  <ScenarioButton
                    key={depth}
                    rainfallMm={depth}
                    selected={selected === depth}
                    onSelect={setSelected}
                  />
                ))}
              </div>

              <div className="flex flex-wrap items-end gap-2 border-t border-line pt-4">
                <div className="w-32">
                  <label
                    htmlFor="sim-custom"
                    className="block text-[11px] font-medium uppercase tracking-wide text-faint"
                  >
                    Custom rainfall
                  </label>
                  <input
                    id="sim-custom"
                    type="text"
                    inputMode="decimal"
                    value={custom}
                    onChange={(event) => {
                      setCustom(event.target.value);
                      setCustomError(null);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        handleAddCustom();
                      }
                    }}
                    placeholder="e.g. 12.5"
                    aria-invalid={customError ? true : undefined}
                    className={`tabular mt-1 w-full rounded-lg border bg-surface px-2.5 py-1.5 text-sm text-ink ${
                      customError ? "border-danger-line" : "border-line-strong"
                    }`}
                  />
                </div>
                <span className="pb-2 text-xs text-muted">mm</span>
                <Button variant="secondary" onClick={handleAddCustom}>
                  Add depth
                </Button>
              </div>
              {customError ? (
                <p className="text-[11px] text-danger">{customError}</p>
              ) : null}

              <div className="border-t border-line pt-4">
                <Eyebrow>Selected scenario</Eyebrow>
                <p className="mt-1.5 text-sm text-ink">
                  {formatRainfall(active?.rainfall_mm ?? null)} mm of rain
                </p>
                <p className="mt-1 text-[13px] leading-relaxed text-muted">
                  {summary.headline}
                </p>
                {summary.detail ? (
                  <p className="mt-0.5 text-[13px] leading-relaxed text-muted">
                    {summary.detail}
                  </p>
                ) : null}
              </div>

              <dl className="grid grid-cols-2 gap-x-5 gap-y-5 border-t border-line pt-4 sm:grid-cols-3">
                <DataPoint
                  label="Current salinity"
                  value={formatSalinity(sweep.initial_salinity_g_l)}
                  unit="g/L"
                />
                <DataPoint
                  label="Rainfall scenario"
                  value={formatRainfall(active?.rainfall_mm ?? null)}
                  unit="mm"
                />
                <DataPoint
                  label="Initial volume (V0)"
                  value={formatVolume(sweep.initial_volume_m3)}
                  unit="m³"
                />
                <DataPoint
                  label="Rainfall volume (Vr)"
                  value={formatVolume(active?.rainfall_volume_m3 ?? null)}
                  unit="m³"
                />
                <DataPoint
                  label="Post-rain volume (V1)"
                  value={formatVolume(active?.post_rain_volume_m3 ?? null)}
                  unit="m³"
                />
                <DataPoint
                  label="Predicted post-rain salinity"
                  value={formatSalinity(active?.post_rain_salinity_g_l ?? null)}
                  unit="g/L"
                />
                <DataPoint
                  label="Expected dilution"
                  value={formatDilution(active?.dilution_percent ?? null)}
                  unit="%"
                />
              </dl>

              {active && active.dilution_percent === null ? (
                <Alert tone="warning" title="No dilution percentage to report">
                  <p>{active.dilution_percent_reason}</p>
                </Alert>
              ) : null}
            </div>
          </Card>

          <SimulationChart
            scenarios={sweep.scenarios}
            selectedRainfallMm={selected}
          />

          <Card>
            <CardHeader
              title="The salt in the pan is unchanged"
              description="Dilution lowers the concentration, not the amount of salt. Both figures are the same number, which is what makes the drop in salinity arithmetic rather than salt loss."
            />
            <dl className="grid grid-cols-2 gap-x-5 gap-y-5 p-5">
              <DataPoint
                label="Salt before the rain"
                value={formatSaltMass(
                  saltMassKg(sweep.initial_volume_m3, sweep.initial_salinity_g_l),
                )}
                unit={saltMassUnit(
                  saltMassKg(sweep.initial_volume_m3, sweep.initial_salinity_g_l),
                )}
              />
              <DataPoint
                label="Salt after the rain"
                value={formatSaltMass(
                  saltMassKg(
                    active?.post_rain_volume_m3 ?? null,
                    active?.post_rain_salinity_g_l ?? null,
                  ),
                )}
                unit={saltMassUnit(
                  saltMassKg(
                    active?.post_rain_volume_m3 ?? null,
                    active?.post_rain_salinity_g_l ?? null,
                  ),
                )}
              />
            </dl>
          </Card>

          <Card>
            <CardHeader
              title="Assumptions this result depends on"
              description="Every process the balance leaves out. Each one would move the answer if it were modelled."
            />
            <ul className="space-y-2 p-5">
              {assumptions.map((assumption) => (
                <li
                  key={assumption}
                  className="flex gap-2.5 text-[13px] leading-relaxed text-muted"
                >
                  <span aria-hidden className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-faint" />
                  {assumption}
                </li>
              ))}
            </ul>
            <p className="border-t border-line px-5 py-3 text-[11px] leading-relaxed text-faint">
              {disclaimer} Model:{" "}
              <span className="font-mono text-muted">{sweep.model_name}</span>
            </p>
          </Card>
        </>
      ) : null}

      {!sweep && !loading ? (
        <Card>
          <div className="px-5 py-8">
            <p className="text-[13px] leading-relaxed text-muted">
              Nothing has been run yet. Choose a starting salinity and press{" "}
              <span className="font-medium text-ink-soft">Run mass balance</span>{" "}
              to resolve all six preset rainfall depths at once.
            </p>
          </div>
        </Card>
      ) : null}
    </div>
  );
}
