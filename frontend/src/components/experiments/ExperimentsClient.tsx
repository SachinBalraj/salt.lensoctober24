"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import ExperimentRecorder from "@/components/experiments/ExperimentRecorder";
import { Button } from "@/components/ui/Controls";
import { Alert, EmptyState, Skeleton } from "@/components/ui/Feedback";
import { TextField } from "@/components/ui/Fields";
import { Card, CardHeader, StatTile } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import {
  abbreviateHash,
  describeCreateOutcome,
  describeExperimentScope,
  describeHyperparameters,
  describeLedgerPage,
  formatDateRange,
  formatInstant,
  formatLedgerTotal,
  type Experiment,
  type ExperimentCreateResponse,
  type ExperimentListResponse,
  type ExperimentStatus,
} from "@/lib/experiment";
import {
  experimentExportUrl,
  experimentManifestUrl,
  getExperimentStatus,
  ledgerExportUrl,
  listExperiments,
} from "@/lib/experimentApi";

interface LoadedState {
  key: string;
  status: ExperimentStatus | null;
  list: ExperimentListResponse | null;
  error: ApiError | null;
}

interface AppliedFilters {
  model: string;
  target: string;
  datasetHash: string;
}

const EMPTY_FILTERS: AppliedFilters = { model: "", target: "", datasetHash: "" };

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(fallback, { status: -1 });
}

function filterKey(filters: AppliedFilters): string {
  return `${filters.model.trim()}|${filters.target.trim()}|${filters.datasetHash.trim()}`;
}

/** The header tiles drawn from the status endpoint's live counts. */
function LedgerTiles({ status }: { status: ExperimentStatus | null }) {
  const counts: ExperimentStatus["experiments"] = status?.experiments ?? { available: false };
  const tiles: Array<{ label: string; value: ReactNode; hint: string }> = [
    {
      label: "Experiments recorded",
      value: counts.experiments_total ?? "—",
      hint: "Rows in the ledger",
    },
    {
      label: "Distinct models",
      value: counts.distinct_models ?? "—",
      hint: "Model types recorded",
    },
    {
      label: "Distinct datasets",
      value: counts.distinct_datasets ?? "—",
      hint: "Datasets by content hash",
    },
    {
      label: "Distinct targets",
      value: counts.distinct_targets ?? "—",
      hint: "Named targets predicted",
    },
    {
      label: "Recent run",
      value: counts.newest_recorded_at ? formatInstant(counts.newest_recorded_at) : "—",
      hint: "The newest row in the ledger",
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {tiles.map((tile) => (
        <StatTile key={tile.label} label={tile.label} value={tile.value} hint={tile.hint} />
      ))}
    </div>
  );
}

/** One recorded experiment as a row a researcher can read and download. */
function ExperimentRow({ experiment }: { experiment: Experiment }) {
  return (
    <li className="flex flex-col gap-3 px-5 py-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[13px] font-semibold text-ink">
            {experiment.model}
          </p>
          <p className="mt-0.5 text-xs text-muted">
            {experiment.target} · definition v{experiment.target_definition_version}
          </p>
        </div>
        <div className="shrink-0 text-right">
          <p className="font-mono text-[11px] text-muted">
            {abbreviateHash(experiment.dataset_hash)} · v{experiment.dataset_version}
          </p>
          <p className="mt-0.5 text-[11px] text-faint">
            {formatInstant(experiment.timestamp)}
          </p>
        </div>
      </div>

      <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-[11px] text-muted sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <dt className="text-faint">Scope</dt>
          <dd className="mt-0.5">{describeExperimentScope(experiment)}</dd>
        </div>
        <div>
          <dt className="text-faint">Features</dt>
          <dd className="mt-0.5">{experiment.features.length} in order</dd>
        </div>
        <div>
          <dt className="text-faint">Dates</dt>
          <dd className="mt-0.5">{formatDateRange(experiment.date_start, experiment.date_end)}</dd>
        </div>
        <div>
          <dt className="text-faint">Hyperparameters</dt>
          <dd className="mt-0.5 truncate" title={describeHyperparameters(experiment.hyperparameters)}>
            {describeHyperparameters(experiment.hyperparameters)}
          </dd>
        </div>
      </dl>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {Object.entries(experiment.metrics).map(([name, value]) => (
            <span key={name} className="font-mono text-[11px] text-ink-soft">
              {name} <span className="text-muted">{String(value)}</span>
            </span>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[10px] text-faint">
            {experiment.experiment_id} · {abbreviateHash(experiment.content_hash)}
          </span>
          <a
            href={experimentExportUrl(experiment.experiment_id, "csv")}
            className="rounded-md border border-line px-2 py-1 text-[11px] font-medium text-ink-soft transition-colors hover:bg-inset"
          >
            CSV
          </a>
          <a
            href={experimentExportUrl(experiment.experiment_id, "json")}
            className="rounded-md border border-line px-2 py-1 text-[11px] font-medium text-ink-soft transition-colors hover:bg-inset"
          >
            JSON
          </a>
          <a
            href={experimentManifestUrl(experiment.experiment_id)}
            className="rounded-md border border-line px-2 py-1 text-[11px] font-medium text-ink-soft transition-colors hover:bg-inset"
          >
            Manifest
          </a>
        </div>
      </div>
    </li>
  );
}

/**
 * The client half of `/experiments`.
 *
 * Holds the ledger filters and the recording form state — the two things a
 * server component cannot — and reads the module status and the ledger from the
 * backend. The identical-resubmission outcome is surfaced as the ledger refusing
 * to write a second row, never as a new record, because that is what it is.
 */
export default function ExperimentsClient() {
  const [draft, setDraft] = useState<AppliedFilters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<AppliedFilters>(EMPTY_FILTERS);
  const [reloadToken, setReloadToken] = useState(0);
  const [state, setState] = useState<LoadedState | null>(null);
  const [recording, setRecording] = useState(false);
  const [outcome, setOutcome] = useState<ExperimentCreateResponse | null>(null);

  const requestKey = `${filterKey(applied)}|${reloadToken}`;
  const loading = state?.key !== requestKey;
  const list = state?.list ?? null;
  const status = state?.status ?? null;
  const error = state?.error ?? null;
  const connectivity = error?.isConnectivity ?? false;
  const filtersDirty =
    draft.model.trim() !== applied.model.trim() ||
    draft.target.trim() !== applied.target.trim() ||
    draft.datasetHash.trim() !== applied.datasetHash.trim();

  const load = useCallback(async () => {
    try {
      const [statusReport, ledgerPage] = await Promise.all([
        getExperimentStatus(),
        listExperiments({
          model: applied.model || undefined,
          target: applied.target || undefined,
          dataset_hash: applied.datasetHash || undefined,
          limit: 100,
          offset: 0,
        }),
      ]);
      setState({ key: requestKey, status: statusReport, list: ledgerPage, error: null });
    } catch (caught) {
      setState({
        key: requestKey,
        status: null,
        list: null,
        error: asApiError(caught, "Unexpected error loading the experiment ledger."),
      });
    }
  }, [requestKey, applied]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await load();
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [load]);

  function applyFilters() {
    setApplied(draft);
  }

  function resetFilters() {
    setDraft(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
  }

  function handleRecorded(outcomeReport: ExperimentCreateResponse) {
    setOutcome(outcomeReport);
    setRecording(false);
    setReloadToken((token) => token + 1);
  }

  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-7 lg:px-8 lg:py-9">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
            Module 19 · Research experiments
          </p>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-ink">
            Research experiments
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-muted">
            The append-only ledger of completed research runs: dataset identity,
            features, target definition, split, seed, model, hyperparameters,
            metrics and the software versions that produced them. Results are
            never overwritten — an identical submission is the stored record.
          </p>
        </div>
        <Button variant="primary" onClick={() => setRecording((current) => !current)}>
          {recording ? "Close recording" : "Record experiment"}
        </Button>
      </header>

      <div className="mt-6 space-y-5">
        {error ? (
          <Alert
            tone={connectivity ? "warning" : "danger"}
            title={
              connectivity
                ? "The experiment ledger is unavailable"
                : "The experiment ledger could not be loaded"
            }
            action={
              <Button variant="secondary" onClick={() => setReloadToken((t) => t + 1)}>
                Try again
              </Button>
            }
          >
            <p>{error.message}</p>
            {connectivity ? (
              <p className="mt-1 text-xs">
                Every row here comes from experiments recorded in the database.
                Without a reachable backend no row exists, and none is shown in
                its place.
              </p>
            ) : null}
          </Alert>
        ) : null}

        {loading ? (
          <div className="space-y-3">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-32 w-full" />
            <Skeleton className="h-32 w-full" />
          </div>
        ) : status ? (
          <LedgerTiles status={status} />
        ) : null}

        {recording ? (
          <Card>
            <CardHeader
              title="Record a completed experiment"
              description="Every field below is part of the run's identity. An identical resubmission returns the stored record instead of writing a new row."
            />
            <div className="p-5">
              <ExperimentRecorder
                onRecorded={handleRecorded}
                onCancelled={() => setRecording(false)}
              />
            </div>
          </Card>
        ) : null}

        {outcome ? (
          <Alert
            tone={outcome.created ? "success" : "info"}
            title={
              outcome.created
                ? "Experiment recorded"
                : "That experiment was already recorded"
            }
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  setOutcome(null);
                  setReloadToken((token) => token + 1);
                }}
              >
                Dismiss
              </Button>
            }
          >
            <p>{describeCreateOutcome(outcome)}</p>
            {!outcome.created ? (
              <p className="mt-1 text-xs">
                {outcome.reason ?? "Results are never overwritten."}
              </p>
            ) : null}
          </Alert>
        ) : null}

        <Card>
          <CardHeader
            title="Ledger of recorded experiments"
            description="Newest first. Filters narrow model, target and dataset identity; exports carry the same filter."
          />
          <div className="grid grid-cols-1 gap-3 border-b border-line p-5 sm:grid-cols-3">
            <TextField
              name="filter-model"
              label="Filter by model"
              value={draft.model}
              onChange={(value) => setDraft((current) => ({ ...current, model: value }))}
              placeholder="random-forest"
            />
            <TextField
              name="filter-target"
              label="Filter by target"
              value={draft.target}
              onChange={(value) => setDraft((current) => ({ ...current, target: value }))}
              placeholder="operational_risk"
            />
            <TextField
              name="filter-dataset"
              label="Filter by dataset hash"
              value={draft.datasetHash}
              onChange={(value) => setDraft((current) => ({ ...current, datasetHash: value }))}
              placeholder="sha256:"
            />
          </div>

          {list && !error ? (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
                <p className="text-xs text-muted">{describeLedgerPage(list)}</p>
                <div className="flex items-center gap-2">
                  <Button variant="secondary" onClick={applyFilters} disabled={!filtersDirty}>
                    Apply filters
                  </Button>
                  <Button variant="ghost" onClick={resetFilters} disabled={!filtersDirty && !applied.model && !applied.target && !applied.datasetHash}>
                    Reset
                  </Button>
                  <span className="h-4 w-px bg-line" />
                  <a
                    href={ledgerExportUrl("csv", {
                      model: applied.model || undefined,
                      target: applied.target || undefined,
                      dataset_hash: applied.datasetHash || undefined,
                    })}
                    className="rounded-md border border-line px-2 py-1 text-[11px] font-medium text-ink-soft transition-colors hover:bg-inset"
                  >
                    Ledger CSV
                  </a>
                  <a
                    href={ledgerExportUrl("json", {
                      model: applied.model || undefined,
                      target: applied.target || undefined,
                      dataset_hash: applied.datasetHash || undefined,
                    })}
                    className="rounded-md border border-line px-2 py-1 text-[11px] font-medium text-ink-soft transition-colors hover:bg-inset"
                  >
                    Ledger JSON
                  </a>
                </div>
              </div>

              {list.total === 0 ? (
                <EmptyState
                  title="No experiments recorded yet"
                  description="Nothing has been measured and filed in the ledger yet, so there is no result to re-derive. Record the first completed run and it appears here."
                  action={
                    <Button
                      variant="primary"
                      onClick={() => setRecording(true)}
                    >
                      Record the first experiment
                    </Button>
                  }
                />
              ) : (
                <ul className="divide-y divide-line">
                  {list.items.map((experiment) => (
                    <ExperimentRow key={experiment.experiment_id} experiment={experiment} />
                  ))}
                </ul>
              )}
            </>
          ) : null}
        </Card>

        <p className="text-xs text-muted">
          {status?.experiments.available
            ? `${formatLedgerTotal(status?.experiments.experiments_total ?? 0)} in the ledger.`
            : "Ledger totals unavailable — the backend does not report them."}
        </p>
      </div>
    </div>
  );
}