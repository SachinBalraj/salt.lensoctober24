"""Module 20 - the HTTP contract for field validation of rainfall events.

This contract exists so a field report can be stored exactly as it arrived: the
pre-event and post-event readings, the actual rainfall (kept separate from any
forecast), and any interventions the operator took during the event. Nothing is
inferred here and nothing is assumed verified.

The comparison block is the research artefact of the module. It replays the
rainfall-only mass balance against the actual rainfall on the recorded starting
brine, and reports the predicted salinity and depth next to the reported post
values, with absolute differences in each unit. It also reports the flags a
reader must know to interpret a disagreement honestly:

* ``intervention_affected`` is true when the event carried any recorded action,
  because the rainfall-only balance models rain alone;
* ``unknown_intervention_volumes`` is true when an intervention was recorded
  without its transferred volume, which is the exact case in which the balance
  cannot even estimate the action's effect;
* ``independently_verified`` is true only when a person has confirmed the event
  against a second source. It is false by default and is never set by the API.

Instants are returned twice, in UTC and in the site's local zone, as separate
fields. A single converted number hides how it was derived and invites a reader
to convert it a second time; two labelled columns do not. ``event_milestone``
says what the event instant marks — or ``unknown``, which is the honest state
until an operator establishes it — and ``corrections`` carries the append-only
record of anything that was later reconciled against a source document.

The rainfall instant is the one figure that may be *replaced* rather than only
annotated, and it comes with its own vocabulary because of that.
``rainfall_timestamp_evidence`` names the document that dates the instant and
``rainfall_timestamp_verification`` says what kind of thing that document is:
``not_recorded`` when nobody has sourced it, ``operator_reported`` when a
notebook or log records the time. Both default to "unproven", and neither can
reach ``record_backed``, because a document that records a time does not measure
one. ``superseded_rainfall_timestamps`` carries the instants the event used to
hold, each with ``meaning`` fixed at ``unknown`` and its ``supporting_record``
usually ``None``: the displaced value stays auditable without ever being offered
as a second reading of when the rain fell.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.base import UtcDateTime
from app.models.field_validation import (
    FIELD_ID_PATTERN,
    CorrectionRecord,
    EventMilestone,
    FieldIntervention,
    FieldMeasurement,
    InterventionDirection,
    InterventionKind,
    RainfallTimestampEvidence,
    VerificationState,
)
from app.models.pan_observation import PAN_ID_PATTERN

#: Pan identifiers are normalized to lowercase everywhere in this project.
PAN_ID_DESCRIPTION = "Lowercase identifier of the registered salt pan."

#: Fields a correction may restate. The whitelist is deliberate: an append-only
#: audit history is only useful if every entry names one of a known set of
#: document paths, and only safe if a measured value cannot be corrected without
#: evidence and an append-only entry recording what it was before.
CORRECTABLE_FIELDS = (
    "event_milestone",
    "pre_event_timestamp",
    "pre_event_observer",
    "pre_event_evidence",
    "post_event_timestamp",
    "post_event_observer",
    "post_event_evidence",
)


class RainfallEventCreate(BaseModel):
    """The request body for filing one reported rainfall event.

    Only the actual rainfall is required from the two rainfall figures, and the
    pre-event snapshot must carry a starting salinity and depth because the
    balance cannot run without them. The post-event snapshot may hold a salinity,
    a depth, or both.
    """

    model_config = ConfigDict(extra="forbid", json_schema_extra={
        "description": "One user-reported rainfall event. Nothing is verified on arrival."
    })

    pan_id: str = Field(
        min_length=1, max_length=64, pattern=PAN_ID_PATTERN, description=PAN_ID_DESCRIPTION
    )
    name: str | None = Field(default=None, max_length=120)
    event_timestamp: UtcDateTime
    #: What the event instant marks. Omit it and it stays ``unknown``; the module
    #: never infers the milestone from the rainfall timestamp.
    event_milestone: EventMilestone = EventMilestone.UNKNOWN
    event_timezone: str | None = Field(default=None, max_length=40)
    reported_by: str | None = Field(default=None, max_length=120)

    #: Actual rainfall, stored separately from any forecast. ``forecast_rainfall_mm``
    #: is optional context and is never used in the retrospective prediction.
    actual_rainfall_mm: float = Field(
        ge=0.0, le=1_000.0, description="Actual rainfall depth in millimetres."
    )
    rainfall_timestamp: UtcDateTime
    rainfall_source: str | None = Field(default=None, max_length=64)
    #: The document that dates ``rainfall_timestamp``, and what kind of thing it
    #: is. Both are optional so a report can be filed exactly as it arrived --
    #: which for an unsourced instant means ``None``/``not_recorded``, never a
    #: value that reads as measured. A later correction through
    #: ``RainfallTimestampResolutionCreate`` is the only way these may change
    #: afterwards.
    rainfall_timestamp_evidence: str | None = Field(default=None, max_length=500)
    rainfall_timestamp_verification: RainfallTimestampEvidence = Field(
        default=RainfallTimestampEvidence.NOT_RECORDED,
        description="How the rainfall instant is supported. Capped at "
        "operator_reported: a document that records a time does not measure one, "
        "so filing an event can never mark its instant instrument-verified.",
    )
    weather_observation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=FIELD_ID_PATTERN,
        description="Optional link to the stored weather record with this actual rainfall.",
    )
    forecast_rainfall_mm: float | None = Field(
        default=None,
        ge=0.0,
        le=1_000.0,
        description="Optional forecast that was made before the event; context only.",
    )

    pre_event: FieldMeasurement
    post_event: FieldMeasurement

    interventions: list[FieldIntervention] = Field(
        default_factory=list,
        description="Actions taken on the pan during the event, with transferred volumes "
        "omitted when not recorded.",
    )
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _cap_rainfall_timestamp_verification(self) -> RainfallEventCreate:
        if self.rainfall_timestamp_verification is RainfallTimestampEvidence.RECORD_BACKED:
            raise ValueError(
                "rainfall_timestamp_verification cannot be record_backed. Filing an "
                "event states that someone recorded a time; only a stored instrument "
                "record could make it measured, and there is none."
            )
        # Naming the document that dates the instant is itself a claim that
        # somebody recorded it, so the standing is inferred rather than left
        # blank. It is still capped at operator_reported by the check above.
        if (
            self.rainfall_timestamp_evidence
            and self.rainfall_timestamp_verification
            is RainfallTimestampEvidence.NOT_RECORDED
        ):
            self.rainfall_timestamp_verification = (
                RainfallTimestampEvidence.OPERATOR_REPORTED
            )
        return self


class FieldInterventionCreate(BaseModel):
    """One intervention appended to an existing event."""

    model_config = ConfigDict(extra="forbid")

    kind: InterventionKind
    direction: InterventionDirection
    performed_at: UtcDateTime
    volume_m3: float | None = Field(
        default=None,
        ge=0.0,
        le=1_000_000.0,
        description="Transferred volume in cubic metres; omit when not recorded.",
    )
    description: str | None = Field(default=None, max_length=500)
    source: str | None = Field(default=None, max_length=64)


class EventCorrectionCreate(BaseModel):
    """One reconciliation step appended to an event's audit history.

    A correction never rewrites history silently: it names the document path it
    touches, carries the new value, and records a reason. Restating a *timestamp*
    additionally demands an ``evidence_source`` — where the original record lives —
    because an unevidenced time is an invention, and the point of this endpoint is
    to attach provenance to a figure that arrived without one.

    The correction never marks the event independently verified, and a restated
    timestamp does not corroborate itself: ``timestamp_verified`` only becomes
    true when the instant is taken from a stored record, through
    :class:`PreEventObservationLink`.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(
        description="Which document path to correct.",
        json_schema_extra={"enum": list(CORRECTABLE_FIELDS)},
    )
    reason: str = Field(
        min_length=1, max_length=1000, description="Why this change is being made."
    )
    event_milestone: EventMilestone | None = Field(
        default=None, description="New value; required when field is event_milestone."
    )
    timestamp: UtcDateTime | None = Field(
        default=None,
        description="New value; required when the field is a snapshot timestamp.",
    )
    recorded_timezone: str | None = Field(
        default=None,
        max_length=40,
        description="The zone the original record was written in, when known.",
    )
    observer: str | None = Field(default=None, max_length=120)
    evidence_source: str | None = Field(default=None, max_length=500)
    corrected_by: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _check_value_matches_field(self) -> EventCorrectionCreate:
        if self.field not in CORRECTABLE_FIELDS:
            # The rainfall instant is deliberately absent from the whitelist, so
            # name the route that can move it rather than leaving a caller to guess
            # which of the endpoints is the sanctioned one.
            raise ValueError(
                f"field must be one of: {', '.join(CORRECTABLE_FIELDS)}. The event's "
                "rainfall_timestamp cannot be corrected here: use "
                "POST /api/field-validation/events/{event_id}/rainfall-timestamp-"
                "resolution, which supersedes the value it replaces instead of "
                "silently overwriting it."
            )
        if self.field == "event_milestone":
            if self.event_milestone is None:
                raise ValueError(
                    "event_milestone is required when correcting event_milestone."
                )
        elif self.field.endswith("_timestamp"):
            if self.timestamp is None:
                raise ValueError(
                    f"timestamp is required when correcting {self.field}."
                )
            if not self.evidence_source:
                raise ValueError(
                    f"evidence_source is required when correcting {self.field}: a "
                    "restated time must say which original record it came from."
                )
        elif self.field.endswith("_observer"):
            if not self.observer:
                raise ValueError(f"observer is required when correcting {self.field}.")
        elif not (self.evidence_source or self.observer):
            raise ValueError(
                f"evidence_source or observer is required when correcting {self.field}."
            )
        return self


class PreEventObservationLink(BaseModel):
    """A genuine stored observation to reconcile the pre-event snapshot against.

    The link is the only route by which a pre-event snapshot becomes traceable:
    the observation must already exist, belong to the same pan, and predate the
    rainfall the snapshot starts from. Everything the module knows about *when*
    and *who* the reading was taken by is then copied from that record, and the
    snapshot's timestamp becomes corroborated rather than asserted.
    """

    model_config = ConfigDict(extra="forbid")

    observation_id: str = Field(
        min_length=1, max_length=64, pattern=FIELD_ID_PATTERN
    )
    #: The date and time exactly as the original record writes it, before any
    #: conversion. Required so the stored instant can be checked against the
    #: record that documents it rather than merely restated.
    measured_at_local: str = Field(
        min_length=1,
        max_length=40,
        description="The original record's local date and time, e.g. 2026-03-13T13:30.",
    )
    measured_timezone: str = Field(
        min_length=1,
        max_length=40,
        description="The zone that local reading was written in.",
    )
    observer: str = Field(min_length=1, max_length=120)
    evidence_source: str = Field(
        min_length=1,
        max_length=500,
        description="Where the original record lives, e.g. a field log reference.",
    )
    reason: str = Field(min_length=1, max_length=1000)
    corrected_by: str | None = Field(default=None, max_length=120)


class RainfallTimestampConflictCreate(BaseModel):
    """A second account of when the rain fell, which disagrees with the stored one.

    Recording a conflict is not correcting the timestamp. The event keeps the
    ``rainfall_timestamp`` it was filed with, because the replay pairs that
    instant with the recorded depth and quietly moving it would change what the
    stored event means while looking like housekeeping. What this does is put
    both accounts on the record, name the source of the second, and block the
    event from being reported as reconciled until the disagreement is resolved
    against evidence.
    """

    model_config = ConfigDict(extra="forbid")

    #: When the rain fell, as the later source dates it. Given with an explicit
    #: offset or a named zone so it is never assumed to be in the site's zone.
    reported_timestamp: UtcDateTime
    reported_timezone: str | None = Field(
        default=None,
        max_length=40,
        description="The zone the reported time was written in, when it is not UTC.",
    )
    reported_source: str = Field(
        min_length=1,
        max_length=300,
        description="Where the second account comes from, e.g. the field notebook.",
    )
    reason: str = Field(min_length=1, max_length=2000)
    recorded_by: str | None = Field(default=None, max_length=120)


class RainfallTimestampConflictRead(BaseModel):
    """One preserved disagreement between two rainfall timestamps."""

    model_config = ConfigDict(from_attributes=True)

    conflict_id: str
    stored_timestamp_utc: str | None = None
    stored_timestamp_ist: str | None = None
    reported_timestamp_utc: str | None = None
    reported_timestamp_ist: str | None = None
    reported_source: str
    offset_seconds: int
    offset_human: str
    status: Literal["unresolved", "resolved"]
    detected_at: str
    recorded_by: str | None = None
    reason: str
    resolution_note: str | None = None
    resolved_at: str | None = None


class SupersededRainfallTimestampRead(BaseModel):
    """One displaced rainfall instant, and what is known about it.

    Rendered with its meaning and its (absent) supporting record precisely so a
    reader cannot mistake it for a second measurement of when the rain fell. It
    is history, not data.
    """

    model_config = ConfigDict(from_attributes=True)

    superseded_id: str
    timestamp_utc: str | None = None
    timestamp_ist: str | None = None
    timezone_label: str | None = None
    source_claim: str | None = None
    origin: str
    supporting_record: str | None = None
    meaning: Literal["unknown"]
    superseded_at: str
    superseded_by: str | None = None
    reason: str
    conflict_id: str | None = None


class RainfallTimestampResolutionCreate(BaseModel):
    """A controlled, evidenced replacement of the event's rainfall instant.

    This is the *only* sanctioned way to change ``rainfall_timestamp``, and it
    exists because the generic correction endpoint refuses that field. Restating a
    time is normally an invention; changing the instant a stored event was filed
    under is a different and rarer act, because that instant is what the event
    means. It is therefore only permitted when the operator can say three things:

    * the new instant, with the zone it was written in;
    * the document that dates it (``evidence_source``) -- a field notebook, a gauge
      export, a log sheet;
    * where the *displaced* value came from (``superseded_origin``), so the audit
      trail shows how a time the project no longer stands behind entered the
      record in the first place.

    What this can never do is upgrade the instant's standing. ``verification``
    defaults to ``operator_reported`` and is capped there: a person supplying a
    document is reporting a time, not measuring it, so there is no value of this
    field that makes a notebook entry an instrument-verified rain-gauge reading.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: UtcDateTime | None = Field(
        default=None,
        description="The evidence-backed instant now in force, given with an explicit "
        "offset. Either this or `timestamp_local` must be supplied.",
    )
    timestamp_local: str | None = Field(
        default=None,
        max_length=64,
        description="The instant as a local clock reading, e.g. '2026-03-14T23:30', "
        "resolved server-side against `timestamp_timezone`. Preferred from a browser, "
        "so no client ever converts a time zone.",
    )
    timestamp_timezone: str | None = Field(
        default=None,
        max_length=40,
        description="The zone `timestamp_local` was written in: an IANA name "
        "(Asia/Kolkata), an abbreviation (IST) or an offset (UTC+05:30). Refused "
        "rather than guessed when unreadable.",
    )
    evidence_source: str = Field(
        min_length=1,
        max_length=500,
        description="The document that dates this instant, e.g. 'North Pan field "
        "notebook (manual field measurement)'. A person's report of a time, not an "
        "instrument reading.",
    )
    superseded_origin: str = Field(
        min_length=1,
        max_length=500,
        description="Where the instant being replaced came from, e.g. a seed script "
        "constant or an API call. Recorded so the displaced value's provenance is "
        "auditable rather than forgotten.",
    )
    superseded_source_claim: str | None = Field(
        default=None,
        max_length=300,
        description="The source label the displaced value carried, e.g. 'manual rain "
        "gauge'. Stored as a claim about provenance, never as evidence for it.",
    )
    superseded_supporting_record: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=FIELD_ID_PATTERN,
        description="A stored record that actually supported the displaced instant. "
        "Leave it out when nothing did, which is the usual case.",
    )
    reason: str = Field(
        min_length=1,
        max_length=2000,
        description="Why the displaced value was set aside and this one adopted.",
    )
    verification: RainfallTimestampEvidence = Field(
        default=RainfallTimestampEvidence.OPERATOR_REPORTED,
        description="How the new instant is supported. Capped at operator_reported: a "
        "supplied document reports a time, it does not measure one.",
    )
    corrected_by: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _cap_verification(self) -> RainfallTimestampResolutionCreate:
        if self.verification is RainfallTimestampEvidence.RECORD_BACKED:
            raise ValueError(
                "verification cannot be record_backed. No stored instrument record "
                "supports this instant, so it is operator-reported; claiming a gauge "
                "reading would assert evidence that does not exist."
            )
        if self.timestamp is None and not (self.timestamp_local and self.timestamp_timezone):
            raise ValueError(
                "supply the new instant as timestamp (with an explicit offset) or as "
                "timestamp_local together with timestamp_timezone."
            )
        if self.timestamp is not None and self.timestamp_local is not None:
            raise ValueError(
                "supply the new instant once, as timestamp or as timestamp_local, "
                "not both: two readings of one instant is the ambiguity this module "
                "refuses to guess at."
            )
        if self.timestamp_local is not None and not self.timestamp_timezone:
            raise ValueError(
                "timestamp_timezone is required with timestamp_local, so the local "
                "clock reading is never placed on the timeline by assumption."
            )
        return self


class FieldValidationComparison(BaseModel):
    """The rainfall-only replay of one event, and how the report fared.

    The predicted figures come from :model:`rainfall-dilution-mass-balance-v1`
    (Module 6) applied to the event's starting brine and its actual rainfall.
    The absolute differences are coarse validation numbers, not proof: the flags
    below state whether an action or an unrecorded volume or a lack of
    independent verification should temper the reading of them.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str
    pan_id: str
    pan_name: str | None = None
    event_timestamp: UtcDateTime | None = None
    #: What the event instant marks. ``unknown`` until an operator establishes it.
    event_milestone: EventMilestone = EventMilestone.UNKNOWN
    #: The zone the ``*_ist`` fields are rendered in, always named so a reader
    #: never has to assume which local time they are looking at.
    site_timezone_label: str = ""
    event_timestamp_utc: str | None = None
    event_timestamp_ist: str | None = None
    rainfall_timestamp_utc: str | None = None
    rainfall_timestamp_ist: str | None = None
    #: Which document dates the instant above, and what kind of thing it is.
    #: ``operator_reported`` means a person recorded the time; only
    #: ``record_backed`` would mean an instrument measured it, and nothing in this
    #: module can set that.
    rainfall_timestamp_evidence: str | None = None
    rainfall_timestamp_verification: RainfallTimestampEvidence = (
        RainfallTimestampEvidence.NOT_RECORDED
    )
    #: True only when a stored instrument record backs the instant. A well-sourced
    #: operator report leaves this False, which is the point: a notebook entry is
    #: not a rain-gauge reading.
    rainfall_timestamp_observed: bool = False
    #: Instants this event used to carry. Rendered as history with their meaning
    #: and their absent supporting record, so a displaced value can never be read
    #: as a second measurement.
    superseded_rainfall_timestamps: list[SupersededRainfallTimestampRead] = Field(
        default_factory=list
    )
    reported_by: str | None = None
    notes: str | None = None
    model_name: str = "rainfall-dilution-mass-balance-v1"
    model_disclaimer: str = ""

    actual_rainfall_mm: float
    #: The forecast made before the event, when one was recorded. Context only:
    #: the replay uses ``actual_rainfall_mm`` and never this figure.
    forecast_rainfall_mm: float | None = None
    #: ``None`` when the pre-event snapshot is missing the salinity or the depth,
    #: so the balance cannot run; a missing prediction is never rendered as zero.
    predicted_salinity_g_l: float | None = None
    predicted_depth_cm: float | None = None

    reported_salinity_g_l: float | None
    reported_depth_cm: float | None
    salinity_abs_diff_g_l: float | None = None
    depth_abs_diff_cm: float | None = None

    # -- provenance of the snapshots -------------------------------------
    pre_event_salinity_g_l: float | None = None
    pre_event_depth_cm: float | None = None
    pre_event_timestamp_utc: str | None = None
    pre_event_timestamp_ist: str | None = None
    pre_event_observation_id: str | None = None
    pre_event_observer: str | None = None
    pre_event_timestamp_verified: bool = False
    post_event_timestamp_utc: str | None = None
    post_event_timestamp_ist: str | None = None
    post_event_observation_id: str | None = None
    post_event_observer: str | None = None
    #: Whether the post-event instant is corroborated by a stored record. A
    #: retained-but-uncorroborated timestamp stays False and is reported as such.
    post_event_timestamp_verified: bool = False
    weather_observation_id: str | None = None
    #: The append-only audit history: every reconciliation ever applied, newest last.
    corrections: list[CorrectionRecord] = Field(default_factory=list)
    correction_count: int = 0
    #: Competing accounts of when the rain fell. The stored
    #: ``rainfall_timestamp`` stays in force; these are recorded, not adopted.
    rainfall_timestamp_conflicts: list[RainfallTimestampConflictRead] = Field(
        default_factory=list
    )
    unresolved_conflict_count: int = 0
    #: ``blocked_by_evidence`` while any conflict is unresolved: the event cannot
    #: be called reconciled while two records disagree and only one is in force.
    #: ``incomplete`` when the milestone, the pre-event source or the post-event
    #: corroboration is still missing. ``complete`` only when neither applies.
    reconciliation_state: Literal["complete", "incomplete", "blocked_by_evidence"] = (
        "incomplete"
    )

    intervention_affected: bool = False
    unknown_intervention_volumes: bool = False
    independently_verified: bool = False
    verification_state: VerificationState = VerificationState.REPORTED

    caveats: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


class FieldValidationSummary(BaseModel):
    """Roll-up of how the events' reported numbers compare with the replay."""

    model_config = ConfigDict(extra="forbid")

    total_events: int
    compared_events: int
    events_with_salinity: int
    events_with_depth: int
    intervention_affected_count: int
    unknown_volume_count: int
    verified_count: int
    #: Events whose instant is filed without a stated meaning. These cannot be
    #: placed relative to the rainfall, so they are counted, not hidden.
    events_with_unknown_milestone: int = 0
    #: Events whose pre-event snapshot has no linked stored observation, i.e. the
    #: starting brine is reported-only and untraceable to a record.
    events_with_unlinked_pre_event: int = 0
    #: Events whose post-event instant has not been corroborated by a record.
    uncorroborated_post_event_timestamps: int = 0
    #: Events carrying at least one unresolved rainfall-timestamp conflict. These
    #: cannot be called reconciled, whatever else is missing.
    events_blocked_on_evidence: int = 0
    #: Events with nothing outstanding against them: milestone established,
    #: pre-event linked to a stored record, and no evidence conflict.
    fully_reconciled_events: int = 0
    mean_abs_salinity_diff_g_l: float | None = None
    mean_abs_depth_diff_cm: float | None = None
    caveats: list[str] = Field(default_factory=list)


class StatusReport(BaseModel):
    """What this module can and cannot validate right now.

    The ``ready`` flag is advisory only: even with no event filed, the module can
    store a report. It is surfaced so the dashboard can say plainly that no field
    data has been recorded yet rather than render an empty dashboard as if it
    were a healthy one.
    """

    model_config = ConfigDict(extra="forbid")

    module: str = "field-validation"
    event_count: int
    ready: bool
    message: str


__all__ = [
    "CORRECTABLE_FIELDS",
    "EventCorrectionCreate",
    "FieldInterventionCreate",
    "FieldValidationComparison",
    "FieldValidationSummary",
    "PreEventObservationLink",
    "RainfallEventCreate",
    "RainfallTimestampResolutionCreate",
    "StatusReport",
]