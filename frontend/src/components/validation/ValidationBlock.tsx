"use client";

import { useCallback, useEffect, useState } from "react";
import { ButtonLink } from "@/components/ui/Controls";
import { Alert } from "@/components/ui/Feedback";
import { Card, CardHeader, StatTile } from "@/components/ui/Primitives";
import {
  blockSampleCounts,
  describeExcluded,
  describeRiskDefinition,
  describeSample,
  formatComparableReading,
  formatRate,
  type ClassificationComparison,
  type ComparisonBlock,
  type ComparisonKey,
  type ContinuousComparison,
  type MetricReading,
  type ValidationPairRow,
  type ValidationPairsResponse,
  type ValidationScope,
} from "@/lib/validation";
import {
  getValidationPairs,
  validationExportUrl,
} from "@/lib/validationApi";

function continuousUnit(block: ContinuousComparison): string {
  return block.unit;
}

/**
 * One metric tile.
 *
 * An absent metric renders as the dash and carries the backend's reason as its
 * title and its hint, so "RMSE" with a dash is never left looking layout-broken
 * and never read as a zero score.
 */
function MetricTile({
  label,
  reading,
  formatted,
  unit,
}: {
  label: string;
  reading: MetricReading | null | undefined;
  formatted: string;
  unit?: string;
}) {
  const absent = !reading?.computed;
  return (
    <StatTile
      label={label}
      value={formatted}
      hint={
        absent && reading?.reason
          ? reading.reason
          : unit
            ? `in ${unit}`
            : undefined
      }
    />
  );
}

function ContinuousBlock({ block }: { block: ContinuousComparison }) {
  const unit = continuousUnit(block);
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <MetricTile
        label={`Mean absolute error (${unit})`}
        reading={block.mean_absolute_error}
        formatted={formatComparableReading(block.mean_absolute_error, unit)}
        unit={unit}
      />
      <MetricTile
        label={`Root mean squared error (${unit})`}
        reading={block.root_mean_squared_error}
        formatted={formatComparableReading(block.root_mean_squared_error, unit)}
        unit={unit}
      />
      <MetricTile
        label={`Mean bias error (${unit})`}
        reading={block.mean_bias_error}
        formatted={formatComparableReading(block.mean_bias_error, unit)}
        unit={unit}
      />
      <MetricTile
        label="R-squared"
        reading={block.r_squared}
        formatted={formatComparableReading(block.r_squared, null)}
      />
    </div>
  );
}

/**
 * A compact table of one class's agreement.
 *
 * The counts travel beside the rates so a precision of zero and a class that was
 * never predicted read as different facts: support is how many events really
 * carried the class, and predicted how many times the model said so.
 */
function PerClassTable({ block }: { block: ClassificationComparison }) {
  if (block.per_class.length === 0) return null;
  return (
    <table className="w-full border-t border-line text-left">
      <thead>
        <tr className="text-[11px] font-semibold uppercase tracking-[0.12em] text-faint">
          <th className="px-5 py-2">Class</th>
          <th className="px-5 py-2 text-right">Support</th>
          <th className="px-5 py-2 text-right">Predicted</th>
          <th className="px-5 py-2 text-right">Precision</th>
          <th className="px-5 py-2 text-right">Recall</th>
          <th className="px-5 py-2 text-right">F1</th>
        </tr>
      </thead>
      <tbody>
        {block.per_class.map((row) => (
          <tr key={row.label} className="border-t border-line/60">
            <td className="px-5 py-2.5">
              <p className="text-[13px] font-medium text-ink">{row.label}</p>
              {row.description ? (
                <p className="text-[11px] text-muted">{row.description}</p>
              ) : null}
            </td>
            <td className="tabular px-5 py-2.5 text-right text-[13px] text-ink">
              {row.support}
            </td>
            <td className="tabular px-5 py-2.5 text-right text-[13px] text-ink">
              {row.predicted}
            </td>
            <td className="tabular px-5 py-2.5 text-right text-[13px] text-ink">
              {formatRate(row.precision)}
            </td>
            <td className="tabular px-5 py-2.5 text-right text-[13px] text-ink">
              {formatRate(row.recall)}
            </td>
            <td className="tabular px-5 py-2.5 text-right text-[13px] text-ink">
              {formatRate(row.f1)}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ConfusionMatrixView({ block }: { block: ClassificationComparison }) {
  const matrix = block.confusion_matrix;
  if (!matrix) return null;
  return (
    <div className="border-t border-line px-5 py-4">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-faint">
        Confusion matrix — rows are what happened, columns are what was predicted
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="border-separate border-spacing-1">
          <thead>
            <tr>
              <th />
              {matrix.labels.map((label) => (
                <th
                  key={label}
                  className="px-2 py-1 text-[11px] font-medium uppercase tracking-wide text-muted"
                >
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.labels.map((actual, rowIndex) => (
              <tr key={actual}>
                <td className="px-2 py-1 text-[11px] font-medium text-muted">
                  {actual}
                </td>
                {matrix.matrix[rowIndex].map((count, colIndex) => {
                  const isDiagonal = rowIndex === colIndex;
                  return (
                    <td
                      key={`${actual}-${matrix.labels[colIndex]}`}
                      className={`tabular rounded px-2 py-1 text-center text-[13px] ${
                        count > 0
                          ? isDiagonal
                            ? "bg-active-soft text-active"
                            : "bg-caution-soft text-caution"
                          : "text-faint"
                      }`}
                    >
                      {count}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {matrix.classes_absent_from_evaluation.length > 0 ? (
        <p className="mt-2 text-[11px] text-muted">
          Zeros for {matrix.classes_absent_from_evaluation.join(", ")} — a class
          no compared event carried, not a score of zero.
        </p>
      ) : null}
    </div>
  );
}

function ClassificationBlock({ block }: { block: ClassificationComparison }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricTile
          label="Accuracy"
          reading={block.accuracy}
          formatted={formatRate(block.accuracy)}
        />
        <MetricTile
          label="Precision (macro)"
          reading={block.precision}
          formatted={formatRate(block.precision)}
        />
        <MetricTile
          label="Recall (macro)"
          reading={block.recall}
          formatted={formatRate(block.recall)}
        />
        <MetricTile
          label="F1 (macro)"
          reading={block.f1}
          formatted={formatRate(block.f1)}
        />
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MetricTile
          label="ROC-AUC"
          reading={block.roc_auc}
          formatted={formatComparableReading(block.roc_auc, null)}
        />
        <MetricTile
          label="PR-AUC"
          reading={block.pr_auc}
          formatted={formatComparableReading(block.pr_auc, null)}
        />
        <StatTile
          label="Predicted distribution"
          value={block.predicted_distribution[block.classes[0]] ?? 0}
          hint={
            block.classes.length
              ? `Counts by ${block.classes.join(", ")}`
              : "No classes on the record yet"
          }
        />
        <StatTile
          label="Actual distribution"
          value={block.actual_distribution[block.classes[0]] ?? 0}
          hint={
            block.classes.length
              ? `Counts by ${block.classes.join(", ")}`
              : "No classes on the record yet"
          }
        />
      </div>
      <PerClassTable block={block} />
      <ConfusionMatrixView block={block} />
    </div>
  );
}

/**
 * The event rows behind one comparison.
 *
 * Lazy-loaded on first expand so the overview stays quick, and only the
 * comparison in question is asked for. Excluded rows appear with their reasons,
 * so the reader can disagree with the aggregation rather than trust it.
 */
function PairsTable({
  comparisonKey,
  panId,
}: {
  comparisonKey: ComparisonKey;
  panId: string | null;
}) {
  const [state, setState] = useState<{
    key: string;
    rows: ValidationPairsResponse | null;
    failed: boolean;
  } | null>(null);
  const requestKey = `${comparisonKey}|${panId ?? "all"}`;
  const rows = state?.key === requestKey ? state.rows : null;
  const loading = state?.key !== requestKey;

  const load = useCallback(async () => {
    try {
      const response = await getValidationPairs(
        comparisonKey,
        panId ? { pan_id: panId } : {},
      );
      setState({ key: requestKey, rows: response, failed: false });
    } catch {
      setState({ key: requestKey, rows: null, failed: true });
    }
  }, [comparisonKey, panId, requestKey]);

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

  if (loading && !rows) {
    return (
      <div className="px-5 py-4">
        <Alert tone="info" title="Loading event rows">
          <p>Fetching this comparison’s event rows…</p>
        </Alert>
      </div>
    );
  }
  if (state?.failed && !rows) {
    return (
      <Alert tone="danger" title="The event rows could not be loaded">
        <p>The backend did not answer for this comparison&apos;s rows.</p>
      </Alert>
    );
  }
  if (!rows) return null;

  return (
    <table className="w-full border-t border-line text-left">
      <thead>
        <tr className="text-[11px] font-semibold uppercase tracking-[0.12em] text-faint">
          <th className="px-5 py-2">Event</th>
          <th className="px-5 py-2 text-right">Predicted</th>
          <th className="px-5 py-2 text-right">Actual</th>
          <th className="px-5 py-2 text-right">Signed error</th>
          <th className="px-5 py-2">Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.items.map((row) => (
          <PairsRow key={row.outcome_id} row={row} />
        ))}
      </tbody>
    </table>
  );
}

function PairsRow({ row }: { row: ValidationPairRow }) {
  const predicted =
    row.predicted_number !== null
      ? row.predicted_number.toFixed(3)
      : row.predicted_class ?? "—";
  const actual =
    row.actual_number !== null
      ? row.actual_number.toFixed(3)
      : row.actual_class ?? "—";
  const error = row.signed_error !== null ? row.signed_error.toFixed(3) : "—";
  return (
    <tr className="border-t border-line/60">
      <td className="px-5 py-2.5">
        <p className="text-[13px] font-medium text-ink">{row.event_id}</p>
        <p className="text-[11px] text-muted">{row.pan_id}</p>
      </td>
      <td className="tabular px-5 py-2.5 text-right text-[13px] text-ink">{predicted}</td>
      <td className="tabular px-5 py-2.5 text-right text-[13px] text-ink">{actual}</td>
      <td className="tabular px-5 py-2.5 text-right text-[13px] text-ink">{error}</td>
      <td className="px-5 py-2.5">
        {row.compared ? (
          <span className="rounded bg-active-soft px-2 py-1 text-[11px] font-medium text-active">
            Compared
          </span>
        ) : (
          <span
            className="rounded bg-caution-soft px-2 py-1 text-[11px] font-medium text-caution"
            title={row.reason ?? undefined}
          >
            {row.reason ?? "Excluded"}
          </span>
        )}
      </td>
    </tr>
  );
}

/**
 * One comparison family: its gated metrics, its sample, and its raw rows.
 *
 * The sample sentence is the block's spine — "Based on 2 of 4 recorded events" —
 * and the excluded summary sits right under the figures so a figure built from
 * part of the set cannot be read as the whole set.
 */
export default function ValidationBlock({
  comparisonKey,
  block,
  scope,
}: {
  comparisonKey: ComparisonKey;
  block: ComparisonBlock;
  scope: ValidationScope;
}) {
  const [open, setOpen] = useState(false);
  const sample = describeSample(block);
  const excluded = describeExcluded(block);
  const counts = blockSampleCounts(block);

  return (
    <Card>
      <CardHeader
        title={block.title}
        description={block.description}
        actions={
          <div className="flex items-center gap-2">
            <ButtonLink
              variant="secondary"
              href={validationExportUrl(comparisonKey, "csv", scope.pan_id ? { pan_id: scope.pan_id } : {})}
            >
              CSV
            </ButtonLink>
            <ButtonLink
              variant="secondary"
              href={validationExportUrl(comparisonKey, "json", scope.pan_id ? { pan_id: scope.pan_id } : {})}
            >
              JSON
            </ButtonLink>
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-5 py-3">
        <p className="text-xs font-medium text-ink-soft">{sample}</p>
        {describeRiskDefinition(block as ClassificationComparison) ? (
          <p className="rounded bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent">
            {describeRiskDefinition(block as ClassificationComparison)}
          </p>
        ) : null}
        <p className="text-[11px] text-muted">
          {counts.compared} compared / {counts.excluded} excluded
        </p>
      </div>

      <div className="space-y-4 px-5 py-4">
        {block.key === "forecast_vs_actual_rainfall" ||
        block.key === "simulated_vs_actual_salinity" ||
        block.key === "forecast_simulated_vs_actual_salinity" ? (
          <ContinuousBlock block={block as ContinuousComparison} />
        ) : (
          <ClassificationBlock block={block as ClassificationComparison} />
        )}

        {excluded ? (
          <Alert tone="info" title="Not everything could be compared">
            <p title={excluded}>{excluded}</p>
            <p className="mt-0.5 text-xs">
              Every excluded event is listed in the rows below with its own
              reason.
            </p>
          </Alert>
        ) : null}

        {"notes" in block && block.notes.length > 0 ? (
          <ul className="space-y-1">
            {block.notes.map((note) => (
              <li key={note} className="text-[11px] leading-relaxed text-muted">
                {note}
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="border-t border-line">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="flex w-full items-center justify-between px-5 py-3 text-left text-[13px] font-medium text-ink-soft transition-colors hover:bg-inset"
          aria-expanded={open}
        >
          <span>Event rows</span>
          <span className="text-[11px] text-muted">{open ? "Hide" : "Show"}</span>
        </button>
        {open ? (
          <PairsTable comparisonKey={comparisonKey} panId={scope.pan_id} />
        ) : null}
      </div>
    </Card>
  );
}