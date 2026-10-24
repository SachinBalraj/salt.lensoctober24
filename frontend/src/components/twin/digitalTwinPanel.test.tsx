/**
 * DigitalTwinPanel tests.
 *
 * The panel is the Module 05 view on the pan detail page, where Module 03
 * (observations) and Module 04 (weather) sit directly above it. Those forms
 * write records the twin must then cite, so the panel re-reads the join when
 * they save — without storing a snapshot, because a save on another panel must
 * never manufacture twin history.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import DigitalTwinPanel from "@/components/twin/DigitalTwinPanel";
import {
  getTwinState,
  listTwinHistory,
  refreshTwinState,
} from "@/lib/digitalTwinApi";
import {
  TWIN_DISPLAY_QUANTITIES,
  type DigitalTwinHistoryResponse,
  type DigitalTwinRefreshResponse,
  type DigitalTwinState,
} from "@/lib/digitalTwin";

vi.mock("@/lib/digitalTwinApi", () => ({
  getTwinState: vi.fn(),
  listTwinHistory: vi.fn(),
  refreshTwinState: vi.fn(),
}));

const getTwinMock = vi.mocked(getTwinState);
const listHistoryMock = vi.mocked(listTwinHistory);
const refreshMock = vi.mocked(refreshTwinState);

const PAN = {
  id: "pan-1",
  pan_id: "north-pan-002",
  pan_name: "North Evaporation Pan",
  location: "Marakkanam, Tamil Nadu",
  latitude: 12.2,
  longitude: 79.9,
  area_m2: 12500,
} as Parameters<typeof DigitalTwinPanel>[0]["pan"];

/** A twin carrying one observation and one weather record, but no forecast. */
function makeTwin(overrides: Partial<DigitalTwinState> = {}): DigitalTwinState {
  return {
    twin_id: "twin-1",
    pan_id: "north-pan-002",
    pan_name: "North Evaporation Pan",
    timestamp: "2026-10-08T12:00:00Z",
    provenance: "observed",
    twin_version: "0.1.0",
    salinity_g_l: 270,
    water_depth_cm: 16.2,
    brine_temperature_c: 28.4,
    ambient_temperature_c: 28.9,
    humidity_percent: 76,
    pan_area_m2: 12500,
    location: "Marakkanam, Tamil Nadu",
    forecast_rainfall_mm: null,
    rainfall_probability_percent: null,
    observation: {
      id: "obs-1",
      timestamp: "2026-10-01T02:30:00Z",
      source: "manual",
      age_seconds: 662213,
    },
    weather: {
      id: "wx-1",
      timestamp: "2026-10-08T11:00:00Z",
      source: "weather_api",
      age_seconds: 953,
    },
    weather_forecast: null,
    is_complete: false,
    missing_inputs: [
      {
        field: "forecast_rainfall_mm",
        reason:
          "No weather forecast has been recorded for this pan, so forecast rainfall is unavailable.",
        expected_from: "weather_forecast",
      },
      {
        field: "rainfall_probability_percent",
        reason:
          "No weather forecast has been recorded for this pan, so rainfall probability is unavailable.",
        expected_from: "weather_forecast",
      },
    ],
    missing_fields: ["forecast_rainfall_mm", "rainfall_probability_percent"],
    quantities: [...TWIN_DISPLAY_QUANTITIES],
    ...overrides,
  };
}

/** A twin assembled before anything was recorded: every input absent. */
function emptyTwin(): DigitalTwinState {
  return {
    ...makeTwin(),
    salinity_g_l: null,
    water_depth_cm: null,
    brine_temperature_c: null,
    ambient_temperature_c: null,
    humidity_percent: null,
    observation: null,
    weather: null,
    weather_forecast: null,
    missing_inputs: [
      {
        field: "salinity_g_l",
        reason:
          "No pan observation has been recorded for this pan, so brine salinity is unavailable.",
        expected_from: "observation",
      },
    ],
  };
}

function makeHistory(
  total = 0,
  items: DigitalTwinState[] = [],
): DigitalTwinHistoryResponse {
  return {
    pan_id: "north-pan-002",
    items,
    total,
    limit: 50,
    offset: 0,
    earliest_timestamp: null,
    latest_timestamp: null,
  };
}

/** A complete twin: observation, observed weather and a forecast in force. */
function completeTwin(
  overrides: Partial<DigitalTwinState> = {},
): DigitalTwinState {
  return makeTwin({
    twin_id: "twin-read",
    is_complete: true,
    forecast_rainfall_mm: 0.9,
    rainfall_probability_percent: 53,
    weather_forecast: {
      id: "fc-1",
      timestamp: "2026-10-08T11:30:00Z",
      source: "weather_api",
      age_seconds: 900,
    },
    missing_inputs: [],
    missing_fields: [],
    ...overrides,
  });
}

describe("what the panel shows", () => {
  it("cites the observation and weather the read found", async () => {
    getTwinMock.mockResolvedValue(makeTwin());
    listHistoryMock.mockResolvedValue(makeHistory());
    render(<DigitalTwinPanel pan={PAN} />);

    await waitFor(() => expect(getTwinMock).toHaveBeenCalledWith("north-pan-002"));
    expect(await screen.findByText("270.0")).toBeTruthy();
    expect(await screen.findByText("28.9")).toBeTruthy();
    // The weather record's provenance is shown, not assumed.
    expect(screen.getByText(/Source: Weather API/)).toBeTruthy();
    // Refused: a real pan is not being claimed to have no record at all.
    expect(
      screen.queryByText(/No pan observation has been recorded for this pan/),
    ).toBeNull();
    expect(
      screen.queryByText(/No weather observation has been recorded for this pan/),
    ).toBeNull();
    // The forecast genuinely is unrecorded, and that fact is not hidden — it is
    // both the summary's headline and each affected quantity's reason.
    expect(
      screen.getAllByText(/No weather forecast has been recorded/).length,
    ).toBeGreaterThan(0);
  });

  it("keeps an empty state truthful when the read finds no records", async () => {
    getTwinMock.mockResolvedValue(emptyTwin());
    listHistoryMock.mockResolvedValue(makeHistory());
    render(<DigitalTwinPanel pan={PAN} />);

    // Two places state the same plain fact: the summary and each quantity's reason.
    expect(
      (await screen.findAllByText(/No pan observation has been recorded for this pan/))
        .length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(/No weather observation has been recorded/)).toBeTruthy();
  });
});

describe("re-reading when evidence changes", () => {
  it("asks again when the token the panels above bump has moved", async () => {
    getTwinMock
      .mockResolvedValueOnce(emptyTwin())
      .mockResolvedValue(makeTwin());
    listHistoryMock.mockResolvedValue(makeHistory());

    const { rerender } = render(<DigitalTwinPanel pan={PAN} evidenceToken={0} />);
    expect(
      (await screen.findAllByText(/No pan observation has been recorded for this pan/))
        .length,
    ).toBeGreaterThan(0);

    // Forget the reads that happened while the page was settling, then record an
    // observation above; the parent bumps the token and the panel must re-read.
    getTwinMock.mockClear();
    rerender(<DigitalTwinPanel pan={PAN} evidenceToken={1} />);

    await waitFor(() => expect(getTwinMock).toHaveBeenCalled());
    expect(getTwinMock).toHaveBeenLastCalledWith("north-pan-002");
    expect(await screen.findByText("270.0")).toBeTruthy();
    expect(
      screen.queryAllByText(/No pan observation has been recorded for this pan/),
    ).toHaveLength(0);

    // The re-read must not have stored a snapshot: another panel's save does not
    // manufacture twin history.
    expect(refreshMock).not.toHaveBeenCalled();
  });
});

describe("when the displayed twin has not been persisted", () => {
  it("surfaces a clear action to assemble and persist the twin before advising", async () => {
    const read = completeTwin();
    const stored = completeTwin({ twin_id: "twin-stored" });
    const history = makeHistory(0, []);
    const historyAfterPersist = makeHistory(1, [stored]);

    getTwinMock.mockResolvedValue(read);
    // First read: nothing stored yet. After the refresh, the snapshot is what
    // was just stored.
    listHistoryMock.mockResolvedValueOnce(history);
    listHistoryMock.mockResolvedValue(historyAfterPersist);

    const refreshResult: DigitalTwinRefreshResponse = {
      state: stored,
      stored: true,
      changed: true,
      previous_twin_id: null,
    };
    refreshMock.mockResolvedValue(refreshResult);

    render(<DigitalTwinPanel pan={PAN} />);

    // The panel explains the dependency and offers the action to persist.
    expect(await screen.findByText("This twin has not been stored")).toBeInTheDocument();
    const persistButton = screen.getAllByRole("button", {
      name: /Refresh & persist twin/i,
    })[0];
    expect(persistButton).toBeInTheDocument();
    const historyReadsBefore = listHistoryMock.mock.calls.length;

    fireEvent.click(persistButton);
    await waitFor(() => expect(refreshMock).toHaveBeenCalledWith("north-pan-002"));

    // After a successful refresh the history is re-read against the state that
    // was just stored, so the twin on screen is recognised as persisted — the
    // backend step that stops the no_twin_state refusal.
    await waitFor(() =>
      expect(listHistoryMock.mock.calls.length).toBeGreaterThan(historyReadsBefore),
    );
    expect(listHistoryMock).toHaveBeenLastCalledWith("north-pan-002", {
      limit: 50,
    });
    expect(screen.queryByText("This twin has not been stored")).toBeNull();
    // History now reports the stored snapshot rather than an empty log.
    expect(await screen.findByText("1 snapshot")).toBeDefined();
    expect(
      screen.queryAllByRole("button", { name: /Refresh & persist twin/i }),
    ).not.toHaveLength(0);
  });

  it("does not prompt to persist an already stored twin", async () => {
    const read = completeTwin();
    const stored = completeTwin({ twin_id: "twin-stored" });

    getTwinMock.mockResolvedValue(read);
    listHistoryMock.mockResolvedValue(makeHistory(1, [stored]));

    render(<DigitalTwinPanel pan={PAN} />);

    expect(await screen.findAllByText("Complete")).not.toHaveLength(0);
    expect(
      screen.queryByText("This twin has not been stored"),
    ).toBeNull();
  });
});