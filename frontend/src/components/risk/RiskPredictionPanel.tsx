"use client";

import { useCallback, useState } from "react";
import { Button, ButtonLink, Spinner } from "@/components/ui/Controls";
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
  GATED_BY_DEPLOYMENT_MESSAGE,
  reportsNoProbability,
  trainingProgress,
  trainingRequirement,
  unavailableMessage,
  type RiskPrediction,
  type TrainingGate,
} from "@/lib/riskPrediction";
import {
  missingInputs,
  predictRisk,
  predictionState,
  predictionUnavailableMessage,
  trainingGateOf,
} from "@/lib/riskPredictionApi";
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
 * **With no answer it says which state the system is in**, and offers no number.
 * The operator is not shown an estimate from a previous deployment, a rule
 * applied in the browser, or a placeholder, because a risk figure that did not
 * come from a deployed model is the one thing this module exists not to produce.
 * Six states, six sentences: nothing trained, a model that was never evaluated,
 * an evaluated model nobody deployed, a deployed model whose inputs the twin
 * could not supply, and a failure this client does not model. Each sentence is
 * the same on every surface, so an operator who sees one here and in a support
 * thread is looking at the same fact — and each names its own remedy, because
 * "train it", "evaluate it" and "deploy it" belong to three different modules
 * and a panel that said only "no model" would leave the operator guessing which
 * one.
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
 * The refused case: one sentence for the state, and the step that unblocks it.
 *
 * With no deployed model this is one fixed sentence and nothing that could be
 * read as a risk figure: no number, no hint of what the model would have said,
 * and no suggestion that the application can be made to answer. The full
 * explanation the server returns is kept in the `title` attribute for a
 * developer, where it is read rather than acted on.
 *
 * The paragraph underneath changes with the state, because the states have
 * different remedies and different owners. A project that has never trained
 * anything is told what the gate requires, with the gate's own counts beside
 * it; a model waiting on an evaluation is pointed at Module 09; an evaluated
 * model waiting on a deployment is pointed at Module 17; a deployed model with
 * unreadable inputs is told which fields arrived empty. One paragraph for all
 * four would be shorter and would leave the operator to work out which module
 * they are missing.
 */
function Unavailable({
  error,
  onRetry,
}: {
  error: ApiError;
  onRetry: () => void;
}) {
  const state = predictionState(error);
  const message =
    state === "failed"
      ? predictionUnavailableMessage(error)
      : unavailableMessage(state, error.message, missingInputs(error));
  // A refusal about the state of the project rather than a failure: the missing
  // thing is a decision somebody has to make, so re-asking cannot change the
  // answer and no retry is offered. A `failed` state is the exception — there,
  // the request may simply not have been decided yet.
  const refusal = state !== "failed";

  return (
    <Alert
      tone={refusal ? "warning" : "danger"}
      title={refusal ? undefined : "Risk prediction failed"}
      action={
        refusal || !canRetry(error) ? undefined : (
          <Button type="button" onClick={onRetry} variant="secondary">
            Try again
          </Button>
        )
      }
    >
      <span title={error.message}>{message}</span>
      {state === "no_model" ? (
        <NoModelExplanation gate={trainingGateOf(error)} />
      ) : null}
      {state === "not_evaluated" ? (
        <div className="mt-1 space-y-1 text-[12px] text-muted">
          <p>
            A model has been trained, but a version with no evaluation report is
            not eligible for deployment — nothing has graded it yet.
          </p>
          <p>
            Run <code className="font-mono">POST /api/evaluation/run</code> for
            it in Module 09, validate the report, then deploy it through Module
            17.
          </p>
          <ButtonLink href="/evaluation" variant="secondary">
            Open evaluation
          </ButtonLink>
        </div>
      ) : null}
      {state === "not_deployed" ? (
        <p className="mt-1 text-[12px] text-muted">
          A trained and evaluated model exists, but nobody has put it in
          service. Deploying is a deliberate act in Module 17
          (<code className="font-mono">POST /api/models/&#123;model_id&#125;/deploy</code>
          ), and until it happens this endpoint will not answer for it.
        </p>
      ) : null}
      {state === "missing_inputs" ? (
        <p className="mt-1 text-[12px] text-muted">
          The model is deployed and answered; these are the quantities its row
          could not read from this pan&apos;s current twin state. Record the
          readings the twin needs, refresh the twin, and ask again — no value is
          guessed in their place.
        </p>
      ) : null}
    </Alert>
  );
}

/**
 * Why there is no figure, and what the project needs before there is one.
 *
 * Three paragraphs, each stating a fact the operator cannot get anywhere else:
 * that a model reaches this endpoint only by being deployed; that a validated
 * model has not been deployed; and what the gate requires next, with the gate's
 * own counts. The requirement is built from the backend's thresholds rather
 * than written here, so the sentence and the rule that enforces it cannot drift
 * apart, and it degrades to the fixed wording when the audit could not be read
 * rather than reporting a number nobody measured.
 */
function NoModelExplanation({ gate }: { gate: TrainingGate | null }) {
  const progress = trainingProgress(gate);
  return (
    <>
      <p className="mt-1 text-[12px] text-muted">
        A model reaches this only by being deployed, after it has been trained
        and evaluated. Until one is, no risk figure is shown — a number from an
        undeployed model would be a guess wearing a measurement&apos;s clothes.
      </p>
      <p className="mt-1 text-[12px] text-muted">{GATED_BY_DEPLOYMENT_MESSAGE}</p>
      <p className="mt-1 text-[12px] text-muted">{trainingRequirement(gate)}</p>
      {progress ? (
        <p className="mt-1 text-[12px] text-muted">{progress}</p>
      ) : null}
    </>
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
