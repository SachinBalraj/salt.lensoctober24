/**
 * PanDetailClient tests.
 *
 * The pan document carries a denormalised copy of its newest observation, and
 * Module 06 reads that copy to decide whether a measured salinity exists. The
 * copy is written by the server when Module 03 saves, so the pan has to be
 * re-read afterwards. These tests exist because that re-read is easy to drop
 * without anything failing loudly: Module 03 keeps its own copy of the latest
 * reading and Module 05 re-reads its own join, so both report the new
 * measurement while the simulator — reading the pan fetched when the page
 * opened — still claims nothing has been measured.
 */

import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import PanDetailClient from "@/components/pans/PanDetailClient";
import type { SaltPan } from "@/lib/pans";
import { getPan } from "@/lib/pansApi";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/pansApi", () => ({
  getPan: vi.fn(),
  deactivatePan: vi.fn(),
}));

// The sibling panels are stubbed so the test exercises the one data flow it is
// about: what PanDetailClient hands SimulatorPanel. Rendering them for real
// would drag every module's network calls into a test about a pan prop.
vi.mock("@/components/observations/ObservationPanel", () => ({
  __esModule: true,
  default: ({ onEvidenceRecorded }: { onEvidenceRecorded?: () => void }) => (
    <button type="button" data-testid="m03-record" onClick={() => onEvidenceRecorded?.()}>
      Module 03
    </button>
  ),
}));
vi.mock("@/components/weather/WeatherPanel", () => ({
  __esModule: true,
  default: () => null,
}));
vi.mock("@/components/twin/DigitalTwinPanel", () => ({
  __esModule: true,
  default: () => null,
}));
vi.mock("@/components/risk/RiskPredictionPanel", () => ({
  __esModule: true,
  default: () => null,
}));
vi.mock("@/components/decisions/DecisionPanel", () => ({
  __esModule: true,
  default: () => null,
}));
vi.mock("@/components/outcomes/OutcomePanel", () => ({
  __esModule: true,
  default: () => null,
}));

const getPanMock = vi.mocked(getPan);

const PAN_ID = "test-pan-003";

function makePan(
  latest_observation: SaltPan["latest_observation"],
): SaltPan {
  return {
    id: "653f1c0e9a2b4c00018f0001",
    pan_id: PAN_ID,
    pan_name: "Test pan 003",
    location: "Marakkanam, Tamil Nadu",
    latitude: 8.7642,
    longitude: 78.1348,
    area_m2: 12500,
    normal_depth_cm: 20,
    target_harvest_salinity_g_l: 285,
    farmer_name: null,
    farmer_whatsapp_masked: null,
    is_active: true,
    created_at: "2026-01-05T08:00:00Z",
    updated_at: "2026-10-15T02:30:00Z",
    deactivated_at: null,
    latest_observation,
  };
}

/** The measurement Module 03 writes: chosen as "A measurement", so `manual`. */
const MEASURED: SaltPan["latest_observation"] = {
  observation_id: "obs-da2a3f48661e4263962abad807838105",
  timestamp: "2026-10-15T02:30:00Z",
  salinity_g_l: 300,
  water_depth_cm: 20,
  brine_temperature_c: 30,
  humidity_percent: 65,
  ambient_temperature_c: 30,
  data_source: "manual",
};

beforeEach(() => {
  getPanMock.mockReset();
});

describe("a salinity recorded in Module 03 on the same page", () => {
  it("re-reads the pan so Module 06 recognises the measurement", async () => {
    // Page opens on a pan that has no reading yet, then the same pan as the
    // server returns it once Module 03's save has been written through.
    getPanMock
      .mockResolvedValueOnce(makePan(null))
      .mockResolvedValueOnce(makePan(MEASURED));

    render(<PanDetailClient panId={PAN_ID} />);

    // Before the save there is genuinely nothing measured, so the simulator says
    // so and leaves the field blank.
    await waitFor(() =>
      expect(
        screen.getByText(/No salinity has been measured for this pan/),
      ).toBeInTheDocument(),
    );
    expect(screen.getByLabelText("Current salinity (C0)")).toHaveValue("");

    // Module 03 records the reading and signals the shared evidence token.
    fireEvent.click(screen.getByTestId("m03-record"));

    // The pan is re-read, and Module 06 now seeds from the measurement rather
    // than from the pan as it was when the page opened.
    await waitFor(() =>
      expect(screen.getByLabelText("Current salinity (C0)")).toHaveValue("300"),
    );
    expect(
      screen.queryByText(/No salinity has been measured for this pan/),
    ).toBeNull();
    expect(
      screen.getByText(/From the newest recorded observation \(manual\)/),
    ).toBeInTheDocument();
  });

  it("re-reads the pan for every save, not only the first", async () => {
    getPanMock
      .mockResolvedValueOnce(makePan(null))
      .mockResolvedValueOnce(makePan(MEASURED))
      .mockResolvedValueOnce(
        makePan({ ...MEASURED, observation_id: "obs-second", salinity_g_l: 295 }),
      );

    render(<PanDetailClient panId={PAN_ID} />);
    await waitFor(() => screen.getByTestId("m03-record"));

    fireEvent.click(screen.getByTestId("m03-record"));
    await waitFor(() =>
      expect(screen.getByLabelText("Current salinity (C0)")).toHaveValue("300"),
    );

    fireEvent.click(screen.getByTestId("m03-record"));
    await waitFor(() =>
      expect(screen.getByLabelText("Current salinity (C0)")).toHaveValue("295"),
    );
  });

  it("keeps the pan on screen when the re-read fails", async () => {
    getPanMock
      .mockResolvedValueOnce(makePan(null))
      .mockRejectedValueOnce(new Error("network down"));

    render(<PanDetailClient panId={PAN_ID} />);
    await waitFor(() => screen.getByTestId("m03-record"));

    fireEvent.click(screen.getByTestId("m03-record"));

    // The skeleton is gated on the first load only, so a failed re-read must not
    // blank the page: the panels stay mounted and the last good pan is kept.
    await waitFor(() => expect(getPanMock).toHaveBeenCalledTimes(2));
    expect(screen.getByTestId("m03-record")).toBeInTheDocument();
    expect(
      screen.getByText(/No salinity has been measured for this pan/),
    ).toBeInTheDocument();
  });
});
