"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import PanScopeSelect from "@/components/outcomes/PanScopeSelect";
import ValidationBlock from "@/components/validation/ValidationBlock";
import { Button, ButtonLink } from "@/components/ui/Controls";
import { Alert, EmptyState, Skeleton } from "@/components/ui/Feedback";
import { Card, StatTile } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import { COMPARISON_KEYS, type ValidationOverview, type ValidationStatus } from "@/lib/validation";
import { getValidationOverview, getValidationStatus } from "@/lib/validationApi";

interface LoadedState {
  key: string;
  status: ValidationStatus | null;
  overview: ValidationOverview | null;
  error: ApiError | null;
}

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(fallback, { status: -1 });
}

/**
 * The header tiles for the record the report covers.
 *
 * Drawn from the status endpoint's live counts when the backend is reachable.
 * The totals are registry counts, not events already compared: the dashboard's
 * headline is what stands behind the figures, so a page with room for ten
 * evaluations is honest about that too.
 */
function RegistryTiles({ status }: { status: ValidationStatus | null }) {
  const counts: ValidationStatus["validation"] = status?.validation ?? { available: false };
  const tiles: Array<{ label: string; value: ReactNode; hint: string }> = [
    {
      label: "Outcomes recorded",
      value: counts.total_outcomes ?? "—",
      hint: "Events measured against a prediction",
    },
    {
      label: "Salt comparisons",
      value: counts.comparable_outcomes ?? "—",
      hint: "Outcomes with a salinity error stored",
    },
    {
      label: "Rainfall errors",
      value: counts.forecast_errors ?? "—",
      hint: "Outcomes with a forecast error stored",
    },
    {
      label: "Risk predictions",
      value: counts.risk_predictions ?? "—",
      hint: "Model estimates cited by advice",
    },
    {
      label: "Operator decisions",
      value: counts.operator_decisions ?? "—",
      hint: "Recorded dispositions of advice",
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {tiles.map((tile) => (
        <StatTile key={tile.label} label={tile.label} value={tile.value} hint={tile.hint} />
      ))}
    </div>
  );
}

function scopeSentence(overview: ValidationOverview): string {
  const total = overview.scope.total_outcomes;
  if (total === 0) return "No recorded events yet — there is nothing to validate.";
  const events = total === 1 ? "event" : "events";
  return overview.scope.pan_id
    ? `Grading ${total} recorded ${events} for ${overview.scope.pan_id}.`
    : `Grading ${total} recorded ${events} across every pan.`;
}

/**
 * The client half of `/research-validation`.
 *
 * Holds the pan scope (the only state a server component cannot), reads the two
 * backend reports, and hands each comparison family to a {@link ValidationBlock}.
 * No sample data is ever substituted: a metric that was not calculated is the
 * dash, and an unreachable backend says so rather than showing a plausible
 * figure for a model that has not been measured.
 */
export default function ValidationClient() {
  const [panId, setPanId] = useState("");
  const [reloadToken, setReloadToken] = useState(0);
  const [state, setState] = useState<LoadedState | null>(null);

  const requestKey = `${panId ?? "all"}|${reloadToken}`;
  const loading = state?.key !== requestKey;
  const overview = state?.overview ?? null;
  const status = state?.status ?? null;
  const error = state?.error ?? null;
  const connectivity = error?.isConnectivity ?? false;

  const load = useCallback(async () => {
    try {
      const [statusReport, report] = await Promise.all([
        getValidationStatus(),
        getValidationOverview(panId ? { pan_id: panId } : {}),
      ]);
      setState({ key: requestKey, status: statusReport, overview: report, error: null });
    } catch (caught) {
      setState({
        key: requestKey,
        status: null,
        overview: null,
        error: asApiError(caught, "Unexpected error loading the validation report."),
      });
    }
  }, [requestKey, panId]);

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

  return (
    <div className="mx-auto w-full max-w-6xl px-5 py-7 lg:px-8 lg:py-9">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
            Module 16 · Research validation
          </p>
          <h1 className="mt-1.5 text-2xl font-semibold tracking-tight text-ink">
            Research validation
          </h1>
          <p className="mt-1.5 max-w-2xl text-sm text-muted">
            Every stored prediction graded against what actually happened, by the
            same rule it was made under. A figure is only ever shown beside the
            events that produced it; what could not be compared is counted with a
            reason, never zero-filled.
          </p>
        </div>
        <PanScopeSelect value={panId} onChange={setPanId} />
      </header>

      <div className="mt-6 space-y-5">
        {error ? (
          <Alert
            tone={connectivity ? "warning" : "danger"}
            title={
              connectivity ? "The validation report is unavailable" : "The validation report could not be loaded"
            }
            action={
              <Button variant="secondary" onClick={() => setReloadToken((t) => t + 1)}>
                Try again
              </Button>
            }
          >
            <p>{error.message}</p>
            {connectivity ? (
              <p className="mt-1 text-xs">
                Figures are graded against outcomes recorded in the database.
                Without a reachable backend no figure exists, and none is shown
                here in its place.
              </p>
            ) : null}
          </Alert>
        ) : null}

        {loading ? (
          <div className="space-y-3">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
          </div>
        ) : overview ? (
          <>
            <RegistryTiles status={status} />

            {overview.scope.total_outcomes === 0 ? (
              <Card>
                <EmptyState
                  title="No outcomes recorded yet"
                  description="Nothing has been measured against a prediction yet, so there is nothing to grade. Record an outcome from a pan's page and its validation appears here."
                  action={
                    <ButtonLink variant="primary" href="/pans">
                      Open a pan
                    </ButtonLink>
                  }
                />
              </Card>
            ) : (
              <>
                <p className="text-xs text-muted">{scopeSentence(overview)}</p>
                {COMPARISON_KEYS.map((key) => (
                  <ValidationBlock
                    key={key}
                    comparisonKey={key}
                    block={overview[key]}
                    scope={overview.scope}
                  />
                ))}
              </>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}