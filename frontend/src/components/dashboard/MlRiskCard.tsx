"use client";

import { Button, Spinner } from "@/components/ui/Controls";
import { Alert, EmptyState } from "@/components/ui/Feedback";
import { Badge } from "@/components/ui/Primitives";
import { ProvenanceCard } from "@/components/dashboard/Provenance";
import { ApiError } from "@/lib/api";
import {
  GATED_BY_DEPLOYMENT_MESSAGE,
  NO_DEPLOYED_MODEL_MESSAGE,
  classProbabilityRows,
  formatProbability,
  reportsNoProbability,
  trainingProgress,
  trainingRequirement,
  unavailableMessage,
  type RiskPrediction,
  type RiskUnavailableState,
} from "@/lib/riskPrediction";
import {
  predictionState,
  type RiskModuleStatus,
} from "@/lib/riskPredictionApi";
import { formatDateTime } from "@/lib/pans";

/**
 * The deployed model's estimate for this pan, and only that.
 *
 * **Prediction is its own act, asked for separately from the recommendation.**
 * This card has its own button and its own state, and nothing on this screen runs
 * a classifier because a page was opened. That is not caution for its own sake: a
 * stored prediction is a record with a model version and a timestamp attached, and
 * a dashboard that quietly produced one on every visit would fill the prediction
 * table with figures nobody asked for and that no operator ever saw. It is also
 * what keeps prediction and recommendation visibly apart — the button here does
 * not produce an action, and the button on the recommendation card does not run
 * this model.
 *
 * **A class is shown with the model's own probability, or without one.**
 * A class-only model reports a label and no likelihood, and the gap is stated
 * rather than filled. Rendering the absence as 0% would be the most damaging
 * thing this card could do: it would assert a number the model never produced, in
 * the one position an operator is most likely to act on.
 *
 * **The model is named.** A risk class with no model and version beside it is a
 * number with nothing behind it, so both are on screen for the same reason the
 * class is — so a reader can say which model said it, and from what state.
 *
 * **A stored prediction outranks a claim that no model exists.** The status probe
 * describes the present; a stored prediction is a record of something that
 * happened. If both are available the record is shown, because a dashboard that
 * answers "no model is trained" over the top of a real stored estimate is
 * destroying evidence it was asked to display. The probe only speaks when there
 * is nothing stored to speak over.
 *
 * **An absence is stated as the state it is, not as one generic gap.** Nothing
 * trained, trained-but-never-evaluated, and evaluated-but-never-deployed have
 * three different remedies in three different modules. The card says which one
 * applies, from the status probe before the button is pressed and from the
 * refusal after it, so the two routes to the card cannot disagree.
 */
export function MlRiskCard({
  prediction,
  error,
  loading,
  onPredict,
  hasRun,
  modelStatus,
}: {
  prediction: RiskPrediction | null;
  error: ApiError | null;
  loading: boolean;
  onPredict: () => void;
  hasRun: boolean;
  /**
   * What the backend reports about the model, read on mount. `null` is "this
   * client did not find out" and must not be read as an absence; a `model_state`
   * of `unknown` means the backend could not read its own registry, and is
   * treated the same way.
   */
  modelStatus?: RiskModuleStatus | null;
}) {
  // A known-absent model is stated whether or not the button has been pressed, so
  // an operator does not have to click to discover the button has nothing behind
  // it. The probe and the refusal resolve to one vocabulary, so they cannot
  // disagree about which step is missing.
  const errorState = error ? predictionState(error) : null;
  const probeState =
    modelStatus?.model_state === "no_model" ||
    modelStatus?.model_state === "not_evaluated" ||
    modelStatus?.model_state === "not_deployed"
      ? modelStatus.model_state
      : null;
  // A refusal this client does not model is shown with the server's own prose
  // below rather than being read as a state of the registry: nothing is known
  // about the model from a request that failed for an unknown reason.
  const unavailableState: RiskUnavailableState | null =
    errorState === null
      ? probeState
      : errorState === "failed"
        ? null
        : errorState;
  // Read once, so the requirement and the counts beside it are two views of the
  // same gate rather than two separate reads that could disagree.
  const gate = modelStatus?.training_gate ?? null;
  const requirement = trainingRequirement(gate);
  const progress = trainingProgress(gate);

  return (
    <ProvenanceCard
      kind="ml"
      title="ML risk"
      description="The deployed classifier's stored estimate. It estimates risk; it does not choose an action."
      actions={
        <Button
          type="button"
          onClick={onPredict}
          disabled={loading}
          data-testid="ml-predict"
        >
          {loading ? "Running…" : hasRun ? "Predict again" : "Predict risk"}
        </Button>
      }
    >
      {loading ? (
        <div className="flex items-center gap-2 py-2 text-[13px] text-muted">
          <Spinner /> Scoring the twin state…
        </div>
      ) : unavailableState !== null && !prediction ? (
        <Alert tone="info" title="No model estimate">
          <span title={error?.message}>
            {unavailableMessage(
              unavailableState,
              error?.message ?? NO_DEPLOYED_MODEL_MESSAGE,
            )}
          </span>
          {unavailableState === "no_model" ? (
            <>
              <p className="mt-1 text-[11px]">{GATED_BY_DEPLOYMENT_MESSAGE}</p>
              <p className="mt-1 text-[11px]">{requirement}</p>
              {progress ? (
                <p className="mt-1 text-[11px]">{progress}</p>
              ) : null}
            </>
          ) : null}
          {unavailableState === "not_evaluated" ? (
            <p className="mt-1 text-[11px]">
              A model has been trained, but a version with no evaluation report
              cannot be deployed. Module 09 grades it; Module 17 fields it.
            </p>
          ) : null}
          {unavailableState === "not_deployed" ? (
            <p className="mt-1 text-[11px]">
              A trained and evaluated model exists, but nobody has put it in
              service. Deployment is a deliberate act in Module 17, and until it
              happens this card has no figure to show.
            </p>
          ) : null}
          <p className="mt-1 text-[11px]">
            The recommendation below does not depend on this: the rule can be applied
            from the recorded state and the mass balance alone. Nothing is being
            withheld and there is no figure to retry for — training and deploying a
            model version is a decision somebody has to make.
          </p>
        </Alert>
      ) : error ? (
        <Alert tone="info" title="No model estimate">
          <span title={error.message}>{error.message}</span>
          <p className="mt-1 text-[11px]">
            The recommendation below does not depend on this: the rule can be applied
            from the recorded state and the mass balance alone.
          </p>
        </Alert>
      ) : !prediction ? (
        <EmptyState
          title="No prediction run for this pan"
          description="A risk estimate is produced on request and stored, so it can be compared against what actually happened later. It is not produced by opening this page."
        />
      ) : (
        <RiskBody prediction={prediction} />
      )}
    </ProvenanceCard>
  );
}

function RiskBody({ prediction }: { prediction: RiskPrediction }) {
  const rows = classProbabilityRows(prediction);
  const noProbability = reportsNoProbability(prediction);

  return (
    <div className="space-y-4" data-testid="ml-risk-body">
      <div className="rounded-lg border border-line bg-inset px-4 py-3.5">
        <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-faint">
          Predicted risk class
        </p>
        <p className="mt-1 text-2xl font-semibold capitalize text-ink">
          {prediction.risk_class.replace(/_/g, " ")}
        </p>
        <p className="mt-1 text-[11px] text-muted">
          {noProbability
            ? "The model reported this class with no probability."
            : `Model probability ${formatProbability(prediction.probability)}.`}
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-lg border border-line bg-inset px-3.5 py-3">
          <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-faint">
            Model probability
          </p>
          <p className="tabular mt-1 text-xl font-semibold text-ink">
            {noProbability ? "Not reported" : formatProbability(prediction.probability)}
          </p>
          <p className="mt-0.5 text-[10px] leading-relaxed text-muted">
            {noProbability
              ? "A class-only model produces a label and no likelihood. No number is shown because none was produced."
              : "The model's own figure for the class it chose."}
          </p>
        </div>
        <div className="rounded-lg border border-line bg-inset px-3.5 py-3">
          <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-faint">
            Model
          </p>
          <p className="mt-1 text-sm font-semibold text-ink">
            {prediction.model_name ?? "Unnamed model"}
          </p>
          <p className="mt-0.5 font-mono text-[11px] text-muted">
            {prediction.model_id} v{prediction.model_version}
            {prediction.algorithm ? ` · ${prediction.algorithm}` : ""}
          </p>
        </div>
      </div>

      {rows.length > 1 ? (
        <details>
          <summary className="cursor-pointer text-[11px] text-muted">
            Every class the model considered
          </summary>
          <ul className="mt-2 space-y-1.5">
            {rows.map((row) => (
              <li key={row.className} className="flex items-center gap-2">
                <span
                  className={`w-24 shrink-0 text-[11px] capitalize ${
                    row.isPredicted ? "font-semibold text-ink" : "text-muted"
                  }`}
                >
                  {row.className.replace(/_/g, " ")}
                </span>
                <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-inset">
                  <span
                    className="block h-full rounded-full"
                    style={{
                      width: `${Math.max(0, Math.min(1, row.probability ?? 0)) * 100}%`,
                      backgroundColor: row.isPredicted
                        ? "var(--color-prov-ml)"
                        : "var(--color-line-strong)",
                    }}
                  />
                </span>
                <span className="tabular w-16 shrink-0 text-right text-[11px] text-ink-soft">
                  {row.probability === null ? "—" : formatProbability(row.probability)}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="border-t border-line pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={prediction.explanation_status === "available" ? "active" : "neutral"}>
            {prediction.explanation_status === "available"
              ? "Explained"
              : "Not explained"}
          </Badge>
          <span className="text-[10px] text-faint">
            Predicted {formatDateTime(prediction.predicted_at)}
          </span>
        </div>
        <p className="mt-1.5 text-[10px] leading-relaxed text-muted">
          Scored from twin state {prediction.twin_id}, assembled{" "}
          {formatDateTime(prediction.twin_as_of)}. This estimate describes the model&apos;s
          view of the pan; it is not a recommendation.
        </p>
        {prediction.explanation_refusal ? (
          <p className="mt-1 text-[10px] text-muted">
            No attribution is stored: {prediction.explanation_refusal}
          </p>
        ) : null}
      </div>
    </div>
  );
}
