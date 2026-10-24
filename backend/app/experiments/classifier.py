"""Phase 5 - deciding what kind of evidence an event is.

Three categories, and the order they are checked is the whole argument.

``INCOMPLETE_EVIDENCE_EVENT``
    The record cannot bear weight, whatever else is true of it. An unrecorded
    transfer volume, a demonstration source, a missing verification, an
    unconfirmed post-event instant. Checked **first**, because these are
    statements about the record's trustworthiness and a record that fails them
    cannot be rescued by also being clean. A pan with no intervention and no
    recorded post-event salinity is not a clean rainfall event; it is an event
    with nothing to grade against.

``INTERVENTION_AFFECTED_EVENT``
    A human moved water, and the model's mass balance does not model that. The
    simulation excludes brine transfer, drainage, overflow and seepage by name
    (``SIMULATION_ASSUMPTIONS`` in ``app.services.mass_balance``). Any difference
    between prediction and observation is then partly a statement about the
    intervention, not about the physics, so it cannot be counted as physics error.

``CLEAN_RAINFALL_EVENT``
    No intervention, and the evidence is good enough to grade. The only category
    that contributes to accuracy figures.

The 14 March 2026 event classifies as ``INCOMPLETE_EVIDENCE_EVENT``, and it did
not need the intervention to get there: its transfer volume is unrecorded, its
post-event timestamp is uncorroborated, its rainfall timestamp is
``operator_reported`` with ``observed: false``, and its ``verification_state`` is
``reported``. Four independent grounds, any one of which is sufficient. It is
*also* intervention-affected, and that is preserved in
:attr:`ClassificationResult.intervention_affected` rather than being lost to the
precedence rule.

Nothing here grades an event. Classification only decides whether an event is
*allowed* to contribute to an aggregate; :mod:`app.experiments.metrics` does the
grading and applies the same rule.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.experiments.models import (
    EventClassification,
    ExperimentEvent,
    MeasurementSource,
    VerificationState,
)

CLEAN_RAINFALL_EVENT = EventClassification.CLEAN_RAINFALL_EVENT
INTERVENTION_AFFECTED_EVENT = EventClassification.INTERVENTION_AFFECTED_EVENT
INCOMPLETE_EVIDENCE_EVENT = EventClassification.INCOMPLETE_EVIDENCE_EVENT

#: Measurement sources that are never evidence, whatever else is recorded.
_NON_EVIDENTIAL_SOURCES = frozenset({MeasurementSource.DEMONSTRATION})


@dataclass(frozen=True)
class ClassificationResult:
    """One event's category, with the reasoning that produced it.

    ``reasons`` is not decoration. A category with no stated reason is a label a
    reader has to take on trust, and the whole point of the three-category scheme
    is that an event's exclusion from a metric is defensible line by line.
    """

    event_id: str
    pan_id: str
    category: EventClassification

    #: Whether any intervention was recorded. Kept independently of ``category``
    #: because an incomplete event is frequently also intervention-affected, and
    #: collapsing the two would lose the reason it matters.
    intervention_affected: bool

    #: Whether the observed figures were independently verified.
    verification_state_is_independent: bool = False

    #: Every reason the event is not a clean rainfall event, in a stable order.
    #: A clean event carries one, saying so, so an empty tuple never has to be
    #: explained to a reader who was expecting to find a reason.
    reasons: tuple[str, ...] = ()

    #: The specific evidence gaps, for the incomplete category. Separated from
    #: :attr:`reasons` because these are the things that would have to be measured
    #: to make the event usable, which is what a field visit needs to be told.
    evidence_gaps: tuple[str, ...] = ()

    @property
    def is_accuracy_evidence(self) -> bool:
        """Whether this event may contribute to an accuracy figure."""
        return self.category.is_accuracy_evidence

    @property
    def is_verified(self) -> bool:
        """Whether this event is clean *and* independently verified.

        The conjunction matters. An event can carry an independent verification
        and still be unusable for accuracy because a brine transfer confounds it,
        and counting it on the strength of the verification alone would be the
        exact error this scheme exists to prevent.
        """
        return (
            self.category is CLEAN_RAINFALL_EVENT
            and self.verification_state_is_independent
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "pan_id": self.pan_id,
            "category": self.category.value,
            "intervention_affected": self.intervention_affected,
            "is_accuracy_evidence": self.is_accuracy_evidence,
            "independently_verified": self.verification_state_is_independent,
            "reasons": list(self.reasons),
            "evidence_gaps": list(self.evidence_gaps),
        }


def classify_event(event: ExperimentEvent) -> ClassificationResult:
    """Categorise one event.

    Deliberately a pure function of the record: no clock, no database, no
    defaulting. The same record always classifies the same way, which is what
    lets a classification be re-checked later against the same code.
    """
    gaps: list[str] = []
    reasons: list[str] = []
    intervention_affected = event.has_intervention

    # -- 1. evidence gaps, checked first ------------------------------------
    if event.has_observed_figures is False:
        gaps.append(
            "No post-event salinity or depth was recorded, so there is nothing "
            "to compare a prediction against."
        )
    if event.has_prediction is False:
        gaps.append(
            "No prediction was recorded for this event, so there is nothing to "
            "grade."
        )
    if not event.prediction_lock.locked:
        gaps.append(
            "The prediction was never sealed, so it cannot be shown to predate "
            "the observation it is graded against."
        )
    if event.measurement_source is None:
        gaps.append(
            "No measurement source was recorded, so whether these figures came "
            "from a field reading, an instrument or a demonstration fixture is "
            "unknown."
        )
    elif event.measurement_source in _NON_EVIDENTIAL_SOURCES:
        gaps.append(
            f"The measurement source is {event.measurement_source.value!r}. "
            "Demonstration records describe no pan and are never evidence."
        )
    if event.independent_verification is not True:
        gaps.append(
            "No independent verification was asserted. A figure reported by the "
            "person who ran the event is not independently verified."
        )
    if event.verification_state is not VerificationState.INDEPENDENTLY_VERIFIED:
        gaps.append(
            f"Verification state is {event.verification_state.value!r}, not "
            "'independently_verified'."
        )
    if event.rainfall_observed is not True:
        gaps.append(
            "The rainfall figure was not confirmed by an instrument or a second "
            "party, so the event's driving input rests on one person's report."
        )
    if event.post_event_timestamp is None:
        gaps.append(
            "No post-event timestamp was recorded, so the reading cannot be "
            "placed after the rain."
        )
    elif event.post_event_timestamp_verified is not True:
        gaps.append(
            "The post-event timestamp was not verified, so the reading's position "
            "in the event rests on one person's record."
        )
    if event.initial_salinity_g_l is None or event.initial_depth_cm is None:
        gaps.append(
            "The pre-event salinity or depth was not recorded, so the mass "
            "balance has no starting state to dilute."
        )

    # An intervention is not itself an evidence gap -- but an intervention whose
    # volume was never written down is, because the difference between
    # prediction and observation then cannot be decomposed into the part the
    # model explains and the part the intervention explains.
    for intervention in event.interventions:
        if not intervention.volume_recorded or intervention.volume_m3 is None:
            gaps.append(
                f"Intervention {intervention.kind!r} has no recorded volume, so "
                "its contribution to the observed change cannot be separated "
                "from the rainfall's."
            )

    if event.brine_transfer is True and event.transfer_volume_m3 is None:
        gaps.append(
            "A brine transfer was reported but its volume was never recorded."
        )

    # -- 2. interventions ---------------------------------------------------
    if intervention_affected:
        kinds = sorted({intervention.kind for intervention in event.interventions})
        described = ", ".join(kinds) if kinds else "an unlisted intervention"
        reasons.append(
            f"The pan was intervened with ({described}) between the prediction "
            "and the reading. The mass balance excludes brine transfer, "
            "drainage, overflow and seepage by name, so a difference here is "
            "partly a statement about the intervention rather than about the "
            "physics."
        )
        if event.drainage_volume_m3 is None and any(
            "drain" in intervention.kind for intervention in event.interventions
        ):
            reasons.append(
                "No drainage volume was recorded, so the drained water cannot be "
                "removed from the comparison."
            )

    # -- 3. forecast-driven prediction --------------------------------------
    if event.rainfall_is_forecast is True:
        reasons.append(
            "The prediction was driven by a rainfall forecast rather than a "
            "measured total. A difference against the observation measures the "
            "forecast and the physics together, not the physics alone."
        )
    elif event.rainfall_is_forecast is None and event.rainfall_mm is not None:
        reasons.append(
            "It was not recorded whether the driving rainfall was a forecast or "
            "a measured total."
        )

    # -- 4. category --------------------------------------------------------
    if gaps:
        category = INCOMPLETE_EVIDENCE_EVENT
        reasons = [*gaps, *reasons]
    elif intervention_affected or event.rainfall_is_forecast is True:
        category = INTERVENTION_AFFECTED_EVENT
    else:
        category = CLEAN_RAINFALL_EVENT
        reasons = reasons or [
            (
                "No intervention was recorded, the rainfall total was confirmed, the "
                "reading was independently verified and the prediction was sealed "
                "before the observation was entered."
            )
        ]

    return ClassificationResult(
        event_id=event.event_id,
        pan_id=event.pan_id,
        category=category,
        intervention_affected=intervention_affected,
        reasons=tuple(reasons),
        evidence_gaps=tuple(gaps),
        verification_state_is_independent=(
            event.independent_verification is True
            and event.verification_state is VerificationState.INDEPENDENTLY_VERIFIED
        ),
    )


def classify_all(events: list[ExperimentEvent]) -> list[ClassificationResult]:
    """Classify a whole registry, ordered as given."""
    return [classify_event(event) for event in events]


__all__ = [
    "CLEAN_RAINFALL_EVENT",
    "INCOMPLETE_EVIDENCE_EVENT",
    "INTERVENTION_AFFECTED_EVENT",
    "ClassificationResult",
    "classify_all",
    "classify_event",
]
