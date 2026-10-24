"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import ObservationPanel from "@/components/observations/ObservationPanel";
import OutcomePanel from "@/components/outcomes/OutcomePanel";
import DecisionPanel from "@/components/decisions/DecisionPanel";
import RiskPredictionPanel from "@/components/risk/RiskPredictionPanel";
import { PanStatusBadge } from "@/components/pans/PanTable";
import SimulatorPanel from "@/components/simulator/SimulatorPanel";
import DigitalTwinPanel from "@/components/twin/DigitalTwinPanel";
import { Button, ButtonLink, Spinner } from "@/components/ui/Controls";
import { Alert, EmptyState, Skeleton } from "@/components/ui/Feedback";
import WeatherPanel from "@/components/weather/WeatherPanel";
import {
  Badge,
  Card,
  CardHeader,
  DataPoint,
  Eyebrow,
} from "@/components/ui/Primitives";
import { ApiError } from "@/lib/api";
import { DEMO_DATA_NOTICE, getDemoPans } from "@/lib/demoPans";
import {
  formatArea,
  formatCoordinate,
  formatDateTime,
  formatMeasurement,
  normalizePanId,
  referenceVolumeM3,
  type SaltPan,
} from "@/lib/pans";
import { deactivatePan, getPan } from "@/lib/pansApi";

interface LoadedState {
  key: string;
  pan: SaltPan | null;
  error: ApiError | null;
}

export default function PanDetailClient({ panId }: { panId: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const isDemo = searchParams.get("demo") === "1";

  const [reloadToken, setReloadToken] = useState(0);
  const [state, setState] = useState<LoadedState | null>(null);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [deactivating, setDeactivating] = useState(false);
  const [confirming, setConfirming] = useState(false);
  // The selected what-if rainfall from Module 06, shared with Module 12 so
  // the recommendation uses the same scenario the user is inspecting.
  const [selectedRainfallMm, setSelectedRainfallMm] = useState<number>(20);

  // Module 05 joins records that Modules 03 and 04 write on this same page. The
  // twin is a read over the observation and weather collections, so it has to
  // re-read them after a save; the `pan` document it also reads was fetched when
  // the page opened and is refreshed by the token below. This counter is bumped
  // on each save so the twin re-reads the join against the record that was just
  // written. It re-reads only; it never stores a snapshot, so a save cannot
  // manufacture twin history.
  const [twinEvidenceToken, setTwinEvidenceToken] = useState(0);
  /**
   * Bumped by Module 03/04 alongside the twin's token, to re-read the pan itself.
   *
   * The pan document carries a denormalised copy of the newest observation, and
   * Module 06 reads that copy to decide whether a measured salinity exists. The
   * twin re-reads its own join on the same signal, and Module 03 re-reads its own
   * latest-reading card — but the `pan` this component holds was fetched when the
   * page opened, so without this the simulator keeps reasoning about a pan as it
   * was before the operator's first reading, while the panels either side of it
   * report that reading as current.
   */
  const [panToken, setPanToken] = useState(0);
  const onEvidenceRecorded = useCallback(() => {
    setTwinEvidenceToken((token) => token + 1);
    setPanToken((token) => token + 1);
  }, []);

  const requestKey = `${panId}|${reloadToken}`;

  const demoPan = useMemo(
    () =>
      isDemo
        ? (getDemoPans().find(
            (view) => normalizePanId(view.pan.pan_id) === normalizePanId(panId),
          )?.pan ?? null)
        : null,
    [isDemo, panId],
  );

  // The first load of a pan, and the only load that shows the skeleton: the
  // panels below are unmounted while it runs, because there is nothing to show.
  useEffect(() => {
    if (isDemo) return;
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await getPan(panId);
        if (cancelled) return;
        setState({ key: requestKey, pan: loaded, error: null });
      } catch (caught) {
        if (cancelled) return;
        setState({
          key: requestKey,
          pan: null,
          error:
            caught instanceof ApiError
              ? caught
              : new ApiError("Unexpected error loading this pan.", { status: -1 }),
        });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requestKey, isDemo, panId]);

  // A quiet re-read of the same pan after a Module 03/04 save.
  //
  // Unlike the load above it never clears the pan already on screen and never
  // trips `loading`, which is gated on `requestKey` alone. Showing the skeleton
  // here would unmount every panel on the page and take the operator's run, their
  // typed what-if values and their open recommendation with it, over a save that
  // changed one denormalised field. A failed refresh also leaves the last good
  // pan visible: the record itself is already stored, and blanking the page after
  // a successful save would be a worse outcome than a briefly stale panel.
  useEffect(() => {
    if (isDemo || panToken === 0) return;
    let cancelled = false;
    void (async () => {
      try {
        const loaded = await getPan(panId);
        if (cancelled) return;
        setState((current) =>
          current !== null && current.key === requestKey
            ? { key: requestKey, pan: loaded, error: current.error }
            : current,
        );
      } catch {
        // Keep the pan already on screen.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [panToken, requestKey, isDemo, panId]);

  const loading = !isDemo && state?.key !== requestKey;
  const pan = isDemo ? demoPan : (state?.pan ?? null);
  const error = isDemo
    ? demoPan === null
      ? new ApiError(
          "That demo record is not available. Demo data is disabled in this build.",
          { status: 404 },
        )
      : null
    : (state?.error ?? null);

  async function handleDeactivate() {
    if (!pan) return;
    setDeactivating(true);
    setActionError(null);
    try {
      setState({
        key: requestKey,
        pan: await deactivatePan(pan.pan_id),
        error: null,
      });
      setConfirming(false);
    } catch (caught) {
      setActionError(
        caught instanceof ApiError
          ? caught
          : new ApiError("Unexpected error while deactivating.", { status: -1 }),
      );
    } finally {
      setDeactivating(false);
    }
  }

  if (loading) {
    return (
      <div className="mx-auto w-full max-w-5xl px-5 py-7 lg:px-8 lg:py-9">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="mt-3 h-7 w-64" />
        <div className="mt-6 space-y-3">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-52 w-full" />
        </div>
      </div>
    );
  }

  if (error || !pan) {
    const notFound = error?.status === 404;
    const title = isDemo
      ? "Demo record not available"
      : notFound
        ? "Pan not found"
        : "This pan could not be loaded";
    const description = isDemo
      ? (error?.message ?? "That demo record is not available.")
      : notFound
        ? `No registered pan has the ID “${panId}”. Check the identifier, or note that pan IDs are matched case-insensitively.`
        : (error?.message ?? "The SaltLens API did not return this pan.");
    return (
      <div className="mx-auto w-full max-w-3xl px-5 py-12 lg:px-8">
        <Card>
          <EmptyState
            title={title}
            description={description}
            action={
              <div className="flex gap-2">
                <ButtonLink href="/pans" variant="secondary">
                  Back to registry
                </ButtonLink>
                {error && !notFound ? (
                  <Button
                    variant="primary"
                    onClick={() => setReloadToken((token) => token + 1)}
                  >
                    Try again
                  </Button>
                ) : null}
              </div>
            }
          />
        </Card>
      </div>
    );
  }

  const referenceVolume = referenceVolumeM3(pan);
  const notice = searchParams.get("registered")
    ? "registered"
    : searchParams.get("edited")
      ? "edited"
      : null;

  return (
    <div className="mx-auto w-full max-w-5xl px-5 py-7 lg:px-8 lg:py-9">
      <nav aria-label="Breadcrumb" className="mb-4 text-xs text-muted">
        <Link href="/pans" className="hover:text-accent hover:underline">
          Pan registry
        </Link>
        <span className="mx-1.5 text-faint">/</span>
        <span className="font-mono text-ink-soft">{pan.pan_id}</span>
      </nav>

      {isDemo ? (
        <div className="mb-5">
          <Alert tone="demo" title="Demo record — not research data">
            <p>{DEMO_DATA_NOTICE}</p>
            <p className="mt-1">
              This record exists only in your browser and cannot be edited or
              deactivated.
            </p>
          </Alert>
        </div>
      ) : notice ? (
        <div className="mb-5">
          <Alert
            tone="success"
            title={notice === "registered" ? "Pan registered" : "Changes saved"}
          >
            {notice === "registered" ? (
              <p>
                <span className="font-mono">{pan.pan_id}</span> is now part of the
                registry. Its first observation can be recorded below.
              </p>
            ) : (
              <p>
                The recorded values for{" "}
                <span className="font-mono">{pan.pan_id}</span> were updated.
              </p>
            )}
          </Alert>
        </div>
      ) : null}

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral" className="font-mono normal-case">
              {pan.pan_id}
            </Badge>
            <PanStatusBadge isActive={pan.is_active} origin={isDemo ? "demo" : "registry"} />
          </div>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">
            {pan.pan_name}
          </h1>
          <p className="mt-1 text-sm text-muted">{pan.location}</p>
        </div>

        {!isDemo ? (
          <div className="flex flex-wrap items-center gap-2">
            <ButtonLink
              href={`/pans/${encodeURIComponent(pan.pan_id)}/edit`}
              variant="primary"
            >
              Edit pan
            </ButtonLink>
            <Button
              variant="secondary"
              onClick={() => setConfirming(true)}
              disabled={!pan.is_active || deactivating}
              title={
                pan.is_active
                  ? "Retire this pan from active operation"
                  : "This pan is already deactivated"
              }
            >
              {deactivating ? <Spinner label="Deactivating" /> : "Deactivate"}
            </Button>
          </div>
        ) : null}
      </header>

      {actionError ? (
        <div className="mt-5">
          <Alert tone="danger" title="The pan could not be deactivated">
            {actionError.message}
          </Alert>
        </div>
      ) : null}

      {confirming ? (
        <div className="mt-5">
          <Alert
            tone="warning"
            title={`Deactivate ${pan.pan_id}?`}
            action={
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  onClick={() => setConfirming(false)}
                  disabled={deactivating}
                >
                  Cancel
                </Button>
                <Button
                  variant="danger"
                  onClick={() => void handleDeactivate()}
                  disabled={deactivating}
                >
                  {deactivating ? "Deactivating…" : "Confirm deactivation"}
                </Button>
              </div>
            }
          >
            <p>
              The pan is withdrawn from active operation and from the default
              registry listing. Its record, and anything that references it, is
              kept. This is a one-way transition in Module 02.
            </p>
          </Alert>
        </div>
      ) : null}

      <div className="mt-6 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Registered parameters"
            description="Operator-declared design values. These are inputs, not measurements."
          />
          <dl className="grid grid-cols-2 gap-x-5 gap-y-5 p-5 sm:grid-cols-3">
            <DataPoint label="Pan area" value={formatArea(pan.area_m2)} />
            <DataPoint
              label="Normal water depth"
              value={formatMeasurement(pan.normal_depth_cm, "cm")}
            />
            <DataPoint
              label="Target harvest salinity"
              value={formatMeasurement(pan.target_harvest_salinity_g_l, "g/L")}
            />
            <DataPoint label="Reference volume" value={referenceVolume.toFixed(1)} unit="m³" />
            <DataPoint label="Latitude" value={formatCoordinate(pan.latitude, "lat")} mono />
            <DataPoint label="Longitude" value={formatCoordinate(pan.longitude, "lon")} mono />
          </dl>
          <p className="border-t border-line px-5 py-3 text-[11px] leading-relaxed text-muted">
            Reference volume is the product of the declared area and normal depth.
            It is a geometric consequence of the two fields above, not an
            independent measurement.
          </p>
        </Card>

        <Card>
          <CardHeader title="Record" />
          <dl className="space-y-4 p-5">
            <div>
              <Eyebrow>Coordinates</Eyebrow>
              <p className="mt-1.5 font-mono text-[13px] text-ink">
                {pan.latitude === null || pan.longitude === null
                  ? "Not geolocated"
                  : `${pan.latitude}, ${pan.longitude}`}
              </p>
              <p className="mt-1 text-[11px] text-muted">
                {pan.latitude === null || pan.longitude === null
                  ? "This pan has no coordinate pair. Add one when editing if the site is known."
                  : "Decimal degrees, WGS84 reference."}
              </p>
            </div>
            <div>
              <Eyebrow>Registered</Eyebrow>
              <p className="tabular mt-1.5 text-[13px] text-ink">
                {formatDateTime(pan.created_at)}{" "}
                <span className="text-muted">UTC</span>
              </p>
            </div>
            <div>
              <Eyebrow>Last updated</Eyebrow>
              <p className="tabular mt-1.5 text-[13px] text-ink">
                {formatDateTime(pan.updated_at)} <span className="text-muted">UTC</span>
              </p>
            </div>
            {pan.deactivated_at ? (
              <div>
                <Eyebrow>Deactivated</Eyebrow>
                <p className="tabular mt-1.5 text-[13px] text-danger">
                  {formatDateTime(pan.deactivated_at)}{" "}
                  <span className="text-muted">UTC</span>
                </p>
              </div>
            ) : null}
            <div>
              <Eyebrow>Document ID</Eyebrow>
              <p className="mt-1.5 break-all font-mono text-[11px] text-muted">
                {pan.id ?? "—"}
              </p>
            </div>
          </dl>
          <div className="border-t border-line px-5 py-3">
            <button
              type="button"
              onClick={() => void router.refresh()}
              className="text-xs text-muted hover:text-accent hover:underline"
            >
              Refresh this pan
            </button>
          </div>
        </Card>
      </div>

      <div className="mt-4">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          Module 03 — Pan observation
        </h2>
      </div>
      <ObservationPanel
        pan={pan}
        isDemo={isDemo}
        onEvidenceRecorded={onEvidenceRecorded}
      />

      <div className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          Module 04 — Weather data
        </h2>
      </div>
      <WeatherPanel
        pan={pan}
        isDemo={isDemo}
        onEvidenceRecorded={onEvidenceRecorded}
      />

      <div className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          Module 05 — Digital twin
        </h2>
      </div>
      <DigitalTwinPanel
        pan={pan}
        isDemo={isDemo}
        evidenceToken={twinEvidenceToken}
      />

      <div className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          Module 06 — What-if rainfall simulator
        </h2>
      </div>
      {/* Keyed by pan so a what-if run never survives onto a different pan.
          The prefix keeps this key unique among its siblings. */}
      <SimulatorPanel
        key={`simulator-${pan.pan_id}`}
        pan={pan}
        isDemo={isDemo}
        onSelectedRainfallChange={setSelectedRainfallMm}
      />

      <div className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          Module 10 — Operational risk
        </h2>
      </div>
      {/* Keyed by pan so a prediction never survives onto a different pan.
          The prefix keeps this key unique among its siblings. */}
      <RiskPredictionPanel key={`risk-${pan.pan_id}`} pan={pan} isDemo={isDemo} />

      <div className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          Module 12 — Recommended action
        </h2>
      </div>
      {/* Keyed by pan so a recommendation never survives onto a different pan, and
          so the advice is asked for on this pan rather than appearing already
          answered from the last one. */}
      <DecisionPanel
        key={`decision-${pan.pan_id}`}
        pan={pan}
        isDemo={isDemo}
        rainfallMm={selectedRainfallMm}
      />

      <div className="mt-8">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-muted">
          Module 15 — Actual outcome
        </h2>
      </div>
      <OutcomePanel pan={pan} isDemo={isDemo} />
    </div>
  );
}
