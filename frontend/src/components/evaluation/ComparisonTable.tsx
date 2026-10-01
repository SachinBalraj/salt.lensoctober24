"use client";

import { Badge } from "@/components/ui/Primitives";
import {
  COMPARISON_COLUMN_TITLES,
  columnTitle,
  columnCoverage,
  comparabilityCaveats,
  describeDenominator,
  formatAlgorithm,
  formatReading,
  formatReadingPercent,
  MISSING_DISPLAY,
  readingTone,
  type ComparisonColumn,
  type ComparisonResponse,
  type ComparisonRow,
  type MetricReading,
} from "@/lib/evaluation";

/**
 * One cell of the comparison table.
 *
 * This is the component the whole module's honesty claim rests on, so it takes a
 * `MetricReading` rather than a number and has no way to receive one. There is no
 * prop through which a caller could hand it a float, which is the point: a cell
 * that could be handed a float would eventually be handed a `0` for a metric that
 * was never computed, and the table would be quoting a score the model never earned.
 *
 * The three states are visually distinct on purpose:
 *
 * - a measured figure is ink, at full strength;
 * - a figure that rests on a declared convention is ink with a marker, because the
 *   number is real and its meaning is narrower than the number alone suggests;
 * - an absent figure is a faint dash with the reason on hover and to a screen
 *   reader. It is the quietest thing in the cell, because a table where refusals
 *   shout is a table where readers stop seeing the refusals.
 */
function ReadingCell({
  reading,
  column,
  comparison,
  percent,
}: {
  reading: MetricReading | undefined;
  column: ComparisonColumn;
  comparison: ComparisonResponse;
  percent?: boolean;
}) {
  const tone = readingTone(reading);
  const title = columnTitle(comparison, column);
  const label = percent
    ? formatReadingPercent(reading)
    : formatReading(reading);

  // The reason is what makes a dash interpretable, so it is always available:
  // as a tooltip for a mouse, and as the accessible name for anyone not using one.
  const reason =
    reading && !reading.computed
      ? (reading.reason ?? "Not calculated, and the backend attached no reason.")
      : reading?.reason ?? null;

  return (
    <td
      className="px-3 py-2.5 text-right align-middle"
      title={reason ? `${title}: ${reason}` : `${title}: ${label}`}
    >
      <span
        className={
          tone === "absent"
            ? "tabular text-faint"
            : tone === "convention"
              ? "tabular text-ink-soft"
              : "tabular text-ink"
        }
      >
        {label}
      </span>
      {tone === "convention" ? (
        <span
          className="ml-1 align-middle text-[10px] font-semibold uppercase tracking-wide text-caution"
          aria-hidden
        >
          ‡
        </span>
      ) : null}
      {reason ? <span className="sr-only">. {reason}</span> : null}
    </td>
  );
}

/** The model name cell, carrying the selection flag and the eligibility note. */
function ModelCell({ row }: { row: ComparisonRow }) {
  return (
    <th scope="row" className="px-3 py-2.5 text-left align-middle font-normal">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-medium text-ink">
          {formatAlgorithm(row.algorithm)}
        </span>
        {row.selected ? <Badge tone="active">Selected</Badge> : null}
        {!row.auto_selectable ? (
          // The badge rather than a footnote, because a candidate shown beside the
          // others with nothing marking it looks like it is in the running.
          <Badge tone="inactive" className="normal-case tracking-normal">
            Not auto-selectable
          </Badge>
        ) : null}
      </div>
      {!row.auto_selectable && row.selection_note ? (
        <p className="mt-0.5 max-w-sm text-[11px] leading-snug text-muted">
          {row.selection_note}
        </p>
      ) : null}
    </th>
  );
}

/**
 * The six-column comparison table.
 *
 * Two rules govern what is on screen, and both are the backend's to decide:
 *
 * **The selected row is a boolean plus a stated rule, not a label.** The word "best"
 * is not used, because a superlative is a claim a reader cannot check against the
 * figures beside it, whereas a named rule can be checked and disagreed with. The
 * rule is printed under the table rather than in a tooltip, so it is visible to
 * everyone and not only to a reader who knows to look.
 *
 * **A column can be partly absent, and says how partly.** The header carries a
 * "n of m" count, because a table of six columns in which two are blank is a table
 * whose remaining four have lost their context -- a reader cannot tell that PR-AUC
 * is only comparable with itself when it was computed the same way, and four tidy
 * numbers invite exactly that assumption.
 */
export default function ComparisonTable({
  comparison,
  caption,
}: {
  comparison: ComparisonResponse;
  caption?: string;
}) {
  const columns = comparison.columns.length > 0
    ? comparison.columns
    : (Object.keys(COMPARISON_COLUMN_TITLES) as string[]);
  const caveats = comparabilityCaveats(comparison);
  const conventions = comparison.conventions_in_force ?? [];

  if (comparison.rows.length === 0) {
    return (
      <p className="px-5 py-6 text-[13px] text-muted">
        No rows to compare. Nothing is inferred from a model merely existing.
      </p>
    );
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[46rem] border-collapse text-[13px]">
          {caption ? <caption className="sr-only">{caption}</caption> : null}
          <thead>
            <tr className="border-b border-line">
              <th
                scope="col"
                className="px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-faint"
              >
                Model
              </th>
              {columns.map((column) => {
                const coverage = columnCoverage(comparison, column);
                const partial = coverage.present < coverage.total;
                return (
                  <th
                    key={column}
                    scope="col"
                    className="px-3 py-2.5 text-right align-bottom"
                    title={
                      partial
                        ? `${coverage.present} of ${coverage.total} rows have a figure for this column. The rest did not calculate it.`
                        : undefined
                    }
                  >
                    <span className="block text-[11px] font-semibold uppercase tracking-wide text-faint">
                      {columnTitle(comparison, column)}
                    </span>
                    {partial ? (
                      <span className="mt-0.5 block text-[10px] font-medium normal-case tracking-normal text-caution">
                        {coverage.present} of {coverage.total} computed
                      </span>
                    ) : null}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {comparison.rows.map((row) => (
              <tr
                key={`${row.model}-${row.version ?? ""}`}
                className={`border-b border-line last:border-0 ${
                  row.selected ? "bg-accent-soft/40" : ""
                }`}
              >
                <ModelCell row={row} />
                {columns.map((column) => (
                  <ReadingCell
                    key={column}
                    column={column as ComparisonColumn}
                    comparison={comparison}
                    reading={row.metrics[column as ComparisonColumn]}
                  />
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* The denominator, the rule, and the conventions. Printed rather than
          tooltipped: a figure whose denominator is hidden behind a hover is a
          figure that will be quoted without it. */}
      <div className="space-y-2 border-t border-line px-5 py-4 text-[11px] leading-relaxed text-muted">
        <p>
          <span className="font-semibold text-ink-soft">Rows:</span>{" "}
          {describeDenominator(
            comparison.rows[0]?.evaluated_rows ?? 0,
            comparison.rows[0]?.evaluation_set ?? "",
          )}
          {comparison.rows.every(
            (row) => row.evaluated_rows === comparison.rows[0]?.evaluated_rows,
          )
            ? " — every row above was scored on these same rows."
            : " — the rows above were NOT all scored on the same rows, so their figures are not on a common denominator."}
        </p>

        <p>
          <span className="font-semibold text-ink-soft">Selected by:</span>{" "}
          {comparison.selection_criterion}
        </p>

        {comparison.average_precision_note ? (
          <p>{comparison.average_precision_note}</p>
        ) : null}

        {comparison.cross_validation_note ? (
          <p>{comparison.cross_validation_note}</p>
        ) : null}

        {conventions.length > 0 ? (
          <ul className="list-disc space-y-1 pl-4">
            {conventions.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : null}

        {caveats.length > 0 ? (
          <ul className="list-disc space-y-1 pl-4 text-caution">
            {caveats.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        ) : (
          <p>No caveats: the rows share a row set and a row count.</p>
        )}

        <p className="text-faint">
          {MISSING_DISPLAY} means the metric was not calculated, and the reason is in
          the cell&apos;s tooltip. It never means zero. ‡ marks a figure that came
          out of a declared convention rather than a measured rate.
        </p>

        <p className="text-faint">{comparison.does_not}</p>
      </div>
    </div>
  );
}
