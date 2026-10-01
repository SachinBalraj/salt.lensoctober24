/**
 * Evaluation panel tests.
 *
 * These are the tests that would catch the failure this module exists to prevent:
 * a figure on screen that the backend did not produce. So the assertions are
 * about *absence* as much as about presence --
 *
 * - an uncomputed metric shows the dash, and `0.000` appears nowhere for it;
 * - a real zero still shows `0.000`, because a model that predicts nothing is a
 *   finding and not a gap;
 * - an unreachable backend produces a message about the backend, and no scores;
 * - a drifted dataset is refused with guidance that does not tell the operator to
 *   press the button again, because pressing it again cannot help;
 * - a model that exists but has never been scored says so, instead of implying a
 *   score of zero.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import EvaluationPanel from "@/components/evaluation/EvaluationPanel";
import { ApiError } from "@/lib/api";
import {
  COMPARISON_COLUMNS,
  type CompareVersionsResponse,
  type ComparisonResponse,
  type EvaluationDetail,
  type EvaluationListResponse,
  type MetricReading,
} from "@/lib/evaluation";
import {
  compareVersions,
  getEvaluation,
  listEvaluations,
  runEvaluation,
} from "@/lib/evaluationApi";

vi.mock("@/lib/evaluationApi", () => ({
  listEvaluations: vi.fn(),
  compareVersions: vi.fn(),
  getEvaluation: vi.fn(),
  runEvaluation: vi.fn(),
  evaluationExportUrl: (modelId: string, format = "json") =>
    `http://localhost:8000/api/evaluation/${modelId}/export?format=${format}`,
}));

const listMock = vi.mocked(listEvaluations);
const versionsMock = vi.mocked(compareVersions);
const detailMock = vi.mocked(getEvaluation);
const runMock = vi.mocked(runEvaluation);

function measured(value: number): MetricReading {
  return { value, computed: true, variant: null, reason: null };
}

function absent(reason: string | null = null): MetricReading {
  return { value: null, computed: false, variant: null, reason };
}

const COMPARISON: ComparisonResponse = {
  columns: [...COMPARISON_COLUMNS],
  column_titles: {},
  rows: [
    {
      model: "logistic_regression",
      algorithm: "logistic_regression",
      model_id: "model-1",
      version: "v1",
      selected: true,
      auto_selectable: true,
      selection_note: "Chosen by the stated rule: highest macro F1.",
      evaluated_rows: 40,
      evaluation_set: "held-out test split",
      classes: ["low", "high"],
      class_distribution: { low: 20, high: 20 },
      metrics: {
        accuracy: measured(0.925),
        precision: measured(0.9),
        recall: measured(0.9),
        f1: measured(0.9),
        roc_auc: absent("ROC-AUC is undefined for a single-class holdout."),
        pr_auc: measured(0.91),
      },
      cross_validation: null,
    },
    {
      model: "random_forest",
      algorithm: "random_forest",
      model_id: "model-1",
      version: "v1",
      selected: false,
      // Fitted and scored, but never a candidate for the rule: the artifact on disk
      // is the selected model's, and a row that quietly became deployable would be
      // a claim this module refuses to make.
      auto_selectable: false,
      selection_note: "Fitted and scored. Never auto-selected.",
      evaluated_rows: 40,
      evaluation_set: "held-out test split",
      classes: ["low", "high"],
      class_distribution: { low: 20, high: 20 },
      metrics: {
        accuracy: measured(0),
        precision: measured(0),
        recall: measured(0),
        f1: measured(0),
        roc_auc: absent(),
        // One of the two rows has PR-AUC, which is what makes the column header
        // report partial coverage below.
        pr_auc: absent("The forest was not fitted far enough to rank."),
      },
      cross_validation: null,
    },
  ],
  row_count: 2,
  selected_algorithm: "logistic_regression",
  selection_criterion: "Highest macro F1 among candidates fitted to the same split.",
  average_precision_note: null,
  cross_validation_performed: false,
  cross_validation_note: null,
  comparable: { same_row_count: true, same_evaluation_set: true, caveats: [] },
  conventions_in_force: [],
  does_not: "It does not say which classifier to deploy.",
};

const LIST: EvaluationListResponse = {
  count: 1,
  evaluations: [
    {
      evaluation_id: "eval-1",
      model_id: "model-1",
      version: "v1",
      target: "harvest_salinity_g_l",
      algorithm: "logistic_regression",
      evaluation_set: "held-out test split",
      evaluated_rows: 40,
      metrics: {},
      comparison: COMPARISON,
      cross_validation: {},
      dataset_id: "ds-1",
      dataset_version: 1,
      dataset_content_hash: "abc123def456",
      random_seed: 42,
      library_versions: { "scikit-learn": "1.5.0" },
      trained_at: "2026-01-04T09:00:00Z",
      evaluated_at: "2026-01-05T10:00:00Z",
      notes: [],
    },
  ],
  evaluable_models: [
    {
      model_id: "model-1",
      version: "v1",
      target: "harvest_salinity_g_l",
      algorithm: "logistic_regression",
      status: "active",
      dataset_id: "ds-1",
      trained_at: "2026-01-04T09:00:00Z",
      evaluated: true,
    },
  ],
};

const VERSIONS: CompareVersionsResponse = {
  evaluations: [],
  count: 0,
  comparison: null,
  not_comparable: [],
};

const DETAIL: EvaluationDetail = {
  ...LIST.evaluations[0],
  evaluation: {
    evaluated_rows: 40,
    evaluation_set: "held-out test split",
    classes: ["low", "high"],
    class_distribution: { low: 20, high: 20 },
    predicted_distribution: { low: 19, high: 21 },
    accuracy: measured(0.925),
    balanced_accuracy: measured(0.9),
    precision: measured(0.9),
    recall: measured(0.9),
    f1: measured(0.9),
    roc_auc: absent("ROC-AUC is undefined for a single-class holdout."),
    pr_auc: measured(0.91),
    brier_score: measured(0.06),
    log_loss: measured(0.31),
    per_class: [
      {
        label: "low",
        support: 20,
        predicted: 19,
        precision: measured(0.9),
        recall: measured(0.9),
        f1: measured(0.9),
        average_precision: measured(0.9),
      },
    ],
    confusion_matrix: {
      labels: ["low", "high"],
      matrix: [
        [18, 2],
        [1, 19],
      ],
      classes_absent_from_evaluation: [],
      classes_absent_from_evaluation_rows: [],
      most_confused_pair: { actual: "low", predicted: "high", count: 2 },
    },
    notes: [],
  },
  candidate_evaluations: {},
  cross_validation: {
    performed: false,
    reason: "The split strategy does not admit resampling without reordering the rows.",
  },
  provenance: {
    dataset_id: "ds-1",
    dataset_version: 1,
    dataset_content_hash: "abc123def4567890",
    artifact_path: "/tmp/models/model-1.joblib",
    feature_names: ["rain_mm", "pan_area_m2"],
    split_strategy: "holdout",
    split_detail: {},
    random_seed: 42,
    library_versions: { "scikit-learn": "1.5.0" },
  },
  recomputed: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  listMock.mockResolvedValue(LIST);
  versionsMock.mockResolvedValue(VERSIONS);
  detailMock.mockResolvedValue(DETAIL);
});

/** The accuracy figure appears in the tile, the table and the per-class list. */
async function waitForReport() {
  return waitFor(() => expect(screen.getAllByText("0.925").length).toBeGreaterThan(0));
}

describe("EvaluationPanel", () => {
  it("shows a stored report rather than an empty picker", async () => {
    render(<EvaluationPanel />);

    await waitForReport();
    expect(screen.getByText("The candidate comparison")).toBeTruthy();
  });

  it("renders an uncomputed metric as the dash, never as zero", async () => {
    render(<EvaluationPanel />);
    await waitForReport();

    const table = screen.getByRole("table", { name: /classifier comparison/i });
    const selectedRow = within(table).getAllByRole("row")[1];

    // ROC-AUC was not calculated. The cell holds a dash and the backend's reason --
    // and specifically not "0.000", which is what a reader would take away from a
    // zero and is exactly the claim the module refuses to make.
    //
    // Located by the reason rather than by column position, so reordering the
    // columns cannot quietly turn this into a test of a different metric.
    const rocCell = within(selectedRow).getByTitle(/single-class/);
    // The visible figure is the dash. The cell also carries the reason, visually
    // hidden but present for a screen reader -- which is the point of it: a dash
    // nobody can interpret is a mystery, not a disclosure.
    expect(rocCell.querySelector("span")?.textContent).toBe("—");
    expect(rocCell.textContent).toContain("single-class");
    expect(rocCell.textContent).not.toContain("0.000");
  });

  it("keeps a genuine zero visible as a figure", async () => {
    // The forest scored nothing. That is a result, and hiding it behind a dash
    // would be the mirror image of the same mistake.
    render(<EvaluationPanel />);
    await waitForReport();

    const table = screen.getByRole("table", { name: /classifier comparison/i });
    const forestRow = within(table).getAllByRole("row")[2];
    expect(within(forestRow).getAllByRole("cell")[1].textContent).toBe("0.000");
  });

  it("counts how much of each column was computed", async () => {
    // One of two rows has ROC-AUC, so the column says so rather than presenting a
    // tidy pair of numbers with a gap nobody mentioned.
    render(<EvaluationPanel />);
    await waitForReport();

    expect(screen.getAllByText("1 of 2 computed").length).toBeGreaterThan(0);
  });
  it("reports an unreachable backend without showing any score", async () => {
    listMock.mockRejectedValue(
      new ApiError("Could not reach the SaltLens API.", { status: 0 }),
    );

    render(<EvaluationPanel />);

    expect(await screen.findByText("Evaluations are unavailable")).toBeTruthy();
    expect(screen.queryByText("0.925")).toBeNull();
  });

  it("does not tell the operator to retry a drifted dataset", async () => {
    // Retrying is the one action that cannot fix this, so the guidance names the
    // action that can.
    runMock.mockRejectedValue(
      new ApiError("The records no longer match the fitted dataset.", {
        status: 409,
        code: "dataset_drift",
      }),
    );

    render(<EvaluationPanel />);
    await waitForReport();
    await userEvent.click(screen.getByRole("button", { name: /re-run/i }));

    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/re-train/i)).toBeTruthy();
    expect(alert.textContent?.toLowerCase()).not.toContain("try again");
  });

  it("invites the run when a model has never been scored", async () => {
    listMock.mockResolvedValue({
      ...LIST,
      count: 0,
      evaluations: [],
      evaluable_models: [{ ...LIST.evaluable_models[0], evaluated: false }],
    });
    // Nothing stored, so the detail endpoint has nothing to return.
    detailMock.mockRejectedValue(
      new ApiError("This model has no stored report.", {
        status: 404,
        code: "not_evaluable",
      }),
    );

    render(<EvaluationPanel />);

    expect(await screen.findByText("Not evaluated")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /logistic/i }));

    // The model exists; that is not evidence it has a score, so the page says the
    // figure does not exist rather than implying a zero.
    expect(await screen.findByText("This model has not been evaluated")).toBeTruthy();
    expect(screen.queryByText("0.000")).toBeNull();
  });

  it("shows the row count every figure is of", async () => {
    render(<EvaluationPanel />);
    await waitForReport();

    // "0.925" on its own is a number with no denominator, and the denominator is
    // what decides whether it means anything.
    expect(screen.getAllByText(/40 rows/).length).toBeGreaterThan(0);
  });

  it("offers the record and the table as downloads, not a reshaped copy", async () => {
    render(<EvaluationPanel />);
    await waitForReport();

    const json = screen.getByRole("link", { name: /json/i });
    expect(json.getAttribute("href")).toContain("/export?format=json");
    const csv = screen.getByRole("link", { name: /csv/i });
    expect(csv.getAttribute("href")).toContain("/export?format=csv");
  });

  it("refreshes the stored view after a run rather than patching it locally", async () => {
    runMock.mockResolvedValue(DETAIL);

    render(<EvaluationPanel />);
    await waitForReport();
    await userEvent.click(screen.getByRole("button", { name: /re-run/i }));

    await waitFor(() => expect(runMock).toHaveBeenCalledWith("model-1"));
    // The count and the cross-version table both changed, so both come back from
    // the backend rather than being edited into place by the client.
    await waitFor(() => expect(listMock.mock.calls.length).toBeGreaterThanOrEqual(2));
    await waitFor(() => expect(versionsMock.mock.calls.length).toBeGreaterThanOrEqual(2));
  });

  it("passes the model id to a run, and nothing else", async () => {
    runMock.mockResolvedValue(DETAIL);

    render(<EvaluationPanel />);
    await waitForReport();
    await userEvent.click(screen.getByRole("button", { name: /re-run/i }));

    await waitFor(() => expect(runMock).toHaveBeenCalledWith("model-1"));
  });
});
