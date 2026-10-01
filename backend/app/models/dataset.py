"""Module 07 - the stored manifest of a research dataset.

A dataset in this project is not a table of numbers. It is a claim about what a
model was trained on, and this document is that claim, made checkable.

It holds three things:

* **the specification** — the named target, the horizon, the lookback window and
  the feature set, so the build can be repeated rather than remembered;
* **the catalogue** — every column with its source collection, its source field,
  the rule that selected the record, its unit and why it can be null, so a
  reader never has to guess where a number came from;
* **the report** — record counts, date range, pan coverage, missing values,
  distributions, class balance, duplicates, data sources and the leakage audit,
  all computed at build time and stored rather than recomputed on demand, because
  a report that changes as newer readings arrive cannot support a reproducibility
  claim.

**The rows themselves are not stored.** A snapshot of the data would go stale the
moment an operator backfilled a reading, and a stale snapshot is worse than no
snapshot: it looks reproducible and is not. The manifest is stored, the rows are
derived from stored observations and outcomes, and the content hash ties the two
together. Re-running the same specification against the same records yields the
same hash and the same ``dataset_id``; if the records have moved on, the hash
changes and the rebuild endpoint says so rather than quietly serving a different
dataset under the old name.

**No target is ever inferred.** ``target`` is required, and its ``label_column``
is part of the hash. There is no default target anywhere in this module.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_identifier,
    normalize_pan_id,
    utcnow,
)
from app.models.enums import FeatureKind, LeakageClass, ResearchTarget, TargetKind
from app.models.risk_definition import RiskClassDistribution
from app.services.dataset_versioning import HASH_PATTERN

#: Version of the row format a builder produces.
#:
#: Bumped whenever a built row changes in a way that would alter its content
#: hash, and hashed into every dataset. Without it a builder that emits different
#: bytes could claim the same dataset identity as one that emitted the old ones.
BUILDER_SCHEMA_VERSION = 1

#: A content hash identifies a dataset by what it contains, not by when it was
#: built. A dataset of the same rows under the same specification is the same
#: dataset, and rebuilding it returns the existing record instead of a second
#: near-identical one.

#: Short form used in filenames and UI, so a hash can be read aloud or pasted
#: into a notebook without the full digest.
SHORT_HASH_LENGTH = 12


class MissingFeature(BaseModel):
    """One column of one row that is empty, and why.

    Reported rather than filled. The alternative — a mean, a carry-forward, a
    zero — would put a number in the file that no instrument produced, and the
    model would learn from it as though it were real.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    field: str = Field(min_length=1, max_length=64, description="The empty column.")
    reason: str = Field(
        min_length=1,
        max_length=400,
        description="Why it is empty, in a sentence a researcher can act on.",
    )


class TargetSpec(BaseModel):
    """The configured label of a dataset.

    Stored verbatim, because it is the part of a dataset most likely to be
    quietly changed between two experiments. Every field here goes into the
    content hash, so altering the horizon or a bin edge produces a visibly
    different dataset rather than one that shares a name with its predecessor.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: ResearchTarget = Field(
        description=(
            "Which quantity to learn. Required: this module has no default "
            "target, because a label nobody chose is a label nobody can defend."
        )
    )
    label_column: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Column the target is written to.",
    )
    kind: TargetKind = Field(description="Regression or classification.")
    horizon_hours: float = Field(
        gt=0.0,
        allow_inf_nan=False,
        description=(
            "How far ahead of the anchor a measurement must have been taken to "
            "count as the outcome. Must exceed zero, so the measurement can never "
            "be the reading it is being asked to predict."
        ),
    )
    require_rain_event: bool = Field(
        default=False,
        description=(
            "Keep only rows whose horizon window contains recorded observed "
            "rainfall. Off by default: it is a real restriction on the data, and "
            "silently applying it would change what the dataset is without saying "
            "so."
        ),
    )
    bin_edges: list[float] | None = Field(
        default=None,
        description=(
            "Class boundaries, ascending, for a banded target. Required for "
            "banded targets and refused for the others, so a set of edges can "
            "never sit in a specification where it is not being used."
        ),
    )
    require_bin_edges: bool = Field(
        default=False,
        description="Whether the target's definition needs bin edges to be usable.",
    )
    requires_risk_definition: bool = Field(
        default=False,
        description=(
            "Whether the target's labels are produced by a researcher's rule "
            "rather than by a measurement. Copied from the target catalogue so the "
            "specification can be read on its own and still say what produced its "
            "labels."
        ),
    )
    risk_definition_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description=(
            "The stored risk definition whose rule derived this dataset's labels. "
            "Required for the operational-risk target, and refused for every other "
            "target, because no other label is produced by a rule."
        ),
    )
    risk_definition_version: int | None = Field(
        default=None,
        ge=1,
        description=(
            "The version of that definition, recorded so the citation resolves to "
            "one document rather than to whatever the name points at now."
        ),
    )
    risk_specification_hash: str | None = Field(
        default=None,
        pattern=HASH_PATTERN,
        description=(
            "The content hash of that definition. Carried alongside the id and "
            "version so a dataset remains traceable to the exact rule even if the "
            "definition document is later read differently."
        ),
    )
    allow_retrospective_label: bool = Field(
        default=False,
        description=(
            "Acknowledge that the named rule reads a post-anchor measurement, so "
            "its labels describe what happened rather than what was knowable. "
            "Required to build a dataset against a retrospective rule, and refused "
            "for a prospective one, where acknowledging it would suggest a "
            "qualification that does not apply. A dataset built this way is "
            "labelled retrospective throughout and cannot be used to forecast the "
            "event it is graded against."
        ),
    )

    @model_validator(mode="after")
    def _edges_match_the_target(self) -> "TargetSpec":
        """Edges and risk references are required exactly when the target needs them.

        All of the rules are enforced in both directions. Without the second half
        of each pair a caller could attach bin edges to a regression target, or a
        risk definition to a measured one, where they would be hashed into the
        dataset identity while changing nothing about the labels.
        """
        if self.require_bin_edges and not self.bin_edges:
            raise ValueError(
                f"target {self.name.value} is banded, so bin_edges are required: "
                "the module will not choose where the class boundaries are, "
                "because a class distribution is then a fact about that choice"
            )
        if self.bin_edges and not self.require_bin_edges:
            raise ValueError(
                f"target {self.name.value} is not banded, so bin_edges have no "
                "meaning here and are refused"
            )
        if self.bin_edges is not None and any(
            upper <= lower for lower, upper in zip(self.bin_edges, self.bin_edges[1:])
        ):
            raise ValueError("bin_edges must be strictly ascending")
        self._check_risk_reference()
        return self

    def _check_risk_reference(self) -> None:
        """A rule reference is all-or-nothing, and only for a derived target."""
        parts = {
            "risk_definition_id": self.risk_definition_id,
            "risk_definition_version": self.risk_definition_version,
            "risk_specification_hash": self.risk_specification_hash,
        }
        present = [name for name, value in parts.items() if value is not None]
        if present and len(present) != len(parts):
            raise ValueError(
                "a risk definition reference needs all three of "
                "risk_definition_id, risk_definition_version and "
                f"risk_specification_hash; got only {', '.join(present)}"
            )
        if not self.requires_risk_definition and present:
            raise ValueError(
                f"target {self.name.value} is not derived from a rule, so a risk "
                "definition reference has no meaning here and is refused"
            )
        if self.requires_risk_definition and not self.risk_definition_id:
            raise ValueError(
                f"target {self.name.value} is derived from a researcher's rule, so "
                "risk_definition_id is required: the module supplies no severity "
                "vocabulary and no threshold, and a dataset built without a rule "
                "would have a class distribution that is a fact about this module "
                "rather than about any pan"
            )

    @property
    def horizon_seconds(self) -> float:
        return self.horizon_hours * 3600.0


class FeatureDefinitionRecord(BaseModel):
    """One feature as it was actually built, copied from the catalogue.

    A copy rather than a reference, so a manifest read years later describes the
    columns it was built from even if the catalogue has since been corrected.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str
    kind: FeatureKind
    leakage_class: LeakageClass
    unit: str | None = None
    source_collection: str = Field(min_length=1, max_length=64)
    source_field: str = Field(min_length=1, max_length=64)
    selection_rule: str = Field(min_length=1, max_length=400)
    derivation: str = Field(min_length=1, max_length=800)
    scientific_basis: str = Field(min_length=1, max_length=800)
    nullable_reason: str | None = None

    @property
    def is_usable_without_missing_data(self) -> bool:
        """Whether the column is defined for every row in principle."""
        return self.nullable_reason is None


class ExcludedFieldRecord(BaseModel):
    """A source field deliberately left out of the feature set, and why."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    field: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)
    reason_kind: Literal["future_measurement", "row_identifier"]


class MissingValueSummary(BaseModel):
    """Coverage of one column across the built rows."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    column: str
    present_count: int = Field(ge=0)
    missing_count: int = Field(ge=0)
    missing_percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        allow_inf_nan=False,
        description="Null only when there are no rows, since there is nothing to divide by.",
    )
    reason: str | None = Field(
        default=None,
        max_length=400,
        description="Why the column is empty, taken from the most common reason reported.",
    )
    coverage_warning: bool = Field(
        default=False,
        description="True when the column is missing often enough to change what a model learns.",
    )


class NumericDistribution(BaseModel):
    """Summary statistics for a numeric column.

    All figures are computed over present values only, and every one is null when
    there are none. A mean of an empty column is not zero.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    count: int = Field(ge=0)
    missing: int = Field(ge=0)
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    p25: float | None = Field(default=None, allow_inf_nan=False)
    median: float | None = Field(default=None, allow_inf_nan=False)
    p75: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)
    mean: float | None = Field(default=None, allow_inf_nan=False)
    standard_deviation: float | None = Field(default=None, allow_inf_nan=False)
    unit: str | None = None
    empty_reason: str | None = Field(
        default=None,
        max_length=300,
        description="Why every figure is null, when the column held no values.",
    )


class CategoricalDistribution(BaseModel):
    """Value counts for a categorical column, most common first."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    count: int = Field(ge=0)
    missing: int = Field(ge=0)
    values: dict[str, int] = Field(default_factory=dict)
    distinct: int = Field(ge=0)
    unit: str | None = None
    empty_reason: str | None = Field(default=None, max_length=300)


class ColumnDistribution(BaseModel):
    """The distribution of one column, in whichever shape its kind calls for."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    column: str
    kind: FeatureKind
    numeric: NumericDistribution | None = None
    categorical: CategoricalDistribution | None = None
    description: str | None = Field(
        default=None,
        max_length=400,
        description="The feature's catalogue entry, repeated here so a distribution is readable alone.",
    )
    unit: str | None = None

    @model_validator(mode="after")
    def _one_shape_matches_the_kind(self) -> "ColumnDistribution":
        if self.kind is FeatureKind.NUMERIC and self.numeric is None:
            raise ValueError("a numeric column needs a numeric distribution")
        if self.kind is FeatureKind.CATEGORICAL and self.categorical is None:
            raise ValueError("a categorical column needs a categorical distribution")
        return self


class ClassDistribution(BaseModel):
    """How the target is spread across the labelled rows.

    Reported for regression targets too, as counts of distinct measured values,
    because a regression target with one distinct value cannot be learned and
    that is a fact about the data rather than about the model.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    label_column: str
    kind: TargetKind
    labelled_count: int = Field(ge=0)
    unlabelled_count: int = Field(ge=0)
    coverage_percent: float | None = Field(
        default=None, ge=0.0, le=100.0, allow_inf_nan=False
    )
    class_counts: dict[str, int] = Field(default_factory=dict)
    class_percentages: dict[str, float] = Field(default_factory=dict)
    distinct_values: int = Field(ge=0)
    imbalance_ratio: float | None = Field(
        default=None,
        ge=1.0,
        allow_inf_nan=False,
        description=(
            "Largest class share divided by the smallest, for a classification "
            "target. Null when a class is absent, because a ratio against zero "
            "is not a number."
        ),
    )
    degenerate: bool = Field(
        default=False,
        description="True when the labelled rows hold fewer than two distinct values.",
    )
    expected_classes: list[str] | None = Field(
        default=None,
        description="The classes a closed-vocabulary target can take, and which were never seen.",
    )
    missing_classes: list[str] = Field(default_factory=list)
    note: str | None = Field(
        default=None,
        max_length=400,
        description="What this distribution means for training, in a sentence.",
    )


class DuplicateSummary(BaseModel):
    """Repeated feature vectors, and what they would do to a metric.

    Two rows with identical features but different labels are not a harmless
    repeat: they are the same question with two different answers, and a model
    scored on them is being scored on noise.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    definition: str = Field(
        min_length=1,
        max_length=400,
        description="What counted as a duplicate, stated so the number can be checked.",
    )
    duplicate_group_count: int = Field(ge=0)
    duplicate_row_count: int = Field(ge=0)
    identical_feature_and_label_groups: int = Field(
        default=0,
        ge=0,
        description="Groups whose rows also agree on the label: exact duplicates.",
    )
    contradictory_groups: int = Field(
        default=0,
        ge=0,
        description="Groups whose rows disagree on the label.",
    )
    example_group_keys: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Up to five example duplicate groups, as anchor instants.",
    )
    note: str | None = Field(default=None, max_length=400)


class DataSourceSummary(BaseModel):
    """Where the rows came from, counted by provenance.

    A dataset assembled mostly from manual entry and a dataset assembled from a
    calibrated gateway are not the same evidence, and the difference has to be
    visible before a result is claimed from it.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    collection_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Source records read per collection.",
    )
    observation_sources: dict[str, int] = Field(
        default_factory=dict,
        description="Anchor observations by their recorded data_source.",
    )
    weather_sources: dict[str, int] = Field(
        default_factory=dict,
        description="Weather records by their recorded source.",
    )
    rows_using_forecast_rainfall: int = Field(default=0, ge=0)
    rows_using_physics_prediction: int = Field(default=0, ge=0)
    demonstration_records_excluded: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Demonstration records withheld from this build, per collection, "
            "because they are not evidence. Reported rather than omitted: a "
            "researcher comparing these counts against the operational database "
            "would otherwise have no way to tell the isolation apart from records "
            "lost to a cap."
        ),
    )
    source_windows: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Earliest and latest record read per collection.",
    )
    truncated: bool = Field(
        default=False,
        description=(
            "True when a source hit the per-collection read cap, so the dataset "
            "is a bounded sample of the collection rather than all of it."
        ),
    )
    truncated_collections: list[str] = Field(default_factory=list)
    note: str | None = Field(
        default=None,
        # Wide enough that the report can state a withheld-record exclusion in
        # full at the same time as a truncation or a forecast-coverage note,
        # which are not mutually exclusive. Truncating here would be the one
        # outcome the field exists to prevent: a report that says a dataset is
        # short of records without saying that some were deliberately withheld.
        max_length=800,
    )


class LeakageViolation(BaseModel):
    """One row where a feature or a target broke the causal ordering.

    Always empty in a well-formed build. It exists as a field so that an empty
    list is a statement that was checked, rather than the absence of a check.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    row_id: str
    field: str
    reason: str = Field(min_length=1, max_length=400)


class LeakageAudit(BaseModel):
    """The result of auditing every built row for causal ordering."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    policy: str = Field(
        min_length=1,
        max_length=800,
        description="The rule every row was checked against, in words.",
    )
    rows_checked: int = Field(ge=0)
    rows_passed: int = Field(ge=0)
    violations: list[LeakageViolation] = Field(default_factory=list)
    excluded_fields: list[ExcludedFieldRecord] = Field(default_factory=list)
    checks: list[str] = Field(
        default_factory=list,
        description="The specific assertions the audit performed, in order.",
    )

    @property
    def passed(self) -> bool:
        return not self.violations


class DatasetReport(BaseModel):
    """Everything that has to be read before a model is trained on these rows.

    Stored with the dataset rather than computed on request, because a report
    that changes as new readings arrive cannot be cited as the state of the data
    at the time of an experiment.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    record_count: int = Field(
        ge=0, description="Rows built, labelled or not."
    )
    labelled_count: int = Field(ge=0)
    unlabelled_count: int = Field(ge=0)
    pan_count: int = Field(ge=0)
    pan_ids: list[str] = Field(default_factory=list)
    date_range: dict[str, Any] = Field(
        default_factory=dict,
        description="Earliest and latest anchor instant, and the span they cover.",
    )
    missing_values: list[MissingValueSummary] = Field(default_factory=list)
    distributions: list[ColumnDistribution] = Field(default_factory=list)
    target_distribution: ClassDistribution | None = None
    risk_distribution: RiskClassDistribution | None = Field(
        default=None,
        description=(
            "How a rule-derived label falls across the rows, reported against the "
            "classes the researcher declared. Null for a measured target, which no "
            "rule labelled. Present alongside target_distribution rather than "
            "instead of it, because the generic block cannot name a class the "
            "catalogue does not know about and this one can."
        ),
    )
    duplicates: DuplicateSummary | None = None
    data_sources: DataSourceSummary | None = None
    leakage_audit: LeakageAudit | None = None
    overlapping_target_groups: int = Field(
        default=0,
        ge=0,
        description=(
            "Groups of rows whose labels come from the same measurement. A random "
            "train/test split over such rows puts the same measurement on both "
            "sides, so this number belongs in the split strategy, not just the "
            "report."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="What a researcher should decide about before training on this.",
    )
    generated_at: UtcDateTime = Field(default_factory=utcnow)


class ResearchDataset(BaseDocument):
    """One built, versioned research dataset and the claim it makes.

    Keyed on ``content_hash`` rather than on anything the caller supplies: the
    same specification over the same stored records is the same dataset, and a
    rebuild that finds an existing hash returns that document instead of writing
    a second one. ``version`` then counts how many distinct datasets have been
    built for a target name, in creation order, so an experiment can cite both
    the number and the hash.
    """

    model_config = ConfigDict(validate_assignment=True)

    dataset_id: str = Field(
        default_factory=lambda: new_id("ds"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    name: str | None = Field(
        default=None,
        max_length=120,
        description="An operator's label for this dataset. Never part of the hash.",
    )
    target: TargetSpec
    content_hash: str = Field(pattern=HASH_PATTERN)
    builder_schema_version: int = Field(
        default=BUILDER_SCHEMA_VERSION,
        ge=1,
        description=(
            "The builder's own row format version, hashed into the dataset so a "
            "changed builder cannot be mistaken for an unchanged one."
        ),
    )
    version: int = Field(
        default=1,
        ge=1,
        description="How many distinct datasets exist for this target name, in order.",
    )
    feature_names: list[str] = Field(
        default_factory=list,
        description="The features built, in file order.",
    )
    features: list[FeatureDefinitionRecord] = Field(default_factory=list)
    row_keys: list[str] = Field(default_factory=list)
    excluded_fields: list[ExcludedFieldRecord] = Field(default_factory=list)
    lookback_hours: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    include_unlabelled: bool = Field(
        default=True,
        description=(
            "Whether rows with no measured target were kept. Stored because it "
            "changes which rows exist, so a rebuild that guessed differently would "
            "not reproduce this dataset."
        ),
    )
    max_source_records: int = Field(
        default=20_000,
        ge=1,
        description=(
            "The per-collection read cap this dataset was built under. Part of "
            "its identity: a wider build is a different dataset, not a bigger one."
        ),
    )
    pan_filter: list[str] = Field(default_factory=list)
    window: dict[str, Any] = Field(default_factory=dict)
    report: DatasetReport
    supersedes_content_hash: str | None = Field(
        default=None,
        pattern=HASH_PATTERN,
        description="The hash of the dataset this one replaces for the same target.",
    )
    created_at: UtcDateTime = Field(default_factory=utcnow)

    @field_validator("pan_filter", mode="before")
    @classmethod
    def _normalize_pan_filter(cls, value):
        if not isinstance(value, list):
            return value
        return [normalize_pan_id(item) for item in value]

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value):
        return normalize_identifier(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _report_matches_the_manifest(self) -> "ResearchDataset":
        """The headline counts must agree with each other.

        A manifest whose own numbers contradict one another cannot be cited in a
        paper, and the contradiction is the kind that only shows up much later.
        """
        if self.report.labelled_count + self.report.unlabelled_count != self.report.record_count:
            raise ValueError(
                "labelled and unlabelled counts do not add up to the record count"
            )
        return self

    @property
    def short_hash(self) -> str:
        """The first :data:`SHORT_HASH_LENGTH` hex digits, without the algorithm prefix."""
        return self.content_hash.split(":", 1)[1][:SHORT_HASH_LENGTH]

    @property
    def label_column(self) -> str:
        return self.target.label_column

    @property
    def is_labelled(self) -> bool:
        return self.report.labelled_count > 0


__all__ = [
    "HASH_PATTERN",
    "SHORT_HASH_LENGTH",
    "CategoricalDistribution",
    "ClassDistribution",
    "ColumnDistribution",
    "DataSourceSummary",
    "DatasetReport",
    "DuplicateSummary",
    "ExcludedFieldRecord",
    "FeatureDefinitionRecord",
    "LeakageAudit",
    "LeakageViolation",
    "MissingFeature",
    "MissingValueSummary",
    "NumericDistribution",
    "ResearchDataset",
    "TargetSpec",
]
