import { cloneElement, type ReactElement } from "react";
import { render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import SimulationChart from "@/components/simulator/SimulationChart";
import { buildDilutionSeries, type RainfallScenario } from "@/lib/simulation";

/**
 * The chart sizes itself from its parent, and jsdom reports every parent as
 * 0x0, so a ResponsiveContainer left alone renders an empty svg. Giving it fixed
 * dimensions is what lets these tests read the axis the operator reads.
 */
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement<Record<string, unknown>> }) =>
      cloneElement(children, { width: 600, height: 300 }),
  };
});

/**
 * The September north pan: 12,300 m² holding 0.20 m of 250 g/L brine. Every
 * salinity here is the mass balance's own answer, C1 = C0 x D0 / (D0 + R), and
 * the 25 mm depth is the one in force as the forecast.
 */
const NORTH_PAN_SEPTEMBER: RainfallScenario[] = [
  { rainfall_mm: 0, post_rain_salinity_g_l: 250.0, dilution_percent: 0.0 },
  { rainfall_mm: 5, post_rain_salinity_g_l: 243.90243902439025, dilution_percent: 2.4390243902439022 },
  { rainfall_mm: 10, post_rain_salinity_g_l: 238.0952380952381, dilution_percent: 4.761904761904759 },
  { rainfall_mm: 20, post_rain_salinity_g_l: 227.27272727272728, dilution_percent: 9.090909090909088 },
  { rainfall_mm: 25, post_rain_salinity_g_l: 222.22222222222223, dilution_percent: 11.111111111111109 },
  { rainfall_mm: 50, post_rain_salinity_g_l: 200.0, dilution_percent: 20.0 },
  { rainfall_mm: 100, post_rain_salinity_g_l: 166.66666666666669, dilution_percent: 33.33333333333333 },
].map((overrides) => {
  const rainfall_mm = overrides.rainfall_mm;
  const salinity = overrides.post_rain_salinity_g_l;
  return {
    rainfall_mm,
    label: `${rainfall_mm} mm`,
    is_standard_preset: true,
    rainfall_volume_m3: 12_300 * (rainfall_mm / 1000),
    post_rain_volume_m3: 2460 + 12_300 * (rainfall_mm / 1000),
    post_rain_depth_m: 0.2 + rainfall_mm / 1000,
    post_rain_salinity_g_l: salinity,
    salinity_drop_g_l: 250 - salinity,
    dilution_percent: overrides.dilution_percent,
    dilution_percent_reason: null,
  };
});

/** One tick on the vertical axis: its salinity and where it was drawn. */
type Tick = { salinity: number; y: number };

/**
 * The vertical axis's ticks, ordered the way a reader walks it: bottom first.
 *
 * SVG measures y downward from the top, so the bottom of the plot is the
 * *larger* y. Ordering by descending y therefore walks the axis upward, which is
 * what makes an ascending salinity in the returned list the same claim as "the
 * axis increases from bottom to top" on screen.
 */
function yAxisTicks(container: HTMLElement): Tick[] {
  return Array.from(
    container.querySelectorAll(".recharts-cartesian-axis-tick-value"),
  )
    .filter((node) => node.getAttribute("orientation") === "left")
    .map((node) => ({
      // The label carries the unit, as it should on screen: "175 g/L".
      label: node.textContent ?? "",
      y: Number(node.getAttribute("y")),
    }))
    .map(({ label, y }) => ({ salinity: Number.parseFloat(label), y }))
    .sort((a, b) => b.y - a.y);
}

function unitLabels(container: HTMLElement): string[] {
  return Array.from(
    container.querySelectorAll(".recharts-cartesian-axis-tick-value"),
  )
    .filter((node) => node.getAttribute("orientation") === "left")
    .map((node) => node.textContent ?? "");
}

/** The salinity the axis maps a vertical pixel position to. */
function salinityAt(ticks: readonly Tick[], y: number): number {
  const bottom = ticks[0];
  const top = ticks[ticks.length - 1];
  const ratio = (bottom.y - y) / (bottom.y - top.y);
  return bottom.salinity + ratio * (top.salinity - bottom.salinity);
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("the rainfall simulation chart's salinity axis", () => {
  it("reads low at the bottom and high at the top", () => {
    const { container } = render(
      <SimulationChart scenarios={NORTH_PAN_SEPTEMBER} selectedRainfallMm={25} />,
    );
    const ticks = yAxisTicks(container);
    expect(ticks.length).toBeGreaterThan(2);

    const bottomToTop = ticks.map((tick) => tick.salinity);
    for (let index = 1; index < bottomToTop.length; index += 1) {
      expect(bottomToTop[index]).toBeGreaterThan(bottomToTop[index - 1]);
    }
  });

  it("labels every tick with the unit, not a bare number", () => {
    const { container } = render(
      <SimulationChart scenarios={NORTH_PAN_SEPTEMBER} selectedRainfallMm={25} />,
    );
    for (const label of unitLabels(container)) {
      expect(label).toMatch(/^\d+(\.\d+)? g\/L$/);
    }
  });

  it("ticks on round numbers a reader can name", () => {
    const { container } = render(
      <SimulationChart scenarios={NORTH_PAN_SEPTEMBER} selectedRainfallMm={25} />,
    );
    // Not 0 / 65 / 130 / 195 / 250, which is what a zero-anchored axis produces.
    expect(yAxisTicks(container).map((tick) => tick.salinity)).toEqual([
      150, 175, 200, 225, 250, 275,
    ]);
  });

  it("plots the 25 mm point at 222.22 g/L on the axis it is drawn against", () => {
    const { container } = render(
      <SimulationChart scenarios={NORTH_PAN_SEPTEMBER} selectedRainfallMm={25} />,
    );
    const ticks = yAxisTicks(container);
    const series = buildDilutionSeries(NORTH_PAN_SEPTEMBER, 25);
    const dots = Array.from(container.querySelectorAll(".recharts-line-dot"));

    expect(dots).toHaveLength(series.length);
    const forecastIndex = series.findIndex((point) => point.rainfall_mm === 25);
    const dot = dots[forecastIndex];
    const plotted = salinityAt(ticks, Number(dot.getAttribute("cy")));

    // The plotted position agrees with the mass balance to well under a tenth
    // of a g/L, which is the precision the axis can even show.
    expect(plotted).toBeCloseTo(222.22222222222223, 1);
  });

  it("rings the 25 mm point where the curve puts it", () => {
    const { container } = render(
      <SimulationChart scenarios={NORTH_PAN_SEPTEMBER} selectedRainfallMm={25} />,
    );
    const series = buildDilutionSeries(NORTH_PAN_SEPTEMBER, 25);
    const forecastIndex = series.findIndex((point) => point.rainfall_mm === 25);
    const dot = Array.from(container.querySelectorAll(".recharts-line-dot"))[forecastIndex];
    const ring = container.querySelector(".recharts-reference-dot-dot");

    expect(ring).not.toBeNull();
    // The marker has to sit on the line, not near it, or the forecast reads as a
    // different result from the curve.
    expect(Number(ring?.getAttribute("cy"))).toBeCloseTo(
      Number(dot.getAttribute("cy")),
      6,
    );
  });

  it("keeps the whole sweep inside the plot instead of against the top edge", () => {
    const { container } = render(
      <SimulationChart scenarios={NORTH_PAN_SEPTEMBER} selectedRainfallMm={25} />,
    );
    const dotYs = Array.from(container.querySelectorAll(".recharts-line-dot")).map(
      (node) => Number(node.getAttribute("cy")),
    );
    const ticks = yAxisTicks(container);
    const bottom = ticks[0].y;
    const top = ticks[ticks.length - 1].y;

    for (const y of dotYs) {
      // Larger y is lower on the screen, so a marker inside the plot has a y
      // between the top and bottom ticks. Sitting on either would clip it.
      expect(y).toBeGreaterThan(top);
      expect(y).toBeLessThan(bottom);
    }
  });
});
