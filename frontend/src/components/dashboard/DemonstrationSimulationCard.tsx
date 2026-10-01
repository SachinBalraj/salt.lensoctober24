"use client";

import { useState } from "react";
import { ProvenanceCard } from "@/components/dashboard/Provenance";
import { Button } from "@/components/ui/Controls";
import { Alert, EmptyState } from "@/components/ui/Feedback";
import { Badge } from "@/components/ui/Primitives";
import {
  BRINE_TEMPERATURE_NOTE,
  DEMONSTRATION_LABEL,
  DEMONSTRATION_NOTICE,
  DEMONSTRATION_RAINFALL_SCENARIOS_MM,
  DEMONSTRATION_SIMULATION_INPUTS,
} from "@/lib/demonstration";
import { ApiError } from "@/lib/api";
import {
  formatDilution,
  formatRainfall,
  formatSalinity,
  type RainfallScenario,
  type RainfallSweep,
} from "@/lib/simulation";
import { sweepRainfallScenarios } from "@/lib/simulationApi";
import type { SaltPan } from "@/lib/pans";

/**
 * The mass balance run on the demonstration's own starting brine.
 *
 * **This card exists because the recorded one cannot be drawn yet.** The
 * dashboard's simulation card refuses to draw a curve without a measured salinity
 * and depth, and that refusal is correct: a curve from an assumed pan is a
 * dilution curve for a pan that does not exist. But a project with no
 * measurements then shows nothing at all, and an operator cannot tell whether the
 * simulator works or the deployment is broken.
 *
 * So the answer is a second card, not a weakened first one. It runs the same
 * server balance, through the same endpoint, on a pan state that is stated on the
 * face of the card and is obviously not a measurement — 250 g/L at 20 cm, the
 * demonstration's own figures. Every number below it is real arithmetic on real
 * inputs; what is hypothetical is the input, and that is named before the figures
 * rather than in a footnote.
 *
 * **It writes nothing.** There is no POST, so recording a demonstration and
 * running this card are independent acts, and neither is a precondition for the
 * other.
 *
 * **The area is the one input it takes from the pan.** Salinity, depth and
 * rainfall are the demonstration's; area is not, because an area is a static
 * registration property rather than a reading, and inventing one would mean the
 * volumes were fictional too. The card says which area it used.
 *
 * **It runs when asked, not when the page is opened.** An earlier version fetched
 * on mount, which was wrong for a reason beyond the cost: the dashboard promises
 * that a page load computes nothing, and a second card quietly breaking that
 * promise teaches an operator that a blank screen might just be work in progress.
 * It also made the recorded card's refusal-to-simulate unreadable, because the
 * endpoint was being hit anyway and "nothing was asked of the balance" was no
 * longer true. One button, and the balance is not touched until it is pressed.
 */
export function DemonstrationSimulationCard({ pan }: { pan: SaltPan | null }) {
  // `idle` is its own state rather than `loaded` with a null sweep, so that "not
  // run yet" and "ran and returned nothing" cannot be mistaken for one another,
  // and so the first render is truthful about having asked for nothing.
  const [status, setStatus] = useState<"idle" | "loading" | "loaded" | "failed">(
    "idle",
  );
  const [sweep, setSweep] = useState<RainfallSweep | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const area = pan && pan.area_m2 > 0 ? pan.area_m2 : null;

  // Changing pan invalidates the previous run: its volumes were computed for the
  // old area, so keeping it on screen would be showing a curve belonging to a
  // different salt pan than the heading names.
  //
  // Adjusted during render rather than in an effect. An effect that clears state
  // in response to a prop runs a second render every time the prop changes, and
  // for one frame in between the card still shows the previous pan's curve. React
  // documents this as the way to reset state when an input changes, and it is
  // both cheaper and correct on the frame it happens.
  const [runArea, setRunArea] = useState<number | null>(null);
  if (area !== runArea) {
    setRunArea(area);
    setSweep(null);
    setError(null);
    setStatus("idle");
  }

  async function run() {
    if (area === null) return;
    setStatus("loading");
    setError(null);
    try {
      const result = await sweepRainfallScenarios({
        area_m2: area,
        initial_depth_m: DEMONSTRATION_SIMULATION_INPUTS.initial_depth_m,
        initial_salinity_g_l: DEMONSTRATION_SIMULATION_INPUTS.salinity_g_l,
      });
      setSweep(result);
      setStatus("loaded");
    } catch (caught) {
      setSweep(null);
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError("The balance could not be run.", { status: -1 }),
      );
      setStatus("failed");
    }
  }

  // The server always resolves its own six presets; the card shows the five the
  // demonstration was defined over, so the reader is not invited to wonder where
  // a sixth column went or whether it was computed and hidden.
  const scenarios: RainfallScenario[] = sweep
    ? sweep.scenarios.filter((scenario) =>
        DEMONSTRATION_RAINFALL_SCENARIOS_MM.includes(
          scenario.rainfall_mm as (typeof DEMONSTRATION_RAINFALL_SCENARIOS_MM)[number],
        ),
      )
    : [];

  return (
    <ProvenanceCard
      kind="simulated"
      title="Demonstration simulation"
      description="The mass balance, run on the demonstration's own starting brine. Real arithmetic on a hypothetical pan."
      actions={
        <div className="flex items-center gap-2">
          <Badge tone="demo">{DEMONSTRATION_LABEL}</Badge>
          <Button
            type="button"
            onClick={() => void run()}
            disabled={area === null || status === "loading"}
            data-testid="demo-simulate"
          >
            {status === "loading"
              ? "Running…"
              : status === "idle"
                ? "Run the balance"
                : "Run again"}
          </Button>
        </div>
      }
    >
      <div className="space-y-4">
        <Alert tone="demo" title={DEMONSTRATION_NOTICE}>
          <p>
            This curve is computed from{" "}
            <strong className="font-semibold">
              {DEMONSTRATION_SIMULATION_INPUTS.salinity_g_l} g/L at{" "}
              {DEMONSTRATION_SIMULATION_INPUTS.initial_depth_m} m
            </strong>
            {area !== null ? (
              <>
                {" "}
                over this pan&apos;s registered{" "}
                <strong className="font-semibold">{area.toLocaleString()} m²</strong>
              </>
            ) : null}
            . Those are the demonstration&apos;s own values, not a reading of this
            pan, so the curve describes the balance and nothing about the salt.
          </p>
          <p className="mt-1.5">{BRINE_TEMPERATURE_NOTE}</p>
        </Alert>

        {area === null ? (
          <EmptyState
            title="This pan has no usable area"
            description="The balance multiplies depth by area to get a volume, and this pan's registration has no area to multiply. One was not invented: a demonstration with a made-up area would have fictional volumes as well as a fictional salinity."
          />
        ) : status === "failed" && error ? (
          <Alert tone="danger" title="The demonstration balance could not be run">
            <p>{error.message}</p>
          </Alert>
        ) : status === "loading" && !sweep ? (
          <div className="py-2 text-[13px] text-muted">Running the balance…</div>
        ) : status === "idle" ? (
          <EmptyState
            title="Not run yet"
            description="The balance has not been asked for anything. Running it asks the server the same question the recorded simulation asks, with this card's stated inputs instead of a measurement."
          />
        ) : scenarios.length === 0 ? (
          <EmptyState
            title="The balance returned no scenarios"
            description="The server resolved no rainfall depth against these inputs, so there is nothing to show."
          />
        ) : (
          <>
            <table className="w-full text-[12px]">
              <caption className="sr-only">
                Post-rain salinity, depth and dilution at each demonstration rainfall depth
              </caption>
              <thead>
                <tr className="border-b border-line text-left text-[10px] uppercase tracking-[0.1em] text-faint">
                  <th scope="col" className="py-1.5 pr-2 font-medium">
                    Rainfall <span className="text-[10px] text-muted">mm</span>
                  </th>
                  <th scope="col" className="py-1.5 pr-2 font-medium">Post-rain salinity</th>
                  <th scope="col" className="py-1.5 pr-2 font-medium">Post-rain depth</th>
                  <th scope="col" className="py-1.5 font-medium">Dilution</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {scenarios.map((scenario) => (
                  <tr key={scenario.rainfall_mm} className="border-b border-line/60">
                    <th scope="row" className="py-1.5 pr-2 text-left font-normal text-ink-soft">
                      {formatRainfall(scenario.rainfall_mm)}
                    </th>
                    <td className="py-1.5 pr-2 text-ink">
                      {formatSalinity(scenario.post_rain_salinity_g_l)}{" "}
                      <span className="text-[10px] text-muted">g/L</span>
                    </td>
                    <td className="py-1.5 pr-2 text-ink">
                      {(scenario.post_rain_depth_m * 100).toFixed(1)}{" "}
                      <span className="text-[10px] text-muted">cm</span>
                    </td>
                    <td className="py-1.5 text-ink">
                      {scenario.dilution_percent === null
                        ? "—"
                        : formatDilution(scenario.dilution_percent)}{" "}
                      {scenario.dilution_percent !== null ? (
                        <span className="text-[10px] text-muted">%</span>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <p className="text-[10px] leading-relaxed text-faint">
              0 mm is the &quot;no rain&quot; reference the other rows are read
              against, not an absent scenario. {sweep?.model_name}. Nothing here
              was recorded against this pan.
            </p>

            {(sweep?.assumptions.length ?? 0) > 0 ? (
              <details>
                <summary className="cursor-pointer text-[11px] text-muted">
                  What this model leaves out
                </summary>
                <ul className="mt-1.5 space-y-0.5">
                  {(sweep?.assumptions ?? []).map((assumption) => (
                    <li key={assumption} className="text-[11px] leading-relaxed text-muted">
                      {assumption}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </>
        )}
      </div>
    </ProvenanceCard>
  );
}
