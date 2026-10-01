"use client";

/**
 * Module 20: the provenance, audit trail and reconciliation forms for one event.
 *
 * This panel is the operator's route into the three things the North Pan audit
 * turned on, and each of them is deliberately awkward to do casually:
 *
 * - **What the event instant marks.** An instant is meaningless until somebody
 *   says what it was a reading *of*. The milestone is stated here, or left as
 *   "not established" — never guessed from the rainfall time nearby.
 * - **Where each figure came from.** The pre-event snapshot is reconciled
 *   against a *stored* observation, and the form demands the original record's
 *   own local time, zone, observer and evidence before it will submit. The
 *   backend then refuses a link whose declared time disagrees with the stored
 *   instant, or that attaches an observation dated after the event — so the
 *   30 September reading cannot be passed off as a March pan's starting brine.
 * - **What changed, and why.** Every reconciliation is appended to the event's
 *   history with the previous value beside the new one. Nothing here edits a
 *   stored figure in place, and no field on this panel can mark the event
 *   independently verified.
 *
 * Instants are shown twice, in UTC and in the site's local zone, each labelled.
 * The browser never converts them: the backend publishes both renderings from the
 * single instant it stores, so the screen cannot disagree with the record.
 */

import { useState } from "react";
import { Badge, Card, CardHeader } from "@/components/ui/Primitives";
import { Button, Spinner } from "@/components/ui/Controls";
import { Alert } from "@/components/ui/Feedback";
import { SelectField, TextAreaField, TextField } from "@/components/ui/Fields";
import { ApiError } from "@/lib/api";
import {
  CORRECTABLE_FIELDS,
  CORRECTABLE_FIELD_LABELS,
  EVENT_MILESTONE_LABELS,
  RECONCILIATION_LABELS,
  correctionNeedsEvidence,
  instantPair,
  rainfallStanding,
  timestampStanding,
  type CorrectableField,
  type EventCorrectionDraft,
  type EventMilestone,
  type FieldValidationComparison,
  type PreEventLinkDraft,
  type RainfallConflictDraft,
  type RainfallResolutionDraft,
} from "@/lib/fieldValidation";
import {
  linkPreEventObservation,
  recordFieldValidationCorrection,
  recordRainfallTimestampConflict,
  resolveRainfallTimestamp,
} from "@/lib/fieldValidationApi";

const MILESTONE_OPTIONS = (
  Object.keys(EVENT_MILESTONE_LABELS) as EventMilestone[]
).map((value) => ({ value, label: EVENT_MILESTONE_LABELS[value] }));

const CORRECTABLE_OPTIONS = CORRECTABLE_FIELDS.map((value) => ({
  value,
  label: CORRECTABLE_FIELD_LABELS[value],
}));

type Instants = {
  label: string;
  utc: string | null;
  local: string | null;
  standing: string;
};

/**
 * The event's instants, one row each, in both zones with how each one stands.
 *
 * The stored instant is UTC; the local column is the backend's labelled
 * rendering of that same instant, and the standing line says whether a record
 * corroborates it or it is only asserted.
 */
export function EventInstantsTable({
  comparison,
}: {
  comparison: FieldValidationComparison;
}) {
  const instants: Instants[] = [
    {
      label: "Event instant",
      utc: comparison.event_timestamp_utc,
      local: comparison.event_timestamp_ist,
      standing: `marks: ${EVENT_MILESTONE_LABELS[comparison.event_milestone].toLowerCase()}`,
    },
    {
      label: "Rainfall",
      utc: comparison.rainfall_timestamp_utc,
      local: comparison.rainfall_timestamp_ist,
      // Lead with what backs the instant. A well-sourced operator report still is
      // not a measured reading, and the "no stored weather record" fact alone
      // would not say that.
      standing: rainfallStanding(comparison),
    },
    {
      label: "Pre-event measurement",
      utc: comparison.pre_event_timestamp_utc,
      local: comparison.pre_event_timestamp_ist,
      standing: timestampStanding(
        comparison.pre_event_timestamp_verified,
        comparison.pre_event_observation_id,
      ),
    },
    {
      label: "Post-event measurement",
      utc: comparison.post_event_timestamp_utc,
      local: comparison.post_event_timestamp_ist,
      standing: timestampStanding(
        comparison.post_event_timestamp_verified,
        comparison.post_event_observation_id,
      ),
    },
  ];

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[38rem] border-collapse text-[12px]">
        <thead>
          <tr className="border-b border-line">
            <th
              scope="col"
              className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-faint"
            >
              Instant
            </th>
            <th
              scope="col"
              className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-faint"
            >
              UTC (stored)
            </th>
            <th
              scope="col"
              className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-faint"
            >
              {comparison.site_timezone_label || "Local"}
            </th>
            <th
              scope="col"
              className="px-3 py-2 text-left text-[11px] font-semibold uppercase tracking-wide text-faint"
            >
              Standing
            </th>
          </tr>
        </thead>
        <tbody>
          {instants.map((instant) => {
            const pair = instantPair(instant.utc, instant.local, comparison.site_timezone_label);
            return (
              <tr key={instant.label} className="border-b border-line last:border-0">
                <th scope="row" className="px-3 py-2 text-left font-medium text-ink">
                  {instant.label}
                </th>
                <td className="tabular px-3 py-2 text-ink-soft">
                  {pair?.utc ?? "not recorded"}
                </td>
                <td className="tabular px-3 py-2 text-ink-soft">
                  {pair?.local ?? "not recorded"}
                </td>
                <td className="px-3 py-2 text-muted">{instant.standing}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] leading-relaxed text-muted">
        Both columns show the same stored instant. Nothing on this page converts a
        time: the stored value is UTC and the local column is a labelled rendering
        of it, so a time is never shifted twice.
      </p>
    </div>
  );
}

/**
 * The event's audit history, oldest entry first, in an order that cannot be
 * rewritten: the API only ever appends, and each entry carries the value that
 * was there before next to the value that is there now.
 */
export function EventAuditHistory({
  comparison,
}: {
  comparison: FieldValidationComparison;
}) {
  if (comparison.corrections.length === 0) {
    return (
      <p className="text-[12px] leading-relaxed text-muted">
        No reconciliation has been applied. The event stands exactly as reported.
      </p>
    );
  }
  return (
    <ol className="space-y-2">
      {comparison.corrections.map((entry) => (
        <li
          key={entry.correction_id}
          className="rounded-lg border border-line bg-inset/40 px-3 py-2"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">{entry.field}</Badge>
            <span className="text-[11px] text-faint">
              {entry.corrected_at}
              {entry.corrected_by ? ` · ${entry.corrected_by}` : ""}
            </span>
          </div>
          <p className="tabular mt-1 text-[12px] text-ink-soft">
            {entry.previous_value ?? "not recorded"} →{" "}
            <span className="font-semibold text-ink">{entry.new_value ?? "not recorded"}</span>
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-muted">{entry.reason}</p>
          {entry.evidence_source || entry.evidence_record_id ? (
            <p className="mt-1 text-[11px] leading-relaxed text-muted">
              Evidence: {entry.evidence_source ?? "—"}
              {entry.evidence_record_id ? ` (${entry.evidence_record_id})` : ""}
            </p>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

/**
 * Append one reconciliation step to the event's history.
 *
 * The previous value is read server-side from what is stored, so this form never
 * states what a field used to say. Restating a timestamp requires an evidence
 * source and still leaves the instant uncorroborated: only a stored record
 * corroborates a time, and only the linking form below can supply one.
 */
export function CorrectionForm({
  eventId,
  onRecorded,
}: {
  eventId: string;
  onRecorded: () => void;
}) {
  const [draft, setDraft] = useState<EventCorrectionDraft>({
    field: "event_milestone",
    reason: "",
    event_milestone: "unknown",
    timestamp: null,
    recorded_timezone: "IST",
    observer: "",
    evidence_source: "",
    corrected_by: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const isMilestone = draft.field === "event_milestone";
  const isTimestamp = correctionNeedsEvidence(draft.field);
  const isObserver = draft.field.endsWith("_observer");

  async function submit() {
    setSubmitting(true);
    setError(null);
    setDone(null);
    try {
      await recordFieldValidationCorrection({
        event_id: eventId,
        draft: {
          ...draft,
          event_milestone: isMilestone ? draft.event_milestone : null,
          timestamp: isTimestamp ? draft.timestamp : null,
          observer: draft.observer,
          evidence_source: draft.evidence_source,
        },
      });
      setDone(
        "Recorded. The previous value is now in the audit history; nothing was verified.",
      );
      setDraft((current) => ({ ...current, reason: "" }));
      onRecorded();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The correction could not be recorded.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-3">
      {error ? <Alert tone="danger" title="Not recorded"><p>{error}</p></Alert> : null}
      {done ? <Alert tone="success" title="Appended to the audit history"><p>{done}</p></Alert> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <SelectField
          label="What to reconcile"
          value={draft.field}
          onChange={(value) => setDraft({ ...draft, field: value as CorrectableField })}
          options={CORRECTABLE_OPTIONS}
          name={`field-correction-field-${eventId}`}
        />
        {isMilestone ? (
          <SelectField
            label="Event instant marks"
            value={draft.event_milestone ?? "unknown"}
            onChange={(value) => setDraft({ ...draft, event_milestone: value as EventMilestone })}
            options={MILESTONE_OPTIONS}
            hint="Leave as “not established” if nobody can say."
            name={`field-correction-milestone-${eventId}`}
          />
        ) : isTimestamp ? (
          <TextField
            label="Corrected instant (ISO, with offset)"
            value={draft.timestamp ?? ""}
            onChange={(value) => setDraft({ ...draft, timestamp: value || null })}
            placeholder="2026-03-15T09:00:00Z"
            name={`field-correction-timestamp-${eventId}`}
          />
        ) : isObserver ? (
          <TextField
            label="Observer"
            value={draft.observer}
            onChange={(value) => setDraft({ ...draft, observer: value })}
            name={`field-correction-observer-${eventId}`}
          />
        ) : null}
        {isTimestamp ? (
          <TextField
            label="Zone the record was written in"
            value={draft.recorded_timezone}
            onChange={(value) => setDraft({ ...draft, recorded_timezone: value })}
            placeholder="IST, Asia/Kolkata or UTC+05:30"
            name={`field-correction-zone-${eventId}`}
          />
        ) : null}
        {!isMilestone && !isObserver ? (
          <TextField
            label="Evidence source"
            value={draft.evidence_source}
            onChange={(value) => setDraft({ ...draft, evidence_source: value })}
            placeholder="Where the record lives"
            name={`field-correction-evidence-${eventId}`}
          />
        ) : null}
        <TextField
          label="Recorded by"
          value={draft.corrected_by}
          onChange={(value) => setDraft({ ...draft, corrected_by: value })}
          name={`field-correction-by-${eventId}`}
        />
      </div>
      <TextAreaField
        label="Reason"
        value={draft.reason}
        onChange={(value) => setDraft({ ...draft, reason: value })}
        hint="Why this reconciliation is being made. Kept in the history forever."
        name={`field-correction-reason-${eventId}`}
      />
      {isTimestamp ? (
        <p className="text-[11px] leading-relaxed text-caution">
          A restated time stays uncorroborated. Evidence is recorded with the entry,
          but only a stored record can corroborate an instant — use the linking form
          below for that.
        </p>
      ) : null}
      <Button
        variant="primary"
        onClick={() => void submit()}
        disabled={submitting || draft.reason.trim().length === 0}
      >
        {submitting ? <Spinner label="Recording" /> : "Record reconciliation"}
      </Button>
    </div>
  );
}

/**
 * Reconcile the pre-event snapshot against a stored observation.
 *
 * The form will not submit without the original record's own local time, the
 * zone it was written in, the observer and where the evidence lives, because
 * those four are what turn a reported figure into a traceable one. Nothing here
 * creates or backdates an observation: the reading must already be filed through
 * the observations module with its own original instant, and the backend checks
 * that the declared time agrees with it before linking anything.
 */
export function PreEventLinkForm({
  eventId,
  onLinked,
}: {
  eventId: string;
  onLinked: () => void;
}) {
  const [draft, setDraft] = useState<PreEventLinkDraft>({
    observation_id: "",
    measured_at_local: "",
    measured_timezone: "IST",
    observer: "",
    evidence_source: "",
    reason: "",
    corrected_by: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const complete =
    draft.observation_id.trim().length > 0 &&
    draft.measured_at_local.trim().length > 0 &&
    draft.measured_timezone.trim().length > 0 &&
    draft.observer.trim().length > 0 &&
    draft.evidence_source.trim().length > 0 &&
    draft.reason.trim().length > 0;

  async function submit() {
    setSubmitting(true);
    setError(null);
    setDone(null);
    try {
      const body = await linkPreEventObservation({ event_id: eventId, draft });
      setDone(
        `Linked ${body.pre_event_observation_id}. The snapshot's instant is now corroborated by that record.`,
      );
      setDraft({
        observation_id: "",
        measured_at_local: "",
        measured_timezone: "IST",
        observer: "",
        evidence_source: "",
        reason: "",
        corrected_by: "",
      });
      onLinked();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The observation could not be linked.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-3">
      {error ? <Alert tone="danger" title="Not linked"><p>{error}</p></Alert> : null}
      {done ? <Alert tone="success" title="Linked"><p>{done}</p></Alert> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label="Stored observation ID"
          value={draft.observation_id}
          onChange={(value) => setDraft({ ...draft, observation_id: value })}
          placeholder="obs-…"
          hint="Must already be filed, on this pan, dated before the rain."
          name={`field-link-observation-${eventId}`}
        />
        <TextField
          label="Measurement time on the original record"
          value={draft.measured_at_local}
          onChange={(value) => setDraft({ ...draft, measured_at_local: value })}
          placeholder="2026-03-13T13:30"
          hint="Written exactly as the record writes it."
          name={`field-link-measured-at-${eventId}`}
        />
        <TextField
          label="Time zone of that record"
          value={draft.measured_timezone}
          onChange={(value) => setDraft({ ...draft, measured_timezone: value })}
          placeholder="IST, Asia/Kolkata or UTC+05:30"
          name={`field-link-zone-${eventId}`}
        />
        <TextField
          label="Observer"
          value={draft.observer}
          onChange={(value) => setDraft({ ...draft, observer: value })}
          name={`field-link-observer-${eventId}`}
        />
        <TextField
          label="Evidence source"
          value={draft.evidence_source}
          onChange={(value) => setDraft({ ...draft, evidence_source: value })}
          placeholder="Field log page, gauge export, lab sheet…"
          name={`field-link-evidence-${eventId}`}
        />
        <TextField
          label="Recorded by"
          value={draft.corrected_by}
          onChange={(value) => setDraft({ ...draft, corrected_by: value })}
          name={`field-link-by-${eventId}`}
        />
      </div>
      <TextAreaField
        label="Reason"
        value={draft.reason}
        onChange={(value) => setDraft({ ...draft, reason: value })}
        hint="Why this record documents this event's pre-event reading."
        name={`field-link-reason-${eventId}`}
      />
      <Button
        variant="primary"
        onClick={() => void submit()}
        disabled={submitting || !complete}
      >
        {submitting ? <Spinner label="Linking" /> : "Link observation"}
      </Button>
      <p className="text-[11px] leading-relaxed text-muted">
        The link is checked, not assumed: an observation dated after this
        event&apos;s rainfall is refused, a record whose time disagrees with the
        stored instant is refused, and figures that disagree are refused rather
        than averaged. The event itself stays reported and unverified either way.
      </p>
    </div>
  );
}

/**
 * Every preserved disagreement about when the rain fell.
 *
 * The point of this block is that it shows two timestamps rather than one. The
 * event keeps the instant it was filed with — that is what its stored replay was
 * run against — and the second account sits beside it with its source and the
 * size of the disagreement, until somebody resolves it against evidence.
 */
export function RainfallConflictList({
  comparison,
}: {
  comparison: FieldValidationComparison;
}) {
  if (comparison.rainfall_timestamp_conflicts.length === 0) {
    return (
      <p className="text-[12px] leading-relaxed text-muted">
        No competing account of the rain&apos;s timing has been recorded.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {comparison.rainfall_timestamp_conflicts.map((conflict) => (
        <li
          key={conflict.conflict_id}
          className="rounded-lg border border-caution/40 bg-caution-soft/30 px-3 py-2"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={conflict.status === "unresolved" ? "caution" : "active"}>
              {conflict.status === "unresolved"
                ? "Unresolved conflict"
                : "Resolved"}
            </Badge>
            <span className="text-[11px] text-faint">
              differing by {conflict.offset_human} · recorded {conflict.detected_at}
              {conflict.recorded_by ? ` by ${conflict.recorded_by}` : ""}
            </span>
          </div>
          <dl className="mt-2 grid gap-x-4 gap-y-1 text-[12px] sm:grid-cols-2">
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-muted">
                In force (stored)
              </dt>
              <dd className="tabular text-ink-soft">
                {conflict.stored_timestamp_utc} · {conflict.stored_timestamp_ist}
              </dd>
            </div>
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-muted">
                Reported by {conflict.reported_source}
              </dt>
              <dd className="tabular text-ink-soft">
                {conflict.reported_timestamp_utc} · {conflict.reported_timestamp_ist}
              </dd>
            </div>
          </dl>
          <p className="mt-2 text-[11px] leading-relaxed text-muted">
            {conflict.reason}
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-caution">
            {conflict.status === "unresolved" ? (
              <>
                The stored timestamp has not been changed. This event cannot be
                reported as reconciled until the disagreement is resolved against
                evidence.
              </>
            ) : (
              <>
                Resolved: the event now carries the instant this record reported.
                {conflict.resolution_note
                  ? ` ${conflict.resolution_note}`
                  : ""}
              </>
            )}
          </p>
        </li>
      ))}
    </ul>
  );
}

/**
 * Rainfall instants this event used to carry.
 *
 * Rendered as history and never as a second measurement. The wording is doing
 * real work: a displaced time shown on its own, in a table of instants, would be
 * read as another reading of when the rain fell. So each entry leads with the
 * fact that nothing supported it, says its meaning is unknown, and names where
 * the value came from — which is what turns it from a number into an audit note.
 */
export function SupersededRainfallList({
  comparison,
}: {
  comparison: FieldValidationComparison;
}) {
  if (comparison.superseded_rainfall_timestamps.length === 0) {
    return null;
  }
  return (
    <ul className="space-y-2">
      {comparison.superseded_rainfall_timestamps.map((displaced) => (
        <li
          key={displaced.superseded_id}
          className="rounded-lg border border-line bg-faint/30 px-3 py-2"
        >
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="neutral">Superseded</Badge>
            <span className="tabular text-[12px] text-muted line-through">
              {displaced.timestamp_utc} · {displaced.timestamp_ist}
            </span>
          </div>
          <dl className="mt-2 grid gap-x-4 gap-y-1 text-[12px] sm:grid-cols-2">
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-muted">
                Supporting record
              </dt>
              <dd className="text-ink-soft">
                {displaced.supporting_record ?? "none — nothing in the system supported it"}
              </dd>
            </div>
            <div>
              <dt className="text-[11px] uppercase tracking-wide text-muted">
                What it marked
              </dt>
              <dd className="text-ink-soft">unknown</dd>
            </div>
          </dl>
          <p className="mt-2 text-[11px] leading-relaxed text-muted">
            <span className="text-faint">Entered via:</span> {displaced.origin}
            {displaced.source_claim
              ? ` · carried the label “${displaced.source_claim}”, which was a claim about provenance and not evidence for it`
              : ""}
            . This is audit history, not a measurement: it is not replayed and
            nothing reads it as a second reading of when the rain fell.
          </p>
          <p className="mt-1 text-[11px] leading-relaxed text-faint">
            Superseded {displaced.superseded_at}
            {displaced.superseded_by ? ` by ${displaced.superseded_by}` : ""}:{" "}
            {displaced.reason}
          </p>
        </li>
      ))}
    </ul>
  );
}

/**
 * Record a competing account of when the rain fell.
 *
 * The form cannot restate the stored timestamp and does not pretend to: it
 * asks for the other account, its source and why it is being raised, and the
 * event keeps what it has while the disagreement is put on the record.
 */
export function RainfallConflictForm({
  eventId,
  onRecorded,
}: {
  eventId: string;
  onRecorded: () => void;
}) {
  const [draft, setDraft] = useState<RainfallConflictDraft>({
    reported_timestamp: "",
    reported_timezone: "IST",
    reported_source: "",
    reason: "",
    recorded_by: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const complete =
    draft.reported_timestamp.trim().length > 0 &&
    draft.reported_source.trim().length > 0 &&
    draft.reason.trim().length > 0;

  async function submit() {
    setSubmitting(true);
    setError(null);
    setDone(null);
    try {
      const body = await recordRainfallTimestampConflict({
        event_id: eventId,
        draft,
      });
      setDone(
        `Recorded. Both accounts are on the record and this event now reports ` +
          `${RECONCILIATION_LABELS[body.reconciliation_state].toLowerCase()}.`,
      );
      setDraft({
        reported_timestamp: "",
        reported_timezone: "IST",
        reported_source: "",
        reason: "",
        recorded_by: "",
      });
      onRecorded();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The conflict could not be recorded.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-3">
      {error ? <Alert tone="danger" title="Not recorded"><p>{error}</p></Alert> : null}
      {done ? <Alert tone="success" title="Both accounts preserved"><p>{done}</p></Alert> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label="When the rain fell, as the other source dates it"
          value={draft.reported_timestamp}
          onChange={(value) => setDraft({ ...draft, reported_timestamp: value })}
          placeholder="2026-03-14T23:30"
          hint="The local clock reading, with the zone below."
          name={`field-conflict-reported-at-${eventId}`}
        />
        <TextField
          label="Time zone of that source"
          value={draft.reported_timezone}
          onChange={(value) => setDraft({ ...draft, reported_timezone: value })}
          placeholder="IST, Asia/Kolkata or UTC+05:30"
          name={`field-conflict-zone-${eventId}`}
        />
        <TextField
          label="Where that account comes from"
          value={draft.reported_source}
          onChange={(value) => setDraft({ ...draft, reported_source: value })}
          placeholder="North Pan field notebook"
          name={`field-conflict-source-${eventId}`}
        />
        <TextField
          label="Recorded by"
          value={draft.recorded_by}
          onChange={(value) => setDraft({ ...draft, recorded_by: value })}
          name={`field-conflict-by-${eventId}`}
        />
      </div>
      <TextAreaField
        label="Reason"
        value={draft.reason}
        onChange={(value) => setDraft({ ...draft, reason: value })}
        hint="Why these two accounts disagree and what evidence is being asked for."
        name={`field-conflict-reason-${eventId}`}
      />
      <Button
        variant="primary"
        onClick={() => void submit()}
        disabled={submitting || !complete}
      >
        {submitting ? <Spinner label="Recording" /> : "Record the conflict"}
      </Button>
      <p className="text-[11px] leading-relaxed text-muted">
        This does not restate the stored rainfall timestamp. The event keeps the
        instant it was filed with, because that is what its replay was run
        against; recording the other account blocks it from being reported as
        reconciled until the disagreement is settled against evidence.
      </p>
    </div>
  );
}

/**
 * Move the rainfall instant onto evidence, superseding the value it replaces.
 *
 * The form is built around the two things it makes the operator account for: the
 * document that dates the new instant, and the origin of the one being displaced.
 * There is no "mark as verified" control, and the trailing note says why — the
 * absence is the guarantee, so a reader is not left wondering whether marking it
 * measured was simply forgotten.
 */
export function RainfallResolutionForm({
  eventId,
  currentUtc,
  onResolved,
}: {
  eventId: string;
  currentUtc: string | null;
  onResolved: () => void;
}) {
  const [draft, setDraft] = useState<RainfallResolutionDraft>({
    timestamp: "",
    timezone: "IST",
    evidence_source: "",
    superseded_origin: "",
    superseded_source_claim: "",
    superseded_supporting_record: "",
    reason: "",
    corrected_by: "",
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const complete =
    draft.timestamp.trim().length > 0 &&
    draft.timezone.trim().length > 0 &&
    draft.evidence_source.trim().length > 0 &&
    draft.superseded_origin.trim().length > 0 &&
    draft.reason.trim().length > 0;

  async function submit() {
    setSubmitting(true);
    setError(null);
    setDone(null);
    try {
      const body = await resolveRainfallTimestamp({ event_id: eventId, draft });
      setDone(
        `Adopted. The event now reports ` +
          `${RECONCILIATION_LABELS[body.reconciliation_state].toLowerCase()}, and the ` +
          `earlier instant is retained above as superseded history.`,
      );
      setDraft({
        timestamp: "",
        timezone: "IST",
        evidence_source: "",
        superseded_origin: "",
        superseded_source_claim: "",
        superseded_supporting_record: "",
        reason: "",
        corrected_by: "",
      });
      onResolved();
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.message
          : "The instant could not be moved.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-3">
      {error ? <Alert tone="danger" title="Not applied"><p>{error}</p></Alert> : null}
      {done ? <Alert tone="success" title="Instant moved onto evidence"><p>{done}</p></Alert> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField
          label="When the rain fell, as the evidence dates it"
          value={draft.timestamp}
          onChange={(value) => setDraft({ ...draft, timestamp: value })}
          placeholder="2026-03-14T23:30"
          hint="The local clock reading, with the zone beside it."
          name={`field-resolution-at-${eventId}`}
        />
        <TextField
          label="Time zone it was written in"
          value={draft.timezone}
          onChange={(value) => setDraft({ ...draft, timezone: value })}
          placeholder="IST, Asia/Kolkata or UTC+05:30"
          name={`field-resolution-zone-${eventId}`}
        />
        <TextField
          label="The document that dates it"
          value={draft.evidence_source}
          onChange={(value) => setDraft({ ...draft, evidence_source: value })}
          placeholder="North Pan field notebook"
          name={`field-resolution-evidence-${eventId}`}
        />
        <TextField
          label="Corrected by"
          value={draft.corrected_by}
          onChange={(value) => setDraft({ ...draft, corrected_by: value })}
          name={`field-resolution-by-${eventId}`}
        />
      </div>
      <div className="rounded-lg border border-line bg-faint/30 px-3 py-2">
        <p className="text-[11px] leading-relaxed text-muted">
          The instant being replaced
          {currentUtc ? (
            <>
              {" "}
              (<span className="tabular text-ink-soft">{currentUtc}</span>)
            </>
          ) : null}{" "}
          is not deleted. Say where it came from, and it is retained as superseded
          history with its meaning left unknown.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <TextField
            label="How the earlier value entered the record"
            value={draft.superseded_origin}
            onChange={(value) => setDraft({ ...draft, superseded_origin: value })}
            placeholder="a seed script constant, an API call"
            name={`field-resolution-origin-${eventId}`}
          />
          <TextField
            label="The label it carried, if any"
            value={draft.superseded_source_claim}
            onChange={(value) =>
              setDraft({ ...draft, superseded_source_claim: value })
            }
            placeholder="e.g. manual rain gauge"
            hint="Kept as a claim about provenance, never as evidence for it."
            name={`field-resolution-claim-${eventId}`}
          />
          <TextField
            label="A stored record that supported it"
            value={draft.superseded_supporting_record}
            onChange={(value) =>
              setDraft({ ...draft, superseded_supporting_record: value })
            }
            hint="Leave empty when nothing did, which is the usual case."
            name={`field-resolution-support-${eventId}`}
          />
        </div>
      </div>
      <TextAreaField
        label="Why the earlier value was set aside"
        value={draft.reason}
        onChange={(value) => setDraft({ ...draft, reason: value })}
        hint="What is wrong with the old instant, and why this document settles it."
        name={`field-resolution-reason-${eventId}`}
      />
      <Button
        variant="primary"
        onClick={() => void submit()}
        disabled={submitting || !complete}
      >
        {submitting ? <Spinner label="Applying" /> : "Adopt this instant"}
      </Button>
      <p className="text-[11px] leading-relaxed text-muted">
        This cannot mark the instant as measured. A document that records a time
        does not measure one, so the instant stays operator-reported and
        &ldquo;instrument-verified&rdquo; stays no however well the source is
        cited — the API refuses the claim rather than storing it.
      </p>
    </div>
  );
}

/** The whole per-event provenance surface, opened from one dashboard row. */
export function EventProvenancePanel({
  comparison,
  onChanged,
}: {
  comparison: FieldValidationComparison;
  onChanged: () => void;
}) {
  return (
    <div className="space-y-4 px-4 pb-4 pt-1">
      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
          Instants, in both zones
        </p>
        <EventInstantsTable comparison={comparison} />
      </div>

      {comparison.rainfall_timestamp_conflicts.length > 0 ? (
        <div>
          <p
            className={`mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] ${
              comparison.unresolved_conflict_count > 0 ? "text-caution" : "text-muted"
            }`}
          >
            {comparison.unresolved_conflict_count > 0
              ? "Evidence conflict — this event is blocked"
              : "Recorded disagreement about the rain's timing — resolved"}
          </p>
          {/* Shown whether or not it is resolved: a closed conflict is the reason
              the instant moved, so hiding it once resolved would delete the
              explanation for the correction from the audit surface. */}
          <RainfallConflictList comparison={comparison} />
        </div>
      ) : null}

      <SupersededRainfallList comparison={comparison} />

      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-muted">
          Audit history ({comparison.correction_count}{" "}
          {comparison.correction_count === 1 ? "entry" : "entries"})
        </p>
        <EventAuditHistory comparison={comparison} />
      </div>

      <Card className="shadow-none">
        <CardHeader
          title="Record a reconciliation"
          description="Appended to the history above with the previous value beside the new one. Nothing is edited in place and nothing becomes verified."
        />
        <div className="px-5 py-4">
          <CorrectionForm eventId={comparison.event_id} onRecorded={onChanged} />
        </div>
      </Card>

      <Card className="shadow-none">
        <CardHeader
          title="Link a genuine pre-event record"
          description="Attach the stored observation that documents this event's starting brine, together with the original record's own time, zone, observer and evidence."
        />
        <div className="px-5 py-4">
          <PreEventLinkForm eventId={comparison.event_id} onLinked={onChanged} />
        </div>
      </Card>

      <Card className="shadow-none">
        <CardHeader
          title="Record a conflict about when it rained"
          description="For when a second source dates the rain differently from the instant this event was filed with."
        />
        <div className="px-5 py-4">
          <RainfallConflictForm eventId={comparison.event_id} onRecorded={onChanged} />
        </div>
      </Card>

      <Card className="shadow-none">
        <CardHeader
          title="Move the rainfall instant onto evidence"
          description="For when a document settles which of two recorded times is right. The value it replaces is superseded, not overwritten, so both stay auditable."
        />
        <div className="px-5 py-4">
          <RainfallResolutionForm
            eventId={comparison.event_id}
            currentUtc={comparison.rainfall_timestamp_utc}
            onResolved={onChanged}
          />
        </div>
      </Card>
    </div>
  );
}