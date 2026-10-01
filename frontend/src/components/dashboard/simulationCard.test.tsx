import { cloneElement, type ReactElement } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SimulationCard } from "@/components/dashboard/SimulationCard";
import type { DigitalTwinState } from "@/lib/digitalTwin";
import type { SaltPan } from "@/lib/pans";
import type { RainfallSweep } from "@/lib/simulation";

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

const PAN: SaltPan = {
  id: "pan-1",
  pan_id: "pan_orginal",
  pan_name: "north pan",
  location: "KZN",
  latitude: null,
  longitude: null,
  area_m2: 12_300,
  normal_depth_cm: 20,
  target_harvest_salinity_g_l: 250,
  is_active: true,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-30T00:00:00Z",
  deactivated_at: null,
  latest_observation: null,
};

const TWIN: DigitalTwinState = {
  twin_id: "twin-1",
  pan_id: PAN.pan_id,
  pan_name: PAN.pan_name,
  timestamp: "2026-09-30T06:00:00Z",
  provenance: "observed",
  twin_version: "0.1.0",
  salinity_g_l: 250,
  water_depth_cm: 20,
  brine_temperature_c: 27,
  ambient_temperature_c: 22,
  humidity_percent: 55,
  pan_area_m2: 12_300,
  location: "KZN",
  forecast_rainfall_mm: 25,
  rainfall_probability_percent: 70,
  observation: null,
  weather: null,
  weather_forecast: null,
  is_complete: true,
  missing_inputs: [],
  missing_fields: [],
  quantities: ["salinity_g_l", "water_depth_cm"],
};

/**
 * The September sweep the server returns for this pan.
 *
 * Every salinity is `C1 = C0 x D0 / (D0 + R)` on 0.20 m of 250 g/L brine, and the
 * 25 mm row is the depth in force as the forecast, swept as an extra scenario so
 * the ringed marker is a point the server genuinely evaluated.
 */
const SWEEP: RainfallSweep = (() => {
  const postRainSalinity = (rainfallMm: number) => (250 * 200) / (200 + rainfallMm);
  const scenarios = [0, 5, 10, 20, 25, 50, 100].map((rainfall_mm) => {
    const post_rain_salinity_g_l = postRainSalinity(rainfall_mm);
    return {
      rainfall_mm,
      label: `${rainfall_mm} mm`,
      is_standard_preset: rainfall_mm !== 25,
      rainfall_volume_m3: 12_300 * (rainfall_mm / 1000),
      post_rain_volume_m3: 2460 + 12_300 * (rainfall_mm / 1000),
      post_rain_depth_m: 0.2 + rainfall_mm / 1000,
      post_rain_salinity_g_l,
      salinity_drop_g_l: 250 - post_rain_salinity_g_l,
      dilution_percent: ((250 - post_rain_salinity_g_l) / 250) * 100,
      dilution_percent_reason: null,
    };
  });
  return {
    area_m2: 12_300,
    initial_depth_m: 0.2,
    initial_salinity_g_l: 250,
    initial_volume_m3: 2460,
    standard_scenarios_mm: [0, 5, 10, 20, 50, 100],
    scenarios,
    is_simulated: true,
    model_name: "rainfall-dilution-mass-balance-v1",
    disclaimer: "Physics-based simulation under simplifying assumptions; not an ML prediction.",
    assumptions: ["Complete mixing."],
    equations: ["C1 = C0 x V0 / V1"],
  };
})();

/**
 * The vertical axis's ticks, ordered the way a reader walks it: bottom first.
 *
 * SVG measures y downward from the top, so the bottom of the plot is the
 * *larger* y. Ordering by descending y therefore walks the axis upward, which is
 * what makes an ascending salinity in the returned list the same claim as "the
 * axis increases from bottom to top" on screen.
 */
function yAxisSalinities(container: HTMLElement): number[] {
  return Array.from(container.querySelectorAll(".recharts-cartesian-axis-tick-value"))
    .filter((node) => node.getAttribute("orientation") === "left")
    .map((node) => ({ y: Number(node.getAttribute("y")), salinity: Number.parseFloat(node.textContent ?? "") }))
    .sort((a, b) => b.y - a.y)
    .map((tick) => tick.salinity);
}

function yAxisLabels(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll(".recharts-cartesian-axis-tick-value"))
    .filter((node) => node.getAttribute("orientation") === "left")
    .map((node) => node.textContent ?? "");
}

function renderCard() {
  return render(<SimulationCard pan={PAN} twin={TWIN} sweep={SWEEP} />);
}

describe("the dashboard's rainfall simulation", () => {
  it("states the 25 mm result in salinity and dilution", () => {
    renderCard();
    // 250 x 200 / 225 = 222.22 g/L, an 11.11% dilution.
    expect(screen.getByText("222.2")).toBeInTheDocument();
    expect(screen.getByText("11.1")).toBeInTheDocument();
    expect(screen.getAllByText("g/L").length).toBeGreaterThan(0);
  });

  it("rings the 25 mm depth on the curve", () => {
    const { container } = renderCard();
    expect(screen.getByText("Forecast 25 mm ringed")).toBeInTheDocument();
    expect(container.querySelector(".recharts-reference-dot-dot")).not.toBeNull();
  });

  it("draws a salinity axis that increases from bottom to top", () => {
    const { container } = renderCard();
    const bottomToTop = yAxisSalinities(container);
    expect(bottomToTop.length).toBeGreaterThan(2);
    for (let index = 1; index < bottomToTop.length; index += 1) {
      expect(bottomToTop[index]).toBeGreaterThan(bottomToTop[index - 1]);
    }
  });

  it("ticks on round salinities carrying the unit", () => {
    const { container } = renderCard();
    // Not 0 / 65 / 130 / 195 / 250, which is what a zero-anchored axis produces.
    expect(yAxisSalinities(container)).toEqual([150, 175, 200, 225, 250, 275]);
    for (const label of yAxisLabels(container)) {
      expect(label).toMatch(/^\d+(\.\d+)? g\/L$/);
    }
  });

  it("plots the 25 mm point where the mass balance puts it", () => {
    const { container } = renderCard();
    const ticks = Array.from(container.querySelectorAll(".recharts-cartesian-axis-tick-value"))
      .filter((node) => node.getAttribute("orientation") === "left")
      .map((node) => ({ y: Number(node.getAttribute("y")), salinity: Number.parseFloat(node.textContent ?? "") }))
      .sort((a, b) => b.y - a.y);
    const bottom = ticks[0];
    const top = ticks[ticks.length - 1];

    // The 25 mm scenario is the fifth of the seven, so the fifth marker is it.
    const dots = Array.from(container.querySelectorAll(".recharts-line-dot"));
    expect(dots).toHaveLength(7);
    const y = Number(dots[4].getAttribute("cy"));
    const plotted = bottom.salinity + ((bottom.y - y) / (bottom.y - top.y)) * (top.salinity - bottom.salinity);

    expect(plotted).toBeCloseTo(222.22222222222223, 1);
  });
});
