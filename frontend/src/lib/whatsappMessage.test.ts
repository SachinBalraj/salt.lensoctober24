/**
 * The farmer-message builder.
 *
 * The assertions are about the facts a farmer will act on: every figure the panel
 * promises is in the text, a simulated salinity is labelled as simulated so it
 * cannot be read as a field reading, and the claim that no model was used appears
 * only on advice that really read none.
 */

import { describe, expect, it } from "vitest";
import {
  buildFarmerWhatsAppMessage,
  FARMER_MESSAGE_DISCLAIMER,
  NO_ML_STATEMENT,
} from "@/lib/whatsappMessage";
import { adviceHasModelEstimate } from "@/lib/decisions";
import type { DecisionAdvice } from "@/lib/decisions";
import type { SaltPan } from "@/lib/pans";

const PAN: SaltPan = {
  id: "pan-1",
  pan_id: "kzn-2026-b01",
  pan_name: "Northern Evaporation Pan",
  location: "Port Shepstone",
  latitude: -30.71,
  longitude: 30.21,
  area_m2: 12500,
  normal_depth_cm: 15.5,
  target_harvest_salinity_g_l: 285,
  is_active: true,
  created_at: "2026-01-05T08:00:00Z",
  updated_at: "2026-01-05T08:00:00Z",
  deactivated_at: null,
  farmer_name: null,
  farmer_whatsapp_masked: "+919•••••••10",
  latest_observation: null,
};

function advice(overrides: Partial<DecisionAdvice> = {}): DecisionAdvice {
  return {
    recommendation: "TRANSFER BRINE",
    reason: "Rule 'wet-season harvest ladder' version 1: the match decided.",
    inputs_used: [],
    simulation_result: {
      run_id: "run-1",
      model_name: "rainfall-dilution-mass-balance-v1",
      is_ml: false,
      rainfall_mm: 20,
      rainfall_source: "request.rainfall_mm",
      initial_salinity_g_l: 282,
      initial_depth_cm: 19,
      post_rain_salinity_g_l: 268.4,
      post_rain_depth_cm: 24,
      salinity_drop_g_l: 13.6,
      dilution_percent: 5,
      dilution_percent_reason: null,
      assumptions: [],
      disclaimer: "Physics-based simulation.",
    },
    ml_result: null,
    rule_version: "wet-season harvest ladder v1 (sha256:abc123)",
    timestamp: "2026-03-14T12:05:00Z",
    basis: "physics_and_rules",
    notice: "Physics/rule-based recommendation — ML prediction unavailable.",
    recommendation_id: "rec-1",
    pan_id: "kzn-2026-b01",
    priority: "medium",
    rule_id: "decrule-1",
    rule_name: "wet-season harvest ladder",
    rule_version_number: 1,
    specification_hash: "sha256:abc123",
    matched_criteria: "simulated_post_rain_salinity_g_l lte 296 -> TRANSFER BRINE",
    criteria: [],
    twin: {
      twin_id: "twin-1",
      as_of: "2026-03-14T12:00:00Z",
      provenance: "observed",
      salinity_g_l: 282,
      water_depth_cm: 19,
      forecast_rainfall_mm: 0,
      observation_id: "obs-1",
      observation_timestamp: "2026-03-14T12:00:00Z",
      weather_forecast_id: "wx-1",
      weather_forecast_timestamp: "2026-03-13T00:00:00Z",
      missing_fields: [],
      is_demonstration: false,
      demonstration_sources: [],
    },
    title: "TRANSFER BRINE",
    body: "Recommended action: TRANSFER BRINE.",
    expires_at: null,
    ...overrides,
  };
}

function build(overrides: Partial<DecisionAdvice> = {}, pan: SaltPan = PAN): string {
  return buildFarmerWhatsAppMessage({ pan, advice: advice(overrides) });
}

describe("buildFarmerWhatsAppMessage", () => {
  it("names the pan and the recommended action", () => {
    const message = build();

    expect(message).toContain("Pan: Northern Evaporation Pan");
    expect(message).toContain("Recommendation: TRANSFER BRINE");
  });

  it("states the current salinity and the water depth", () => {
    const message = build();

    expect(message).toContain("Current salinity: 282 g/L");
    expect(message).toContain("Water depth: 19 cm");
  });

  it("states the rainfall scenario that was simulated", () => {
    const message = build();

    expect(message).toContain(
      "Rainfall scenario: 20 mm (request.rainfall_mm)",
    );
  });

  it("states the simulated post-rain salinity as simulated", () => {
    const message = build();

    expect(message).toContain("Simulated salinity after rain: 268.4 g/L");
  });

  it("states the pan's configured harvest target", () => {
    const message = build();

    expect(message).toContain("Target harvest salinity: 285 g/L");
  });

  it("names the decision rule that decided", () => {
    const message = build();

    expect(message).toContain("Decision rule: wet-season harvest ladder v1");
  });

  it("states the decision basis in the panel's own words", () => {
    const message = build();

    expect(message).toContain(
      "Decision basis: Physics and the written rule only — no model estimate was read.",
    );
  });

  it("says no ML prediction was used when none was read", () => {
    const message = build();

    expect(message).toContain(NO_ML_STATEMENT);
  });

  it("makes no claim of a missing model when one was read", () => {
    const withEstimate = advice({
      basis: "physics_and_ml",
      notice: "Physics and the deployed model v1.0.0 were both read.",
      ml_result: {
        prediction_id: "pred-1",
        model_id: "ml-1",
        model_version: "v1.0.0",
        target: "salinity_risk",
        predicted_class: "high",
        probability: 0.82,
        confidence: null,
        probability_is_null_reason: null,
        predicted_at: "2026-03-14T12:00:00Z",
        twin_as_of: "2026-03-14T12:00:00Z",
        input_snapshot_id: "snap-1",
        explanation_id: null,
        matches_recommendation_snapshot: true,
      },
    });

    expect(adviceHasModelEstimate(withEstimate)).toBe(true);
    const message = buildFarmerWhatsAppMessage({ pan: PAN, advice: withEstimate });
    expect(message).not.toContain(NO_ML_STATEMENT);
  });

  it("says a figure is not recorded rather than showing a blank", () => {
    const message = build({
      twin: {
        ...advice().twin,
        salinity_g_l: null,
        water_depth_cm: null,
      },
    });

    expect(message).toContain("Current salinity: not recorded");
    expect(message).toContain("Water depth: not recorded");
  });

  it("carries the fixed verification disclaimer", () => {
    const message = build();

    expect(message).toContain(FARMER_MESSAGE_DISCLAIMER);
  });

  it("is one string with the lines the operator reviews", () => {
    const message = build();

    expect(message.split("\n").length).toBeGreaterThan(8);
    expect(message.startsWith("SaltLens DSS - Salt-pan recommendation")).toBe(true);
  });

  it("drops the trailing zero but keeps the decimal that matters", () => {
    const message = build({
      twin: { ...advice().twin, salinity_g_l: 300.4, water_depth_cm: 19 },
    });

    expect(message).toContain("Current salinity: 300.4 g/L");
    expect(message).toContain("Target harvest salinity: 285 g/L");
  });
});
