"use client";

import { useCallback, useState } from "react";
import { Button, Spinner } from "@/components/ui/Controls";
import { Alert, EmptyState } from "@/components/ui/Feedback";
import { Badge, Card, CardHeader, Eyebrow } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import {
  describeGlobalScope,
  describeMethod,
  formatContribution,
  type GlobalImportance,
} from "@/lib/explanation";
import {
  explanationUnavailableMessage,
  fetchGlobalImportance,
} from "@/lib/explanationApi";
import { formatTimestamp } from "@/lib/evaluation";

interface ImportanceState {
  importance: GlobalImportance | null;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(fallback, { status: 500 });
}

/**
 * Module 11's global summary, on the evaluation page.
 *
 * A score says how often the model was right. This says which features it leaned
 * on, over the held-out rows the report was computed from -- the same rows, so the
 * two read as one piece of evidence about one model version.
 *
 * **The values are SHAP values, averaged.** Each row is explained on its own and
 * the mean of the absolute contributions is taken, which is not the same number as a
 * coefficient, a correlation, or a permutation importance, and can disagree with all
 * three. The panel says what was averaged and over what, so a reader comparing this
 * against a coefficient elsewhere is warned rather than misled.
 *
 * **It is asked for, not fetched on mount.** Explaining the held-out rows is real
 * work, and a model evaluation page opening should not silently spend it. The
 * button is the act.
 *
 * **A model with no probability has no importance here.** Its estimator emits a
 * class and no number, so there is nothing to attribute; the refusal is shown with
 * the server's own wording rather than answered with a coefficient.
 */
export default function FeatureImportanceCard({
  modelId,
  version,
  isDemo,
}: {
  modelId: string;
  version: string;
  isDemo?: boolean;
}) {
  const [state, setState] = useState<ImportanceState | null>(null);
  const [asking, setAsking] = useState(false);

  const ask = useCallback(
    (rows: number) => {
      setAsking(true);
      void (async () => {
        try {
          setState({
            importance: await fetchGlobalImportance({ modelId, version, rows }),
            error: null,
          });
        } catch (caught) {
          setState({
            importance: null,
            error: asApiError(
              caught,
              "Unexpected error requesting feature importance.",
            ),
          });
        } finally {
          setAsking(false);
        }
      })();
    },
    [modelId, version],
  );

  if (isDemo) {
    return (
      <Card>
        <CardHeader
          title="Which features it leaned on"
          description="Mean absolute SHAP contribution over the held-out rows."
        />
        <div className="px-5 py-4">
          <Alert tone="demo" title="No importance in demo mode">
            Demo figures are illustrative and were not produced by this model
            version, so no importance is computed for them.
          </Alert>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Which features it leaned on"
        description="Mean absolute SHAP contribution over the held-out rows."
        actions={
          <div className="flex items-center gap-2">
            {state?.importance ? (
              <Button
                type="button"
                onClick={() => ask(state.importance!.rows_explained)}
                disabled={asking}
                variant="secondary"
              >
                Recompute
              </Button>
            ) : null}
            <Button
              type="button"
              onClick={() => ask(25)}
              disabled={asking}
              data-testid="importance-ask"
            >
              {asking ? "Computing…" : "Load feature importance"}
            </Button>
          </div>
        }
      />

      <div className="px-5 py-4">
        {asking ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Spinner /> Attributing the held-out rows…
          </div>
        ) : state === null ? (
          <EmptyState
            title="No importance computed"
            description="Explaining the held-out rows is real work, so it happens when you ask for it. The result is stored, so the same model version gives the same ranking."
          />
        ) : state.error ? (
          <Alert
            tone="warning"
            title="No feature importance available"
            action={
              state.error.isConnectivity ? undefined : (
                <Button type="button" onClick={() => ask(25)} variant="secondary">
                  Try again
                </Button>
              )
            }
          >
            <span title={state.error.message}>
              {explanationUnavailableMessage(state.error)}
            </span>
          </Alert>
        ) : state.importance ? (
          <ImportanceBody importance={state.importance} />
        ) : null}
      </div>
    </Card>
  );
}

function ImportanceBody({ importance }: { importance: GlobalImportance }) {
  const classes = Object.entries(importance.explained_classes);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Eyebrow>{importance.measure.label}</Eyebrow>
          <p className="mt-1 text-[12px] text-muted">
            {importance.measure.note}
          </p>
        </div>
        <Badge tone={importance.approximate ? "caution" : "active"}>
          {importance.approximate ? "Estimated" : "Exact"} SHAP
        </Badge>
      </div>

      <table className="w-full text-[13px]">
        <caption className="sr-only">
          Mean absolute SHAP contribution per feature, in probability points
        </caption>
        <thead>
          <tr className="border-b border-line text-left text-[11px] uppercase tracking-wide text-muted">
            <th scope="col" className="py-1.5 pr-2 font-medium">
              Feature
            </th>
            <th scope="col" className="py-1.5 pr-2 text-right font-medium">
              Mean |SHAP|
            </th>
            <th scope="col" className="py-1.5 text-right font-medium">
              Rank
            </th>
          </tr>
        </thead>
        <tbody>
          {importance.features.map((feature) => (
            <tr
              key={feature.feature}
              className="border-b border-line/60 last:border-0"
              data-testid={`importance-${feature.feature}`}
            >
              <td className="py-1.5 pr-2 text-ink">{feature.feature}</td>
              <td className="tabular py-1.5 pr-2 text-right text-ink-soft">
                {formatContribution(feature.mean_abs_shap).replace(/^\+/, "")}
              </td>
              <td className="tabular py-1.5 text-right text-muted">
                {feature.rank}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <dl className="space-y-1 border-t border-line pt-3 text-[12px] text-muted">
        <div className="flex flex-wrap gap-x-2">
          <dt>Scope</dt>
          <dd className="text-ink-soft">{describeGlobalScope(importance)}</dd>
        </div>
        <div className="flex flex-wrap gap-x-2">
          <dt>Predicted classes</dt>
          <dd className="text-ink-soft">
            {classes.map(([name, count]) => `${name} ${count}`).join(", ")}
          </dd>
        </div>
        <div className="flex flex-wrap gap-x-2">
          <dt>Method</dt>
          <dd className="text-ink-soft">{describeMethod(importance)}</dd>
        </div>
        <div className="flex flex-wrap gap-x-2">
          <dt>Computed at</dt>
          <dd className="text-ink-soft">{formatTimestamp(importance.created_at)}</dd>
        </div>
        <div className="flex flex-wrap gap-x-2">
          <dt>Summary</dt>
          <dd className="tabular break-all text-ink-soft">
            {importance.explanation_id}
          </dd>
        </div>
      </dl>
    </div>
  );
}
