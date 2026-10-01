import { describe, expect, it } from "vitest";
import {
  adviceHasModelEstimate,
  boundIsOperatorConfigured,
  DECISION_ACTIONS,
  DEMONSTRATION_ONLY_LABEL,
  describeBasis,
  describeSource,
  estimateIsStale,
  formatDilution,
  formatProbability,
  inputRows,
  ML_UNAVAILABLE_LABEL,
  reportsNoProbability,
  type DecisionAdvice,
  type DecisionCriterionFired,
  type DecisionMlResult,
  type DecisionSimulationResult,
} from "@/lib/decisions";
import {
  ADVICE_REFUSAL_MESSAGES,
  ADVICE_REFUSAL_REMEDIES,
  ADVICE_REFUSALS,
} from "@/lib/decisionsRefusals";

const simulation = (
  overrides: Partial<DecisionSimulationResult> = {},
): DecisionSimulationResult => ({
  run_id: "run-1",
  model_name: "rainfall-dilution-mass-balance-v1",
  is_ml: false,
  rainfall_mm: 20,
  rainfall_source: "twin.forecast_rainfall_mm",
  initial_salinity_g_l: 300,
  initial_depth_cm: 12,
  post_rain_salinity_g_l: 257.1,
  post_rain_depth_cm: 14,
  salinity_drop_g_l: 42.9,
  dilution_percent: 14.3,
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
  probability: 0.82,
  confidence: 0.9,
  probability_is_null_reason: null,
  predicted_at: "2026-03-10T18:00:00Z",
  twin_as_of: "2026-03-10T18:00:00Z",
  input_snapshot_id: "sha256:abc",
  explanation_id: null,
  matches_recommendation_snapshot: true,
  ...overrides,
});

const advice = (overrides: Partial<DecisionAdvice> = {}): DecisionAdvice => ({
  recommendation: "TRANSFER BRINE",
  reason: "Rule 'wet-season' v1: salinity gte 296 — the match decided.",
  inputs_used: [],
  simulation_result: simulation(),
  ml_result: null,
  rule_version: "wet-season v1 (sha256:abc)",
  timestamp: "2026-03-10T18:05:00Z",
  basis: "physics_and_rules",
  notice: ML_UNAVAILABLE_LABEL,
  recommendation_id: "rec-1",
  pan_id: "kzn-2026-b01",
  priority: "medium",
  rule_id: "rule-1",
  rule_name: "wet-season",
  rule_version_number: 1,
  specification_hash: "sha256:abc",
  matched_criteria: "simulated_post_rain_salinity_g_l gte 296 -> TRANSFER BRINE",
  criteria: [],
  twin: {
    twin_id: "twin-1",
    as_of: "2026-03-10T18:00:00Z",
    provenance: "observed",
    salinity_g_l: 300,
    water_depth_cm: 12,
    forecast_rainfall_mm: 20,
    observation_id: "obs-1",
    observation_timestamp: "2026-03-10T18:00:00Z",
    weather_forecast_id: "wx-1",
    weather_forecast_timestamp: "2026-03-09T00:00:00Z",
    missing_fields: [],
    is_demonstration: false,
    demonstration_sources: [],
  },
  title: "TRANSFER BRINE",
  body: "Recommended action: TRANSFER BRINE.",
  expires_at: null,
  ...overrides,
});

describe("the action vocabulary", () => {
  it("is exactly the four actions and nothing else", () => {
    expect([...DECISION_ACTIONS].sort()).toEqual([
      "HARVEST NOW",
      "PROTECT / DRAIN",
      "TRANSFER BRINE",
      "WAIT AND RECHECK",
    ]);
  });

  it("holds no fifth value, so an unknown action is a type error not a blank", () => {
    expect(DECISION_ACTIONS).toHaveLength(4);
  });
});

describe("the unavailable label", () => {
  it("is the project's exact required wording", () => {
    expect(ML_UNAVAILABLE_LABEL).toBe(
      "Physics/rule-based recommendation — ML prediction unavailable.",
    );
  });
});

describe("adviceHasModelEstimate", () => {
  it("is false when no estimate was read", () => {
    expect(adviceHasModelEstimate(advice())).toBe(false);
  });

  it("is true when one was, and it is attached", () => {
    const withMl = advice({ basis: "physics_and_ml", ml_result: ml() });
    expect(adviceHasModelEstimate(withMl)).toBe(true);
  });
});

describe("reportsNoProbability", () => {
  it("is true for a class-only model, which is not the same as a probability of zero", () => {
    const classOnly = ml({
      probability: null,
      confidence: null,
      probability_is_null_reason: "The stored prediction is from a class-only model.",
    });
    expect(reportsNoProbability(classOnly)).toBe(true);
  });

  it("is false when the model reported one", () => {
    expect(reportsNoProbability(ml())).toBe(false);
  });
});

describe("formatProbability", () => {
  it("states the absence rather than rendering a zero", () => {
    // The single most damaging thing this panel could do is print 0% for a
    // likelihood the model never produced.
    expect(formatProbability(null)).toBe("not reported by the model");
    expect(formatProbability(null)).not.toContain("0");
  });

  it("keeps small probabilities precise and large ones readable", () => {
    expect(formatProbability(0.04)).toBe("4.00%");
    expect(formatProbability(0.847)).toBe("84.7%");
    expect(formatProbability(1)).toBe("100.0%");
  });
});

describe("formatDilution", () => {
  it("gives the reason when the ratio is undefined", () => {
    const zeroSalinity = simulation({
      dilution_percent: null,
      dilution_percent_reason: "The starting salinity was zero.",
    });
    expect(formatDilution(zeroSalinity)).toBe("The starting salinity was zero.");
  });

  it("never renders an undefined dilution as 0%", () => {
    const zeroSalinity = simulation({
      dilution_percent: null,
      dilution_percent_reason: "The starting salinity was zero.",
    });
    expect(formatDilution(zeroSalinity)).not.toBe("0.0%");
  });

  it("shows a real dilution to one decimal", () => {
    expect(formatDilution(simulation())).toBe("14.3%");
  });
});

describe("estimateIsStale", () => {
  it("is false with no estimate at all, which is absent rather than stale", () => {
    expect(estimateIsStale(advice())).toBe(false);
  });

  it("is true when the estimate came from an older twin state", () => {
    const withMl = advice({
      basis: "physics_and_ml",
      ml_result: ml({ matches_recommendation_snapshot: false }),
    });
    expect(estimateIsStale(withMl)).toBe(true);
  });

  it("is false when the estimate and the advice share a state", () => {
    const withMl = advice({ basis: "physics_and_ml", ml_result: ml() });
    expect(estimateIsStale(withMl)).toBe(false);
  });
});

describe("describeSource", () => {
  it("names each provenance in words rather than in enum values", () => {
    expect(describeSource("twin")).toBe("Recorded twin state");
    expect(describeSource("simulation")).toBe("Mass balance");
    expect(describeSource("prediction")).toBe("Stored model estimate");
    expect(describeSource("pan_registration")).toBe("Pan registration");
  });
});

describe("inputRows", () => {
  it("keeps a row for a variable the rule could not read, and says why", () => {
    // A table of only the values that happened to be present reads as a complete
    // account. A gap in the evidence has to be visible as a gap.
    const rows = inputRows(
      advice({
        inputs_used: [
          {
            variable: "predicted_risk_class",
            value: null,
            unit: null,
            source: "prediction",
            available: false,
            reason: "No operational-risk prediction is stored for this pan.",
          },
        ],
      }),
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].available).toBe(false);
    expect(rows[0].display).toContain("No operational-risk prediction is stored");
  });

  it("shows a value with its unit and names where it came from", () => {
    const rows = inputRows(
      advice({
        inputs_used: [
          {
            variable: "simulated_post_rain_salinity_g_l",
            value: 257.1,
            unit: "g/L",
            source: "simulation",
            available: true,
            reason: null,
          },
        ],
      }),
    );
    expect(rows[0].display).toBe("257.1 g/L");
    // The provenance is beside the number so a simulated salinity cannot be read
    // as a measured one.
    expect(rows[0].sourceLabel).toBe("Mass balance");
  });

  it("keeps simulated and recorded values distinguishable side by side", () => {
    const rows = inputRows(
      advice({
        inputs_used: [
          {
            variable: "current_salinity_g_l",
            value: 300,
            unit: "g/L",
            source: "twin",
            available: true,
            reason: null,
          },
          {
            variable: "simulated_post_rain_salinity_g_l",
            value: 257.1,
            unit: "g/L",
            source: "simulation",
            available: true,
            reason: null,
          },
        ],
      }),
    );
    expect(rows.map((row) => row.sourceLabel)).toEqual([
      "Recorded twin state",
      "Mass balance",
    ]);
  });
});

describe("describeBasis", () => {
  it("says the model was not consulted when it was not", () => {
    expect(describeBasis(advice())).toBe(
      "Physics and the written rule only — no model estimate was read.",
    );
  });

  it("names the model version when one was read", () => {
    const withMl = advice({ basis: "physics_and_ml", ml_result: ml() });
    expect(describeBasis(withMl)).toContain("v1.0.0");
  });
});

describe("the refusal table", () => {
  it("covers every code the engine refuses with", () => {
    expect(Object.keys(ADVICE_REFUSAL_MESSAGES).sort()).toEqual(
      [...ADVICE_REFUSALS].sort(),
    );
  });

  it("gives every refusal a sentence and a remedy", () => {
    for (const code of ADVICE_REFUSALS) {
      expect(ADVICE_REFUSAL_MESSAGES[code].length).toBeGreaterThan(0);
      expect(ADVICE_REFUSAL_REMEDIES[code].length).toBeGreaterThan(0);
    }
  });

  it("starts every sentence as a sentence, in one of the two stated forms", () => {
    // Two openings are accepted, and the test says why rather than being relaxed
    // to "starts with a capital". A refusal to evaluate evidence and a refusal to
    // issue a recommendation are different answers to the operator's question, and
    // the requirement is explicit that stale evidence reads as insufficient
    // evidence. A message opening any other way is still a failure here: this
    // asserts the vocabulary, not merely the punctuation.
    const openings = ["No recommendation — ", "Insufficient evidence — "];
    for (const code of ADVICE_REFUSALS) {
      expect(
        openings.some((opening) =>
          ADVICE_REFUSAL_MESSAGES[code].startsWith(opening),
        ),
      ).toBe(true);
    }
  });

  it("names the missing-evidence refusal as insufficient evidence", () => {
    // Asserted separately so that collapsing this case back into the generic
    // wording fails here, with a message saying what was lost, rather than
    // silently passing as one of the other refusals.
    expect(ADVICE_REFUSAL_MESSAGES.insufficient_evidence).toBe(
      "Insufficient evidence — something this rule needs is not there. Nothing was assumed in its place.",
    );
  });

  it("covers both causes of insufficient evidence in its remedy", () => {
    // The backend raises this for an unconfigured limit and for a stale reading
    // alike, so a remedy that only mentions one of them would send an operator
    // to fix the wrong thing. Both routes have to appear.
    const remedy = ADVICE_REFUSAL_REMEDIES.insufficient_evidence;
    expect(remedy).toContain("risk thresholds");
    expect(remedy).toContain("max_observation_age_hours");
  });

  it("keeps stale evidence distinct from a rule that cannot be evaluated", () => {
    // The two must not share a message, or an operator cannot tell whether to go
    // and read the pan or to go and rewrite the rule.
    expect(ADVICE_REFUSAL_MESSAGES.insufficient_evidence).not.toBe(
      ADVICE_REFUSAL_MESSAGES.rule_undecided,
    );
    expect(ADVICE_REFUSAL_REMEDIES.insufficient_evidence).toContain(
      "max_observation_age_hours",
    );
  });
});

describe("operator-configured bounds", () => {
  const fired = (overrides: Partial<DecisionCriterionFired> = {}): DecisionCriterionFired => ({
    variable: "simulated_post_rain_salinity_g_l",
    operator: "lte",
    comparator:
      "simulated_post_rain_salinity_g_l lte target_harvest_salinity_g_l",
    action: "HARVEST NOW",
    rationale: "Harvest once the predicted salinity reaches the target.",
    value: 240,
    threshold: 250,
    bound_variable: "target_harvest_salinity_g_l",
    lower_bound: null,
    upper_bound: null,
    source: "simulation",
    ...overrides,
  });

  it("tells a configured bound from one the rule asserted", () => {
    expect(boundIsOperatorConfigured(fired())).toBe(true);
    expect(
      boundIsOperatorConfigured(
        fired({ bound_variable: null, comparator: "current_depth_cm gte 45" }),
      ),
    ).toBe(false);
  });

  it("keeps the two kinds of bound distinguishable at the same value", () => {
    // Both carry threshold: 250. Only one of them is a figure the operator chose,
    // and the bound_variable is the only field that says so -- which is why the
    // distinction is tested at an identical value rather than two different ones.
    const configured = fired();
    const asserted = fired({
      bound_variable: null,
      comparator: "simulated_post_rain_salinity_g_l lte 250",
    });
    expect(configured.threshold).toBe(asserted.threshold);
    expect(boundIsOperatorConfigured(configured)).not.toBe(
      boundIsOperatorConfigured(asserted),
    );
  });

  it("names the bound as its configured field, not as a number", () => {
    // The comparator is what a reader reads as the rule's argument. Printing the
    // resolved figure there would present a per-pan value as part of the rule.
    expect(fired().comparator).toContain("target_harvest_salinity_g_l");
    expect(fired().comparator).not.toContain("250");
  });
});

describe("the demonstration label", () => {
  it("is one exact string, so two surfaces cannot word it differently", () => {
    expect(DEMONSTRATION_ONLY_LABEL).toBe("DEMONSTRATION ONLY");
  });

  it("is not present on a recommendation built from real records", () => {
    const real = advice();
    expect(real.twin.is_demonstration).toBe(false);
    expect(real.twin.demonstration_sources).toEqual([]);
    expect(real.notice).not.toContain(DEMONSTRATION_ONLY_LABEL);
  });
});
