/**
 * Field-validation display shaping tests.
 *
 * The module's single rule is that a reported figure is never shown as proof,
 * so the tests are mostly about the caveats that must ride beside a comparison.
 * A comparison that was never independently verified always carries that caveat,
 * an intervention makes the caveat explicit, and an unrecorded volume makes the
 * estimate limitation explicit too.
 */

import { describe, expect, it } from "vitest";
import {
  INTERVENTION_KIND_LABELS,
  SINGLE_EVENT_DIFFERENCE_NOTE,
  captionCasePanName,
  differenceMetricLabel,
  displayDifference,
  eventChartLabel,
  eventDateLabel,
  headlineCaveats,
  interventionLabel,
  isComparableDepth,
  isComparableSalinity,
  rainfallStanding,
  warrantsSingleEventNote,
  type FieldValidationComparison,
  type SupersededRainfallTimestamp,
} from "@/lib/fieldValidation";

function comparison(
  overrides: Partial<FieldValidationComparison> = {},
): FieldValidationComparison {
  return {
    event_id: "ev-north-pan-001",
    pan_id: "pan_orginal",
    pan_name: "North Pan",
    event_timestamp: "2026-03-14T06:00:00Z",
    event_milestone: "unknown",
    site_timezone_label: "IST (UTC+05:30)",
    event_timestamp_utc: "2026-03-14 06:00:00 UTC",
    event_timestamp_ist: "2026-03-14 11:30:00 IST",
    rainfall_timestamp_utc: "2026-03-14 06:30:00 UTC",
    rainfall_timestamp_ist: "2026-03-14 12:00:00 IST",
    rainfall_timestamp_evidence: null,
    rainfall_timestamp_verification: "not_recorded" as const,
    rainfall_timestamp_observed: false,
    superseded_rainfall_timestamps: [],
    pre_event_salinity_g_l: 250,
    pre_event_depth_cm: 20,
    pre_event_timestamp_utc: null,
    pre_event_timestamp_ist: null,
    pre_event_timestamp_verified: false,
    pre_event_observation_id: null,
    pre_event_observer: null,
    post_event_timestamp_utc: null,
    post_event_timestamp_ist: null,
    post_event_timestamp_verified: false,
    post_event_observation_id: null,
    post_event_observer: null,
    weather_observation_id: null,
    correction_count: 0,
    corrections: [],
    rainfall_timestamp_conflicts: [],
    unresolved_conflict_count: 0,
    reconciliation_state: "incomplete" as const,
    reported_by: "A. Operator",
    notes: null,
    model_name: "rainfall-dilution-mass-balance-v1",
    model_disclaimer: "",
    actual_rainfall_mm: 90,
    forecast_rainfall_mm: 70,
    predicted_salinity_g_l: 172.41,
    predicted_depth_cm: 29,
    reported_salinity_g_l: 20,
    reported_depth_cm: 50,
    salinity_abs_diff_g_l: 152.41,
    depth_abs_diff_cm: 21,
    intervention_affected: false,
    unknown_intervention_volumes: false,
    independently_verified: false,
    verification_state: "reported",
    caveats: [],
    assumptions: [],
    ...overrides,
  };
}

describe("isComparableSalinity / isComparableDepth", () => {
  it("accepts a pair where both figures exist", () => {
    expect(isComparableSalinity(comparison())).toBe(true);
    expect(isComparableDepth(comparison())).toBe(true);
  });

  it("rejects a pair whose prediction is missing", () => {
    expect(
      isComparableSalinity(comparison({ predicted_salinity_g_l: null })),
    ).toBe(false);
    expect(
      isComparableDepth(comparison({ predicted_depth_cm: null })),
    ).toBe(false);
  });
});

describe("displayDifference", () => {
  it("renders two decimals", () => {
    expect(displayDifference(152.412)).toBe("152.41");
  });

  it("renders a dash rather than a zero for a missing difference", () => {
    expect(displayDifference(null)).toBe("—");
  });
});

describe("headlineCaveats", () => {
  it("always flags an unverified comparison as not proof", () => {
    const caveats = headlineCaveats(comparison());
    expect(caveats.some((c) => c.includes("not independently verified"))).toBe(true);
  });

  it("flags an intervention explicitly", () => {
    const caveats = headlineCaveats(comparison({ intervention_affected: true }));
    expect(caveats.some((c) => c.includes("intervention"))).toBe(true);
  });

  it("flags an unrecorded volume as beyond the balance's reach", () => {
    const caveats = headlineCaveats(
      comparison({ intervention_affected: true, unknown_intervention_volumes: true }),
    );
    expect(
      caveats.some((c) => c.includes("no recorded volume") && c.includes("cannot be estimated")),
    ).toBe(true);
  });

  it("does not claim independent verification when the state says reported", () => {
    for (const caveat of headlineCaveats(comparison())) {
      expect(caveat.toLowerCase()).not.toContain("independently verified.");
    }
  });
});

describe("intervention labels", () => {
  it("labels a brine transfer out as a removal", () => {
    expect(interventionLabel("brine_transfer_out", "out")).toBe(
      "Brine transferred out (out of the pan)",
    );
  });

  it("covers every kind the API can send", () => {
    expect(INTERVENTION_KIND_LABELS.brine_transfer_in).toBeTruthy();
    expect(INTERVENTION_KIND_LABELS.brine_transfer_out).toBeTruthy();
    expect(INTERVENTION_KIND_LABELS.fresh_water_added).toBeTruthy();
    expect(INTERVENTION_KIND_LABELS.harvest).toBeTruthy();
    expect(INTERVENTION_KIND_LABELS.other).toBeTruthy();
  });
});
describe("rainfallStanding", () => {
  it("says an unsourced instant has no source recorded", () => {
    expect(rainfallStanding(comparison())).toBe(
      "no source recorded (no source recorded) - not a measured reading",
    );
  });

  it("labels a documented instant as reported and never as measured", () => {
    const standing = rainfallStanding(
      comparison({
        rainfall_timestamp_evidence: "North Pan field notebook",
        rainfall_timestamp_verification: "operator_reported",
      }),
    );
    expect(standing).toContain("operator-reported");
    expect(standing).toContain("North Pan field notebook");
    expect(standing).toContain("not a measured reading");
  });

  it("is the only wording that admits an observed instant", () => {
    const standing = rainfallStanding(
      comparison({
        rainfall_timestamp_evidence: "gauge export 2026-03-14",
        rainfall_timestamp_verification: "record_backed",
        rainfall_timestamp_observed: true,
      }),
    );
    expect(standing).toBe("backed by a stored record (gauge export 2026-03-14)");
    expect(standing).not.toContain("not a measured reading");
  });
});

describe("a superseded instant is kept as history, not as a measurement", () => {
  const displaced: SupersededRainfallTimestamp = {
    superseded_id: "sup-1",
    timestamp_utc: "2026-03-14 14:00:00 UTC",
    timestamp_ist: "2026-03-14 19:30:00 IST",
    timezone_label: "IST (UTC+05:30)",
    source_claim: "manual rain gauge",
    origin: "scripts/seed_field_validation.py RAIN_AT literal",
    supporting_record: null,
    meaning: "unknown",
    superseded_at: "2026-03-14 18:05:00 UTC",
    superseded_by: "audit",
    reason: "No rain-gauge record supports 19:30 IST.",
    conflict_id: "rfc-1",
  };

  it("carries the meaning and the absent supporting record with the value", () => {
    // A reader who sees only the timestamp must still be able to tell it is not
    // a second reading, so these travel with it rather than being implied.
    expect(displaced.meaning).toBe("unknown");
    expect(displaced.supporting_record).toBeNull();
  });

  it("keeps the seed's gauge label as a claim, not as evidence", () => {
    expect(displaced.source_claim).toBe("manual rain gauge");
    expect(displaced.origin).toContain("seed_field_validation.py");
  });
});

describe("headline caveats never call a reported instant verified", () => {
  it("keeps the reported instant out of any verified phrasing", () => {
    const caveats = headlineCaveats(
      comparison({
        rainfall_timestamp_evidence: "North Pan field notebook",
        rainfall_timestamp_verification: "operator_reported",
        independently_verified: false,
      }),
    );
    expect(caveats.join(" ")).not.toMatch(/independently verified\./i);
  });
});

describe("a chart caption names the place and the day, not the record", () => {
  it("reads the North Pan event as the paper's own caption", () => {
    expect(eventChartLabel("north pan", "2026-03-14T18:00:00Z")).toBe(
      "North Pan — 14 March 2026",
    );
  });

  it("labels a second pan and a second day from that event's own record", () => {
    expect(eventChartLabel("KZN Salt Works", "2026-09-02T05:15:00Z")).toBe(
      "KZN Salt Works — 2 September 2026",
    );
  });

  it("never puts the event id in the caption", () => {
    const label = eventChartLabel("north pan", "2026-03-14T18:00:00Z");
    expect(label).not.toContain("ev-");
    expect(label).not.toContain("pan_orginal");
  });

  it("reads the date in UTC so a reader in another zone keeps the same day", () => {
    // 18:00Z is 23:30 IST the same day, but 13:30 the previous day in New York.
    // A zone-shifted format would silently caption the event with the wrong day.
    expect(eventDateLabel("2026-03-14T02:00:00Z")).toBe("14 March 2026");
  });

  it("names what it can when one half of the record is missing", () => {
    expect(eventChartLabel("north pan", null)).toBe("North Pan");
    expect(eventChartLabel(null, "2026-03-14T18:00:00Z")).toBe("14 March 2026");
    expect(eventChartLabel(null, "not-a-date")).toBe("Unnamed event");
  });

  it("leaves an operator's capitals alone and does not invent any", () => {
    expect(captionCasePanName("KZN Salt Works")).toBe("KZN Salt Works");
    expect(captionCasePanName("  pan 3  ")).toBe("Pan 3");
    expect(captionCasePanName("")).toBeNull();
  });
});

describe("one event is a difference, not a mean of one", () => {
  it("names the single-event figure for what it is", () => {
    expect(differenceMetricLabel("salinity", 1).label).toBe(
      "Absolute salinity difference",
    );
    expect(differenceMetricLabel("depth", 1).label).toBe(
      "Absolute depth difference",
    );
  });

  it("claims no sample count for a single event", () => {
    expect(differenceMetricLabel("salinity", 1).sampleCount).toBeNull();
  });

  it("keeps the mean and shows the sample count once there are several events", () => {
    expect(differenceMetricLabel("salinity", 3)).toEqual({
      label: "Mean |Δ| salinity",
      sampleCount: 3,
    });
    expect(differenceMetricLabel("depth", 2).label).toBe("Mean |Δ| depth");
  });

  it("makes no claim about an aggregate when nothing was averaged", () => {
    expect(differenceMetricLabel("salinity", 0)).toEqual({
      label: "Salinity difference",
      sampleCount: null,
    });
  });
});

describe("the single-event note appears only where it is true", () => {
  const affected = {
    independently_verified: false,
    intervention_affected: true,
    unknown_intervention_volumes: true,
  } as const;

  it("appears for one unverified, unknown-volume, intervention-affected event", () => {
    expect(warrantsSingleEventNote([comparison(affected)])).toBe(true);
  });

  it("stays off once a second event is on screen", () => {
    expect(
      warrantsSingleEventNote([comparison(affected), comparison(affected)]),
    ).toBe(false);
  });

  it("stays off when there is nothing on screen", () => {
    expect(warrantsSingleEventNote([])).toBe(false);
  });

  it("stays off for a verified event, an unaffected event, or a known volume", () => {
    expect(
      warrantsSingleEventNote([comparison({ ...affected, independently_verified: true })]),
    ).toBe(false);
    expect(
      warrantsSingleEventNote([comparison({ ...affected, intervention_affected: false })]),
    ).toBe(false);
    expect(
      warrantsSingleEventNote([comparison({ ...affected, unknown_intervention_volumes: false })]),
    ).toBe(false);
  });

  it("names the single event, the unrecorded volume, and what the figure is not", () => {
    expect(SINGLE_EVENT_DIFFERENCE_NOTE).toBe(
      "Single operator-reported rainfall event. An outbound brine transfer occurred, but its volume is unknown. The displayed differences are descriptive and do not establish independently validated model accuracy.",
    );
  });
});
