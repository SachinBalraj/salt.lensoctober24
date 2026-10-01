"use client";

import { useCallback, useState } from "react";
import { Button, Spinner } from "@/components/ui/Controls";
import { Alert, EmptyState } from "@/components/ui/Feedback";
import { Badge, Card, CardHeader, DataPoint, Eyebrow } from "@/components/ui/Primitives";
import { CriteriaTable } from "@/components/decisions/CriteriaTable";
import { ApiError } from "@/lib/api";
import {
  adviceCanRetry,
  advise,
  adviceUnavailableMessage,
} from "@/lib/decisionsApi";
import {
  adviceHasModelEstimate,
  DEMONSTRATION_ONLY_LABEL,
  describeBasis,
  estimateIsStale,
  formatDilution,
  formatProbability,
  inputRows,
  ML_UNAVAILABLE_LABEL,
  reportsNoProbability,
  type DecisionAdvice,
} from "@/lib/decisions";
import { ADVICE_REFUSAL_REMEDIES } from "@/lib/decisionsRefusals";
import { DEMO_DATA_NOTICE } from "@/lib/demoPans";
import { formatDateTime } from "@/lib/pans";
import type { SaltPan } from "@/lib/pans";

interface LoadedState {
  advice: DecisionAdvice | null;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(fallback, { status: -1 });
}

/**
 * Module 12 on the pan detail page: the operational action for this pan, and the
 * four kinds of evidence behind it.
 *
 * **The four actions are shown, and the panel never renders a fifth.** A
 * recommendation is a call to act, and an action the panel has no wording for is
 * one an operator cannot act on. The union in `decisions.ts` is the whole list.
 *
 * **The evidence is shown as four separate things.** The recorded twin state, the
 * mass balance, the model estimate and the rule's matching each get their own
 * heading and their own provenance. They are not merged into a single inputs table,
 * because a simulated salinity in the same list as a measured one is exactly how a
 * computed figure comes to be read as an observation — so every input row carries
 * its own source label instead.
 *
 * **There is no confidence anywhere on this panel.** No percentage, no bar, no
 * hedged wording. A rule either decided or it did not, and the panel says which
 * criteria decided rather than implying a strength. The model's own probability
 * appears only inside the model block, and only when the model reported one.
 *
 * **A missing model is the project's exact label, rendered as sent.** Not
 * reworded, not composed, and not accompanied by a suggestion that the page could
 * be made to produce one.
 *
 * Advice is asked for, not computed on render: each request stores a
 * recommendation, and opening the page should not quietly write history. The button
 * is the explicit act.
 */
export default function DecisionPanel({
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
        const advice = await advise({ panId });
        setState({ advice, error: null });
      } catch (caught) {
        setState({
          advice: null,
          error: asApiError(caught, "Unexpected error requesting a recommendation."),
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
          title="Recommended action"
          description="From the recorded state, the mass balance and a written rule."
        />
        <div className="px-5 py-4">
          <Alert tone="demo" title="No recommendation in demo mode">
            {DEMO_DATA_NOTICE} A demo pan has no twin state and no written rule, so
            there is nothing to reason from and no rule to reason with.
          </Alert>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader
        title="Recommended action"
        description="From the recorded state, the mass balance and a written rule."
        actions={
          <Button
            type="button"
            onClick={ask}
            disabled={asking}
            data-testid="decision-advise"
          >
            {asking ? "Working out the action…" : "Get recommendation"}
          </Button>
        }
      />

      <div className="px-5 py-4">
        {asking ? (
          <div className="flex items-center gap-2 text-sm text-muted">
            <Spinner /> Running the mass balance and the rule…
          </div>
        ) : state === null ? (
          <EmptyState
            title="No recommendation requested"
            description="A recommendation is made on request, from this pan's recorded state and a written rule, and stored so it can be checked against what happened later."
          />
        ) : state.error ? (
          <Refused error={state.error} onRetry={ask} />
        ) : state.advice ? (
          <AdviceBody advice={state.advice} />
        ) : null}
      </div>
    </Card>
  );
}

/**
 * The refused case.
 *
 * Each refusal is a state of the system rather than a mistake in the request, so
 * the panel shows one fixed sentence per refusal, names what has to exist to get
 * past it, and offers no retry. A rule nobody has written will not appear by
 * waiting, and a button that re-asks it invites an operator to keep pressing it.
 *
 * The server's own explanation is kept in the `title` attribute, where a developer
 * can read it without an operator being shown a refusal as an error.
 */
function Refused({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const message = adviceUnavailableMessage(error);
  const remedy =
    error.code && error.code in ADVICE_REFUSAL_REMEDIES
      ? ADVICE_REFUSAL_REMEDIES[error.code as keyof typeof ADVICE_REFUSAL_REMEDIES]
      : null;

  return (
    <Alert
      tone="warning"
      title="No recommendation"
      action={
        !adviceCanRetry(error) ? undefined : (
          <Button type="button" onClick={onRetry} variant="secondary">
            Try again
          </Button>
        )
      }
    >
      <span title={error.message}>{message}</span>
      {remedy ? <p className="mt-1 text-[12px] text-muted">{remedy}</p> : null}
    </Alert>
  );
}

function AdviceBody({ advice }: { advice: DecisionAdvice }) {
  const hasEstimate = adviceHasModelEstimate(advice);
  const stale = estimateIsStale(advice);
  const rows = inputRows(advice);

  return (
    <div className="space-y-5">
      {/* First in the panel, above the action itself. A demonstration label that
          sits below the recommendation it qualifies is a label a hurried reader
          never reaches, and the action is the one thing on this page somebody
          might act on. */}
      {advice.twin.is_demonstration ? <DemonstrationBanner advice={advice} /> : null}

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Eyebrow>Recommended action</Eyebrow>
          <p className="mt-1 text-2xl font-semibold text-ink" data-testid="decision-action">
            {advice.recommendation}
          </p>
          <p className="mt-0.5 text-[12px] text-muted">{advice.reason}</p>
        </div>
        <Badge tone={hasEstimate ? "active" : "neutral"}>
          {hasEstimate ? "Rule + model" : "Rule only"}
        </Badge>
      </div>

      <Notice advice={advice} />

      {stale ? (
        <Alert tone="info" title="The model estimate is from an earlier state">
          The stored estimate was made from a different twin state than the one this
          action is based on. It is shown because the rule read it, and the mismatch
          is stated rather than left for a reader to discover.
        </Alert>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2">
        <DataPoint label="Rule" value={`${advice.rule_name} v${advice.rule_version_number}`} />
        <DataPoint label="Decided from" value={describeBasis(advice)} />
        <DataPoint label="Issued at" value={formatDateTime(advice.timestamp)} />
        <DataPoint
          label="Twin state as of"
          value={formatDateTime(advice.twin.as_of)}
        />
      </div>

      <TwinBlock advice={advice} />
      <SimulationBlock advice={advice} />
      <ModelBlock advice={advice} />
      <RuleBlock advice={advice} />

      {rows.length > 0 ? <InputBlock advice={advice} /> : null}

      <div className="border-t border-line pt-3">
        <Eyebrow>Traceability</Eyebrow>
        <dl className="mt-2 space-y-1 text-[12px] text-muted">
          <div className="flex flex-wrap gap-x-2">
            <dt>Recommendation</dt>
            <dd className="tabular break-all text-ink-soft">
              {advice.recommendation_id}
            </dd>
          </div>
          <div className="flex flex-wrap gap-x-2">
            <dt>Rule content</dt>
            <dd className="tabular break-all text-ink-soft">
              {advice.specification_hash}
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}

/**
 * The notice, rendered as the server sent it.
 *
 * When no estimate was read this is the project's exact required label plus the
 * reason, and it is displayed verbatim rather than reconstructed: a second wording
 * for the same state is how two surfaces end up saying different things.
 */
function Notice({ advice }: { advice: DecisionAdvice }) {
  if (adviceHasModelEstimate(advice)) {
    return (
      <p className="text-[12px] text-muted" data-testid="decision-notice">
        {advice.notice}
      </p>
    );
  }
  return (
    <Alert tone="info" title="No model estimate was read">
      <p data-testid="decision-notice">{advice.notice}</p>
      {advice.notice.startsWith(ML_UNAVAILABLE_LABEL) ? null : (
        // The label is the contract. A response whose notice does not begin with it
        // is shown as sent, but the discrepancy is not hidden from the reader.
        <p className="mt-1 text-[12px]">
          This response did not carry the project&apos;s required label, which is
          &ldquo;{ML_UNAVAILABLE_LABEL}&rdquo;
        </p>
      )}
    </Alert>
  );
}

function TwinBlock({ advice }: { advice: DecisionAdvice }) {
  const { twin } = advice;
  return (
    <section data-testid="decision-twin">
      <Eyebrow>Recorded state</Eyebrow>
      <p className="mt-0.5 text-[12px] text-muted">
        What was observed, as assembled into the twin state this action is based on.
      </p>
      <div className="mt-2 grid gap-4 sm:grid-cols-3">
        <DataPoint
          label="Salinity"
          value={twin.salinity_g_l === null ? "—" : twin.salinity_g_l.toFixed(1)}
          unit={twin.salinity_g_l === null ? undefined : "g/L"}
        />
        <DataPoint
          label="Water depth"
          value={twin.water_depth_cm === null ? "—" : twin.water_depth_cm.toFixed(1)}
          unit={twin.water_depth_cm === null ? undefined : "cm"}
        />
        <DataPoint
          label="Forecast rain"
          value={twin.forecast_rainfall_mm === null ? "—" : twin.forecast_rainfall_mm}
          unit={twin.forecast_rainfall_mm === null ? undefined : "mm"}
        />
      </div>
      <p className="mt-2 text-[12px] text-faint">
        Assembled {formatDateTime(twin.as_of)} · {twin.provenance}
        {twin.missing_fields.length > 0
          ? ` · missing: ${twin.missing_fields.join(", ")}`
          : ""}
      </p>
    </section>
  );
}

function SimulationBlock({ advice }: { advice: DecisionAdvice }) {
  const sim = advice.simulation_result;
  return (
    <section data-testid="decision-simulation">
      <Eyebrow>Mass balance</Eyebrow>
      <p className="mt-0.5 text-[12px] text-muted">{sim.disclaimer}</p>
      <div className="mt-2 grid gap-4 sm:grid-cols-2">
        <DataPoint
          label="Salinity after rain"
          value={sim.post_rain_salinity_g_l.toFixed(1)}
          unit="g/L"
        />
        <DataPoint
          label="Depth after rain"
          value={sim.post_rain_depth_cm.toFixed(1)}
          unit="cm"
        />
        <DataPoint
          label="Salinity drop"
          value={sim.salinity_drop_g_l.toFixed(1)}
          unit="g/L"
        />
        <DataPoint label="Dilution" value={formatDilution(sim)} />
      </div>
      <p className="mt-2 text-[12px] text-faint">
        {sim.rainfall_mm} mm of rain from {sim.rainfall_source} ·{" "}
        {sim.is_ml ? "model" : "not ML"} · {sim.model_name}
      </p>
      {sim.run_id ? (
        <p className="mt-1 break-all font-mono text-[11px] text-faint">
          Run {sim.run_id}
        </p>
      ) : null}
      {sim.assumptions.length > 0 ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-[12px] text-muted">
            What this model leaves out
          </summary>
          <ul className="mt-1.5 list-disc space-y-1 pl-4 text-[12px] text-muted">
            {sim.assumptions.map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}

function ModelBlock({ advice }: { advice: DecisionAdvice }) {
  const ml = advice.ml_result;
  if (!ml) {
    // The absence is stated here as well as in the notice, so a reader scanning
    // only the model section is not left assuming one exists.
    return (
      <section data-testid="decision-model">
        <Eyebrow>Model estimate</Eyebrow>
        <p className="mt-1 text-[12px] text-muted">
          None was read for this recommendation. Nothing is shown in place of it.
        </p>
      </section>
    );
  }

  return (
    <section data-testid="decision-model">
      <Eyebrow>Model estimate</Eyebrow>
      <p className="mt-0.5 text-[12px] text-muted">
        A stored prediction, copied exactly. This module did not score anything.
      </p>
      <div className="mt-2 grid gap-4 sm:grid-cols-2">
        <DataPoint
          label="Predicted class"
          value={ml.predicted_class ?? "—"}
        />
        <DataPoint
          label="Model"
          value={`${ml.model_id ?? "—"} v${ml.model_version}`}
        />
        <DataPoint
          label="Probability"
          value={reportsNoProbability(ml) ? "not reported" : formatProbability(ml.probability)}
        />
        <DataPoint
          label="Predicted at"
          value={formatDateTime(ml.predicted_at)}
        />
      </div>
      {reportsNoProbability(ml) ? (
        <p className="mt-2 text-[12px] text-muted">
          {ml.probability_is_null_reason ??
            "The model reported no probability for this class."}{" "}
          No likelihood is shown because none was produced.
        </p>
      ) : null}
      {ml.confidence !== null ? (
        <p className="mt-2 text-[12px] text-muted">
          Model confidence {formatProbability(ml.confidence)}. This is the
          model&apos;s own figure, not a strength for the action.
        </p>
      ) : null}
      <p className="mt-2 break-all font-mono text-[11px] text-faint">
        Prediction {ml.prediction_id}
        {ml.input_snapshot_id ? ` · ${ml.input_snapshot_id}` : ""}
      </p>
      {ml.explanation_id ? (
        <p className="mt-1 text-[12px] text-muted">
          A stored explanation of this prediction is on record as{" "}
          <span className="break-all font-mono">{ml.explanation_id}</span>. It
          describes the model&apos;s estimate only, not the action.
        </p>
      ) : null}
    </section>
  );
}

/**
 * The label a recommendation built from demonstration data must carry.
 *
 * Placed above the action rather than below it, and in the same weight as the
 * action, because the action is the part a reader acts on. The server states the
 * same thing in `notice`; this is rendered from the structured flag so that a
 * client showing the panel cannot omit it by not displaying the notice.
 */
function DemonstrationBanner({ advice }: { advice: DecisionAdvice }) {
  const sources = advice.twin.demonstration_sources;
  return (
    // The `demo` tone rather than `warning`: this codebase already has a colour
    // for demonstration data, and reusing it keeps every demonstration in the
    // product looking the same. A demonstration is not a fault, so `danger` would
    // misdescribe it and `warning` would be the first thing in the app using that
    // tone for something that is not a problem.
    <div data-testid="decision-demonstration-banner">
      <Alert tone="demo" title={DEMONSTRATION_ONLY_LABEL}>
        <p>
          This action was produced from data entered to demonstrate the workflow. It
          is a worked example and not a reading of this pan, and it must not be
          acted on.
        </p>
        {sources.length > 0 ? (
          <p className="mt-1 text-[12px]">
            Demonstration inputs:{" "}
            <span className="font-mono">{sources.join(", ")}</span>
          </p>
        ) : null}
      </Alert>
    </div>
  );
}

function RuleBlock({ advice }: { advice: DecisionAdvice }) {
  return (
    <section data-testid="decision-rule">
      <Eyebrow>The rule that decided</Eyebrow>
      <p className="mt-1 text-[12px] text-ink-soft">{advice.reason}</p>
      {advice.matched_criteria ? (
        <ul className="mt-2 space-y-1">
          {advice.matched_criteria.split("; ").map((line) => (
            <li key={line} className="font-mono text-[12px] text-muted">
              {line}
            </li>
          ))}
        </ul>
      ) : null}
      {advice.criteria.length > 0 ? <CriteriaTable criteria={advice.criteria} /> : null}
      <p className="mt-2 text-[12px] text-faint">
        {advice.rule_version}
        {advice.expires_at ? ` · expires ${formatDateTime(advice.expires_at)}` : ""}
      </p>
    </section>
  );
}

/**
 * Every variable the rule read, each labelled with where its value came from.
 *
 * A variable that could not be read keeps its row and states why. Dropping it would
 * hide the fact that a criterion went unchecked, and a table of only the values
 * that happened to be present reads as a complete account.
 */
function InputBlock({ advice }: { advice: DecisionAdvice }) {
  const rows = inputRows(advice);
  return (
    <section data-testid="decision-inputs">
      <Eyebrow>What the rule read</Eyebrow>
      <table className="mt-2 w-full text-left text-[12px]">
        <thead>
          <tr className="text-faint">
            <th className="py-1 pr-3 font-medium uppercase tracking-wide">Variable</th>
            <th className="py-1 pr-3 font-medium uppercase tracking-wide">Value</th>
            <th className="py-1 font-medium uppercase tracking-wide">From</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.variable} className="border-t border-line">
              <td className="py-1.5 pr-3 font-mono text-ink-soft">{row.variable}</td>
              <td
                className={`tabular py-1.5 pr-3 ${row.available ? "text-ink" : "text-muted"}`}
              >
                {row.display}
              </td>
              <td className="py-1.5 text-muted">{row.sourceLabel}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
