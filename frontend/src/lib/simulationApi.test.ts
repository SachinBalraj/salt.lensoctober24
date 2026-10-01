import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { simulateMassBalance, sweepRainfallScenarios } from "@/lib/simulationApi";
import {
  MASS_BALANCE_EQUATIONS,
  SIMULATOR_DISCLAIMER,
  SIMULATION_ASSUMPTIONS,
  STANDARD_RAINFALL_SCENARIOS_MM,
  type MassBalanceResult,
  type RainfallSweep,
} from "@/lib/simulation";

const INPUTS = {
  area_m2: 12_500.0,
  initial_depth_m: 0.155,
  initial_salinity_g_l: 285.0,
};

function sweep(overrides: Partial<RainfallSweep> = {}): RainfallSweep {
  return {
    area_m2: 12_500.0,
    initial_depth_m: 0.155,
    initial_salinity_g_l: 285.0,
    initial_volume_m3: 1937.5,
    standard_scenarios_mm: [...STANDARD_RAINFALL_SCENARIOS_MM],
    scenarios: [],
    is_simulated: true,
    model_name: "rainfall-dilution-mass-balance-v1",
    disclaimer: SIMULATOR_DISCLAIMER,
    assumptions: SIMULATION_ASSUMPTIONS,
    equations: MASS_BALANCE_EQUATIONS,
    ...overrides,
  };
}

function result(overrides: Partial<MassBalanceResult> = {}): MassBalanceResult {
  return {
    ...INPUTS,
    rainfall_mm: 20.0,
    initial_volume_m3: 1937.5,
    rainfall_volume_m3: 250.0,
    post_rain_volume_m3: 2187.5,
    initial_salt_mass_kg: 552_187.5,
    post_rain_salt_mass_kg: 552_187.5,
    salt_mass_conserved: true,
    post_rain_salinity_g_l: 252.42857142857142,
    salinity_drop_g_l: 32.571428571428584,
    dilution_percent: 11.428571428571432,
    dilution_percent_reason: null,
    post_rain_depth_m: 0.175,
    is_simulated: true,
    model_name: "rainfall-dilution-mass-balance-v1",
    disclaimer: SIMULATOR_DISCLAIMER,
    assumptions: SIMULATION_ASSUMPTIONS,
    equations: MASS_BALANCE_EQUATIONS,
    ...overrides,
  };
}

function mockFetch(body: unknown, init: { status?: number } = {}) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: (init.status ?? 200) < 400,
    status: init.status ?? 200,
    text: async () => (body === undefined ? "" : JSON.stringify(body)),
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function sentBody(fetchMock: ReturnType<typeof vi.fn>): Record<string, unknown> {
  return JSON.parse(fetchMock.mock.calls[0][1].body as string);
}

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://api.test");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("sweeping the rainfall scenarios", () => {
  it("posts the three inputs and nothing else", async () => {
    const fetchMock = mockFetch(sweep());
    await sweepRainfallScenarios(INPUTS);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://api.test/api/simulator/rainfall-scenarios",
      expect.objectContaining({ method: "POST", cache: "no-store" }),
    );
    expect(sentBody(fetchMock)).toEqual(INPUTS);
  });

  it("sends custom depths only when there are some", async () => {
    const withoutCustom = mockFetch(sweep());
    await sweepRainfallScenarios(INPUTS);
    expect(sentBody(withoutCustom)).not.toHaveProperty("rainfall_scenarios_mm");

    const withCustom = mockFetch(sweep());
    await sweepRainfallScenarios({ ...INPUTS, rainfallScenariosMm: [12.5, 75] });
    expect(sentBody(withCustom)["rainfall_scenarios_mm"]).toEqual([12.5, 75]);
  });

  it("sends an empty custom list as absent rather than as []", async () => {
    const fetchMock = mockFetch(sweep());
    await sweepRainfallScenarios({ ...INPUTS, rainfallScenariosMm: [] });
    expect(sentBody(fetchMock)).not.toHaveProperty("rainfall_scenarios_mm");
  });

  it("carries no way for a caller to state a result", async () => {
    const fetchMock = mockFetch(sweep());
    await sweepRainfallScenarios({
      ...INPUTS,
      rainfallScenariosMm: [20],
    } as Parameters<typeof sweepRainfallScenarios>[0]);
    const body = sentBody(fetchMock);
    expect(Object.keys(body).sort()).toEqual([
      "area_m2",
      "initial_depth_m",
      "initial_salinity_g_l",
      "rainfall_scenarios_mm",
    ]);
  });

  it("returns the scenarios the server swept", async () => {
    mockFetch(sweep());
    const response = await sweepRainfallScenarios(INPUTS);
    expect(response.initial_volume_m3).toBe(1937.5);
    expect(response.standard_scenarios_mm).toEqual([0, 5, 10, 20, 50, 100]);
  });
});

describe("resolving a single rainfall event", () => {
  it("posts the inputs together with the rainfall depth", async () => {
    const fetchMock = mockFetch(result());
    await simulateMassBalance(INPUTS, 20);

    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://api.test/api/simulator/mass-balance",
    );
    expect(sentBody(fetchMock)).toEqual({ ...INPUTS, rainfall_mm: 20 });
  });

  it("sends a zero rainfall depth rather than omitting it", async () => {
    // 0 mm is a real scenario, so it must not be indistinguishable from absent.
    const fetchMock = mockFetch(result({ rainfall_mm: 0, dilution_percent: 0 }));
    await simulateMassBalance(INPUTS, 0);
    expect(sentBody(fetchMock)["rainfall_mm"]).toBe(0);
  });
});

describe("a result that could not be trusted", () => {
  it("is not presented as a result", async () => {
    mockFetch({ detail: "Unexpected error." }, { status: 500 });
    await expect(sweepRainfallScenarios(INPUTS)).rejects.toBeInstanceOf(ApiError);
  });

  it("explains a degenerate input in the server's own words", async () => {
    mockFetch(
      {
        detail:
          "Pan area must be greater than zero. Every volume in the balance is the product of the area and a depth, so a zero area leaves nothing to dilute.",
      },
      { status: 422 },
    );
    await expect(sweepRainfallScenarios({ ...INPUTS, area_m2: 0 })).rejects.toThrow(
      /Pan area must be greater than zero/,
    );
  });

  it("explains an unreachable backend", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("connection refused")));
    await expect(sweepRainfallScenarios(INPUTS)).rejects.toThrow(/Could not reach/);
  });
});
