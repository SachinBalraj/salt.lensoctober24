"""Module 20 - user-reported rainfall events, checked against the physics.

A rainfall event is a field observation, not an operational record. It joins one
reported rainfall depth with a pre-event reading and a post-event reading, so
that the rainfall-only mass balance can be run afterwards on what actually fell
and the arithmetic compared with what was actually measured. Nothing here is a
forecast: the actual rainfall is a separate field from any forecast that may
also have been made, and every value is user-reported and stored as reported.

The discipline of the module is that none of these figures is assumed to be
independently verified. They arrive as ``verification_state = "reported"`` and
stay there until a person marks them verified against a second source; nothing
in the code ever sets that flag. An event with an intervention (brine moved in
or out, fresh water added, a harvest) is flagged ``intervention_affected``,
because any such action moves the pan in a way the rainfall-only balance does
not model, and an intervention whose transferred volume was not recorded is
flagged ``unknown_intervention_volumes`` so the gap is visible rather than
silently assumed away.

Measurements keep their own timestamps, units and sources, on the pre-event and
post-event snapshots as well as on the rainfall itself. ``observation_id`` on a
snapshot links to a stored ``pan_observation`` when one exists; a reported-only
figure has no link and is not claimed to be part of the stored record.

Two further disciplines follow from what an event record can and cannot prove. A
stored instant says when the clock read, not what it was a reading *of*, so
``event_milestone`` carries that meaning and defaults to ``unknown``. And nothing
here is edited in place: ``corrections`` is an append-only history in which each
entry carries the previous value, the new value, the reason, the author, the
time, and the source evidence — so a figure that was reconciled against a record
is visibly distinct from one that was reported as it arrived.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.base import BaseDocument, UtcDateTime, new_id, normalize_pan_id, utcnow
from app.models.pan_observation import (
    PAN_ID_PATTERN,
    SALINITY_RANGE,
    WATER_DEPTH_RANGE,
)

#: Physical plausibility limits for rainfall and transfer volumes. These exist to
#: catch mistyped entries, not to police genuine measurements, and are far wider
#: than any plausible event.
RAINFALL_RANGE = {"ge": 0.0, "le": 1_000.0}
TRANSFER_VOLUME_RANGE = {"ge": 0.0, "le": 1_000_000.0}

#: Identifiers follow the pattern used across the project's records.
FIELD_ID_PATTERN = r"^[A-Za-z0-9._-]+$"


class VerificationState(str, Enum):
    """How far a field figure has been checked. Never set automatically.

    ``verified`` means a person has confirmed the figure against a second source.
    ``reported`` is the default for everything entered through this module.
    """

    REPORTED = "reported"
    VERIFIED = "verified"


class InterventionKind(str, Enum):
    BRINE_TRANSFER_OUT = "brine_transfer_out"
    BRINE_TRANSFER_IN = "brine_transfer_in"
    FRESH_WATER_ADDED = "fresh_water_added"
    HARVEST = "harvest"
    OTHER = "other"


class InterventionDirection(str, Enum):
    IN = "in"
    OUT = "out"
    NONE = "none"


class EventMilestone(str, Enum):
    """What the event timestamp is asserted to mark, when it is known.

    A stored event instant is a bare clock reading until somebody says what it
    was a clock reading *of*. ``unknown`` is the honest default and is what every
    event filed without a stated meaning carries; it is never inferred from the
    nearby rainfall timestamp, because assuming the event began when the rain did
    would silently turn a guess into an assertion.
    """

    RAINFALL_START = "rainfall_start"
    RAINFALL_END = "rainfall_end"
    EVENT_LOGGED = "event_logged"
    UNKNOWN = "unknown"


class CorrectionRecord(BaseModel):
    """One append-only entry in an event's audit history.

    Nothing in this module edits a recorded figure in place: a correction adds an
    entry carrying the value that was there before, the value that is there now,
    why the change was made, who made it, when, and which source evidence
    supports it. The earlier value therefore stays readable forever, and a
    reader can always see whether a figure was reported as-is or reconciled
    against a record.
    """

    correction_id: str = Field(
        default_factory=lambda: new_id("cor"),
        min_length=1,
        max_length=64,
        pattern=FIELD_ID_PATTERN,
    )
    #: The document path that was corrected, e.g. ``event_milestone`` or
    #: ``pre_event.timestamp``. Kept as a path so the history reads as an audit
    #: trail rather than a list of opaque notes.
    field: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_.]+$")
    #: Values are rendered as text: an audit entry must survive any later change
    #: to the types they describe.
    previous_value: str | None = Field(default=None, max_length=200)
    new_value: str | None = Field(default=None, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    corrected_at: UtcDateTime = Field(default_factory=utcnow)
    corrected_by: str | None = Field(default=None, max_length=120)
    #: The source document, record or person the correction rests on. Free text
    #: because evidence is a paper log, a gauge export or an observation id.
    evidence_source: str | None = Field(default=None, max_length=500)
    #: Identifier of a stored record that corroborates the change, when there is
    #: one. This is what makes a reconciled figure traceable rather than asserted.
    evidence_record_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=FIELD_ID_PATTERN,
    )

    @field_validator("reason", "evidence_source", "corrected_by", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class FieldMeasurement(BaseModel):
    """One reported reading of the pan, with the instant and source it carries.

    ``observation_id`` links to a stored :class:`PanObservation` when the figure
    was also recorded through the observations module; ``None`` means the figure
    is reported-only and is not claimed to be part of the stored record.

    ``timestamp_verified`` says whether the *instant* of the reading is
    corroborated by a stored record. It is False by default and stays False when
    an operator merely restates a time: a restated time is an assertion, and only
    a linked observation — a record that existed independently of this event —
    corroborates one. ``recorded_timezone`` keeps the zone the original record
    was written in, so a reader can see how the instant was derived rather than
    being handed a converted number with no provenance.
    """

    salinity_g_l: float | None = Field(
        default=None, allow_inf_nan=False, **SALINITY_RANGE
    )
    water_depth_cm: float | None = Field(
        default=None, allow_inf_nan=False, **WATER_DEPTH_RANGE
    )
    timestamp: UtcDateTime | None = None
    recorded_timezone: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=64)
    observation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=FIELD_ID_PATTERN,
    )
    observer: str | None = Field(default=None, max_length=120)
    evidence_source: str | None = Field(default=None, max_length=500)
    #: Whether ``timestamp`` is corroborated by a stored record rather than only
    #: asserted by the operator.
    timestamp_verified: bool = False
    note: str | None = Field(default=None, max_length=500)

    @field_validator(
        "source", "recorded_timezone", "observer", "evidence_source", mode="before"
    )
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _require_a_measurement(self) -> FieldMeasurement:
        if self.salinity_g_l is None and self.water_depth_cm is None:
            raise ValueError(
                "a field measurement must carry at least a salinity or a depth"
            )
        return self


class FieldIntervention(BaseModel):
    """One action a person took on the pan during an event.

    The transferred volume is optional by design: an operator often knows that
    brine was drawn out but not how much, and a record must be able to say that
    without inventing a number.
    """

    intervention_id: str = Field(
        default_factory=lambda: new_id("iv"),
        min_length=1,
        max_length=64,
        pattern=FIELD_ID_PATTERN,
    )
    kind: InterventionKind
    direction: InterventionDirection
    performed_at: UtcDateTime
    volume_m3: float | None = Field(
        default=None, allow_inf_nan=False, **TRANSFER_VOLUME_RANGE
    )
    volume_unit: Literal["m3"] = "m3"
    description: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default=None, max_length=64)

    @property
    def volume_known(self) -> bool:
        """Whether a transferred volume was recorded for this intervention."""
        return self.volume_m3 is not None


class RainfallTimestampEvidence(str, Enum):
    """How far the event's rainfall *instant* has been backed.

    A separate vocabulary from :class:`VerificationState` because the two answer
    different questions. ``verification_state`` is about the reported figures as a
    whole; this is about one instant and, crucially, about *what kind* of thing
    supports it.

    ``not_recorded`` is the default for an instant nobody has sourced.
    ``operator_reported`` means a named document records the instant but nothing
    measured it -- a field notebook, a log sheet. ``record_backed`` would mean a
    stored instrument record agrees with it, which is the only thing that could
    ever justify calling the instant an observed measurement.

    There is deliberately no path in this module that writes ``record_backed``:
    a correction always carries the evidence a person supplied, and a person
    asserting a time cannot turn it into an instrument reading.
    """

    NOT_RECORDED = "not_recorded"
    OPERATOR_REPORTED = "operator_reported"
    RECORD_BACKED = "record_backed"


class SupersededRainfallTimestamp(BaseModel):
    """A rainfall instant that was replaced, and what is known about it.

    Retained rather than deleted. When an event's rainfall instant is corrected
    because a later document dates the rain differently, the value that was there
    before does not stop having existed -- an auditor has to be able to see that
    the event used to say something else, and why that something was set aside.

    What this record is careful about is the *status* of the displaced instant.
    It is not a second measurement and is never replayed: ``meaning`` is
    ``unknown`` unless a source ever established what the time marked, and
    ``supporting_record`` is None when nothing in the database backs it. The
    origin and the claimed source are kept as claims about how the value was
    produced, explicitly separated from evidence that it was.
    """

    superseded_id: str = Field(
        default_factory=lambda: new_id("sup"),
        min_length=1,
        max_length=64,
        pattern=FIELD_ID_PATTERN,
    )
    #: The instant that was displaced, kept verbatim in UTC.
    timestamp: UtcDateTime
    #: The zone label in force when it was displaced, so the local reading is
    #: reproducible from this record alone.
    timezone_label: str | None = Field(default=None, max_length=40)
    #: What the value was, at the time it was in force. This is a label, not a
    #: citation: an audit reader has to be able to see that the project once
    #: asserted a rain-gauge reading without ever producing the gauge record.
    source_claim: str | None = Field(default=None, max_length=300)
    #: Where the value actually came from, in the sense of the code or document
    #: that introduced it -- e.g. a seed script constant, or an API call.
    origin: str = Field(min_length=1, max_length=500)
    #: A stored record that corroborates the displaced instant. None means nothing
    #: in this database supported it, which is the usual case.
    supporting_record: str | None = Field(default=None, max_length=64)
    #: What the displaced instant was understood to mark. ``unknown`` unless a
    #: source said otherwise; it is not guessed from the event's milestone.
    meaning: Literal["unknown"] = "unknown"
    superseded_at: UtcDateTime = Field(default_factory=utcnow)
    superseded_by: str | None = Field(default=None, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    #: The conflict this resolution closed, when it closed one.
    conflict_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=FIELD_ID_PATTERN
    )

    @field_validator("origin", "source_claim", "reason", "superseded_by", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class RainfallTimestampConflict(BaseModel):
    """Two irreconcilable accounts of when the rain fell, kept side by side.

    A rainfall timestamp is the one instant an event cannot safely restate on
    its own authority: the replay uses ``actual_rainfall_mm`` and the stored
    instant together, so quietly moving the instant to match a later document
    would change what the stored event *means* while looking like a correction.
    When a second account of the rain's timing arrives and disagrees, this
    record holds both — the timestamp the event still carries and the one the
    later source reports — with where the second came from and who recorded it.

    The stored timestamp is never overwritten by recording a conflict. An
    unresolved conflict blocks the event from being reported as fully reconciled
    until somebody resolves it against evidence, which is the point: the gap
    stays visible instead of being closed by a plausible guess.
    """

    conflict_id: str = Field(
        default_factory=lambda: new_id("rfc"),
        min_length=1,
        max_length=64,
        pattern=FIELD_ID_PATTERN,
    )
    #: What the event carries right now. This stays the effective timestamp.
    stored_timestamp: UtcDateTime
    #: What the later source says. Recorded, not adopted.
    reported_timestamp: UtcDateTime
    #: Where the second account comes from, e.g. the original field notebook.
    reported_source: str = Field(min_length=1, max_length=300)
    #: The signed difference between the two accounts, in seconds. Stored rather
    #: than recomputed so the number that was reported cannot drift.
    offset_seconds: int
    #: Always ``unresolved`` when recorded. Resolution is a separate, evidenced
    #: step; there is no path here that flips it automatically.
    status: Literal["unresolved", "resolved"] = "unresolved"
    detected_at: UtcDateTime = Field(default_factory=utcnow)
    recorded_by: str | None = Field(default=None, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    resolution_note: str | None = Field(default=None, max_length=2000)
    resolved_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def _require_a_real_disagreement(self) -> RainfallTimestampConflict:
        if self.stored_timestamp == self.reported_timestamp:
            raise ValueError(
                "these two accounts agree, so there is no conflict to record"
            )
        return self

    @property
    def is_unresolved(self) -> bool:
        return self.status == "unresolved"


class RainfallEvent(BaseDocument):
    """One reported rainfall event on one pan, frozen as reported.

    The pre-event and post-event snapshots and the actual rainfall are stored
    inside the event record so the report can be reproduced from the record alone
    even if the linked observations later change. The ``observation_id`` and
    ``weather_observation_id`` links preserve the trace to those stored records
    without making the event's numbers depend on them.
    """

    event_id: str = Field(
        default_factory=lambda: new_id("ev"),
        min_length=1,
        max_length=64,
        pattern=FIELD_ID_PATTERN,
    )
    pan_id: str = Field(min_length=1, max_length=64, pattern=PAN_ID_PATTERN)
    name: str | None = Field(default=None, max_length=120)
    #: The instant the rainfall happened, which is what the event is filed under.
    event_timestamp: UtcDateTime
    #: What the instant above is asserted to mark. ``unknown`` until an operator
    #: states otherwise; never inferred from the rainfall timestamp.
    event_milestone: EventMilestone = EventMilestone.UNKNOWN
    #: The zone the site works in, kept so exports can show a local time beside
    #: the UTC instant without a reader having to assume one.
    site_timezone: str | None = Field(default=None, max_length=40)
    reported_at: UtcDateTime = Field(default_factory=utcnow)
    reported_by: str | None = Field(default=None, max_length=120)
    verification_state: VerificationState = VerificationState.REPORTED

    # Actual rainfall, stored separately from any forecast. The units travel in
    # the field name; ``forecast_rainfall_mm`` is context only and is never used
    # in the retrospective prediction.
    actual_rainfall_mm: float = Field(
        allow_inf_nan=False, **RAINFALL_RANGE
    )
    rainfall_timestamp: UtcDateTime
    rainfall_source: str | None = Field(default=None, max_length=64)
    #: Which document, if any, dates the instant above, and what kind of thing
    #: that document is. Both default to "nobody has sourced this", so an instant
    #: that arrived with a report cannot read as measured until somebody says
    #: where it came from -- and saying so can never make it instrument-verified.
    rainfall_timestamp_evidence: str | None = Field(default=None, max_length=500)
    rainfall_timestamp_verification: RainfallTimestampEvidence = (
        RainfallTimestampEvidence.NOT_RECORDED
    )
    #: Rainfall instants this event used to carry, with what is known about each.
    #: Append-only: a corrected instant is superseded here rather than erased, so
    #: the earlier claim stays auditable without occupying the field a reader
    #: would mistake for a measurement.
    superseded_rainfall_timestamps: list[SupersededRainfallTimestamp] = Field(
        default_factory=list
    )
    #: Link to the stored weather observation when the actual rainfall was also
    #: recorded through the weather module.
    weather_observation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=FIELD_ID_PATTERN,
    )
    forecast_rainfall_mm: float | None = Field(
        default=None, allow_inf_nan=False, **RAINFALL_RANGE
    )

    pre_event: FieldMeasurement
    post_event: FieldMeasurement

    interventions: list[FieldIntervention] = Field(default_factory=list)
    #: The append-only audit history. Entries are only ever added, so the value a
    #: field carried before a reconciliation stays readable forever.
    corrections: list[CorrectionRecord] = Field(default_factory=list)
    #: Competing accounts of when the rain fell, appended and never merged. The
    #: stored ``rainfall_timestamp`` above stays authoritative for the replay
    #: while any entry here is unresolved.
    rainfall_timestamp_conflicts: list[RainfallTimestampConflict] = Field(
        default_factory=list
    )
    notes: str | None = Field(default=None, max_length=2000)
    created_at: UtcDateTime = Field(default_factory=utcnow)

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value: object) -> object:
        return normalize_pan_id(value) if isinstance(value, str) else value

    @property
    def intervention_affected(self) -> bool:
        """Whether any recorded action moves the pan outside the rainfall model."""
        return len(self.interventions) > 0

    @property
    def unknown_intervention_volumes(self) -> bool:
        """Whether an intervention was recorded without its transferred volume."""
        return any(not intervention.volume_known for intervention in self.interventions)

    @property
    def independently_verified(self) -> bool:
        """Whether the figures have been confirmed against a second source.

        Deliberately almost always False: nothing in this module sets the flag,
        and a report must never present a reported event as independent proof.
        """
        return self.verification_state is VerificationState.VERIFIED

    @property
    def unresolved_rainfall_timestamp_conflicts(self) -> list[RainfallTimestampConflict]:
        return [
            conflict
            for conflict in self.rainfall_timestamp_conflicts
            if conflict.is_unresolved
        ]

    @property
    def rainfall_timestamp_is_observed(self) -> bool:
        """Whether any stored instrument record backs the rainfall instant.

        False for an instant that only a person reported, however well sourced
        that report is. The distinction matters because a notebook entry and a
        gauge export are different kinds of evidence, and only the second makes
        an instant an observation rather than an assertion.
        """
        return self.rainfall_timestamp_verification is (
            RainfallTimestampEvidence.RECORD_BACKED
        )


__all__ = [
    "FIELD_ID_PATTERN",
    "RAINFALL_RANGE",
    "TRANSFER_VOLUME_RANGE",
    "CorrectionRecord",
    "EventMilestone",
    "FieldIntervention",
    "FieldMeasurement",
    "InterventionDirection",
    "InterventionKind",
    "RainfallEvent",
    "RainfallTimestampConflict",
    "RainfallTimestampEvidence",
    "SupersededRainfallTimestamp",
    "VerificationState",
]