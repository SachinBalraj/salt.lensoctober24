/**
 * Field-validation chart labelling tests.
 *
 * The charts draw a reported figure beside what the rainfall alone predicts, so
 * the assertions are about how each event is named under its bars and about the
 * caveat that must sit beneath them. A 32-character event id is a key, not a
 * caption: a reader has to see which pan and which day they are looking at, and
 * the id still has to survive underneath as the row's identity.
 */

import { cloneElement, type ReactElement } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import FieldValidationChart from "@/components/fieldValidation/FieldValidationChart";
import type { FieldValidationComparison } from "@/lib/fieldValidation";

/**
 * The chart sizes itself from its parent, and jsdom reports every parent as
 * 0x0, so a ResponsiveContainer left alone renders an empty svg. Giving it fixed
 * dimensions is what lets these tests read the caption the reader reads.
 */
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({
      children,
    }: {
      children: ReactElement<Record<string, unknown>>;
    }) => cloneElement(children, { width: 600, height: 300 }),
  };
});

/**
 * The live North Pan comparison: one event, one outbound transfer of unrecorded
 * volume, an operator-reported rainfall instant, and differences far larger than
 * the model would like. Every number is the stored one, unchanged.
 */
function comparison(
  overrides: Partial<FieldValidationComparison> = {},
): FieldValidationComparison {
  return {
    event_id: "ev-249b575de8234ff09b341e525ef5d4a1",
    pan_id: "pan_orginal",
    // The API fills this with the pan's location, not its name, so the caption
    // must come from the registry map instead.
    pan_name: "Salem, Tamil Nadu, India",
    event_timestamp: "2026-03-14T18:00:00Z",
    event_milestone: "rainfall_start",
    site_timezone_label: "IST (UTC+05:30)",
    event_timestamp_utc: "2026-03-14 18:00:00 UTC",
    event_timestamp_ist: "2026-03-14 23:30:00 IST",
    rainfall_timestamp_utc: "2026-03-14 18:00:00 UTC",
    rainfall_timestamp_ist: "2026-03-14 23:30:00 IST",
    rainfall_timestamp_evidence: "operator field notebook",
    rainfall_timestamp_verification: "operator_reported" as const,
    rainfall_timestamp_observed: true,
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
    reconciliation_state: "complete" as const,
    reported_by: "A. Operator",
    notes: null,
    model_name: "rainfall-dilution-mass-balance-v1",
    model_disclaimer: "",
    actual_rainfall_mm: 90,
    forecast_rainfall_mm: 70,
    predicted_salinity_g_l: 172.41379310344828,
    predicted_depth_cm: 29,
    reported_salinity_g_l: 20,
    reported_depth_cm: 50,
    salinity_abs_diff_g_l: 152.41379310344828,
    depth_abs_diff_cm: 21,
    intervention_affected: true,
    unknown_intervention_volumes: true,
    independently_verified: false,
    verification_state: "reported",
    caveats: [],
    assumptions: [],
    ...overrides,
  };
}

const NORTH_PAN = { pan_orginal: "north pan" };

describe("FieldValidationChart captions", () => {
  it("names the North Pan event from the registry's pan name and the event date", () => {
    render(
      <FieldValidationChart comparisons={[comparison()]} panNames={NORTH_PAN} />,
    );
    // Both charts caption their rows, so the caption appears twice on screen.
    expect(
      screen.getAllByText("North Pan — 14 March 2026").length,
    ).toBeGreaterThan(0);
  });

  it("draws the same caption on both charts, since both show the same event", () => {
    render(
      <FieldValidationChart comparisons={[comparison()]} panNames={NORTH_PAN} />,
    );
    const captions = screen.getAllByText("North Pan — 14 March 2026");
    // One under the salinity bars and one under the depth bars.
    expect(captions).toHaveLength(2);
  });

  it("does not caption the event with its id, and does not use the pan's location", () => {
    const { container } = render(
      <FieldValidationChart comparisons={[comparison()]} panNames={NORTH_PAN} />,
    );
    expect(container.textContent).not.toContain("ev-249b575de8234ff09b341e525ef5d4a1");
    expect(container.textContent).not.toContain("Salem, Tamil Nadu, India");
  });

  it("labels a second pan and a second day from that event's own record", () => {
    render(
      <FieldValidationChart
        comparisons={[
          comparison({ event_id: "ev-222", pan_id: "pan_kzn" }),
          comparison({
            event_id: "ev-333",
            pan_id: "pan_kzn",
            event_timestamp: "2026-09-02T05:15:00Z",
          }),
        ]}
        panNames={{ pan_kzn: "KZN Salt Works" }}
      />,
    );
    expect(screen.getAllByText("KZN Salt Works — 14 March 2026").length).toBe(2);
    expect(screen.getAllByText("KZN Salt Works — 2 September 2026").length).toBe(2);
  });

  it("falls back to the comparison's own pan field when the registry has no entry", () => {
    render(<FieldValidationChart comparisons={[comparison()]} />);
    expect(
      screen.getAllByText("Salem, Tamil Nadu, India — 14 March 2026").length,
    ).toBeGreaterThan(0);
  });

  it("shows no chart at all when there is nothing to compare", () => {
    const { container } = render(<FieldValidationChart comparisons={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("the single-event note rides beneath the charts", () => {
  it("appears for the one unverified, unknown-volume, intervention-affected event", () => {
    render(
      <FieldValidationChart comparisons={[comparison()]} panNames={NORTH_PAN} />,
    );
    expect(
      screen.getByText(
        "Single operator-reported rainfall event. An outbound brine transfer occurred, but its volume is unknown. The displayed differences are descriptive and do not establish independently validated model accuracy.",
      ),
    ).toBeInTheDocument();
  });

  it("stays off once a second event makes it untrue of the set", () => {
    render(
      <FieldValidationChart
        comparisons={[comparison(), comparison({ event_id: "ev-222" })]}
        panNames={NORTH_PAN}
      />,
    );
    expect(screen.queryByText(/do not establish independently validated/i)).toBeNull();
  });

  it("stays off for a verified event or a known transfer volume", () => {
    render(
      <FieldValidationChart
        comparisons={[
          comparison({ independently_verified: true, verification_state: "verified" }),
        ]}
        panNames={NORTH_PAN}
      />,
    );
    expect(screen.queryByText(/do not establish independently validated/i)).toBeNull();

    render(
      <FieldValidationChart
        comparisons={[comparison({ unknown_intervention_volumes: false })]}
        panNames={NORTH_PAN}
      />,
    );
    expect(screen.queryByText(/do not establish independently validated/i)).toBeNull();
  });
});

describe("the drawn values are the stored ones", () => {
  it("draws the predicted and reported figures the record holds", () => {
    const { container } = render(
      <FieldValidationChart comparisons={[comparison()]} panNames={NORTH_PAN} />,
    );
    const drawn = [...container.querySelectorAll(".recharts-bar-rectangle")].map(
      (rect) => rect.getAttribute("fill"),
    );
    // Two bars per chart, two charts: predicted and reported, side by side.
    expect(drawn.length).toBe(4);
    // 172.41 against 20 g/L, and 29 against 50 cm, is the whole point of the page.
    expect(container.querySelectorAll(".recharts-bar")).toHaveLength(4);
  });
});
