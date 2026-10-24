import { describe, expect, it } from "vitest";
import {
  ACTION_GLOSSARY,
  PROVENANCE,
  PROVENANCE_KINDS,
  PROVENANCE_ORDER,
  adviceIsModelInformed,
  conditionReadings,
  describeAction,
  describeTwinCurrency,
  humanizeFeature,
  priorityLabel,
  priorityTone,
  provenanceOf,
  shapFactorsAreFlat,
  simulationRequest,
  topShapFactors,
  twinSources,
  weatherReadings,
} from "@/lib/dashboard";
import { TWIN_DISPLAY_QUANTITIES, type DigitalTwinState } from "@/lib/digitalTwin";
import type { FeatureAttribution, LocalExplanation } from "@/lib/explanation";
import type { DecisionAdvice } from "@/lib/decisions";
import type { SaltPan } from "@/lib/pans";

function makeTwin(overrides: Partial<DigitalTwinState> = {}): DigitalTwinState {
  return {
    twin_id: "twin-1",
    pan_id: "kzn-2026-a01",
    pan_name: "Northern Evaporation Pan",
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
  farmer_name: null,
  farmer_whatsapp_masked: null,
  latest_observation: null,
};

function attribution(
  feature: string,
  shap_value: number,
  overrides: Partial<FeatureAttribution> = {},
): FeatureAttribution {
  return {
    feature,
    feature_value: 1,
    shap_value,
    direction: shap_value > 0 ? "increases_risk" : shap_value < 0 ? "decreases_risk" : "neutral",
    source: null,
    source_kind: "digital_twin_state",
    imputed: false,
    ...overrides,
  };
}

function makeExplanation(attributions: FeatureAttribution[]): LocalExplanation {
  return {
    explanation_id: "exp-1",
    prediction_id: "pred-1",
    pan_id: "kzn-2026-a01",
    model_id: "m1",
    model_version: "0.2.0",
    model_name: "Gradient boosting",
    algorithm: "gradient_boosting",
    kind: "local",
    created_at: "2026-03-14T12:00:00Z",
    target_class: "high",
    input_snapshot_id: "sha256:abc",
    twin_id: "twin-1",
    twin_as_of: "2026-03-14T12:00:00Z",
    quantities: {
      model_probability: { label: "p", value: 0.7, unit: "", note: "" },
      shap_contribution: {
        label: "c",
        unit: "",
        base_value: 0.1,
        explained_output: "high",
        target_class: "high",
        note: "",
      },
      physical_simulation: { label: "s", unit: "", results: {}, note: "" },
    },
    model_probability: 0.7,
    base_value: 0.1,
    attribution_error: 0.001,
    method: "tree_explainer",
    approximate: false,
    shap_version: "0.46.0",
    background: {
      source: "training_split",
      rows: 40,
      width: 20,
      train_rows: 40,
      holdout_rows: null,
      selection: null,
    },
    attributions,
    top_increasing: [],
    top_decreasing: [],
  };
}

describe("the provenance taxonomy", () => {
  it("is exactly the six categories, and no more", () => {
    expect(PROVENANCE_ORDER).toEqual([
      "observed",
      "forecast",
      "simulated",
      "ml",
      "recommended",
      "operator_decision",
    ]);
    expect(Object.keys(PROVENANCE).sort()).toEqual([...PROVENANCE_KINDS].sort());
  });

  it("gives every category a distinct colour, so two categories never look alike", () => {
    const colours = PROVENANCE_KINDS.map((kind) => PROVENANCE[kind].color);
    expect(new Set(colours).size).toBe(PROVENANCE_KINDS.length);
  });

  it("keeps the provenance scale apart from the status scale", () => {
    // A figure that borrowed a status colour would have taken on a severity it
    // never had. A forecast is not severe; a simulation is never "danger".
    const statusColours = new Set([
      "var(--color-active)",
      "var(--color-caution)",
      "var(--color-danger)",
      "var(--color-demo)",
    ]);
    for (const kind of PROVENANCE_KINDS) {
      expect(statusColours.has(PROVENANCE[kind].color)).toBe(false);
    }
  });

  it("states what produced each category, so the label is a claim and not a colour", () => {
    for (const kind of PROVENANCE_KINDS) {
      expect(PROVENANCE[kind].meaning.length).toBeGreaterThan(40);
      expect(PROVENANCE[kind].label.length).toBeGreaterThan(0);
      expect(PROVENANCE[kind].verb.length).toBeGreaterThan(0);
    }
  });

  it("resolves a category to its definition", () => {
    expect(provenanceOf("simulated").label).toBe("Simulated");
  });
});

describe("the readings a card is allowed to show", () => {
  it("gives every condition reading the observed category", () => {
    const readings = conditionReadings(makeTwin());
    expect(readings.map((item) => item.key)).toEqual([
      "salinity_g_l",
      "water_depth_cm",
      "brine_temperature_c",
      "humidity_percent",
    ]);
    expect(readings.every((item) => item.provenance === "observed")).toBe(true);
  });

  it("gives every weather reading the forecast category, including the timestamp", () => {
    const readings = weatherReadings(makeTwin());
    expect(readings.every((item) => item.provenance === "forecast")).toBe(true);
  });

  it("never gives an observed and a forecast reading the same category", () => {
    // The mixing this guards against is the one that matters most: an operator
    // comparing a measured salinity with a forecast rainfall in the same glance.
    const observed = conditionReadings(makeTwin()).map((item) => item.provenance);
    const forecast = weatherReadings(makeTwin()).map((item) => item.provenance);
    expect(observed.some((item) => forecast.includes(item))).toBe(false);
  });

  it("records which stored record supplied each figure", () => {
    const [salinity, , , humidity] = conditionReadings(makeTwin());
    expect(salinity.fromLabel).toBe("Pan observation");
    // Humidity is grouped with the pan's condition but did not come from the pan.
    expect(humidity.fromLabel).toBe("Weather observation");
    expect(humidity.fromTimestamp).toBe("2026-03-14T11:30:00Z");
  });

  it("keeps a measured zero as a reading, because a zero was measured", () => {
    const [salinity] = conditionReadings(makeTwin({ salinity_g_l: 0 }));
    expect(salinity.available).toBe(true);
    expect(salinity.value).toBe("0.0");
  });

  it("keeps an unmeasured quantity as a gap carrying its reason, never a zero", () => {
    const twin = makeTwin({
      brine_temperature_c: null,
      missing_inputs: [
        {
          field: "brine_temperature_c",
          reason: "No pan observation has been recorded, so the brine temperature is unavailable.",
          expected_from: "observation",
        },
      ],
    });
    const reading = conditionReadings(twin)[2];
    expect(reading.available).toBe(false);
    expect(reading.value).toContain("No pan observation");
    expect(reading.unit).toBeNull();
    expect(reading.reason).not.toBeNull();
  });

  it("states that no forecast is in force rather than showing a zero rainfall", () => {
    const readings = weatherReadings(
      makeTwin({ weather_forecast: null, forecast_rainfall_mm: null }),
    );
    const rainfall = readings[0];
    expect(rainfall.available).toBe(false);
    expect(rainfall.value).not.toBe("0.0");
  });
});

describe("the twin's provenance", () => {
  it("lists all three contributing records", () => {
    const sources = twinSources(makeTwin());
    expect(sources.map((item) => item.kind)).toEqual([
      "observation",
      "weather",
      "weather_forecast",
    ]);
  });

  it("grades each record's age rather than the twin's own assembly time", () => {
    const sources = twinSources(makeTwin());
    expect(sources[0].age).toBe("1 hour ago");
    expect(sources[0].freshness.label).toBe("Current");
  });

  it("grades the state by its oldest contributing reading", () => {
    const fresh = describeTwinCurrency(makeTwin());
    expect(fresh.tone).toBe("active");
    // The oldest of the three is the forecast at two hours, not the reading at one.
    expect(fresh.headline).toContain("2 hours ago");

    const stale = describeTwinCurrency(
      makeTwin({
        observation: { id: "o", timestamp: "2026-01-01T00:00:00Z", source: "manual", age_seconds: 5_000_000 },
        weather: { id: "w", timestamp: "2026-01-01T00:00:00Z", source: "manual", age_seconds: 5_000_000 },
        weather_forecast: { id: "f", timestamp: "2026-01-01T00:00:00Z", source: "manual", age_seconds: 5_000_000 },
      }),
    );
    expect(stale.tone).toBe("danger");
  });

  it("says plainly when nothing has contributed to the state", () => {
    const currency = describeTwinCurrency(
      makeTwin({ observation: null, weather: null, weather_forecast: null }),
    );
    expect(currency.tone).toBe("danger");
    expect(currency.headline).toContain("No record");
  });
});

describe("the simulation request", () => {
  it("is built from recorded figures only, converting depth once", () => {
    const request = simulationRequest(PAN, makeTwin());
    expect(request.ok).toBe(true);
    if (!request.ok) return;
    expect(request.inputs.area_m2).toBe(12500);
    expect(request.inputs.initial_depth_m).toBe(0.125);
    expect(request.inputs.initial_salinity_g_l).toBe(285.4);
  });

  it("refuses to draw a curve when the starting brine is unknown", () => {
    const request = simulationRequest(PAN, makeTwin({ salinity_g_l: null }));
    expect(request.ok).toBe(false);
    if (request.ok) return;
    expect(request.missing).toEqual(["a measured salinity"]);
    expect(request.reason).toContain("assumed pan");
  });

  it("names every missing input at once rather than one at a time", () => {
    const request = simulationRequest(
      { ...PAN, area_m2: 0 },
      makeTwin({ salinity_g_l: null, water_depth_cm: null }),
    );
    expect(request.ok).toBe(false);
    if (request.ok) return;
    expect(request.missing).toHaveLength(3);
  });

  it("refuses a pan with no registered area rather than assuming one", () => {
    const request = simulationRequest({ ...PAN, area_m2: 0 }, makeTwin());
    expect(request.ok).toBe(false);
  });
});

describe("the SHAP factor list", () => {
  const explanation = makeExplanation([
    attribution("current_salinity_g_l", 0.04),
    attribution("forecast_rainfall_mm", -0.31),
    attribution("water_depth_cm", 0.12),
    attribution("brine_temperature_c", 0),
    attribution("season", 0.02),
  ]);

  it("ranks by absolute contribution, so a large negative is not buried", () => {
    const factors = topShapFactors(explanation);
    expect(factors.map((item) => item.feature)).toEqual([
      "forecast_rainfall_mm",
      "water_depth_cm",
      "current_salinity_g_l",
      "season",
      "brine_temperature_c",
    ]);
  });

  it("honours the requested count", () => {
    expect(topShapFactors(explanation, 2)).toHaveLength(2);
  });

  it("formats contributions in probability points, never as a probability", () => {
    const [largest] = topShapFactors(explanation);
    expect(largest.display).toBe("-31.0 pp");
    expect(largest.display).not.toContain("%");
  });

  it("gives every feature a readable name", () => {
    const factors = topShapFactors(explanation);
    const rainfall = factors.find((item) => item.feature === "forecast_rainfall_mm");
    expect(rainfall?.label).toBe("Forecast rainfall");
  });

  it("marks which factors the mass balance supplied, so a simulated figure is not read as a reading", () => {
    const withPhysics = makeExplanation([
      attribution("physics_predicted_post_rain_salinity_g_l", 0.2, {
        source_kind: "physical_simulation",
      }),
    ]);
    expect(topShapFactors(withPhysics)[0].simulated).toBe(true);
    expect(topShapFactors(explanation)[0].simulated).toBe(false);
  });

  it("marks an imputed factor, because the model never read it", () => {
    const imputed = makeExplanation([
      attribution("recent_rainfall_mm", 0.1, { feature_value: null, imputed: true }),
    ]);
    expect(topShapFactors(imputed)[0].imputed).toBe(true);
  });

  it("recognises a model that moved nothing", () => {
    const flat = makeExplanation([
      attribution("season", 0),
      attribution("hour_of_day_utc", 0),
    ]);
    expect(shapFactorsAreFlat(topShapFactors(flat))).toBe(true);
    expect(shapFactorsAreFlat(topShapFactors(explanation))).toBe(false);
  });

  it("falls back to a readable form of an unknown feature name", () => {
    expect(humanizeFeature("some_feature_never_catalogued")).toBe(
      "Some feature never catalogued",
    );
  });
});

describe("the recommendation", () => {
  it("glosses all four actions and no others", () => {
    expect(Object.keys(ACTION_GLOSSARY).sort()).toEqual([
      "HARVEST NOW",
      "PROTECT / DRAIN",
      "TRANSFER BRINE",
      "WAIT AND RECHECK",
    ]);
  });

  it("glosses an action without asserting a threshold or a severity", () => {
    const text = describeAction("WAIT AND RECHECK");
    expect(text).toContain("no action");
    // A glossary that read as a second opinion would be the dashboard grading the
    // rule's decision, which is not this screen's standing.
    expect(text).not.toMatch(/\d/);
  });

  it("maps the server's own priority to a colour and invents no ranking", () => {
    expect(priorityTone("high")).toBe("danger");
    expect(priorityTone("medium")).toBe("caution");
    expect(priorityTone("low")).toBe("active");
    expect(priorityTone("something_new")).toBe("neutral");
    expect(priorityLabel("high")).toBe("High priority");
  });

  it("answers the one question about strength as provenance, not degree", () => {
    const advice = { basis: "physics_and_ml" } as DecisionAdvice;
    const rulesOnly = { basis: "physics_and_rules" } as DecisionAdvice;
    expect(adviceIsModelInformed(advice)).toBe(true);
    expect(adviceIsModelInformed(rulesOnly)).toBe(false);
  });
});
