/**
 * Field-validation dashboard tests.
 *
 * The page exists to draw a reported figure beside the number the rainfall alone
 * predicts, so the assertions are about that being visible and about the caveats
 * riding with it. A comparison that carries an intervention and an unrecorded
 * volume must say so, and an unverified event must never be presented as verified.
 */

import { cloneElement, type ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import FieldValidationClient from "@/components/fieldValidation/FieldValidationClient";
import {
  EventInstantsTable,
  SupersededRainfallList,
} from "@/components/fieldValidation/FieldEventProvenance";
import {
  getFieldValidationStatus,
  getFieldValidationSummary,
  linkPreEventObservation,
  listFieldValidationComparisons,
  recordFieldValidationCorrection,
} from "@/lib/fieldValidationApi";
import { ApiError } from "@/lib/api";
import { listPans } from "@/lib/pansApi";
import type {
  FieldValidationComparison,
  FieldValidationSummary,
  SupersededRainfallTimestamp,
} from "@/lib/fieldValidation";

vi.mock("@/lib/fieldValidationApi", () => ({
  getFieldValidationStatus: vi.fn(),
  getFieldValidationSummary: vi.fn(),
  listFieldValidationComparisons: vi.fn(),
  createFieldValidationEvent: vi.fn(),
  addFieldValidationIntervention: vi.fn(),
  linkPreEventObservation: vi.fn(),
  recordFieldValidationCorrection: vi.fn(),
  fieldValidationExportUrl: () => "http://localhost:8000/api/field-validation/export?format=csv",
}));
vi.mock("@/lib/pansApi", () => ({
  listPans: vi.fn().mockResolvedValue({ items: [] }),
}));
/**
 * The charts size themselves from their parent, and jsdom reports every parent
 * as 0x0, so a ResponsiveContainer left alone renders an empty svg. Fixing the
 * dimensions is what lets these tests read the caption a reader reads.
 */
vi.mock("recharts", async () => {
  const actual = await vi.importActual<typeof import("recharts")>("recharts");
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement<Record<string, unknown>> }) =>
      cloneElement(children, { width: 600, height: 300 }),
  };
});

const statusMock = vi.mocked(getFieldValidationStatus);
const summaryMock = vi.mocked(getFieldValidationSummary);
const comparisonsMock = vi.mocked(listFieldValidationComparisons);
const linkPreEventObservationMock = vi.mocked(linkPreEventObservation);
const recordCorrectionMock = vi.mocked(recordFieldValidationCorrection);
const listPansMock = vi.mocked(listPans);

/**
 * The two reconciliation forms are rendered inside the same panel, so queries
 * that name a field both forms use are scoped to one card rather than reaching
 * for whichever one happens to be first in the document.
 */
function linkForm() {
  const heading = screen.getByRole("heading", { name: /link a genuine pre-event record/i });
  return within(heading.closest("section") as HTMLElement);
}

function correctionForm() {
  const heading = screen.getByRole("heading", { name: /record a reconciliation/i });
  return within(heading.closest("section") as HTMLElement);
}

function emptySummary(): FieldValidationSummary {
  return {
    total_events: 1,
    compared_events: 0,
    events_with_salinity: 1,
    events_with_depth: 1,
    intervention_affected_count: 0,
    unknown_volume_count: 0,
    verified_count: 0,
    events_with_unknown_milestone: 1,
    events_with_unlinked_pre_event: 1,
    uncorroborated_post_event_timestamps: 1,
    events_blocked_on_evidence: 0,
    fully_reconciled_events: 0,
    mean_abs_salinity_diff_g_l: null,
    mean_abs_depth_diff_cm: null,
    caveats: [],
  };
}

function comparison(
  overrides: Partial<FieldValidationComparison> = {},
): FieldValidationComparison {
  return {
    event_id: "ev-north-pan-001",
    pan_id: "pan_orginal",
    pan_name: "Centre Point Pan",
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
    intervention_affected: true,
    unknown_intervention_volumes: true,
    independently_verified: false,
    verification_state: "reported",
    caveats: [],
    assumptions: [],
    ...overrides,
  };
}

afterEach(() => {
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("FieldValidationClient", () => {
  it("renders the reported figures next to a missing prediction as a dash, not a zero", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue({
      total_events: 1,
      compared_events: 0,
      events_with_salinity: 1,
      events_with_depth: 1,
      intervention_affected_count: 1,
      unknown_volume_count: 1,
      verified_count: 0,
      events_with_unknown_milestone: 1,
      events_with_unlinked_pre_event: 1,
      uncorroborated_post_event_timestamps: 1,
    events_blocked_on_evidence: 0,
    fully_reconciled_events: 0,
      mean_abs_salinity_diff_g_l: null,
      mean_abs_depth_diff_cm: null,
      caveats: [],
    });
    comparisonsMock.mockResolvedValue([
      comparison({ predicted_salinity_g_l: null, salinity_abs_diff_g_l: null }),
    ]);

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("ev-north-pan-001")).toBeInTheDocument();
    });
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.queryByText("152.41")).toBeNull();
  });

  it("shows the intervention caveats beside an affected event", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue({
      total_events: 1,
      compared_events: 1,
      events_with_salinity: 1,
      events_with_depth: 1,
      intervention_affected_count: 1,
      unknown_volume_count: 1,
      verified_count: 0,
      events_with_unknown_milestone: 1,
      events_with_unlinked_pre_event: 1,
      uncorroborated_post_event_timestamps: 1,
    events_blocked_on_evidence: 0,
    fully_reconciled_events: 0,
      mean_abs_salinity_diff_g_l: 152.41,
      mean_abs_depth_diff_cm: 21,
      caveats: [],
    });
    comparisonsMock.mockResolvedValue([comparison()]);

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getAllByText("152.41").length).toBeGreaterThan(0);
    });
    expect(
      screen.getByText((text) => text.includes("no recorded volume")),
    ).toBeInTheDocument();
    expect(
      screen.getByText((text) => text.includes("not independently verified")),
    ).toBeInTheDocument();
    expect(screen.queryByText("Verified")).toBeNull();
    // Both charts legend the reported bar, so the word appears on each of them.
    expect(screen.getAllByText("Reported").length).toBeGreaterThan(0);
  });

  it("names the single event's figure as a difference, not as a mean of one", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue({
      total_events: 1,
      compared_events: 1,
      events_with_salinity: 1,
      events_with_depth: 1,
      intervention_affected_count: 0,
      unknown_volume_count: 0,
      verified_count: 1,
      events_with_unknown_milestone: 0,
      events_with_unlinked_pre_event: 0,
      uncorroborated_post_event_timestamps: 0,
      events_blocked_on_evidence: 0,
      fully_reconciled_events: 1,
      mean_abs_salinity_diff_g_l: 152.41,
      mean_abs_depth_diff_cm: 21,
      caveats: [],
    });
    comparisonsMock.mockResolvedValue([
      comparison({ intervention_affected: false, unknown_intervention_volumes: false }),
    ]);

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("Absolute salinity difference")).toBeInTheDocument();
    });
    expect(screen.getByText("Absolute depth difference")).toBeInTheDocument();
    expect(screen.queryByText("Mean |Δ| salinity")).toBeNull();
    expect(screen.queryByText("Mean |Δ| depth")).toBeNull();
  });

  it("keeps the mean and shows the sample count once several events are compared", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 3,
      ready: true,
      message: "Three events are recorded.",
    });
    summaryMock.mockResolvedValue({
      total_events: 3,
      compared_events: 3,
      events_with_salinity: 3,
      events_with_depth: 3,
      intervention_affected_count: 0,
      unknown_volume_count: 0,
      verified_count: 3,
      events_with_unknown_milestone: 0,
      events_with_unlinked_pre_event: 0,
      uncorroborated_post_event_timestamps: 0,
      events_blocked_on_evidence: 0,
      fully_reconciled_events: 3,
      mean_abs_salinity_diff_g_l: 41.8,
      mean_abs_depth_diff_cm: 7.25,
      caveats: [],
    });
    comparisonsMock.mockResolvedValue([
      comparison({ event_id: "ev-a" }),
      comparison({ event_id: "ev-b" }),
      comparison({ event_id: "ev-c" }),
    ]);

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("Mean |Δ| salinity")).toBeInTheDocument();
    });
    expect(screen.getByText("Mean |Δ| depth")).toBeInTheDocument();
    // The reader can see how much the average rests on.
    expect(screen.getAllByText(/n = 3 events/).length).toBe(2);
    expect(screen.queryByText("Absolute salinity difference")).toBeNull();
  });

  it("labels the charts from the pan registry's name, not the location the API returns", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue(emptySummary());
    comparisonsMock.mockResolvedValue([
      comparison({
        event_id: "ev-249b575de8234ff09b341e525ef5d4a1",
        event_timestamp: "2026-03-14T18:00:00Z",
        pan_name: "Salem, Tamil Nadu, India",
      }),
    ]);
    listPansMock.mockResolvedValue({
      items: [
        { pan_id: "pan_orginal", pan_name: "north pan" },
        { pan_id: "pan_other", pan_name: "South Pan" },
      ],
      total: 2,
    } as unknown as Awaited<ReturnType<typeof listPans>>);

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getAllByText("North Pan — 14 March 2026").length).toBe(2);
    });
    expect(screen.queryByText(/Salem, Tamil Nadu, India —/)).toBeNull();
  });

  it("still renders the page when the pan registry cannot be reached", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue(emptySummary());
    comparisonsMock.mockResolvedValue([comparison()]);
    listPansMock.mockRejectedValue(
      new ApiError("The pan registry is unreachable.", { status: 503 }),
    );

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("ev-north-pan-001")).toBeInTheDocument();
    });
    // The fallback is the comparison's own field, never a blank chart.
    expect(screen.getAllByText("Centre Point Pan — 14 March 2026").length).toBe(2);
  });

  it("shows both zones for the event instant and never converts it in the browser", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue(emptySummary());
    comparisonsMock.mockResolvedValue([comparison()]);

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("ev-north-pan-001")).toBeInTheDocument();
    });
    expect(screen.getByText("2026-03-14 06:00:00 UTC")).toBeInTheDocument();
    expect(screen.getByText("2026-03-14 11:30:00 IST")).toBeInTheDocument();
  });

  it("marks an event whose instant and pre-event source are unestablished", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue(emptySummary());
    comparisonsMock.mockResolvedValue([comparison()]);

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("Milestone unknown")).toBeInTheDocument();
    });
    expect(screen.getByText("No source record")).toBeInTheDocument();
  });

  it("opens the provenance panel with the instants and audit history in both zones", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue(emptySummary());
    comparisonsMock.mockResolvedValue([
      comparison({
        correction_count: 1,
        corrections: [
          {
            correction_id: "cor-1",
            field: "event_milestone",
            previous_value: "unknown",
            new_value: "unknown",
            reason: "No source document establishes what the instant marks.",
            corrected_at: "2026-03-14T20:15:00Z",
            corrected_by: "provenance audit",
            evidence_source: null,
            evidence_record_id: null,
          },
        ],
      }),
    ]);

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("ev-north-pan-001")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /provenance & audit/i }));

    await waitFor(() => {
      expect(screen.getByText(/audit history \(1 entry\)/i)).toBeInTheDocument();
    });
    // Scoped to the rainfall row: the resolution form deliberately echoes the
    // instant it would replace, so the bare value now appears in two places.
    const rainRow = screen
      .getAllByRole("rowheader")
      .find((cell) => cell.textContent === "Rainfall")
      ?.closest("tr") as HTMLElement;
    expect(within(rainRow).getByText("2026-03-14 06:30:00 UTC")).toBeInTheDocument();
    expect(within(rainRow).getByText("2026-03-14 12:00:00 IST")).toBeInTheDocument();
    expect(within(rainRow).getByText(/no source recorded/i)).toBeInTheDocument();
    expect(screen.getByText(/no source document establishes/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /record reconciliation/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /link observation/i })).toBeInTheDocument();
  });

  it("appends a milestone correction with its reason and nothing else", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue(emptySummary());
    comparisonsMock.mockResolvedValue([comparison()]);
    recordCorrectionMock.mockResolvedValue(comparison());

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("ev-north-pan-001")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /provenance & audit/i }));

    const record = correctionForm().getByRole("button", { name: /record reconciliation/i });
    expect(record).toBeDisabled();

    fireEvent.change(correctionForm().getByLabelText(/^reason/i), {
      target: { value: "No source document establishes what the instant marks." },
    });
    expect(record).not.toBeDisabled();
    fireEvent.click(record);

    await waitFor(() => {
      expect(recordCorrectionMock).toHaveBeenCalledWith({
        event_id: "ev-north-pan-001",
        draft: {
          field: "event_milestone",
          reason: "No source document establishes what the instant marks.",
          event_milestone: "unknown",
          timestamp: null,
          recorded_timezone: "IST",
          observer: "",
          evidence_source: "",
          corrected_by: "",
        },
      });
    });
  });

  it("states that a restated timestamp stays uncorroborated", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue(emptySummary());
    comparisonsMock.mockResolvedValue([comparison()]);

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("ev-north-pan-001")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /provenance & audit/i }));

    fireEvent.change(correctionForm().getByLabelText(/what to reconcile/i), {
      target: { value: "post_event_timestamp" },
    });

    await waitFor(() => {
      expect(correctionForm().getByText(/stays uncorroborated/i)).toBeInTheDocument();
    });
    expect(correctionForm().getByLabelText(/corrected instant/i)).toBeInTheDocument();
    expect(correctionForm().getByLabelText(/evidence source/i)).toBeInTheDocument();
  });

  it("refuses to submit a pre-event link that is missing the original record's details", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue(emptySummary());
    comparisonsMock.mockResolvedValue([comparison()]);

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("ev-north-pan-001")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /provenance & audit/i }));

    const link = linkForm().getByRole("button", { name: /link observation/i });
    expect(link).toBeDisabled();

    fireEvent.change(linkForm().getByLabelText(/stored observation id/i), {
      target: { value: "obs-1" },
    });
    expect(link).toBeDisabled();
    expect(linkPreEventObservationMock).not.toHaveBeenCalled();
  });

  it("posts the pre-event link only once every original-record field is given", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue(emptySummary());
    comparisonsMock.mockResolvedValue([comparison()]);
    linkPreEventObservationMock.mockResolvedValue({
      ...comparison(),
      pre_event_observation_id: "obs-1",
      pre_event_timestamp_verified: true,
    });

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("ev-north-pan-001")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /provenance & audit/i }));

    fireEvent.change(await linkForm().findByLabelText(/stored observation id/i), {
      target: { value: "obs-1" },
    });
    fireEvent.change(linkForm().getByLabelText(/measurement time on the original record/i), {
      target: { value: "2026-03-13T13:30" },
    });
    fireEvent.change(linkForm().getByLabelText(/^observer/i), {
      target: { value: "R. Kulkarni" },
    });
    fireEvent.change(linkForm().getByLabelText(/evidence source/i), {
      target: { value: "Field log p.14" },
    });
    fireEvent.change(linkForm().getByLabelText(/^reason/i), {
      target: { value: "Starting brine sampled before the rain." },
    });

    const link = linkForm().getByRole("button", { name: /link observation/i });
    expect(link).not.toBeDisabled();
    fireEvent.click(link);

    await waitFor(() => {
      expect(linkPreEventObservationMock).toHaveBeenCalledWith({
        event_id: "ev-north-pan-001",
        draft: {
          observation_id: "obs-1",
          measured_at_local: "2026-03-13T13:30",
          measured_timezone: "IST",
          observer: "R. Kulkarni",
          evidence_source: "Field log p.14",
          reason: "Starting brine sampled before the rain.",
          corrected_by: "",
        },
      });
    });
  });

  it("surfaces a refused link rather than reporting the snapshot as corroborated", async () => {
    statusMock.mockResolvedValue({
      module: "field-validation",
      event_count: 1,
      ready: true,
      message: "One event is recorded.",
    });
    summaryMock.mockResolvedValue(emptySummary());
    comparisonsMock.mockResolvedValue([comparison()]);
    linkPreEventObservationMock.mockRejectedValue(
      new ApiError("That observation was recorded after this event's rainfall.", {
        status: 409,
        code: "pre_event_observation_after_event",
      }),
    );

    render(<FieldValidationClient />);

    await waitFor(() => {
      expect(screen.getByText("ev-north-pan-001")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /provenance & audit/i }));

    fireEvent.change(await linkForm().findByLabelText(/stored observation id/i), {
      target: { value: "obs-september" },
    });
    fireEvent.change(linkForm().getByLabelText(/measurement time on the original record/i), {
      target: { value: "2026-09-30T14:40" },
    });
    fireEvent.change(linkForm().getByLabelText(/^observer/i), {
      target: { value: "R. Kulkarni" },
    });
    fireEvent.change(linkForm().getByLabelText(/evidence source/i), {
      target: { value: "Field log p.14" },
    });
    fireEvent.change(linkForm().getByLabelText(/^reason/i), {
      target: { value: "Attempting to attach the September reading." },
    });
    fireEvent.click(linkForm().getByRole("button", { name: /link observation/i }));

    await waitFor(() => {
      expect(
        screen.getByText("That observation was recorded after this event's rainfall."),
      ).toBeInTheDocument();
    });
    expect(screen.queryByText(/verified by that record/i)).toBeNull();
  });
});
/**
 * The displaced 19:30 IST instant.
 *
 * Kept as its own block because it is the case most likely to mislead: a
 * corrected timestamp is exactly the sort of change that, rendered carelessly,
 * leaves two times sitting side by side looking equally valid.
 */
const SUPERSEDED_RAINFALL: SupersededRainfallTimestamp = {
  superseded_id: "sup-20260314",
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

function renderSuperseded(overrides: Partial<FieldValidationComparison> = {}) {
  return render(
    <SupersededRainfallList
      comparison={comparison({
        rainfall_timestamp_utc: "2026-03-14 18:00:00 UTC",
        rainfall_timestamp_ist: "2026-03-14 23:30:00 IST",
        rainfall_timestamp_evidence: "North Pan field notebook",
        rainfall_timestamp_verification: "operator_reported",
        superseded_rainfall_timestamps: [SUPERSEDED_RAINFALL],
        ...overrides,
      })}
    />,
  );
}

describe("SupersededRainfallList", () => {
  it("shows the earlier instant in both zones", () => {
    renderSuperseded();
    expect(screen.getByText(/2026-03-14 14:00:00 UTC/)).toBeInTheDocument();
    expect(screen.getByText(/2026-03-14 19:30:00 IST/)).toBeInTheDocument();
  });

  it("labels it superseded rather than presenting it as a reading", () => {
    renderSuperseded();
    expect(screen.getByText("Superseded")).toBeInTheDocument();
    expect(
      screen.getByText(/audit history, not a measurement/i),
    ).toBeInTheDocument();
  });

  it("says nothing supported it and that its meaning is unknown", () => {
    renderSuperseded();
    expect(
      screen.getByText(/none — nothing in the system supported it/i),
    ).toBeInTheDocument();
    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  it("names the origin that produced the value", () => {
    renderSuperseded();
    expect(
      screen.getByText(/seed_field_validation\.py RAIN_AT literal/),
    ).toBeInTheDocument();
  });

  it("holds the seed's gauge label as a claim, not as evidence", () => {
    renderSuperseded();
    expect(
      screen.getByText(/was a claim about provenance and not evidence for it/),
    ).toBeInTheDocument();
  });

  it("renders nothing when the event has never been corrected", () => {
    const { container } = render(
      <SupersededRainfallList
        comparison={comparison({ superseded_rainfall_timestamps: [] })}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("rainfallStanding on the instants table", () => {
  it("presents a reported instant as reported, never as measured", () => {
    render(
      <EventInstantsTable
        comparison={comparison({
          rainfall_timestamp_utc: "2026-03-14 18:00:00 UTC",
          rainfall_timestamp_ist: "2026-03-14 23:30:00 IST",
          rainfall_timestamp_evidence: "North Pan field notebook",
          rainfall_timestamp_verification: "operator_reported",
        })}
      />,
    );
    expect(
      screen.getByText(/operator-reported.*North Pan field notebook.*not a measured reading/),
    ).toBeInTheDocument();
  });

  it("never presents a reported instant as instrument-verified", () => {
    render(
      <EventInstantsTable
        comparison={comparison({
          rainfall_timestamp_evidence: "North Pan field notebook",
          rainfall_timestamp_verification: "operator_reported",
          rainfall_timestamp_observed: false,
        })}
      />,
    );
    const row = screen.getByText("Rainfall").closest("tr");
    expect(within(row as HTMLElement).getByText(/not a measured reading/)).toBeTruthy();
    expect(within(row as HTMLElement).queryByText(/instrument-verified/i)).toBeNull();
  });
});
