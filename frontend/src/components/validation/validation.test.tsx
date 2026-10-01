/**
 * Research-validation dashboard tests.
 *
 * These are the tests that would catch the failure this module exists to
 * prevent: a figure on screen that the data did not support. So the assertions
 * are about *absence* as much as about presence —
 *
 * - an unreachable backend produces a message about the backend, and no metric
 *   tiles at all;
 * - a metric the backend withheld (RMSE over a single event, R-squared over a
 *   constant set) renders as the dash, and `0.000` appears nowhere for it;
 * - a classification block reports a real agree as `100.000%`, on its own scale;
 * - the block names the risk rule and version it graded under;
 * - every block carries its sample sentence ("2 of 4 recorded events") so a
 *   figure built from part of the set reads as part of the set.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ValidationClient from "@/components/validation/ValidationClient";
import { ApiError } from "@/lib/api";
import type {
  MetricReading,
  ValidationOverview,
  ValidationStatus,
} from "@/lib/validation";
import { getValidationOverview, getValidationStatus } from "@/lib/validationApi";

vi.mock("@/lib/validationApi", () => ({
  getValidationStatus: vi.fn(),
  getValidationOverview: vi.fn(),
  getValidationPairs: vi.fn().mockResolvedValue({ items: [] }),
  validationExportUrl: (
    comparison: string,
    format: string,
    params: { pan_id?: string } = {},
  ) =>
    `http://localhost:8000/api/validation/export?comparison=${comparison}&output_format=${format}${
      params.pan_id ? `&pan_id=${params.pan_id}` : ""
    }`,
}));

const statusMock = vi.mocked(getValidationStatus);
const overviewMock = vi.mocked(getValidationOverview);

function measured(value: number): MetricReading {
  return { value, computed: true, variant: null, reason: null };
}

function absent(reason: string): MetricReading {
  return { value: null, computed: false, variant: null, reason };
}

function continuous(
  key: "forecast_vs_actual_rainfall",
  title: string,
  description: string,
  overrides: {
    n_compared?: number;
    n_excluded?: number;
    mean_absolute_error?: MetricReading;
    mean_bias_error?: MetricReading;
  } = {},
) {
  const { n_compared = 2, n_excluded = 0 } = overrides;
  return {
    key,
    title,
    unit: "mm",
    description,
    n_compared,
    n_excluded,
    excluded_reasons: n_excluded
      ? [{ reason: "No rainfall figure was recorded for the event.", count: n_excluded }]
      : [],
    mean_absolute_error: overrides.mean_absolute_error ?? measured(12.35),
    root_mean_squared_error: absent(
      "RMSE is only reported over at least two comparable events; a single event cannot support a typical-error figure.",
    ),
    r_squared: absent(
      "R-squared is only reported over at least two comparable events.",
    ),
    mean_bias_error: overrides.mean_bias_error ?? measured(-4.6),
    notes: [],
  };
}

function classification(
  title: string,
  description: string,
  overrides: { n_compared?: number; n_excluded?: number } = {},
) {
  const { n_compared = 2, n_excluded = 0 } = overrides;
  return {
    key: "predicted_risk_vs_outcome",
    title,
    description,
    classes: ["low", "high"],
    n_compared,
    n_excluded,
    excluded_reasons: n_excluded
      ? [{ reason: "No recommendation was in force for this event.", count: n_excluded }]
      : [],
    predicted_distribution: { low: 0, high: n_compared },
    actual_distribution: { low: 1, high: n_compared - 1 },
    accuracy: measured(100),
    precision: measured(100),
    recall: measured(100),
    f1: measured(100),
    roc_auc: measured(1),
    pr_auc: measured(1),
    per_class: [
      {
        label: "low",
        description: "The brine stays usable.",
        support: 1,
        predicted: 0,
        precision: absent("This class was never predicted."),
        recall: measured(100),
        f1: measured(100),
      },
      {
        label: "high",
        description: "Loss worth acting on.",
        support: 1,
        predicted: 2,
        precision: measured(100),
        recall: measured(50),
        f1: measured(66.667),
      },
    ],
    confusion_matrix: {
      labels: ["low", "high"],
      matrix: [
        [1, 0],
        [0, 1],
      ],
      classes_absent_from_evaluation: [],
    },
    notes: [],
    definition_id: "risk-9001",
    definition_name: "salinity-loss-risk",
    definition_version: 1,
  };
}

function overview(total = 4): ValidationOverview {
  return {
    scope: { pan_id: null, total_outcomes: total },
    forecast_vs_actual_rainfall: continuous(
      "forecast_vs_actual_rainfall",
      "Forecast rainfall vs actual rainfall",
      "The forecast's own error.",
    ),
    simulated_vs_actual_salinity: continuous(
      "forecast_vs_actual_rainfall",
      "Simulated salinity vs actual salinity",
      "The simulator's own error.",
      {
        mean_absolute_error: measured(12.5),
        mean_bias_error: measured(-3.1),
      },
    ),
    forecast_simulated_vs_actual_salinity: continuous(
      "forecast_vs_actual_rainfall",
      "Forecast-simulated salinity vs actual salinity",
      "The forecast's and the simulator's error together.",
      {
        n_compared: 1,
        n_excluded: 1,
        mean_absolute_error: measured(13.1),
        mean_bias_error: measured(0.9),
      },
    ),
    predicted_risk_vs_outcome: classification(
      "Predicted risk vs recorded outcome",
      "The risk rule re-applied to what actually happened.",
    ),
    recommended_vs_operator_action: {
      key: "recommended_vs_operator_action",
      title: "Recommended action vs operator action",
      description: "Advice, disposition and outcome in one graded chain.",
      classes: ["HARVEST NOW", "TRANSFER BRINE", "PROTECT / DRAIN", "WAIT AND RECHECK"],
      n_compared: 1,
      n_excluded: 3,
      excluded_reasons: [
        { reason: "No operator decision was recorded for the recommendation in force.", count: 3 },
      ],
      predicted_distribution: { "HARVEST NOW": 1 },
      actual_distribution: { "HARVEST NOW": 1 },
      accuracy: absent("Accuracy is only reported over at least two compared events."),
      precision: absent("withheld"),
      recall: absent("withheld"),
      f1: absent("withheld"),
      roc_auc: absent("No probability is stored for an advised action."),
      pr_auc: absent("No probability is stored for an advised action."),
      per_class: [],
      confusion_matrix: null,
      notes: [],
      definition_id: null,
      definition_name: null,
      definition_version: null,
    },
  };
}

function status(): ValidationStatus {
  return {
    module: "predicted-vs-actual",
    module_number: 16,
    status: "implemented",
    description: "Honest comparison of historical predictions against outcomes.",
    endpoints: ["GET  /api/validation/status"],
    validation: {
      available: true,
      total_outcomes: 4,
      comparable_outcomes: 3,
      forecast_errors: 4,
      risk_predictions: 2,
      operator_decisions: 1,
    },
  };
}

beforeEach(() => {
  statusMock.mockResolvedValue(status());
  overviewMock.mockResolvedValue(overview());
  vi.unstubAllGlobals();
});

describe("ValidationClient", () => {
  it("renders the five comparison families with their download links", async () => {
    render(<ValidationClient />);

    expect(await screen.findByText("Forecast rainfall vs actual rainfall")).toBeInTheDocument();
    expect(screen.getByText("Predicted risk vs recorded outcome")).toBeInTheDocument();
    expect(screen.getByText("Recommended action vs operator action")).toBeInTheDocument();
    expect(screen.getByText("Simulated salinity vs actual salinity")).toBeInTheDocument();
    expect(screen.getByText("Forecast-simulated salinity vs actual salinity")).toBeInTheDocument();

    const csv = screen.getAllByRole("link", { name: "CSV" });
    expect(csv.length).toBe(5);
    expect(csv[0]).toHaveAttribute(
      "href",
      expect.stringContaining("output_format=csv"),
    );
  });

  it("shows a measured mean absolute error with its unit", async () => {
    render(<ValidationClient />);

    expect(await screen.findByText("12.350 mm")).toBeInTheDocument();
    expect(screen.getByText("-4.600 mm")).toBeInTheDocument();
  });

  it("renders a withheld RMSE as the dash, never as 0.000", async () => {
    render(<ValidationClient />);

    await screen.findByText("12.350 mm");
    const labels = screen.getAllByText(/Root mean squared error/);
    expect(labels.length).toBeGreaterThan(0);
    expect(screen.queryByText("0.000")).not.toBeInTheDocument();
  });

  it("reports a classification rate on the percentage scale", async () => {
    render(<ValidationClient />);

    expect((await screen.findAllByText("100.000%")).length).toBeGreaterThan(0);
    expect(screen.getByText("66.667%")).toBeInTheDocument();
  });

  it("names the risk rule and version the block graded under", async () => {
    render(<ValidationClient />);

    expect(await screen.findByText("salinity-loss-risk · version 1")).toBeInTheDocument();
  });

  it("carries the sample sentence on a block built from part of the set", async () => {
    render(<ValidationClient />);

    expect(await screen.findByText(/Based on 1 of 2 recorded events/)).toBeInTheDocument();
  });

  it("reveals event rows on demand", async () => {
    const user = userEvent.setup();
    render(<ValidationClient />);

    const buttons = await screen.findAllByRole("button", { name: /Event rows/ });
    await user.click(buttons[0]);

    expect(await screen.findByText("Event")).toBeInTheDocument();
  });

  it("shows nothing metric-like when the backend is unreachable", async () => {
    statusMock.mockRejectedValue(new ApiError("Could not reach the SaltLens API.", { status: 0 }));
    overviewMock.mockRejectedValue(new ApiError("Could not reach the SaltLens API.", { status: 0 }));

    render(<ValidationClient />);

    expect(await screen.findByText("The validation report is unavailable")).toBeInTheDocument();
    expect(screen.queryByText("12.350 mm")).not.toBeInTheDocument();
    expect(screen.queryByText("100.000%")).not.toBeInTheDocument();
  });
})