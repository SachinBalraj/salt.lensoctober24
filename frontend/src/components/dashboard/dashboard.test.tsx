/**
 * Dashboard card tests.
 *
 * These are the tests that would catch the failure this screen exists to prevent:
 * an operator reading a computed figure as a measured one, or a rule-based
 * recommendation as a model-informed one. The assertions are therefore mostly
 * about separation, and several are about what must *not* appear --
 *
 * - every card declares which of the six categories it holds, and no figure is
 *   rendered without its card's category on the face of it;
 * - an unmeasured quantity shows as unavailable with its reason, and never as a
 *   zero or a bare dash, because a gap that looks like a reading is evidence of
 *   nothing;
 * - no simulation curve is drawn when the starting brine is unknown, and the
 *   missing quantity is named;
 * - the model's own probability appears only inside the model's own card;
 * - with no model estimate the recommendation carries the project's exact
 *   required label, and no confidence figure is rendered anywhere for the action;
 * - a recorded decision belongs to its own card, is never filed by opening the
 *   page, and is the one thing on screen that is a person's act rather than a
 *   figure.
 *
 * The acts are also asserted to be separate, because that is the screen's other
 * load-bearing claim: mounting the dashboard writes no prediction, no
 * recommendation and no decision, and the button that predicts is not the button
 * that analyses.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DashboardClient from "@/components/dashboard/DashboardClient";
import type { EventOutcome } from "@/lib/outcomes";
import { ApiError } from "@/lib/api";
import { ML_UNAVAILABLE_LABEL, type DecisionAdvice } from "@/lib/decisions";
import { advise, latestAdvice } from "@/lib/decisionsApi";
import { getTwinState, listTwinHistory, refreshTwinState } from "@/lib/digitalTwinApi";
import {
  TWIN_DISPLAY_QUANTITIES,
  type DigitalTwinHistoryResponse,
  type DigitalTwinRefreshResponse,
  type DigitalTwinState,
} from "@/lib/digitalTwin";
import { listPans } from "@/lib/pansApi";
import type { SaltPan } from "@/lib/pans";
import { recordObservation } from "@/lib/observationsApi";
import type { PanObservation } from "@/lib/observations";
import { recordWeather } from "@/lib/weatherApi";
import type { WeatherObservation } from "@/lib/weather";
import { predictRisk } from "@/lib/riskPredictionApi";
import type { RiskPrediction } from "@/lib/riskPrediction";
import { sweepRainfallScenarios } from "@/lib/simulationApi";
import type { RainfallSweep } from "@/lib/simulation";
import { DEMONSTRATION_SIMULATION_INPUTS } from "@/lib/demonstration";
import { feedbackForRecommendation, recordFeedback } from "@/lib/operatorFeedbackApi";
import type { OperatorFeedback } from "@/lib/operatorFeedback";
import { listOutcomes } from "@/lib/outcomesApi";

vi.mock("@/lib/pansApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/pansApi")>("@/lib/pansApi");
  return { ...actual, listPans: vi.fn() };
});
vi.mock("@/lib/digitalTwinApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/digitalTwinApi")>(
    "@/lib/digitalTwinApi",
  );
  return {
    ...actual,
    getTwinState: vi.fn(),
    refreshTwinState: vi.fn(),
    listTwinHistory: vi.fn(),
  };
});
vi.mock("@/lib/observationsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/observationsApi")>(
    "@/lib/observationsApi",
  );
  return { ...actual, recordObservation: vi.fn() };
});
vi.mock("@/lib/weatherApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/weatherApi")>(
    "@/lib/weatherApi",
  );
  return { ...actual, recordWeather: vi.fn() };
});
vi.mock("@/lib/simulationApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/simulationApi")>(
    "@/lib/simulationApi",
  );
  return { ...actual, sweepRainfallScenarios: vi.fn() };
});
vi.mock("@/lib/riskPredictionApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/riskPredictionApi")>(
    "@/lib/riskPredictionApi",
  );
  return { ...actual, predictRisk: vi.fn() };
});
vi.mock("@/lib/decisionsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/decisionsApi")>(
    "@/lib/decisionsApi",
  );
  return { ...actual, advise: vi.fn(), latestAdvice: vi.fn() };
});
vi.mock("@/lib/operatorFeedbackApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/operatorFeedbackApi")>(
    "@/lib/operatorFeedbackApi",
  );
  return { ...actual, recordFeedback: vi.fn(), feedbackForRecommendation: vi.fn() };
});
vi.mock("@/lib/outcomesApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/outcomesApi")>(
    "@/lib/outcomesApi",
  );
  return { ...actual, listOutcomes: vi.fn() };
});

const listPansMock = vi.mocked(listPans);
const getTwinMock = vi.mocked(getTwinState);
const refreshTwinMock = vi.mocked(refreshTwinState);
const listTwinHistoryMock = vi.mocked(listTwinHistory);
const recordObservationMock = vi.mocked(recordObservation);
const recordWeatherMock = vi.mocked(recordWeather);
const sweepMock = vi.mocked(sweepRainfallScenarios);
const predictMock = vi.mocked(predictRisk);
const adviseMock = vi.mocked(advise);
const latestMock = vi.mocked(latestAdvice);
const feedbackForRecommendationMock = vi.mocked(feedbackForRecommendation);
const recordFeedbackMock = vi.mocked(recordFeedback);
const listOutcomesMock = vi.mocked(listOutcomes);

const PAN: SaltPan = {
  id: "pan-1",
  pan_id: "kzn-2026-a01",
  pan_name: "Northern Evaporation Pan",
  location: "Port Shepstone",
  latitude: -30.7444,
  longitude: 30.8071,
  area_m2: 12500,
  normal_depth_cm: 15.5,
  target_harvest_salinity_g_l: 285,
  is_active: true,
  created_at: "2026-01-05T08:00:00Z",
  updated_at: "2026-03-14T08:00:00Z",
  deactivated_at: null,
  latest_observation: null,
};

const OBSERVATION: PanObservation = {
  observation_id: "obs-saved-1",
  pan_id: PAN.pan_id,
  timestamp: "2026-09-30T06:29:00Z",
  salinity_g_l: 250,
  water_depth_cm: 20,
  brine_temperature_c: null,
  humidity_percent: null,
  ambient_temperature_c: null,
  data_source: "manual",
  note: null,
  created_at: "2026-09-30T06:29:00Z",
};

const WEATHER_RECORD: WeatherObservation = {
  weather_id: "wx-saved-1",
  pan_id: PAN.pan_id,
  timestamp: "2026-09-30T06:30:00Z",
  forecast_for: "2026-09-30T12:30:00Z",
  rainfall_mm: 25,
  rainfall_probability_percent: 70,
  ambient_temperature_c: null,
  humidity_percent: null,
  source: "manual",
  is_forecast: true,
  created_at: "2026-09-30T06:30:00Z",
};

function makeTwin(overrides: Partial<DigitalTwinState> = {}): DigitalTwinState {
  return {
    twin_id: "twin-1",
    pan_id: PAN.pan_id,
    pan_name: PAN.pan_name,
    timestamp: "2026-03-14T12:00:00Z",
    provenance: "observed",
    twin_version: "0.1.0",
    salinity_g_l: 285.4,
    water_depth_cm: 12.5,
    brine_temperature_c: 31.2,
    ambient_temperature_c: 26.5,
    humidity_percent: 64,
    pan_area_m2: 12500,
    location: "Port Shepstone",
    forecast_rainfall_mm: 18,
    rainfall_probability_percent: 80,
    observation: {
      id: "obs-1",
      timestamp: "2026-03-14T11:00:00Z",
      source: "manual",
      age_seconds: 3600,
    },
    weather: {
      id: "wx-1",
      timestamp: "2026-03-14T11:30:00Z",
      source: "manual",
      age_seconds: 1800,
    },
    weather_forecast: {
      id: "wx-2",
      timestamp: "2026-03-14T10:00:00Z",
      source: "manual",
      age_seconds: 7200,
    },
    is_complete: true,
    missing_inputs: [],
    missing_fields: [],
    quantities: [...TWIN_DISPLAY_QUANTITIES],
    ...overrides,
  };
}

function makeRefresh(): DigitalTwinRefreshResponse {
  return {
    state: makeTwin(),
    stored: true,
    changed: true,
    previous_twin_id: null,
  };
}

/** A stored-snapshot history, newest first, as the history endpoint returns it. */
function makeHistory(total = 1): DigitalTwinHistoryResponse {
  return {
    pan_id: PAN.pan_id,
    items: total === 0 ? [] : [makeTwin()],
    total,
    limit: 1,
    offset: 0,
    earliest_timestamp: total === 0 ? null : "2026-03-14T12:00:00Z",
    latest_timestamp: total === 0 ? null : "2026-03-14T12:00:00Z",
  };
}

function makeSweep(): RainfallSweep {
  const scenarios = [0, 5, 10, 18, 20, 50, 100].map((rainfall_mm) => {
    const post_rain_salinity_g_l = 285.4 / (1 + rainfall_mm / 12.5);
    return {
      rainfall_mm,
      label: `${rainfall_mm} mm`,
      is_standard_preset: [0, 5, 10, 20, 50, 100].includes(rainfall_mm),
      rainfall_volume_m3: 12500 * (rainfall_mm / 1000),
      post_rain_volume_m3: 156.25 + 12500 * (rainfall_mm / 1000),
      post_rain_depth_m: 0.125 + rainfall_mm / 1000,
      post_rain_salinity_g_l,
      salinity_drop_g_l: 285.4 - post_rain_salinity_g_l,
      dilution_percent: ((285.4 - post_rain_salinity_g_l) / 285.4) * 100,
      dilution_percent_reason: null,
    };
  });
  return {
    area_m2: 12500,
    initial_depth_m: 0.125,
    initial_salinity_g_l: 285.4,
    initial_volume_m3: 156.25,
    standard_scenarios_mm: [0, 5, 10, 20, 50, 100],
    scenarios,
    is_simulated: true,
    model_name: "mass_balance",
    disclaimer: "Physics-based simulation under simplifying assumptions; not an ML prediction.",
    assumptions: ["Complete mixing: the pan reaches one uniform salinity instantly."],
    equations: ["C1 = C0 x V0 / V1"],
  };
}

const PREDICTION: RiskPrediction = {
  prediction_id: "pred-1",
  pan_id: PAN.pan_id,
  predicted_at: "2026-03-14T12:00:00Z",
  as_of: "2026-03-14T12:00:00Z",
  risk_class: "leakage_risk",
  probability: 0.72,
  class_probabilities: { leakage_risk: 0.72, stable: 0.21, flooding_risk: 0.07 },
  defined_classes: ["stable", "leakage_risk", "flooding_risk"],
  model_id: "rf-2026",
  model_name: "Random forest",
  model_version: "0.2.0",
  algorithm: "random_forest",
  deployment_id: "dep-1",
  deployed_at: "2026-03-01T00:00:00Z",
  input_snapshot_id: "sha256:abc",
  twin_id: "twin-1",
  twin_as_of: "2026-03-14T12:00:00Z",
  risk_definition_id: "rd-1",
  risk_definition_version: 1,
  dataset_id: "ds-1",
  dataset_version: 1,
  selection_criterion: null,
  features_used: ["current_salinity_g_l"],
  features_absent: [],
  features_imputed: [],
  used_physics_prediction: false,
  input_source: "current_digital_twin_state",
  explanation_status: "available",
  explanation_id: "exp-1",
  explanation_refusal: null,
  explanation_refusal_detail: null,
};

function makeAdvice(overrides: Partial<DecisionAdvice> = {}): DecisionAdvice {
  return {
    recommendation: "PROTECT / DRAIN",
    reason: "Rule 'flood_guard' version 3: >= (read 82.0 from simulation).",
    inputs_used: [],
    simulation_result: {
      run_id: "sim-1",
      model_name: "mass_balance",
      is_ml: false,
      rainfall_mm: 18,
      rainfall_source: "twin.forecast_rainfall_mm",
      initial_salinity_g_l: 285.4,
      initial_depth_cm: 12.5,
      post_rain_salinity_g_l: 178.4,
      post_rain_depth_cm: 14.3,
      salinity_drop_g_l: 107,
      dilution_percent: 37.5,
      dilution_percent_reason: null,
      assumptions: ["Complete mixing."],
      disclaimer: "Physics-based simulation under simplifying assumptions; not an ML prediction.",
    },
    ml_result: null,
    rule_version: "flood_guard v3 (sha256:abc123)",
    timestamp: "2026-03-14T12:00:00Z",
    basis: "physics_and_rules",
    notice: `${ML_UNAVAILABLE_LABEL} No operational-risk prediction is stored for this pan.`,
    recommendation_id: "rec-1",
    pan_id: PAN.pan_id,
    priority: "high",
    rule_id: "rule-1",
    rule_name: "flood_guard",
    rule_version_number: 3,
    specification_hash: "sha256:abc123",
    matched_criteria: ">= 45 mm -> PROTECT / DRAIN",
  criteria: [],
    twin: {
      twin_id: "twin-1",
      as_of: "2026-03-14T12:00:00Z",
      provenance: "observed",
      salinity_g_l: 285.4,
      water_depth_cm: 12.5,
      forecast_rainfall_mm: 18,
      observation_id: "obs-1",
      observation_timestamp: "2026-03-14T11:00:00Z",
      weather_forecast_id: "wx-2",
      weather_forecast_timestamp: "2026-03-14T10:00:00Z",
      missing_fields: [],
    is_demonstration: false,
    demonstration_sources: [],
    },
    title: "PROTECT / DRAIN",
    body: "Recommended action: PROTECT / DRAIN.",
    expires_at: null,
    ...overrides,
  };
}

function makeDecisionRecord(
  overrides: Partial<OperatorFeedback> = {},
): OperatorFeedback {
  return {
    decision_id: "dec-1",
    recommendation_id: "rec-1",
    pan_id: PAN.pan_id,
    decision: "accepted",
    actual_action: "PROTECT / DRAIN",
    recommended_action: "PROTECT / DRAIN",
    operator_id: "M. Ndlovu",
    rule_name: "wet-season harvest ladder",
    rule_version: 3,
    specification_hash: "sha256:abc123",
    is_demonstration: false,
    demonstration_sources: [],
    notes: null,
    timestamp: "2026-03-14T12:10:00Z",
    created_at: "2026-03-14T12:10:00Z",
    recommendation: {
      recommendation_id: "rec-1",
      action: "PROTECT / DRAIN",
      issued_at: "2026-03-14T12:00:00Z",
      basis: "physics_and_rules",
      priority: "high",
      simulation_run_id: "sim-1",
      ml_prediction_id: null,
      explanation_id: null,
      twin_id: "twin-1",
      twin_as_of: "2026-03-14T12:00:00Z",
      observation_id: "obs-1",
      observation_timestamp: "2026-03-14T11:00:00Z",
      weather_forecast_id: "wx-2",
      rule_name: "flood_guard",
      rule_version: 3,
      specification_hash: "sha256:abc123",
    },
    ...overrides,
  };
}

function errorTerms(): EventOutcome["simulation_error"] {
  return {
    predicted: null,
    actual: null,
    predicted_from: "",
    predicted_from_label: "",
    unit: "g/L",
    signed_error: null,
    absolute_error: null,
    percentage_error_percent: null,
    percentage_error_reason: null,
    is_comparable: false,
    missing_inputs: [],
    missing_fields: [],
  };
}

function makeOutcome(overrides: Partial<EventOutcome> = {}): EventOutcome {
  return {
    outcome_id: "out-1",
    pan_id: PAN.pan_id,
    event_id: "storm-2026-03-14",
    timestamp: "2026-03-15T06:00:00Z",
    created_at: "2026-03-15T06:30:00Z",
    forecast_rainfall_mm: 18,
    actual_rainfall_mm: 42,
    pre_event_salinity_g_l: 285.4,
    pre_event_depth_cm: 12.5,
    post_event_salinity_g_l: 178.4,
    post_event_depth_cm: 14.3,
    operator_action: null,
    harvest_outcome: null,
    simulation_error: errorTerms(),
    forecast_simulation_error: errorTerms(),
    rainfall_forecast_error: errorTerms(),
    links: {
      pan_state: null,
      weather_forecast: null,
      simulation_run: null,
      recommendation: null,
      operator_decision: null,
    },
    has_actual_measurement: true,
    is_comparable: true,
    ...overrides,
  };
}

type ProvenanceValue =
  | "observed"
  | "forecast"
  | "simulated"
  | "ml"
  | "recommended"
  | "operator_decision";

/** The card that declares one kind of number. */
function sectionFor(kind: ProvenanceValue): HTMLElement {
  const section = document.querySelector(`section[data-provenance="${kind}"]`);
  expect(section, `a ${kind} card should be on screen`).not.toBeNull();
  return section as HTMLElement;
}

async function renderDashboard() {
  render(<DashboardClient />);
  await waitFor(() => expect(screen.getByTestId("pan-selector")).toBeInTheDocument());
  await waitFor(() => {
    expect(screen.getByText("Current pan condition")).toBeInTheDocument();
    expect(sectionFor("simulated")).toBeInTheDocument();
  });
}

beforeEach(() => {
  // `restoreMocks` restores implementations but not cumulative call counts, so
  // clear history here to keep the count-based assertions deterministic.
  listPansMock.mockClear();
  getTwinMock.mockClear();
  refreshTwinMock.mockClear();
  listTwinHistoryMock.mockClear();
  recordObservationMock.mockClear();
  recordWeatherMock.mockClear();
  sweepMock.mockClear();
  predictMock.mockClear();
  adviseMock.mockClear();
  latestMock.mockClear();
  feedbackForRecommendationMock.mockClear();
  recordFeedbackMock.mockClear();
  listOutcomesMock.mockClear();

  listPansMock.mockResolvedValue({
    items: [PAN],
    total: 1,
    limit: 20,
    offset: 0,
    total_active: 1,
    total_inactive: 0,
  });
  getTwinMock.mockResolvedValue(makeTwin());
  refreshTwinMock.mockResolvedValue(makeRefresh());
  // A stored snapshot is the normal state: the recommendation engine reads it,
  // so most tests analyze without re-running the snapshot step.
  listTwinHistoryMock.mockResolvedValue(makeHistory(1));
  recordObservationMock.mockResolvedValue(OBSERVATION);
  recordWeatherMock.mockResolvedValue(WEATHER_RECORD);
  sweepMock.mockResolvedValue(makeSweep());
  predictMock.mockResolvedValue(PREDICTION);
  adviseMock.mockResolvedValue(makeAdvice());
  latestMock.mockResolvedValue({ found: false, advice: null });
  feedbackForRecommendationMock.mockResolvedValue({ count: 0, items: [] });
  recordFeedbackMock.mockResolvedValue(makeDecisionRecord());
  listOutcomesMock.mockResolvedValue({
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
    earliest_timestamp: null,
    latest_timestamp: null,
  });
});

describe("the dashboard's provenance discipline", () => {
  it("tags every card with the kind of number it holds", async () => {
    await renderDashboard();

    expect(sectionFor("observed").dataset.provenance).toBe("observed");
    expect(sectionFor("forecast").dataset.provenance).toBe("forecast");
    expect(sectionFor("simulated").dataset.provenance).toBe("simulated");
    expect(sectionFor("ml").dataset.provenance).toBe("ml");
    expect(sectionFor("recommended").dataset.provenance).toBe("recommended");
  });

  it("teaches all six categories in the legend", async () => {
    await renderDashboard();
    for (const label of [
      "Observed",
      "Forecast",
      "Simulated",
      "ML-predicted",
      "Recommended",
      "Decided",
    ]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it("keeps a measured reading and a forecast in separate cards", async () => {
    await renderDashboard();

    const condition = sectionFor("observed");
    const weather = sectionFor("forecast");

    // The salinity tile sits in the observed card; the forecast rainfall tile in
    // the forecast card. Neither card carries the other's figure.
    expect(within(condition).getByText("285.4")).toBeInTheDocument();
    expect(within(condition).queryByText("18.0")).not.toBeInTheDocument();
    expect(within(weather).getByText("18.0")).toBeInTheDocument();
    expect(within(weather).queryByText("285.4")).not.toBeInTheDocument();
  });

  it("names the record each measured figure came from", async () => {
    await renderDashboard();

    const tile = document.querySelector('[data-reading="humidity_percent"]');
    expect(tile).not.toBeNull();
    // Humidity is observed, but it was measured by the weather station, not in
    // the pan, and the card says so on the tile.
    expect(tile?.textContent).toContain("64");
    expect(tile?.textContent).toContain("Weather observation");
  });
});

describe("gaps are shown as gaps", () => {
  it("shows an unmeasured quantity as unavailable, with its reason", async () => {
    getTwinMock.mockResolvedValue(
      makeTwin({
        brine_temperature_c: null,
        missing_inputs: [
          {
            field: "brine_temperature_c",
            reason: "No pan observation has been recorded, so the brine temperature is unavailable.",
            expected_from: "observation",
          },
        ],
      }),
    );

    await renderDashboard();

    const tile = document.querySelector('[data-reading="brine_temperature_c"]');
    expect(tile).not.toBeNull();
    expect(tile?.textContent).toContain("Unavailable");
    expect(tile?.textContent).toContain("No pan observation");
    // The gap must not be dressed as a reading: no figure is present.
    const figure = tile?.querySelector(".text-xl");
    expect(figure).toBeNull();
  });

  it("draws no simulation curve when the starting salinity is unknown", async () => {
    getTwinMock.mockResolvedValue(makeTwin({ salinity_g_l: null }));

    await renderDashboard();

    const simulation = sectionFor("simulated");
    expect(within(simulation).getByText(/No simulation can be run/)).toBeInTheDocument();
    expect(within(simulation).getByText(/a measured salinity/)).toBeInTheDocument();
    // Nothing was asked of the balance, so nothing was invented.
    expect(sweepMock).not.toHaveBeenCalled();
  });

  it("sweeps the forecast depth as a real scenario so the marker is not an interpolation", async () => {
    await renderDashboard();

    expect(sweepMock).toHaveBeenCalledWith(
      expect.objectContaining({ rainfallScenariosMm: [18] }),
    );
  });
});

describe("prediction and recommendation are separate acts", () => {
  it("writes neither a prediction nor a recommendation just by being opened", async () => {
    await renderDashboard();
    expect(predictMock).not.toHaveBeenCalled();
    expect(adviseMock).not.toHaveBeenCalled();
    // Reading a stored recommendation is a read, and is allowed.
    expect(latestMock).toHaveBeenCalled();
  });

  it("writes no decision just by being opened", async () => {
    await renderDashboard();

    // With no stored advice there is nothing to answer, so the decision read is
    // not even asked for, and certainly nothing is filed.
    expect(feedbackForRecommendationMock).not.toHaveBeenCalled();
    expect(recordFeedbackMock).not.toHaveBeenCalled();
    // No decision card exists until a recommendation does. (The legend's tag is
    // a span, so the query is scoped to card sections.)
    expect(document.querySelector('section[data-provenance="operator_decision"]')).toBeNull();
  });

  it("shows the model's own probability only in the model's own card", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("ml-predict"));

    await waitFor(() => expect(screen.getByTestId("ml-risk-body")).toBeInTheDocument());
    expect(screen.getAllByText("72.0%").length).toBeGreaterThan(0);

    // When the risk card loads a prediction, the decision card still holds no
    // action and no figure, because prediction is not analysis.
    const recommendation = sectionFor("recommended");
    expect(within(recommendation).getByText(/No analysis run/)).toBeInTheDocument();
    expect(within(recommendation).queryByText(/72\.0%/)).not.toBeInTheDocument();
  });

  it("labels a recommendation with no model estimate in the project's exact words", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("decision-run"));

    const notice = await screen.findByTestId("recommendation-notice");
    expect(notice.textContent).toContain(ML_UNAVAILABLE_LABEL);
    expect(within(sectionFor("recommended")).getByText("PROTECT / DRAIN")).toBeInTheDocument();
  });

  it("renders no confidence figure for the action", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("decision-run"));
    await screen.findByTestId("recommendation-action");

    const text = sectionFor("recommended").textContent ?? "";
    // A rule either decided or it did not; no likelihood was computed for it.
    expect(text).not.toMatch(/confidence/i);
    expect(text).not.toMatch(/\d+(\.\d+)?%.*(confidence|strength|decisi)/i);
  });

  it("shows the thresholds the rule decided on, and where each was read from", async () => {
    // The dashboard is the page an operator works from, so a figure that can only
    // be found on the pan page is a figure they will not see when they act on the
    // recommendation. Asserted on the resolved value and the field it came from,
    // because a number with no provenance cannot be challenged: an objection to
    // the operator's own limit and an objection to the rule that used it have to
    // go to different people.
    adviseMock.mockResolvedValueOnce(
      makeAdvice({
        criteria: [
          {
            variable: "simulated_post_rain_depth_cm",
            operator: "gte",
            comparator: "simulated_post_rain_depth_cm gte flood_depth_cm",
            value: 14.3,
            threshold: 12.0,
            lower_bound: null,
            upper_bound: null,
            bound_variable: "flood_depth_cm",
            source: "simulation",
            action: "PROTECT / DRAIN",
            rationale: "Beyond the depth this operator says the pan can hold.",
          },
        ],
      }),
    );

    await renderDashboard();
    await userEvent.click(screen.getByTestId("decision-run"));

    const table = await screen.findByTestId("decision-criteria");
    expect(within(table).getByText("simulated_post_rain_depth_cm gte flood_depth_cm")).toBeInTheDocument();
    expect(within(table).getByText("14.3")).toBeInTheDocument();
    // The bound, and the fact that it is the operator's own figure.
    expect(within(table).getByText("12")).toBeInTheDocument();
    expect(within(table).getByTestId("decision-criterion-configured")).toHaveTextContent(
      "Your configured limit",
    );
  });

  it("shows no thresholds table when nothing fired", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("decision-run"));
    await screen.findByTestId("recommendation-action");

    // An empty table would imply a comparison was made and found nothing, which
    // is a different claim from "no criterion fired".
    expect(screen.queryByTestId("decision-criteria")).not.toBeInTheDocument();
  });

  it("claims no model provenance for a rule that read no prediction", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("decision-run"));
    await screen.findByTestId("recommendation-action");

    // The rule read the recorded state and the mass balance only, so the
    // "what it was decided from" row names observed, simulated and forecast --
    // and no ML tag.
    const decision = sectionFor("recommended");
    const tags = decision.querySelectorAll("[data-provenance='ml']");
    expect(tags.length).toBe(0);
  });
});

describe("the operator decision", () => {
  it("records an acceptance with no action as no physical action recorded", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("decision-run"));
    await screen.findByTestId("decision-form");

    // The stored record mirrors what the fixed backend returns: accepting with a
    // blank action keeps the action field null, because nothing was done.
    recordFeedbackMock.mockResolvedValue(makeDecisionRecord({ actual_action: null }));

    await userEvent.type(screen.getByLabelText(/Operator/), "M. Ndlovu");
    await userEvent.click(screen.getByTestId("decision-submit"));

    // Accepting and acting are separate facts; the client sends no action for
    // the server to invent on the operator's behalf.
    await waitFor(() =>
      expect(recordFeedbackMock).toHaveBeenCalledWith({
        recommendationId: "rec-1",
        decision: "accepted",
        actualAction: undefined,
        operatorId: "M. Ndlovu",
        notes: undefined,
      }),
    );

    // The stored record replaces the form, and says plainly that the advice was
    // accepted without a physical act — never "took" the recommended action.
    const recorded = await waitFor(() => {
      const element = screen.getByTestId("decision-recorded");
      expect(element).toBeInTheDocument();
      return element;
    });
    expect(within(recorded).getByText("Accepted")).toBeInTheDocument();
    expect(
      within(recorded).getByText(/Recommendation accepted — no physical action recorded/),
    ).toBeInTheDocument();
    expect(within(recorded).queryByText(/Took/)).not.toBeInTheDocument();
    expect(within(recorded).getByText("dec-1")).toBeInTheDocument();
  });

  it("shows the recommended action as taken when the operator recorded it", async () => {
    latestMock.mockResolvedValue({ found: true, advice: makeAdvice() });
    feedbackForRecommendationMock.mockResolvedValue({
      count: 1,
      items: [makeDecisionRecord()],
    });

    await renderDashboard();

    const recorded = await screen.findByTestId("decision-recorded");
    expect(within(recorded).getByText("Accepted")).toBeInTheDocument();
    expect(
      within(recorded).getByText(/Took PROTECT \/ DRAIN — the recommended action stands/),
    ).toBeInTheDocument();
  });

  it("refuses a modified decision until the action actually taken is named", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("decision-run"));
    await screen.findByTestId("decision-form");

    await userEvent.selectOptions(screen.getByLabelText(/Disposition/), "modified");

    // The form will not file a decision that cannot name what was done.
    expect(screen.getByTestId("decision-submit")).toBeDisabled();
    expect(screen.getByTestId("decision-action-required")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("decision-submit"));
    expect(recordFeedbackMock).not.toHaveBeenCalled();

    await userEvent.selectOptions(
      screen.getByLabelText(/Action actually taken/),
      "TRANSFER BRINE",
    );
    await userEvent.click(screen.getByTestId("decision-submit"));

    await waitFor(() =>
      expect(recordFeedbackMock).toHaveBeenCalledWith({
        recommendationId: "rec-1",
        decision: "modified",
        actualAction: "TRANSFER BRINE",
        operatorId: undefined,
        notes: undefined,
      }),
    );
  });

  it("shows an already-recorded decision when a stored recommendation loads", async () => {
    latestMock.mockResolvedValue({ found: true, advice: makeAdvice() });
    feedbackForRecommendationMock.mockResolvedValue({
      count: 1,
      items: [
        makeDecisionRecord({
          decision: "modified",
          actual_action: "TRANSFER BRINE",
          notes: "Drained early — the forecast looked heavy.",
        }),
      ],
    });

    await renderDashboard();

    const recorded = await screen.findByTestId("decision-recorded");
    expect(within(recorded).getByText("Modified")).toBeInTheDocument();
    expect(
      within(recorded).getByText(/Took TRANSFER BRINE instead of the recommended PROTECT \/ DRAIN/),
    ).toBeInTheDocument();
    expect(within(recorded).getByText("M. Ndlovu")).toBeInTheDocument();
    // Reading an existing decision is a read, not a rewrite.
    expect(recordFeedbackMock).not.toHaveBeenCalled();
  });

  it("shows the rule version the decision answers, from the decision itself", async () => {
    // Read from the record rather than the live recommendation, so the panel keeps
    // naming the rule version after that recommendation has been superseded and
    // removed -- which is the whole reason the copy exists.
    latestMock.mockResolvedValue({ found: true, advice: makeAdvice() });
    feedbackForRecommendationMock.mockResolvedValue({
      count: 1,
      items: [
        makeDecisionRecord({
          rule_name: "wet-season harvest ladder",
          rule_version: 3,
          recommendation: null,
        }),
      ],
    });

    await renderDashboard();

    const recorded = await screen.findByTestId("decision-recorded");
    expect(within(recorded).getByText("wet-season harvest ladder v3")).toBeInTheDocument();
  });

  it("labels a decision made on demonstration advice", async () => {
    // An operator can make a real decision about a worked example, and the record
    // is worth keeping. What must not happen is that record reading as evidence
    // about the rule from field results, so the label is asserted on the panel
    // rather than left to whoever exports it.
    latestMock.mockResolvedValue({ found: true, advice: makeAdvice() });
    feedbackForRecommendationMock.mockResolvedValue({
      count: 1,
      items: [
        makeDecisionRecord({
          is_demonstration: true,
          demonstration_sources: ["observation_data_source"],
        }),
      ],
    });

    await renderDashboard();

    const recorded = await screen.findByTestId("decision-recorded");
    expect(within(recorded).getByText(/A decision on demonstration advice/)).toBeInTheDocument();
    expect(within(recorded).getByText(/observation_data_source/)).toBeInTheDocument();
    // The decision itself is still shown as a real record, not hidden away.
    expect(within(recorded).getByText("Accepted")).toBeInTheDocument();
  });

  it("carries no demonstration label on a decision about real advice", async () => {
    latestMock.mockResolvedValue({ found: true, advice: makeAdvice() });
    feedbackForRecommendationMock.mockResolvedValue({
      count: 1,
      items: [makeDecisionRecord({ is_demonstration: false })],
    });

    await renderDashboard();

    const recorded = await screen.findByTestId("decision-recorded");
    expect(within(recorded).queryByText(/A decision on demonstration advice/)).not.toBeInTheDocument();
  });

  it("does not claim a decision is real when its provenance was never recorded", async () => {
    // Null is not false. A record that predates the provenance flag, and whose
    // advice is gone, establishes nothing either way -- and this is the panel an
    // operator reads while deciding whether to trust the row, so it has to say so
    // rather than quietly imply the answer is "field data".
    latestMock.mockResolvedValue({ found: true, advice: makeAdvice() });
    feedbackForRecommendationMock.mockResolvedValue({
      count: 1,
      items: [makeDecisionRecord({ is_demonstration: null, recommendation: null })],
    });

    await renderDashboard();

    const recorded = await screen.findByTestId("decision-recorded");
    expect(within(recorded).getByText(/Provenance not recorded/)).toBeInTheDocument();
    // Not the demonstration label, and not its absence taken as reassurance.
    expect(within(recorded).queryByText(/A decision on demonstration advice/)).not.toBeInTheDocument();
  });

  it("never puts a figure on a decision", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("decision-run"));
    await screen.findByTestId("decision-form");

    // The decision card holds a disposition, an action and a note — never a
    // measured or computed figure, and never the model's confidence or
    // probability. "recommended" contains the letters "mm", so the unit check is
    // scoped to a number-adjacent form rather than a bare substring.
    const panel = sectionFor("operator_decision");
    const text = panel.textContent ?? "";
    expect(text).not.toMatch(/confidence|probability/i);
    expect(text).not.toMatch(/\d+(\.\d+)?\s?(%|mm|g\/L)/i);
    expect(text).not.toMatch(/\d/);
  });
});

describe("the trace card", () => {
  it("rebuilds the chain, naming each record that exists and each that does not", async () => {
    latestMock.mockResolvedValue({ found: true, advice: makeAdvice() });
    feedbackForRecommendationMock.mockResolvedValue({
      count: 1,
      items: [makeDecisionRecord()],
    });
    listOutcomesMock.mockResolvedValue({
      items: [
        makeOutcome({
          links: {
            pan_state: null,
            weather_forecast: null,
            simulation_run: null,
            recommendation: null,
            operator_decision: {
              kind: "operator_decision",
              record_id: "dec-1",
              timestamp: "2026-03-15T06:00:00Z",
              summary: "Decision dec-1 filed for the advice",
              is_resolved: true,
              salinity_g_l: null,
              water_depth_cm: null,
              rainfall_mm: null,
              detail: null,
            },
          },
        }),
      ],
      total: 1,
      limit: 20,
      offset: 0,
      earliest_timestamp: "2026-03-15T06:00:00Z",
      latest_timestamp: "2026-03-15T06:00:00Z",
    });

    await renderDashboard();

    const trace = await screen.findByTestId("trace-card");
    expect(within(trace).getByTestId("trace-observation").textContent).toContain("obs-1");
    expect(within(trace).getByTestId("trace-simulation").textContent).toContain("sim-1");
    // This advice read no model estimate, so the ML and SHAP steps keep their
    // place and say so rather than vanishing.
    expect(within(trace).getByTestId("trace-ml").textContent).toContain("no record yet");
    expect(within(trace).getByTestId("trace-shap").textContent).toContain("no record yet");
    expect(within(trace).getByTestId("trace-decision").textContent).toContain("dec-1");
    const outcome = within(trace).getByTestId("trace-outcome");
    expect(outcome.textContent).toContain("out-1");
    expect(outcome.textContent).toContain("linked to this decision");
  });
});
describe("the demonstration simulation card", () => {
  const DEMO_AREA_M2 = 12_500;

  /** Sweep calls carrying the demonstration's own fixed inputs. */
  function demoSweepCalls() {
    return sweepMock.mock.calls
      .map(([params]) => params)
      .filter(
        (params) =>
          params?.initial_salinity_g_l ===
            DEMONSTRATION_SIMULATION_INPUTS.salinity_g_l,
      );
  }

  it("asks the balance for nothing until it is asked to", async () => {
    await renderDashboard();
    const card = screen.getByTestId("demo-simulate").closest("section") as HTMLElement;
    expect(within(card).getByText("Not run yet")).toBeInTheDocument();
    // The dashboard promises a page load computes nothing. A demonstration card
    // that swept on mount would break that promise quietly. The recorded card
    // sweeps for itself when the twin has a salinity, so the assertion is scoped
    // to the demonstration's own inputs rather than to the mock as a whole.
    expect(demoSweepCalls()).toHaveLength(0);
  });

  it("sends the fixed demonstration inputs and the pan's registered area", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("demo-simulate"));

    await waitFor(() => expect(demoSweepCalls()).toHaveLength(1));
    // The area comes from the registration, because an invented area would mean
    // the volumes were fictional too. The salinity and depth are the
    // demonstration's own and are stated on the card.
    expect(demoSweepCalls()[0]).toEqual({
      area_m2: DEMO_AREA_M2,
      initial_salinity_g_l: DEMONSTRATION_SIMULATION_INPUTS.salinity_g_l,
      initial_depth_m: DEMONSTRATION_SIMULATION_INPUTS.initial_depth_m,
    });
  });

  it("shows the five requested rainfall depths and hides the sixth the server sends", async () => {
    // The server resolves its own presets, including 100 mm. Showing five while
    // claiming the demonstration covers them all would leave a reader unable to
    // tell whether a sixth was computed and hidden.
    await renderDashboard();
    await userEvent.click(screen.getByTestId("demo-simulate"));

    const card = screen.getByTestId("demo-simulate").closest("section") as HTMLElement;
    const rows = await within(card).findAllByRole("row");
    // The header row is first; the rest are the scenarios.
    const rainfalls = rows.slice(1).map((row) => row.querySelector("th")?.textContent);
    expect(rainfalls).toEqual(["0", "5", "10", "20", "50"]);
    // The server resolves 100 mm as well. Showing five while claiming the
    // demonstration covers them all would leave a reader unable to tell whether
    // a sixth was computed and hidden.
    expect(rainfalls).not.toContain("100");
  });

  it("names the depth and salinity the curve was computed from", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("demo-simulate"));

    const card = screen.getByTestId("demo-simulate").closest("section") as HTMLElement;
    // A curve with no stated inputs is a curve from nowhere, and the reader has
    // no way to tell which figure is the fiction.
    await within(card).findAllByRole("row");
    expect(card.textContent).toContain(
      `${DEMONSTRATION_SIMULATION_INPUTS.salinity_g_l} g/L`,
    );
    expect(card.textContent).toContain(
      `${DEMONSTRATION_SIMULATION_INPUTS.initial_depth_m} m`,
    );
  });

  it("says the balance ignores the brine temperature rather than listing it as an input", async () => {
    await renderDashboard();
    const card = screen.getByTestId("demo-simulate").closest("section") as HTMLElement;
    // The observation records 32 °C. A card that did not address it would let a
    // reader assume the balance is temperature-dependent, which it is not.
    expect(card.textContent).toMatch(/no temperature term/i);
  });
});

describe("the data entry panel", () => {
  it("is an input form and is filed as no kind of number at all", async () => {
    await renderDashboard();
    const panel = screen.getByTestId("data-entry-panel");
    // It carries no `data-provenance` attribute, because the six categories say
    // where a figure came from and a form holds no figure. Filing it under
    // `operator_decision` put a pan id and a six-hour lead time inside a card
    // that promises never to contain a figure.
    expect(panel.getAttribute("data-provenance")).toBeNull();
  });

  it("does not file a reading against a pan of its own choosing", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("data-entry-toggle"));
    // There is one pan selector and one form; the form takes the selected pan
    // rather than offering a second, which is how a reading ends up on the wrong
    // salt pan.
    expect(screen.getAllByTestId("pan-selector")).toHaveLength(1);
  });

  it("stays closed until asked, so the dashboard still shows what is known", async () => {
    await renderDashboard();
    const toggle = screen.getByTestId("data-entry-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await userEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("says plainly that its contents are not a reading", async () => {
    await renderDashboard();
    const panel = screen.getByTestId("data-entry-panel");
    expect(panel.textContent).toMatch(/input form, not a reading/i);
  });
});

describe("recording a reading stores the twin snapshot", () => {
  it("stores the snapshot the recommendation reads when an observation is saved", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("data-entry-toggle"));
    await userEvent.click(
      screen.getByRole("button", { name: "Record demonstration" }),
    );

    // The recommendation engine reads the recorded snapshot, never a freshly
    // assembled one, so a save that does not store the snapshot leaves "Run
    // analysis" refusing even though the observation exists.
    await waitFor(() => {
      expect(refreshTwinMock).toHaveBeenCalledWith(PAN.pan_id);
    });
    await waitFor(() => {
      expect(screen.getByTestId("twin-snapshot-notice")).toHaveTextContent(
        "snapshot is stored",
      );
    });
  });

  it("stores another snapshot when the weather forecast is saved", async () => {
    await renderDashboard();
    await userEvent.click(screen.getByTestId("data-entry-toggle"));
    await userEvent.click(screen.getByRole("tab", { name: "Weather" }));
    // The weather form opens on a blank real reading; switch it to the
    // demonstration forecast, which is valid as stored.
    await userEvent.click(screen.getByRole("radio", { name: /demonstration/i }));
    await userEvent.click(
      screen.getByRole("button", { name: "Record demonstration forecast" }),
    );

    await waitFor(() => {
      expect(refreshTwinMock).toHaveBeenCalledWith(PAN.pan_id);
    });
  });

  it("warns rather than pretending analysis can run when the snapshot is refused", async () => {
    refreshTwinMock.mockRejectedValueOnce(
      new ApiError("storage unavailable", { status: 503 }),
    );
    await renderDashboard();
    await userEvent.click(screen.getByTestId("data-entry-toggle"));
    await userEvent.click(
      screen.getByRole("button", { name: "Record demonstration" }),
    );

    const notice = await screen.findByTestId("twin-snapshot-notice");
    expect(notice).toHaveAttribute("role", "alert");
    expect(notice).toHaveTextContent("could not be stored");
    expect(notice).toHaveTextContent("Run analysis");
  });
});

describe("re-running analysis stores a missing snapshot before advising", () => {
  it("stores the missing snapshot, tells the operator, and then advises", async () => {
    // A pan can hold an observation and a forecast while no snapshot is stored
    // for them (records written before the save-first flow existed, or entered
    // through an API instead of the form). The recommendation engine reads the
    // recorded snapshot, so without this step re-running analysis refuses even
    // though the state card above shows a complete twin.
    listTwinHistoryMock.mockResolvedValue(makeHistory(0));
    await renderDashboard();

    await userEvent.click(screen.getByTestId("decision-run"));

    await waitFor(() => {
      expect(refreshTwinMock).toHaveBeenCalledWith(PAN.pan_id);
    });
    await waitFor(() => {
      expect(adviseMock).toHaveBeenCalledWith({ panId: PAN.pan_id });
    });
    await screen.findByTestId("recommendation-action");
    expect(screen.getByTestId("twin-snapshot-notice")).toHaveTextContent(
      "stored one first",
    );
  });

  it("does not write another snapshot when a stored one already exists", async () => {
    await renderDashboard();

    await userEvent.click(screen.getByTestId("decision-run"));

    // A refresh always writes a snapshot, so restating one that is already
    // stored would duplicate the history entry on every re-run. The history
    // read must therefore short-circuit the store, and analysis must advise
    // from the snapshot that was already there.
    await waitFor(() => {
      expect(adviseMock).toHaveBeenCalledWith({ panId: PAN.pan_id });
    });
    await screen.findByTestId("recommendation-action");
    expect(refreshTwinMock).not.toHaveBeenCalled();
  });

  it("refuses rather than inventing advice when the snapshot cannot be stored", async () => {
    listTwinHistoryMock.mockResolvedValue(makeHistory(0));
    refreshTwinMock.mockRejectedValueOnce(
      new ApiError("storage unavailable", { status: 503 }),
    );
    await renderDashboard();

    await userEvent.click(screen.getByTestId("decision-run"));

    await waitFor(() => {
      expect(refreshTwinMock).toHaveBeenCalledWith(PAN.pan_id);
    });
    // The failed storage is the reason analysis cannot run; the dashboard must
    // not paper over it by issuing advice from a live view the engine never
    // reads.
    expect(adviseMock).not.toHaveBeenCalled();
    const alert = await screen.findByText("No recommendation");
    expect(alert).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
  });
});
