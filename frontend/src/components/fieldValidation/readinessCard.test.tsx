/**
 * DatasetReadinessCard tests.
 *
 * The card's one honest requirement is that it renders only what the backend
 * computed. So the tests assert that a "not_ready" report is shown as not ready
 * with its reasons and its genuine-labelled-row figure, and that the card never
 * invents a number: no "ready" unless the verdict says ready.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import DatasetReadinessCard from "@/components/DatasetReadinessCard";
import { fetchDatasetReadiness } from "@/lib/datasetReadinessApi";
import type { DatasetReadiness } from "@/lib/datasetReadiness";

vi.mock("@/lib/datasetReadinessApi", () => ({
  fetchDatasetReadiness: vi.fn(),
}));

const fetchMock = vi.mocked(fetchDatasetReadiness);

function report(overrides: Partial<DatasetReadiness> = {}): DatasetReadiness {
  return {
    verdict: "not_ready",
    message: "Insufficient labelled data for reliable model training.",
    reasons: ["only 0 genuine labelled row(s) are on file; at least 80 are required."],
    genuine_labelled_rows: 0,
    distinct_pans_in_labels: 0,
    counts: {
      registered_pans: 2,
      stored_observations: 3,
      stored_weather: 3,
      outcomes_on_file: 2,
      datasets_on_file: 0,
      model_versions_on_file: 0,
      experiments_on_file: 0,
    },
    thresholds: {
      min_total_labelled_rows: 80,
      min_rows_per_split: 96,
      min_classes: 2,
      min_rows_per_class: 20,
      min_train_rows_per_class: 40,
      min_distinct_pans: 3,
    },
    note: "Operational ML prediction stays unavailable while the gate is shut.",
    ...overrides,
  };
}

afterEach(() => {
  fetchMock.mockReset();
});

describe("DatasetReadinessCard", () => {
  it("shows a not-ready audit as not ready with its reasons and figures", async () => {
    fetchMock.mockResolvedValue(report());

    render(<DatasetReadinessCard />);

    await waitFor(() => {
      expect(screen.getByText("Not ready")).toBeInTheDocument();
      expect(screen.getByText(/Insufficient labelled data/)).toBeInTheDocument();
    });
    expect(screen.getByText(/only 0 genuine labelled row/)).toBeInTheDocument();
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
    expect(screen.getByText("80")).toBeInTheDocument();
  });

  it("shows ready only when the verdict is ready", async () => {
    fetchMock.mockResolvedValue(
      report({
        verdict: "ready",
        message:
          "Sufficient genuine labelled data is on file to open the training gate; fitting still requires a stored dataset and a deliberate run.",
      }),
    );

    render(<DatasetReadinessCard />);

    await waitFor(() => {
      expect(screen.getByText("Ready")).toBeInTheDocument();
    });
    expect(screen.queryByText("Not ready")).toBeNull();
  });

  it("reports an unreachable audit rather than inventing figures", async () => {
    fetchMock.mockRejectedValue(new Error("fetch failed"));

    render(<DatasetReadinessCard />);

    await waitFor(() => {
      expect(screen.getByText("Dataset audit unavailable")).toBeInTheDocument();
    });
    expect(screen.queryByText("Ready")).toBeNull();
    expect(screen.queryByText("Not ready")).toBeNull();
  });
});