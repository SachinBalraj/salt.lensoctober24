"""Phase 4 - prediction-first, locked, then observed.

The store is where the integrity claim is actually enforced, so the invariants
live here as code rather than as guidance in a README.

**A prediction is written once.** :meth:`ExperimentStore.lock_prediction` is the
only path that sets ``predicted_salinity_g_l`` or ``predicted_depth_cm``. Once
:meth:`ExperimentStore.lock_prediction` has sealed a record, any subsequent write
that would change a predicted figure raises :class:`PredictionLockedError` --
including one that arrives after the observation is known. That is the single
property that makes the resulting comparison worth anything: the prediction
cannot have been fitted to the answer.

**An observation is written once, and only after the prediction is sealed.**
:meth:`ExperimentStore.record_observation` refuses an event with no sealed
prediction (:class:`PredictionLockedError`), and refuses to overwrite an
observation that has already been recorded
(:class:`ObservationAlreadyRecordedError`). A measurement somebody took once
cannot be quietly restated; changing it goes through
:meth:`ExperimentStore.correct_observation`, which keeps the previous value and
the reason in an append-only correction entry.

**Corrections are append-only.** Nothing in this store deletes a field. A figure
that turns out to be wrong is corrected with its prior value preserved, because
"this number was wrong and here is what it was" is the information an audit
needs, and overwriting destroys it.

Writes are whole-file and atomic (temp file plus rename), so an interrupted run
leaves the previous record intact rather than a truncated one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from app.experiments.models import (
    EventClassification,
    ExperimentEvent,
    InterventionRecord,
    MeasurementSource,
    PredictionLock,
    VerificationState,
)
from app.experiments.paths import ExperimentPaths
from app.models.base import normalize_datetime, utcnow

#: Fields a sealed prediction owns. Changing any of them after the seal is
#: refused; the list is explicit so a new field added to the model cannot
#: accidentally become editable.
PREDICTION_FIELDS: tuple[str, ...] = ("predicted_salinity_g_l", "predicted_depth_cm")

#: Fields an observation owns, written once.
OBSERVATION_FIELDS: tuple[str, ...] = (
    "observed_salinity_g_l",
    "observed_depth_cm",
    "post_event_timestamp",
    "post_event_timestamp_verified",
    "observation_recorded_at",
    "measurement_source",
    "verification_state",
    "independent_verification",
    "verified_by",
)


class ExperimentStoreError(Exception):
    """Base class for every refusal this store makes."""


class PredictionLockedError(ExperimentStoreError):
    """A write would have changed a sealed prediction, or needed one.

    Two situations, deliberately the same exception: changing a prediction after
    it is sealed, and entering an observation for an event whose prediction was
    never sealed. Both mean the same thing to a reader -- the ordering between
    prediction and observation cannot be demonstrated -- so they read the same.
    """


class ObservationAlreadyRecordedError(ExperimentStoreError):
    """An observation that was already recorded is being written again."""


class UnknownEventError(ExperimentStoreError):
    """No such event in the registry."""


class DuplicateEventError(ExperimentStoreError):
    """An event with this identifier already exists."""


def prediction_content_hash(
    *, salinity_g_l: float | None, depth_cm: float | None, rainfall_mm: float | None
) -> str:
    """A hash over the predicted figures and the rainfall that drove them.

    Covers the input as well as the output, because a prediction that is
    unchanged while its driving rainfall is edited is just as much a
    retrospective change as one whose output moved.
    """
    payload = json.dumps(
        {
            "predicted_salinity_g_l": salinity_g_l,
            "predicted_depth_cm": depth_cm,
            "rainfall_mm": rainfall_mm,
        },
        sort_keys=True,
        default=str,
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


class ExperimentStore:
    """The registry, as one JSON file per event plus an index.

    Not a database, deliberately: see :mod:`app.experiments.paths` for why a
    frozen experiment wants to be diffable files. Concurrency is handled by
    whole-file atomic replacement and by refusing a second write of anything
    that is written once, so two writers cannot both succeed at changing a sealed
    prediction -- the second is refused when it reads a record the first already
    sealed.
    """

    def __init__(self, paths: ExperimentPaths | None = None) -> None:
        self.paths = paths or ExperimentPaths()

    # -- reading ------------------------------------------------------------

    def exists(self, event_id: str) -> bool:
        return self.paths.record_file(event_id).exists()

    def get(self, event_id: str) -> ExperimentEvent:
        path = self.paths.record_file(event_id)
        if not path.exists():
            raise UnknownEventError(
                f"No experiment event {event_id!r} is registered. Nothing is "
                "created by asking for it; register it first."
            )
        return ExperimentEvent.model_validate_json(path.read_text(encoding="utf-8"))

    def list_events(self) -> list[ExperimentEvent]:
        """Every registered event, oldest first, then by identifier.

        Sorted rather than filesystem order so two runs of the same registry
        produce the same list, which is what makes a diff of two exports
        meaningful.
        """
        directory = self.paths.records_dir
        if not directory.exists():
            return []
        events: list[ExperimentEvent] = []
        for path in sorted(directory.glob("*.json")):
            events.append(ExperimentEvent.model_validate_json(path.read_text(encoding="utf-8")))
        return sorted(events, key=lambda event: (event.date, event.created_at, event.event_id))

    def __len__(self) -> int:
        directory = self.paths.records_dir
        if not directory.exists():
            return 0
        return len(list(directory.glob("*.json")))

    # -- writing ------------------------------------------------------------

    def register_event(
        self,
        *,
        event_id: str,
        pan_id: str,
        date: Any,
        initial_salinity_g_l: float | None = None,
        initial_depth_cm: float | None = None,
        pre_event_timestamp: datetime | None = None,
        pre_event_timestamp_verified: bool | None = None,
        observation_source_id: str | None = None,
        area_m2: float | None = None,
        rainfall_mm: float | None = None,
        rainfall_is_forecast: bool | None = None,
        rainfall_source: str | None = None,
        rainfall_observed: bool | None = None,
        confirmed_rainfall_mm: float | None = None,
        notes: str | None = None,
        created_at: datetime | None = None,
    ) -> ExperimentEvent:
        """Register an event with its pre-event state and rainfall, unsealed.

        Every measured quantity is a parameter that may be ``None``, and ``None``
        is stored as ``None``. This call deliberately cannot set a predicted or an
        observed figure: those arrive through :meth:`lock_prediction` and
        :meth:`record_observation`, in that order, so the ordering is enforced by
        the API's shape rather than by a check that could be forgotten.
        """
        if self.exists(event_id):
            raise DuplicateEventError(
                f"Event {event_id!r} is already registered. A second registration "
                "would create a parallel record of one event; correct the "
                "existing one instead."
            )
        event = ExperimentEvent(
            event_id=event_id,
            pan_id=pan_id,
            date=date,
            initial_salinity_g_l=initial_salinity_g_l,
            initial_depth_cm=initial_depth_cm,
            pre_event_timestamp=pre_event_timestamp,
            pre_event_timestamp_verified=pre_event_timestamp_verified,
            observation_source_id=observation_source_id,
            area_m2=area_m2,
            rainfall_mm=rainfall_mm,
            rainfall_is_forecast=rainfall_is_forecast,
            rainfall_source=rainfall_source,
            rainfall_observed=rainfall_observed,
            confirmed_rainfall_mm=confirmed_rainfall_mm,
            notes=notes,
            created_at=created_at or utcnow(),
        )
        return self._write(event, action="registered")

    def lock_prediction(
        self,
        event_id: str,
        *,
        predicted_salinity_g_l: float,
        predicted_depth_cm: float,
        rainfall_mm: float,
        prediction_made_at: datetime,
        model_name: str,
        app_version: str,
        git_commit: str | None,
        manifest_hash: str | None,
        locked_at: datetime | None = None,
    ) -> ExperimentEvent:
        """Seal the prediction for an event.

        Refuses when a prediction is already present, and refuses when the
        event already carries an observation -- a prediction made after the
        answer is on the record is not a prediction, and allowing one would make
        every comparison in the registry retro-fitted without anybody editing a
        file.
        """
        event = self.get(event_id)

        if event.has_observed_figures:
            raise PredictionLockedError(
                f"Event {event_id!r} already carries observed figures, so a "
                "prediction cannot now be sealed for it. A prediction computed "
                "after the measurement was recorded was fitted to that "
                "measurement, whatever the code path that produced it."
            )
        if event.has_prediction:
            raise PredictionLockedError(
                f"Event {event_id!r} already has a prediction "
                f"({event.predicted_salinity_g_l} g/L, {event.predicted_depth_cm} cm). "
                "A prediction is written once; re-running the simulator creates a "
                "new event rather than replacing this one, so the first figure "
                "stays on the record."
            )

        made_at = normalize_datetime(prediction_made_at)
        event.predicted_salinity_g_l = predicted_salinity_g_l
        event.predicted_depth_cm = predicted_depth_cm
        event.rainfall_mm = rainfall_mm
        event.prediction_lock = PredictionLock(
            locked=True,
            locked_at=normalize_datetime(locked_at or utcnow()),
            prediction_made_at=made_at,
            model_name=model_name,
            app_version=app_version,
            git_commit=git_commit,
            manifest_hash=manifest_hash,
            content_hash=prediction_content_hash(
                salinity_g_l=predicted_salinity_g_l,
                depth_cm=predicted_depth_cm,
                rainfall_mm=rainfall_mm,
            ),
        )
        return self._write(event, action="prediction_locked")

    def record_observation(
        self,
        event_id: str,
        *,
        observed_salinity_g_l: float | None = None,
        observed_depth_cm: float | None = None,
        post_event_timestamp: datetime | None = None,
        post_event_timestamp_verified: bool | None = None,
        measurement_source: MeasurementSource | None = None,
        verification_state: VerificationState | None = None,
        independent_verification: bool | None = None,
        verified_by: str | None = None,
        observation_recorded_at: datetime | None = None,
    ) -> ExperimentEvent:
        """Enter the real observed measurement, once.

        Requires a sealed prediction and refuses a second write of any field this
        already set. Either figure may be ``None`` -- a depth taken without a
        salinity is a partial record, and refusing it would push an operator to
        invent the missing half rather than file what they measured.
        """
        event = self.get(event_id)

        if not event.prediction_lock.locked:
            raise PredictionLockedError(
                f"Event {event_id!r} has no sealed prediction, so an observation "
                "cannot be entered against it. Seal the prediction first; the "
                "ordering is the evidence."
            )
        if event.has_observed_figures:
            raise ObservationAlreadyRecordedError(
                f"Event {event_id!r} already records an observation "
                f"({event.observed_salinity_g_l} g/L, {event.observed_depth_cm} cm). "
                "A measurement is written once. Use correct_observation with a "
                "reason if the recorded figure is wrong -- the previous value is "
                "kept either way."
            )

        recorded_at = normalize_datetime(observation_recorded_at or utcnow())
        lock = event.prediction_lock
        if lock.prediction_made_at is not None and recorded_at < lock.prediction_made_at:
            raise PredictionLockedError(
                f"Observation for {event_id!r} is stamped "
                f"{recorded_at.isoformat()}, which is before the prediction was "
                f"made ({lock.prediction_made_at.isoformat()}). An observation "
                "that predates the prediction it is graded against cannot be "
                "used to grade it."
            )

        if observed_salinity_g_l is not None:
            event.observed_salinity_g_l = observed_salinity_g_l
        if observed_depth_cm is not None:
            event.observed_depth_cm = observed_depth_cm
        if post_event_timestamp is not None:
            event.post_event_timestamp = post_event_timestamp
        if post_event_timestamp_verified is not None:
            event.post_event_timestamp_verified = post_event_timestamp_verified
        if measurement_source is not None:
            event.measurement_source = measurement_source
        if verification_state is not None:
            event.verification_state = verification_state
        if independent_verification is not None:
            event.independent_verification = independent_verification
        if verified_by is not None:
            event.verified_by = verified_by
        event.observation_recorded_at = recorded_at

        return self._write(event, action="observation_recorded")

    def record_intervention(self, event_id: str, intervention: InterventionRecord) -> ExperimentEvent:
        """Append one intervention.

        Append-only, and never given a volume it did not have:
        :class:`~app.experiments.models.InterventionRecord` refuses
        ``volume_recorded=True`` with a null volume, so an intervention whose size
        was never written down cannot be written as though it were known.
        """
        event = self.get(event_id)
        event.interventions.append(intervention)
        if intervention.volume_m3 is not None:
            if "brine" in intervention.kind and event.transfer_volume_m3 is None:
                event.transfer_volume_m3 = intervention.volume_m3
            if "drain" in intervention.kind and event.drainage_volume_m3 is None:
                event.drainage_volume_m3 = intervention.volume_m3
        if "brine" in intervention.kind:
            event.brine_transfer = True
        return self._write(event, action="intervention_recorded")

    def correct_observation(
        self,
        event_id: str,
        *,
        field: str,
        new_value: Any,
        reason: str,
        corrected_by: str,
        corrected_at: datetime | None = None,
    ) -> ExperimentEvent:
        """Change a recorded observation, keeping the previous value and the reason.

        Never available for a predicted field. A correction to a *prediction* is
        the one edit that would break the study's central guarantee, so there is
        no path to it: the prediction is sealed or it is wrong, and a wrong
        prediction is a finding, not something to edit.
        """
        if field in PREDICTION_FIELDS or field.startswith("prediction"):
            raise PredictionLockedError(
                f"{field!r} is part of the sealed prediction and cannot be "
                "corrected. A sealed prediction that turns out to be wrong is a "
                "result; changing it after the observation was recorded would "
                "make it a fit."
            )
        if field not in OBSERVATION_FIELDS:
            raise ExperimentStoreError(
                f"{field!r} is not a recorded observation field. Correctable "
                f"fields are: {', '.join(OBSERVATION_FIELDS)}."
            )
        if not reason.strip():
            raise ExperimentStoreError(
                "A correction needs a reason. A changed measurement with no "
                "stated reason is indistinguishable from a fabricated one."
            )

        event = self.get(event_id)
        previous = getattr(event, field)
        if previous == new_value:
            return event

        event.corrections.append(
            {
                "field": field,
                "previous_value": _serialisable(previous),
                "new_value": _serialisable(new_value),
                "reason": reason,
                "corrected_by": corrected_by,
                "corrected_at": normalize_datetime(corrected_at or utcnow()).isoformat(),
            }
        )
        setattr(event, field, new_value)
        return self._write(event, action="observation_corrected")

    # -- internals ----------------------------------------------------------

    def _assert_no_prediction_drift(self, event: ExperimentEvent, *, before: ExperimentEvent) -> None:
        """Refuse any write that moved a predicted figure.

        Enforced against the record *on disk*, not against the in-memory copy the
        caller started from, so a second writer that read the file before the first
        one sealed it is still caught: it finds the sealed figures at write time
        and is refused.
        """
        if not before.prediction_lock.locked:
            return
        for name in PREDICTION_FIELDS:
            if getattr(event, name) != getattr(before, name):
                raise PredictionLockedError(
                    f"The sealed prediction field {name!r} would change from "
                    f"{getattr(before, name)!r} to {getattr(event, name)!r}. A "
                    "sealed prediction is immutable."
                )

    def _write(self, event: ExperimentEvent, *, action: str) -> ExperimentEvent:
        """Persist a record, refusing any drift in the sealed prediction.

        Every write funnels through here, including the one that performs the
        seal itself. A seal moves the fields from unset to set on a record that
        carries no lock, so the check finds nothing to compare and passes; every
        later write is measured against the figures already on disk.
        """
        self.paths.ensure()
        path = self.paths.record_file(event.event_id)
        if path.exists():
            self._assert_no_prediction_drift(
                event,
                before=ExperimentEvent.model_validate_json(path.read_text(encoding="utf-8")),
            )
        payload = event.model_dump(mode="json", exclude_none=False)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        self._write_index(action=action, event=event)
        return event

    def _write_index(self, *, action: str, event: ExperimentEvent) -> None:
        """A flat index beside the records.

        Carries the classification so a reader can see the category of every
        event without opening thirteen files, and the append-only write log so the
        order of operations on each event is inspectable -- including that the
        prediction was sealed before the observation was entered.
        """
        from app.experiments.classifier import classify_event

        events = self.list_events()
        index = {
            "schema": "saltlens.experiment-registry.index/1",
            "event_count": len(events),
            "events": [
                {
                    "event_id": item.event_id,
                    "pan_id": item.pan_id,
                    "date": item.date.isoformat(),
                    "category": classify_event(item).category.value,
                    "prediction_locked": item.prediction_lock.locked,
                    "has_observation": item.has_observed_figures,
                }
                for item in events
            ],
            "write_log": _append_log_entry(
                self.paths.events_index_file, action=action, event_id=event.event_id
            ),
        }
        self.paths.events_index_file.write_text(
            json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def _append_log_entry(path: Path, *, action: str, event_id: str) -> list[dict[str, str]]:
    """Append one entry to the write log, preserving what is already there."""
    existing: list[dict[str, str]] = []
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            entries = previous.get("write_log")
            if isinstance(entries, list):
                existing = [entry for entry in entries if isinstance(entry, dict)]
        except json.JSONDecodeError:
            existing = []
    existing.append({"at": normalize_datetime(utcnow()).isoformat(), "action": action, "event_id": event_id})
    return existing


def _serialisable(value: Any) -> Any:
    """JSON-safe rendering of a value, for the correction history."""
    if isinstance(value, datetime):
        return normalize_datetime(value).isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def write_events_csv(store: ExperimentStore, path: Path | None = None) -> Path:
    """Write the registry as CSV with the study's own column order.

    Absent measurements are written as empty cells. A reader that cannot tell an
    empty cell from a measured zero is precisely the reader this file must not be
    handed, so no absent value is ever rendered as ``0``.
    """
    import csv

    from app.experiments.models import CSV_COLUMNS

    destination = path or store.paths.events_csv_file
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for event in store.list_events():
            writer.writerow(event.csv_row())
    return destination


def summarise_classifications(
    events: Iterable[ExperimentEvent],
) -> dict[str, int]:
    """Counts per category, for a status line.

    Counts every category that is present at zero, so "0 intervention-affected
    events" is stated rather than being absent from the report and read as an
    oversight.
    """
    from app.experiments.classifier import classify_event

    counts = {category.value: 0 for category in EventClassification}
    for event in events:
        counts[classify_event(event).category.value] += 1
    return counts


__all__ = [
    "OBSERVATION_FIELDS",
    "PREDICTION_FIELDS",
    "DuplicateEventError",
    "ExperimentStore",
    "ExperimentStoreError",
    "ObservationAlreadyRecordedError",
    "PredictionLockedError",
    "UnknownEventError",
    "prediction_content_hash",
    "summarise_classifications",
    "write_events_csv",
]
