"use client";

import { useCallback, useEffect, useState } from "react";
import PanScopeSelect from "@/components/outcomes/PanScopeSelect";
import FieldValidationChart from "@/components/fieldValidation/FieldValidationChart";
import { EventProvenancePanel } from "@/components/fieldValidation/FieldEventProvenance";
import { Badge, Card, CardHeader, StatTile } from "@/components/ui/Primitives";
import { Button, ButtonLink, ButtonRow, Spinner } from "@/components/ui/Controls";
import { Alert, EmptyState, LoadingRows } from "@/components/ui/Feedback";
import { NumberField, SelectField, TextAreaField, TextField } from "@/components/ui/Fields";
import { ApiError } from "@/lib/api";
import {
  INTERVENTION_KIND_LABELS,
  INTERVENTION_DIRECTION_LABELS,
  EVENT_MILESTONE_LABELS,
  RECONCILIATION_LABELS,
  differenceMetricLabel,
  displayDifference,
  headlineCaveats,
  isComparableDepth,
  isComparableSalinity,
  isPreEventTraceable,
  type EventMilestone,
  type FieldValidationComparison,
  type FieldValidationStatus,
  type FieldValidationSummary,
  type InterventionDirection,
  type InterventionKind,
} from "@/lib/fieldValidation";
import { listPans } from "@/lib/pansApi";
import {
  createFieldValidationEvent,
  fieldValidationExportUrl,
  getFieldValidationStatus,
  getFieldValidationSummary,
  listFieldValidationComparisons,
} from "@/lib/fieldValidationApi";

const DATETIME_FORMAT = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const MILESTONE_OPTIONS = (Object.keys(EVENT_MILESTONE_LABELS) as EventMilestone[]).map(
  (value) => ({ value, label: EVENT_MILESTONE_LABELS[value] }),
);

function formatInstant(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return DATETIME_FORMAT.format(date);
}

function formatNumber(value: number | null, digits = 2): string {
  if (value === null) return "—";
  return value.toFixed(digits);
}

function nowIso(): string {
  return new Date().toISOString();
}

/**
 * The field-validation dashboard (Module 20).
 *
 * Renders whatever the backend computed from the stored reports and nothing else:
 * status tiles, the predicted-against-reported charts, a table of the events with
 * their flags and caveats, export links, and the form that files a new reported
 * event. There is no number on this page that the API did not send.
 */
export default function FieldValidationClient() {
  const [panId, setPanId] = useState("");
  const [status, setStatus] = useState<FieldValidationStatus | null>(null);
  const [summary, setSummary] = useState<FieldValidationSummary | null>(null);
  const [comparisons, setComparisons] = useState<FieldValidationComparison[]>([]);
  /**
   * Pan id to pan name, read from the registry.
   *
   * The comparison carries a `pan_name`, but the API fills it with the pan's
   * *location* rather than its name, so it cannot caption a chart. The registry
   * is the one place a pan's own name exists, so the caption is resolved from it
   * and the chart falls back to the comparison's field if the registry is
   * unreachable. Chart labelling is never worth failing the page over.
   */
  const [panNames, setPanNames] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const response = await listPans({ pan_status: "active", limit: 200 });
        if (cancelled) return;
        setPanNames(
          Object.fromEntries(response.items.map((pan) => [pan.pan_id, pan.pan_name])),
        );
      } catch {
        // Not fatal: the charts still label from the comparison's own field.
        if (cancelled) return;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const reload = useCallback(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [nextStatus, nextSummary, nextComparisons] = await Promise.all([
          getFieldValidationStatus(panId || undefined),
          getFieldValidationSummary(panId ? { pan_id: panId } : {}),
          listFieldValidationComparisons(panId ? { pan_id: panId } : {}),
        ]);
        if (cancelled) return;
        setStatus(nextStatus);
        setSummary(nextSummary);
        setComparisons(nextComparisons);
        setError(null);
      } catch (caught) {
        if (cancelled) return;
        setStatus(null);
        setSummary(null);
        setComparisons([]);
        setError(
          caught instanceof ApiError
            ? caught
            : new ApiError("Could not load the field-validation data.", { status: 0 }),
        );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [panId]);

  useEffect(() => {
    const cleanup = reload();
    return cleanup;
  }, [reload]);

  if (loading && status === null && comparisons.length === 0 && error === null) {
    return (
      <Card className="p-5">
        <LoadingRows rows={5} />
      </Card>
    );
  }

  if (error && status === null) {
    return (
      <Alert tone="danger" title="Field validation is unavailable">
        <p>{error.message}</p>
        <ButtonRow>
          <Button onClick={() => void reload()}>Try again</Button>
        </ButtonRow>
      </Alert>
    );
  }

  const comparable = comparisons.filter(isComparableSalinity).length;
  const interventions = comparisons.reduce((sum, c) => sum + (c.intervention_affected ? 1 : 0), 0);
  const unknownVolumes = comparisons.reduce((sum, c) => sum + (c.unknown_intervention_volumes ? 1 : 0), 0);
  const verified = comparisons.reduce((sum, c) => sum + (c.independently_verified ? 1 : 0), 0);
  const untraceable = comparisons.filter((c) => !isPreEventTraceable(c)).length;
  const blocked = comparisons.filter(
    (c) => c.reconciliation_state === "blocked_by_evidence",
  ).length;
  const unreconciled = comparisons.filter(
    (c) => c.reconciliation_state !== "complete",
  ).length;

  // How many events each difference figure is drawn from. The backend's own
  // counts are preferred because the mean is computed over those events; the
  // loaded comparisons are the fallback before the summary has arrived.
  const salinitySamples = summary?.events_with_salinity ?? comparisons.filter(isComparableSalinity).length;
  const depthSamples = summary?.events_with_depth ?? comparisons.filter(isComparableDepth).length;
  const salinityMetric = differenceMetricLabel("salinity", salinitySamples);
  const depthMetric = differenceMetricLabel("depth", depthSamples);

  const sampleHint = (count: number | null, unit: string): string | undefined => {
    if (count === null) return undefined;
    if (count === 0) return unit;
    return `n = ${count} event${count === 1 ? "" : "s"} · ${unit}`;
  };

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink">Field validation</h1>
          <p className="mt-0.5 text-[13px] leading-relaxed text-muted">
            Reported post-event readings compared with a rainfall-only mass balance
            replayed from the event&apos;s stored inputs. A gap is a gap a validation
            number is supposed to explain — it is not proof of anything on its own.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <PanScopeSelect value={panId} onChange={setPanId} />
          <div className="flex gap-2">
            <ButtonLinkExport format="csv" panId={panId} />
            <ButtonLinkExport format="pdf" panId={panId} />
          </div>
        </div>
      </div>

      {status && !status.ready ? (
        <Alert tone="warning" title={status.message}>
          <p>
            The module can still store an event — nothing here blocks a report — but
            an empty dashboard reads as a healthy one, so it is not shown as if it
            were.
          </p>
        </Alert>
      ) : null}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 xl:grid-cols-7">
        <StatTile label="Events filed" value={status?.event_count ?? comparisons.length} />
        <StatTile
          label="Compared (salinity)"
          value={comparable}
          hint={comparisons.length > 0 ? `of ${comparisons.length} events` : undefined}
        />
        <StatTile
          label={salinityMetric.label}
          value={summary ? formatNumber(summary.mean_abs_salinity_diff_g_l) : "—"}
          hint={
            summary?.mean_abs_salinity_diff_g_l !== null
              ? sampleHint(salinityMetric.sampleCount, "g/L")
              : undefined
          }
        />
        <StatTile
          label={depthMetric.label}
          value={summary ? formatNumber(summary.mean_abs_depth_diff_cm) : "—"}
          hint={
            summary?.mean_abs_depth_diff_cm !== null
              ? sampleHint(depthMetric.sampleCount, "cm")
              : undefined
          }
        />
        <StatTile
          label="Recorded interventions"
          value={interventions}
          hint={unknownVolumes > 0 ? `${unknownVolumes} with unrecorded volumes` : undefined}
        />
        <StatTile
          label="Unreconciled"
          value={unreconciled}
          hint="no milestone, or no linked source record"
        />
        <StatTile
          label="Blocked by evidence"
          value={blocked}
          hint="two records disagree; neither changed"
        />
        <StatTile
          label="Independently verified"
          value={verified}
          hint="confirmed against a second source"
        />
      </div>

      {blocked > 0 ? (
        <Alert tone="warning" title="An event is blocked on conflicting evidence">
          <p>
            {blocked} event{blocked === 1 ? " carries" : "s carry"} two records that
            disagree about when the rain fell. Neither timestamp has been changed:
            the event keeps the one it was filed with and shows the other beside it,
            and it cannot be reported as reconciled until the disagreement is settled
            against evidence. Open the event&apos;s provenance panel to see both.
          </p>
        </Alert>
      ) : null}

      {untraceable > 0 ? (
        <Alert tone="warning" title="Some reported figures cannot be traced to a record">
          <p>
            {untraceable} event{untraceable === 1 ? " has" : "s have"} a pre-event
            snapshot with no linked stored observation, so the starting brine behind
            the replay is reported-only. Open an event&apos;s provenance panel to
            reconcile it against the original field record; nothing is invented for
            you, and a link is refused if the record does not support it.
          </p>
        </Alert>
      ) : null}

      {comparisons.length > 0 ? (
        <FieldValidationChart comparisons={comparisons} panNames={panNames} />
      ) : (
        <Card>
          <EmptyState
            title="No rainfall events recorded yet"
            description="File the first report below, and the predicted-versus-reported comparison will appear here. Nothing on this page is estimated or simulated in the browser."
          />
        </Card>
      )}

      {comparisons.length > 0 ? (
        <ComparisonTable comparisons={comparisons} onChanged={() => void reload()} />
      ) : null}

      <SummaryPanel summary={summary} />

      <ReportEventForm
        panId={panId}
        onFiled={() => void reload()}
      />
    </div>
  );
}

function ButtonLinkExport({ format, panId }: { format: "csv" | "pdf"; panId: string }) {
  return (
    <ButtonLink variant="ghost" href={fieldValidationExportUrl(format, panId || undefined)}>
      {format === "csv" ? "Export CSV" : "Export PDF"}
    </ButtonLink>
  );
}

function ComparisonTable({
  comparisons,
  onChanged,
}: {
  comparisons: FieldValidationComparison[];
  onChanged: () => void;
}) {
  return (
    <Card>
      <CardHeader
        title="Events"
        description="Predicted is the mass-balance replay; reported is what the pan reported. |Δ| is the absolute difference in each unit. Each event's time is shown in UTC and in the site's local zone; open a row for its provenance, audit history and reconciliation forms."
      />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[64rem] border-collapse text-[13px]">
          <thead>
            <tr className="border-b border-line">
              <th scope="col" className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-faint">
                Event
              </th>
              <th scope="col" className="px-2 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wide text-faint">
                Rainfall (mm)
              </th>
              <th scope="col" className="px-2 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wide text-faint">
                Pred. salinity
              </th>
              <th scope="col" className="px-2 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wide text-faint">
                Reported salinity
              </th>
              <th scope="col" className="px-2 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wide text-faint">
                |Δ| (g/L)
              </th>
              <th scope="col" className="px-2 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wide text-faint">
                Pred. depth
              </th>
              <th scope="col" className="px-2 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wide text-faint">
                Reported depth
              </th>
              <th scope="col" className="px-2 py-2.5 text-right text-[11px] font-semibold uppercase tracking-wide text-faint">
                |Δ| (cm)
              </th>
              <th scope="col" className="px-2 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wide text-faint">
                Flags
              </th>
            </tr>
          </thead>
          <tbody>
            {comparisons.map((comparison) => {
              const caveats = headlineCaveats(comparison);
              return (
                <EventRow
                  key={comparison.event_id}
                  comparison={comparison}
                  caveats={caveats}
                  onChanged={onChanged}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function EventRow({
  comparison,
  caveats,
  onChanged,
}: {
  comparison: FieldValidationComparison;
  caveats: string[];
  onChanged: () => void;
}) {
  const [showProvenance, setShowProvenance] = useState(false);
  const salinityComparable = isComparableSalinity(comparison);
  const depthComparable = isComparableDepth(comparison);
  return (
    <>
      <tr className="border-b border-line last:border-0">
        <th scope="row" className="px-4 py-2.5 text-left align-top">
          <span className="text-ink">{comparison.event_id}</span>
          <span className="mt-0.5 block text-[11px] text-muted">
            {comparison.pan_name ?? comparison.pan_id}
          </span>
          {/* Both zones, both labelled: the stored instant is UTC and the local
              line is the backend's rendering of that same instant. */}
          <span className="tabular mt-0.5 block text-[11px] text-ink-soft">
            {comparison.event_timestamp_utc ?? formatInstant(comparison.event_timestamp)}
          </span>
          <span className="tabular block text-[11px] text-muted">
            {comparison.event_timestamp_ist ?? "—"}
          </span>
          <button
            type="button"
            onClick={() => setShowProvenance((open) => !open)}
            aria-expanded={showProvenance}
            className="mt-1 text-[11px] font-medium text-active underline underline-offset-2"
          >
            {showProvenance ? "Hide provenance" : "Provenance & audit"}
          </button>
        </th>
        <td className="tabular px-2 py-2.5 text-right align-top text-ink-soft">
          {formatNumber(comparison.actual_rainfall_mm)}
        </td>
        <td className="tabular px-2 py-2.5 text-right align-top text-ink-soft">
          {salinityComparable ? formatNumber(comparison.predicted_salinity_g_l) : "—"}
        </td>
        <td className="tabular px-2 py-2.5 text-right align-top text-ink-soft">
          {comparison.reported_salinity_g_l !== null ? formatNumber(comparison.reported_salinity_g_l) : "—"}
        </td>
        <td className={`tabular px-2 py-2.5 text-right align-top ${salinityComparable ? "text-ink" : "text-faint"}`}>
          {salinityComparable ? displayDifference(comparison.salinity_abs_diff_g_l) : "—"}
        </td>
        <td className="tabular px-2 py-2.5 text-right align-top text-ink-soft">
          {depthComparable ? formatNumber(comparison.predicted_depth_cm) : "—"}
        </td>
        <td className="tabular px-2 py-2.5 text-right align-top text-ink-soft">
          {comparison.reported_depth_cm !== null ? formatNumber(comparison.reported_depth_cm) : "—"}
        </td>
        <td className={`tabular px-2 py-2.5 text-right align-top ${depthComparable ? "text-ink" : "text-faint"}`}>
          {depthComparable ? displayDifference(comparison.depth_abs_diff_cm) : "—"}
        </td>
        <td className="px-2 py-2.5 align-top">
          <div className="flex flex-wrap gap-1">
            {comparison.independently_verified ? (
              <Badge tone="active">Verified</Badge>
            ) : (
              <Badge tone="neutral">Reported</Badge>
            )}
            {comparison.intervention_affected ? (
              <Badge tone="caution">Intervention</Badge>
            ) : null}
            {comparison.unknown_intervention_volumes ? (
              <Badge tone="caution">Unknown volume</Badge>
            ) : null}
            {comparison.event_milestone === "unknown" ? (
              <Badge tone="caution">Milestone unknown</Badge>
            ) : null}
            {comparison.reconciliation_state === "blocked_by_evidence" ? (
              <Badge tone="danger">
                {RECONCILIATION_LABELS.blocked_by_evidence}
              </Badge>
            ) : comparison.reconciliation_state === "complete" ? (
              <Badge tone="active">Reconciled</Badge>
            ) : null}
            {isPreEventTraceable(comparison) ? (
              <Badge tone="active">Source linked</Badge>
            ) : (
              <Badge tone="caution">No source record</Badge>
            )}
            {comparison.correction_count > 0 ? (
              <Badge tone="neutral">{comparison.correction_count} reconciled</Badge>
            ) : null}
          </div>
        </td>
      </tr>
      {caveats.length > 0 ? (
        <tr className="border-b border-line last:border-0 bg-inset/50">
          <td colSpan={9} className="px-4 py-2">
            <ul className="space-y-0.5">
              {caveats.map((caveat) => (
                <li key={caveat} className="text-[11px] leading-relaxed text-muted">
                  <span className="mr-1 text-caution">△</span>
                  {caveat}
                </li>
              ))}
            </ul>
          </td>
        </tr>
      ) : null}
      {showProvenance ? (
        <tr className="border-b border-line last:border-0 bg-inset/30">
          <td colSpan={9} className="px-0 py-0">
            <EventProvenancePanel comparison={comparison} onChanged={onChanged} />
          </td>
        </tr>
      ) : null}
    </>
  );
}

function SummaryPanel({ summary }: { summary: FieldValidationSummary | null }) {
  if (!summary) return null;
  const caveats = summary.caveats ?? [];
  if (caveats.length === 0) return null;
  return (
    <Card>
      <CardHeader
        title="Reading the averages"
        description="The |Δ| averages above are computed only over events where the comparison could be drawn."
      />
      <div className="px-5 py-4">
        <ul className="space-y-1.5">
          {caveats.map((caveat) => (
            <li key={caveat} className="text-[12px] leading-relaxed text-muted">
              — {caveat}
            </li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

const INTERVENTION_KIND_OPTIONS = (Object.keys(INTERVENTION_KIND_LABELS) as InterventionKind[]).map(
  (kind) => ({ value: kind, label: INTERVENTION_KIND_LABELS[kind] }),
);
const INTERVENTION_DIRECTION_OPTIONS = (
  Object.keys(INTERVENTION_DIRECTION_LABELS) as InterventionDirection[]
).map((direction) => ({
  value: direction,
  label: `Brine moved ${INTERVENTION_DIRECTION_LABELS[direction]}`,
}));

function ReportEventForm({
  panId,
  onFiled,
}: {
  panId: string;
  onFiled: () => void;
}) {
  const [pan, setPan] = useState(panId || "pan_orginal");
  const [name, setName] = useState("");
  const [eventTimestamp, setEventTimestamp] = useState(nowIso());
  const [eventMilestone, setEventMilestone] = useState<EventMilestone>("unknown");
  const [reportedBy, setReportedBy] = useState("");
  const [actualRainfall, setActualRainfall] = useState("");
  const [rainfallTimestamp, setRainfallTimestamp] = useState(nowIso());
  const [rainfallSource, setRainfallSource] = useState("");
  const [forecastRainfall, setForecastRainfall] = useState("");
  const [preSalinity, setPreSalinity] = useState("");
  const [preDepth, setPreDepth] = useState("");
  const [postSalinity, setPostSalinity] = useState("");
  const [postDepth, setPostDepth] = useState("");
  const [notes, setNotes] = useState("");

  const [withIntervention, setWithIntervention] = useState(false);
  const [interventionKind, setInterventionKind] = useState<InterventionKind>("brine_transfer_out");
  const [interventionDirection, setInterventionDirection] =
    useState<InterventionDirection>("out");
  const [interventionPerformedAt, setInterventionPerformedAt] = useState(nowIso());
  const [interventionVolume, setInterventionVolume] = useState("");
  const [interventionDescription, setInterventionDescription] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [filed, setFiled] = useState(false);

  async function submit() {
    setSubmitting(true);
    setFormError(null);
    setFieldErrors({});
    setFiled(false);
    try {
      const interventions = withIntervention
        ? [
            {
              kind: interventionKind,
              direction: interventionDirection,
              performed_at: interventionPerformedAt,
              volume_m3: interventionVolume === "" ? null : Number(interventionVolume),
              description: interventionDescription,
            },
          ]
        : [];
      await createFieldValidationEvent({
        pan_id: pan.trim(),
        name: name.trim(),
        event_timestamp: eventTimestamp,
        event_milestone: eventMilestone,
        reported_by: reportedBy.trim(),
        actual_rainfall_mm: Number(actualRainfall),
        rainfall_timestamp: rainfallTimestamp,
        rainfall_source: rainfallSource.trim(),
        forecast_rainfall_mm: forecastRainfall === "" ? null : Number(forecastRainfall),
        pre_salinity_g_l: preSalinity === "" ? null : Number(preSalinity),
        pre_depth_cm: preDepth === "" ? null : Number(preDepth),
        post_salinity_g_l: postSalinity === "" ? null : Number(postSalinity),
        post_depth_cm: postDepth === "" ? null : Number(postDepth),
        interventions,
        notes: notes.trim(),
      });
      setFiled(true);
      onFiled();
    } catch (caught) {
      if (caught instanceof ApiError) {
        setFormError(caught.message);
        setFieldErrors(caught.fields ?? {});
      } else {
        setFormError("The event could not be filed.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card>
      <CardHeader
        title="File a rainfall event"
        description="Store one reported event exactly as it arrived. Nothing here is verified: the comparison is recomputed from the stored figures on read, and the reported values are never rewritten."
        actions={
          <Button onClick={() => void submit()} disabled={submitting} variant="primary">
            {submitting ? <Spinner label="Filing" /> : "File event"}
          </Button>
        }
      />
      <div className="space-y-4 px-5 py-4">
        {formError ? <Alert tone="danger" title="Could not file the event"><p>{formError}</p></Alert> : null}
        {filed ? (
          <Alert tone="success" title="Event filed and compared">
            <p>
              The event is stored as reported, and the predicted-versus-reported
              comparison has been recomputed from it — including any flags the
              intervention raised.
            </p>
          </Alert>
        ) : null}

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <TextField
            label="Pan"
            value={pan}
            onChange={setPan}
            hint="Identifier of the registered salt pan."
            name="field-validation-pan"
            error={fieldErrors["pan_id"]}
          />
          <TextField
            label="Event name"
            value={name}
            onChange={setName}
            placeholder="Optional label, e.g. North Pan 14 Mar 2026"
            name="field-validation-name"
          />
          <TextField
            label="Event timestamp"
            value={eventTimestamp}
            onChange={setEventTimestamp}
            hint="ISO instant the rainfall happened."
            name="field-validation-event-timestamp"
            error={fieldErrors["event_timestamp"]}
          />
          <SelectField
            label="What this instant marks"
            value={eventMilestone}
            onChange={(value) => setEventMilestone(value as EventMilestone)}
            options={MILESTONE_OPTIONS}
            hint="Stays “Not established” if nobody can say. It is never guessed from the rain time."
            name="field-validation-event-milestone"
          />
          <TextField
            label="Reported by"
            value={reportedBy}
            onChange={setReportedBy}
            name="field-validation-reported-by"
            error={fieldErrors["reported_by"]}
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <NumberField
            label="Actual rainfall"
            value={actualRainfall}
            onChange={setActualRainfall}
            unit="mm"
            step="any"
            min={0}
            required
            name="field-validation-rainfall-mm"
            error={fieldErrors["actual_rainfall_mm"]}
          />
          <TextField
            label="Rainfall timestamp"
            value={rainfallTimestamp}
            onChange={setRainfallTimestamp}
            hint="ISO instant the rain fell."
            name="field-validation-rainfall-timestamp"
            error={fieldErrors["rainfall_timestamp"]}
          />
          <TextField
            label="Rainfall source"
            value={rainfallSource}
            onChange={setRainfallSource}
            placeholder="e.g. gauge, field report"
            name="field-validation-rainfall-source"
          />
          <NumberField
            label="Forecast rainfall (optional)"
            value={forecastRainfall}
            onChange={setForecastRainfall}
            unit="mm"
            step="any"
            min={0}
            required={false}
            hint="Context only; never used in the replay."
            name="field-validation-forecast-mm"
          />
        </div>

        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
            Pre-event snapshot
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <NumberField
              label="Salinity"
              value={preSalinity}
              onChange={setPreSalinity}
              unit="g/L"
              step="any"
              min={0}
              hint="Required for the balance to run."
              name="field-validation-pre-salinity"
              error={fieldErrors["pre_event.salinity_g_l"]}
            />
            <NumberField
              label="Water depth"
              value={preDepth}
              onChange={setPreDepth}
              unit="cm"
              step="any"
              min={0}
              hint="Required for the balance to run."
              name="field-validation-pre-depth"
              error={fieldErrors["pre_event.water_depth_cm"]}
            />
          </div>
        </div>

        <div>
          <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
            Post-event snapshot (what was measured)
          </p>
          <div className="grid gap-3 sm:grid-cols-2">
            <NumberField
              label="Salinity"
              value={postSalinity}
              onChange={setPostSalinity}
              unit="g/L"
              step="any"
              min={0}
              required={false}
              name="field-validation-post-salinity"
              error={fieldErrors["post_event.salinity_g_l"]}
            />
            <NumberField
              label="Water depth"
              value={postDepth}
              onChange={setPostDepth}
              unit="cm"
              step="any"
              min={0}
              required={false}
              name="field-validation-post-depth"
              error={fieldErrors["post_event.water_depth_cm"]}
            />
          </div>
        </div>

        <div className="flex items-center gap-2 border-t border-line pt-4">
          <input
            id="field-validation-intervention-toggle"
            type="checkbox"
            checked={withIntervention}
            onChange={(event) => setWithIntervention(event.target.checked)}
            className="rounded border-line-strong"
          />
          <label
            htmlFor="field-validation-intervention-toggle"
            className="text-[13px] font-medium text-ink-soft"
          >
            This event included an intervention (brine moved, fresh water, harvest…)
          </label>
        </div>

        {withIntervention ? (
          <div className="rounded-lg border border-line bg-inset/40 p-4">
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              <SelectField
                label="Kind"
                value={interventionKind}
                onChange={(value) => setInterventionKind(value as InterventionKind)}
                options={INTERVENTION_KIND_OPTIONS}
                name="field-validation-intervention-kind"
              />
              <SelectField
                label="Direction"
                value={interventionDirection}
                onChange={(value) => setInterventionDirection(value as InterventionDirection)}
                options={INTERVENTION_DIRECTION_OPTIONS}
                name="field-validation-intervention-direction"
              />
              <TextField
                label="Performed at"
                value={interventionPerformedAt}
                onChange={setInterventionPerformedAt}
                hint="ISO instant"
                name="field-validation-intervention-performed-at"
              />
              <NumberField
                label="Volume (optional)"
                value={interventionVolume}
                onChange={setInterventionVolume}
                unit="m³"
                step="any"
                min={0}
                required={false}
                hint="Omit when not recorded — never invent it."
                name="field-validation-intervention-volume"
              />
              <TextField
                label="Description"
                value={interventionDescription}
                onChange={setInterventionDescription}
                name="field-validation-intervention-description"
              />
            </div>
          </div>
        ) : null}

        <TextAreaField
          label="Notes"
          value={notes}
          onChange={setNotes}
          name="field-validation-notes"
          hint="Free text kept alongside the stored report."
        />
      </div>
    </Card>
  );
}