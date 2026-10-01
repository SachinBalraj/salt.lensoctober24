/**
 * Experiment ledger API client tests.
 *
 * Several things are worth locking down here.
 *
 * The URLs, because the export endpoints live beside the list and an experiment
 * that could not be re-derived by its own identifier would be an anecdote. The
 * create request — the ledger is append-only, and the client's whole
 * responsibility is to send the full record and report `created` honestly.
 * And the field-error mapping, so a 422 from the backend attaches its message to
 * the input that produced it.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import type { ExperimentCreate } from "@/lib/experiment";
import {
  createExperiment,
  experimentExportUrl,
  experimentManifestUrl,
  getExperimentManifest,
  getExperimentStatus,
  ledgerExportUrl,
  listExperiments,
} from "@/lib/experimentApi";

const BASE = "http://localhost:8000";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function createPayload(): ExperimentCreate {
  return {
    dataset_version: 3,
    dataset_hash: "sha256:" + "a".repeat(64),
    n_observations: 1024,
    n_pans: 6,
    date_start: "2025-01-01",
    date_end: "2026-03-31",
    features: ["salinity", "depth"],
    target: "operational_risk",
    target_definition_version: 2,
    train_test_strategy: "80/20 chronological holdout",
    random_seed: 42,
    model: "random-forest",
    hyperparameters: { n_estimators: 300 },
    metrics: { accuracy: 0.94 },
    software_versions: { python: "3.12" },
    notes: null,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

async function captureApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (caught) {
    if (caught instanceof ApiError) return caught;
    throw caught;
  }
  throw new Error("Expected the request to fail, but it resolved.");
}

describe("getExperimentStatus", () => {
  it("reads the module status endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ module: "research-experiments" }));
    vi.stubGlobal("fetch", fetchMock);

    await getExperimentStatus();

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/experiments/status`);
  });

  it("reports an unreachable backend as connectivity", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    const error = await captureApiError(getExperimentStatus());
    expect(error.status).toBe(0);
    expect(error.isConnectivity).toBe(true);
    expect(error.message).toContain("Could not reach");
  });
});

describe("listExperiments", () => {
  it("asks for the ledger with no filters by default", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [], total: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await listExperiments();

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toBe(`${BASE}/api/experiments/?`);
  });

  it("passes filters and paging through, trimmed", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ items: [], total: 0 }));
    vi.stubGlobal("fetch", fetchMock);

    await listExperiments({
      model: " random-forest ",
      target: "operational_risk",
      dataset_hash: "sha256:" + "a".repeat(64),
      limit: 100,
      offset: 0,
    });

    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("model=random-forest");
    expect(url).toContain("target=operational_risk");
    expect(url).toContain("dataset_hash=sha256%3A");
    expect(url).toContain("limit=100");
    expect(url).toContain("offset=0");
  });
});

describe("createExperiment", () => {
  it("posts the full record to the ledger root", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ created: true, reason: null, experiment: {} }, 201));
    vi.stubGlobal("fetch", fetchMock);

    const outcome = await createExperiment(createPayload());

    expect(outcome.created).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${BASE}/api/experiments/`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toMatchObject({ model: "random-forest" });
  });

  it("surfaces a reused record as created=false, not as an error", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ created: false, reason: "already there", experiment: {} }, 200));
    vi.stubGlobal("fetch", fetchMock);

    const outcome = await createExperiment(createPayload());
    expect(outcome.created).toBe(false);
  });

  it("attaches backend field errors to the same input names", async () => {
    const detail = [
      { loc: ["body", "features", 0], msg: "Value error, feature name is empty." },
      { loc: ["body", "metrics"], msg: "Metrics must not be empty." },
    ];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ detail }, 422));
    vi.stubGlobal("fetch", fetchMock);

    const error = await captureApiError(createExperiment(createPayload()));
    expect(error.status).toBe(422);
    expect(error.fields["features.0"]).toBeTruthy();
    expect(error.fields.metrics).toContain("must not be empty");
  });
});

describe("export and manifest URLs", () => {
  it("builds per-experiment download URLs with their formats", () => {
    expect(experimentExportUrl("exp-abc", "csv")).toBe(
      `${BASE}/api/experiments/exp-abc/export?output_format=csv`,
    );
    expect(experimentExportUrl("exp-abc", "json")).toBe(
      `${BASE}/api/experiments/exp-abc/export?output_format=json`,
    );
    expect(experimentManifestUrl("exp-abc")).toBe(
      `${BASE}/api/experiments/exp-abc/manifest`,
    );
  });

  it("builds the ledger export URL with the applied filters", () => {
    const url = ledgerExportUrl("json", {
      model: "random-forest",
      dataset_hash: "sha256:" + "a".repeat(64),
    });
    expect(url).toBe(
      `${BASE}/api/experiments/export?output_format=json&model=random-forest&dataset_hash=sha256%3A${"a".repeat(64)}`,
    );
  });
});

describe("getExperimentManifest", () => {
  it("reads the manifest endpoint", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse({ experiment_id: "exp-abc", record: {} }));
    vi.stubGlobal("fetch", fetchMock);

    await getExperimentManifest("exp-abc");

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/api/experiments/exp-abc/manifest`);
  });
});