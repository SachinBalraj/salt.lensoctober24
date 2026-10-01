/**
 * Module 19: the research experiment ledger — types and display shaping.
 *
 * Like `validation.ts` and `evaluation.ts`, this module is free of React and of
 * `fetch`, so the rules a reader depends on can be unit-tested directly.
 *
 * The idea this module enforces on the screen is that **a recorded run is a
 * claim to have measured something, and nothing here lets that claim be
 * overwritten**. An experiment carries everything that produced it; the ledger
 * returns recorded runs newest first; an identical resubmission is the stored
 * record, reported that way; and the download links a researcher follows always
 * point at the backend's own export, not at a re-serialisation that could drift.
 */

/** One hyperparameter value, the JSON shapes the backend accepts. */
export type ExperimentHyperparameterValue =
  | string
  | number
  | boolean
  | null;

export type ExperimentHyperparameters = Record<
  string,
  ExperimentHyperparameterValue
>;

export type SoftwareVersions = Record<string, string>;

/** Everything a researcher records about one completed run. */
export type ExperimentCreate = {
  dataset_version: number;
  dataset_hash: string;
  n_observations: number;
  n_pans: number;
  date_start: string | null;
  date_end: string | null;
  features: string[];
  target: string;
  target_definition_version: number;
  train_test_strategy: string;
  random_seed: number;
  model: string;
  hyperparameters: ExperimentHyperparameters;
  metrics: Record<string, number>;
  software_versions: SoftwareVersions;
  notes: string | null;
};

/** One stored experiment, exactly as it will be reproduced. */
export type Experiment = ExperimentCreate & {
  experiment_id: string;
  content_hash: string;
  short_hash: string;
  timestamp: string;
  manifest_version: string;
};

/** What a write to the ledger did — the field that matters is `created`. */
export type ExperimentCreateResponse = {
  created: boolean;
  reason: string | null;
  experiment: Experiment;
};

/** A page of the experiment ledger, newest first. */
export type ExperimentListResponse = {
  items: Experiment[];
  total: number;
  limit: number;
  offset: number;
  earliest_timestamp: string | null;
  latest_timestamp: string | null;
};

/** The self-contained reproducibility document for one experiment. */
export type ExperimentManifest = {
  manifest_version: string;
  experiment_id: string;
  timestamp: string;
  content_hash: string;
  hash_algorithm: string;
  canonical_form: string;
  record: ExperimentCreate;
};

/** The live record totals the status endpoint reports. */
export type ExperimentStatus = {
  module: string;
  module_number: number;
  status: string;
  description: string;
  endpoints: string[];
  experiments: {
    available: boolean;
    detail?: string;
    experiments_total?: number;
    distinct_models?: number;
    distinct_datasets?: number;
    distinct_targets?: number;
    oldest_recorded_at?: string | null;
    newest_recorded_at?: string | null;
  };
};

// -- display shaping ------------------------------------------------------

/** A stored hash, abbreviated for a tiled row without hiding the digest. */
export function abbreviateHash(hash: string): string {
  if (hash.length <= 16) return hash;
  return `${hash.slice(0, 8)}…${hash.slice(-8)}`;
}

/** A timestamp as it reads in a row, e.g. "28 Sep 2026, 05:32". */
export function formatInstant(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "Recovered the identity from the content hash, not from a label." */
export function formatLedgerTotal(total: number): string {
  return `${total} experiment${total === 1 ? "" : "s"}`;
}

function formatDay(iso: string): string {
  const parsed = new Date(`${iso}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

/**
 * The date range as one clause.
 *
 * Reads "5 Jan 2025 → 31 Mar 2026" for a full range, and the honest single-ended
 * forms when only one bound was recorded, rather than inventing the missing one.
 */
export function formatDateRange(
  start: string | null,
  end: string | null,
): string {
  if (start && end) return `${formatDay(start)} → ${formatDay(end)}`;
  if (start) return `from ${formatDay(start)}`;
  if (end) return `to ${formatDay(end)}`;
  return "dates not recorded";
}

/** How much of the dataset one run stood on, as one sentence. */
export function describeExperimentScope(experiment: Experiment): string {
  const observations = experiment.n_observations.toLocaleString("en-GB");
  const pan = experiment.n_pans === 1 ? "pan" : "pans";
  return `${observations} observations across ${experiment.n_pans} ${pan}`;
}

/**
 * Hyperparameters as one readable clause.
 *
 * A row already carries the model name and the dataset identity; this is the
 * "what actually differed from a default." A run that kept every default says so
 * rather than pretending to an empty object.
 */
export function describeHyperparameters(
  hyperparameters: ExperimentHyperparameters,
): string {
  const entries = Object.entries(hyperparameters);
  if (entries.length === 0) return "default hyperparameters";
  return entries.map(([name, value]) => `${name}=${String(value)}`).join(", ");
}

/**
 * What a page of the ledger is actually showing.
 *
 * Honest about the paging the list endpoint applies, so "3 experiments" next to
 * a page of one never reads as a promise that three rows will appear.
 */
export function describeLedgerPage(list: ExperimentListResponse): string {
  if (list.total === 0) return "No experiments recorded yet.";
  const shown = Math.min(list.items.length, list.total);
  const experiments = shown === 1 ? "experiment" : "experiments";
  if (shown === list.total) {
    return `Showing all ${list.total} recorded ${experiments}, newest first.`;
  }
  return `Showing the ${shown} most recent of ${list.total} recorded experiments.`;
}

/** The sentence after a write settles whether anything was overwritten. */
export function describeCreateOutcome(
  response: ExperimentCreateResponse,
): string {
  if (response.created) {
    return `Recorded as ${response.experiment.experiment_id}.`;
  }
  return `${response.experiment.experiment_id} was already recorded; nothing was overwritten.`;
}

// -- recording form -------------------------------------------------------

const HASH_PATTERN = /^sha256:[0-9a-f]{64}$/;
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;
const FEATURE_SEPARATOR = /\s*,\s*/;

export type ExperimentFormValues = {
  dataset_version: string;
  dataset_hash: string;
  n_observations: string;
  n_pans: string;
  date_start: string;
  date_end: string;
  features: string;
  target: string;
  target_definition_version: string;
  train_test_strategy: string;
  random_seed: string;
  model: string;
  hyperparameters_json: string;
  metrics_json: string;
  software_versions_json: string;
  notes: string;
};

export type ExperimentFormErrors = Partial<
  Record<keyof ExperimentFormValues, string>
>;

export const EMPTY_EXPERIMENT_FORM: ExperimentFormValues = {
  dataset_version: "",
  dataset_hash: "",
  n_observations: "",
  n_pans: "",
  date_start: "",
  date_end: "",
  features: "",
  target: "",
  target_definition_version: "",
  train_test_strategy: "",
  random_seed: "",
  model: "",
  hyperparameters_json: "",
  metrics_json: "",
  software_versions_json: "",
  notes: "",
};

function requiredInteger(
  raw: string,
  minimum: number,
): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (!/^\d+$/.test(trimmed)) return null;
  const value = Number.parseInt(trimmed, 10);
  return value >= minimum ? value : null;
}

function parseObjectField(
  raw: string,
): Record<string, never> | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return null;
    }
    return parsed as Record<string, never>;
  } catch {
    return null;
  }
}

/**
 * Turn the text fields on the recording form into the payload the API stores,
 * or the per-field errors that say why it cannot.
 *
 * The same rules the backend enforces are checked here first, so a bad value is
 * a message beside the input that produced it rather than a 422 the researcher
 * has to read. The form's keys are the API's field names, so a validation the
 * server still rejects can attach to the same input.
 */
export function buildExperimentCreate(
  values: ExperimentFormValues,
): { payload: ExperimentCreate | null; errors: ExperimentFormErrors } {
  const errors: ExperimentFormErrors = {};

  const dataset_version = requiredInteger(values.dataset_version, 1);
  if (dataset_version === null) {
    errors.dataset_version = "Enter a dataset version of 1 or more.";
  }

  const dataset_hash = values.dataset_hash.trim();
  if (!HASH_PATTERN.test(dataset_hash)) {
    errors.dataset_hash = "Enter the sha256 content hash, like sha256: followed by 64 hex digits.";
  }

  const n_observations = requiredInteger(values.n_observations, 0);
  if (n_observations === null) {
    errors.n_observations = "Enter a whole number of rows used.";
  }

  const n_pans = requiredInteger(values.n_pans, 0);
  if (n_pans === null) {
    errors.n_pans = "Enter a whole number of pans.";
  }

  const date_start = values.date_start.trim() || null;
  const date_end = values.date_end.trim() || null;
  if (date_start && !DATE_PATTERN.test(date_start)) {
    errors.date_start = "Dates are YYYY-MM-DD.";
  }
  if (date_end && !DATE_PATTERN.test(date_end)) {
    errors.date_end = "Dates are YYYY-MM-DD.";
  }
  if (
    date_start &&
    date_end &&
    !errors.date_start &&
    !errors.date_end &&
    date_start > date_end
  ) {
    errors.date_end = "Must not be before the start date.";
  }

  const features = values.features
    .split(FEATURE_SEPARATOR)
    .map((feature) => feature.trim())
    .filter((feature) => feature.length > 0);
  if (features.length === 0) {
    errors.features = "Name at least one feature, comma-separated.";
  }

  const target = values.target.trim();
  if (!target) {
    errors.target = "Name the target the run predicted.";
  }

  const target_definition_version = requiredInteger(
    values.target_definition_version,
    1,
  );
  if (target_definition_version === null) {
    errors.target_definition_version = "Enter a definition version of 1 or more.";
  }

  const train_test_strategy = values.train_test_strategy.trim();
  if (!train_test_strategy) {
    errors.train_test_strategy = "Describe the split, in words.";
  }

  const random_seed = requiredInteger(values.random_seed, 0);
  if (random_seed === null) {
    errors.random_seed = "Enter a whole seed of 0 or more.";
  }

  const model = values.model.trim();
  if (!model) {
    errors.model = "Name the model that was fitted.";
  }

  const hyperparameters =
    parseObjectField(values.hyperparameters_json) ?? {};
  if (values.hyperparameters_json.trim() && parseObjectField(values.hyperparameters_json) === null) {
    errors.hyperparameters_json = "Must be a JSON object, e.g. {\"n_estimators\": 300}.";
  }

  const metrics =
    parseObjectField(values.metrics_json) ?? {};
  if (values.metrics_json.trim() && parseObjectField(values.metrics_json) === null) {
    errors.metrics_json = "Must be a JSON object of metric names to numbers.";
  }

  const software_versions =
    parseObjectField(values.software_versions_json) ?? {};
  if (values.software_versions_json.trim() && parseObjectField(values.software_versions_json) === null) {
    errors.software_versions_json = "Must be a JSON object of library names to versions.";
  }

  const notes = values.notes.trim() || null;

  if (Object.keys(errors).length > 0) {
    return { payload: null, errors };
  }

  return {
    payload: {
      dataset_version: dataset_version as number,
      dataset_hash,
      n_observations: n_observations as number,
      n_pans: n_pans as number,
      date_start,
      date_end,
      features,
      target,
      target_definition_version: target_definition_version as number,
      train_test_strategy,
      random_seed: random_seed as number,
      model,
      hyperparameters,
      metrics,
      software_versions,
      notes,
    },
    errors,
  };
}