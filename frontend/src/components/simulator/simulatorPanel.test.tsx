/**
 * SimulatorPanel (Module 06) tests.
 *
 * The panel seeds the two geometry inputs from registration and the starting
 * salinity from the pan's newest *eligible* observation — one that was actually
 * measured or operator-reported, never a demonstration. When no eligible
 * measurement exists the field stays blank and must still accept a hypothetical
 * value typed by hand, because the balance needs a starting C0 and the panel
 * will not borrow one from a design target.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import SimulatorPanel from "@/components/simulator/SimulatorPanel";
import type { SaltPan } from "@/lib/pans";
import { sweepRainfallScenarios } from "@/lib/simulationApi";

vi.mock("@/lib/simulationApi", () => ({
  sweepRainfallScenarios: vi.fn(),
}));

const sweepMock = vi.mocked(sweepRainfallScenarios);

beforeEach(() => {
  sweepMock.mockClear();
});

function makePan(
  overrides: Partial<SaltPan> & {
    latest_observation?: SaltPan["latest_observation"];
  } = {},
): SaltPan {
  return {
    id: "pan-1",
    pan_id: "harvest-test-001",
    pan_name: "Harvest test pan",
    location: "Marakkanam, Tamil Nadu",
    latitude: 8.7642,
    longitude: 78.1348,
    area_m2: 12500,
    normal_depth_cm: 20,
    target_harvest_salinity_g_l: 285.4,
    farmer_name: null,
    farmer_whatsapp_masked: null,
    is_active: true,
    created_at: "2026-01-05T08:00:00Z",
    updated_at: "2026-10-09T04:30:00Z",
    deactivated_at: null,
    latest_observation: {
      observation_id: "obs-4fdf563f80d44724899a31f9c75a57eb",
      timestamp: "2026-10-09T04:30:00Z",
      salinity_g_l: 290,
      water_depth_cm: 20,
      brine_temperature_c: 32,
      humidity_percent: 68,
      ambient_temperature_c: 34,
      data_source: "manual",
    },
    ...overrides,
  };
}

describe("what the form is seeded from", () => {
  it("seeds the current salinity from a real, operator-reported measurement", () => {
    render(<SimulatorPanel pan={makePan()} />);
    expect(screen.getByLabelText("Current salinity (C0)")).toHaveValue("290");
    // The geometry inputs seed from registration.
    expect(screen.getByLabelText("Pan area (A)")).toHaveValue("12500");
    expect(screen.getByLabelText("Brine depth (D0)")).toHaveValue("20");
    // A measured value present means no "nothing measured" warning.
    expect(
      screen.queryByText(/No salinity has been measured for this pan/),
    ).toBeNull();
  });

  it("leaves the current salinity blank when only a demonstration observation exists", () => {
    render(
      <SimulatorPanel
        pan={makePan({
          latest_observation: {
            ...makePan().latest_observation!,
            observation_id: "obs-demo-1",
            data_source: "demonstration",
          },
        })}
      />,
    );
    // A demonstration is not a measurement, so it is not treated as one.
    expect(screen.getByLabelText("Current salinity (C0)")).toHaveValue("");
    expect(
      screen.getByText(/No salinity has been measured for this pan/),
    ).toBeTruthy();
  });

  it("leaves the current salinity blank when no observation has been recorded", () => {
    render(<SimulatorPanel pan={makePan({ latest_observation: null })} />);
    expect(screen.getByLabelText("Current salinity (C0)")).toHaveValue("");
    expect(
      screen.getByText(/No salinity has been measured for this pan/),
    ).toBeTruthy();
  });
});

describe("manually entering a salinity", () => {
  it("accepts keyboard input into the current salinity field", () => {
    render(<SimulatorPanel pan={makePan({ latest_observation: null })} />);
    const field = screen.getByLabelText("Current salinity (C0)");
    fireEvent.focus(field);
    // Character by character, the way a person actually types. If the field
    // were remounting or re-seeding on every keystroke, "290" would never land.
    fireEvent.change(field, { target: { value: "2" } });
    expect(field).toHaveValue("2");
    fireEvent.change(field, { target: { value: "29" } });
    expect(field).toHaveValue("29");
    fireEvent.change(field, { target: { value: "290" } });
    expect(field).toHaveValue("290");
  });

  it("sends the manually typed salinity to the mass balance", async () => {
    sweepMock.mockResolvedValue({
      area_m2: 12500,
      initial_depth_m: 0.2,
      initial_salinity_g_l: 290,
      initial_volume_m3: 2500,
      standard_scenarios_mm: [0, 5, 10, 20, 50, 100],
      scenarios: [],
      is_simulated: true,
      model_name: "rainfall-dilution-mass-balance-v1",
      disclaimer:
        "Physics-based simulation under simplifying assumptions; not an ML prediction.",
      assumptions: [],
      equations: [],
    });

    const pan = makePan({ latest_observation: null });
    render(<SimulatorPanel pan={pan} />);
    const area = screen.getByLabelText("Pan area (A)");
    const depth = screen.getByLabelText("Brine depth (D0)");
    const salinity = screen.getByLabelText("Current salinity (C0)");

    fireEvent.change(area, { target: { value: "12500" } });
    fireEvent.change(depth, { target: { value: "20" } });
    fireEvent.change(salinity, { target: { value: "290" } });

    fireEvent.click(screen.getByRole("button", { name: /run mass balance/i }));

    await waitFor(() =>
      expect(sweepMock).toHaveBeenCalledWith(
        expect.objectContaining({
          area_m2: 12500,
          initial_depth_m: 0.2,
          initial_salinity_g_l: 290,
        }),
      ),
    );
  });

  it("refuses a salinity field that is empty, text, or not greater than zero", async () => {
    const pan = makePan({ latest_observation: null });
    const { rerender } = render(<SimulatorPanel pan={pan} />);
    const salinity = screen.getByLabelText("Current salinity (C0)");
    const area = screen.getByLabelText("Pan area (A)");
    const depth = screen.getByLabelText("Brine depth (D0)");

    // Empty salinity: the run is refused and nothing is requested.
    fireEvent.click(screen.getByRole("button", { name: /run mass balance/i }));
    expect(
      await screen.findByText(/Enter the current salinity/i),
    ).toBeTruthy();
    expect(sweepMock).not.toHaveBeenCalled();

    // Non-numeric text.
    fireEvent.change(salinity, { target: { value: "brine" } });
    fireEvent.click(screen.getByRole("button", { name: /run mass balance/i }));
    expect(
      await screen.findByText(/Current salinity must be a number/i),
    ).toBeTruthy();
    expect(sweepMock).not.toHaveBeenCalled();

    // Zero is not a valid starting brine for a what-if.
    fireEvent.change(salinity, { target: { value: "0" } });
    fireEvent.change(area, { target: { value: "12500" } });
    fireEvent.change(depth, { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: /run mass balance/i }));
    expect(
      await screen.findByText(/Enter a salinity greater than zero/i),
    ).toBeTruthy();
    expect(sweepMock).not.toHaveBeenCalled();

    // A typo in another field still surfaces next to that field.
    rerender(<SimulatorPanel pan={pan} />);
    fireEvent.change(screen.getByLabelText("Brine depth (D0)"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByRole("button", { name: /run mass balance/i }));
    expect(
      await screen.findByText(/no salinity to dilute/i),
    ).toBeTruthy();
  });
});