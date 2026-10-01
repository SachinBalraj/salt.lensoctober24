/**
 * Research experiment display-shaping and form tests.
 *
 * These are the tests that would catch the failure this module exists to
 * prevent: a row on screen that reads as a result it is not. So the assertions
 * are about honesty as much as correctness —
 *
 * - the ledger page admits what it is showing ("most recent of N"), never a
 *   count the page itself cannot back;
 * - a record-reuse outcome is worded as nothing being overwritten, because that
 *   is what the ledger did;
 * - the recording form's validation matches the backend's rules field by field,
 *   so a bad value is a message beside the input that produced it;
 * - a hash that is not a hash, and JSON that is not an object, are refused
 *   rather than stored.
 */

import { describe, expect, it } from "vitest";
import {
  abbreviateHash,
  buildExperimentCreate,
  describeCreateOutcome,
  describeExperimentScope,
  describeHyperparameters,
  describeLedgerPage,
  EMPTY_EXPERIMENT_FORM,
  formatDateRange,
  formatInstant,
  formatLedgerTotal,
  type Experiment,
  type ExperimentCreateResponse,
  type ExperimentListResponse,
} from "@/lib/experiment";

function experiment(overrides: Partial<Experiment> = {}): Experiment {
  return {
    experiment_id: "exp-abc123",
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
    metrics: { accuracy: 0.94 },
    software_versions: { python: "3.12" },
    notes: null,
    content_hash: "sha256:" + "b".repeat(64),
    short_hash: "bbbbbbbbbbbb",
    timestamp: "2026-09-28T05:32:36Z",
    manifest_version: "1.0",
    ...overrides,
  };
}

function list(total: number, items = total): ExperimentListResponse {
  return {
    items: Array.from({ length: items }, () => experiment()),
    total,
    limit: 100,
    offset: 0,
    earliest_timestamp: "2026-01-01T00:00:00Z",
    latest_timestamp: "2026-09-28T05:32:36Z",
  };
}

describe("abbreviateHash", () => {
  it("shortens a full content hash without hiding it entirely", () => {
    const hash = "sha256:" + "ab".repeat(32);
    const abbreviated = abbreviateHash(hash);
    expect(abbreviated).toBe(`sha256:a…${"ab".repeat(4)}`);
    expect(abbreviated).toHaveLength(17);
  });

  it("leaves a short value untouched", () => {
    expect(abbreviateHash("exp-abc123")).toBe("exp-abc123");
  });
});

describe("formatInstant", () => {
  it("formats a backend timestamp", () => {
    expect(formatInstant("2026-09-28T05:32:36Z")).not.toBe("2026-09-28T05:32:36Z");
    expect(formatInstant("2026-09-28T05:32:36Z")).toMatch(/2026/);
  });

  it("returns an unparseable value as itself", () => {
    expect(formatInstant("not-an-instant")).toBe("not-an-instant");
  });
});

describe("formatDateRange", () => {
  it("renders a full range", () => {
    expect(formatDateRange("2025-01-01", "2026-03-31")).toContain("1 Jan 2025");
    expect(formatDateRange("2025-01-01", "2026-03-31")).toContain("31 Mar 2026");
  });

  it("renders single-ended ranges honestly", () => {
    expect(formatDateRange("2025-01-01", null)).toBe("from 1 Jan 2025");
    expect(formatDateRange(null, "2026-03-31")).toBe("to 31 Mar 2026");
  });

  it("admits when no date was recorded", () => {
    expect(formatDateRange(null, null)).toBe("dates not recorded");
  });
});

describe("describeExperimentScope", () => {
  it("pluralises pans", () => {
    expect(describeExperimentScope(experiment())).toBe("1,024 observations across 6 pans");
    expect(describeExperimentScope(experiment({ n_pans: 1 }))).toBe(
      "1,024 observations across 1 pan",
    );
  });
});

describe("describeHyperparameters", () => {
  it("joins the values that differed from a default", () => {
    expect(describeHyperparameters({ n_estimators: 300, max_depth: 8 })).toBe(
      "n_estimators=300, max_depth=8",
    );
  });

  it("says so when every default was kept", () => {
    expect(describeHyperparameters({})).toBe("default hyperparameters");
  });
});

describe("formatLedgerTotal and describeLedgerPage", () => {
  it("counts one experiment as one", () => {
    expect(formatLedgerTotal(1)).toBe("1 experiment");
    expect(formatLedgerTotal(3)).toBe("3 experiments");
  });

  it("admits when a page is a page", () => {
    expect(describeLedgerPage(list(0))).toBe("No experiments recorded yet.");
    expect(describeLedgerPage(list(3))).toBe(
      "Showing all 3 recorded experiments, newest first.",
    );
    expect(describeLedgerPage(list(137, 100))).toContain(
      "Showing the 100 most recent of 137 recorded experiments.",
    );
  });
});

describe("describeCreateOutcome", () => {
  it("names the new record when one was written", () => {
    const response: ExperimentCreateResponse = {
      created: true,
      reason: null,
      experiment: experiment(),
    };
    expect(describeCreateOutcome(response)).toBe("Recorded as exp-abc123.");
  });

  it("says nothing was overwritten when the record already existed", () => {
    const response: ExperimentCreateResponse = {
      created: false,
      reason: "An identical experiment is already recorded.",
      experiment: experiment(),
    };
    expect(describeCreateOutcome(response)).toContain("exp-abc123 was already recorded");
    expect(describeCreateOutcome(response)).toContain("nothing was overwritten");
  });
});

describe("buildExperimentCreate", () => {
  const VALID = (overrides: Partial<typeof EMPTY_EXPERIMENT_FORM> = {}) => ({
    ...EMPTY_EXPERIMENT_FORM,
    dataset_version: "3",
    dataset_hash: "sha256:" + "a".repeat(64),
    n_observations: "1024",
    n_pans: "6",
    date_start: "2025-01-01",
    date_end: "2026-03-31",
    features: "salinity, depth ,   rainfall",
    target: "operational_risk",
    target_definition_version: "2",
    train_test_strategy: "80/20 chronological holdout",
    random_seed: "42",
    model: "random-forest",
    hyperparameters_json: '{"n_estimators": 300}',
    metrics_json: '{"accuracy": 0.94}',
    software_versions_json: '{"python": "3.12"}',
    notes: "",
    ...overrides,
  });

  it("builds a complete payload from the form", () => {
    const { payload, errors } = buildExperimentCreate(VALID());

    expect(errors).toEqual({});
    expect(payload).toMatchObject({
      dataset_version: 3,
      n_observations: 1024,
      n_pans: 6,
      features: ["salinity", "depth", "rainfall"],
      target_definition_version: 2,
      random_seed: 42,
      model: "random-forest",
      hyperparameters: { n_estimators: 300 },
      metrics: { accuracy: 0.94 },
      software_versions: { python: "3.12" },
      notes: null,
    });
  });

  it("refuses a hash that is not a content hash", () => {
    const { payload, errors } = buildExperimentCreate(
      VALID({ dataset_hash: "not-a-hash" }),
    );
    expect(payload).toBeNull();
    expect(errors.dataset_hash).toBeTruthy();
  });

  it("refuses fields the ledger cannot reproduce without", () => {
    const { payload, errors } = buildExperimentCreate(
      VALID({
        dataset_version: "",
        features: "",
        model: "",
        target_definition_version: "0",
        n_observations: "-1",
      }),
    );
    expect(payload).toBeNull();
    expect(errors.dataset_version).toBeTruthy();
    expect(errors.features).toBeTruthy();
    expect(errors.model).toBeTruthy();
    expect(errors.target_definition_version).toBeTruthy();
    expect(errors.n_observations).toBeTruthy();
  });

  it("refuses a date range that runs backwards", () => {
    const { payload, errors } = buildExperimentCreate(
      VALID({ date_start: "2026-12-01", date_end: "2020-01-01" }),
    );
    expect(payload).toBeNull();
    expect(errors.date_end).toBeTruthy();
  });

  it("refuses JSON that is not an object", () => {
    const { payload, errors } = buildExperimentCreate(
      VALID({ metrics_json: "[0.94]" }),
    );
    expect(payload).toBeNull();
    expect(errors.metrics_json).toBeTruthy();
  });
});