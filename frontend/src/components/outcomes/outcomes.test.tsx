import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import OutcomePanel from "@/components/outcomes/OutcomePanel";
import PredictedVsActualView from "@/components/outcomes/PredictedVsActualView";
import { ApiError } from "@/lib/api";
import {
  MISSING_DISPLAY,
  type EventOutcome,
  type OutcomeComparisonSummary,
  type OutcomeErrorTerms,
  type OutcomeLink,
  type OutcomeLinkKind,
} from "@/lib/outcomes";
import { getOutcomeComparison, listOutcomes, recordOutcome } from "@/lib/outcomesApi";
import type { SaltPan } from "@/lib/pans";

vi.mock("@/lib/outcomesApi", () => ({
  listOutcomes: vi.fn(),
  getOutcomeComparison: vi.fn(),
  recordOutcome: vi.fn(),
  getOutcome: vi.fn(),
}));

const listMock = vi.mocked(listOutcomes);
const comparisonMock = vi.mocked(getOutcomeComparison);
const recordMock = vi.mocked(recordOutcome);

const PAN: SaltPan = {
  id: "db-1",
  pan_id: "PAN-001",
  pan_name: "North evaporation",
  location: "Plot A",
  latitude: null,
  longitude: null,
  area_m2: 4000,
  normal_depth_cm: 45,
  target_harvest_salinity_g_l: 280,
  is_active: true,
  created_at: "2026-01-04T09:00:00Z",
  updated_at: "2026-01-04T09:00:00Z",
  deactivated_at: null,
  latest_observation: null,
};

function statistics(overrides: Partial<OutcomeComparisonSummary["simulation_error"]> = {}) {
  return {
    label: "Predicted post-event salinity against the measured figure",
    unit: "g/L",
    compared: 0,
    not_comparable: 0,
    coverage_percent: null,
    mean_absolute_error: null,
    mean_absolute_error_reason: null,
    bias: null,
    bias_reason: null,
    largest_absolute_error: null,
    ...overrides,
  };
}

function summary(overrides: Partial<OutcomeComparisonSummary> = {}): OutcomeComparisonSummary {
  return {
    pan_id: "PAN-001",
    total_outcomes: 0,
    simulation_error: statistics(),
    forecast_simulation_error: statistics(),
    rainfall_forecast_error: statistics(),
    events_missing_actual_salinity: 0,
    events_with_operator_action: 0,
    ...overrides,
  };
}

function link(kind: OutcomeLinkKind, recordId: string | null, summary: string): OutcomeLink {
  return {
    kind,
    record_id: recordId,
    timestamp: recordId === null ? null : "2026-02-11T06:00:00Z",
    summary,
    is_resolved: recordId !== null,
    salinity_g_l: null,
    water_depth_cm: null,
    rainfall_mm: null,
    detail: null,
  };
}

function terms(overrides: Partial<OutcomeErrorTerms> = {}): OutcomeErrorTerms {
  return {
    predicted: null,
    actual: null,
    predicted_from: "actual_rainfall_mm",
    predicted_from_label: "actual rainfall",
    unit: "g/L",
    signed_error: null,
    absolute_error: null,
    percentage_error_percent: null,
    percentage_error_reason: null,
    is_comparable: false,
    missing_inputs: [],
    missing_fields: [],
    ...overrides,
  };
}

function outcome(overrides: Partial<EventOutcome> = {}): EventOutcome {
  return {
    outcome_id: "OUT-1",
    pan_id: "PAN-001",
    event_id: "EVT-2026-02-11",
    timestamp: "2026-02-11T06:30:00Z",
    created_at: "2026-02-12T08:00:00Z",
    forecast_rainfall_mm: 42,
    actual_rainfall_mm: 18,
    pre_event_salinity_g_l: 240,
    pre_event_depth_cm: 45,
    post_event_salinity_g_l: 236,
    post_event_depth_cm: 48,
    operator_action: "Partial harvest taken before the storm.",
    harvest_outcome: "partial",
    simulation_error: terms({
      predicted: 232.4,
      actual: 236,
      signed_error: -3.6,
      absolute_error: 3.6,
      percentage_error_percent: 1.5254237288,
      is_comparable: true,
    }),
    forecast_simulation_error: terms({
      predicted: 228.1,
      actual: 236,
      predicted_from: "forecast_rainfall_mm",
      predicted_from_label: "forecast rainfall",
      signed_error: -7.9,
      absolute_error: 7.9,
      percentage_error_percent: 3.3474576271,
      is_comparable: true,
    }),
    rainfall_forecast_error: terms({
      predicted: 42,
      actual: 18,
      predicted_from: "forecast_rainfall_mm",
      predicted_from_label: "forecast rainfall",
      unit: "mm",
      signed_error: 24,
      absolute_error: 24,
      percentage_error_percent: 133.3333333333,
      is_comparable: true,
    }),
    links: {
      pan_state: link("pan_state", "OBS-1", "240 g/L over 45 cm before the rain"),
      weather_forecast: link("weather_forecast", "FC-1", "42 mm forecast, issued 06:00"),
      simulation_run: link("simulation_run", "RUN-1", "Mass-balance run at 06:00"),
      recommendation: null,
      operator_decision: null,
    },
    has_actual_measurement: true,
    is_comparable: true,
    ...overrides,
  };
}

/**
 * The card that contains `element`.
 *
 * `Card` renders a `section`, not a `div`, so a `closest("div")` reaches past
 * the card and ends up scoping assertions to a grid that holds every tile.
 */
function cardOf(element: HTMLElement): HTMLElement {
  const card = element.closest("section");
  if (!card) throw new Error("Expected the element to sit inside a card.");
  return card as HTMLElement;
}

function emptyList() {
  return {
    items: [],
    total: 0,
    limit: 100,
    offset: 0,
    earliest_timestamp: null,
    latest_timestamp: null,
  };
}

beforeEach(() => {
  listMock.mockReset();
  comparisonMock.mockReset();
  recordMock.mockReset();
});

describe("OutcomePanel", () => {
  it("makes no request and shows no figures for a demo record", async () => {
    render(<OutcomePanel pan={PAN} isDemo />);

    await screen.findByRole("heading", { name: /record what actually happened/i });
    expect(listMock).not.toHaveBeenCalled();
    expect(comparisonMock).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /record outcome/i })).toBeDisabled();
    expect(
      screen.getByText(/nothing recorded for this pan yet/i),
    ).toBeInTheDocument();
  });

  it("shows the mean error with the count it was drawn from", async () => {
    listMock.mockResolvedValue({ ...emptyList(), items: [outcome()], total: 1 });
    comparisonMock.mockResolvedValue(
      summary({
        total_outcomes: 1,
        simulation_error: statistics({
          compared: 1,
          coverage_percent: 100,
          mean_absolute_error: 3.6,
          bias: -3.6,
          largest_absolute_error: 3.6,
        }),
      }),
    );

    render(<OutcomePanel pan={PAN} />);

    // The tiles render before the request resolves, so wait for the figure
    // rather than for the tile's label.
    const scope = cardOf(await screen.findByText("Mean absolute error"));
    await waitFor(() => {
      expect(within(scope).getByText("3.6 g/L")).toBeInTheDocument();
    });
    expect(within(scope).getByText(/Over 1 compared event/)).toBeInTheDocument();
  });

  it("keeps events with no measurement out of the mean and says how many", async () => {
    const waiting = outcome({
      outcome_id: "OUT-2",
      event_id: "EVT-2026-02-18",
      post_event_salinity_g_l: null,
      has_actual_measurement: false,
      is_comparable: false,
      simulation_error: terms({
        predicted: 231,
        is_comparable: false,
        missing_fields: ["post_event_salinity_g_l"],
        missing_inputs: [
          {
            field: "post_event_salinity_g_l",
            reason: "Not recorded when the outcome was filed.",
          },
        ],
      }),
    });
    listMock.mockResolvedValue({ ...emptyList(), items: [outcome(), waiting], total: 2 });
    comparisonMock.mockResolvedValue(
      summary({
        total_outcomes: 2,
        events_missing_actual_salinity: 1,
        simulation_error: statistics({
          compared: 1,
          not_comparable: 1,
          coverage_percent: 50,
          mean_absolute_error: 3.6,
          bias: -3.6,
          largest_absolute_error: 3.6,
        }),
      }),
    );

    render(<OutcomePanel pan={PAN} />);

    await screen.findByText(/some events are still waiting on a measurement/i);
    expect(screen.getByText(/1 of 2 listed event has no recorded/i)).toBeInTheDocument();
    const scope = cardOf(screen.getByText("Mean absolute error"));
    await waitFor(() => {
      expect(within(scope).getByText("3.6 g/L")).toBeInTheDocument();
    });
    expect(within(scope).getByText(/Over 1 compared event/)).toBeInTheDocument();
  });

  it("reports an unreachable backend without inventing an error figure", async () => {
    listMock.mockRejectedValue(
      new ApiError("Backend is unreachable.", { status: 0 }),
    );
    comparisonMock.mockRejectedValue(
      new ApiError("Backend is unreachable.", { status: 0 }),
    );

    render(<OutcomePanel pan={PAN} />);

    await waitFor(() => {
      expect(
        screen.getByText(/the recorded outcomes could not be reached/i),
      ).toBeInTheDocument();
    });
    expect(screen.getByText("Backend is unreachable.")).toBeInTheDocument();
    // No zero anywhere: an unreachable backend is not a pan with no outcomes.
    expect(screen.queryByText("Mean absolute error")).not.toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/nothing recorded for this pan yet/i),
    ).not.toBeInTheDocument();
  });

  it("shows the backend's refusal when an event is filed twice", async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValue(emptyList());
    comparisonMock.mockResolvedValue(summary({ total_outcomes: 0 }));
    recordMock.mockRejectedValue(
      new ApiError(
        "Event “storm-1” is already recorded for pan “PAN-001” (outcome OUT-1). " +
          "An event is measured once. If this measurement is wrong, record the " +
          "corrected event under a new event_id rather than replacing a figure an " +
          "earlier validation run may already have read.",
        { status: 409 },
      ),
    );

    render(<OutcomePanel pan={PAN} />);

    await user.type(screen.getByLabelText(/event name/i), "storm-1");
    await user.type(screen.getByLabelText(/salinity after the event/i), "236");
    await user.click(screen.getByRole("button", { name: /record outcome/i }));

    await waitFor(() => {
      expect(screen.getByText(/already recorded for pan/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/an event is measured once/i)).toBeInTheDocument();
    // A refusal is not a success: nothing is claimed to have been recorded.
    expect(screen.queryByText(/outcome recorded/i)).not.toBeInTheDocument();
  });
});

describe("PredictedVsActualView", () => {
  it("renders each error family with its coverage sentence", async () => {
    listMock.mockResolvedValue({ ...emptyList(), items: [outcome()], total: 1 });
    comparisonMock.mockResolvedValue(
      summary({
        pan_id: null,
        total_outcomes: 1,
        simulation_error: statistics({
          compared: 1,
          coverage_percent: 100,
          mean_absolute_error: 3.6,
          bias: -3.6,
          largest_absolute_error: 3.6,
        }),
      }),
    );

    render(<PredictedVsActualView />);

    await screen.findByText("Simulation mean absolute error");

    const compared = cardOf(screen.getByText("Simulation mean absolute error"));
    await waitFor(() => {
      expect(
        within(compared).getByText(/mean over 1 compared event/i),
      ).toBeInTheDocument();
    });
    expect(within(compared).getByText("3.6 g/L")).toBeInTheDocument();
    expect(within(compared).getByText(/every event in scope was compared/i)).toBeInTheDocument();

    // The other two families had nothing comparable and must say so rather than
    // showing a mean of zero.
    for (const label of [
      "Forecast + simulation mean absolute error",
      "Rainfall forecast mean absolute error",
    ]) {
      const scope = cardOf(screen.getByText(label));
      expect(within(scope).getByText(MISSING_DISPLAY)).toBeInTheDocument();
      expect(
        within(scope).getByText(/no event in scope could be compared/i),
      ).toBeInTheDocument();
      expect(within(scope).queryByText("0")).not.toBeInTheDocument();
    }
  });

  it("shows no accuracy at all when the backend is unreachable", async () => {
    listMock.mockRejectedValue(new ApiError("Backend is unreachable.", { status: 503 }));
    comparisonMock.mockRejectedValue(new ApiError("Backend is unreachable.", { status: 503 }));

    render(<PredictedVsActualView />);

    await waitFor(() => {
      expect(screen.getByText(/the comparison is unavailable/i)).toBeInTheDocument();
    });
    expect(screen.queryByText("Mean absolute error")).not.toBeInTheDocument();
    expect(screen.getByText(/no accuracy figure exists, and none is shown here in its place/i)).toBeInTheDocument();
  });

  it("filters by pan without changing the aggregates it was given", async () => {
    listMock.mockResolvedValue(emptyList());
    comparisonMock.mockResolvedValue(summary({ total_outcomes: 0 }));

    render(<PredictedVsActualView panId="PAN-001" />);

    await waitFor(() => {
      expect(comparisonMock).toHaveBeenCalledWith({ pan_id: "PAN-001" });
    });
    expect(listMock).toHaveBeenCalledWith({ pan_id: "PAN-001", limit: 100 });
    expect(await screen.findByText(/no outcomes recorded yet/i)).toBeInTheDocument();
  });

  it("says plainly when nothing is in scope, and reports zero events", async () => {
    listMock.mockResolvedValue(emptyList());
    comparisonMock.mockResolvedValue(summary({ total_outcomes: 0 }));

    render(<PredictedVsActualView />);

    expect(await screen.findByText(/no outcomes recorded yet/i)).toBeInTheDocument();
    // With nothing in scope the page says so outright rather than showing a
    // mean of zero over zero events.
    expect(screen.queryByText("Simulation mean absolute error")).not.toBeInTheDocument();
  });

  it("does not show a zero error for an unmeasured event", async () => {
    const unmeasured = outcome({
      post_event_salinity_g_l: null,
      has_actual_measurement: false,
      is_comparable: false,
      simulation_error: terms({
        predicted: 231,
        is_comparable: false,
        missing_fields: ["post_event_salinity_g_l"],
        missing_inputs: [
          { field: "post_event_salinity_g_l", reason: "Not recorded when the outcome was filed." },
        ],
      }),
    });
    listMock.mockResolvedValue({ ...emptyList(), items: [unmeasured], total: 1 });
    comparisonMock.mockResolvedValue(
      summary({
        total_outcomes: 1,
        events_missing_actual_salinity: 1,
        simulation_error: statistics({
          not_comparable: 1,
          coverage_percent: 0,
          mean_absolute_error_reason:
            "No event could be compared: every event in scope has no measurement.",
        }),
      }),
    );

    const user = userEvent.setup();
    render(<PredictedVsActualView />);

    await user.click(
      await screen.findByRole("button", {
        name: /show comparison and links for event EVT-2026-02-11/i,
      }),
    );
    const row = cardOf(
      await screen.findByText("Simulation, from the rainfall that fell"),
    );
    expect(within(row).getAllByText(MISSING_DISPLAY).length).toBeGreaterThan(0);
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
    expect(within(row).getAllByText(/not recorded/i).length).toBeGreaterThan(0);
    expect(
      within(row).getByText(/excluded from the mean absolute error/i),
    ).toBeInTheDocument();
    expect(
      within(row).getByText(/not recorded when the outcome was filed/i),
    ).toBeInTheDocument();
  });
});
