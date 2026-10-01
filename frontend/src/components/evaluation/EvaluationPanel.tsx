"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ComparisonCharts, {
  ConfusionMatrix,
  PerClassTable,
} from "@/components/evaluation/ComparisonCharts";
import ComparisonTable from "@/components/evaluation/ComparisonTable";
import FeatureImportanceCard from "@/components/evaluation/FeatureImportanceCard";
import { Button } from "@/components/ui/Controls";
import { Alert, EmptyState, Skeleton } from "@/components/ui/Feedback";
import { Badge, Card, CardHeader, StatTile } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import {
  columnTitle,
  formatAlgorithm,
  formatContentHash,
  formatReading,
  formatTimestamp,
  MISSING_DISPLAY,
  type CompareVersionsResponse,
  type EvaluationDetail,
  type EvaluationListResponse,
  type EvaluableModel,
} from "@/lib/evaluation";
import {
  compareVersions,
  evaluationExportUrl,
  getEvaluation,
  listEvaluations,
  runEvaluation,
} from "@/lib/evaluationApi";

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError(fallback, { status: 500 });
}

/**
 * The sentence shown when a run is refused, by cause.
 *
 * The two 409s are separated because they are fixed in different ways, and a
 * message that said "try again" for a drifted dataset would send an operator to do
 * the one thing that cannot help. A dataset that has drifted needs the model
 * re-registered against the records as they now are; a missing artifact needs the
 * file restored or the model re-trained. Neither is fixed by pressing the button
 * again, so neither offers it.
 */
function refusalGuidance(error: ApiError): string | null {
  switch (error.code) {
    case "dataset_drift":
      return "The stored records no longer match the dataset this model version was fitted to. Re-train against the current records; evaluating it again cannot make the two agree.";
    case "artifact_unavailable":
      return "The fitted model is not on this host. Restore the artifact file, or re-train to produce a new one. Nothing was written.";
    case "not_evaluable":
      return "This model has not been evaluated yet, or the model id does not resolve. Nothing is inferred from a model merely existing.";
    case null:
      return null;
    default:
      return "Nothing was written. The counts in the response say what the module refused on.";
  }
}

/** One stored report, in full: figures, per-class detail, the matrix, provenance. */
function ReportDetail({
  detail,
}: {
  detail: EvaluationDetail;
}) {
  const evaluation = detail.evaluation;
  const provenance = detail.provenance;

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Rows evaluated"
          value={detail.evaluated_rows}
          hint={detail.evaluation_set}
        />
        <StatTile
          label="Accuracy"
          value={formatReading(evaluation.accuracy)}
          hint={
            evaluation.accuracy.computed
              ? "Fraction of rows classified correctly"
              : "Not calculated"
          }
        />
        <StatTile
          label="Macro F1"
          value={formatReading(evaluation.f1)}
          hint={
            evaluation.f1.computed
              ? "The criterion the selection rule uses"
              : "Not calculated"
          }
        />
        <StatTile
          label="Classes"
          value={evaluation.classes.length}
          hint={
            evaluation.classes.length > 0
              ? `Support: ${evaluation.classes
                  .map((label) => `${label} ${evaluation.class_distribution[label] ?? 0}`)
                  .join(", ")}`
              : undefined
          }
        />
      </div>

      {detail.notes.length > 0 ? (
        <Alert tone="info" title="About this report">
          <ul className="list-disc space-y-1 pl-4">
            {detail.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </Alert>
      ) : null}

      <Card>
        <CardHeader
          title="The candidate comparison"
          description="The four classifiers trained for this model version, scored on the same held-out rows."
        />
        <ComparisonTable
          comparison={detail.comparison}
          caption={`Classifier comparison for ${detail.model_id}`}
        />
      </Card>

      <ComparisonCharts comparison={detail.comparison} />

      {/* The same held-out rows, attributed rather than scored: which features the
          model leaned on when it was right and when it was wrong. */}
      <FeatureImportanceCard
        key={`${detail.model_id}-${detail.version}`}
        modelId={detail.model_id}
        version={detail.version}
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Confusion matrix"
            description="Counts, not rates. A zero here is a fact about the model; an absent metric is a different thing entirely."
          />
          <ConfusionMatrix evaluation={evaluation} />
        </Card>
        <Card>
          <CardHeader
            title="Per-class performance"
            description="Each class on its own rows, with the row counts the rates are of."
          />
          <PerClassTable evaluation={evaluation} />
        </Card>
      </div>

      <Card>
        <CardHeader
          title="Cross-validation"
          description={
            detail.cross_validation.performed
              ? `The same rows, resampled ${detail.cross_validation.actual_splits ?? 0} times under the design the split dictated.`
              : "Whether the score above rests on one held-out set or on several."
          }
        />
        {detail.cross_validation.performed ? (
          <div className="px-5 py-4">
            {detail.cross_validation.aggregate ? (
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {(["accuracy", "f1", "roc_auc", "average_precision"] as const).map(
                  (key) => {
                    const reading = detail.cross_validation.aggregate?.[key];
                    return (
                      <div key={key}>
                        <dt className="text-[11px] font-medium uppercase tracking-wide text-faint">
                          {columnTitle(detail.comparison, key)}
                        </dt>
                        <dd
                          className={`tabular mt-0.5 text-lg font-semibold ${
                            !reading?.computed ? "text-faint" : "text-ink"
                          }`}
                          title={reading?.reason ?? undefined}
                        >
                          {formatReading(reading)}
                        </dd>
                      </div>
                    );
                  },
                )}
              </dl>
            ) : null}
            {detail.cross_validation.notes?.length ? (
              <ul className="mt-3 list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-muted">
                {detail.cross_validation.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            ) : null}
            {detail.cross_validation.folds?.length ? (
              <details className="mt-3">
                <summary className="cursor-pointer text-[12px] font-medium text-accent">
                  Show the {detail.cross_validation.folds.length} folds
                </summary>
                <table className="mt-2 w-full text-[12px]">
                  <thead>
                    <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-faint">
                      <th className="py-1.5 pr-3 font-medium">Fold</th>
                      <th className="py-1.5 pr-3 font-medium">Test rows</th>
                      <th className="py-1.5 pr-3 font-medium">Accuracy</th>
                      <th className="py-1.5 pr-3 font-medium">Macro F1</th>
                      <th className="py-1.5 font-medium">Note</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {detail.cross_validation.folds.map((fold) => (
                      <tr key={fold.fold}>
                        <td className="tabular py-1.5 pr-3 text-ink">{fold.fold}</td>
                        <td className="tabular py-1.5 pr-3 text-ink">{fold.test_rows}</td>
                        <td className="tabular py-1.5 pr-3 text-ink">
                          {formatReading(fold.metrics?.accuracy)}
                        </td>
                        <td className="tabular py-1.5 pr-3 text-ink">
                          {formatReading(fold.metrics?.f1)}
                        </td>
                        <td className="py-1.5 text-muted">{fold.note ?? ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            ) : null}
          </div>
        ) : (
          <div className="px-5 py-4">
            <p className="text-[13px] text-muted">
              {detail.cross_validation.reason ??
                "Cross-validation was not run for this report, and the backend attached no reason."}
            </p>
            <p className="mt-1.5 text-[11px] leading-relaxed text-faint">
              The figures above still come from a held-out set. This section says
              whether that set was one partition or several; it does not add a score.
            </p>
          </div>
        )}
      </Card>

      <Card>
        <CardHeader
          title="How this run was produced"
          description="Everything needed to re-derive it, and to tell it apart from another run."
        />
        <dl className="grid gap-x-6 gap-y-3 px-5 py-4 sm:grid-cols-2 lg:grid-cols-3">
          <Provenance label="Model" value={formatAlgorithm(detail.algorithm)} />
          <Provenance label="Version" value={detail.version} />
          <Provenance label="Target" value={detail.target} />
          <Provenance label="Dataset" value={provenance.dataset_id ?? MISSING_DISPLAY} />
          <Provenance
            label="Dataset revision"
            value={
              provenance.dataset_version === null
                ? MISSING_DISPLAY
                : String(provenance.dataset_version)
            }
          />
          <Provenance
            label="Content hash"
            value={formatContentHash(provenance.dataset_content_hash)}
            mono
          />
          <Provenance label="Split strategy" value={provenance.split_strategy} />
          <Provenance
            label="Seed"
            value={
              provenance.random_seed === null
                ? MISSING_DISPLAY
                : String(provenance.random_seed)
            }
            mono
          />
          <Provenance
            label="Evaluated"
            value={formatTimestamp(detail.evaluated_at)}
          />
          <Provenance
            label="Trained"
            value={formatTimestamp(detail.trained_at)}
          />
          <Provenance
            label="Features"
            value={
              provenance.feature_names.length > 0
                ? provenance.feature_names.join(", ")
                : MISSING_DISPLAY
            }
          />
          <Provenance
            label="scikit-learn"
            value={provenance.library_versions["scikit-learn"] ?? MISSING_DISPLAY}
            mono
          />
        </dl>
        <div className="flex flex-wrap gap-2 border-t border-line px-5 py-3">
          <a
            href={evaluationExportUrl(detail.model_id, "json")}
            className="text-[12px] font-medium text-accent underline hover:text-ink-soft"
            download
          >
            Download the record (JSON)
          </a>
          <a
            href={evaluationExportUrl(detail.model_id, "csv")}
            className="text-[12px] font-medium text-accent underline hover:text-ink-soft"
            download
          >
            Download the comparison table (CSV)
          </a>
        </div>
      </Card>
    </div>
  );
}

function Provenance({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  const absent = value === MISSING_DISPLAY;
  return (
    <div className="min-w-0">
      <dt className="text-[11px] font-medium uppercase tracking-wide text-faint">
        {label}
      </dt>
      <dd
        className={`mt-0.5 truncate text-[13px] ${
          absent ? "text-faint" : mono ? "font-mono text-[12px] text-ink" : "text-ink"
        }`}
        title={value}
      >
        {value}
      </dd>
    </div>
  );
}

/** Across model versions, when the rows can honestly be compared. */
function VersionsPanel({ data }: { data: CompareVersionsResponse }) {
  if (data.count === 0) {
    return (
      <Card>
        <CardHeader
          title="Across model versions"
          description="Whether a later model did better than an earlier one."
        />
        <p className="px-5 py-4 text-[13px] text-muted">
          Nothing to compare. No evaluation has been stored yet, and nothing is
          inferred from a model merely existing.
        </p>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Across model versions"
        description="Whether a later model did better than an earlier one."
        actions={<span className="tabular text-xs text-muted">{data.count} stored</span>}
      />
      {data.comparison ? (
        <>
          <ComparisonTable
            comparison={data.comparison}
            caption="Evaluation figures across model versions"
          />
          {data.comparison.selected_algorithm === null ? (
            <p className="border-t border-line px-5 py-3 text-[11px] leading-relaxed text-muted">
              Nothing is selected here. The rule that picks a candidate picks among
              algorithms fitted to one dataset; applying it across versions would
              rank figures from different held-out sets under a criterion written
              for a single one.
            </p>
          ) : null}
        </>
      ) : (
        <ul className="list-disc space-y-1 px-8 py-4 text-[13px] leading-relaxed text-muted">
          {data.not_comparable.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/**
 * The list of models, the reports, and the cross-version view, as one object.
 *
 * Grouped and tagged with the request that produced it, so a response from a
 * superseded request cannot overwrite a newer one, and so a reload shows a
 * skeleton again rather than the previous page's figures dressed as current.
 */
interface ListState {
  key: string;
  list: EvaluationListResponse | null;
  versions: CompareVersionsResponse | null;
  error: ApiError | null;
}

/** The selected report, tagged the same way. */
interface DetailState {
  key: string;
  detail: EvaluationDetail | null;
  error: ApiError | null;
}

/**
 * Module 09: score a registered classifier and show what the score means.
 *
 * The order on screen follows the order a reader needs it. First the list of
 * models, because a model that has not been scored cannot be discussed. Then the
 * report, then the comparison across the candidates it was chosen from, and only
 * then the raw figures.
 *
 * **No figure is ever filled in.** If the backend is unreachable, the tiles are
 * absent and say so, because the one thing this view must never do is show a
 * plausible score for a model that has not been evaluated. The same rule holds for
 * an individual metric within a report that did load: an absent one stays a dash
 * in the tile, a dash in the table, and a ring rather than a bar in the chart.
 */
export default function EvaluationPanel() {
  const [reloadToken, setReloadToken] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [listState, setListState] = useState<ListState | null>(null);
  const [detailState, setDetailState] = useState<DetailState | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<ApiError | null>(null);

  // Loading is derived from the key rather than tracked by a second boolean that
  // could disagree with the data it describes: a response tagged with a key the
  // panel is no longer waiting for is not the current answer, whatever it says.
  const listKey = `list|${reloadToken}`;
  const detailKey = `${selectedId ?? "none"}|${reloadToken}`;

  const loadList = useCallback(async () => {
    try {
      const [listed, compared] = await Promise.all([
        listEvaluations(),
        compareVersions(),
      ]);
      setListState({ key: listKey, list: listed, versions: compared, error: null });
      // Open on a report rather than on a picker: when something is stored, the
      // newest figure is what a reader came for. Only when nothing is stored does
      // the empty state -- which says there is no score -- stand in for one.
      setSelectedId((current) => {
        if (current && listed.evaluable_models.some((m) => m.model_id === current)) {
          return current;
        }
        return listed.evaluations[0]?.model_id ?? null;
      });
    } catch (caught) {
      setListState({
        key: listKey,
        list: null,
        versions: null,
        error: asApiError(caught, "Unexpected error loading evaluations."),
      });
    }
  }, [listKey]);

  const loadDetail = useCallback(async () => {
    if (!selectedId) return;
    try {
      setDetailState({
        key: detailKey,
        detail: await getEvaluation(selectedId),
        error: null,
      });
    } catch (caught) {
      setDetailState({
        key: detailKey,
        detail: null,
        error: asApiError(caught, "Unexpected error loading the report."),
      });
    }
  }, [detailKey, selectedId]);

  // The `cancelled` guard drops a response that arrives after the request it belongs
  // to was superseded, so a slow first response cannot overwrite a fast second one.
  // The key check in the render does the same job for state already committed; this
  // one stops the write from happening at all.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await loadList();
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [loadList]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await loadDetail();
      if (cancelled) return;
    })();
    return () => {
      cancelled = true;
    };
  }, [loadDetail]);

  const list = listState?.key === listKey ? listState.list : null;
  const versions = listState?.key === listKey ? listState.versions : null;
  const listError = listState?.key === listKey ? listState.error : null;
  const detail = detailState?.key === detailKey ? detailState.detail : null;
  const detailError = detailState?.key === detailKey ? detailState.error : null;
  const detailLoading = selectedId !== null && detailState?.key !== detailKey;

  const onRun = useCallback(
    async (modelId: string) => {
      setRunning(true);
      setRunError(null);
      try {
        const result = await runEvaluation(modelId);
        setSelectedId(modelId);
        setDetailState({ key: detailKey, detail: result, error: null });
        // The list and the cross-version view both changed, so both are refetched
        // rather than patched locally. A locally patched count would be a claim
        // about what the backend stores, made by the client.
        await loadList();
      } catch (caught) {
        setRunError(asApiError(caught, "The evaluation could not be run."));
      } finally {
        setRunning(false);
      }
    },
    [detailKey, loadList],
  );

  const models = useMemo(() => list?.evaluable_models ?? [], [list]);
  const selectedModel: EvaluableModel | null = useMemo(
    () => models.find((model) => model.model_id === selectedId) ?? null,
    [models, selectedId],
  );

  if (listError) {
    return (
      <Alert
        tone={listError.isConnectivity ? "warning" : "danger"}
        title={
          listError.isConnectivity
            ? "Evaluations are unavailable"
            : "Evaluations could not be loaded"
        }
        action={
          <Button variant="secondary" onClick={() => setReloadToken((t) => t + 1)}>
            Try again
          </Button>
        }
      >
        <p>{listError.message}</p>
        {listError.isConnectivity ? (
          <p className="mt-1 text-xs">
            Scores are computed from the stored records by the backend. Without a
            reachable backend no score exists, and none is shown here in its place.
          </p>
        ) : null}
      </Alert>
    );
  }

  // A skeleton until the list arrives. It is not a set of zeroes, and it is not a
  // set of zeroes wearing a grey box: nothing numeric is rendered while the answer
  // is unknown, so there is no figure on screen to misread.
  if (list === null) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader
          title="Models available to evaluate"
          description="Registered classifiers, including those never scored. A model that exists is not evidence that it has been evaluated."
          actions={
            <Button variant="secondary" onClick={() => setReloadToken((t) => t + 1)}>
              Refresh
            </Button>
          }
        />
        {models.length === 0 ? (
          <EmptyState
            title="No trained models yet"
            description="Module 09 scores models that were fitted and registered. Train one from a dataset first, and it will appear here to be scored."
          />
        ) : (
          <ul className="divide-y divide-line">
            {models.map((model) => {
              const isSelected = model.model_id === selectedId;
              return (
                <li
                  key={`${model.model_id}-${model.version}`}
                  className={`flex flex-wrap items-center justify-between gap-3 px-5 py-3 ${
                    isSelected ? "bg-accent-soft/40" : ""
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => setSelectedId(model.model_id)}
                    aria-current={isSelected ? "true" : undefined}
                    className="min-w-0 flex-1 text-left"
                  >
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-[13px] font-medium text-ink">
                        {formatAlgorithm(model.algorithm)}
                      </span>
                      <span className="font-mono text-[11px] text-muted">
                        {model.version}
                      </span>
                      {model.evaluated ? (
                        <Badge tone="active">Evaluated</Badge>
                      ) : (
                        <Badge tone="inactive">Not evaluated</Badge>
                      )}
                    </span>
                    <span className="mt-0.5 block truncate text-[11px] text-muted">
                      {model.target} · trained{" "}
                      {formatTimestamp(model.trained_at)}
                    </span>
                  </button>
                  <Button
                    variant={model.evaluated ? "secondary" : "primary"}
                    disabled={running}
                    onClick={() => void onRun(model.model_id)}
                  >
                    {running
                      ? "Running…"
                      : model.evaluated
                        ? "Re-run"
                        : "Evaluate"}
                  </Button>
                </li>
              );
            })}
          </ul>
        )}
        {runError ? (
          <div className="px-5 pb-4">
            <Alert
              tone="danger"
              title={
                runError.status === 409
                  ? "This model cannot be evaluated as it stands"
                  : "The evaluation was refused"
              }
            >
              <p>{runError.message}</p>
              {refusalGuidance(runError) ? (
                <p className="mt-1 text-xs">{refusalGuidance(runError)}</p>
              ) : null}
              {runError.isConnectivity ? (
                <p className="mt-1 text-xs">
                  Nothing was written. A score is never shown for a run that did not
                  complete.
                </p>
              ) : null}
            </Alert>
          </div>
        ) : null}
      </Card>

      {detailError ? (
        <Alert
          tone={detailError.isConnectivity ? "warning" : "info"}
          title={
            detailError.code === "not_evaluable"
              ? "This model has not been evaluated"
              : "The report could not be loaded"
          }
        >
          <p>{detailError.message}</p>
          {refusalGuidance(detailError) ? (
            <p className="mt-1 text-xs">{refusalGuidance(detailError)}</p>
          ) : null}
        </Alert>
      ) : detail ? (
        <ReportDetail detail={detail} />
      ) : detailLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-72 w-full" />
        </div>
      ) : selectedModel ? (
        <Card>
          <EmptyState
            title="Nothing scored yet"
            description={`${formatAlgorithm(selectedModel.algorithm)} ${selectedModel.version} is registered but has no stored report, so there is no figure to show. Evaluating it scores the rows it was held out on and stores the result.`}
            action={
              <Button
                variant="primary"
                disabled={running}
                onClick={() => void onRun(selectedModel.model_id)}
              >
                {running ? "Running…" : "Evaluate this model"}
              </Button>
            }
          />
        </Card>
      ) : null}

      {versions ? <VersionsPanel data={versions} /> : null}
    </div>
  );
}
