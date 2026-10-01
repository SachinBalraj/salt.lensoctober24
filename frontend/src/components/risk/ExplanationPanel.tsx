"use client";

import { useCallback, useState } from "react";
import { Button, Spinner } from "@/components/ui/Controls";
import { Alert, EmptyState } from "@/components/ui/Feedback";
import { Badge, Card, CardHeader, DataPoint, Eyebrow } from "@/components/ui/Primitives";
import { ApiError, canRetry } from "@/lib/api";
import {
  describeAdditivity,
  describeMethod,
  formatContribution,
  formatFeatureValue,
  unattributedFeatures,
  type FeatureAttribution,
  type LocalExplanation,
} from "@/lib/explanation";
import {
  explanationUnavailableMessage,
  explainPrediction,
} from "@/lib/explanationApi";
import { formatDateTime } from "@/lib/pans";
import { formatProbability } from "@/lib/riskPrediction";

interface ExplanationState {
  explanation: LocalExplanation | null;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(fallback, { status: -1 });
}

/**
 * Module 11 on the pan detail page: why the model gave this pan the class it did.
 *
 * A prediction arrives here already explained -- every operational prediction is
 * attributed as it is made, and the response carries the explanation's id -- so this
 * panel's job is to show the attribution that already exists, and to say plainly
 * when there is none.
 *
 * **A refusal is shown as a refusal, with its reason.** A model that reports no
 * probability, an artifact that is not on this host, a stored snapshot that no
 * longer matches its hash: these are facts about the deployment, and the panel
 * prints the server's own wording rather than substituting a plausible looking
 * ranking. There is no state in which this panel shows a list of features ordered
 * by influence that it did not receive as SHAP values.
 *
 * **A contribution is never rendered as a probability.** The lists are headed in
 * probability points and each value carries its sign, because "+0.25 pp" and "25%"
 * are different claims and an operator reading both would reasonably act on the
 * wrong one.
 *
 * **An imputed column shows no value.** The preprocessor put the training median
 * there; that is a statistic of the training rows, not a reading of this pan, and
 * the panel says "imputed" in the value's place rather than printing the number
 * where it would look like a measurement.
 *
 * The full attribution table is shown beneath the two factor lists. Both lists are
 * capped, and a feature missing from a capped list would otherwise be
 * indistinguishable from a feature that contributed nothing.
 */
export default function ExplanationPanel({
  predictionId,
  explanationId,
  status,
  refusalDetail,
  isDemo,
}: {
  predictionId: string;
  /** Set when the prediction was explained as it was made. */
  explanationId: string | null;
  status: "available" | "unavailable";
  refusalDetail: string | null;
  isDemo?: boolean;
}) {
  const [state, setState] = useState<ExplanationState | null>(null);
  const [asking, setAsking] = useState(false);

  const ask = useCallback(() => {
    setAsking(true);
    void (async () => {
      try {
        setState({ explanation: await explainPrediction(predictionId), error: null });
      } catch (caught) {
        setState({
          explanation: null,
          error: asApiError(caught, "Unexpected error requesting an explanation."),
        });
      } finally {
        setAsking(false);
      }
    })();
  }, [predictionId]);

  if (isDemo) {
    return (
      <Card>
        <CardHeader
          title="Why this class"
          description="SHAP attributions of the model's probability to the features it read."
        />
        <div className="px-5 py-4">
          <Alert tone="demo" title="No explanation in demo mode">
            A demo pan has no prediction, so there is no probability to attribute
            and no model to attribute it with.
          </Alert>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Why this class"
        description="SHAP attributions of the model's probability to the features it read."
        actions={
          <Button type="button" onClick={ask} disabled={asking} data-testid="explain-ask">
            {asking ? "Computing…" : explanationId ? "Show explanation" : "Explain"}
          </Button>
        }
      />

      <div className="px-5 py-4">
        {asking ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Spinner /> Computing SHAP values from the registered model…
          </div>
        ) : state === null && status === "unavailable" && refusalDetail ? (
          // The reason is already known -- it came with the prediction -- so it is
          // shown rather than left behind a button. A reader is told what is
          // missing instead of being asked to go and find out.
          <Alert
            tone="warning"
            title="No explanation available"
            action={
              <Button type="button" onClick={ask} variant="secondary">
                Ask again
              </Button>
            }
          >
            <span>{refusalDetail}</span>
          </Alert>
        ) : state === null ? (
          <EmptyState
            title="No explanation shown"
            description={
              explanationId
                ? "This prediction was explained when it was made. Press the button to read the stored attributions."
                : "This prediction could not be explained as it was made. Press the button to see why, or to try again."
            }
          />
        ) : state.error ? (
          <Alert
            tone="warning"
            title="No explanation available"
            action={
              canRetry(state.error) ? (
                <Button type="button" onClick={ask} variant="secondary">
                  Try again
                </Button>
              ) : undefined
            }
          >
            <span title={state.error.message}>
              {explanationUnavailableMessage(state.error)}
            </span>
            {refusalDetail ? (
              <p className="mt-1 text-[12px] text-muted">{refusalDetail}</p>
            ) : null}
          </Alert>
        ) : state.explanation ? (
          <ExplanationBody explanation={state.explanation} />
        ) : null}
      </div>
    </Card>
  );
}

function ExplanationBody({ explanation }: { explanation: LocalExplanation }) {
  const rest = unattributedFeatures(explanation);
  const simulation = explanation.quantities.physical_simulation.results;
  const simulatedFeatures = Object.keys(simulation);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Eyebrow>Model probability</Eyebrow>
          <p className="mt-1 text-2xl font-semibold text-ink">
            {formatProbability(explanation.quantities.model_probability.value ?? null)}
          </p>
          <p className="mt-0.5 text-[12px] text-muted">
            for &ldquo;{explanation.target_class}&rdquo;.{" "}
            {explanation.quantities.model_probability.note}
          </p>
        </div>
        <Badge tone={explanation.approximate ? "caution" : "active"}>
          {explanation.approximate ? "Estimated" : "Exact"} SHAP
        </Badge>
      </div>

      <FactorList
        title="Pushed the risk up"
        description="Positive contributions, largest first."
        items={explanation.top_increasing}
        emptyText="No feature raised this prediction's probability above the model's expected value."
      />
      <FactorList
        title="Pushed the risk down"
        description="Negative contributions, largest first."
        items={explanation.top_decreasing}
        emptyText="No feature lowered this prediction's probability below the model's expected value."
      />

      {rest.length > 0 ? (
        <div>
          <Eyebrow>Every other feature</Eyebrow>
          <p className="mt-1 text-[12px] text-muted">
            Not in the two lists above. Shown so that a small contribution is
            distinguishable from one that was left out.
          </p>
          <ul className="mt-2 space-y-1">
            {rest.map((item) => (
              <ContributionRow key={item.feature} item={item} muted />
            ))}
          </ul>
        </div>
      ) : null}

      {simulatedFeatures.length > 0 ? (
        <div>
          <Eyebrow>Physical simulation inputs</Eyebrow>
          <p className="mt-1 text-[12px] text-muted">
            {explanation.quantities.physical_simulation.note}
          </p>
          <ul className="mt-2 space-y-1">
            {simulatedFeatures.map((name) => (
              <li
                key={name}
                className="flex items-center justify-between gap-3 text-[13px]"
              >
                <span className="text-ink-soft">{name}</span>
                <span className="tabular text-muted">
                  {simulation[name]} {explanation.quantities.physical_simulation.unit}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <DataPoint
          label="Expected value"
          value={`${(explanation.base_value * 100).toFixed(1)}%`}
        />
        <DataPoint label="Additivity" value={describeAdditivity(explanation)} />
      </div>

      <div className="border-t border-line pt-3">
        <Eyebrow>How these values were produced</Eyebrow>
        <dl className="mt-2 space-y-1 text-[12px] text-muted">
          <div className="flex flex-wrap gap-x-2">
            <dt>Method</dt>
            <dd className="text-ink-soft">{describeMethod(explanation)}</dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt>Reference rows</dt>
            <dd className="text-ink-soft">
              {explanation.background.rows} of {explanation.background.train_rows}{" "}
              training rows, {explanation.background.selection}.
            </dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt>Explained at</dt>
            <dd className="text-ink-soft">{formatDateTime(explanation.created_at)}</dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt>Input row</dt>
            <dd className="tabular break-all text-ink-soft">
              {explanation.input_snapshot_id}
            </dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt>Explanation</dt>
            <dd className="tabular break-all text-ink-soft">
              {explanation.explanation_id}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

/**
 * One factor list, headed with the unit the numbers are in.
 *
 * The unit is in the heading rather than in a tooltip because the sign of a
 * contribution and the size of a probability are read at a glance, and a glance
 * that misses "pp" reads a change as a level.
 */
function FactorList({
  title,
  description,
  items,
  emptyText,
}: {
  title: string;
  description: string;
  items: FeatureAttribution[];
  emptyText: string;
}) {
  return (
    <div>
      <Eyebrow>{title}</Eyebrow>
      <p className="mt-1 text-[12px] text-muted">{description}</p>
      {items.length === 0 ? (
        <p className="mt-2 text-[13px] text-ink-soft">{emptyText}</p>
      ) : (
        <ul className="mt-2 space-y-1.5">
          {items.map((item) => (
            <ContributionRow key={item.feature} item={item} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ContributionRow({
  item,
  muted = false,
}: {
  item: FeatureAttribution;
  muted?: boolean;
}) {
  const value = formatContribution(item.shap_value);
  return (
    <li
      className="flex items-start justify-between gap-3 text-[13px]"
      data-testid={`attribution-${item.feature}`}
    >
      <span className="min-w-0">
        <span className={muted ? "text-ink-soft" : "text-ink"}>{item.feature}</span>
        <span className="ml-2 text-[11px] text-muted">
          {formatFeatureValue(item)}
          {item.source_kind === "physical_simulation" ? " · from mass balance" : null}
        </span>
      </span>
      <span
        className={`tabular whitespace-nowrap ${
          item.shap_value > 0
            ? "text-ink"
            : item.shap_value < 0
              ? "text-ink-soft"
              : "text-muted"
        }`}
      >
        {value}
      </span>
    </li>
  );
}
