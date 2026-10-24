/**
 * Decision panel tests.
 *
 * These are the tests that would catch the failures this module exists to prevent:
 * an operator acting on an action whose basis is not on screen. So the assertions
 * are about what is *absent* as much as what is present --
 *
 * - with no model estimate the panel shows the project's exact required label and
 *   offers no number, no percentage and no stand-in;
 * - no confidence, score, strength or percentage is rendered anywhere, because a
 *   rule either decided or it did not and no likelihood was computed for it;
 * - a class-only model's missing probability is stated, and `0%` appears nowhere;
 * - the four kinds of evidence each appear under their own heading, and every input
 *   row names where its value came from, so a simulated salinity cannot be read as
 *   a measured one;
 * - a variable the rule could not read keeps its row and says why;
 * - each refusal shows one fixed sentence, names what must exist to get past it,
 *   and offers no retry.
 *
 * The panel asks for advice only when the button is pressed. A test asserts that
 * too: mounting it must not write a stored recommendation, because each request is
 * one row in a history meant to be checked against what happened later.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DecisionPanel from "@/components/decisions/DecisionPanel";
import { ApiError } from "@/lib/api";
import type { SaltPan } from "@/lib/pans";
import {
  ML_UNAVAILABLE_LABEL,
  type DecisionAdvice,
} from "@/lib/decisions";
import { ADVICE_REFUSAL_MESSAGES } from "@/lib/decisionsRefusals";
import { advise } from "@/lib/decisionsApi";
import { farmerWhatsAppLink } from "@/lib/whatsappApi";

vi.mock("@/lib/decisionsApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/decisionsApi")>(
      "@/lib/decisionsApi",
    );
  return { ...actual, advise: vi.fn() };
});

vi.mock("@/lib/whatsappApi", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/whatsappApi")>(
      "@/lib/whatsappApi",
    );
  return { ...actual, farmerWhatsAppLink: vi.fn() };
});

const adviseMock = vi.mocked(advise);
const farmerLinkMock = vi.mocked(farmerWhatsAppLink);

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
  farmer_whatsapp_masked: null,
  latest_observation: null,
};

/** The same pan, but with a farmer contact recorded, so sharing is possible. */
const PAN_WITH_NUMBER: SaltPan = {
  ...PAN,
  farmer_whatsapp_masked: "+919•••••••10",
};

const NO_ESTIMATE_NOTICE =
  `${ML_UNAVAILABLE_LABEL} No operational-risk prediction is stored for this pan, ` +
  "so no model estimate was read. The recommendation rests on the recorded twin " +
  "state, the mass balance and the written rule alone.";

function advice(overrides: Partial<DecisionAdvice> = {}): DecisionAdvice {
  return {
    recommendation: "TRANSFER BRINE",
    reason:
      "Rule 'wet-season harvest ladder' version 1: simulated_post_rain_salinity_g_l gte 296 (read 300.4 from simulation) — the match decided.",
    inputs_used: [
      {
        variable: "simulated_post_rain_salinity_g_l",
        value: 300.4,
        unit: "g/L",
        source: "simulation",
        available: true,
        reason: null,
      },
      {
        variable: "current_salinity_g_l",
        value: 300.4,
        unit: "g/L",
        source: "twin",
        available: true,
        reason: null,
      },
    ],
    simulation_result: {
      run_id: "run-1",
      model_name: "rainfall-dilution-mass-balance-v1",
      is_ml: false,
      rainfall_mm: 0,
      rainfall_source: "twin.forecast_rainfall_mm",
      initial_salinity_g_l: 300.4,
      initial_depth_cm: 12,
      post_rain_salinity_g_l: 300.4,
      post_rain_depth_cm: 12,
      salinity_drop_g_l: 0,
      dilution_percent: 0,
      dilution_percent_reason: null,
      assumptions: ["Complete mixing: one uniform salinity instantly."],
      disclaimer: "Physics-based simulation under simplifying assumptions; not an ML prediction.",
    },
    ml_result: null,
    rule_version: "wet-season harvest ladder v1 (sha256:abc123)",
    timestamp: "2026-03-14T12:05:00Z",
    basis: "physics_and_rules",
    notice: NO_ESTIMATE_NOTICE,
    recommendation_id: "rec-1",
    pan_id: "kzn-2026-b01",
    priority: "medium",
    rule_id: "decrule-1",
    rule_name: "wet-season harvest ladder",
    rule_version_number: 1,
    specification_hash: "sha256:abc123",
    matched_criteria: "simulated_post_rain_salinity_g_l gte 296 -> TRANSFER BRINE",
    criteria: [],
    twin: {
      twin_id: "twin-1",
      as_of: "2026-03-14T12:00:00Z",
      provenance: "observed",
      salinity_g_l: 300.4,
      water_depth_cm: 12,
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

const WITH_ESTIMATE = advice({
  basis: "physics_and_ml",
  notice: "Physics and the deployed model v1.0.0 were both read.",
  ml_result: {
    prediction_id: "pred-1",
    model_id: "model-1",
    model_version: "1.0.0",
    target: "operational_risk",
    predicted_class: "act now",
    probability: 0.82,
    confidence: 0.9,
    probability_is_null_reason: null,
    predicted_at: "2026-03-14T12:01:00Z",
    twin_as_of: "2026-03-14T12:00:00Z",
    input_snapshot_id: "sha256:snap",
    explanation_id: null,
    matches_recommendation_snapshot: true,
  },
  inputs_used: [
    ...advice().inputs_used,
    {
      variable: "predicted_risk_class",
      value: "act now",
      unit: null,
      source: "prediction",
      available: true,
      reason: null,
    },
  ],
});

/**
 * Every percentage rendered outside the physics block.
 *
 * Returns their text rather than just counting them, so a failure names the
 * element that put a number where no number belongs. Walks text nodes rather than
 * matching `textContent`, because an ancestor's textContent includes its
 * descendants' and would report the physics figure as an offender.
 */
function percentagesOutsideTheSimulation(): string[] {
  const simulation = screen.getByTestId("decision-simulation");
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const offenders: string[] = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.textContent?.trim() ?? "";
    if (!/%/.test(text)) continue;
    if (simulation.contains(node.parentElement)) continue;
    offenders.push(text);
  }
  return offenders;
}

async function ask() {
  render(<DecisionPanel pan={PAN} />);
  await userEvent.click(screen.getByTestId("decision-advise"));
}

beforeEach(() => {
  adviseMock.mockReset();
  farmerLinkMock.mockReset();
});

describe("asking for advice", () => {
  it("does nothing on mount, so opening the page writes no history", () => {
    render(<DecisionPanel pan={PAN} />);
    expect(adviseMock).not.toHaveBeenCalled();
    expect(screen.getByText("No recommendation requested")).toBeInTheDocument();
  });

  it("asks only when the button is pressed", async () => {
    adviseMock.mockResolvedValue(advice());
    await ask();
    expect(adviseMock).toHaveBeenCalledWith({ panId: "kzn-2026-b01" });
  });

  it("includes the selected what-if rainfall when provided", async () => {
    adviseMock.mockResolvedValue(advice());
    render(<DecisionPanel pan={PAN} rainfallMm={20} />);
    await userEvent.click(screen.getByTestId("decision-advise"));
    expect(adviseMock).toHaveBeenCalledWith({
      panId: "kzn-2026-b01",
      rainfallMm: 20,
    });
  });

  it("keeps a pan's advice off another pan", async () => {
    adviseMock.mockResolvedValue(advice());
    const { unmount } = render(<DecisionPanel pan={PAN} />);
    await userEvent.click(screen.getByTestId("decision-advise"));
    await waitFor(() => expect(screen.getByText("TRANSFER BRINE")).toBeInTheDocument());
    unmount();
    render(<DecisionPanel pan={{ ...PAN, pan_id: "kzn-2026-b02" }} />);
    expect(screen.getByText("No recommendation requested")).toBeInTheDocument();
  });
});

describe("the action", () => {
  it("is shown as the server's own wording, not rephrased", async () => {
    adviseMock.mockResolvedValue(advice({ recommendation: "PROTECT / DRAIN" }));
    await ask();
    expect(screen.getByText("PROTECT / DRAIN")).toBeInTheDocument();
  });

  it("is shown with the criterion that decided it", async () => {
    adviseMock.mockResolvedValue(advice());
    await ask();
    // In the headline, and again in the rule's own audit line.
    expect(screen.getAllByText(/the match decided/).length).toBeGreaterThanOrEqual(1);
  });
});

describe("with no model estimate", () => {
  it("shows the project's exact required label", async () => {
    adviseMock.mockResolvedValue(advice());
    await ask();
    const notice = screen.getByTestId("decision-notice");
    expect(notice.textContent?.startsWith(ML_UNAVAILABLE_LABEL)).toBe(true);
  });

  it("shows no number standing in for the missing estimate", async () => {
    adviseMock.mockResolvedValue(advice());
    await ask();
    expect(screen.getByTestId("decision-model")).toHaveTextContent(
      "None was read for this recommendation",
    );
  });

  it("labels the basis as rule only", async () => {
    adviseMock.mockResolvedValue(advice());
    await ask();
    expect(screen.getByText("Rule only")).toBeInTheDocument();
  });
});

describe("confidence", () => {
  it("appears nowhere on the panel when no estimate was read", async () => {
    adviseMock.mockResolvedValue(advice());
    await ask();
    const panel = document.body.textContent ?? "";
    // A rule that matched is either decisive or it is not. Any number standing in
    // for a strength would be compared against the model's own and imply a
    // likelihood nobody computed.
    for (const word of [
      "Confidence",
      "confidence",
      "confident",
      "Likelihood",
      "likelihood",
      "certainty",
      "certainty",
      "sure",
    ]) {
      expect(panel).not.toContain(word);
    }
    // The one percentage allowed on this panel is the physics dilution, which is a
    // real figure from the mass balance. Every percentage on screen is asserted to
    // sit inside that block, so a strength cannot be dressed as a number
    // anywhere else -- in the headline, the badge or the rule's audit line.
    expect(percentagesOutsideTheSimulation()).toEqual([]);
    expect(screen.getByTestId("decision-simulation")).toHaveTextContent("0.0%");
  });

  it("lets the physics report a real dilution percentage", async () => {
    // The bar above is about invented numbers, not about percentages as such. A
    // pan with no rain really does dilute by 0%, and hiding that would be the same
    // fault in the other direction.
    adviseMock.mockResolvedValue(advice());
    await ask();
    expect(screen.getByTestId("decision-simulation")).toHaveTextContent("0.0%");
  });

  it("appears nowhere for a model-informed recommendation either", async () => {
    adviseMock.mockResolvedValue(WITH_ESTIMATE);
    await ask();
    // Only the model's own figures may appear, and only labelled as the model's.
    const panel = document.body.textContent ?? "";
    expect(panel).not.toMatch(/rule confidence/i);
    expect(panel).not.toMatch(/recommendation confidence/i);
  });

  it("still shows the model's own confidence, attributed to the model", async () => {
    adviseMock.mockResolvedValue(WITH_ESTIMATE);
    await ask();
    expect(screen.getByText(/Model confidence 90.0%/)).toBeInTheDocument();
  });
});

describe("a class-only model", () => {
  it("shows the class and states the absence, with 0% nowhere", async () => {
    adviseMock.mockResolvedValue(
      advice({
        basis: "physics_and_ml",
        ml_result: {
          ...WITH_ESTIMATE.ml_result!,
          probability: null,
          confidence: null,
          probability_is_null_reason:
            "The stored prediction is from a class-only model, so it reported a risk class with no probability.",
        },
      }),
    );
    await ask();
    expect(screen.getByText("act now")).toBeInTheDocument();
    expect(screen.getByText("not reported")).toBeInTheDocument();
    // "0%" must not appear as the model's answer. The dilution figure below it may
    // legitimately be 0.0%, so the absence is asserted where the model is.
    expect(screen.getByTestId("decision-model")).not.toHaveTextContent("0%");
  });
});

describe("a stored explanation of the estimate", () => {
  it("names it, and says what it does and does not describe", async () => {
    adviseMock.mockResolvedValue(
      advice({
        basis: "physics_and_ml",
        ml_result: { ...WITH_ESTIMATE.ml_result!, explanation_id: "exp-abc123" },
      }),
    );
    await ask();
    const model = screen.getByTestId("decision-model");
    expect(model).toHaveTextContent("exp-abc123");
    // An attribution explains the model's number, not the operator's action, and
    // showing it beside the action without that distinction would invite the
    // reading that it justifies the action.
    expect(model).toHaveTextContent("not the action");
  });

  it("says nothing when the prediction was never explained", async () => {
    adviseMock.mockResolvedValue(WITH_ESTIMATE);
    await ask();
    expect(screen.getByTestId("decision-model")).not.toHaveTextContent(
      "explanation of this prediction",
    );
  });
});

describe("a stale estimate", () => {
  it("says the estimate is from a different state rather than hiding it", async () => {
    adviseMock.mockResolvedValue(
      advice({
        basis: "physics_and_ml",
        ml_result: {
          ...WITH_ESTIMATE.ml_result!,
          matches_recommendation_snapshot: false,
        },
      }),
    );
    await ask();
    expect(
      screen.getByText("The model estimate is from an earlier state"),
    ).toBeInTheDocument();
  });

  it("says nothing about staleness when the states match", async () => {
    adviseMock.mockResolvedValue(WITH_ESTIMATE);
    await ask();
    expect(
      screen.queryByText("The model estimate is from an earlier state"),
    ).not.toBeInTheDocument();
  });
});

describe("the four kinds of evidence", () => {
  it("each appear under their own heading", async () => {
    adviseMock.mockResolvedValue(WITH_ESTIMATE);
    await ask();
    expect(screen.getByText("Recorded state")).toBeInTheDocument();
    // "Mass balance" is both a heading and an input's source label, so it is
    // counted rather than matched once.
    expect(screen.getAllByText("Mass balance").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Model estimate")).toBeInTheDocument();
    expect(screen.getByText("The rule that decided")).toBeInTheDocument();
  });

  it("label every input with where its value came from", async () => {
    // This is what stops a simulated salinity being read as a measured one.
    adviseMock.mockResolvedValue(WITH_ESTIMATE);
    await ask();
    const table = screen.getByTestId("decision-inputs");
    expect(table).toHaveTextContent("Mass balance");
    expect(table).toHaveTextContent("Recorded twin state");
    expect(table).toHaveTextContent("Stored model estimate");
  });

  it("show the physics disclaimer with the physics figures", async () => {
    adviseMock.mockResolvedValue(advice());
    await ask();
    expect(screen.getByTestId("decision-simulation")).toHaveTextContent(
      "not an ML prediction",
    );
  });

  it("show the physics run id, so the arithmetic can be replayed", async () => {
    adviseMock.mockResolvedValue(advice());
    await ask();
    expect(screen.getByTestId("decision-simulation")).toHaveTextContent("run-1");
  });
});

describe("an input the rule could not read", () => {
  it("keeps its row and says why it is empty", async () => {
    adviseMock.mockResolvedValue(
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
    await ask();
    const table = screen.getByTestId("decision-inputs");
    expect(table).toHaveTextContent("predicted_risk_class");
    // A table of only the values that happened to be present would read as a
    // complete account.
    expect(table).toHaveTextContent("No operational-risk prediction is stored");
  });
});

describe("a refusal", () => {
  it("shows one fixed sentence for a missing rule", async () => {
    adviseMock.mockRejectedValue(
      new ApiError("No decision rule has been written.", {
        status: 409,
        code: "no_rule_defined",
      }),
    );
    await ask();
    await waitFor(() =>
      expect(screen.getByText(ADVICE_REFUSAL_MESSAGES.no_rule_defined)).toBeInTheDocument(),
    );
  });

  it("names what has to exist to get past it", async () => {
    adviseMock.mockRejectedValue(
      new ApiError("x", { status: 409, code: "no_rule_defined" }),
    );
    await ask();
    expect(screen.getByText(/POST \/api\/decisions\/rules/)).toBeInTheDocument();
  });

  it("offers no retry, because a rule will not appear by waiting", async () => {
    adviseMock.mockRejectedValue(
      new ApiError("x", { status: 409, code: "no_rule_defined" }),
    );
    await ask();
    await waitFor(() =>
      expect(screen.getByText(ADVICE_REFUSAL_MESSAGES.no_rule_defined)).toBeInTheDocument(),
    );
    expect(screen.queryByText("Try again")).not.toBeInTheDocument();
  });

  it("offers a retry for a server fault, where asking again could help", async () => {
    adviseMock.mockRejectedValue(new ApiError("x", { status: 503 }));
    await ask();
    await waitFor(() => expect(screen.getByText("Try again")).toBeInTheDocument());
  });

  it("shows the same sentence whichever way it is reached", async () => {
    adviseMock.mockRejectedValue(
      new ApiError("x", { status: 409, code: "no_twin_state" }),
    );
    await ask();
    await waitFor(() =>
      expect(screen.getByText(ADVICE_REFUSAL_MESSAGES.no_twin_state)).toBeInTheDocument(),
    );
  });
});

describe("demo mode", () => {
  it("explains that a demo pan has nothing to reason from", () => {
    render(<DecisionPanel pan={PAN} isDemo />);
    expect(screen.getByText("No recommendation in demo mode")).toBeInTheDocument();
  });

  it("offers no action, because a demo pan has no rule", () => {
    render(<DecisionPanel pan={PAN} isDemo />);
    expect(screen.queryByTestId("decision-advise")).not.toBeInTheDocument();
  });
});

describe("a recommendation from demonstration data", () => {
  const fromDemonstration = (sources: string[] = ["observation_data_source"]) =>
    advice({
      notice: `${NO_ESTIMATE_NOTICE} DEMONSTRATION ONLY: built from demonstration data.`,
      twin: {
        ...advice().twin,
        is_demonstration: true,
        demonstration_sources: sources,
      },
    });

  it("carries the label above the action", async () => {
    adviseMock.mockResolvedValue(fromDemonstration());
    await ask();
    const banner = screen.getByTestId("decision-demonstration-banner");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent("DEMONSTRATION ONLY");
    // Above the action in the document, because the action is the part acted on.
    // Compared by position in the rendered HTML rather than by
    // compareDocumentPosition, which reports relationship in both directions at
    // once and would pass for an ancestor-descendant pair that is not siblings.
    const order = Array.from(document.querySelectorAll("[data-testid]")).map(
      (node) => node.getAttribute("data-testid"),
    );
    expect(order.indexOf("decision-demonstration-banner")).toBeLessThan(
      order.lastIndexOf("decision-action"),
    );
  });

  it("names which inputs were demonstrations", async () => {
    adviseMock.mockResolvedValue(fromDemonstration(["observation_data_source"]));
    await ask();
    expect(screen.getByTestId("decision-demonstration-banner")).toHaveTextContent(
      "observation_data_source",
    );
  });

  it("says the action must not be acted on", async () => {
    adviseMock.mockResolvedValue(fromDemonstration());
    await ask();
    expect(screen.getByTestId("decision-demonstration-banner")).toHaveTextContent(
      /must not be acted on/i,
    );
  });

  it("is absent on a recommendation from real records", async () => {
    adviseMock.mockResolvedValue(advice());
    await ask();
    expect(
      screen.queryByTestId("decision-demonstration-banner"),
    ).not.toBeInTheDocument();
  });
});

describe("the thresholds behind a recommendation", () => {
  const configured = advice({
    recommendation: "HARVEST NOW",
    matched_criteria:
      "simulated_post_rain_salinity_g_l lte target_harvest_salinity_g_l -> HARVEST NOW",
    criteria: [
      {
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
      },
    ],
  });

  it("shows the figure that was compared, not only the sentence", async () => {
    adviseMock.mockResolvedValue(configured);
    await ask();
    const row = screen.getByTestId("decision-criterion");
    expect(row).toHaveTextContent("240");
    expect(row).toHaveTextContent("250");
  });

  it("marks a bound the operator configured as theirs, not the rule's", async () => {
    adviseMock.mockResolvedValue(configured);
    await ask();
    // A reviewer has no case for arguing with a number the operator chose, only
    // with the rule that used it, so the panel has to make that distinction.
    expect(screen.getByTestId("decision-criterion-configured")).toHaveTextContent(
      "Your configured limit",
    );
  });

  it("shows the author's stated reason for the comparison", async () => {
    adviseMock.mockResolvedValue(configured);
    await ask();
    expect(screen.getByTestId("decision-criterion")).toHaveTextContent(
      "Harvest once the predicted salinity reaches the target.",
    );
  });

  it("does not mark a bound the rule asserted as configured", async () => {
    adviseMock.mockResolvedValue(
      advice({
        criteria: [
          {
            ...configured.criteria[0],
            bound_variable: null,
            comparator: "simulated_post_rain_salinity_g_l lte 250",
            rationale: "Harvest at or below 250 g/L.",
          },
        ],
      }),
    );
    await ask();
    expect(
      screen.queryByTestId("decision-criterion-configured"),
    ).not.toBeInTheDocument();
  });

  it("shows no table when nothing fired and the fallback was used", async () => {
    adviseMock.mockResolvedValue(advice({ criteria: [] }));
    await ask();
    expect(screen.queryByTestId("decision-criteria")).not.toBeInTheDocument();
  });
});

describe("sharing the recommendation with the farmer", () => {
  function link(overrides: Partial<ReturnType<typeof farmerLinkResult>> = {}) {
    return { ...farmerLinkResult(), ...overrides };
  }

  function farmerLinkResult() {
    return {
      pan_id: "kzn-2026-b01",
      recipient_contact_reference: "+919•••••••10",
      // The server percent-encodes the message. A realistic link carries encoded
      // spaces, newlines, ampersands and plus signs, so the client is exercised
      // against a URL it must pass through untouched.
      click_to_chat_url:
        "https://wa.me/919876543210?text=SaltLens%20DSS%0ARecommendation%3A%20TRANSFER%20BRINE%20%26%20recheck%20%2B%20depth",
      message_character_count: 42,
    };
  }

  it("offers the button only after a recommendation is on screen", async () => {
    adviseMock.mockResolvedValue(advice());
    render(<DecisionPanel pan={PAN_WITH_NUMBER} />);

    expect(screen.queryByTestId("decision-whatsapp-open")).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("decision-advise"));

    await waitFor(() =>
      expect(screen.getByTestId("decision-whatsapp-open")).toBeInTheDocument(),
    );
  });

  it("builds the message from the advice and the pan, and opens WhatsApp", async () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    adviseMock.mockResolvedValue(advice());
    farmerLinkMock.mockResolvedValue(link());

    render(<DecisionPanel pan={PAN_WITH_NUMBER} />);
    await userEvent.click(screen.getByTestId("decision-advise"));
    await waitFor(() => screen.getByTestId("decision-whatsapp-open"));
    await userEvent.click(screen.getByTestId("decision-whatsapp-open"));

    await waitFor(() => expect(farmerLinkMock).toHaveBeenCalledTimes(1));
    const [panId, message] = farmerLinkMock.mock.calls[0];

    expect(panId).toBe("kzn-2026-b01");
    expect(message).toContain("Pan: Northern Evaporation Pan");
    expect(message).toContain("Recommendation: TRANSFER BRINE");
    expect(message).toContain("Current salinity: 300.4 g/L");
    expect(message).toContain("Water depth: 12 cm");
    expect(message).toContain("Rainfall scenario: 0 mm (twin.forecast_rainfall_mm)");
    expect(message).toContain("Simulated salinity after rain: 300.4 g/L");
    expect(message).toContain("Target harvest salinity: 285 g/L");
    expect(message).toContain("Decision rule: wet-season harvest ladder v1");
    expect(message).toContain("Decision basis: Physics and the written rule only");
    expect(message).toContain(
      "No ML prediction or model estimate was used for this recommendation.",
    );

    expect(openSpy).toHaveBeenCalledWith(
      "https://wa.me/919876543210?text=SaltLens%20DSS%0ARecommendation%3A%20TRANSFER%20BRINE%20%26%20recheck%20%2B%20depth",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("opens the encoded URL exactly as the server returned it", async () => {
    // The client must not re-encode, decode or truncate the link: WhatsApp needs
    // the message percent-encoded, and a second encoding would change the text.
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const encoded =
      "https://wa.me/919876543210?text=Line%20one%0ALine%20two%20%26%20more%20%2B%20sign";
    adviseMock.mockResolvedValue(advice());
    farmerLinkMock.mockResolvedValue(link({ click_to_chat_url: encoded }));

    render(<DecisionPanel pan={PAN_WITH_NUMBER} />);
    await userEvent.click(screen.getByTestId("decision-advise"));
    await waitFor(() => screen.getByTestId("decision-whatsapp-open"));
    await userEvent.click(screen.getByTestId("decision-whatsapp-open"));

    await waitFor(() => expect(openSpy).toHaveBeenCalledWith(
      encoded,
      "_blank",
      "noopener,noreferrer",
    ));
  });

  it("does not open WhatsApp until the button is pressed", async () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    adviseMock.mockResolvedValue(advice());

    render(<DecisionPanel pan={PAN_WITH_NUMBER} />);
    await userEvent.click(screen.getByTestId("decision-advise"));
    await waitFor(() => screen.getByTestId("decision-whatsapp-open"));

    expect(farmerLinkMock).not.toHaveBeenCalled();
    expect(openSpy).not.toHaveBeenCalled();
  });

  it("disables the button and explains when no number is stored", async () => {
    adviseMock.mockResolvedValue(advice());

    render(<DecisionPanel pan={PAN} />);
    await userEvent.click(screen.getByTestId("decision-advise"));
    await waitFor(() => screen.getByTestId("decision-whatsapp-open"));

    expect(screen.getByTestId("decision-whatsapp-open")).toBeDisabled();
    expect(
      screen.getByTestId("decision-whatsapp-missing-number"),
    ).toHaveTextContent("Add the farmer's number on the pan");
  });

  it("shows the masked recipient when a number is stored", async () => {
    adviseMock.mockResolvedValue(advice());

    render(<DecisionPanel pan={PAN_WITH_NUMBER} />);
    await userEvent.click(screen.getByTestId("decision-advise"));

    await waitFor(() =>
      expect(screen.getByTestId("decision-whatsapp-recipient")).toHaveTextContent(
        "+919•••••••10",
      ),
    );
    expect(
      screen.queryByTestId("decision-whatsapp-missing-number"),
    ).not.toBeInTheDocument();
  });

  it("reports a contact refusal without offering a retry", async () => {
    adviseMock.mockResolvedValue(advice());
    farmerLinkMock.mockRejectedValue(
      new ApiError("server prose", {
        status: 409,
        code: "farmer_contact_not_configured",
      }),
    );

    render(<DecisionPanel pan={PAN_WITH_NUMBER} />);
    await userEvent.click(screen.getByTestId("decision-advise"));
    await waitFor(() => screen.getByTestId("decision-whatsapp-open"));
    await userEvent.click(screen.getByTestId("decision-whatsapp-open"));

    await waitFor(() =>
      expect(screen.getByTestId("decision-whatsapp-error")).toBeInTheDocument(),
    );
    expect(screen.getByTestId("decision-whatsapp-open-pan")).toBeInTheDocument();
  });
});
