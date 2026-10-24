"""The record of one real rainfall event on one real pan.

Every field the multi-event study needs, and one rule that governs all of them:
**a quantity nobody measured is ``None``.** Not zero. Not the mean of its
neighbours. Not the value the simulator would have produced. ``None`` — and the
aggregates downstream count it as excluded, with the reason, rather than as a
zero error.

The field names are the ones the study is specified in, kept verbatim so the CSV
a reviewer downloads and the code that produced it can be read side by side.
Units are in the names, because a salinity in g/L and a salinity in PSU differ by
a factor of 1.05 and nothing in a number says which one it is.
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime
from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.base import UtcDateTime, normalize_datetime

#: The smallest rainfall depth the mass balance will accept, and the largest.
#: Mirrors ``RAINFALL_RANGE`` in ``app.models.weather``; re-declared rather than
#: imported so this record cannot be written by a rule that the simulation
#: refuses, without the simulator ever seeing an experiment file.
MIN_RAINFALL_MM = 0.0
MAX_RAINFALL_MM = 1000.0


class EventClassification(str, Enum):
    """The three categories an event can fall into.

    Declared in the order they are *checked*, which is not the order they are
    written. ``INCOMPLETE_EVIDENCE_EVENT`` is checked first because it is a
    statement about whether the record can bear weight at all; an event whose
    transfer volume was never recorded cannot be promoted to a clean rainfall
    case by also happening to have no intervention. See
    :func:`app.experiments.classifier.classify_event` for the precedence rule.
    """

    INCOMPLETE_EVIDENCE_EVENT = "INCOMPLETE_EVIDENCE_EVENT"
    INTERVENTION_AFFECTED_EVENT = "INTERVENTION_AFFECTED_EVENT"
    CLEAN_RAINFALL_EVENT = "CLEAN_RAINFALL_EVENT"

    @property
    def is_accuracy_evidence(self) -> bool:
        """Whether this category may contribute to an accuracy figure.

        Only a clean event may. An intervention-affected event has an unknown
        contribution from the intervention, and an incomplete event has an
        unknown contribution from the missing evidence; both would put a
        confounded number into a denominator and call it simulator error.
        """
        return self is EventClassification.CLEAN_RAINFALL_EVENT


class MeasurementSource(str, Enum):
    """Who or what produced the observed figures.

    ``DEMONSTRATION`` exists as a first-class value rather than being inferred
    from a note, because an event sourced from demonstration data must never be
    counted as evidence no matter how carefully it is filled in. It is also why
    this registry cannot silently absorb the project's demonstration pans.
    """

    MANUAL_FIELD = "manual_field"
    INSTRUMENT = "instrument"
    OPERATOR_REPORTED = "operator_reported"
    DEMONSTRATION = "demonstration"


class VerificationState(str, Enum):
    """How far the recorded evidence has been corroborated.

    ``UNVERIFIED`` is the default and the honest answer for anything a single
    person reported. ``OPERATOR_REPORTED`` means the same person who ran the
    event filed the figures, which is not independent. Only
    ``INDEPENDENTLY_VERIFIED`` means a second party or an instrument corroborated
    them.
    """

    UNVERIFIED = "unverified"
    OPERATOR_REPORTED = "operator_reported"
    INDEPENDENTLY_VERIFIED = "independently_verified"


class PredictionLock(BaseModel):
    """The seal on a prediction: what it was, and what produced it.

    Sealing is what makes the prediction usable as evidence. An unsealed
    prediction is a scratch number — correct now, but with nothing recording
    that it was not adjusted once the answer was known. The four provenance
    fields exist so a reviewer can establish which code, which commit and which
    physics produced the figure they are grading.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    locked: bool = Field(
        default=False,
        description="Whether the prediction has been sealed against later change.",
    )
    locked_at: UtcDateTime | None = Field(
        default=None,
        description="When the prediction was sealed. Never moved once set.",
    )
    prediction_made_at: UtcDateTime | None = Field(
        default=None,
        description=(
            "When the prediction was computed. Must not be later than the "
            "observation it is graded against; a prediction made after the "
            "answer was known is not a prediction."
        ),
    )
    model_name: str | None = Field(
        default=None,
        max_length=128,
        description="The physics model that produced the figure, e.g. rainfall-dilution-mass-balance-v1.",
    )
    app_version: str | None = Field(default=None, max_length=32)
    git_commit: str | None = Field(
        default=None,
        max_length=64,
        description="Full commit hash of the code the prediction was computed under.",
    )
    manifest_hash: str | None = Field(
        default=None,
        max_length=128,
        description="Content hash of the frozen experiment manifest at lock time.",
    )
    content_hash: str | None = Field(
        default=None,
        max_length=128,
        description="Hash of the prediction figures themselves, so a change is detectable.",
    )

    @property
    def is_sealed(self) -> bool:
        """Whether every field a lock needs to be auditable is present."""
        return self.locked and all(
            value is not None
            for value in (
                self.locked_at,
                self.prediction_made_at,
                self.model_name,
                self.content_hash,
            )
        )


class InterventionRecord(BaseModel):
    """One thing a human did to the pan between the prediction and the reading.

    ``volume_m3`` is nullable and that null is the point. The 14 March 2026 event
    has a recorded ``brine_transfer_out`` whose volume was never written down, and
    that single absence is what stops the event from being usable as accuracy
    evidence — not the existence of the transfer.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    kind: str = Field(
        min_length=1,
        max_length=64,
        description="What was done: brine_transfer_out, drainage_out, harvest, other.",
    )
    direction: str | None = Field(
        default=None,
        max_length=32,
        description="in or out, where that applies.",
    )
    performed_at: UtcDateTime | None = Field(
        default=None,
        description="When it was done. Null when the operator did not record a time.",
    )
    volume_m3: float | None = Field(
        default=None,
        ge=0,
        description="The volume moved, in cubic metres. Null when not recorded; never 0 as a stand-in.",
    )
    volume_recorded: bool = Field(
        default=False,
        description=(
            "Whether the volume was actually written down, as distinct from being "
            "known to be zero. An intervention with no recorded volume sets this "
            "false, and that event cannot be accuracy evidence."
        ),
    )
    described_by: str | None = Field(default=None, max_length=128)
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _volume_and_flag_agree(self) -> Self:
        if self.volume_recorded and self.volume_m3 is None:
            raise ValueError(
                "volume_recorded is true but no volume was given. An intervention "
                "whose volume was not written down must set volume_recorded to "
                "false; setting it true with a null volume would claim the "
                "volume is known while the record says it is not."
            )
        if self.volume_m3 is not None and not self.volume_recorded:
            raise ValueError(
                f"a volume of {self.volume_m3} m3 was given but volume_recorded is "
                "false. If the number is on the record, say so."
            )
        return self


class ExperimentEvent(BaseModel):
    """One real rainfall event, from pre-event brine to post-event reading.

    Field groups, in the order a study needs them:

    Identity
        ``event_id``, ``pan_id``, ``date``.
    Pre-event state
        ``initial_salinity_g_l``, ``initial_depth_cm``, ``pre_event_timestamp``,
        ``observation_source_id``.
    Driver
        ``rainfall_mm``, ``rainfall_is_forecast``, ``rainfall_source``.
    Prediction
        ``predicted_salinity_g_l``, ``predicted_depth_cm``, ``prediction_lock``.
    Observation
        ``observed_salinity_g_l``, ``observed_depth_cm``, ``post_event_timestamp``,
        ``observation_recorded_at``, ``measurement_source``, ``verification_state``,
        ``independent_verification``.
    Intervention
        ``interventions``, ``brine_transfer``, ``transfer_volume_m3``,
        ``drainage_volume_m3``.
    Free text and audit
        ``notes``, ``corrections``, ``created_at``.

    ``area_m2`` is recorded per event rather than read from the pan at grading
    time. A pan's registered area can be corrected, and a prediction graded
    against an area that changed after the event is graded against the wrong
    volume.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    # -- identity -----------------------------------------------------------

    event_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Stable identifier, unique within the registry.",
    )
    pan_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="The pan this happened on. Lower-cased and stripped on the way in.",
    )
    date: date_type = Field(description="Calendar date of the rainfall event, in the pan's local day.")

    # -- pre-event state ----------------------------------------------------

    initial_salinity_g_l: float | None = Field(
        default=None, ge=0, le=1000, description="Pre-event brine salinity in g/L. Null when not measured."
    )
    initial_depth_cm: float | None = Field(
        default=None, gt=0, le=1000, description="Pre-event brine depth in cm. Null when not measured."
    )
    pre_event_timestamp: UtcDateTime | None = Field(
        default=None, description="When the pre-event reading was taken."
    )
    pre_event_timestamp_verified: bool | None = Field(
        default=None,
        description="Whether a second party or an instrument fixed that instant.",
    )
    observation_source_id: str | None = Field(
        default=None,
        max_length=128,
        description="Identifier of the stored observation the pre-event figures came from, if any.",
    )
    area_m2: float | None = Field(
        default=None, gt=0, description="Pan area at the time of the event, in m2. Null when unknown."
    )

    # -- rainfall driver ----------------------------------------------------

    rainfall_mm: float | None = Field(
        default=None,
        ge=MIN_RAINFALL_MM,
        le=MAX_RAINFALL_MM,
        description=(
            "Rainfall used to produce the prediction, in mm. Whether that was the "
            "forecast or the confirmed total is stated by rainfall_is_forecast; a "
            "forecast-driven prediction graded against an observation is measuring "
            "the forecast too."
        ),
    )
    rainfall_is_forecast: bool | None = Field(
        default=None,
        description="True when rainfall_mm is a forecast rather than a measured total.",
    )
    rainfall_source: str | None = Field(
        default=None,
        max_length=256,
        description="Where the rainfall figure came from: a gauge record, an operator report, a forecast.",
    )
    rainfall_observed: bool | None = Field(
        default=None,
        description=(
            "Whether an instrument or a second party confirmed the rainfall figure. "
            "Null means unknown, which is not the same as false."
        ),
    )
    confirmed_rainfall_mm: float | None = Field(
        default=None,
        ge=MIN_RAINFALL_MM,
        le=MAX_RAINFALL_MM,
        description=(
            "The measured rainfall total, when it differs from the figure the "
            "prediction used. Null when the prediction was driven by the confirmed "
            "total already, or when no total was measured."
        ),
    )

    # -- prediction ---------------------------------------------------------

    predicted_salinity_g_l: float | None = Field(
        default=None, ge=0, le=1000, description="Predicted post-rain salinity in g/L. Sealed by prediction_lock."
    )
    predicted_depth_cm: float | None = Field(
        default=None, gt=0, le=1000, description="Predicted post-rain depth in cm. Sealed by prediction_lock."
    )
    prediction_lock: PredictionLock = Field(default_factory=PredictionLock)

    # -- observation --------------------------------------------------------

    observed_salinity_g_l: float | None = Field(
        default=None, ge=0, le=1000, description="Measured post-event salinity in g/L. Null when not measured."
    )
    observed_depth_cm: float | None = Field(
        default=None, gt=0, le=1000, description="Measured post-event depth in cm. Null when not measured."
    )
    post_event_timestamp: UtcDateTime | None = Field(
        default=None, description="When the post-event reading was taken."
    )
    post_event_timestamp_verified: bool | None = Field(
        default=None, description="Whether a second party or an instrument fixed that instant.",
    )
    observation_recorded_at: UtcDateTime | None = Field(
        default=None,
        description="When the observation was entered into this registry. Must not precede the prediction.",
    )
    measurement_source: MeasurementSource | None = Field(
        default=None, description="Who or what produced the observed figures. Null when unrecorded."
    )
    verification_state: VerificationState = Field(
        default=VerificationState.UNVERIFIED,
        description="How far the observed figures have been corroborated.",
    )
    independent_verification: bool | None = Field(
        default=None,
        description=(
            "Explicit independent-verification flag. Null means it was never "
            "asserted, which is not a yes and is not a no."
        ),
    )
    verified_by: str | None = Field(
        default=None, max_length=128, description="Who verified it, when verification_state is not unverified."
    )

    # -- intervention -------------------------------------------------------

    interventions: list[InterventionRecord] = Field(default_factory=list)
    brine_transfer: bool | None = Field(
        default=None,
        description="Whether brine was moved. Null means not recorded either way.",
    )
    transfer_volume_m3: float | None = Field(
        default=None, ge=0, description="Brine volume transferred in m3. Null when never recorded."
    )
    drainage_volume_m3: float | None = Field(
        default=None, ge=0, description="Water drained in m3. Null when never recorded."
    )

    # -- audit --------------------------------------------------------------

    notes: str | None = Field(
        default=None,
        max_length=4000,
        description="Free text. Never parsed; never a substitute for a field.",
    )
    corrections: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Append-only history of every change made after creation, with a reason for each.",
    )
    created_at: UtcDateTime = Field(description="When this record was first written.")

    # -- derived ------------------------------------------------------------

    @property
    def has_observed_figures(self) -> bool:
        """Whether either observed figure is on the record."""
        return (
            self.observed_salinity_g_l is not None or self.observed_depth_cm is not None
        )

    @property
    def has_prediction(self) -> bool:
        return (
            self.predicted_salinity_g_l is not None or self.predicted_depth_cm is not None
        )

    @property
    def has_intervention(self) -> bool:
        """Whether any intervention is recorded, whatever its volume.

        A recorded intervention with no volume still counts as an intervention.
        Discarding it because its size is unknown would let an unknown-volume
        transfer read as a clean rainfall event.
        """
        if self.interventions:
            return True
        return bool(self.brine_transfer) or self.drainage_volume_m3 is not None

    def verified(self) -> bool:
        """Whether this event carries independently verified observed figures."""
        return (
            self.independent_verification is True
            and self.verification_state is VerificationState.INDEPENDENTLY_VERIFIED
            and self.has_observed_figures
        )

    def csv_row(self) -> dict[str, Any]:
        """One flat row for ``experiments/events/events.csv``.

        The columns are the study's own vocabulary, in the study's order. Absent
        measurements are written as an empty cell rather than a zero, so the CSV
        cannot be read as claiming a measurement nobody made.
        """
        from app.experiments.classifier import classify_event

        classification = classify_event(self)
        return {
            "event_id": self.event_id,
            "pan_id": self.pan_id,
            "date": self.date.isoformat(),
            "pre_event_timestamp": _iso(self.pre_event_timestamp),
            "post_event_timestamp": _iso(self.post_event_timestamp),
            "initial_salinity_g_l": _number(self.initial_salinity_g_l),
            "initial_depth_cm": _number(self.initial_depth_cm),
            "rainfall_mm": _number(self.rainfall_mm),
            "rainfall_is_forecast": _bool(self.rainfall_is_forecast),
            "predicted_salinity_g_l": _number(self.predicted_salinity_g_l),
            "observed_salinity_g_l": _number(self.observed_salinity_g_l),
            "predicted_depth_cm": _number(self.predicted_depth_cm),
            "observed_depth_cm": _number(self.observed_depth_cm),
            "brine_transfer": _bool(self.brine_transfer),
            "transfer_volume_m3": _number(self.transfer_volume_m3),
            "drainage_volume_m3": _number(self.drainage_volume_m3),
            "measurement_source": self.measurement_source.value
            if self.measurement_source
            else "",
            "verification_state": self.verification_state.value,
            "independent_verification": _bool(self.independent_verification),
            "intervention_status": classification.category.value,
            "included_in_metrics": _bool(classification.is_accuracy_evidence),
            "prediction_locked": _bool(self.prediction_lock.locked),
            "prediction_made_at": _iso(self.prediction_lock.prediction_made_at),
            "notes": self.notes or "",
        }


#: The CSV column order, matching :meth:`ExperimentEvent.csv_row`.
CSV_COLUMNS: tuple[str, ...] = (
    "event_id",
    "pan_id",
    "date",
    "pre_event_timestamp",
    "post_event_timestamp",
    "initial_salinity_g_l",
    "initial_depth_cm",
    "rainfall_mm",
    "rainfall_is_forecast",
    "predicted_salinity_g_l",
    "observed_salinity_g_l",
    "predicted_depth_cm",
    "observed_depth_cm",
    "brine_transfer",
    "transfer_volume_m3",
    "drainage_volume_m3",
    "measurement_source",
    "verification_state",
    "independent_verification",
    "intervention_status",
    "included_in_metrics",
    "prediction_locked",
    "prediction_made_at",
    "notes",
)


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return normalize_datetime(value).isoformat()


def _number(value: float | None) -> str | float:
    """A number as text, or an empty cell.

    ``None`` becomes ``""`` and never ``0``. A CSV reader that cannot tell an
    absent measurement from a measured zero is exactly the reader this registry
    must not be handed.
    """
    if value is None:
        return ""
    return value


def _bool(value: bool | None) -> str | bool:
    return "" if value is None else value


__all__ = [
    "CSV_COLUMNS",
    "MAX_RAINFALL_MM",
    "MIN_RAINFALL_MM",
    "EventClassification",
    "ExperimentEvent",
    "InterventionRecord",
    "MeasurementSource",
    "PredictionLock",
    "VerificationState",
]
