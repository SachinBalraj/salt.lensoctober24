"use client";

import { useCallback, useState } from "react";
import { Button, Spinner } from "@/components/ui/Controls";
import ExplanationPanel from "@/components/risk/ExplanationPanel";
import { Alert, EmptyState } from "@/components/ui/Feedback";
import { Badge, Card, CardHeader, DataPoint, Eyebrow } from "@/components/ui/Primitives";
import { ApiError, canRetry } from "@/lib/api";
import { DEMO_DATA_NOTICE } from "@/lib/demoPans";
import {
  classProbabilityRows,
  describeFeatureCoverage,
  formatModel,
  formatProbability,
  NO_DEPLOYED_MODEL_MESSAGE,
  reportsNoProbability,
  type RiskPrediction,
} from "@/lib/riskPrediction";
import { predictRisk, predictionUnavailableMessage } from "@/lib/riskPredictionApi";
import { formatDateTime } from "@/lib/pans";
import type { SaltPan } from "@/lib/pans";

interface LoadedState {
  prediction: RiskPrediction | null;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(fallback, { status: -1 });
}

/**
 * Module 10 on the pan detail page: the deployed model's risk class for this pan,
 * read from the pan's current twin state.
 *
 * The panel is built to be honest under three conditions rather than to look
 * complete.
 *
 * **With no deployed model it says one fixed sentence** and offers no number. The
 * operator is not shown an estimate from a previous deployment, a rule applied in
 * the browser, or a placeholder, because a risk figure that did not come from a
 * deployed model is the one thing this module exists not to produce. The message
 * is the same sentence on every surface, so an operator who sees it here and in a
 * support thread is looking at the same fact.
 *
 * **With no probability it shows the class and the absence.** A model that emits a
 * class without a number has still answered; the panel renders what it said and
 * states that the model reported no probability, rather than rendering a zero.
 *
 * **It shows the model and the input state with the answer.** The class, the
 * model version that produced it, the instant it was predicted, and the content
 * hash of the twin state it read. A risk figure without those is a number with
 * nothing behind it, and the panel is the place an operator can see they are
 * there.
 *
 * A prediction is asked for, not computed on render: each request is one stored
 * prediction, and opening this page should not quietly write a history. The
 * reload button is the explicit act.
 */
export default function RiskPredictionPanel({
  pan,
  isDemo,
}: {
  pan: SaltPan;
  isDemo?: boolean;
}) {
  const panId = pan.pan_id;
  const [state, setState] = useState<LoadedState | null>(null);
  const [asking, setAsking] = useState(false);

  const ask = useCallback(() => {
    setAsking(true);
    setState(null);
    void (async () => {
      try {
        const prediction = await predictRisk({ panId });
        setState({ prediction, error: null });
      } catch (caught) {
        setState({
          prediction: null,
          error: asApiError(caught, "Unexpected error requesting a risk prediction."),
        });
      } finally {
        setAsking(false);
      }
    })();
  }, [panId]);

  if (isDemo) {
    return (
      <Card>
        <CardHeader
          title="Operational risk"
          description="Predicted by the deployed model, from this pan's current twin state."
        />
        <div className="px-5 py-4">
          <Alert tone="demo" title="No prediction in demo mode">
            {DEMO_DATA_NOTICE} A demo pan has no twin state and no deployed model,
            so there is nothing to predict from and nothing to score it with.
          </Alert>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Operational risk"
        description="Predicted by the deployed model, from this pan's current twin state."
        actions={
          <Button
            type="button"
            onClick={ask}
            disabled={asking}
            data-testid="risk-predict"
          >
            {asking ? "Asking the model…" : "Get risk prediction"}
          </Button>
        }
      />

      <div className="px-5 py-4">
        {asking ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Spinner /> Asking the deployed model…
          </div>
        ) : state === null ? (
          <EmptyState
            title="No prediction requested"
            description="A prediction is made on request, from the pan's current twin state, and stored so it can be checked against what happened later."
          />
        ) : state.error ? (
          <Unavailable error={state.error} onRetry={ask} />
        ) : state.prediction ? (
          <PredictionBody prediction={state.prediction} />
        ) : null}
      </div>
    </Card>
  );
}

/**
 * The refused case.
 *
 * With no deployed model this is one fixed sentence and nothing else: no number,
 * no hint of what the model would have said, and no suggestion that the
 * application can be made to answer. The full explanation the server returns is
 * kept in the `title` attribute for a developer, where it is read rather than
 * acted on.
 */
function Unavailable({
  error,
  onRetry,
}: {
  error: ApiError;
  onRetry: () => void;
}) {
  const message = predictionUnavailableMessage(error);
  const noModel = message === NO_DEPLOYED_MODEL_MESSAGE;

  return (
    <Alert
      tone={noModel ? "warning" : "danger"}
      title={noModel ? undefined : "Risk prediction failed"}
      action={
        // A retry is offered only where asking again could change the answer. With
        // no deployed model the missing thing is a deployment decision, and a
        // button that re-asks a question whose answer cannot have changed would
        // invite an operator to keep pressing it.
        noModel || !canRetry(error) ? undefined : (
          <Button type="button" onClick={onRetry} variant="secondary">
            Try again
          </Button>
        )
      }
    >
      <span title={error.message}>{message}</span>
      {noModel ? (
        <p className="mt-1 text-[12px] text-muted">
          A model reaches this only by being deployed, after it has been trained
          and evaluated. Until one is, no risk figure is shown — a number from an
          undeployed model would be a guess wearing a measurement&apos;s clothes.
        </p>
      ) : null}
    </Alert>
  );
}

function PredictionBody({ prediction }: { prediction: RiskPrediction }) {
  const noProbability = reportsNoProbability(prediction);
  const rows = classProbabilityRows(prediction);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Eyebrow>Risk class</Eyebrow>
          <p className="mt-1 text-2xl font-semibold capitalize text-ink">
            {prediction.risk_class}
          </p>
          <p className="mt-0.5 text-[12px] text-muted">
            {noProbability
              ? "The model reported no probability for this class."
              : `The model gave this class ${formatProbability(prediction.probability)}.`}
          </p>
        </div>
        <Badge tone="active">{prediction.input_source === "current_digital_twin_state" ? "From the twin" : prediction.input_source}</Badge>
      </div>

      {noProbability ? (
        <Alert tone="info" title="No probability was reported">
          The deployed model&apos;s estimator produces a class but no per-class
          probabilities, so none is shown. Nothing has been substituted for it.
        </Alert>
      ) : null}

      {rows.length > 1 ? (
        <div>
          <Eyebrow>Every class the model could have returned</Eyebrow>
          <ul className="mt-2 space-y-1.5">
            {rows.map((row) => (
              <li
                key={row.className}
                className="flex items-center justify-between gap-3 text-[13px]"
              >
                <span className={row.isPredicted ? "font-semibold text-ink" : "text-ink-soft"}>
                  {row.className}
                </span>
                <span className="tabular text-muted">
                  {formatProbability(row.probability)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <DataPoint label="Model" value={formatModel(prediction)} />
        <DataPoint
          label="Risk rule"
          value={`${prediction.risk_definition_id} v${prediction.risk_definition_version}`}
        />
        <DataPoint label="Predicted at" value={formatDateTime(prediction.predicted_at)} />
        <DataPoint label="Twin state as of" value={formatDateTime(prediction.as_of)} />
      </div>

      <div className="border-t border-line pt-3">
        <Eyebrow>Traceability</Eyebrow>
        <dl className="mt-2 space-y-1 text-[12px] text-muted">
          <div className="flex flex-wrap gap-x-2">
            <dt>Input snapshot</dt>
            <dd className="tabular break-all text-ink-soft">
              {prediction.input_snapshot_id}
            </dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt>Twin</dt>
            <dd className="tabular break-all text-ink-soft">{prediction.twin_id}</dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt>Prediction</dt>
            <dd className="tabular break-all text-ink-soft">
              {prediction.prediction_id}
            </dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt>Features</dt>
            <dd className="text-ink-soft">{describeFeatureCoverage(prediction)}</dd>
          </div>
          {prediction.features_absent.length > 0 ? (
            <div className="flex flex-wrap gap-x-2">
              <dt>Imputed</dt>
              <dd className="text-ink-soft">
                {prediction.features_imputed.join(", ")}
              </dd>
            </div>
          ) : null}
        </dl>
      </div>

      {/* Keyed by prediction so an attribution never survives onto a different
          prediction: they are per-row, and a stale one would attribute this
          probability to the wrong row. */}
      <ExplanationPanel
        key={prediction.prediction_id}
        predictionId={prediction.prediction_id}
        explanationId={prediction.explanation_id}
        status={prediction.explanation_status}
        refusalDetail={prediction.explanation_refusal_detail}
      />
    </div>
  );
}
