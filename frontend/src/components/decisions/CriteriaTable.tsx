"use client";

import { boundIsOperatorConfigured, type DecisionCriterionFired } from "@/lib/decisions";

/**
 * The criteria that fired, as a table of figures rather than a sentence.
 *
 * A bound the operator configured is labelled as such and names the field it was
 * read from, because that figure is a property of this pan and not of the rule.
 * Showing the resolved number without that distinction would let a reviewer's
 * objection go to the wrong place: there is no case for arguing with a number the
 * operator chose here, only with the rule that used it.
 *
 * **Shared by the pan page and the dashboard.** Both are places an operator reads
 * a recommendation, and a figure shown on one but not the other means the two
 * pages disagree about what the recommendation rests on. The dashboard is the
 * page an operator works from, so the thresholds have to be there.
 */
export function CriteriaTable({ criteria }: { criteria: DecisionCriterionFired[] }) {
  return (
    <table
      className="mt-3 w-full border-collapse text-[12px]"
      data-testid="decision-criteria"
    >
      <caption className="sr-only">
        Criteria that fired, with the bound each was compared against
      </caption>
      <thead>
        <tr className="border-b border-line text-left text-faint">
          <th scope="col" className="py-1 pr-2 font-medium">
            Comparison
          </th>
          <th scope="col" className="py-1 pr-2 font-medium">
            Value
          </th>
          <th scope="col" className="py-1 pr-2 font-medium">
            Bound
          </th>
        </tr>
      </thead>
      <tbody>
        {criteria.map((criterion) => {
          const configured = boundIsOperatorConfigured(criterion);
          return (
            <tr
              key={`${criterion.variable}-${criterion.operator}-${criterion.action}`}
              className="border-b border-line/60 align-top"
              data-testid="decision-criterion"
            >
              <td className="py-1 pr-2 font-mono text-muted">
                {criterion.comparator}
                {criterion.rationale ? (
                  <span className="mt-0.5 block font-sans text-faint">
                    {criterion.rationale}
                  </span>
                ) : null}
              </td>
              <td className="py-1 pr-2 tabular text-ink-soft">
                {criterion.value === null ? "\u2014" : String(criterion.value)}
              </td>
              <td className="py-1 pr-2 tabular text-ink-soft">
                {criterion.threshold === null ? "\u2014" : String(criterion.threshold)}
                {configured ? (
                  <span
                    className="mt-0.5 block text-[11px] text-faint"
                    data-testid="decision-criterion-configured"
                  >
                    Your configured limit
                  </span>
                ) : null}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
