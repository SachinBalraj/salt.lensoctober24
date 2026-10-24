"""Module 21 - the experiment registry, evidence classifier and metrics.

The whole point of this module is that a result cannot be better than its
evidence. So the tests here are weighted towards the refusals rather than the
happy paths: each one asserts that a specific way of manufacturing a good-looking
number fails, and says what the correct answer is instead.

The four properties, in the order they matter:

* **The ordering cannot be faked.** An observation cannot be entered before a
  prediction is sealed, a sealed prediction cannot be rewritten, and an
  observation cannot be restated. A correction to a *predicted* figure has no
  code path at all.
* **A confounded event is not evidence.** The 14 March 2026 event -- rainfall
  reported rather than observed, a brine transfer with no recorded volume, a
  post-event reading from the same person who reported the rain -- classifies as
  ``INCOMPLETE_EVIDENCE_EVENT`` and is excluded, with the reason stated.
* **Nothing is invented.** Absent measurements are ``None`` and serialise as
  empty cells, never as ``0``.
* **Insufficient evidence yields no number.** One clean event is not enough, and
  the withholding message is exact, because a caller matches on it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.experiments.classifier import (
    CLEAN_RAINFALL_EVENT,
    INCOMPLETE_EVIDENCE_EVENT,
    INTERVENTION_AFFECTED_EVENT,
    EventClassification,
    classify_event,
)
from app.experiments.metrics import (
    INSUFFICIENT_VALIDATION_MESSAGE,
    MIN_CLEAN_EVENTS_FOR_VALIDATION,
    MIN_DISTINCT_PANS_FOR_VALIDATION,
    absolute_error,
    mean_absolute_error,
    mean_bias,
    root_mean_squared_error,
    validate_events,
)
from app.experiments.models import (
    ExperimentEvent,
    InterventionRecord,
    MeasurementSource,
    VerificationState,
)
from app.experiments.paths import ExperimentPaths
from app.experiments.store import (
    DuplicateEventError,
    ExperimentStore,
    ObservationAlreadyRecordedError,
    PredictionLockedError,
    write_events_csv,
)

UTC = timezone.utc


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def paths(tmp_path: Path) -> ExperimentPaths:
    """A registry of its own, so nothing here touches the committed experiment."""
    return ExperimentPaths(root=tmp_path / "experiments")


@pytest.fixture()
def store(paths: ExperimentPaths) -> ExperimentStore:
    return ExperimentStore(paths=paths)


def _register(
    store: ExperimentStore,
    event_id: str = "evt-1",
    pan_id: str = "pan-a",
    **overrides: Any,
) -> ExperimentEvent:
    """Register an unsealed event with sensible complete defaults."""
    defaults: dict[str, Any] = {
        "initial_salinity_g_l": 250.0,
        "initial_depth_cm": 20.0,
        "rainfall_mm": 90.0,
        "rainfall_is_forecast": False,
        "rainfall_observed": True,
        "rainfall_source": "manual",
    }
    defaults.update(overrides)
    return store.register_event(
        event_id=event_id, pan_id=pan_id, date="2026-03-14", **defaults
    )


def _lock(
    store: ExperimentStore,
    event_id: str = "evt-1",
    *,
    salinity: float = 172.41379310344828,
    depth: float = 29.0,
    rainfall: float = 90.0,
    made_at: datetime | None = None,
    **overrides: Any,
) -> ExperimentEvent:
    defaults: dict[str, Any] = {
        "model_name": "rainfall-dilution-mass-balance-v1",
        "app_version": "0.1.0",
        "git_commit": "abc123",
        "manifest_hash": None,
    }
    defaults.update(overrides)
    return store.lock_prediction(
        event_id,
        predicted_salinity_g_l=salinity,
        predicted_depth_cm=depth,
        rainfall_mm=rainfall,
        prediction_made_at=made_at or datetime(2026, 3, 14, 18, 0, tzinfo=UTC),
        **defaults,
    )


def _observe(
    store: ExperimentStore,
    event_id: str = "evt-1",
    *,
    salinity: float | None = 20.0,
    depth: float | None = 50.0,
    verified: bool = True,
    verification_state: VerificationState = VerificationState.INDEPENDENTLY_VERIFIED,
    source: MeasurementSource = MeasurementSource.INSTRUMENT,
    recorded_at: datetime | None = None,
) -> ExperimentEvent:
    return store.record_observation(
        event_id,
        observed_salinity_g_l=salinity,
        observed_depth_cm=depth,
        post_event_timestamp=datetime(2026, 3, 14, 20, 0, tzinfo=UTC),
        post_event_timestamp_verified=True,
        measurement_source=source,
        verification_state=verification_state,
        independent_verification=verified,
        verified_by="second party" if verified else None,
        observation_recorded_at=recorded_at,
    )


def _clean_event(
    store: ExperimentStore,
    event_id: str,
    pan_id: str,
    *,
    observed_salinity: float,
    predicted_salinity: float = 100.0,
) -> ExperimentEvent:
    """Register, lock and observe an event that should classify as clean.

    The only route to a clean event in these tests, deliberately: every fixture
    that needs accuracy evidence has to satisfy the same conditions a real one
    would, and nothing weaker gets there.
    """
    _register(store, event_id=event_id, pan_id=pan_id)
    _lock(store, event_id, salinity=predicted_salinity, depth=40.0, rainfall=50.0)
    return _observe(store, event_id, salinity=observed_salinity, depth=30.0)


# --------------------------------------------------------------------------
# the event model
# --------------------------------------------------------------------------


class TestEventModel:
    def test_the_registry_starts_empty(self, store: ExperimentStore) -> None:
        assert store.list_events() == []
        assert len(store) == 0

    def test_an_absent_measurement_is_null_not_zero(self, store: ExperimentStore) -> None:
        event = _register(store, initial_salinity_g_l=None, initial_depth_cm=None)

        assert event.initial_salinity_g_l is None
        assert event.initial_depth_cm is None
        # Zero would be a claim that the measurement was taken and read zero, so
        # the assertion is against 0 rather than merely against None.
        assert event.initial_salinity_g_l != 0
        assert event.initial_depth_cm != 0

    def test_registering_the_same_event_twice_is_refused(self, store: ExperimentStore) -> None:
        _register(store)

        with pytest.raises(DuplicateEventError, match="already registered"):
            _register(store)

    def test_reading_an_unregistered_event_raises_rather_than_creating_one(
        self, store: ExperimentStore
    ) -> None:
        from app.experiments.store import UnknownEventError

        with pytest.raises(UnknownEventError):
            store.get("evt-does-not-exist")

        assert store.list_events() == []

    def test_an_intervention_cannot_claim_a_volume_that_was_never_recorded(self) -> None:
        with pytest.raises(ValueError, match="volume_recorded"):
            InterventionRecord(kind="brine_transfer_out", volume_m3=None, volume_recorded=True)

    def test_an_intervention_with_a_volume_must_say_it_was_recorded(self) -> None:
        with pytest.raises(ValueError, match="volume_recorded"):
            InterventionRecord(kind="brine_transfer_out", volume_m3=12.0, volume_recorded=False)


# --------------------------------------------------------------------------
# Phase 4 - prediction first, sealed, then observed
# --------------------------------------------------------------------------


class TestPredictionLocking:
    def test_a_prediction_can_be_sealed_exactly_once(self, store: ExperimentStore) -> None:
        _register(store)
        _lock(store)

        with pytest.raises(PredictionLockedError, match="written once"):
            _lock(store, salinity=1.0, depth=1.0, rainfall=1.0)

    def test_a_sealed_prediction_records_what_it_was_made_with(self, store: ExperimentStore) -> None:
        _register(store)
        event = _lock(store, made_at=datetime(2026, 3, 14, 18, 0, tzinfo=UTC))

        lock = event.prediction_lock
        assert lock.locked is True
        assert lock.model_name == "rainfall-dilution-mass-balance-v1"
        assert lock.git_commit == "abc123"
        assert lock.prediction_made_at == datetime(2026, 3, 14, 18, 0, tzinfo=UTC)
        assert lock.content_hash.startswith("sha256:")

    def test_the_seal_hashes_the_rainfall_too_not_only_the_outputs(self) -> None:
        from app.experiments.store import prediction_content_hash

        first = prediction_content_hash(salinity_g_l=100.0, depth_cm=40.0, rainfall_mm=50.0)
        same = prediction_content_hash(salinity_g_l=100.0, depth_cm=40.0, rainfall_mm=50.0)
        other_rainfall = prediction_content_hash(salinity_g_l=100.0, depth_cm=40.0, rainfall_mm=60.0)

        assert first == same
        # An unchanged prediction computed from edited rainfall is still a change.
        assert first != other_rainfall

    def test_a_prediction_cannot_be_sealed_after_the_answer_is_known(
        self, store: ExperimentStore
    ) -> None:
        """The ordering guarantee, tested from the direction that breaks it.

        Sealing a prediction against an event that already carries an observation
        would be fitting the prediction to the measurement while every field still
        looked correct. The state is reached by clearing the seal on the stored
        record, which is the only way to get here without the refusal.
        """
        _register(store)
        _lock(store)
        _observe(store)

        on_disk = store.get("evt-1")
        on_disk.prediction_lock.locked = False
        on_disk.predicted_salinity_g_l = None
        on_disk.predicted_depth_cm = None
        store.paths.record_file("evt-1").write_text(on_disk.model_dump_json(), encoding="utf-8")

        with pytest.raises(PredictionLockedError, match="already carries observed figures"):
            _lock(store, salinity=1.0, depth=1.0, rainfall=1.0)

    def test_clearing_a_seal_is_itself_refused(self, store: ExperimentStore) -> None:
        """The escape hatch used above is closed in normal operation.

        Without this, clearing the seal would be a one-line route to re-fitting a
        prediction, and the ordering guarantee would rest on nobody finding it.
        """
        _register(store)
        _lock(store)
        _observe(store)

        tampered = store.get("evt-1")
        tampered.predicted_salinity_g_l = 1.0

        with pytest.raises(PredictionLockedError, match="immutable"):
            store._write(tampered, action="tampered")


class TestObservationOrdering:
    def test_an_observation_needs_a_sealed_prediction(self, store: ExperimentStore) -> None:
        _register(store)

        with pytest.raises(PredictionLockedError, match="no sealed prediction"):
            _observe(store)

    def test_an_observation_is_written_once(self, store: ExperimentStore) -> None:
        _register(store)
        _lock(store)
        _observe(store, salinity=20.0)

        with pytest.raises(ObservationAlreadyRecordedError, match="written once"):
            _observe(store, salinity=21.0)

    def test_an_observation_stamped_before_its_prediction_is_refused(
        self, store: ExperimentStore
    ) -> None:
        _register(store)
        _lock(store, made_at=datetime(2026, 3, 14, 18, 0, tzinfo=UTC))

        with pytest.raises(PredictionLockedError, match="before the prediction"):
            _observe(store, recorded_at=datetime(2026, 3, 14, 17, 0, tzinfo=UTC))

    def test_a_partial_observation_is_recorded_as_a_partial_observation(
        self, store: ExperimentStore
    ) -> None:
        """A depth taken without a salinity is a real measurement.

        Refusing it would push an operator to invent the missing half, which is
        the one outcome this project must never cause.
        """
        _register(store)
        _lock(store)
        event = _observe(store, salinity=None, depth=35.0)

        assert event.observed_salinity_g_l is None
        assert event.observed_depth_cm == 35.0


class TestCorrections:
    def test_a_correction_keeps_the_previous_value_and_the_reason(
        self, store: ExperimentStore
    ) -> None:
        _register(store)
        _lock(store)
        _observe(store, salinity=20.0)

        event = store.correct_observation(
            "evt-1",
            field="observed_salinity_g_l",
            new_value=25.0,
            reason="transcription error",
            corrected_by="auditor",
        )

        assert len(event.corrections) == 1
        correction = event.corrections[0]
        assert correction["previous_value"] == 20.0
        assert correction["new_value"] == 25.0
        assert correction["reason"] == "transcription error"
        assert correction["corrected_by"] == "auditor"
        assert event.observed_salinity_g_l == 25.0

    def test_a_prediction_has_no_correction_path(self, store: ExperimentStore) -> None:
        _register(store)
        _lock(store)
        _observe(store)

        for field in ("predicted_salinity_g_l", "predicted_depth_cm", "prediction_lock"):
            with pytest.raises(PredictionLockedError, match="sealed prediction"):
                store.correct_observation(
                    "evt-1", field=field, new_value=1.0, reason="x", corrected_by="y"
                )

    def test_a_correction_without_a_reason_is_refused(self, store: ExperimentStore) -> None:
        _register(store)
        _lock(store)
        _observe(store)

        with pytest.raises(Exception, match="reason"):
            store.correct_observation(
                "evt-1",
                field="observed_salinity_g_l",
                new_value=25.0,
                reason="   ",
                corrected_by="auditor",
            )

    def test_corrections_accumulate_rather_than_replacing_each_other(
        self, store: ExperimentStore
    ) -> None:
        _register(store)
        _lock(store)
        _observe(store, salinity=20.0)

        store.correct_observation(
            "evt-1",
            field="observed_salinity_g_l",
            new_value=25.0,
            reason="first",
            corrected_by="a",
        )
        event = store.correct_observation(
            "evt-1",
            field="observed_salinity_g_l",
            new_value=30.0,
            reason="second",
            corrected_by="b",
        )

        assert [entry["new_value"] for entry in event.corrections] == [25.0, 30.0]


class TestSealedPredictionCannotDrift:
    def test_an_intervention_after_the_seal_leaves_the_prediction_alone(
        self, store: ExperimentStore
    ) -> None:
        _register(store)
        _lock(store)

        event = store.record_intervention(
            "evt-1",
            InterventionRecord(
                kind="drainage_out",
                direction="out",
                performed_at=datetime(2026, 3, 14, 19, 0, tzinfo=UTC),
                volume_m3=8.0,
                volume_recorded=True,
            ),
        )

        assert event.predicted_salinity_g_l == pytest.approx(172.41379310344828)
        assert event.drainage_volume_m3 == 8.0

    def test_a_direct_drift_on_the_stored_record_is_refused_at_write_time(
        self, store: ExperimentStore
    ) -> None:
        """The check is against what is on disk, not against the caller's copy.

        A second writer that read the record before the first one sealed it would
        otherwise get its retro-fitted prediction committed.
        """
        _register(store)
        _lock(store)

        stale = store.get("evt-1")  # read before anything else changes it
        tampered = store.get("evt-1")
        tampered.predicted_salinity_g_l = 1.0

        with pytest.raises(PredictionLockedError, match="immutable"):
            store._write(tampered, action="tampered")

        assert store.get("evt-1").predicted_salinity_g_l == pytest.approx(172.41379310344828)
        assert stale.prediction_lock.locked is True


# --------------------------------------------------------------------------
# Phase 5 - the classifier
# --------------------------------------------------------------------------


class TestClassifier:
    def test_the_march_event_is_incomplete_and_says_why(self, store: ExperimentStore) -> None:
        """The real event, reconstructed.

        Reported rather than observed rainfall, a brine transfer whose volume was
        never written down, and a post-event reading from the same person who
        reported the rain. Every one of those is disqualifying, and the classifier
        has to name each rather than produce a bare verdict.
        """
        _register(
            store,
            event_id="ev-249b575de8234ff09b341e525ef5d4a1",
            pan_id="pan_orginal",
            initial_salinity_g_l=250.0,
            initial_depth_cm=20.0,
            rainfall_mm=90.0,
            rainfall_is_forecast=False,
            rainfall_observed=False,
            rainfall_source="operator reported",
        )
        _lock(store, "ev-249b575de8234ff09b341e525ef5d4a1", salinity=172.41379310344828, depth=29.0)
        store.record_intervention(
            "ev-249b575de8234ff09b341e525ef5d4a1",
            InterventionRecord(
                kind="brine_transfer_out",
                direction="out",
                performed_at=datetime(2026, 3, 14, 19, 0, tzinfo=UTC),
                volume_m3=None,
                volume_recorded=False,
            ),
        )
        _observe(
            store,
            "ev-249b575de8234ff09b341e525ef5d4a1",
            salinity=20.0,
            depth=50.0,
            verified=False,
            verification_state=VerificationState.OPERATOR_REPORTED,
            source=MeasurementSource.OPERATOR_REPORTED,
        )

        result = classify_event(store.get("ev-249b575de8234ff09b341e525ef5d4a1"))

        assert result.category is INCOMPLETE_EVIDENCE_EVENT
        assert result.is_accuracy_evidence is False
        assert result.intervention_affected is True
        assert any("rainfall" in reason.lower() for reason in result.reasons)
        assert any("volume" in reason.lower() for reason in result.reasons)
        assert any("verif" in reason.lower() for reason in result.reasons)
        # The intervention flag survives the incomplete category rather than
        # being collapsed into it.
        assert result.category is not INTERVENTION_AFFECTED_EVENT

    def test_an_intervention_with_a_recorded_volume_is_affected_not_incomplete(
        self, store: ExperimentStore
    ) -> None:
        _register(store)
        _lock(store)
        store.record_intervention(
            "evt-1",
            InterventionRecord(
                kind="harvest_out",
                performed_at=datetime(2026, 3, 14, 19, 0, tzinfo=UTC),
                volume_m3=5.0,
                volume_recorded=True,
            ),
        )
        _observe(store)

        result = classify_event(store.get("evt-1"))

        assert result.category is INTERVENTION_AFFECTED_EVENT
        assert result.intervention_affected is True
        assert result.is_accuracy_evidence is False

    def test_a_forecast_driven_prediction_is_never_clean(self, store: ExperimentStore) -> None:
        _register(
            store,
            rainfall_mm=25.0,
            rainfall_is_forecast=True,
            rainfall_observed=False,
            rainfall_source="forecast",
        )
        _lock(store, salinity=222.22222222222223, depth=22.5, rainfall=25.0)
        _observe(store, salinity=100.0, depth=30.0)

        result = classify_event(store.get("evt-1"))

        assert result.category is not CLEAN_RAINFALL_EVENT
        assert result.is_accuracy_evidence is False
        assert any("forecast" in reason.lower() for reason in result.reasons)

    def test_demonstration_sourced_observations_are_never_clean(self, store: ExperimentStore) -> None:
        _register(store)
        _lock(store)
        _observe(store, salinity=100.0, depth=30.0, source=MeasurementSource.DEMONSTRATION)

        assert classify_event(store.get("evt-1")).category is not CLEAN_RAINFALL_EVENT

    def test_an_unverified_reading_is_not_clean(self, store: ExperimentStore) -> None:
        _register(store)
        _lock(store)
        _observe(
            store,
            salinity=100.0,
            depth=30.0,
            verified=False,
            verification_state=VerificationState.OPERATOR_REPORTED,
        )

        assert classify_event(store.get("evt-1")).category is not CLEAN_RAINFALL_EVENT

    def test_a_complete_clean_event_is_clean(self, store: ExperimentStore) -> None:
        event = _clean_event(store, "evt-clean", "pan-a", observed_salinity=105.0)

        result = classify_event(event)

        assert result.category is CLEAN_RAINFALL_EVENT
        assert result.is_accuracy_evidence is True
        assert result.is_verified is True
        assert result.intervention_affected is False
        assert result.evidence_gaps == ()
        # A clean event states why it qualifies, so the empty tuple is never a
        # verdict a reader has to take on trust.
        assert len(result.reasons) == 1
        assert "no intervention" in result.reasons[0].lower()

    def test_is_verified_requires_both_conditions_not_either(self, store: ExperimentStore) -> None:
        """An independent verification on a confounded event is still not evidence.

        The conjunction is the whole point: a verified reading of a pan that had a
        brine transfer tells you the reading is trustworthy and tells you nothing
        about whether the prediction was right.
        """
        _register(store)
        _lock(store)
        store.record_intervention(
            "evt-1",
            InterventionRecord(
                kind="drainage_out",
                performed_at=datetime(2026, 3, 14, 19, 0, tzinfo=UTC),
                volume_m3=3.0,
                volume_recorded=True,
            ),
        )
        _observe(store, salinity=100.0, depth=30.0, verified=True)

        result = classify_event(store.get("evt-1"))

        assert result.category is INTERVENTION_AFFECTED_EVENT
        assert result.is_verified is False

    def test_the_three_category_names_are_the_published_contract(self) -> None:
        """Pinned as literals: these strings appear in the CSV and the API.

        Renaming one is a breaking change to a published vocabulary, and a
        refactor that renamed it should fail here rather than quietly orphan every
        already-exported row.
        """
        # As a set, not a sequence: declaration order is an implementation detail,
        # the membership is the contract.
        assert {category.value for category in EventClassification} == {
            "CLEAN_RAINFALL_EVENT",
            "INTERVENTION_AFFECTED_EVENT",
            "INCOMPLETE_EVIDENCE_EVENT",
        }


# --------------------------------------------------------------------------
# Phase 6 - the metric definitions
# --------------------------------------------------------------------------


class TestMetricDefinitions:
    def test_absolute_error(self) -> None:
        assert absolute_error(observed=20.0, predicted=29.0) == 9.0
        assert absolute_error(observed=29.0, predicted=20.0) == 9.0

    def test_mae_and_rmse_agree_on_a_single_pair(self) -> None:
        """Over n=1 they are the same number, which is why n=1 is not enough."""
        pairs = [(20.0, 29.0)]

        assert mean_absolute_error(pairs) == root_mean_squared_error(pairs) == 9.0

    def test_rmse_exceeds_mae_when_errors_are_unequal(self) -> None:
        pairs = [(100.0, 110.0), (100.0, 130.0)]

        assert mean_absolute_error(pairs) == 20.0
        assert root_mean_squared_error(pairs) > mean_absolute_error(pairs)

    def test_bias_is_signed_and_positive_means_the_prediction_ran_high(self) -> None:
        assert mean_bias([(100.0, 110.0)]) == 10.0
        assert mean_bias([(100.0, 90.0)]) == -10.0
        assert mean_bias([(100.0, 90.0), (100.0, 110.0)]) == 0.0

    def test_an_empty_set_yields_no_number_rather_than_zero(self) -> None:
        assert mean_absolute_error([]) is None
        assert root_mean_squared_error([]) is None
        assert mean_bias([]) is None

    def test_a_reading_inherits_the_system_wide_absent_metric_invariants(self) -> None:
        """These come from the shared validator, not from this module's own copy.

        The two failure modes are the ones this project cannot afford: a reading
        marked computed with no number, and a reading not computed carrying a
        number that a renderer would draw as if it meant something.
        """
        from app.experiments.metrics import MetricReading

        with pytest.raises(ValueError, match="must carry a value"):
            MetricReading(computed=True, value=None)

        with pytest.raises(ValueError, match="must not carry a value"):
            MetricReading(computed=False, value=0.0, reason="x")

        with pytest.raises(ValueError, match="must say why"):
            MetricReading(computed=False, value=None)

    def test_a_computed_reading_rejects_a_non_finite_value(self) -> None:
        from app.experiments.metrics import MetricReading

        for bad in (float("nan"), float("inf")):
            with pytest.raises(ValueError, match="finite"):
                MetricReading(computed=True, value=bad)


# --------------------------------------------------------------------------
# Phase 6 - insufficiency is enforced, not reported
# --------------------------------------------------------------------------


class TestValidationReport:
    def test_the_current_registry_yields_no_metrics_at_all(self, store: ExperimentStore) -> None:
        """The state this project is actually in.

        One event, incomplete. Every figure must come back uncomputed with the
        exact withholding message, because a dashboard that renders a number here
        is the failure this module exists to prevent.
        """
        _register(store)
        _lock(store)
        _observe(store, salinity=20.0, depth=50.0, verified=False)

        report = validate_events(store.list_events())

        assert report.sufficient is False
        assert report.message == INSUFFICIENT_VALIDATION_MESSAGE
        for report_quantity in (report.salinity, report.depth):
            assert report_quantity is not None
            for reading in (
                report_quantity.mean_absolute_error,
                report_quantity.root_mean_squared_error,
                report_quantity.mean_bias,
            ):
                assert reading.computed is False
                assert reading.value is None
                assert INSUFFICIENT_VALIDATION_MESSAGE in (reading.reason or "")

    def test_the_withholding_message_is_exactly_as_documented(self) -> None:
        assert INSUFFICIENT_VALIDATION_MESSAGE == "Insufficient independently verified events"

    def test_one_clean_event_is_still_insufficient(self, store: ExperimentStore) -> None:
        _clean_event(store, "evt-1", "pan-a", observed_salinity=105.0)

        report = validate_events(store.list_events())

        assert report.total_events == 1
        assert report.clean_events == 1
        assert report.sufficient is False
        assert report.salinity is not None
        assert report.salinity.mean_absolute_error.computed is False
        assert any("at least" in reason for reason in report.reasons)

    def test_three_events_on_one_pan_are_still_insufficient(
        self, store: ExperimentStore
    ) -> None:
        """Three events on one pan are one measurement taken three times."""
        for index in range(MIN_CLEAN_EVENTS_FOR_VALIDATION):
            _clean_event(store, f"evt-{index}", "pan-a", observed_salinity=100.0 + index)

        report = validate_events(store.list_events())

        assert report.clean_events == MIN_CLEAN_EVENTS_FOR_VALIDATION
        assert report.distinct_pans == ("pan-a",)
        assert report.sufficient is False
        assert any("pan" in reason.lower() for reason in report.reasons)

    def test_three_clean_events_across_two_pans_produce_metrics(
        self, store: ExperimentStore
    ) -> None:
        _clean_event(store, "evt-1", "pan-a", observed_salinity=110.0, predicted_salinity=100.0)
        _clean_event(store, "evt-2", "pan-a", observed_salinity=90.0, predicted_salinity=100.0)
        _clean_event(store, "evt-3", "pan-b", observed_salinity=120.0, predicted_salinity=100.0)

        report = validate_events(store.list_events())

        assert report.sufficient is True
        assert report.message is None
        assert report.sufficient is True
        assert report.salinity is not None
        reading = report.salinity.mean_absolute_error
        assert reading.computed is True
        assert reading.value == pytest.approx((10.0 + 10.0 + 20.0) / 3)
        assert reading.n == 3

    def test_bias_direction_is_reported_and_signed(self, store: ExperimentStore) -> None:
        for index, pan in enumerate(("pan-a", "pan-a", "pan-b")):
            _clean_event(
                store,
                f"evt-{index}",
                pan,
                observed_salinity=90.0,
                predicted_salinity=100.0,
            )

        report = validate_events(store.list_events())

        assert report.salinity is not None
        bias = report.salinity.mean_bias
        assert bias.computed is True
        # Every prediction ran 10 g/L high, and bias is the one figure that says so.
        assert bias.value == pytest.approx(10.0)

    def test_excluded_events_are_counted_with_reasons_not_dropped(
        self, store: ExperimentStore
    ) -> None:
        _clean_event(store, "evt-1", "pan-a", observed_salinity=100.0)
        _clean_event(store, "evt-2", "pan-b", observed_salinity=100.0)
        _register(store, event_id="evt-bad", pan_id="pan-c")
        _lock(store, "evt-bad")
        _observe(store, "evt-bad", salinity=100.0, depth=30.0, verified=False)

        report = validate_events(store.list_events())

        assert report.total_events == 3
        assert report.clean_events == 2
        assert report.incomplete_events == 1
        assert report.reasons  # the reasons for insufficiency are stated

    def test_a_clean_event_missing_one_figure_is_not_graded_on_the_other(
        self, store: ExperimentStore
    ) -> None:
        """A depth reading is a real measurement even when salinity is absent.

        It is excluded from the salinity aggregate, and the exclusion is reported.
        What it must never do is enter as a zero deviation.
        """
        _clean_event(store, "evt-1", "pan-a", observed_salinity=None)
        _clean_event(store, "evt-2", "pan-b", observed_salinity=100.0)
        _clean_event(store, "evt-3", "pan-c", observed_salinity=100.0)

        report = validate_events(store.list_events())

        assert report.salinity is not None
        assert report.salinity.pairs_compared == 2
        assert report.salinity.excluded == 1
        assert report.salinity.mean_absolute_error.value == pytest.approx(0.0)
        # Depth was recorded for all three, so it is unaffected.
        assert report.depth is not None
        assert report.depth.pairs_compared == 3

    def test_the_per_event_table_reports_exclusions_and_keeps_the_raw_deviations(
        self, store: ExperimentStore
    ) -> None:
        _register(store, event_id="evt-march", pan_id="pan_orginal")
        _lock(store, "evt-march", salinity=172.41379310344828, depth=29.0)
        _observe(
            store,
            "evt-march",
            salinity=20.0,
            depth=50.0,
            verified=False,
            verification_state=VerificationState.OPERATOR_REPORTED,
        )

        report = validate_events(store.list_events())

        row = report.per_event[0]
        assert row["included_in_metrics"] is False
        assert row["category"] == "INCOMPLETE_EVIDENCE_EVENT"
        # The deviation is a fact about the record; whether it may be averaged is
        # the classifier's call, and the flag says so separately.
        assert row["salinity_absolute_error_g_l"] == pytest.approx(152.41379310344828)
        assert row["depth_absolute_error_cm"] == pytest.approx(21.0)
        assert row["salinity_bias_g_l"] == pytest.approx(172.41379310344828 - 20.0)

    def test_the_report_serialises_to_plain_json(self, store: ExperimentStore) -> None:
        _clean_event(store, "evt-1", "pan-a", observed_salinity=100.0)

        payload = validate_events(store.list_events()).as_dict()

        # Round-trips without a custom encoder, which is what the API and the CSV
        # writer both rely on.
        assert json.loads(json.dumps(payload, default=str))["salinity"] is not None
        assert payload["salinity"]["mae"]["computed"] is False
        assert payload["thresholds"]["min_clean_events"] == MIN_CLEAN_EVENTS_FOR_VALIDATION


# --------------------------------------------------------------------------
# the export
# --------------------------------------------------------------------------


class TestCsvExport:
    def test_absent_measurements_are_empty_cells_not_zeroes(
        self, store: ExperimentStore
    ) -> None:
        _register(store)
        _lock(store)
        store.record_intervention(
            "evt-1",
            InterventionRecord(
                kind="brine_transfer_out",
                performed_at=datetime(2026, 3, 14, 19, 0, tzinfo=UTC),
                volume_m3=None,
                volume_recorded=False,
            ),
        )
        _observe(store, salinity=20.0, depth=50.0, verified=False)

        import csv

        path = write_events_csv(store)
        rows = list(csv.DictReader(path.read_text().splitlines()))

        assert len(rows) == 1
        row = rows[0]
        assert row["transfer_volume_m3"] == ""
        assert row["drainage_volume_m3"] == ""
        assert row["observed_salinity_g_l"] == "20.0"
        # Nothing anywhere renders an absent measurement as a digit.
        for value in row.values():
            assert value != "0"

    def test_the_csv_header_is_the_documented_column_order(self, store: ExperimentStore) -> None:
        from app.experiments.models import CSV_COLUMNS

        _register(store)
        path = write_events_csv(store)
        header = path.read_text().splitlines()[0]

        assert header.split(",") == list(CSV_COLUMNS)

    def test_the_csv_carries_the_classification(self, store: ExperimentStore) -> None:
        """A reader of the CSV alone must be able to see what was excluded.

        The category and the inclusion flag travel with each row, so a figure
        computed elsewhere cannot be re-derived from the CSV against events that
        the classifier rejected.
        """
        _clean_event(store, "evt-1", "pan-a", observed_salinity=100.0)
        _register(store, event_id="evt-march", pan_id="pan_orginal")
        _lock(store, "evt-march")
        _observe(
            store,
            "evt-march",
            salinity=20.0,
            depth=50.0,
            verified=False,
            verification_state=VerificationState.OPERATOR_REPORTED,
        )

        import csv

        rows = {
            row["event_id"]: row
            for row in csv.DictReader(write_events_csv(store).read_text().splitlines())
        }

        assert rows["evt-1"]["intervention_status"] == "CLEAN_RAINFALL_EVENT"
        assert rows["evt-1"]["included_in_metrics"] == "True"
        assert rows["evt-march"]["intervention_status"] == "INCOMPLETE_EVIDENCE_EVENT"
        assert rows["evt-march"]["included_in_metrics"] == "False"

    def test_the_index_records_the_order_of_operations(self, store: ExperimentStore) -> None:
        """The write log is what makes the prediction-first ordering checkable."""
        _register(store)
        _lock(store)
        _observe(store)

        index = json.loads(store.paths.events_index_file.read_text())

        actions = [entry["action"] for entry in index["write_log"]]
        assert actions.index("prediction_locked") < actions.index("observation_recorded")
        assert index["events"][0]["prediction_locked"] is True
        assert index["events"][0]["has_observation"] is True


# --------------------------------------------------------------------------
# the registry's location
# --------------------------------------------------------------------------


class TestPaths:
    def test_the_default_root_is_the_repository_experiments_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.experiments import paths as paths_module

        monkeypatch.delenv(paths_module.ENVIRONMENT_VARIABLE, raising=False)

        root = ExperimentPaths().root

        # Four levels up from app/experiments/paths.py is the repository root, not
        # backend/. Getting this wrong would write the registry into the package
        # directory, where nothing would find it.
        assert root.name == paths_module.DEFAULT_RELATIVE_PATH
        assert root.parent.name == "final mini"
        assert (root / "events").exists() or root.name == "experiments"

    def test_the_environment_variable_overrides_the_location(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from app.experiments import paths as paths_module

        monkeypatch.setenv(paths_module.ENVIRONMENT_VARIABLE, str(tmp_path / "elsewhere"))

        assert ExperimentPaths().root == tmp_path / "elsewhere"

    def test_ensure_creates_every_directory_and_is_idempotent(self, tmp_path: Path) -> None:
        paths = ExperimentPaths(root=tmp_path / "experiments")

        paths.ensure()
        paths.ensure()

        for directory in paths.all_directories():
            assert directory.is_dir()

    def test_reading_does_not_create_anything(self, tmp_path: Path) -> None:
        """A read-only process must be able to read a committed experiment."""
        paths = ExperimentPaths(root=tmp_path / "never-created")

        ExperimentStore(paths=paths).list_events()

        assert not paths.root.exists()


# --------------------------------------------------------------------------
# timestamps
# --------------------------------------------------------------------------


class TestTimestamps:
    def test_the_gap_between_a_prediction_and_its_observation_is_real_utc(
        self, store: ExperimentStore
    ) -> None:
        """Naive-local arithmetic once turned a 15-hour gap into a 15-day one.

        Every stored timestamp is normalised to UTC on the way in, so the two ends
        of the interval are comparable and the ordering check in
        :meth:`record_observation` can be trusted.
        """
        _register(store, event_id="evt-early")
        _lock(store, "evt-early", made_at=datetime(2026, 3, 14, 18, 0, tzinfo=UTC))
        _observe(store, "evt-early", recorded_at=datetime(2026, 3, 15, 9, 0, tzinfo=UTC))

        event = store.get("evt-early")

        gap = event.observation_recorded_at - event.prediction_lock.prediction_made_at
        assert gap == timedelta(hours=15)
        assert event.prediction_lock.prediction_made_at.tzinfo is not None

    def test_a_naive_timestamp_is_read_as_utc_rather_than_local(
        self, store: ExperimentStore
    ) -> None:
        """MongoDB hands back naive datetimes, and naive means different instants.

        A naive 18:00 read as local time in a UTC+05:30 site is 12:30 UTC, which
        would move the prediction five and a half hours relative to the reading
        and could silently pass the ordering check. The store reads them as UTC,
        and this pins that.
        """
        _register(store, event_id="evt-naive")
        _lock(store, "evt-naive", made_at=datetime(2026, 3, 14, 18, 0))  # noqa: DTZ001

        event = store.get("evt-naive")

        assert event.prediction_lock.prediction_made_at == datetime(
            2026, 3, 14, 18, 0, tzinfo=UTC
        )
        assert event.prediction_lock.prediction_made_at.utcoffset() == timedelta(0)


def test_the_thresholds_are_the_documented_ones() -> None:
    """Pinned so a later change to the sufficiency rule cannot pass unnoticed."""
    assert MIN_CLEAN_EVENTS_FOR_VALIDATION == 3
    assert MIN_DISTINCT_PANS_FOR_VALIDATION == 2
