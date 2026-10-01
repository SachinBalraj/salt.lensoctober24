"""Tests for the evidence gate in front of every reported score.

Module 08's sufficiency gate asks whether there is *enough* data. This asks
whether the data is *evidence*, and the two answer different questions: the
development database holds a demonstration pan with enough readings, across
enough rows, to clear every count in the sufficiency gate. Nothing downstream
could tell a model fitted to that from one fitted to a field campaign, because a
metric does not carry its own provenance.

So the assertions here are about which class a row lands in and what a caller is
then allowed to say. The one that matters most is the last: a version that
records no provenance at all must fail closed, because defaulting it to
"genuine" is precisely how an old number silently becomes a field claim.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from app.ml.provenance import (
    EVIDENCE_NOT_GENUINE_MESSAGE,
    EvidenceClass,
    classify_evidence,
    demonstration_target_ids,
    verdict_from_parameters,
)
from app.services.ml import LeakageDetectedError, MLService


@dataclasses.dataclass
class Row:
    """A stand-in for ``DatasetRow`` carrying the provenance fields the gate reads."""

    row_id: str
    observation_data_source: str | None
    weather_source: str | None = None
    target_source_id: str | None = None
    features: dict[str, Any] = dataclasses.field(default_factory=dict)


def genuine(count: int = 2) -> list[Row]:
    return [Row(f"r{n}", "sensor", "weather_api") for n in range(count)]


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def test_measured_rows_are_the_only_evidential_ones() -> None:
    verdict = classify_evidence(genuine(3))

    assert verdict.evidence_class is EvidenceClass.GENUINE
    assert verdict.is_evidential is True
    assert verdict.genuine_rows == 3
    assert verdict.refusal_reason() is None


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        pytest.param(Row("r0", "demonstration"), EvidenceClass.DEMONSTRATION, id="observation"),
        pytest.param(Row("r0", "sensor", "demonstration"), EvidenceClass.DEMONSTRATION, id="weather"),
        pytest.param(Row("r0", None), EvidenceClass.UNKNOWN, id="no_source"),
    ],
)
def test_a_row_that_is_not_a_measurement_is_never_evidential(
    row: Row, expected: EvidenceClass
) -> None:
    verdict = classify_evidence([row])

    assert verdict.evidence_class is expected
    assert verdict.is_evidential is False


def test_a_demonstration_forecast_is_caught_even_though_the_observation_is_real() -> None:
    """A real salinity reading beside a typed forecast is still not a real event.

    This is the case that matters operationally: the observation passes every
    existing check in the project, and only the weather provenance disqualifies
    the row. A gate that looked at observations alone would call this genuine.
    """
    verdict = classify_evidence([Row("r0", "sensor", "demonstration")])

    assert verdict.evidence_class is EvidenceClass.DEMONSTRATION
    assert "demonstration forecast" in " ".join(verdict.reasons)


def test_a_label_from_a_demonstration_outcome_disqualifies_a_measured_observation() -> None:
    verdict = classify_evidence(
        [Row("r0", "sensor", "weather_api", target_source_id="out-demo")],
        demonstration_targets={"out-demo"},
    )

    assert verdict.evidence_class is EvidenceClass.DEMONSTRATION
    assert verdict.demonstration_rows == 1


def test_one_demonstration_row_among_genuine_ones_is_mixed_not_genuine() -> None:
    """Severity ordering: the worst class present wins.

    A single typed row inside an otherwise real campaign does not make the model
    a description of the field, and reporting ``genuine`` because most rows were
    real would be averaging away exactly the rows that invalidate the claim.
    """
    verdict = classify_evidence([Row("r0", "sensor"), Row("r1", "demonstration")])

    assert verdict.evidence_class is EvidenceClass.MIXED
    assert verdict.is_evidential is False
    assert (verdict.genuine_rows, verdict.demonstration_rows) == (1, 1)


def test_a_row_with_no_provenance_is_untraceable_rather_than_assumed_real() -> None:
    """Fails closed. An unclassifiable row is not evidence of anything."""
    verdict = classify_evidence([Row("r0", None), Row("r1", "sensor")])

    assert verdict.evidence_class is EvidenceClass.UNKNOWN
    assert verdict.unverified_rows == 1
    assert verdict.is_evidential is False
    assert "not assumed to be genuine" in " ".join(verdict.reasons)


def test_an_absent_weather_source_is_not_treated_as_a_typed_one() -> None:
    """Silence is not a typed row.

    An unfilled optional field is a metadata gap, and it belongs in a
    completeness check. Treating it here as fabrication would refuse nearly every
    dataset and turn an evidence gate into a completeness gate.
    """
    verdict = classify_evidence(
        [Row("r0", "sensor", features={"forecast_rainfall_mm": 4.0})]
    )

    assert verdict.evidence_class is EvidenceClass.GENUINE
    assert verdict.is_evidential is True


def test_no_rows_classifies_as_unknown_rather_than_vacuously_genuine() -> None:
    """The empty set is not evidence. `all()` over nothing must not read as true."""
    verdict = classify_evidence([])

    assert verdict.evidence_class is EvidenceClass.UNKNOWN
    assert verdict.is_evidential is False
    assert verdict.total_rows == 0


def test_the_refusal_names_the_class_and_the_counts() -> None:
    verdict = classify_evidence([Row("r0", "demonstration"), Row("r1", "sensor")])

    reason = verdict.refusal_reason() or ""
    assert reason.startswith(EVIDENCE_NOT_GENUINE_MESSAGE)
    assert "mixed" in reason
    assert "1 genuine, 1 demonstration" in reason


# --------------------------------------------------------------------------
# Reading it back off a model version
# --------------------------------------------------------------------------


def test_a_version_recording_no_provenance_fails_closed() -> None:
    """The important one. Absent is not the same as genuine."""
    for parameters in (None, {}, {"evidence": "yes"}, {"evidence": {"evidence_class": "real"}}):
        verdict = verdict_from_parameters(parameters)

        assert verdict.evidence_class is EvidenceClass.UNKNOWN
        assert verdict.is_evidential is False


def test_a_genuine_verdict_survives_the_round_trip() -> None:
    recorded = classify_evidence(genuine(4)).as_dict()

    verdict = verdict_from_parameters({"evidence": recorded})

    assert verdict.evidence_class is EvidenceClass.GENUINE
    assert verdict.is_evidential is True
    assert verdict.genuine_rows == 4


def test_demonstration_target_ids_reads_the_outcomes_own_flag() -> None:
    """The rule that marks an outcome as a demonstration stays in the outcome model."""
    outcomes = [
        {"outcome_id": "out-1", "is_demonstration": True},
        {"outcome_id": "out-2", "is_demonstration": False},
    ]

    assert demonstration_target_ids(outcomes) == frozenset({"out-1"})


# --------------------------------------------------------------------------
# Leakage is re-checked at training time
# --------------------------------------------------------------------------


def _report() -> Any:
    """The build report a stored dataset always carries."""
    from app.models.dataset import DatasetReport

    return DatasetReport(
        record_count=1,
        labelled_count=1,
        unlabelled_count=0,
        pan_count=1,
        date_range={"min": None, "max": None},
    )


def _dataset_with_leakage(leakage_class: str) -> Any:
    """A dataset manifest declaring one feature with the given leakage class."""
    from app.models.dataset import (
        FeatureDefinitionRecord,
        ResearchDataset,
        TargetSpec,
    )
    from app.models.enums import LeakageClass, ResearchTarget, TargetKind

    definition = FeatureDefinitionRecord(
        name="post_event_salinity_g_l",
        kind="numeric",
        leakage_class=LeakageClass(leakage_class),
        source_collection="pan_observations",
        source_field="salinity_g_l",
        selection_rule="carried straight through",
        derivation="read from the observation",
        scientific_basis="a quantity the forecast is trying to predict",
    )
    return ResearchDataset(
        dataset_id="ds-leak",
        name=None,
        target=TargetSpec(
            name=ResearchTarget.OPERATIONAL_RISK,
            label_column="label_operational_risk",
            kind=TargetKind.CLASSIFICATION,
            horizon_hours=24.0,
        ),
        content_hash="sha256:" + "a" * 64,
        feature_names=[definition.name],
        features=[definition],
        report=_report(),
    )


def test_a_feature_measured_after_the_anchor_is_refused_before_any_estimator_exists() -> None:
    """The builder already refuses these; this is the check at the last line.

    A manifest can be hand-edited, restored from a backup, or written by an
    older builder, and a post-event measurement handed to an estimator produces
    a clean run and an excellent score with nothing in the record to say the
    score is meaningless.
    """
    with pytest.raises(LeakageDetectedError) as raised:
        MLService._check_leakage(_dataset_with_leakage("future"))

    assert "post_event_salinity_g_l" in str(raised.value)
    assert "could not have had" in str(raised.value)


def test_features_knowable_at_prediction_time_pass_the_leakage_check() -> None:
    assert MLService._check_leakage(_dataset_with_leakage("as_of")) is None
    assert MLService._check_leakage(_dataset_with_leakage("static")) is None


def test_a_feature_the_manifest_does_not_describe_is_left_to_the_builder() -> None:
    """No definition means no leakage claim to contradict.

    The builder refuses a post-anchor variable when it builds the dataset, so a
    feature absent from the manifest's definitions is either one the catalogue
    never described or a manifest gap. Guessing either way here would refuse
    datasets for a reason the reader cannot act on.
    """
    from app.models.dataset import ResearchDataset, TargetSpec
    from app.models.enums import ResearchTarget, TargetKind

    dataset = ResearchDataset(
        dataset_id="ds-bare",
        name=None,
        target=TargetSpec(
            name=ResearchTarget.OPERATIONAL_RISK,
            label_column="label_operational_risk",
            kind=TargetKind.CLASSIFICATION,
            horizon_hours=24.0,
        ),
        content_hash="sha256:" + "b" * 64,
        feature_names=["current_salinity_g_l"],
        features=[],
        report=_report(),
    )

    assert MLService._check_leakage(dataset) is None
