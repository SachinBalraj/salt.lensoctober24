"use client";

import { Button, Spinner } from "@/components/ui/Controls";
import { Alert, EmptyState } from "@/components/ui/Feedback";
import { ProvenanceCard, ProvenanceTag } from "@/components/dashboard/Provenance";
import { ApiError } from "@/lib/api";
import { shapFactorsAreFlat, topShapFactors, type ShapFactor } from "@/lib/dashboard";
import {
  describeDirection,
  describeMethod,
  type LocalExplanation,
} from "@/lib/explanation";
import { explanationUnavailableMessage } from "@/lib/explanationApi";

/**
 * Which features moved the model's estimate, and in which direction.
 *
 * **Contributions are changes, not levels, and are never shown as a percentage.**
 * A SHAP value answers "how far did this one feature move the output away from
 * what the model would have said about a typical row", and it is measured in
 * probability *points*. The model's probability is a level. Putting the two in the
 * same column with the same `%` would assert that a +0.25 contribution makes a pan
 * 25% more likely, which is a different and wrong claim — so the bars are signed,
 * centred on zero, and labelled in points, and the probability is not on this card
 * at all.
 *
 * **A factor whose value came from the mass balance is marked as simulated.** The
 * model did read that figure, and its contribution is exactly as real as any
 * other's — but the number that produced it was computed, not measured, and an
 * operator reading the factor list should not come away believing a simulated
 * salinity was an observation. The mark is on the row rather than a separate note
 * because the two things differ per feature.
 *
 * **An imputed feature is marked too.** A preprocessor's median is a statistic of
 * the training rows, not a measurement of this pan, so a factor that rests on one
 * is resting on nothing about this pan in particular.
 *
 * Ranking is by absolute contribution, so a large downward push is not buried
 * beneath the upward ones — an operator asking what drove this number wants the
 * strongest influence whichever way it went.
 */
export function ExplainabilityCard({
  explanation,
  error,
  loading,
  onLoad,
  canLoad,
}: {
  explanation: LocalExplanation | null;
  error: ApiError | null;
  loading: boolean;
  onLoad: () => void;
  canLoad: boolean;
}) {
  return (
    <ProvenanceCard
      kind="ml"
      title="Explainability"
      description="How the model's estimate was reached, in probability points. Signed: a bar left of zero pushed the risk down."
      actions={
        canLoad ? (
          <Button
            type="button"
            onClick={onLoad}
            disabled={loading}
            data-testid="shap-load"
          >
            {loading ? "Loading…" : "Load factors"}
          </Button>
        ) : null
      }
    >
      {loading ? (
        <div className="flex items-center gap-2 py-2 text-[13px] text-muted">
          <Spinner /> Reading the stored attribution…
        </div>
      ) : error ? (
        <Alert tone="info" title="No attribution available">
          <span title={error.message}>{explanationUnavailableMessage(error)}</span>
        </Alert>
      ) : !explanation ? (
        <EmptyState
          title="No explanation loaded"
          description={
            canLoad
              ? "Load the attribution Module 11 stored for this prediction. Every prediction is explained as it is made, so the numbers already exist and are not being computed here."
              : "Run a risk prediction first. There is nothing to attribute until a model has scored this pan."
          }
        />
      ) : (
        <FactorList explanation={explanation} />
      )}
    </ProvenanceCard>
  );
}

function FactorList({ explanation }: { explanation: LocalExplanation }) {
  const factors = topShapFactors(explanation, 6);
  const scale = Math.max(...factors.map((factor) => Math.abs(factor.value)), 0.0001);

  return (
    <div data-testid="shap-factors">
      {shapFactorsAreFlat(factors) ? (
        <p className="text-[12px] text-muted">
          No feature moved this estimate. The model returned its expected value for
          this class whatever it was given.
        </p>
      ) : (
        <ul className="space-y-2.5">
          {factors.map((factor) => (
            <FactorRow key={factor.feature} factor={factor} scale={scale} />
          ))}
        </ul>
      )}

      <div className="mt-4 border-t border-line pt-3">
        <p className="text-[10px] leading-relaxed text-faint">
          {describeMethod(explanation)} Measured against {explanation.background.rows}{" "}
          {explanation.background.source.replace(/_/g, " ")} rows. A contribution is a
          change in probability points, not a probability.
        </p>
      </div>
    </div>
  );
}

/**
 * One factor: its name, a signed bar about a zero line, and the change in points.
 *
 * The zero line is drawn rather than implied, because the sign is the whole
 * content: a bar that grows to the right raised the model's estimate and one that
 * grows to the left lowered it, and an operator reading magnitude alone would take
 * a strong reason to harvest as evidence for a strong reason to wait.
 */
function FactorRow({ factor, scale }: { factor: ShapFactor; scale: number }) {
  const magnitude = (Math.abs(factor.value) / scale) * 50;
  const positive = factor.value >= 0;

  return (
    <li data-factor={factor.feature} className="flex items-center gap-2.5">
      <div className="w-40 shrink-0 min-w-0">
        <p className="truncate text-[11px] font-medium text-ink" title={factor.feature}>
          {factor.label}
        </p>
        <p className="text-[10px] text-faint">{describeDirection(factor.direction)}</p>
      </div>

      <div className="relative h-4 min-w-0 flex-1">
        <span
          aria-hidden
          className="absolute inset-y-0 left-1/2 w-px bg-line-strong"
        />
        <span
          className="absolute inset-y-0.5 rounded-sm"
          style={{
            width: `${magnitude}%`,
            left: positive ? "50%" : `${50 - magnitude}%`,
            backgroundColor: positive
              ? "var(--color-danger-soft)"
              : "var(--color-active-soft)",
            borderLeft: positive
              ? "2px solid var(--color-danger)"
              : "none",
            borderRight: positive
              ? "none"
              : "2px solid var(--color-active)",
            borderTop: positive
              ? "2px solid var(--color-danger-line)"
              : "2px solid var(--color-active-line)",
            borderBottom: positive
              ? "2px solid var(--color-danger-line)"
              : "2px solid var(--color-active-line)",
          }}
        />
      </div>

      <span className="tabular w-16 shrink-0 text-right text-[11px] font-medium text-ink-soft">
        {factor.display}
      </span>

      {factor.simulated || factor.imputed ? (
        <span className="shrink-0">
          {factor.simulated ? <ProvenanceTag kind="simulated" /> : null}
          {factor.imputed ? (
            <span
              title="The preprocessor supplied this value. It is a statistic of the training rows, not a measurement of this pan."
              className="rounded-md border border-line-strong bg-inset px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted"
            >
              Imputed
            </span>
          ) : null}
        </span>
      ) : null}
    </li>
  );
}
