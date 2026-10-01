/**
 * Outcome validation, form shaping and display tests.
 *
 * The behaviours locked down here are the ones that could quietly turn a
 * research record into a flattering one:
 *
 * - a blank input stays blank and is sent as `null`, never as zero;
 * - zero stays a real measurement, because a gauge reading of zero is evidence;
 * - an event with nothing recorded is refused rather than stored as a row of
 *   nulls that would inflate the event count;
 * - a mean that does not exist renders as the dash, and every mean is shown with
 *   the number of events that was excluded from it.
 */

import { describe, expect, it } from "vitest";
import {
  EMPTY_OUTCOME_FORM,
  HARVEST_LABELS,
  LINK_LABELS,
  MAX_OPERATOR_ACTION_LENGTH,
  OUTCOME_ACTUAL_FIELDS,
  OUTCOME_MEASUREMENT_FIELDS,
  OUTCOME_RANGES,
  MISSING_DISPLAY,
  describeCoverage,
  describeMissing,
  emptyOutcomeForm,
  formatEventTimestamp,
  formatMeanAbsoluteError,
  formatOutcomeNumber,
  formatOutcomeValue,
  formatPercentage,
  formatSignedError,
  readLink,
  resetOutcomeForm,
  validateEventId,
  validateOperatorAction,
  validateOutcome,
  validateOutcomeMeasurement,
  validateOutcomeTimestamp,
  type ErrorStatistics,
  type EventOutcome,
  type OutcomeFormValues,
  type OutcomeLinkSet,
} from "@/lib/outcomes";

const PAN_ID = "kzn-2026-a01";

function form(overrides: Partial<OutcomeFormValues> = {}): OutcomeFormValues {
  return { ...emptyOutcomeForm(), event_id: "storm-2026-03-14", ...overrides };
}

function outcome(overrides: Partial<EventOutcome> = {}): EventOutcome {
  return {
    outcome_id: "outcome-1",
    pan_id: PAN_ID,
    event_id: "storm-2026-03-14",
    timestamp: "2026-03-14T18:00:00Z",
    created_at: "2026-03-14T18:05:00Z",
    forecast_rainfall_mm: 30,
    actual_rainfall_mm: 40,
    pre_event_salinity_g_l: 280,
    pre_event_depth_cm: 20,
    post_event_salinity_g_l: 255,
    post_event_depth_cm: 24,
    operator_action: null,
    harvest_outcome: null,
    simulation_error: {
      predicted: 233.33,
      actual: 255,
      predicted_from: "actual_rainfall_mm",
      predicted_from_label: "the rainfall that actually fell",
      unit: "g/L",
      signed_error: -21.67,
      absolute_error: 21.67,
      percentage_error_percent: -8.5,
      percentage_error_reason: null,
      is_comparable: true,
      missing_inputs: [],
      missing_fields: [],
    },
    forecast_simulation_error: {
      predicted: 243.48,
      actual: 255,
      predicted_from: "forecast_rainfall_mm",
      predicted_from_label: "the forecast rainfall",
      unit: "g/L",
      signed_error: -11.52,
      absolute_error: 11.52,
      percentage_error_percent: -4.5,
      percentage_error_reason: null,
      is_comparable: true,
      missing_inputs: [],
      missing_fields: [],
    },
    rainfall_forecast_error: {
      predicted: 30,
      actual: 40,
      predicted_from: "forecast_rainfall_mm",
      predicted_from_label: "the forecast rainfall",
      unit: "mm",
      signed_error: -10,
      absolute_error: 10,
      percentage_error_percent: -25,
      percentage_error_reason: null,
      is_comparable: true,
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
  };
}

function statistics(overrides: Partial<ErrorStatistics> = {}): ErrorStatistics {
  return {
    label: "Salinity error, predicted from the rainfall that actually fell",
    unit: "g/L",
    compared: 2,
    not_comparable: 0,
    coverage_percent: 100,
    mean_absolute_error: 21.67,
    mean_absolute_error_reason: null,
    bias: -21.67,
    bias_reason: null,
    largest_absolute_error: 21.67,
    ...overrides,
  };
}

// -- optional measurements -------------------------------------------------

describe("validateOutcomeMeasurement", () => {
  it("treats a blank input as not measured rather than as zero", () => {
    expect(validateOutcomeMeasurement("", "salinity_g_l")).toEqual({
      value: null,
      error: null,
    });
    expect(validateOutcomeMeasurement("   ", "rainfall_mm")).toEqual({
      value: null,
      error: null,
    });
  });

  it("keeps zero as a real measurement", () => {
    // A gauge reading of zero and a dry pan at zero depth are evidence.
    expect(validateOutcomeMeasurement("0", "rainfall_mm")).toEqual({
      value: 0,
      error: null,
    });
    expect(validateOutcomeMeasurement("0", "depth_cm").value).toBe(0);
  });

  it("rejects text and anything not finite", () => {
    expect(validateOutcomeMeasurement("abc", "salinity_g_l").error).toBe(
      "Salinity must be a number.",
    );
    expect(validateOutcomeMeasurement("Infinity", "salinity_g_l").error).toBe(
      "Salinity must be a number.",
    );
  });

  it("rejects a figure outside the plausibility range", () => {
    expect(validateOutcomeMeasurement("600", "salinity_g_l").error).toBe(
      "Salinity must be between 0 and 500 g/L.",
    );
    expect(validateOutcomeMeasurement("-1", "rainfall_mm").error).toBe(
      "Rainfall must be between 0 and 1000 mm.",
    );
  });

  it("accepts the range boundaries", () => {
    expect(validateOutcomeMeasurement("0", "salinity_g_l").value).toBe(0);
    expect(validateOutcomeMeasurement("500", "salinity_g_l").value).toBe(500);
  });
});

describe("the range table", () => {
  it("matches the backend's own ranges", () => {
    // The form and the API must agree, or the form accepts values that are
    // rejected on arrival.
    expect(OUTCOME_RANGES.rainfall_mm.min).toBe(0);
    expect(OUTCOME_RANGES.rainfall_mm.max).toBe(1000);
    expect(OUTCOME_RANGES.salinity_g_l.max).toBe(500);
    expect(OUTCOME_RANGES.depth_cm.max).toBe(1000);
  });

  it("covers every figure the operator is asked for", () => {
    // Forecast and actual rainfall both go through the same range check.
    expect(OUTCOME_MEASUREMENT_FIELDS).toContain("forecast_rainfall_mm");
    expect(OUTCOME_MEASUREMENT_FIELDS).toContain("actual_rainfall_mm");
    expect(OUTCOME_MEASUREMENT_FIELDS).toHaveLength(6);
  });

  it("names the three figures that make an event recordable", () => {
    expect(OUTCOME_ACTUAL_FIELDS).toEqual([
      "actual_rainfall_mm",
      "post_event_salinity_g_l",
      "post_event_depth_cm",
    ]);
  });
});

// -- event identity, time and narrative -----------------------------------

describe("validateEventId", () => {
  it("requires a name, because an unnamed event cannot be grouped", () => {
    const result = validateEventId("  ");
    expect(result.value).toBeNull();
    expect(result.error).toMatch(/Name this event/);
  });

  it("trims the name it returns", () => {
    expect(validateEventId("  storm-a  ").value).toBe("storm-a");
  });

  it("refuses a name longer than the backend accepts", () => {
    expect(validateEventId("x".repeat(65)).error).toMatch(/64 characters/);
  });
});

describe("validateOutcomeTimestamp", () => {
  it("treats a blank event time as the moment of saving", () => {
    expect(validateOutcomeTimestamp("").value).toBeNull();
  });

  it("converts a local input to an ISO instant", () => {
    const result = validateOutcomeTimestamp("2026-03-14T18:00");
    expect(result.error).toBeNull();
    expect(result.value).toMatch(/Z$/);
  });

  it("refuses an event that has not happened yet", () => {
    const now = new Date("2026-03-14T12:00:00Z");
    const result = validateOutcomeTimestamp("2026-03-15T12:00", { now });
    expect(result.value).toBeNull();
    expect(result.error).toMatch(/in the future/);
  });

  it("tolerates a small clock difference", () => {
    const now = new Date("2026-03-14T12:00:00Z");
    // Inside the same five-minute tolerance the backend allows.
    expect(validateOutcomeTimestamp("2026-03-14T12:03", { now }).value).not.toBeNull();
  });

  it("refuses text it cannot read", () => {
    expect(validateOutcomeTimestamp("not a time").error).toMatch(/could not be read/);
  });
});

describe("validateOperatorAction", () => {
  it("treats a blank action as not recorded", () => {
    expect(validateOperatorAction("  ").value).toBeNull();
  });

  it("refuses text longer than the backend accepts", () => {
    expect(
      validateOperatorAction("x".repeat(MAX_OPERATOR_ACTION_LENGTH + 1)).error,
    ).toMatch(/2000 characters/);
  });
});

// -- whole-form validation ------------------------------------------------

describe("validateOutcome", () => {
  it("builds a payload from what the operator measured", () => {
    const { payload, errors } = validateOutcome(
      form({
        actual_rainfall_mm: "40",
        forecast_rainfall_mm: "30",
        pre_event_salinity_g_l: "280",
        pre_event_depth_cm: "20",
        post_event_salinity_g_l: "255",
        post_event_depth_cm: "24",
        operator_action: "Opened the outlet gate.",
        harvest_outcome: "partial",
      }),
      { panId: PAN_ID },
    );

    expect(errors).toEqual({});
    expect(payload).toEqual({
      pan_id: PAN_ID,
      event_id: "storm-2026-03-14",
      forecast_rainfall_mm: 30,
      actual_rainfall_mm: 40,
      pre_event_salinity_g_l: 280,
      pre_event_depth_cm: 20,
      post_event_salinity_g_l: 255,
      post_event_depth_cm: 24,
      operator_action: "Opened the outlet gate.",
      harvest_outcome: "partial",
    });
  });

  it("sends a blank measurement as null rather than as zero", () => {
    const { payload } = validateOutcome(
      form({ actual_rainfall_mm: "", post_event_salinity_g_l: "255" }),
      { panId: PAN_ID },
    );

    expect(payload?.actual_rainfall_mm).toBeNull();
    expect(payload?.post_event_salinity_g_l).toBe(255);
  });

  it("omits the timestamp when the operator left it blank", () => {
    const { payload } = validateOutcome(
      form({ post_event_salinity_g_l: "255" }),
      { panId: PAN_ID },
    );
    expect(payload && "timestamp" in payload).toBe(false);
  });

  it("never carries a predicted figure, because the type has no field for one", () => {
    const { payload } = validateOutcome(
      form({ post_event_salinity_g_l: "255" }),
      { panId: PAN_ID },
    );
    // The backend forbids unknown keys, and this is what keeps a client from
    // grading the model on its own account.
    expect(Object.keys(payload ?? {}).sort()).toEqual([
      "actual_rainfall_mm",
      "event_id",
      "forecast_rainfall_mm",
      "pan_id",
      "post_event_depth_cm",
      "post_event_salinity_g_l",
      "pre_event_depth_cm",
      "pre_event_salinity_g_l",
    ]);
  });

  it("refuses an event that records nothing about what happened", () => {
    const { payload, errors } = validateOutcome(form(), { panId: PAN_ID });
    expect(payload).toBeNull();
    expect(errors.form).toMatch(/cannot be compared with anything/);
  });

  it("accepts an operator action on its own, with no measurement at all", () => {
    // Some events are worth recording for what was done, not for a figure.
    const { payload, errors } = validateOutcome(
      form({ operator_action: "Repaired the collapsed southern bund." }),
      { panId: PAN_ID },
    );
    expect(errors).toEqual({});
    expect(payload?.post_event_salinity_g_l).toBeNull();
    expect(payload?.operator_action).toBe("Repaired the collapsed southern bund.");
  });

  it("distinguishes 'nothing was harvested' from 'not reported'", () => {
    const none = validateOutcome(form({ harvest_outcome: "none" }), {
      panId: PAN_ID,
    });
    // A recorded harvest is itself something that happened, so the event is
    // recordable on that alone.
    expect(none.payload?.harvest_outcome).toBe("none");
    expect(none.errors.form).toBeUndefined();

    // With a measurement present but no harvest reported, the field is left out
    // of the payload entirely rather than sent as null or as "none".
    const unreported = validateOutcome(
      form({ harvest_outcome: "", post_event_salinity_g_l: "255" }),
      { panId: PAN_ID },
    );
    expect(unreported.payload).not.toBeNull();
    expect("harvest_outcome" in (unreported.payload ?? {})).toBe(false);

    // And with nothing at all, the event is refused rather than stored as a
    // row of nulls that would inflate the event count.
    expect(validateOutcome(form({ harvest_outcome: "" }), { panId: PAN_ID }).errors.form)
      .toMatch(/cannot be compared/);
  });

  it("reports a bad figure against its own field", () => {
    const { errors } = validateOutcome(
      form({ post_event_salinity_g_l: "900" }),
      { panId: PAN_ID },
    );
    expect(errors.post_event_salinity_g_l).toMatch(/between 0 and 500/);
  });

  it("reports a missing event name against the name field", () => {
    const { errors } = validateOutcome(
      form({ event_id: "", post_event_salinity_g_l: "255" }),
      { panId: PAN_ID },
    );
    expect(errors.event_id).toMatch(/Name this event/);
  });

  it("refuses to build a payload while any field is invalid", () => {
    const { payload } = validateOutcome(
      form({ actual_rainfall_mm: "-5" }),
      { panId: PAN_ID },
    );
    expect(payload).toBeNull();
  });
});

// -- form lifecycle -------------------------------------------------------

describe("the form", () => {
  it("starts blank", () => {
    expect(emptyOutcomeForm()).toEqual(EMPTY_OUTCOME_FORM);
  });

  it("keeps the event name after a save and drops every measurement", () => {
    // The same storm is usually filed against several pans, and a dropped name
    // invites a typo that silently splits one storm's events into two groups.
    const reset = resetOutcomeForm(
      form({ actual_rainfall_mm: "40", post_event_salinity_g_l: "255" }),
    );
    expect(reset.event_id).toBe("storm-2026-03-14");
    expect(reset.actual_rainfall_mm).toBe("");
    expect(reset.post_event_salinity_g_l).toBe("");
  });
});

// -- display: the three states must stay apart ----------------------------

describe("formatting a missing figure", () => {
  it("renders an absent figure as the dash, never as zero", () => {
    expect(formatOutcomeValue(null, "g/L")).toBe(MISSING_DISPLAY);
    expect(formatOutcomeNumber(null)).toBe(MISSING_DISPLAY);
    expect(formatPercentage(null)).toBe(MISSING_DISPLAY);
    expect(formatSignedError(null)).toBe(MISSING_DISPLAY);
    expect(formatEventTimestamp(null)).toBe(MISSING_DISPLAY);
  });

  it("renders a real figure with its unit", () => {
    expect(formatOutcomeValue(255, "g/L")).toBe("255 g/L");
    expect(formatOutcomeNumber(40)).toBe("40");
    expect(formatPercentage(-8.5)).toBe("-8.5%");
  });

  it("keeps the sign of a signed error visible in both directions", () => {
    // "+0" and "0" are a model that was marginally high and one that was
    // marginally low, and only one of them is a coincidence.
    expect(formatSignedError(2.5)).toBe("+2.5");
    expect(formatSignedError(-2.5)).toBe("-2.5");
    expect(formatSignedError(2.5, { unit: "g/L" })).toBe("+2.5 g/L");
  });

  it("renders a timestamp in UTC so two operators read the same instant", () => {
    expect(formatEventTimestamp("2026-03-14T18:00:00Z")).toContain("UTC");
    expect(formatEventTimestamp("nonsense")).toBe(MISSING_DISPLAY);
  });
});

describe("describeMissing", () => {
  it("uses the reasons the backend attached", () => {
    const text = describeMissing([
      { field: "post_event_salinity_g_l", reason: "No post-event salinity was recorded." },
    ]);
    expect(text).toBe("No post-event salinity was recorded.");
  });

  it("joins several reasons rather than showing only the first", () => {
    const text = describeMissing([
      { field: "a", reason: "First reason." },
      { field: "b", reason: "Second reason." },
    ]);
    expect(text).toBe("First reason. Second reason.");
  });

  it("still says something when the backend attached no reason", () => {
    expect(describeMissing([])).toMatch(/nothing to compare/);
  });
});

describe("reading a link", () => {
  it("returns the link in its slot, and null when nothing was found", () => {
    const links: OutcomeLinkSet = {
      pan_state: {
        kind: "pan_state",
        record_id: "obs-1",
        timestamp: "2026-03-14T06:00:00Z",
        summary: "Pan observation obs-1 describes the pan.",
        is_resolved: true,
        salinity_g_l: 281,
        water_depth_cm: 19,
        rainfall_mm: null,
        detail: null,
      },
      weather_forecast: null,
      simulation_run: null,
      recommendation: null,
      operator_decision: null,
    };

    expect(readLink(links, "pan_state")?.record_id).toBe("obs-1");
    expect(readLink(links, "weather_forecast")).toBeNull();
  });

  it("names all five kinds", () => {
    // A kind added to the union without a label would be a blank heading.
    expect(Object.keys(LINK_LABELS)).toHaveLength(5);
  });

  it("labels the harvest outcomes, including the one that is not a harvest", () => {
    expect(HARVEST_LABELS.none).toBe("Nothing harvested");
    expect(HARVEST_LABELS.lost).toBe("Harvest lost");
  });
});

// -- aggregates: the excluded count must travel with the mean -------------

describe("describeCoverage", () => {
  it("always says how many events the mean covers", () => {
    expect(describeCoverage(statistics())).toMatch(
      /Mean over 2 compared events — every event in scope was compared/,
    );
  });

  it("names the events that were left out", () => {
    const text = describeCoverage(statistics({ compared: 2, not_comparable: 18 }));
    expect(text).toMatch(/Mean over 2 compared events/);
    // A mean over 2 of 20 must never read as a mean over 20.
    expect(text).toMatch(/18 events are excluded/);
  });

  it("uses the singular for a single excluded event", () => {
    expect(
      describeCoverage(statistics({ compared: 1, not_comparable: 1 })),
    ).toMatch(/1 event is excluded/);
  });

  it("says plainly that nothing could be compared", () => {
    const text = describeCoverage(
      statistics({ compared: 0, not_comparable: 20, coverage_percent: 0 }),
    );
    expect(text).toMatch(/No event in scope could be compared/);
    expect(text).toMatch(/excluded rather than counted as zero errors/);
  });
});

describe("formatMeanAbsoluteError", () => {
  it("shows the mean when it exists", () => {
    expect(formatMeanAbsoluteError(statistics())).toBe("21.67");
  });

  it("shows the dash when no event could be compared", () => {
    // There is no honest way to compress this into a tile-sized number.
    expect(
      formatMeanAbsoluteError(
        statistics({ compared: 0, mean_absolute_error: null }),
      ),
    ).toBe(MISSING_DISPLAY);
  });
});

describe("the fixture itself", () => {
  it("models a comparable event, so the display tests are testing something", () => {
    const stored = outcome();
    expect(stored.is_comparable).toBe(true);
    expect(stored.simulation_error.signed_error).not.toBeNull();
  });
});
