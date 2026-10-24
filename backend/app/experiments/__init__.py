"""Module 21 - the reproducible multi-event experiment registry.

This package answers one question and refuses to answer another.

The question: **given a set of real rainfall events on real salt pans, how close
did SaltLens get, and can that comparison be trusted?**

The refused question: **what would SaltLens have scored if the events had been
cleaner than they were.** There is no path here that imputes a measurement,
smooths an intervention, drops an inconvenient event from a denominator, or
computes a mean over a set that does not clear a declared sufficiency gate.

Everything is built on three commitments that the rest of the codebase already
holds to and that this package makes explicit:

*Prediction first, then lock, then observe.* A prediction is produced by the
real :class:`~app.services.mass_balance.MassBalanceService`, recorded with the
model name, the application version, the git commit and the frozen manifest hash
it was computed under, and then sealed. Once sealed, no write — including a
write that knows the answer — can change it. That is the only defence against
fitting a prediction to an observation after the fact, and it is enforced by the
store rather than by convention (:mod:`app.experiments.store`).

*Nothing measured stays null.* An unknown transfer volume is ``None``, not zero.
A missing post-event salinity is ``None``, not an error of zero. The existing
:class:`~app.models.evaluation.MetricReading` discipline is reused rather than
reinvented, so a metric the data could not support is reported as
``computed=False`` with a reason.

*Absence is a result, not a gap.* With zero qualifying events the answer is the
fixed sentence in :data:`app.experiments.metrics.INSUFFICIENT_VALIDATION_MESSAGE`
and no number at all. That is the state the system is in today, and it is
reported as such.
"""

from app.experiments.classifier import (
    CLEAN_RAINFALL_EVENT,
    INCOMPLETE_EVIDENCE_EVENT,
    INTERVENTION_AFFECTED_EVENT,
    ClassificationResult,
    classify_event,
)
from app.experiments.freeze import FrozenManifest, freeze_manifest
from app.experiments.metrics import (
    INSUFFICIENT_VALIDATION_MESSAGE,
    MIN_CLEAN_EVENTS_FOR_VALIDATION,
    MIN_DISTINCT_PANS_FOR_VALIDATION,
    MetricReading,
    ValidationReport,
    absolute_error,
    mean_absolute_error,
    mean_bias,
    root_mean_squared_error,
    validate_events,
)
from app.experiments.models import (
    EventClassification,
    ExperimentEvent,
    InterventionRecord,
    MeasurementSource,
    PredictionLock,
    VerificationState,
)
from app.experiments.paths import ExperimentPaths
from app.experiments.store import (
    DuplicateEventError,
    ExperimentStore,
    ExperimentStoreError,
    ObservationAlreadyRecordedError,
    PredictionLockedError,
    UnknownEventError,
    write_events_csv,
)

__all__ = [
    "CLEAN_RAINFALL_EVENT",
    "INCOMPLETE_EVIDENCE_EVENT",
    "INSUFFICIENT_VALIDATION_MESSAGE",
    "INTERVENTION_AFFECTED_EVENT",
    "MIN_CLEAN_EVENTS_FOR_VALIDATION",
    "MIN_DISTINCT_PANS_FOR_VALIDATION",
    "ClassificationResult",
    "DuplicateEventError",
    "EventClassification",
    "ExperimentEvent",
    "ExperimentPaths",
    "ExperimentStore",
    "ExperimentStoreError",
    "FrozenManifest",
    "InterventionRecord",
    "MeasurementSource",
    "MetricReading",
    "ObservationAlreadyRecordedError",
    "PredictionLock",
    "PredictionLockedError",
    "UnknownEventError",
    "ValidationReport",
    "VerificationState",
    "absolute_error",
    "classify_event",
    "freeze_manifest",
    "mean_absolute_error",
    "mean_bias",
    "root_mean_squared_error",
    "validate_events",
    "write_events_csv",
]
