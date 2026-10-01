"""Module 07 - the report that has to be read before a model is trained.

A dataset file on its own says nothing about whether it can support a claim. It
takes 200 rows from one pan in one dry week, or a column that is 90% empty, or a
target with a single distinct value, and a model will still return a number. This
module is what turns the file into an assessment.

Everything here is computed with the standard library. A research report that
silently depended on a statistics library's default handling of empty input would
be a report whose edge-case behaviour nobody in this project could check, and
pandas is a training-time dependency this module must not drag into the API.

The rules the calculations follow:

* **A statistic over an empty set is null, not zero.** A mean of no values, a
  median of no values, a class balance over no labels: all null, each with a
  reason attached. Zero would be indistinguishable from a real measurement.
* **Percentiles interpolate between observed values.** Stated because the choice
  is visible in the output: for one value p25 and p75 equal it, and for two they
  sit halfway.
* **Missingness is reported per column with the reason.** A column that is 60%
  empty and one that is 60% empty *for a known reason* are different problems, so
  the most common per-row reason travels with the count.
* **Duplicates are counted on the feature vector, not the whole row.** A repeated
  reading of one pan on one visit is real operational data. What matters is
  whether identical features arrived with different labels, which is a question
  about the data rather than about the visit schedule.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from app.models.base import normalize_datetime
from app.models.dataset import (
    CategoricalDistribution,
    ClassDistribution,
    ColumnDistribution,
    DatasetReport,
    DataSourceSummary,
    DuplicateSummary,
    MissingValueSummary,
    NumericDistribution,
)
from app.models.enums import FeatureKind
from app.models.risk_definition import RiskClassDistribution
from app.services.dataset_builder import BuiltDataset, DatasetRow
from app.services.dataset_catalog import FeatureDefinition, TargetDefinition
from app.services.risk_evaluation import build_class_distribution

#: A column missing at least this share of its rows gets flagged. Not a hard
#: failure: plenty of legitimate datasets have a sparse column, and a model can
#: handle one. It is flagged because it should be a decision rather than a
#: surprise.
COVERAGE_WARNING_PERCENT = 40.0

#: A dataset this small cannot support a train/test split with any confidence.
#: The number is a judgement, and it is stated as one rather than enforced.
MIN_ROWS_FOR_TRAINING = 50

#: How many example duplicate groups to name, so the figure is checkable.
MAX_DUPLICATE_EXAMPLES = 5

DUPLICATE_DEFINITION = (
    "Two rows are duplicates when their feature vectors are identical, including "
    "the nulls. The row identity, the anchor instant and the label are excluded "
    "from the comparison, so a repeated visit with a new label is reported as a "
    "contradiction rather than as a repeat."
)

NO_RECORDS = (
    "No rows were built, so there is nothing to summarise. Every figure below is "
    "null rather than zero, and no model can be trained on this."
)

EMPTY_COLUMN = "No row carried a value for this column, so no statistic exists."


def percentile(sorted_values: Sequence[float], fraction: float) -> float | None:
    """A linearly interpolated percentile of an already-sorted sequence.

    Interpolating rather than picking a neighbour is stated because it is visible
    in the output: for a single value every percentile equals it, and for two
    values p25 and p75 land a quarter of the way along.
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def numeric_distribution(
    values: Sequence[float | None], *, unit: str | None
) -> NumericDistribution:
    """Summarise a numeric column over its present values.

    The sample standard deviation is used, with the ``n - 1`` denominator, and is
    null for a single value because a spread around one observation is not a
    spread. Reported as null with a reason rather than as 0.0.
    """
    present = sorted(value for value in values if value is not None)
    missing = len(values) - len(present)
    if not present:
        return NumericDistribution(
            count=0, missing=missing, empty_reason=EMPTY_COLUMN, unit=unit
        )
    mean = sum(present) / len(present)
    if len(present) > 1:
        variance = sum((value - mean) ** 2 for value in present) / (len(present) - 1)
        deviation = variance**0.5
    else:
        deviation = None
    return NumericDistribution(
        count=len(present),
        missing=missing,
        minimum=present[0],
        p25=percentile(present, 0.25),
        median=percentile(present, 0.5),
        p75=percentile(present, 0.75),
        maximum=present[-1],
        mean=mean,
        standard_deviation=deviation,
        unit=unit,
    )


def categorical_distribution(
    values: Sequence[str | None], *, unit: str | None
) -> CategoricalDistribution:
    """Summarise a categorical column by value counts, most common first.

    Ordered by count and then alphabetically, so two runs over the same data
    produce the same report and a diff between them shows a real change.
    """
    present = [value for value in values if value is not None]
    missing = len(values) - len(present)
    counter = Counter(present)
    if not counter:
        return CategoricalDistribution(
            count=0,
            missing=missing,
            values={},
            distinct=0,
            unit=unit,
            empty_reason=EMPTY_COLUMN,
        )
    ordered = dict(
        sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    )
    return CategoricalDistribution(
        count=len(present),
        missing=missing,
        values=ordered,
        distinct=len(ordered),
        unit=unit,
    )


def column_distribution(
    column: str, values: Sequence[Any], feature: FeatureDefinition
) -> ColumnDistribution:
    """Summarise one column in the shape its kind calls for."""
    if feature.kind is FeatureKind.NUMERIC:
        numeric = numeric_distribution(list(values), unit=feature.unit)
        return ColumnDistribution(
            column=column,
            kind=FeatureKind.NUMERIC,
            numeric=numeric,
            description=feature.scientific_basis,
            unit=feature.unit,
        )
    categorical = categorical_distribution(
        [None if value is None else str(value) for value in values], unit=feature.unit
    )
    return ColumnDistribution(
        column=column,
        kind=FeatureKind.CATEGORICAL,
        categorical=categorical,
        description=feature.scientific_basis,
        unit=feature.unit,
    )


def missing_value_summary(
    rows: Sequence[DatasetRow], column: str, feature: FeatureDefinition
) -> MissingValueSummary:
    """Coverage of one column, with the reason its cells are empty.

    The reason reported is the most common one among the empty cells, because a
    single reason usually explains the whole column. A column can be empty for
    more than one reason at once — a salinity change is missing both when there is
    no earlier observation and when the earlier one had no salinity — and this
    reports the dominant cause rather than pretending there is only one.
    """
    total = len(rows)
    missing_entries = [
        entry for row in rows for entry in row.missing_features if entry.field == column
    ]
    present = total - len(missing_entries)
    reason: str | None = feature.nullable_reason
    if missing_entries:
        reason = Counter(entry.reason for entry in missing_entries).most_common(1)[0][0]
    missing_percent = (len(missing_entries) / total * 100.0) if total else None
    return MissingValueSummary(
        column=column,
        present_count=present,
        missing_count=len(missing_entries),
        missing_percent=missing_percent,
        reason=reason,
        coverage_warning=missing_percent is not None
        and missing_percent >= COVERAGE_WARNING_PERCENT,
    )


def class_distribution(
    rows: Sequence[DatasetRow], target: TargetDefinition
) -> ClassDistribution:
    """How the target spreads across the rows, for regression and classification alike.

    For a classification target this is the class balance, and
    ``imbalance_ratio`` is the largest share over the smallest. That ratio is null
    rather than infinite when a class is entirely absent, because "the rarest class
    holds zero rows" is a fact and "it is infinitely rare" is a division by zero.

    For a regression target the same block reports the number of distinct
    measured values, because a target with one distinct value cannot be learned
    and that is a property of the data rather than of any model.
    """
    labels = [row.label for row in rows if row.is_labelled]
    unlabelled = len(rows) - len(labels)
    coverage = (len(labels) / len(rows) * 100.0) if rows else None
    counts = Counter(_label_key(label) for label in labels)
    ordered = dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
    percentages = {
        key: (value / len(labels) * 100.0) if labels else 0.0
        for key, value in ordered.items()
    }

    shares = sorted(ordered.values())
    imbalance = (shares[-1] / shares[0]) if shares and shares[0] > 0 else None

    missing_classes: list[str] = []
    if target.classes:
        missing_classes = [name for name in target.classes if name not in ordered]

    degenerate = len(ordered) < 2
    note = _distribution_note(
        target=target,
        ordered=ordered,
        degenerate=degenerate,
        labelled=len(labels),
        unlabelled=unlabelled,
        imbalance=imbalance,
        missing_classes=missing_classes,
    )
    return ClassDistribution(
        label_column=target.label_column,
        kind=target.kind,
        labelled_count=len(labels),
        unlabelled_count=unlabelled,
        coverage_percent=coverage,
        class_counts=ordered,
        class_percentages=percentages,
        distinct_values=len(ordered),
        imbalance_ratio=imbalance,
        degenerate=degenerate,
        expected_classes=list(target.classes) if target.classes else None,
        missing_classes=missing_classes,
        note=note,
    )


def _distribution_note(
    *,
    target: TargetDefinition,
    ordered: dict[str, int],
    degenerate: bool,
    labelled: int,
    unlabelled: int,
    imbalance: float | None,
    missing_classes: Sequence[str],
) -> str:
    if labelled == 0:
        return (
            "No row carries this target. Every measurement that would have labelled "
            "one is either absent or was not taken after the horizon, and none of "
            "them was invented to fill the gap."
        )
    if degenerate:
        return (
            f"The {labelled} labelled row(s) hold {len(ordered)} distinct value(s), "
            "so there is nothing to learn: any model would reproduce a constant. "
            "This is a fact about the recorded data, not about a model."
        )
    if missing_classes:
        return (
            f"{len(missing_classes)} of the target's classes were never observed: "
            + ", ".join(missing_classes)
            + ". A model cannot be evaluated against a class no event produced."
        )
    if target.is_classification and imbalance is not None and imbalance > 10.0:
        return (
            f"The rarest class holds {min(ordered.values())} row(s) and the most "
            f"common {max(ordered.values())}, a ratio of {imbalance:.1f}:1. An "
            "accuracy figure on this split would mostly be reporting the majority "
            "class."
        )
    if unlabelled > 0:
        return (
            f"{unlabelled} row(s) have no target and cannot be trained on. They "
            "are kept so the gap is visible rather than silently dropped."
        )
    return f"{labelled} labelled row(s) across {len(ordered)} distinct value(s)."


def _label_key(label: Any) -> str:
    """A stable, readable key for a label value.

    Floats go through ``repr`` so two different salinities never collapse into
    one bucket, and a class name is passed through unchanged.
    """
    if isinstance(label, float):
        return repr(label)
    return str(label)


def duplicate_summary(rows: Sequence[DatasetRow]) -> DuplicateSummary:
    """Count repeated feature vectors, and split them by whether they agree.

    The split is the useful part. Rows with identical features and identical
    labels are a redundant visit; rows with identical features and different
    labels are the same question with two answers, and a model scored on them is
    being scored on noise.
    """
    if not rows:
        return DuplicateSummary(
            definition=DUPLICATE_DEFINITION,
            duplicate_group_count=0,
            duplicate_row_count=0,
            example_group_keys=[],
            note=NO_RECORDS,
        )

    groups: dict[tuple[tuple[str, Any], ...], list[DatasetRow]] = {}
    for row in rows:
        signature = tuple(
            (name, _label_key(row.features.get(name)))
            for name in sorted(row.features)
        )
        groups.setdefault(signature, []).append(row)

    duplicated = [members for members in groups.values() if len(members) > 1]
    contradictory = [
        members
        for members in duplicated
        if len({_label_key(member.label) for member in members}) > 1
    ]
    examples = [
        f"{members[0].pan_id} @ {normalize_datetime(members[0].anchor_timestamp).isoformat()}"
        " x" + str(len(members))
        for members in sorted(
            duplicated, key=lambda members: members[0].row_id
        )[:MAX_DUPLICATE_EXAMPLES]
    ]
    note: str | None = None
    if contradictory:
        note = (
            f"{len(contradictory)} feature vector(s) appear with more than one "
            "label. Two rows the model cannot tell apart were given different "
            "answers, so neither can be predicted; expect a score that reflects "
            "recording noise rather than skill."
        )
    elif duplicated:
        note = (
            f"{len(duplicated)} feature vector(s) repeat with a consistent label, "
            "which is what a repeated visit looks like. They will still inflate a "
            "random split's apparent performance if one visit's rows land on both "
            "sides of it."
        )
    return DuplicateSummary(
        definition=DUPLICATE_DEFINITION,
        duplicate_group_count=len(duplicated),
        duplicate_row_count=sum(len(members) - 1 for members in duplicated),
        identical_feature_and_label_groups=len(duplicated) - len(contradictory),
        contradictory_groups=len(contradictory),
        example_group_keys=examples,
        note=note,
    )


def data_source_summary(built: BuiltDataset) -> DataSourceSummary:
    """Where the rows came from, and whether the read was bounded."""
    sources = built.sources
    truncated = bool(built.truncated_collections)
    note: str | None = None
    if truncated:
        note = (
            "The read cap was reached for "
            + ", ".join(built.truncated_collections)
            + ", so this dataset is a bounded sample of those collections rather "
            "than all of it. The cap is part of the dataset's hash, so a wider "
            "build is a visibly different dataset."
        )
    elif sources["rows_using_forecast_rainfall"] < len(built.rows):
        note = (
            f"{len(built.rows) - sources['rows_using_forecast_rainfall']} row(s) had "
            "no forecast in force at the anchor, so their physics columns are empty. "
            "The rainfall that actually fell was not used to fill them."
        )
    excluded = sources["demonstration_records_excluded"]
    withheld = sum(excluded.values())
    if withheld:
        counted = (
            ", ".join(f"{count} {collection}" for collection, count in excluded.items() if count)
        )
        note = (
            (note + " ") if note else ""
        ) + (
            f"{withheld} demonstration record(s) were withheld from this build "
            f"({counted}). They are not measurements, so no row is anchored on them "
            "and no feature is read from them. The records themselves are still in "
            "the operational database and are still visible on the dashboard."
        )
    return DataSourceSummary(
        collection_counts=sources["collection_counts"],
        observation_sources=sources["observation_sources"],
        weather_sources=sources["weather_sources"],
        rows_using_forecast_rainfall=sources["rows_using_forecast_rainfall"],
        rows_using_physics_prediction=sources["rows_using_physics_prediction"],
        demonstration_records_excluded=excluded,
        source_windows=sources["source_windows"],
        truncated=truncated,
        truncated_collections=list(built.truncated_collections),
        note=note,
    )


def _overlapping_target_groups(rows: Sequence[DatasetRow]) -> int:
    """How many measurements back more than one row.

    Overlapping horizons are ordinary when a pan is visited often, and they are
    also the reason a random train/test split leaks: the same measurement ends up
    on both sides and the model is scored partly on a value it has seen. The count
    belongs to the split strategy, so it is reported with the data.
    """
    counter = Counter(
        row.target_source_id for row in rows if row.is_labelled and row.target_source_id
    )
    return sum(1 for count in counter.values() if count > 1)


def _warnings(
    *,
    rows: Sequence[DatasetRow],
    labelled: int,
    distributions: Sequence[ColumnDistribution],
    missing_values: Sequence[MissingValueSummary],
    target_distribution: ClassDistribution,
    risk_distribution: RiskClassDistribution | None,
    duplicates: DuplicateSummary,
    overlapping: int,
    audit_passed: bool,
) -> list[str]:
    """The things a researcher should decide about before training.

    Phrased as decisions rather than errors. Most of them are ordinary properties
    of a small operational dataset, and refusing to build would be less honest
    than building and saying plainly what the data is.
    """
    warnings: list[str] = []
    if not rows:
        warnings.append(NO_RECORDS)
        return warnings

    if labelled == 0:
        warnings.append(
            "No row carries a target. Nothing can be trained on this until "
            "outcomes are measured after the horizon, and no label was invented "
            "to make the file usable."
        )
    if len(rows) < MIN_ROWS_FOR_TRAINING:
        warnings.append(
            f"{len(rows)} row(s) is below the {MIN_ROWS_FOR_TRAINING} at which a "
            "train/test split means anything. Treat any score from this dataset as "
            "descriptive."
        )
    if target_distribution.degenerate and labelled:
        warnings.append(target_distribution.note or "The target is degenerate.")
    if target_distribution.missing_classes:
        warnings.append(
            "These target classes were never observed: "
            + ", ".join(target_distribution.missing_classes)
            + ". Any accuracy figure over the observed classes alone is not an "
            "accuracy figure over the target."
        )
    # A rule-derived label carries warnings the generic block above cannot make:
    # its classes are the researcher's, not the catalogue's, so an unobserved one
    # is invisible to target_distribution and the imbalance policy is the risk
    # module's. Surfaced here so the dataset report and the rule's own report
    # cannot disagree about whether a dataset is worth fitting.
    if risk_distribution is not None:
        warnings.extend(risk_distribution.warnings)
    sparse = [entry.column for entry in missing_values if entry.coverage_warning]
    if sparse:
        warnings.append(
            "These columns are missing for at least "
            f"{COVERAGE_WARNING_PERCENT:.0f}% of rows: "
            + ", ".join(sparse)
            + ". Imputing them would add a value no instrument produced; leaving "
            "them empty will make most models refuse the column entirely."
        )
    constant = [
        column.column
        for column in distributions
        if column.kind is FeatureKind.NUMERIC
        and column.numeric is not None
        and column.numeric.count > 0
        and column.numeric.minimum == column.numeric.maximum
    ]
    if constant:
        warnings.append(
            "These columns hold one distinct value across every row: "
            + ", ".join(constant)
            + ". They carry no information and will be dropped or given an "
            "arbitrary coefficient by most learners."
        )
    if duplicates.contradictory_groups:
        warnings.append(duplicates.note or "Duplicate feature vectors disagree on the label.")
    if overlapping:
        warnings.append(
            f"{overlapping} target measurement(s) back more than one row. Split the "
            "data by measurement or by pan, not randomly, or the same measurement "
            "will appear on both sides of the split."
        )
    if not audit_passed:
        warnings.append("The causal audit reported violations; do not train on this.")
    return warnings


def build_report(built: BuiltDataset) -> DatasetReport:
    """Assemble the full pre-training report for a built dataset.

    This is the function the module exists for. Everything in
    :class:`~app.models.dataset.DatasetReport` is computed here, from the built
    rows, before anything is stored.
    """
    rows = built.rows
    labelled = sum(1 for row in rows if row.is_labelled)
    unlabelled = len(rows) - labelled

    distributions = [
        column_distribution(
            feature.name, [row.features.get(feature.name) for row in rows], feature
        )
        for feature in built.features
    ]
    missing_values = [
        missing_value_summary(rows, feature.name, feature) for feature in built.features
    ]
    target_distribution = class_distribution(rows, built.target)
    risk_distribution = (
        build_class_distribution(
            built.spec.risk_definition,
            [row.risk for row in rows if row.risk is not None],
        )
        if built.spec.risk_definition is not None
        else None
    )
    duplicates = duplicate_summary(rows)
    overlapping = _overlapping_target_groups(rows)
    sources = data_source_summary(built)

    anchors = [row.anchor_timestamp for row in rows]
    date_range: dict[str, Any]
    if anchors:
        earliest, latest = min(anchors), max(anchors)
        date_range = {
            "first_anchor": earliest,
            "last_anchor": latest,
            "span_days": (latest - earliest).total_seconds() / 86_400.0,
        }
    else:
        date_range = {
            "first_anchor": None,
            "last_anchor": None,
            "span_days": None,
            "reason": "No rows were built, so there is no date range to report.",
        }

    pan_ids = sorted({row.pan_id for row in rows})
    warnings = _warnings(
        rows=rows,
        labelled=labelled,
        distributions=distributions,
        missing_values=missing_values,
        target_distribution=target_distribution,
        risk_distribution=risk_distribution,
        duplicates=duplicates,
        overlapping=overlapping,
        audit_passed=built.audit.passed,
    )
    return DatasetReport(
        record_count=len(rows),
        labelled_count=labelled,
        unlabelled_count=unlabelled,
        pan_count=len(pan_ids),
        pan_ids=pan_ids,
        date_range=date_range,
        missing_values=missing_values,
        distributions=distributions,
        target_distribution=target_distribution,
        risk_distribution=risk_distribution,
        duplicates=duplicates,
        data_sources=sources,
        leakage_audit=built.audit,
        overlapping_target_groups=overlapping,
        warnings=warnings,
    )


__all__ = [
    "COVERAGE_WARNING_PERCENT",
    "MIN_ROWS_FOR_TRAINING",
    "build_report",
    "class_distribution",
    "column_distribution",
    "duplicate_summary",
    "percentile",
]
