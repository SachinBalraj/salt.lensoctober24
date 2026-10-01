/**
 * Research experiment ledger page tests.
 *
 * These are the tests that would catch the failure this module exists to
 * prevent: the ledger showing a result that was not recorded, or recording one
 * over another. So the assertions are about integrity as much as presence —
 *
 * - an unreachable backend produces a message about the backend, and no row;
 * - tiles and rows come only from what the backend reports;
 * - recording a run posts the full record and the outcome sentence is honest:
 *   "Recorded as …" for a new row, "was already recorded; nothing was
 *   overwritten" for a reuse;
 * - a form value the backend would reject is refused before any POST.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ExperimentsClient from "@/components/experiments/ExperimentsClient";
import { ApiError } from "@/lib/api";
import type {
  Experiment,
  ExperimentCreateResponse,
  ExperimentListResponse,
  ExperimentStatus,
} from "@/lib/experiment";
import {
  createExperiment,
  getExperimentStatus,
  listExperiments,
} from "@/lib/experimentApi";

vi.mock("@/lib/experimentApi", () => ({
  getExperimentStatus: vi.fn(),
  listExperiments: vi.fn(),
  createExperiment: vi.fn(),
  experimentExportUrl: (id: string, format: string) =>
    `http://localhost:8000/api/experiments/${id}/export?output_format=${format}`,
  experimentManifestUrl: (id: string) =>
    `http://localhost:8000/api/experiments/${id}/manifest`,
  ledgerExportUrl: (format: string) =>
    `http://localhost:8000/api/experiments/export?output_format=${format}`,
}));

const statusMock = vi.mocked(getExperimentStatus);
const listMock = vi.mocked(listExperiments);
const createMock = vi.mocked(createExperiment);

function status(): ExperimentStatus {
  return {
    module: "research-experiments",
    module_number: 19,
    status: "implemented",
    description: "Append-only ledger of completed runs.",
    endpoints: ["GET  /api/experiments/status"],
    experiments: {
      available: true,
      experiments_total: 1,
      distinct_models: 1,
      distinct_datasets: 1,
      distinct_targets: 1,
      oldest_recorded_at: "2026-09-28T05:32:36Z",
      newest_recorded_at: "2026-09-28T05:32:36Z",
    },
  };
}

function record(): Experiment {
  return {
    experiment_id: "exp-b73c41",
    dataset_version: 3,
    dataset_hash: "sha256:" + "a".repeat(64),
    n_observations: 1024,
    n_pans: 6,
    date_start: "2025-01-01",
    date_end: "2026-03-31",
    features: ["salinity", "depth", "rainfall"],
    target: "operational_risk",
    target_definition_version: 2,
    train_test_strategy: "80/20 chronological holdout",
    random_seed: 42,
    model: "random-forest",
    hyperparameters: { n_estimators: 300 },
    metrics: { accuracy: 0.94, macro_f1: 0.91 },
    software_versions: { python: "3.12" },
    notes: null,
    content_hash: "sha256:" + "b".repeat(64),
    short_hash: "bbbbbbbbbbbb",
    timestamp: "2026-09-28T05:32:36Z",
    manifest_version: "1.0",
  };
}

function page(total: number): ExperimentListResponse {
  return {
    items: total > 0 ? [record()] : [],
    total,
    limit: 100,
    offset: 0,
    earliest_timestamp: "2026-09-28T05:32:36Z",
    latest_timestamp: "2026-09-28T05:32:36Z",
  };
}

beforeEach(() => {
  statusMock.mockReset();
  listMock.mockReset();
  createMock.mockReset();
  listMock.mockResolvedValue(page(1));
});

describe("ExperimentsClient", () => {
  it("renders the ledger from what the backend reports", async () => {
    statusMock.mockResolvedValue(status());
    render(<ExperimentsClient />);

    expect(await screen.findByText("random-forest")).toBeInTheDocument();
    expect(screen.getByText("operational_risk · definition v2")).toBeInTheDocument();
    expect(screen.getByText(/1,024 observations across 6 pans/)).toBeInTheDocument();
    expect(screen.getByText("Experiments recorded")).toBeInTheDocument();

    const csv = screen.getByRole("link", { name: "CSV" });
    expect(csv).toHaveAttribute(
      "href",
      "http://localhost:8000/api/experiments/exp-b73c41/export?output_format=csv",
    );
    const manifest = screen.getByRole("link", { name: "Manifest" });
    expect(manifest).toHaveAttribute(
      "href",
      "http://localhost:8000/api/experiments/exp-b73c41/manifest",
    );
  });

  it("shows the empty state when no experiment has been recorded", async () => {
    statusMock.mockResolvedValue(status());
    listMock.mockResolvedValue(page(0));
    render(<ExperimentsClient />);

    expect(
      await screen.findByText("No experiments recorded yet"),
    ).toBeInTheDocument();
    expect(screen.queryByText("random-forest")).not.toBeInTheDocument();
  });

  it("shows nothing row-like when the backend is unreachable", async () => {
    statusMock.mockRejectedValue(
      new ApiError("Could not reach the SaltLens API.", { status: 0 }),
    );
    listMock.mockRejectedValue(
      new ApiError("Could not reach the SaltLens API.", { status: 0 }),
    );
    render(<ExperimentsClient />);

    expect(
      await screen.findByText("The experiment ledger is unavailable"),
    ).toBeInTheDocument();
    expect(screen.queryByText("random-forest")).not.toBeInTheDocument();
  });

  it("records a run and says so honestly", async () => {
    const user = userEvent.setup();
    const outcome: ExperimentCreateResponse = {
      created: true,
      reason: null,
      experiment: record(),
    };
    createMock.mockResolvedValue(outcome);
    statusMock.mockResolvedValue(status());
    render(<ExperimentsClient />);

    await user.click(await screen.findByRole("button", { name: "Record experiment" }));

    await user.type(screen.getByLabelText(/Dataset version/), "3");
    await user.type(screen.getByLabelText(/Dataset content hash/), "sha256:" + "a".repeat(64));
    await user.type(screen.getByLabelText(/Observations used/), "1024");
    await user.type(screen.getByLabelText(/Pans spanned/), "6");
    await user.type(screen.getByLabelText(/Data starts/), "2025-01-01");
    await user.type(screen.getByLabelText(/Data ends/), "2026-03-31");
    await user.type(screen.getByLabelText(/Features, in order/), "salinity, depth");
    await user.type(screen.getByLabelText(/Target/), "operational_risk");
    await user.type(screen.getByLabelText(/Definition version/), "2");
    await user.type(screen.getByLabelText(/Train\/test strategy/), "80/20 chronological holdout");
    await user.type(screen.getByLabelText(/Random seed/), "42");
    await user.type(screen.getByLabelText(/^Model/), "random-forest");
    await user.click(screen.getByLabelText(/Hyperparameters/));
    await user.paste('{"n_estimators": 300}');
    await user.click(screen.getByLabelText(/^Metrics/));
    await user.paste('{"accuracy": 0.94}');
    await user.click(screen.getByLabelText(/Software & library versions/));
    await user.paste('{"python": "3.12"}');

    await user.click(screen.getByRole("button", { name: "Record this experiment" }));

    expect(createMock).toHaveBeenCalledTimes(1);
    expect(await screen.findByText(/Recorded as exp-b73c41/)).toBeInTheDocument();
  });

  it("refuses a malformed record before any POST", async () => {
    const user = userEvent.setup();
    statusMock.mockResolvedValue(status());
    render(<ExperimentsClient />);

    await user.click(await screen.findByRole("button", { name: "Record experiment" }));

    await user.type(screen.getByLabelText(/Dataset version/), "3");
    await user.type(screen.getByLabelText(/Features, in order/), "salinity");
    await user.type(screen.getByLabelText(/^Model/), "random-forest");

    await user.click(screen.getByRole("button", { name: "Record this experiment" }));

    expect(createMock).not.toHaveBeenCalled();
    expect(screen.getByText(/Enter the sha256 content hash/)).toBeInTheDocument();
    expect(screen.getByText(/Name the target the run predicted/)).toBeInTheDocument();
  });

  it("surfaces a duplicate submission as nothing-overwritten", async () => {
    const user = userEvent.setup();
    const outcome: ExperimentCreateResponse = {
      created: false,
      reason: "An identical experiment is already recorded.",
      experiment: record(),
    };
    createMock.mockResolvedValue(outcome);
    statusMock.mockResolvedValue(status());
    render(<ExperimentsClient />);

    await user.click(await screen.findByRole("button", { name: "Record experiment" }));

    await user.type(screen.getByLabelText(/Dataset version/), "3");
    await user.type(screen.getByLabelText(/Dataset content hash/), "sha256:" + "a".repeat(64));
    await user.type(screen.getByLabelText(/Observations used/), "1024");
    await user.type(screen.getByLabelText(/Pans spanned/), "6");
    await user.type(screen.getByLabelText(/Data starts/), "2025-01-01");
    await user.type(screen.getByLabelText(/Data ends/), "2026-03-31");
    await user.type(screen.getByLabelText(/Features, in order/), "salinity, depth");
    await user.type(screen.getByLabelText(/Target/), "operational_risk");
    await user.type(screen.getByLabelText(/Definition version/), "2");
    await user.type(screen.getByLabelText(/Train\/test strategy/), "80/20 chronological holdout");
    await user.type(screen.getByLabelText(/Random seed/), "42");
    await user.type(screen.getByLabelText(/^Model/), "random-forest");
    await user.click(screen.getByLabelText(/Hyperparameters/));
    await user.paste('{"n_estimators": 300}');
    await user.click(screen.getByLabelText(/^Metrics/));
    await user.paste('{"accuracy": 0.94}');
    await user.click(screen.getByLabelText(/Software & library versions/));
    await user.paste('{"python": "3.12"}');

    await user.click(screen.getByRole("button", { name: "Record this experiment" }));

    expect(await screen.findByText("That experiment was already recorded")).toBeInTheDocument();
    expect(screen.getByText(/nothing was overwritten/)).toBeInTheDocument();
  });
});