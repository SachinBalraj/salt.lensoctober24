"""Module 20 - user-reported rainfall events, replayed against the rainfall.

The service files a field report as it arrived and, on read, replays the
rainfall-only mass balance (Module 6) against the report's actual rainfall and
starting brine. The comparison is a coarse check, deliberately: it reports the
predicted salinity and depth next to what was reported, in each unit, with the
absolute difference, and it is honest about what those numbers cannot claim.

The replay is recomputed on every read from the frozen inputs inside the event
document, never from the live twin — the twin can change, and a field report
must compare against the arithmetic of *its own* numbers. A stored observation
or weather record may be linked by id for traceability, but the event does not
depend on them.

Nothing written here is ever marked verified. ``verification_state`` stays
``reported`` for every event this module files, and ``independently_verified``
is derived from it — so a report can never claim independent proof it does not
have. A person can later mark an event verified through the API, and only then
do the flags change.

Two rules follow from the audit of one filed event, which found a stored instant
with no stated meaning and a starting brine with no traceable record:

* Nothing is edited in place. ``record_correction`` appends to the event's
  audit history, carrying the value that was there before next to the value that
  is there now, so a reconciled figure stays visibly distinct from a reported
  one and the earlier value is never lost.
* A pre-event snapshot cannot be made traceable by assertion. Only a *stored
  observation* that predates the rainfall may be linked to it, and the operator
  must also state the original record's local time, zone, observer and evidence —
  which must agree with the stored instant. An observation dated after the event
  is refused outright, so a same-pan observation from a different month can never
  be passed off as this event's starting brine.

Instants are rendered twice in every output, once in UTC and once in the site's
local zone, each labelled. A single converted number hides how it was derived
and invites a reader to convert it twice.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status

from app.db.errors import DocumentNotFoundError
from app.db.indexes import PAN_OBSERVATIONS, RAINFALL_EVENTS, SALT_PANS
from app.db.repository import MongoRepository
from app.models.base import normalize_pan_id, utcnow
from app.models.field_validation import (
    CorrectionRecord,
    EventMilestone,
    FieldIntervention,
    FieldMeasurement,
    RainfallEvent,
    RainfallTimestampConflict,
    RainfallTimestampEvidence,
    SupersededRainfallTimestamp,
    VerificationState,
)
from app.models.pan_observation import PanObservation
from app.models.salt_pan import SaltPan
from app.schemas.field_validation import (
    EventCorrectionCreate,
    FieldInterventionCreate,
    FieldValidationComparison,
    FieldValidationSummary,
    PreEventObservationLink,
    RainfallEventCreate,
    RainfallTimestampConflictCreate,
    RainfallTimestampConflictRead,
    RainfallTimestampResolutionCreate,
    StatusReport,
    SupersededRainfallTimestampRead,
)
from app.services.csv_security import sanitize_prose_cell
from app.services.mass_balance import (
    MASS_BALANCE_MODEL_NAME,
    SIMULATION_ASSUMPTIONS,
    SIMULATOR_DISCLAIMER,
    MassBalanceService,
)

#: Columns of the CSV export, in display order.
EXPORT_COLUMNS = (
    "event_id",
    "pan_id",
    "name",
    "event_timestamp",
    "event_milestone",
    "site_timezone_label",
    "event_timestamp_utc",
    "event_timestamp_ist",
    "actual_rainfall_mm",
    "forecast_rainfall_mm",
    "rainfall_timestamp_utc",
    "rainfall_timestamp_ist",
    "rainfall_timestamp_evidence",
    "rainfall_timestamp_verification",
    "rainfall_timestamp_observed",
    "superseded_rainfall_timestamps",
    "unresolved_rainfall_conflicts",
    "reconciliation_state",
    "pre_event_salinity_g_l",
    "pre_event_depth_cm",
    "pre_event_timestamp_utc",
    "pre_event_timestamp_ist",
    "pre_event_observation_id",
    "pre_event_observer",
    "pre_event_timestamp_verified",
    "reported_salinity_g_l",
    "reported_depth_cm",
    "post_event_timestamp_utc",
    "post_event_timestamp_ist",
    "post_event_observation_id",
    "post_event_observer",
    "post_event_timestamp_verified",
    "salinity_abs_diff_g_l",
    "depth_abs_diff_cm",
    "predicted_salinity_g_l",
    "predicted_depth_cm",
    "weather_observation_id",
    "intervention_affected",
    "unknown_intervention_volumes",
    "independently_verified",
    "verification_state",
    "correction_count",
    "reported_by",
    "notes",
)

#: Metadata lines written at the top of the PDF report, under the title.
PDF_TITLE = "SaltLens Field Validation"
PDF_SUBTITLE = "Reported rainfall events replayed against the rainfall-only mass balance"

#: The zone the project's pans are worked in. Used only for presentation: the
#: stored instant is always UTC, and the label travels with every rendered local
#: time so a reader is never left to assume one.
SITE_TIMEZONE = ZoneInfo("Asia/Kolkata")
SITE_TIMEZONE_LABEL = "IST (UTC+05:30)"

#: Time zones accepted by the correction and linking forms, beyond the IANA
#: names ``zoneinfo`` knows.
_FIXED_OFFSET_ZONES = {"IST": 5.5, "UTC": 0.0, "GMT": 0.0, "Z": 0.0}
_OFFSET_PATTERN = re.compile(
    r"^(?:utc|gmt)?\s*(?P<sign>[+-])\s*(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?$",
    re.IGNORECASE,
)

#: How closely an operator's stated original record must agree with the stored
#: observation it is meant to document. Sixty seconds covers a paper log written
#: to the minute against a timestamp that carries seconds; nothing wider is
#: tolerated, because the point is to confirm a record, not to reshape one.
LINK_TIME_TOLERANCE_SECONDS = 60.0

#: A mid-year instant used only to ask a zone what offset it observes, so a
#: label like "UTC+05:30" can be printed for whatever zone an event declares.
_REFERENCE_INSTANT = datetime(2026, 1, 1, tzinfo=timezone.utc)
LINK_SALINITY_TOLERANCE_G_L = 0.05
LINK_DEPTH_TOLERANCE_CM = 0.5


def _as_latin1(value: object) -> str:
    """Coerce any string to the Latin-1 subset fpdf2 can typeset.

    fpdf2's default font is Latin-1, and an unreplaceable character would raise
    in the middle of a long report. Replacing the rare non-Latin-1 character
    with ``?`` is preferable to dropping the report.
    """
    return str(value).encode("latin-1", errors="replace").decode("latin-1")


def resolve_timezone(label: str) -> timezone | ZoneInfo:
    """Turn a written zone label into a real offset, or explain it cannot.

    Accepted: the IANA name the site uses (``Asia/Kolkata``), ``IST``/``UTC``/
    ``GMT``, and a fixed offset written any of the ways an operator writes one
    (``UTC+05:30``, ``+0530``, ``-04:00``). Anything else is refused rather than
    guessed, because a misread zone silently moves a measurement by hours.
    """
    text = label.strip()
    if not text:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "A time zone is required, written as an IANA name "
                "(Asia/Kolkata), an abbreviation (IST) or an offset (UTC+05:30)."
            ),
        )
    upper = text.upper()
    if upper in _FIXED_OFFSET_ZONES:
        return timezone(timedelta(hours=_FIXED_OFFSET_ZONES[upper]))
    match = _OFFSET_PATTERN.match(text)
    if match:
        hours = int(match.group("hours"))
        minutes = int(match.group("minutes") or 0)
        delta = timedelta(hours=hours, minutes=minutes)
        return timezone(-delta if match.group("sign") == "-" else delta)
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"“{label}” is not a time zone this service can resolve. Use an "
                "IANA name (Asia/Kolkata), an abbreviation (IST) or an offset "
                "(UTC+05:30)."
            ),
        ) from exc


def parse_local_instant(value: str, zone: timezone | ZoneInfo) -> datetime:
    """Read a local clock reading off a record and place it on the timeline.

    A naive reading takes the declared zone. A reading that carries its own
    offset is accepted only when that offset is the declared zone's own — if the
    two disagree, the record is ambiguous about which clock it used, and
    guessing would be exactly the double conversion this module avoids.
    """
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"“{value}” is not a readable date and time. Write it as it appears "
                "on the record, e.g. 2026-03-13T13:30."
            ),
        ) from exc
    if parsed.tzinfo is not None:
        declared = parsed.replace(tzinfo=zone).utcoffset()
        if declared != parsed.utcoffset():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"“{value}” carries the offset {parsed.utcoffset()}, which is not "
                    f"the offset of the declared zone {_zone_label(zone)}. Restate it "
                    "as the local clock reading, or declare the zone it was written in."
                ),
            )
        return parsed
    return parsed.replace(tzinfo=zone).astimezone(timezone.utc)


def _zone_label(zone: timezone | ZoneInfo) -> str:
    """Name an offset the way a form would show it."""
    offset = zone.utcoffset(_REFERENCE_INSTANT)
    if offset is None:
        return "local time"
    if offset == timedelta(0):
        return "UTC+00:00"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    return f"UTC{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"


def _instant_text(instant: datetime | None) -> str | None:
    """Render an instant for the audit history, or say plainly that there is none."""
    if instant is None:
        return None
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _measurement_summary(measurement: FieldMeasurement) -> str:
    """One-line rendering of a snapshot, so a history entry stays readable."""
    salinity = (
        f"{measurement.salinity_g_l:g} g/L"
        if measurement.salinity_g_l is not None
        else "no salinity"
    )
    depth = (
        f"{measurement.water_depth_cm:g} cm"
        if measurement.water_depth_cm is not None
        else "no depth"
    )
    stamp = _instant_text(measurement.timestamp) or "no timestamp"
    verified = "corroborated" if measurement.timestamp_verified else "uncorroborated"
    return f"timestamp={stamp}; salinity={salinity}; depth={depth}; {verified}"


def _humanise_offset(seconds: int) -> str:
    """Say by how much two accounts differ, in the units a person would use."""
    magnitude = abs(seconds)
    sign = "later" if seconds > 0 else "earlier"
    if magnitude % 86_400 == 0 and magnitude >= 86_400:
        days = magnitude // 86_400
        return f"{days} day{'s' if days != 1 else ''} {sign}"
    hours, remainder = divmod(magnitude, 3_600)
    minutes = remainder // 60
    parts = [f"{hours} h"] if hours else []
    if minutes:
        parts.append(f"{minutes} min")
    return f"{' '.join(parts) or '0 min'} {sign}"


def _compare_values(
    label: str,
    reported: float | None,
    observed: float | None,
    unit: str,
    tolerance: float,
) -> str | None:
    """Describe how two figures differ, or return ``None`` when they agree.

    A missing figure on either side is reported rather than treated as agreement:
    a record that measured only salinity cannot vouch for a depth.
    """
    if reported is None or observed is None:
        if reported is None and observed is None:
            return None
        missing = "depth" if label == "depth" else "salinity"
        return f"{missing} is recorded in only one of the two"
    difference = abs(reported - observed)
    if difference <= tolerance:
        return None
    return f"{label} differs by {difference:g} {unit} (snapshot {reported:g}, observation {observed:g})"


class FieldValidationService:
    """Filing, listing and replaying reported rainfall events."""

    def __init__(self, database: Any) -> None:
        self._database = database
        self._mass_balance = MassBalanceService()
        self._resolved_events: MongoRepository[RainfallEvent] | None = None
        self._resolved_pans: MongoRepository[SaltPan] | None = None
        self._resolved_observations: MongoRepository[PanObservation] | None = None

    def _event_repository(self) -> MongoRepository[RainfallEvent]:
        if self._resolved_events is None:
            self._resolved_events = MongoRepository(
                self._database, RAINFALL_EVENTS, RainfallEvent
            )
        return self._resolved_events

    def _pan_repository(self) -> MongoRepository[SaltPan]:
        if self._resolved_pans is None:
            self._resolved_pans = MongoRepository(self._database, SALT_PANS, SaltPan)
        return self._resolved_pans

    def _observation_repository(self) -> MongoRepository[PanObservation]:
        if self._resolved_observations is None:
            self._resolved_observations = MongoRepository(
                self._database, PAN_OBSERVATIONS, PanObservation
            )
        return self._resolved_observations

    def _conflict_reads(
        self, event: RainfallEvent, zone: timezone | ZoneInfo
    ) -> list[RainfallTimestampConflictRead]:
        """Render every preserved rainfall-timestamp disagreement for an event."""
        reads: list[RainfallTimestampConflictRead] = []
        for conflict in event.rainfall_timestamp_conflicts:
            stored_utc, stored_local = self._render_instant(
                conflict.stored_timestamp, zone
            )
            reported_utc, reported_local = self._render_instant(
                conflict.reported_timestamp, zone
            )
            reads.append(
                RainfallTimestampConflictRead(
                    conflict_id=conflict.conflict_id,
                    stored_timestamp_utc=stored_utc,
                    stored_timestamp_ist=stored_local,
                    reported_timestamp_utc=reported_utc,
                    reported_timestamp_ist=reported_local,
                    reported_source=conflict.reported_source,
                    offset_seconds=conflict.offset_seconds,
                    offset_human=_humanise_offset(conflict.offset_seconds),
                    status=conflict.status,
                    detected_at=_instant_text(conflict.detected_at) or "",
                    recorded_by=conflict.recorded_by,
                    reason=conflict.reason,
                    resolution_note=conflict.resolution_note,
                    resolved_at=(
                        _instant_text(conflict.resolved_at)
                        if conflict.resolved_at is not None
                        else None
                    ),
                )
            )
        return reads

    def _render_superseded(
        self, event: RainfallEvent, zone: timezone | ZoneInfo
    ) -> list[SupersededRainfallTimestampRead]:
        """Render each displaced instant, with its meaning and its support.

        The meaning and the absent supporting record travel with the value
        deliberately. A displaced instant rendered as a bare timestamp would be
        read as a second measurement of when the rain fell, which is exactly the
        misreading the supersession exists to prevent.
        """
        reads: list[SupersededRainfallTimestampRead] = []
        for superseded in event.superseded_rainfall_timestamps:
            utc_text, local_text = self._render_instant(superseded.timestamp, zone)
            reads.append(
                SupersededRainfallTimestampRead(
                    superseded_id=superseded.superseded_id,
                    timestamp_utc=utc_text,
                    timestamp_ist=local_text,
                    timezone_label=superseded.timezone_label,
                    source_claim=superseded.source_claim,
                    origin=superseded.origin,
                    supporting_record=superseded.supporting_record,
                    meaning=superseded.meaning,
                    superseded_at=_instant_text(superseded.superseded_at) or "",
                    superseded_by=superseded.superseded_by,
                    reason=superseded.reason,
                    conflict_id=superseded.conflict_id,
                )
            )
        return reads

    @staticmethod
    def _reconciliation_state(event: RainfallEvent) -> str:
        """How far an event is from being honestly called reconciled.

        An unresolved evidence conflict outranks everything: while two records
        disagree and only one is in force, the event is blocked, whatever else
        happens to be in place. Otherwise it is complete only when the milestone
        is established, the pre-event snapshot traces to a stored record, and
        that link's instant is corroborated.
        """
        if event.unresolved_rainfall_timestamp_conflicts:
            return "blocked_by_evidence"
        outstanding = (
            event.event_milestone is EventMilestone.UNKNOWN
            or event.pre_event.observation_id is None
            or not event.pre_event.timestamp_verified
        )
        return "incomplete" if outstanding else "complete"

    @staticmethod
    def _normalized_pan_id(pan_id: str) -> str:
        normalized = normalize_pan_id(pan_id)
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="pan_id must not be blank.",
            )
        return normalized

    async def _require_registered_pan(self, pan_id: str) -> SaltPan:
        """Return the pan, or explain that the identifier is unknown."""
        normalized = self._normalized_pan_id(pan_id)
        try:
            return await self._pan_repository().get("pan_id", normalized)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No registered pan has the ID “{pan_id}”. Register the pan "
                    "before filing a field-validation event for it."
                ),
            ) from exc

    async def _require_event(self, event_id: str) -> RainfallEvent:
        """Return the event, or explain that it is unknown."""
        try:
            return await self._event_repository().get("event_id", event_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No field-validation event has the ID “{event_id}”."
                ),
            ) from exc

    async def _require_observation(self, observation_id: str) -> PanObservation:
        """Return the stored observation, or explain that it is unknown."""
        try:
            return await self._observation_repository().get(
                "observation_id", observation_id
            )
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No stored observation has the ID “{observation_id}”. A "
                    "pre-event snapshot is reconciled against a record that already "
                    "exists; this module does not invent one."
                ),
            ) from exc

    # -- instants ---------------------------------------------------------

    @staticmethod
    def _event_zone(event: RainfallEvent) -> tuple[timezone | ZoneInfo, str]:
        """The zone an event's local times are shown in, and its label.

        An event may name the zone it was filed in; otherwise the project's site
        zone is used. A zone that cannot be resolved falls back rather than
        raising on a read, and the label says so.
        """
        if event.site_timezone:
            try:
                zone = resolve_timezone(event.site_timezone)
            except HTTPException:
                return SITE_TIMEZONE, f"{SITE_TIMEZONE_LABEL}; declared zone not resolvable"
            return zone, f"{event.site_timezone} ({_zone_label(zone)})"
        return SITE_TIMEZONE, SITE_TIMEZONE_LABEL

    @classmethod
    def _render_instant(
        cls, instant: datetime | None, zone: timezone | ZoneInfo
    ) -> tuple[str | None, str | None]:
        """One instant as a labelled ``(utc, local)`` pair.

        Seconds are kept because a record reconciled against a stored timestamp
        is compared to the second, and rounding them away here would make an
        agreement look approximate that the audit trail treats as exact.
        """
        if instant is None:
            return None, None
        utc_instant = instant.astimezone(timezone.utc)
        local = utc_instant.astimezone(zone)
        local_name = local.tzname() or "local"
        return (
            utc_instant.strftime("%Y-%m-%d %H:%M:%S UTC"),
            f"{local.strftime('%Y-%m-%d %H:%M:%S')} {local_name}",
        )

    @classmethod
    def _measurement_instants(
        cls, measurement: FieldMeasurement, zone: timezone | ZoneInfo
    ) -> tuple[str | None, str | None]:
        return cls._render_instant(
            measurement.timestamp if measurement.timestamp else None, zone
        )

    # -- reads -------------------------------------------------------------

    async def status(self, pan_id: str | None = None) -> StatusReport:
        """A short, never-failing report of how much field data exists."""
        query: dict[str, Any] = {}
        if pan_id:
            query["pan_id"] = self._normalized_pan_id(pan_id)
        event_count = await self._event_repository().count(query)
        ready = event_count > 0
        message = (
            f"{event_count} reported rainfall event{'s' if event_count != 1 else ''} "
            "on file"
            + (f" for {pan_id}" if pan_id else "")
            + "."
            if ready
            else "No reported rainfall events on file yet. The dashboard can still "
            "store one; it just has nothing to compare yet."
        )
        return StatusReport(
            event_count=event_count, ready=ready, message=message
        )

    async def list_events(
        self,
        pan_id: str | None = None,
        limit: int = 100,
    ) -> list[RainfallEvent]:
        """Newest-first events, optionally narrowed to a pan."""
        query: dict[str, Any] = {}
        if pan_id:
            query["pan_id"] = self._normalized_pan_id(pan_id)
        return await self._event_repository().find(
            query,
            sort=[("event_timestamp", -1)],
            limit=limit,
        )

    async def list_comparisons(
        self,
        pan_id: str | None = None,
        limit: int = 100,
    ) -> list[FieldValidationComparison]:
        """The replay for each event, newest first, with the pan's name."""
        return [
            comparison
            for event, comparison in await self._comparisons_with_events(
                pan_id=pan_id, limit=limit
            )
        ]

    async def _comparisons_with_events(
        self,
        pan_id: str | None = None,
        limit: int = 100,
    ) -> list[tuple[RainfallEvent, FieldValidationComparison]]:
        """``(event, comparison)`` pairs, newest, sharing one pan lookup each."""
        events = await self.list_events(pan_id=pan_id, limit=limit)
        rows: list[tuple[RainfallEvent, FieldValidationComparison]] = []
        for event in events:
            pan = await self._pan_repository().get("pan_id", event.pan_id)
            rows.append((event, self.comparison(event, pan)))
        return rows

    async def get_comparison(self, event_id: str) -> FieldValidationComparison:
        """The replay for one event."""
        event = await self._require_event(event_id)
        pan = await self._pan_repository().get("pan_id", event.pan_id)
        return self.comparison(event, pan)

    async def summary(
        self,
        pan_id: str | None = None,
        limit: int = 1000,
    ) -> FieldValidationSummary:
        """How often the reported numbers match the replay's prediction.

        Averages are only computed over events with both a reported and a
        predicted figure in that unit, and are ``None`` when there are none —
        never zeros.
        """
        events = await self.list_events(pan_id=pan_id, limit=limit)
        compared = 0
        with_salinity = 0
        with_depth = 0
        intervention_affected = 0
        unknown_volume = 0
        verified = 0
        unknown_milestone = 0
        unlinked_pre_event = 0
        uncorroborated_post = 0
        blocked_on_evidence = 0
        fully_reconciled = 0
        salinity_diffs: list[float] = []
        depth_diffs: list[float] = []
        for event in events:
            if event.event_milestone is EventMilestone.UNKNOWN:
                unknown_milestone += 1
            if event.pre_event.observation_id is None:
                unlinked_pre_event += 1
            if event.post_event.timestamp and not event.post_event.timestamp_verified:
                uncorroborated_post += 1
            if event.unresolved_rainfall_timestamp_conflicts:
                blocked_on_evidence += 1
            if self._reconciliation_state(event) == "complete":
                fully_reconciled += 1
            if event.pre_event.salinity_g_l is None or event.pre_event.water_depth_cm is None:
                continue
            pan = await self._pan_repository().get("pan_id", event.pan_id)
            comparison = self.comparison(event, pan)
            compared += 1
            if comparison.reported_salinity_g_l is not None:
                with_salinity += 1
                salinity_diffs.append(comparison.salinity_abs_diff_g_l or 0.0)
            if comparison.reported_depth_cm is not None:
                with_depth += 1
                depth_diffs.append(comparison.depth_abs_diff_cm or 0.0)
            if comparison.intervention_affected:
                intervention_affected += 1
            if comparison.unknown_intervention_volumes:
                unknown_volume += 1
            if comparison.independently_verified:
                verified += 1
        return FieldValidationSummary(
            total_events=len(events),
            compared_events=compared,
            events_with_salinity=with_salinity,
            events_with_depth=with_depth,
            intervention_affected_count=intervention_affected,
            unknown_volume_count=unknown_volume,
            verified_count=verified,
            events_with_unknown_milestone=unknown_milestone,
            events_with_unlinked_pre_event=unlinked_pre_event,
            uncorroborated_post_event_timestamps=uncorroborated_post,
            events_blocked_on_evidence=blocked_on_evidence,
            fully_reconciled_events=fully_reconciled,
            mean_abs_salinity_diff_g_l=(
                round(sum(salinity_diffs) / len(salinity_diffs), 6)
                if salinity_diffs
                else None
            ),
            mean_abs_depth_diff_cm=(
                round(sum(depth_diffs) / len(depth_diffs), 6)
                if depth_diffs
                else None
            ),
            caveats=[
                "Figures are user-reported and were not independently verified.",
                (
                    "Predicted values come from the rainfall-only mass balance "
                    f"(Module 6), {MASS_BALANCE_MODEL_NAME}; they are not ML predictions."
                ),
                (
                    f"{unknown_milestone} event(s) carry no established milestone and "
                    f"{unlinked_pre_event} have a pre-event snapshot with no linked "
                    "observation, so their reported figures cannot be traced to a record."
                )
                if unknown_milestone or unlinked_pre_event
                else (
                    "Every event names its milestone and its pre-event snapshot is "
                    "linked to a stored observation."
                ),
                (
                    f"{blocked_on_evidence} event(s) carry an unresolved rainfall-"
                    "timestamp conflict: two records disagree about when the rain fell "
                    "and only one is in force, so those events cannot be reported as "
                    "reconciled."
                )
                if blocked_on_evidence
                else "No event carries an unresolved rainfall-timestamp conflict.",
            ],
        )

    def comparison(
        self, event: RainfallEvent, pan: SaltPan
    ) -> FieldValidationComparison:
        """Replay one event against its starting brine and actual rainfall.

        Units are carried in field names, so a salinity stays in g/L, a depth in
        cm, a rainfall depth in mm. The prediction reads the pre-event salinity
        and depth and the actual rainfall from the event's frozen inputs — never
        from the live twin.
        """
        caveats: list[str] = []
        predicted_salinity: float | None = None
        predicted_depth_cm: float | None = None

        pre_salinity = event.pre_event.salinity_g_l
        pre_depth_cm = event.pre_event.water_depth_cm
        if pre_salinity is None or pre_depth_cm is None:
            caveats.append(
                "The pre-event snapshot is missing the salinity or the depth, so the "
                "rainfall-only balance cannot run; predicted figures are unavailable."
            )
        else:
            result = self._mass_balance.evaluate(
                area_m2=pan.area_m2,
                initial_depth_m=pre_depth_cm / 100.0,
                initial_salinity_g_l=pre_salinity,
                rainfall_mm=event.actual_rainfall_mm,
            )
            predicted_salinity = result.post_rain_salinity_g_l
            predicted_depth_cm = result.post_rain_depth_m * 100.0

        if event.intervention_affected:
            caveats.append(
                "The event recorded an intervention: rainfall-only balance does not "
                "model it, so the prediction is not expected to match. The reported "
                "difference cannot be attributed to the intervention alone."
            )
        if event.unknown_intervention_volumes:
            caveats.append(
                "At least one intervention has no recorded transferred volume, so the "
                "balance cannot even estimate the action's effect."
            )
        if not event.independently_verified:
            caveats.append(
                "The reported figures were not independently verified against a "
                "second source; this comparison is not proof."
            )
        if event.forecast_rainfall_mm is not None and event.forecast_rainfall_mm != event.actual_rainfall_mm:
            caveats.append(
                "The forecast rainfall differs from the actual rainfall; the prediction "
                "uses the actual rainfall only."
            )

        zone, zone_label = self._event_zone(event)
        event_utc, event_local = self._render_instant(event.event_timestamp, zone)
        rainfall_utc, rainfall_local = self._render_instant(event.rainfall_timestamp, zone)
        pre_utc, pre_local = self._measurement_instants(event.pre_event, zone)
        post_utc, post_local = self._measurement_instants(event.post_event, zone)

        if event.event_milestone is EventMilestone.UNKNOWN:
            caveats.append(
                "The event instant has not been assigned a meaning — rainfall start, "
                "rainfall end, or when the report was logged — so it cannot be placed "
                "relative to the rainfall itself."
            )
        if event.pre_event.observation_id is None:
            caveats.append(
                "The pre-event snapshot is not linked to a stored observation: the "
                "starting brine is reported-only and its instant has no record behind it."
            )
        elif not event.pre_event.timestamp_verified:
            caveats.append(
                "The pre-event snapshot is linked to observation "
                f"{event.pre_event.observation_id}, but that link's instant is not "
                "marked corroborated."
            )
        if event.post_event.timestamp and not event.post_event.timestamp_verified:
            caveats.append(
                "The post-event instant is retained as reported and has not been "
                "corroborated by any record."
            )
        if event.weather_observation_id is None:
            caveats.append(
                "The actual rainfall is not linked to a stored weather record; its "
                "depth and instant are reported only."
            )
        if event.corrections:
            caveats.append(
                f"{len(event.corrections)} reconciliation entr"
                f"{'y' if len(event.corrections) == 1 else 'ies'} recorded in this "
                "event's audit history; the figures shown are the current ones and the "
                "earlier values remain in the history."
            )

        conflicts = self._conflict_reads(event, zone)
        superseded = self._render_superseded(event, zone)
        unresolved = [
            conflict for conflict in conflicts if conflict.status == "unresolved"
        ]
        for conflict in unresolved:
            caveats.append(
                f"Two accounts of when the rain fell disagree by "
                f"{conflict.offset_human}: this event carries "
                f"{conflict.stored_timestamp_utc}, while {conflict.reported_source} "
                f"reports {conflict.reported_timestamp_utc}. The stored timestamp is "
                "in force for the replay and has not been changed; the event cannot "
                "be reported as reconciled until the disagreement is resolved against "
                "evidence."
            )

        # A milestone that names the rain's start makes the stored rainfall
        # instant checkable: rain cannot be recorded before it began.
        stored_rainfall = event.rainfall_timestamp.astimezone(timezone.utc)
        event_instant = event.event_timestamp.astimezone(timezone.utc)
        if (
            event.event_milestone is EventMilestone.RAINFALL_START
            and stored_rainfall < event_instant
        ):
            caveats.append(
                "The event instant is filed as the rainfall start, but the stored "
                f"rainfall timestamp ({rainfall_utc}) is "
                f"{_humanise_offset(int((event_instant - stored_rainfall).total_seconds()))} "
                f"than it ({event_utc}). One of the two is wrong; neither has been "
                "changed, and the disagreement is why this event is not reconciled."
            )

        # A corrected instant is the one case where the stored value has been
        # moved off a record on evidence, so say what was displaced and why it is
        # no longer a measurement. The wording is load-bearing: the earlier value
        # is named as history, never as a second reading.
        for displaced in event.superseded_rainfall_timestamps:
            displaced_utc, displaced_local = self._render_instant(
                displaced.timestamp, zone
            )
            support = (
                f"it was supported by stored record {displaced.supporting_record}"
                if displaced.supporting_record
                else (
                    "no record in this system supported it, and what it was meant "
                    "to mark is unknown, so it is retained as audit history only "
                    "and is not a measurement"
                )
            )
            caveats.append(
                f"This event's rainfall instant was corrected to {rainfall_utc} "
                f"({rainfall_local}), replacing the earlier value "
                f"{displaced_utc} ({displaced_local}), which entered the record via "
                f"{displaced.origin}. For that earlier value, {support}."
            )
        if (
            event.rainfall_timestamp_verification
            is RainfallTimestampEvidence.OPERATOR_REPORTED
        ):
            caveats.append(
                "The rainfall instant in force is operator-reported, dated by "
                f"{event.rainfall_timestamp_evidence or 'a reported source'}. A "
                "document that records a time does not measure one, so this is not "
                "an instrument-verified rain-gauge reading."
            )

        return FieldValidationComparison(
            event_id=event.event_id,
            pan_id=event.pan_id,
            pan_name=pan.location_name,
            event_timestamp=event.event_timestamp,
            event_milestone=event.event_milestone,
            site_timezone_label=zone_label,
            event_timestamp_utc=event_utc,
            event_timestamp_ist=event_local,
            rainfall_timestamp_utc=rainfall_utc,
            rainfall_timestamp_ist=rainfall_local,
            rainfall_timestamp_evidence=event.rainfall_timestamp_evidence,
            rainfall_timestamp_verification=event.rainfall_timestamp_verification,
            rainfall_timestamp_observed=event.rainfall_timestamp_is_observed,
            superseded_rainfall_timestamps=superseded,
            reported_by=event.reported_by,
            notes=event.notes,
            model_name=MASS_BALANCE_MODEL_NAME,
            model_disclaimer=SIMULATOR_DISCLAIMER,
            actual_rainfall_mm=event.actual_rainfall_mm,
            forecast_rainfall_mm=event.forecast_rainfall_mm,
            predicted_salinity_g_l=predicted_salinity,
            predicted_depth_cm=(
                round(predicted_depth_cm, 6) if predicted_depth_cm is not None else None
            ),
            reported_salinity_g_l=event.post_event.salinity_g_l,
            reported_depth_cm=event.post_event.water_depth_cm,
            salinity_abs_diff_g_l=(
                abs(predicted_salinity - event.post_event.salinity_g_l)
                if predicted_salinity is not None and event.post_event.salinity_g_l is not None
                else None
            ),
            depth_abs_diff_cm=(
                abs(predicted_depth_cm - event.post_event.water_depth_cm)
                if predicted_depth_cm is not None and event.post_event.water_depth_cm is not None
                else None
            ),
            pre_event_salinity_g_l=event.pre_event.salinity_g_l,
            pre_event_depth_cm=event.pre_event.water_depth_cm,
            pre_event_timestamp_utc=pre_utc,
            pre_event_timestamp_ist=pre_local,
            pre_event_observation_id=event.pre_event.observation_id,
            pre_event_observer=event.pre_event.observer,
            pre_event_timestamp_verified=event.pre_event.timestamp_verified,
            post_event_timestamp_utc=post_utc,
            post_event_timestamp_ist=post_local,
            post_event_observation_id=event.post_event.observation_id,
            post_event_observer=event.post_event.observer,
            post_event_timestamp_verified=event.post_event.timestamp_verified,
            weather_observation_id=event.weather_observation_id,
            corrections=list(event.corrections),
            correction_count=len(event.corrections),
            rainfall_timestamp_conflicts=conflicts,
            unresolved_conflict_count=len(unresolved),
            reconciliation_state=self._reconciliation_state(event),
            intervention_affected=event.intervention_affected,
            unknown_intervention_volumes=event.unknown_intervention_volumes,
            independently_verified=event.independently_verified,
            verification_state=event.verification_state,
            caveats=caveats,
            assumptions=list(SIMULATION_ASSUMPTIONS),
        )

    # -- writes ------------------------------------------------------------

    async def create_event(
        self, payload: RainfallEventCreate
    ) -> RainfallEvent:
        """File one reported event, frozen exactly as it arrived.

        The pre-event snapshot must carry a starting salinity and depth, because
        the replay needs both; the post-event snapshot may carry either. The
        event is stored with the actual rainfall and any forecast side by side,
        with the actual rainfall the replay reads.
        """
        pan = await self._require_registered_pan(payload.pan_id)
        if payload.pre_event.salinity_g_l is None or payload.pre_event.water_depth_cm is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "The pre-event snapshot must carry both a salinity (g/L) and a "
                    "water depth (cm), because the rainfall-only balance replays the "
                    "event from them."
                ),
            )
        event = RainfallEvent(
            pan_id=pan.pan_id,
            name=payload.name,
            event_timestamp=payload.event_timestamp,
            event_milestone=payload.event_milestone,
            site_timezone=payload.event_timezone,
            reported_at=utcnow(),
            reported_by=payload.reported_by,
            verification_state=VerificationState.REPORTED,
            actual_rainfall_mm=payload.actual_rainfall_mm,
            rainfall_timestamp=payload.rainfall_timestamp,
            rainfall_source=payload.rainfall_source,
            rainfall_timestamp_evidence=payload.rainfall_timestamp_evidence,
            rainfall_timestamp_verification=payload.rainfall_timestamp_verification,
            weather_observation_id=payload.weather_observation_id,
            forecast_rainfall_mm=payload.forecast_rainfall_mm,
            pre_event=payload.pre_event,
            post_event=payload.post_event,
            interventions=list(payload.interventions),
            notes=payload.notes,
        )
        return await self._event_repository().insert(event)

    async def add_intervention(
        self,
        event_id: str,
        payload: FieldInterventionCreate,
    ) -> RainfallEvent:
        """Append one intervention to an existing event, then persist the whole.

        The event is replaced wholesale so the array append and the document
        write are one operation; the event id and all prior content are kept
        exactly as filed. Nothing is ever edited in place.
        """
        event = await self._require_event(event_id)
        intervention = FieldIntervention(
            kind=payload.kind,
            direction=payload.direction,
            performed_at=payload.performed_at,
            volume_m3=payload.volume_m3,
            description=payload.description,
            source=payload.source,
        )
        updated = event.model_copy(update={"interventions": [*event.interventions, intervention]})
        return await self._event_repository().replace_where(
            {"event_id": event.event_id}, updated
        )

    async def record_correction(
        self, event_id: str, payload: EventCorrectionCreate
    ) -> RainfallEvent:
        """Append one reconciliation step, then persist the whole event.

        Nothing here edits a figure in place: the previous value goes into an
        append-only ``CorrectionRecord`` next to the new one, so the event's
        history always shows what each field used to say and why it changed.

        A restated timestamp keeps ``timestamp_verified`` False even when an
        evidence source is named. Corroboration means an independent record
        agrees with the instant; an operator asserting a time cannot corroborate
        itself. Restating a previously corroborated instant therefore clears the
        flag as well — the old record no longer describes the new time.
        """
        event = await self._require_event(event_id)
        pre_event = event.pre_event.model_copy()
        post_event = event.post_event.model_copy()
        update: dict[str, Any] = {}

        if payload.field in {"rainfall_timestamp", "actual_rainfall_mm"}:
            # Not reachable through the schema whitelist; stated here so that the
            # reason survives any future edit that widens it.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The rainfall timestamp and depth cannot be restated as a "
                    "correction. If another source dates the rain differently, "
                    "record it as a rainfall-timestamp conflict so both accounts "
                    "stay on the record and the event stays blocked until the "
                    "disagreement is resolved against evidence."
                ),
            )

        if payload.field == "event_milestone":
            previous = event.event_milestone.value
            new = payload.event_milestone.value if payload.event_milestone else None
            update["event_milestone"] = payload.event_milestone
        elif payload.field == "pre_event_timestamp":
            previous = _instant_text(pre_event.timestamp)
            new = _instant_text(payload.timestamp)
            pre_event = pre_event.model_copy(
                update={
                    "timestamp": payload.timestamp,
                    "recorded_timezone": payload.recorded_timezone,
                    "timestamp_verified": False,
                }
            )
            update["pre_event"] = pre_event
        elif payload.field == "post_event_timestamp":
            previous = _instant_text(post_event.timestamp)
            new = _instant_text(payload.timestamp)
            post_event = post_event.model_copy(
                update={
                    "timestamp": payload.timestamp,
                    "recorded_timezone": payload.recorded_timezone,
                    "timestamp_verified": False,
                }
            )
            update["post_event"] = post_event
        elif payload.field == "pre_event_observer":
            previous = pre_event.observer
            new = payload.observer
            update["pre_event"] = pre_event.model_copy(update={"observer": payload.observer})
        elif payload.field == "post_event_observer":
            previous = post_event.observer
            new = payload.observer
            update["post_event"] = post_event.model_copy(update={"observer": payload.observer})
        elif payload.field == "pre_event_evidence":
            previous = pre_event.evidence_source
            new = payload.evidence_source
            update["pre_event"] = pre_event.model_copy(
                update={"evidence_source": payload.evidence_source}
            )
        else:
            previous = post_event.evidence_source
            new = payload.evidence_source
            update["post_event"] = post_event.model_copy(
                update={"evidence_source": payload.evidence_source}
            )

        record = CorrectionRecord(
            field=payload.field,
            previous_value=previous,
            new_value=new,
            reason=payload.reason,
            corrected_by=payload.corrected_by,
            evidence_source=payload.evidence_source,
        )
        updated = event.model_copy(
            update={**update, "corrections": [*event.corrections, record]}
        )
        return await self._event_repository().replace_where(
            {"event_id": event.event_id}, updated
        )

    async def link_pre_event_observation(
        self, event_id: str, payload: PreEventObservationLink
    ) -> RainfallEvent:
        """Reconcile the pre-event snapshot against a stored observation.

        This is the only route by which a starting brine becomes traceable, and
        it is deliberately hard to satisfy. The observation must exist, belong to
        the same pan, and predate the rainfall the snapshot starts from — so the
        30 September reading of ``pan_orginal`` is refused for a March event, and
        the refusal says why. The operator must then state the original record's
        own local time, zone, observer and evidence, and that local time must
        resolve to the same instant the stored observation carries. Finally the
        snapshot's figures must agree with the observation's to within
        instrument precision; a disagreement is refused rather than averaged, so
        two records of the same pan at different times can never be merged.
        """
        event = await self._require_event(event_id)
        observation = await self._require_observation(payload.observation_id)

        if event.pre_event.observation_id == observation.observation_id:
            return event

        if observation.pan_id != event.pan_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Observation {observation.observation_id} belongs to pan "
                    f"“{observation.pan_id}”, not to “{event.pan_id}”. Nothing was linked."
                ),
            )

        zone, _ = self._event_zone(event)
        declared_zone = resolve_timezone(payload.measured_timezone)
        declared_instant = parse_local_instant(payload.measured_at_local, declared_zone)
        observation_instant = observation.timestamp.astimezone(timezone.utc)
        drift = abs(
            (declared_instant - observation_instant).total_seconds()
        )
        local_name = observation_instant.astimezone(zone).tzname() or ""

        # Chronology is checked before agreement, so a cross-date observation is
        # refused for the reason that actually matters: it cannot be this event's
        # starting brine, whatever its numbers happen to be.
        if observation_instant > event.rainfall_timestamp.astimezone(timezone.utc):
            observed_local = observation_instant.astimezone(zone).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            rainfall_local = event.rainfall_timestamp.astimezone(zone).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Observation {observation.observation_id} is dated "
                    f"{observation_instant.strftime('%Y-%m-%d %H:%M:%S UTC')} "
                    f"({observed_local} {local_name}), which is after this event's "
                    f"rainfall at "
                    f"{event.rainfall_timestamp.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')} "
                    f"({rainfall_local} {local_name}). A pre-event reading cannot "
                    "postdate the rain it starts from, so it cannot be this event's "
                    "starting brine. Nothing was linked. If a pre-event record exists, "
                    "file it as its own observation with its own original timestamp and "
                    "link that."
                ),
            )

        if drift > LINK_TIME_TOLERANCE_SECONDS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"The original record's time resolves to "
                    f"{declared_instant.strftime('%Y-%m-%d %H:%M:%S UTC')}, but "
                    f"observation {observation.observation_id} is stored at "
                    f"{observation_instant.strftime('%Y-%m-%d %H:%M:%S UTC')}. Those "
                    "are different instants, so the record does not document this "
                    "observation and nothing was linked."
                ),
            )

        mismatches = [
            detail
            for detail in (
                _compare_values(
                    "salinity", event.pre_event.salinity_g_l, observation.salinity_g_l,
                    "g/L", LINK_SALINITY_TOLERANCE_G_L,
                ),
                _compare_values(
                    "depth", event.pre_event.water_depth_cm, observation.water_depth_cm,
                    "cm", LINK_DEPTH_TOLERANCE_CM,
                ),
            )
            if detail is not None
        ]
        if mismatches:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Observation {observation.observation_id} does not agree with this "
                    f"event's pre-event snapshot ({'; '.join(mismatches)}). Nothing was "
                    "linked: two records of one pan at different instants cannot be "
                    "merged, and the reported figure has not been changed."
                ),
            )

        previous_summary = _measurement_summary(event.pre_event)
        previous_summary = (
            f"{previous_summary}; observation_id={event.pre_event.observation_id or 'none'}"
        )
        reconciled = FieldMeasurement(
            salinity_g_l=observation.salinity_g_l,
            water_depth_cm=observation.water_depth_cm,
            timestamp=observation.timestamp,
            recorded_timezone=payload.measured_timezone,
            source=observation.data_source.value,
            observation_id=observation.observation_id,
            observer=payload.observer,
            evidence_source=payload.evidence_source,
            # The instant now comes from a record that existed independently of
            # this event, and the operator's own reading of it agrees: that is
            # corroboration. The event itself stays reported and unverified.
            timestamp_verified=True,
            note=event.pre_event.note,
        )
        record = CorrectionRecord(
            field="pre_event_link",
            previous_value=previous_summary,
            new_value=_measurement_summary(reconciled),
            reason=payload.reason,
            corrected_by=payload.corrected_by,
            evidence_source=payload.evidence_source,
            evidence_record_id=observation.observation_id,
        )
        updated = event.model_copy(
            update={
                "pre_event": reconciled,
                "corrections": [*event.corrections, record],
            }
        )
        return await self._event_repository().replace_where(
            {"event_id": event.event_id}, updated
        )

    async def record_rainfall_timestamp_conflict(
        self, event_id: str, payload: RainfallTimestampConflictCreate
    ) -> RainfallEvent:
        """Preserve a second account of when the rain fell, without adopting it.

        This is deliberately *not* a correction. The event keeps the
        ``rainfall_timestamp`` it was filed with, because that instant and the
        recorded depth are what the replay was run against; moving the instant
        would quietly change the meaning of a stored event. What happens instead
        is that both accounts go on the record — the one in force and the one a
        later source reports — together with the source and the size of the
        disagreement, and the event is blocked from being called reconciled until
        somebody resolves it against evidence.

        Repeating a conflict that is already on the record is a no-op, so the
        call is safe to run again.
        """
        event = await self._require_event(event_id)
        stored = event.rainfall_timestamp.astimezone(timezone.utc)
        reported = payload.reported_timestamp.astimezone(timezone.utc)

        if stored == reported:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "The reported time matches the timestamp this event already "
                    "carries, so there is no disagreement to record. If the figure "
                    "itself needs evidence, reconcile it as a correction instead."
                ),
            )

        for existing in event.rainfall_timestamp_conflicts:
            if (
                existing.status == "unresolved"
                and existing.stored_timestamp.astimezone(timezone.utc) == stored
                and existing.reported_timestamp.astimezone(timezone.utc) == reported
            ):
                return event

        offset_seconds = int((reported - stored).total_seconds())
        conflict = RainfallTimestampConflict(
            stored_timestamp=stored,
            reported_timestamp=reported,
            reported_source=payload.reported_source,
            offset_seconds=offset_seconds,
            detected_at=utcnow(),
            recorded_by=payload.recorded_by,
            reason=payload.reason,
        )
        record = CorrectionRecord(
            field="rainfall_timestamp_conflict",
            previous_value=_instant_text(stored),
            new_value=_instant_text(reported),
            reason=payload.reason,
            corrected_by=payload.recorded_by,
            evidence_source=payload.reported_source,
            evidence_record_id=conflict.conflict_id,
        )
        updated = event.model_copy(
            update={
                "rainfall_timestamp_conflicts": [
                    *event.rainfall_timestamp_conflicts,
                    conflict,
                ],
                "corrections": [*event.corrections, record],
            }
        )
        return await self._event_repository().replace_where(
            {"event_id": event.event_id}, updated
        )

    @staticmethod
    def _adopted_instant(payload: RainfallTimestampResolutionCreate) -> datetime:
        """The instant being adopted, in UTC, resolved here rather than in a client.

        A local clock reading is placed on the timeline by the same helpers the
        pre-event link uses, so the browser never performs a zone conversion and a
        reading that contradicts its own declared zone is refused rather than
        resolved by guesswork.
        """
        if payload.timestamp is not None:
            return payload.timestamp.astimezone(timezone.utc)
        declared = resolve_timezone(payload.timestamp_timezone or "")
        return parse_local_instant(payload.timestamp_local or "", declared).astimezone(
            timezone.utc
        )

    async def resolve_rainfall_timestamp(
        self, event_id: str, payload: RainfallTimestampResolutionCreate
    ) -> RainfallEvent:
        """Replace the event's rainfall instant with an evidenced one, audited.

        This is the single sanctioned way to move ``rainfall_timestamp``, and it
        exists because the generic correction endpoint refuses that field. What
        makes the move defensible is not that a caller asked for it, but that the
        caller had to account for the value they were displacing: the payload
        names where it came from, the label it carried, and whether any stored
        record actually backed it. So the value the project no longer stands
        behind stays on the record, in full, as history.

        Three properties are enforced rather than requested:

        * **The displaced value is superseded, never deleted.** It moves to
          :class:`SupersededRainfallTimestamp` with ``meaning`` fixed at
          ``unknown``. It is not re-entered as a second timestamp anywhere, and no
          output renders it as a measurement.
        * **The new instant cannot be upgraded.** ``verification`` is capped at
          ``operator_reported``: supplying a document reports a time, it does not
          measure one, so no caller can turn a notebook entry into a
          rain-gauge reading. ``verification_state`` is never touched, so
          ``independently_verified`` stays False.
        * **Nothing else moves.** The pre-event and post-event snapshots, the
          interventions and their unknown volumes, and every other correction in
          the history are carried across byte-for-byte.

        A conflict is closed only when the instant being adopted is the one that
        conflict was raised about, and only then does the event stop being
        ``blocked_by_evidence``. Adopting some third instant leaves the recorded
        disagreement open, because the thing it recorded still disagrees with what
        the event now says.

        Re-running the same resolution is a no-op: the instant, the evidence and
        the displaced value are all recognised, so a second call adds no second
        correction entry and no second superseded record.
        """
        event = await self._require_event(event_id)
        stored = event.rainfall_timestamp.astimezone(timezone.utc)
        adopted = self._adopted_instant(payload)
        zone_label = self._event_zone(event)[1]

        already_applied = (
            stored == adopted
            and event.rainfall_timestamp_evidence == payload.evidence_source
            and event.rainfall_timestamp_verification is payload.verification
            and any(
                record.field == "rainfall_timestamp"
                and record.new_value == _instant_text(adopted)
                and record.evidence_source == payload.evidence_source
                for record in event.corrections
            )
        )
        if already_applied:
            return event

        if stored == adopted:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This event already carries "
                    f"{stored.strftime('%Y-%m-%d %H:%M:%S UTC')} as its rainfall "
                    "timestamp, so there is no instant to replace. If the instant is "
                    "right but its provenance is wrong, record that as a correction on "
                    "another field rather than restating an unchanged time."
                ),
            )

        superseded = SupersededRainfallTimestamp(
            timestamp=stored,
            timezone_label=zone_label,
            source_claim=payload.superseded_source_claim,
            origin=payload.superseded_origin,
            supporting_record=payload.superseded_supporting_record,
            meaning="unknown",
            superseded_by=payload.corrected_by,
            reason=payload.reason,
        )

        record = CorrectionRecord(
            field="rainfall_timestamp",
            previous_value=_instant_text(stored),
            new_value=_instant_text(adopted),
            reason=payload.reason,
            corrected_by=payload.corrected_by,
            evidence_source=payload.evidence_source,
        )

        updated = event.model_copy(
            update={
                "rainfall_timestamp": adopted,
                "rainfall_timestamp_evidence": payload.evidence_source,
                "rainfall_timestamp_verification": payload.verification,
                "superseded_rainfall_timestamps": [
                    *event.superseded_rainfall_timestamps,
                    superseded,
                ],
                "corrections": [*event.corrections, record],
            }
        )

        # Close the conflict this instant answers, and only that one. A conflict
        # raised about a different instant is still an open disagreement about the
        # value the event now carries.
        resolved: list[RainfallTimestampConflict] = []
        for conflict in updated.rainfall_timestamp_conflicts:
            if (
                conflict.is_unresolved
                and conflict.reported_timestamp.astimezone(timezone.utc) == adopted
            ):
                superseded = superseded.model_copy(update={"conflict_id": conflict.conflict_id})
                updated = updated.model_copy(
                    update={
                        "superseded_rainfall_timestamps": [
                            *updated.superseded_rainfall_timestamps[:-1],
                            superseded,
                        ]
                    }
                )
                resolved.append(
                    conflict.model_copy(
                        update={
                            "status": "resolved",
                            "resolved_at": utcnow(),
                            "resolution_note": payload.reason,
                        }
                    )
                )
            else:
                resolved.append(conflict)
        if resolved != updated.rainfall_timestamp_conflicts:
            updated = updated.model_copy(
                update={"rainfall_timestamp_conflicts": resolved}
            )

        return await self._event_repository().replace_where(
            {"event_id": event.event_id}, updated
        )

    # -- exports -----------------------------------------------------------

    def _superseded_summary(
        self, event: RainfallEvent, zone: timezone | ZoneInfo
    ) -> str:
        """One cell describing every instant this event used to carry.

        Rendered as prose rather than as a timestamp column, and each entry leads
        with the fact that nothing supported it. A bare instant in a column
        beside ``rainfall_timestamp_utc`` would be read as a second measurement
        of when the rain fell, which is the misreading this cell exists to block.
        The value is not hidden -- an auditor can still see the time, its local
        reading and how it arrived -- but it is never presented as a figure.
        """
        if not event.superseded_rainfall_timestamps:
            return ""
        parts: list[str] = []
        for displaced in event.superseded_rainfall_timestamps:
            utc_text, local_text = self._render_instant(displaced.timestamp, zone)
            support = (
                f"supported by {displaced.supporting_record}"
                if displaced.supporting_record
                else "no supporting record; meaning unknown; not a measurement"
            )
            claim = (
                f'; claimed source "{displaced.source_claim}"'
                if displaced.source_claim
                else ""
            )
            parts.append(
                f"SUPERSEDED {utc_text} ({local_text}) - {support}; origin: "
                f"{displaced.origin}{claim}; superseded because: {displaced.reason}"
            )
        return " | ".join(parts)

    def _export_row(
        self, event: RainfallEvent, comparison: FieldValidationComparison
    ) -> dict[str, Any]:
        """One CSV row from one replay, values rendered in plain text."""
        zone, zone_label = self._event_zone(event)
        event_utc, event_local = self._render_instant(event.event_timestamp, zone)
        rainfall_utc, rainfall_local = self._render_instant(event.rainfall_timestamp, zone)
        pre_utc, pre_local = self._measurement_instants(event.pre_event, zone)
        post_utc, post_local = self._measurement_instants(event.post_event, zone)

        return {
            "event_id": comparison.event_id,
            "pan_id": comparison.pan_id,
            "name": event.name or comparison.pan_name or "",
            "event_timestamp": event.event_timestamp.isoformat()
            if event.event_timestamp
            else "",
            "event_milestone": event.event_milestone.value,
            "site_timezone_label": zone_label,
            "event_timestamp_utc": event_utc or "",
            "event_timestamp_ist": event_local or "",
            "actual_rainfall_mm": event.actual_rainfall_mm,
            "forecast_rainfall_mm": event.forecast_rainfall_mm
            if event.forecast_rainfall_mm is not None
            else "",
            "rainfall_timestamp_utc": rainfall_utc or "",
            "rainfall_timestamp_ist": rainfall_local or "",
            "rainfall_timestamp_evidence": event.rainfall_timestamp_evidence or "",
            "rainfall_timestamp_verification": event.rainfall_timestamp_verification.value,
            "rainfall_timestamp_observed": event.rainfall_timestamp_is_observed,
            "superseded_rainfall_timestamps": self._superseded_summary(event, zone),
            "unresolved_rainfall_conflicts": comparison.unresolved_conflict_count,
            "reconciliation_state": comparison.reconciliation_state,
            "pre_event_salinity_g_l": event.pre_event.salinity_g_l
            if event.pre_event.salinity_g_l is not None
            else "",
            "pre_event_depth_cm": event.pre_event.water_depth_cm
            if event.pre_event.water_depth_cm is not None
            else "",
            "pre_event_timestamp_utc": pre_utc or "",
            "pre_event_timestamp_ist": pre_local or "",
            "pre_event_observation_id": event.pre_event.observation_id or "",
            "pre_event_observer": event.pre_event.observer or "",
            "pre_event_timestamp_verified": event.pre_event.timestamp_verified,
            "reported_salinity_g_l": comparison.reported_salinity_g_l
            if comparison.reported_salinity_g_l is not None
            else "",
            "reported_depth_cm": comparison.reported_depth_cm
            if comparison.reported_depth_cm is not None
            else "",
            "post_event_timestamp_utc": post_utc or "",
            "post_event_timestamp_ist": post_local or "",
            "post_event_observation_id": event.post_event.observation_id or "",
            "post_event_observer": event.post_event.observer or "",
            "post_event_timestamp_verified": event.post_event.timestamp_verified,
            "salinity_abs_diff_g_l": comparison.salinity_abs_diff_g_l
            if comparison.salinity_abs_diff_g_l is not None
            else "",
            "depth_abs_diff_cm": comparison.depth_abs_diff_cm
            if comparison.depth_abs_diff_cm is not None
            else "",
            "predicted_salinity_g_l": comparison.predicted_salinity_g_l
            if comparison.predicted_salinity_g_l is not None
            else "",
            "predicted_depth_cm": comparison.predicted_depth_cm
            if comparison.predicted_depth_cm is not None
            else "",
            "weather_observation_id": event.weather_observation_id or "",
            "intervention_affected": comparison.intervention_affected,
            "unknown_intervention_volumes": comparison.unknown_intervention_volumes,
            "independently_verified": comparison.independently_verified,
            "verification_state": comparison.verification_state.value,
            "correction_count": len(event.corrections),
            "reported_by": event.reported_by or "",
            "notes": event.notes or "",
        }

    async def export(
        self,
        output_format: str,
        pan_id: str | None = None,
        limit: int = 1000,
    ) -> tuple[str, str, str]:
        """Return ``(media_type, body, filename)`` for a downloadable report.

        CSV carries one row per replayed event with the predicted and reported
        figures side by side and the absolute differences. PDF carries the same
        table plus the module disclaimers, rendered as a single section report.
        """
        rows = await self._comparisons_with_events(pan_id=pan_id, limit=limit)
        if output_format == "csv":
            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer, fieldnames=list(EXPORT_COLUMNS), extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(
                {
                    key: sanitize_prose_cell(value)
                    for key, value in self._export_row(event, comparison).items()
                }
                for event, comparison in rows
            )
            return "text/csv; charset=utf-8", buffer.getvalue(), "field-validation.csv"
        if output_format == "pdf":
            return (
                "application/pdf",
                self._export_pdf(rows),
                "field-validation.pdf",
            )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="output_format must be 'csv' or 'pdf'.",
        )

    def _export_pdf(
        self, rows: list[tuple[RainfallEvent, FieldValidationComparison]]
    ) -> bytes:
        """Render the field-validation table and caveats as a one-section PDF."""
        from fpdf import FPDF

        pdf = FPDF(format="A4")
        pdf.set_auto_page_break(auto=True, margin=18)
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, _as_latin1(PDF_TITLE), new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 5, _as_latin1(PDF_SUBTITLE))
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 5, _as_latin1(SIMULATOR_DISCLAIMER))
        pdf.ln(3)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(
            0,
            4,
            _as_latin1(
                "Times shown as UTC and IST (Asia/Kolkata, UTC+05:30). The stored "
                "instants are UTC; the IST columns are a labelled rendering, not a "
                "conversion applied to the recorded value."
            ),
        )
        pdf.ln(2)

        columns = (
            ("Event", 24),
            ("Rain mm", 16),
            ("Pred salt g/L", 22),
            ("Rep salt g/L", 22),
            ("|Δ| salt", 18),
            ("Pred cm", 18),
            ("Rep cm", 18),
            ("|Δ| cm", 16),
            ("Status", 24),
        )
        header = [label for label, _ in columns]
        widths = [width for _, width in columns]
        pdf.set_font("Helvetica", "B", 9)
        for label, width in zip(header, widths, strict=True):
            pdf.cell(width, 7, _as_latin1(label), border=1, align="C")
        pdf.ln()

        def render(value: float | None) -> str:
            return f"{value:.3f}" if value is not None else "—"

        def _text(value: str | None) -> str:
            """Say 'not recorded' rather than leaving a cell blank."""
            return value if value else "not recorded"

        pdf.set_font("Helvetica", "", 9)
        for event, comparison in rows:
            row = (
                comparison.event_id,
                f"{comparison.actual_rainfall_mm:.1f}",
                render(comparison.predicted_salinity_g_l),
                render(comparison.reported_salinity_g_l),
                render(comparison.salinity_abs_diff_g_l),
                render(comparison.predicted_depth_cm),
                render(comparison.reported_depth_cm),
                render(comparison.depth_abs_diff_cm),
                comparison.verification_state.value,
            )
            for cell, width in zip(row, widths, strict=True):
                pdf.cell(width, 7, _as_latin1(cell), border=1, align="C")
            pdf.ln()

        if rows:
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 6, "Caveats for each event:", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9)
            for event, comparison in rows:
                title = event.name or comparison.event_id
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(0, 5, f"{title} ({comparison.event_id}):")
                for caveat in comparison.caveats:
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(0, 5, "  - " + _as_latin1(caveat))

            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(
                0, 6, "Provenance and audit history:", new_x="LMARGIN", new_y="NEXT"
            )
            pdf.set_font("Helvetica", "", 8)
            for event, comparison in rows:
                title = event.name or comparison.event_id
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(0, 4, f"{title} ({comparison.event_id}):")
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(
                    0,
                    4,
                    _as_latin1(
                        f"  milestone: {comparison.event_milestone.value} "
                        f"({comparison.site_timezone_label} for local times)"
                    ),
                )
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(
                    0,
                    4,
                    _as_latin1(
                        "  event instant: "
                        f"UTC {_text(comparison.event_timestamp_utc)} | "
                        f"local {_text(comparison.event_timestamp_ist)}"
                    ),
                )
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(
                    0,
                    4,
                    _as_latin1(
                        "  rainfall instant: "
                        f"UTC {_text(comparison.rainfall_timestamp_utc)} | "
                        f"local {_text(comparison.rainfall_timestamp_ist)}; "
                        f"weather record: {_text(comparison.weather_observation_id)}"
                    ),
                )
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(
                    0,
                    4,
                    _as_latin1(
                        "  pre-event snapshot: "
                        f"UTC {_text(comparison.pre_event_timestamp_utc)} | "
                        f"local {_text(comparison.pre_event_timestamp_ist)}; "
                        f"observation {_text(comparison.pre_event_observation_id)}; "
                        f"observer {_text(comparison.pre_event_observer)}; "
                        f"instant corroborated: "
                        f"{'yes' if comparison.pre_event_timestamp_verified else 'no'}"
                    ),
                )
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(
                    0,
                    4,
                    _as_latin1(
                        "  post-event snapshot: "
                        f"UTC {_text(comparison.post_event_timestamp_utc)} | "
                        f"local {_text(comparison.post_event_timestamp_ist)}; "
                        f"observation {_text(comparison.post_event_observation_id)}; "
                        f"observer {_text(comparison.post_event_observer)}; "
                        f"instant corroborated: "
                        f"{'yes' if comparison.post_event_timestamp_verified else 'no'}"
                    ),
                )
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(
                    0,
                    4,
                    _as_latin1(
                        f"  verification: {comparison.verification_state.value}; "
                        f"independently verified: "
                        f"{'yes' if comparison.independently_verified else 'no'}; "
                        f"reconciliation: {comparison.reconciliation_state}"
                    ),
                )
                pdf.set_x(pdf.l_margin)
                if not event.rainfall_timestamp_conflicts:
                    pdf.multi_cell(0, 4, "  rainfall timestamp: no recorded conflict.")
                for conflict in comparison.rainfall_timestamp_conflicts:
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(
                        0,
                        4,
                        _as_latin1(
                            f"  rainfall timestamp conflict ({conflict.status}): the "
                            f"event carries "
                            f"{_text(conflict.stored_timestamp_utc)} "
                            f"({conflict.stored_timestamp_ist}); "
                            f"{conflict.reported_source} reports "
                            f"{_text(conflict.reported_timestamp_utc)} "
                            f"({conflict.reported_timestamp_ist}); "
                            f"difference {conflict.offset_human}. "
                            + (
                                "The stored timestamp is in force and has not been "
                                "changed."
                                if conflict.status == "unresolved"
                                else "Resolved: the event now carries the instant "
                                "this record reported."
                            )
                        ),
                    )
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(
                        0, 4, _as_latin1(f"      reason: {conflict.reason}")
                    )
                    if conflict.resolution_note:
                        pdf.set_x(pdf.l_margin)
                        pdf.multi_cell(
                            0,
                            4,
                            _as_latin1(
                                f"      resolution: {conflict.resolution_note}"
                            ),
                        )
                pdf.set_x(pdf.l_margin)
                pdf.multi_cell(
                    0,
                    4,
                    _as_latin1(
                        f"  rainfall instant in force: UTC "
                        f"{_text(comparison.rainfall_timestamp_utc)} | local "
                        f"{_text(comparison.rainfall_timestamp_ist)}; evidence: "
                        f"{_text(comparison.rainfall_timestamp_evidence) or 'none recorded'}; "
                        f"standing: "
                        f"{comparison.rainfall_timestamp_verification.value}; "
                        f"instrument-verified: "
                        f"{'yes' if comparison.rainfall_timestamp_observed else 'no'}"
                    ),
                )
                for displaced in comparison.superseded_rainfall_timestamps:
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(
                        0,
                        4,
                        _as_latin1(
                            f"  superseded rainfall instant (audit history, not a "
                            f"measurement): UTC {_text(displaced.timestamp_utc)} | "
                            f"local {_text(displaced.timestamp_ist)}; origin: "
                            f"{displaced.origin}; meaning: {displaced.meaning}; "
                            f"supporting record: "
                            f"{displaced.supporting_record or 'none'}"
                        ),
                    )
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(
                        0, 4, _as_latin1(f"      superseded because: {displaced.reason}")
                    )
                pdf.set_x(pdf.l_margin)
                entries = comparison.correction_count
                pdf.multi_cell(
                    0,
                    4,
                    _as_latin1(
                        f"  audit history ({entries} "
                        f"{'entry' if entries == 1 else 'entries'}):"
                    ),
                )
                if not event.corrections:
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(0, 4, "  - none; the event stands exactly as reported.")
                for record in event.corrections:
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(
                        0,
                        4,
                        _as_latin1(
                            f"  - {_instant_text(record.corrected_at)} by "
                            f"{_text(record.corrected_by)}: {record.field} "
                            f"{_text(record.previous_value)} -> {_text(record.new_value)}"
                        ),
                    )
                    pdf.set_x(pdf.l_margin)
                    pdf.multi_cell(
                        0, 4, _as_latin1(f"      reason: {record.reason}")
                    )
                    if record.evidence_source or record.evidence_record_id:
                        pdf.set_x(pdf.l_margin)
                        pdf.multi_cell(
                            0,
                            4,
                            _as_latin1(
                                f"      evidence: {_text(record.evidence_source)}"
                                f"{' (' + record.evidence_record_id + ')' if record.evidence_record_id else ''}"
                            ),
                        )
        return bytes(pdf.output())


__all__ = [
    "EXPORT_COLUMNS",
    "FieldValidationService",
    "parse_local_instant",
    "resolve_timezone",
]