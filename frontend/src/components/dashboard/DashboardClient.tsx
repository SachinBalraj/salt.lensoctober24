"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import DataEntryPanel from "@/components/dashboard/DataEntryPanel";
import { DemonstrationSimulationCard } from "@/components/dashboard/DemonstrationSimulationCard";
import { ConditionCard, TwinStateCard, WeatherCard } from "@/components/dashboard/EvidenceCards";
import { ExplainabilityCard } from "@/components/dashboard/ExplainabilityCard";
import { MlRiskCard } from "@/components/dashboard/MlRiskCard";
import { ProvenanceLegend } from "@/components/dashboard/Provenance";
import { RecommendationCard } from "@/components/dashboard/RecommendationCard";
import { SimulationCard } from "@/components/dashboard/SimulationCard";
import { TraceCard } from "@/components/dashboard/TraceCard";
import { Spinner } from "@/components/ui/Controls";
import { Alert, EmptyState } from "@/components/ui/Feedback";
import { Badge } from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import { simulationRequest } from "@/lib/dashboard";
import { advise, latestAdvice } from "@/lib/decisionsApi";
import type { DecisionAdvice } from "@/lib/decisions";
import type { DigitalTwinState } from "@/lib/digitalTwin";
import { getTwinState, listTwinHistory, refreshTwinState } from "@/lib/digitalTwinApi";
import { readPredictionExplanation } from "@/lib/explanationApi";
import type { LocalExplanation } from "@/lib/explanation";
import { listPans } from "@/lib/pansApi";
import type { SaltPan } from "@/lib/pans";
import { predictRisk, readRiskModuleStatus } from "@/lib/riskPredictionApi";
import type { RiskPrediction } from "@/lib/riskPrediction";
import { sweepRainfallScenarios } from "@/lib/simulationApi";
import type { RainfallSweep } from "@/lib/simulation";
import type { EventOutcome } from "@/lib/outcomes";
import { listOutcomes } from "@/lib/outcomesApi";
import { feedbackForRecommendation, recordFeedback } from "@/lib/operatorFeedbackApi";
import type { FeedbackSubmission, OperatorFeedback } from "@/lib/operatorFeedback";

/**
 * The main SaltLens dashboard: one pan, its evidence, and the action that follows.
 *
 * ## What runs when, and why
 *
 * The screen separates four acts that are easy to conflate, and each is a
 * deliberate button rather than something a page visit triggers:
 *
 * 1. **Reading the evidence** — the twin state and the mass balance. Both are
 *    records of things that happened or were computed from records, and both are
 *    loaded on selection. This is the only work done without being asked.
 * 2. **Predicting risk** — writes a prediction record, so it is the operator's
 *    explicit act.
 * 3. **Analysing** — writes a recommendation record, so it is a separate explicit
 *    act, and it is not the button that predicts.
 * 4. **Recording the decision** — the operator's disposition of an issued
 *    recommendation. Accepting, modifying or rejecting files a stored record;
 *    opening the page reads a decision that already exists and files nothing.
 *
 * Keeping these apart is the point rather than tidiness. A prediction is
 * the model's answer to "what is the risk"; a recommendation is the rule's answer
 * to "what should be done"; a recorded decision is the operator's answer to the
 * recommendation. One button doing two of them would make the second look like a
 * consequence of the first, and each stage is perfectly possible — and routinely
 * issued — without the other stages having run at all.
 *
 * ## Why nothing is filled in
 *
 * A pan with no twin state, or a twin with no measured salinity, cannot be
 * simulated, and the cards say which quantity is missing instead of drawing a
 * curve from a plausible default. The simulation sweep is the one computation made
 * on load, and it is a pure function of recorded values that writes nothing, so it
 * is safe to ask for; a refused sweep leaves the card empty rather than
 * substituting figures.
 */

interface Evidence {
  twin: DigitalTwinState | null;
  sweep: RainfallSweep | null;
  error: ApiError | null;
}

interface RiskState {
  prediction: RiskPrediction | null;
  error: ApiError | null;
  loading: boolean;
  hasRun: boolean;
}

interface ShapState {
  explanation: LocalExplanation | null;
  error: ApiError | null;
  loading: boolean;
}

interface AnalysisState {
  advice: DecisionAdvice | null;
  error: ApiError | null;
  loading: boolean;
  hasRun: boolean;
}

interface SnapshotNotice {
  state: "recorded" | "failed";
  message?: string;
}

const EMPTY_RISK: RiskState = { prediction: null, error: null, loading: false, hasRun: false };
const EMPTY_SHAP: ShapState = { explanation: null, error: null, loading: false };
const EMPTY_ANALYSIS: AnalysisState = {
  advice: null,
  error: null,
  loading: false,
  hasRun: false,
};

function asApiError(caught: unknown, fallback: string): ApiError {
  return caught instanceof ApiError ? caught : new ApiError(fallback, { status: -1 });
}

/**
 * Ensure a stored twin snapshot exists before advice is asked for.
 *
 * The recommendation engine reads the recorded snapshot, never a freshly
 * assembled one. A pan whose observation and forecast are stored but whose
 * snapshot was never saved — records entered through an API, or before the
 * save-first flow existed — would otherwise refuse analysis while the state
 * card shows a complete twin. This checks the stored history and stores exactly
 * one snapshot only when none exists: a refresh always writes a snapshot, so one
 * already present must not be restated, or every re-run of the analysis would
 * manufacture another history entry that no record changed.
 *
 * Returns true when a snapshot was missing and has now been stored.
 */
async function ensureStoredSnapshot(panId: string): Promise<boolean> {
  const history = await listTwinHistory(panId, { limit: 1 });
  if (history.total > 0) return false;
  await refreshTwinState(panId);
  return true;
}

export default function DashboardClient() {
  const [pans, setPans] = useState<SaltPan[] | null>(null);
  const [listError, setListError] = useState<ApiError | null>(null);
  const [panId, setPanId] = useState<string | null>(null);

  const [evidence, setEvidence] = useState<Evidence>({
    twin: null,
    sweep: null,
    error: null,
  });
  const [risk, setRisk] = useState<RiskState>(EMPTY_RISK);
  const [shap, setShap] = useState<ShapState>(EMPTY_SHAP);
  const [analysis, setAnalysis] = useState<AnalysisState>(EMPTY_ANALYSIS);
  const [decision, setDecision] = useState<OperatorFeedback | null>(null);
  const [outcome, setOutcome] = useState<EventOutcome | null>(null);

  // Whether the reading the operator just saved has a twin snapshot stored for
  // it. Survives the re-read so the operator is told when analysis will still
  // refuse even though the record exists.
  const [snapshotNotice, setSnapshotNotice] = useState<SnapshotNotice | null>(null);

  // Bumped when a record is saved, so the evidence effect re-reads. The effect is
  // keyed on the pan, and a save does not change the pan — so without this the
  // cards above the forms would keep showing the state from before the operator
  // recorded anything, which is the one thing a form on this screen exists to
  // change. A counter rather than a boolean because two saves in a row must both
  // land, and a boolean would swallow the second.
  const [evidenceToken, setEvidenceToken] = useState(0);

  // Whether a trained model is in service, read once on mount so the ML card can
  // state an absence without the operator pressing a button to find out. `null`
  // means the read did not answer, and the card then falls back to asking rather
  // than asserting anything.
  const [modelTrained, setModelTrained] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    void readRiskModuleStatus().then((status) => {
      if (!cancelled) setModelTrained(status?.trained ?? null);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Saving a reading is an explicit act, so it is also the moment the twin
  // snapshot is stored. The recommendation engine reads the recorded snapshot,
  // never a freshly assembled one, so a reading saved without a snapshot would
  // leave "Run analysis" refusing even though every record exists. Storing a
  // snapshot on a page view would manufacture history, but an operator writing
  // a reading into the system is the opposite of a page view, and the snapshot
  // is a snapshot of the record that was just written. The evidence is re-read
  // only after the snapshot is stored, so the cards and the analysis agree on
  // what is recorded.
  const selectedPan = pans?.find((candidate) => candidate.pan_id === panId) ?? null;
  const onReadingRecorded = useCallback(async () => {
    if (!selectedPan) {
      setEvidenceToken((token) => token + 1);
      return;
    }
    try {
      await refreshTwinState(selectedPan.pan_id);
      setSnapshotNotice({ state: "recorded" });
    } catch (caught) {
      const error = asApiError(
        caught,
        "The twin snapshot could not be stored.",
      );
      setSnapshotNotice({ state: "failed", message: error.message });
    }
    setEvidenceToken((token) => token + 1);
  }, [selectedPan]);

  const pan = selectedPan;

  // Which pan the follow-up reads belong to. `selectPan` already clears the
  // follow-ups, so a late read from a previously selected pan must not land
  // figures under a new pan's name.
  const panIdRef = useRef<string | null>(null);
  useEffect(() => {
    panIdRef.current = panId;
  }, [panId]);

  // -- the follow-ups to a recommendation -----------------------------------
  //
  // Module 14 reads two more records alongside the advice: the operator decision
  // filed for it, and the latest outcome filed for the pan. Both are reads — a
  // recommendation nobody has answered, and a pan nobody has fished for yet, are
  // normal states whose absence the panels state themselves.
  const readFollowUps = useCallback(
    async (panValue: string, recommendationId: string) => {
      const owner = panIdRef.current;
      try {
        const recorded = await feedbackForRecommendation(recommendationId);
        if (panIdRef.current === owner) setDecision(recorded.items[0] ?? null);
      } catch {
        // Quiet: a pan with no recorded decision is a normal state, and the
        // decision panel's form says so.
      }
      try {
        const outcomes = await listOutcomes({ pan_id: panValue, limit: 1 });
        if (panIdRef.current === owner) setOutcome(outcomes.items[0] ?? null);
      } catch {
        // Quiet: no outcome is a normal state, and the trace's last step says so.
      }
    },
    [],
  );

  const recordDecision = useCallback(
    async (submission: FeedbackSubmission): Promise<void> => {
      // Filing is an explicit act. On success the full stored record replaces the
      // form; the server derives the pan and the recommended action from the
      // stored advice, so nothing on this side can be filed for the wrong pan.
      setDecision(await recordFeedback(submission));
    },
    [],
  );

  // -- the pan list, and the first selection ------------------------------
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await listPans({ pan_status: "active" });
        if (cancelled) return;
        setPans(response.items);
        setPanId(response.items[0]?.pan_id ?? null);
      } catch (caught) {
        if (!cancelled) {
          setPans([]);
          setListError(
            asApiError(caught, "Could not load the salt-pan registry."),
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // -- the evidence for the selected pan ----------------------------------
  useEffect(() => {
    if (!pan) return;
    let cancelled = false;

    // No synchronous reset here: changing pan already clears every card through
    // `selectPan`, and the initial state is empty. Re-setting it inside the effect
    // would be a cascading render for a value that is already correct.
    void (async () => {
      try {
        const twin = await getTwinState(pan.pan_id);
        const request = simulationRequest(pan, twin);

        // The forecast depth is swept as an extra scenario so the ringed marker on
        // the curve is a point the balance genuinely evaluated, not an
        // interpolation between the presets.
        let sweep: RainfallSweep | null = null;
        if (request.ok) {
          const extra: number[] = [];
          if (twin.forecast_rainfall_mm !== null) extra.push(twin.forecast_rainfall_mm);
          sweep = await sweepRainfallScenarios({
            ...request.inputs,
            rainfallScenariosMm: extra,
          });
        }
        if (cancelled) return;
        setEvidence({ twin, sweep, error: null });
      } catch (caught) {
        if (!cancelled) {
          setEvidence({
            twin: null,
            sweep: null,
            error: asApiError(caught, "Could not read this pan's recorded state."),
          });
        }
      }
    })();

    // The last stored recommendation is a read, so showing it costs nothing and
    // saves the operator re-running an analysis they already ran. A decision and
    // an outcome are the same kind of read: each is the last record of its kind
    // for this pan, and each belongs to this advice.
    void (async () => {
      try {
        const latest = await latestAdvice(pan.pan_id);
        if (cancelled || !latest.found || !latest.advice) return;
        setAnalysis((state) => ({
          ...state,
          advice: latest.advice,
          error: null,
          hasRun: true,
        }));
        void readFollowUps(pan.pan_id, latest.advice.recommendation_id);
      } catch {
        // A pan with no stored recommendation is a normal state, not a fault. The
        // card's empty state already says so, and a failure here must not blank
        // the evidence above it.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [pan, readFollowUps, evidenceToken]);

  // A prediction, an attribution and an analysis all belong to one pan. Moving to
  // another pan clears them rather than leaving another pan's figures on screen
  // beside a new pan's name. The operator decision and the outcome are cleared in
  // the same sweep.
  const selectPan = useCallback((next: string) => {
    setPanId(next);
    setRisk(EMPTY_RISK);
    setShap(EMPTY_SHAP);
    setAnalysis(EMPTY_ANALYSIS);
    setDecision(null);
    setOutcome(null);
  }, []);

  // -- act 1: predict risk ------------------------------------------------
  const runPrediction = useCallback(() => {
    if (!pan) return;
    setRisk((state) => ({ ...state, loading: true, error: null }));
    void (async () => {
      try {
        const prediction = await predictRisk({ panId: pan.pan_id });
        setRisk({ prediction, error: null, loading: false, hasRun: true });
      } catch (caught) {
        setRisk({
          prediction: null,
          error: asApiError(caught, "The model could not be run for this pan."),
          loading: false,
          hasRun: true,
        });
      }
    })();
  }, [pan]);

  // -- act 2: read the stored attribution ---------------------------------
  const loadShap = useCallback(() => {
    const prediction = risk.prediction;
    if (!prediction) return;
    setShap({ explanation: null, error: null, loading: true });
    void (async () => {
      try {
        // Read-only. Module 11 attributes a prediction as it is made, so the
        // numbers already exist; asking for them must not create a second
        // explanation of the same estimate.
        const explanation = await readPredictionExplanation(prediction.prediction_id);
        setShap({ explanation, error: null, loading: false });
      } catch (caught) {
        setShap({
          explanation: null,
          error: asApiError(caught, "The stored attribution could not be read."),
          loading: false,
        });
      }
    })();
  }, [risk.prediction]);

  // -- act 3: analyse and store a recommendation -------------------------
  const runAnalysis = useCallback(() => {
    if (!pan) return;
    setAnalysis((state) => ({ ...state, loading: true, error: null }));
    void (async () => {
      try {
        // The engine reads the recorded snapshot, never a freshly assembled one,
        // so a pan whose records exist but whose snapshot was never stored would
        // refuse here even though the state card looks complete. Re-running the
        // analysis stores the missing snapshot first — and only when it is
        // missing, so no history entry is duplicated — then advises from it. A
        // snapshot stored this way is the same kind of act as the one stored
        // when a reading is saved, and the notice says so it is not silent.
        const stored = await ensureStoredSnapshot(pan.pan_id);
        if (stored) {
          setSnapshotNotice({
            state: "recorded",
            message:
              "A stored twin snapshot was missing, so re-running the analysis stored one first. The recommendation reads the recorded state, not the live view.",
          });
        }
        const advice = await advise({ panId: pan.pan_id });
        setAnalysis({ advice, error: null, loading: false, hasRun: true });
      } catch (caught) {
        setAnalysis({
          advice: null,
          error: asApiError(caught, "The analysis could not be completed."),
          loading: false,
          hasRun: true,
        });
      }
    })();
  }, [pan]);

  // -- render -------------------------------------------------------------

  if (pans === null) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted">
        <Spinner /> Loading the pan registry…
      </div>
    );
  }

  if (listError) {
    return (
      <Alert tone="danger" title="The pan registry could not be read">
        <span title={listError.message}>{listError.message}</span>
      </Alert>
    );
  }

  if (pans.length === 0) {
    return (
      <EmptyState
        title="No active salt pans are registered"
        description="The dashboard reads real records, so it needs at least one registered pan. Register a pan and record a salinity, a depth and a weather forecast, then the evidence, the simulation and the recommendation all have something to work from."
      />
    );
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-accent">
            Decision support · operational view
          </p>
          <h1 className="mt-1 truncate text-2xl font-semibold tracking-tight text-ink">
            {pan?.pan_name ?? panId}
          </h1>
          {pan ? (
            <p className="mt-0.5 font-mono text-[12px] text-muted">
              {pan.pan_id}
              {pan.location ? ` · ${pan.location}` : ""}
            </p>
          ) : null}
        </div>

        <div className="min-w-0">
          <label
            htmlFor="pan-selector"
            className="block text-[10px] font-semibold uppercase tracking-[0.1em] text-faint"
          >
            Pan
          </label>
          <select
            id="pan-selector"
            value={panId ?? ""}
            onChange={(event) => selectPan(event.target.value)}
            className="mt-1 w-full min-w-56 rounded-lg border border-line bg-surface px-3 py-2 text-[13px] text-ink"
            data-testid="pan-selector"
          >
            {pans.map((candidate) => (
              <option key={candidate.pan_id} value={candidate.pan_id}>
                {candidate.pan_name} ({candidate.pan_id})
              </option>
            ))}
          </select>
        </div>
      </header>

      <ProvenanceLegend />

      {evidence.error ? (
        <Alert
          tone="warning"
          title="This pan's recorded state could not be read"
          action={
            <Link
              href="/pans"
              className="rounded-lg border border-line bg-surface px-3 py-1.5 text-[12px] font-medium text-ink-soft"
            >
              Open the registry
            </Link>
          }
        >
          <span title={evidence.error.message}>{evidence.error.message}</span>
          <p className="mt-1 text-[11px] text-muted">
            The simulation, the model estimate and the recommendation are all resolved
            from this state, so none of them can be shown without it. Record an
            observation and a weather forecast; the twin snapshot is stored with each
            reading.
          </p>
        </Alert>
      ) : !evidence.twin || !pan ? (
        <div className="flex items-center gap-2 py-8 text-sm text-muted">
          <Spinner /> Reading the pan&apos;s recorded state…
        </div>
      ) : (
        <>
          <DataEntryPanel
            panId={pan.pan_id}
            panName={pan.pan_name}
            onRecorded={onReadingRecorded}
          />

          {snapshotNotice ? (
            <p
              data-testid="twin-snapshot-notice"
              className={`mt-2 text-[12px] ${
                snapshotNotice.state === "failed" ? "text-danger" : "text-muted"
              }`}
              role={snapshotNotice.state === "failed" ? "alert" : "status"}
            >
              {snapshotNotice.state === "failed"
                ? `Reading saved, but the twin snapshot could not be stored (${snapshotNotice.message}). "Run analysis" will refuse until the recorded state is stored: save the reading again, or store the snapshot from the pan page.`
                : (snapshotNotice.message ??
                  "Reading saved — the twin snapshot is stored, and analysis resolves from the recorded state.")}
            </p>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-3">
            <ConditionCard twin={evidence.twin} />
            <WeatherCard twin={evidence.twin} />
            <TwinStateCard twin={evidence.twin} />
          </div>

          <div className="grid gap-4 lg:grid-cols-12">
            <div className="lg:col-span-7">
              <SimulationCard pan={pan} twin={evidence.twin} sweep={evidence.sweep} />
            </div>
            <div className="lg:col-span-5">
              <MlRiskCard
                prediction={risk.prediction}
                error={risk.error}
                loading={risk.loading}
                onPredict={runPrediction}
                hasRun={risk.hasRun}
                modelTrained={modelTrained}
              />
            </div>
          </div>

          <DemonstrationSimulationCard pan={pan} />

          <div className="grid gap-4 lg:grid-cols-12">
            <div className="lg:col-span-5">
              <ExplainabilityCard
                explanation={shap.explanation}
                error={shap.error}
                loading={shap.loading}
                onLoad={loadShap}
                canLoad={risk.prediction !== null}
              />
            </div>
            <div className="lg:col-span-7">
              <RecommendationCard
                advice={analysis.advice}
                error={analysis.error}
                loading={analysis.loading}
                onRun={runAnalysis}
                hasRun={analysis.hasRun}
                decision={decision}
                onRecord={recordDecision}
              />
            </div>
          </div>

          {analysis.advice ? (
            <TraceCard
              advice={analysis.advice}
              decision={decision}
              outcome={outcome}
            />
          ) : null}

          <footer className="flex flex-wrap items-center gap-x-3 gap-y-1.5 pt-1 text-[11px] text-faint">
            <Badge tone="neutral">Research prototype</Badge>
            <span>
              Evidence is read from stored records. Nothing on this screen is a
              simulated measurement, and no figure is filled in where a record is
              missing.
            </span>
          </footer>
        </>
      )}
    </div>
  );
}
