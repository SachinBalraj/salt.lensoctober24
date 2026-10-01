/**
 * Module 14 lib rules: the decision vocabulary, what the record means, and the
 * eight-step trace.
 *
 * The assertions that matter here are the honesty ones. A recorded decision must
 * never be read as a measurement (it has no figure, and an action is only
 * reported as taken if the record contains one), and the trace must keep a
 * missing step in its place rather than let the chain look complete.
 */

import { describe, expect, it } from "vitest";
import {
  describeDecision,
  describeRecordedAction,
  decisionFollowedRecommendation,
  FEEDBACK_REFUSALS,
  OPERATOR_DECISIONS,
  trace,
  type OperatorFeedback,
} from "@/lib/operatorFeedback";
import { ML_UNAVAILABLE_LABEL, type DecisionAdvice, type DecisionMlResult, type DecisionSimulationResult } from "@/lib/decisions";
import type { EventOutcome } from "@/lib/outcomes";

const simulation = (
  overrides: Partial<DecisionSimulationResult> = {},
): DecisionSimulationResult => ({
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
  assumptions: ["Complete mixing"],
  disclaimer: "Physics-based simulation under simplifying assumptions.",
  ...overrides,
});

const ml = (overrides: Partial<DecisionMlResult> = {}): DecisionMlResult => ({
  prediction_id: "pred-1",
  model_id: "model-1",
  model_version: "v1.0.0",
  target: "operational_risk",
  predicted_class: "act now",
  probability: 0.72,
  confidence: 0.9,
  probability_is_null_reason: null,
  predicted_at: "2026-03-14T12:00:00Z",
  twin_as_of: "2026-03-14T12:00:00Z",
  input_snapshot_id: "sha256:abc",
  explanation_id: null,
  matches_recommendation_snapshot: true,
  ...overrides,
});

const advice = (overrides: Partial<DecisionAdvice> = {}): DecisionAdvice => ({
  recommendation: "PROTECT / DRAIN",
  reason: "Rule 'flood_guard' v3: >= 45 mm.",
  inputs_used: [],
  simulation_result: simulation(),
  ml_result: null,
  rule_version: "flood_guard v3 (sha256:abc123)",
  timestamp: "2026-03-14T12:00:00Z",
  basis: "physics_and_rules",
  notice: ML_UNAVAILABLE_LABEL,
  recommendation_id: "rec-1",
  pan_id: "kzn-2026-a01",
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
});

const decision = (
  overrides: Partial<OperatorFeedback> = {},
): OperatorFeedback => ({
  decision_id: "dec-1",
  recommendation_id: "rec-1",
  pan_id: "kzn-2026-a01",
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
});

const outcome = (overrides: Partial<EventOutcome> = {}): EventOutcome => ({
  outcome_id: "out-1",
  pan_id: "kzn-2026-a01",
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
  simulation_error: {
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
  },
  forecast_simulation_error: {
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
  },
  rainfall_forecast_error: {
    predicted: null,
    actual: null,
    predicted_from: "",
    predicted_from_label: "",
    unit: "mm",
    signed_error: null,
    absolute_error: null,
    percentage_error_percent: null,
    percentage_error_reason: null,
    is_comparable: false,
    missing_inputs: [],
    missing_fields: [],
  },
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
});

describe("the decision vocabulary", () => {
  it("is exactly the three dispositions and nothing else", () => {
    expect([...OPERATOR_DECISIONS].sort()).toEqual([
      "accepted",
      "modified",
      "rejected",
    ]);
  });

  it("has a plain word for each disposition", () => {
    expect(describeDecision("accepted")).toBe("Accepted");
    expect(describeDecision("modified")).toBe("Modified");
    expect(describeDecision("rejected")).toBe("Rejected");
  });

  it("is a closed set of refusals", () => {
    expect([...FEEDBACK_REFUSALS].sort()).toEqual([
      "decision_already_recorded",
      "recommendation_not_a_decision",
      "recommendation_unknown",
      "unknown_decision_vocabulary",
    ]);
  });
});

describe("what the record says was done", () => {
  it("reports the recommended action as taken only when the record names it", () => {
    const accepted = decision();
    expect(decisionFollowedRecommendation(accepted)).toBe(true);
    expect(describeRecordedAction(accepted)).toBe(
      "Took PROTECT / DRAIN — the recommended action stands.",
    );
  });

  it("renders an acceptance with no action as a disposition without a physical act", () => {
    const accepted = decision({ actual_action: null });
    expect(decisionFollowedRecommendation(accepted)).toBe(false);
    expect(describeRecordedAction(accepted)).toBe(
      "Recommendation accepted — no physical action recorded.",
    );
  });

  it("says which action was taken instead of which was recommended", () => {
    const modified = decision({
      decision: "modified",
      actual_action: "TRANSFER BRINE",
    });
    expect(decisionFollowedRecommendation(modified)).toBe(false);
    expect(describeRecordedAction(modified)).toBe(
      "Took TRANSFER BRINE instead of the recommended PROTECT / DRAIN.",
    );
  });

  it("names the gap when a non-acceptance names no action", () => {
    const empty = decision({ decision: "rejected", actual_action: null });
    expect(decisionFollowedRecommendation(empty)).toBe(false);
    expect(describeRecordedAction(empty)).toBe(
      "The record names no action actually taken.",
    );
  });
});

describe("trace", () => {
  it("reconstructs the chain in order, carrying every identifier", () => {
    const steps = trace(advice(), decision(), outcome());
    expect(steps.map((step) => step.key)).toEqual([
      "observation",
      "twin",
      "simulation",
      "ml",
      "shap",
      "recommendation",
      "decision",
      "outcome",
    ]);

    const by = new Map(steps.map((step) => [step.key, step]));
    expect(by.get("observation")?.id).toBe("obs-1");
    expect(by.get("observation")?.timestamp).toBe("2026-03-14T11:00:00Z");
    expect(by.get("simulation")?.id).toBe("sim-1");
    expect(by.get("recommendation")?.id).toBe("rec-1");
    expect(by.get("decision")?.id).toBe("dec-1");
    expect(by.get("outcome")?.id).toBe("out-1");
  });

  it("keeps the ML and SHAP steps when no model estimate was read, and says why", () => {
    const steps = trace(advice(), decision(), outcome());
    const by = new Map(steps.map((step) => [step.key, step]));

    expect(by.get("ml")?.id).toBeNull();
    expect(by.get("ml")?.detail).toBe("No model estimate was read for this advice.");
    expect(by.get("shap")?.id).toBeNull();
    expect(by.get("shap")?.detail).toBe(
      "No SHAP explanation — no model estimate was read.",
    );
  });

  it("makes the honest missing-state claims for an unanswered advice", () => {
    const steps = trace(advice(), null, null);
    const by = new Map(steps.map((step) => [step.key, step]));

    expect(by.get("decision")?.detail).toBe(
      "No operator decision is recorded for this advice yet.",
    );
    expect(by.get("outcome")?.detail).toBe(
      "No outcome has been recorded for this pan yet.",
    );
  });

  it("marks the outcome as linked only when it names this decision", () => {
    const linked = outcome({
      links: {
        pan_state: null,
        weather_forecast: null,
        simulation_run: null,
        recommendation: null,
        operator_decision: {
          kind: "operator_decision",
          record_id: "dec-1",
          timestamp: "2026-03-15T06:00:00Z",
          summary: "Decision dec-1",
          is_resolved: true,
          salinity_g_l: null,
          water_depth_cm: null,
          rainfall_mm: null,
          detail: null,
        },
      },
    });
    const trailing = trace(advice(), decision(), linked).at(-1);
    expect(trailing?.detail).toContain("linked to this decision");

    const foreign = outcome({
      links: {
        ...linked.links,
        operator_decision: {
          ...(linked.links.operator_decision as NonNullable<EventOutcome["links"]["operator_decision"]>),
          record_id: "dec-999",
        },
      },
    });
    const foreignStep = trace(advice(), decision(), foreign).at(-1);
    expect(foreignStep?.detail).toBe(
      "Recorded, but its decision reference is a different decision or none.",
    );
  });

  it("carries the attribution when a model estimate was explained", () => {
    const explained = advice({
      basis: "physics_and_ml",
      ml_result: ml({ explanation_id: "exp-1" }),
    });
    const by = new Map(trace(explained, decision(), outcome()).map((step) => [step.key, step]));

    expect(by.get("ml")?.id).toBe("pred-1");
    expect(by.get("shap")?.id).toBe("exp-1");
  });
});