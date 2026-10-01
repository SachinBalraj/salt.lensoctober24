"""Module 07 - the HTTP contract for building research datasets.

The create schema is narrow on purpose, and the narrowness is the module's main
defence.

**There is no ``target_label`` field.** A caller names a target from the
server's catalogue and supplies the horizon; the server decides the column name,
whether it is a regression or a classification, and whether it needs bin edges.
A client that could describe its own label would let a dataset claim to learn
something the server never defined.

**There is no ``rows`` field, and no way to post values.** A dataset is derived
from what is already stored. This endpoint cannot be used to introduce a number
that no observation and no outcome contains, which is the only way to guarantee
that the file it produces is built out of measurements.

**The feature list is checked against the catalogue, not trusted.** An unknown
name is a 422 listing what exists, so a typo cannot silently become a column that
is missing from every row.

The read schema embeds the report's own models rather than mirroring them. The
report is produced by this module and never accepted from a client, so a second
copy of its shape in the wire layer would be a second thing to keep in step with
no gain: the caller can trust that what it reads is what was stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.base import normalize_pan_id
from app.models.dataset import (
    CategoricalDistribution,
    ClassDistribution,
    ColumnDistribution,
    DatasetReport,
    DataSourceSummary,
    DuplicateSummary,
    ExcludedFieldRecord,
    FeatureDefinitionRecord,
    LeakageAudit,
    MissingValueSummary,
    NumericDistribution,
    ResearchDataset,
)
from app.models.enums import LeakageClass, ResearchTarget, TargetKind
from app.schemas.common import FiniteFloat
from app.services.dataset_catalog import (
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_MAX_SOURCE_RECORDS,
    MIN_HORIZON_HOURS,
)

#: Upper bound on a single build request's source read. Present as a ceiling on
#: the request as well as in the builder, so an enormous cap has to be a
#: deliberate act rather than a typo in a large number.
MAX_SOURCE_RECORDS_CEILING = 200_000

#: How many rows the build response previews by default. Enough to see whether
#: the join did what was intended, few enough to stay readable in a browser.
DEFAULT_PREVIEW_ROWS = 10


class DatasetTargetRequest(BaseModel):
    """Which quantity to learn, and how far ahead it must be measured.

    Everything else about the target is server-owned. Naming the target is the
    researcher's decision and is required; the column name, the kind and the
    requirement for bin edges come from the catalogue so that two callers asking
    for the same target cannot end up with differently shaped labels.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: ResearchTarget = Field(
        description=(
            "Which quantity to learn. There is no default: a dataset whose label "
            "was chosen for the researcher is a dataset that will report whatever "
            "the researcher hoped for."
        ),
        examples=["post_rain_salinity_g_l"],
    )
    horizon_hours: float = Field(
        gt=MIN_HORIZON_HOURS - 1e-9,
        le=24 * 30,
        allow_inf_nan=False,
        description=(
            "How far ahead of the anchor a measurement must have been taken to "
            "count as the outcome. Must be at least "
            f"{MIN_HORIZON_HOURS} hour, so the measurement can never be the "
            "reading it is being asked to predict."
        ),
        examples=[24.0],
    )
    require_rain_event: bool = Field(
        default=False,
        description=(
            "Keep only rows whose horizon window contains recorded observed "
            "rainfall. Off by default: it is a real restriction on the data, and "
            "applying it silently would change what the dataset is."
        ),
    )
    bin_edges: list[FiniteFloat] | None = Field(
        default=None,
        max_length=32,
        description=(
            "Ascending class boundaries, required for a banded target and refused "
            "for the others. The module will not choose them: a class distribution "
            "is then a fact about that choice rather than about the pans."
        ),
        examples=[[200.0, 260.0, 300.0]],
    )
    risk_definition_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description=(
            "The stored risk definition whose rule will derive these labels. "
            "Required for the operational_risk target, because the module supplies "
            "no severity vocabulary and no threshold of its own; refused for every "
            "other target, whose labels are measured rather than derived."
        ),
        examples=["risk-4f2a91c0b7"],
    )
    allow_retrospective_label: bool = Field(
        default=False,
        description=(
            "Acknowledge that the named rule reads a measurement taken after the "
            "anchor, so its labels describe what happened rather than what was "
            "knowable at the time. Required to build against such a rule and "
            "refused for a prospective one, where it would imply a qualification "
            "that does not apply. A dataset built this way is labelled "
            "retrospective throughout and cannot be used to forecast the event it is "
            "graded against."
        ),
    )

    @model_validator(mode="after")
    def _edges_must_ascend(self) -> DatasetTargetRequest:
        if self.bin_edges is not None and any(
            upper <= lower for lower, upper in zip(self.bin_edges, self.bin_edges[1:])
        ):
            raise ValueError("bin_edges must be strictly ascending")
        return self


class DatasetBuildRequest(BaseModel):
    """One request to build a dataset from stored observations and outcomes."""

    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, validate_assignment=True
    )

    name: str | None = Field(
        default=None,
        max_length=120,
        description=(
            "A label for this dataset, for a human reader. Never part of the "
            "content hash, so renaming a dataset does not make it a new one."
        ),
        examples=["post-rain salinity, 24 h horizon, dry season"],
    )
    target: DatasetTargetRequest = Field(
        description="The label to learn. Required; there is no default target."
    )
    features: list[str] | None = Field(
        default=None,
        max_length=64,
        description=(
            "Feature names from the catalogue, in the order they should appear. "
            "Omit for the whole catalogue. An empty list is refused: a dataset "
            "with no features cannot say anything."
        ),
        examples=[["current_salinity_g_l", "water_depth_cm", "forecast_rainfall_mm"]],
    )
    lookback_hours: float = Field(
        default=DEFAULT_LOOKBACK_HOURS,
        ge=0.0,
        le=24 * 30,
        allow_inf_nan=False,
        description=(
            "How far back the rainfall-window columns reach from each anchor. "
            "Widened when the records are read, so a row at the edge of the window "
            "still has its history."
        ),
    )
    pan_ids: list[str] | None = Field(
        default=None,
        max_length=100,
        description="Restrict to these pans. Omit for every registered pan.",
    )
    start: datetime | None = Field(
        default=None,
        description="Earliest anchor instant to build a row for (ISO 8601).",
    )
    end: datetime | None = Field(
        default=None, description="Latest anchor instant to build a row for (ISO 8601)."
    )
    include_unlabelled: bool = Field(
        default=True,
        description=(
            "Keep rows whose target was never measured. On by default so the size "
            "of the measurement backlog is part of the dataset rather than an "
            "invisible filter; turn it off to get a training file."
        ),
    )
    max_source_records: int = Field(
        default=DEFAULT_MAX_SOURCE_RECORDS,
        ge=1,
        le=MAX_SOURCE_RECORDS_CEILING,
        description=(
            "Ceiling on records read per collection. Reaching it is reported in "
            "the dataset, because a bounded sample must be described as one."
        ),
    )
    preview_rows: int = Field(
        default=DEFAULT_PREVIEW_ROWS,
        ge=0,
        le=200,
        description="How many built rows to return alongside the manifest.",
    )

    @model_validator(mode="after")
    def _window_is_ordered(self) -> DatasetBuildRequest:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must not be later than end")
        return self

    @model_validator(mode="after")
    def _normalize_pans(self) -> DatasetBuildRequest:
        if self.pan_ids is not None:
            self.pan_ids = [normalize_pan_id(pan_id) for pan_id in self.pan_ids]
        return self


class DatasetFeatureRead(FeatureDefinitionRecord):
    """One feature, as documented in the catalogue."""


class DatasetExcludedFieldRead(ExcludedFieldRecord):
    """One source field deliberately not built as a feature, and why."""


class DatasetRebuildStatus(BaseModel):
    """What a rebuild found.

    ``unchanged`` is the reproducibility claim made concrete: the same
    specification over the same stored records produced the same digest, so the
    dataset the caller already has is still the dataset the records describe.
    """

    model_config = ConfigDict(extra="forbid")

    unchanged: bool = Field(
        description="Whether the rebuild produced the same content hash."
    )
    reason: str = Field(min_length=1, max_length=400)
    previous_content_hash: str | None = None
    previous_dataset_id: str | None = None


class ResearchDatasetRead(BaseModel):
    """A stored dataset manifest, with its report."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    dataset_id: str
    name: str | None = None
    target: dict[str, Any]
    content_hash: str
    short_hash: str
    builder_schema_version: int
    version: int
    feature_names: list[str]
    features: list[DatasetFeatureRead]
    row_keys: list[str]
    excluded_fields: list[DatasetExcludedFieldRead]
    lookback_hours: float
    include_unlabelled: bool
    max_source_records: int
    pan_filter: list[str]
    window: dict[str, Any]
    report: DatasetReport
    supersedes_content_hash: str | None = None
    created_at: datetime

    @classmethod
    def from_dataset(cls, dataset: ResearchDataset) -> ResearchDatasetRead:
        return cls(
            dataset_id=dataset.dataset_id,
            name=dataset.name,
            target=dataset.target.model_dump(mode="json"),
            content_hash=dataset.content_hash,
            short_hash=dataset.short_hash,
            builder_schema_version=dataset.builder_schema_version,
            version=dataset.version,
            feature_names=list(dataset.feature_names),
            features=[DatasetFeatureRead(**feature.model_dump()) for feature in dataset.features],
            row_keys=list(dataset.row_keys),
            excluded_fields=[
                DatasetExcludedFieldRead(**entry.model_dump())
                for entry in dataset.excluded_fields
            ],
            lookback_hours=dataset.lookback_hours,
            include_unlabelled=dataset.include_unlabelled,
            max_source_records=dataset.max_source_records,
            pan_filter=list(dataset.pan_filter),
            window=dataset.window,
            report=dataset.report,
            supersedes_content_hash=dataset.supersedes_content_hash,
            created_at=dataset.created_at,
        )


class DatasetBuildResponse(BaseModel):
    """A build's result: the manifest, whether it was new, and a preview."""

    model_config = ConfigDict(extra="forbid")

    dataset: ResearchDatasetRead
    created: bool = Field(
        description=(
            "False when an identical dataset already existed and was returned "
            "instead of written. Same specification over the same records is the "
            "same dataset, not a second copy of it."
        )
    )
    reused_reason: str | None = Field(
        default=None,
        max_length=400,
        description="Why an existing dataset was returned, when one was.",
    )
    preview_columns: list[str] = Field(default_factory=list)
    preview_rows: list[dict[str, Any]] = Field(default_factory=list)


class DatasetRebuildResponse(DatasetBuildResponse):
    """A rebuild's result, with the reproducibility verdict attached."""

    rebuild: DatasetRebuildStatus


class DatasetPreviewResponse(BaseModel):
    """Rows of a dataset as they would be written to a file."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    content_hash: str
    short_hash: str
    columns: list[str]
    rows: list[dict[str, Any]]
    returned: int
    total_rows: int = Field(
        description=(
            "How many rows the dataset holds, which is usually more than "
            "``returned``. The gap is stated rather than left for the caller to "
            "notice."
        )
    )
    drift: bool = Field(
        description=(
            "True when the rows rebuilt now no longer match the hash stored with "
            "the dataset, because a reading or an outcome has been recorded or "
            "corrected since."
        )
    )
    note: str | None = Field(default=None, max_length=400)


class DatasetListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ResearchDatasetRead]
    total: int
    limit: int
    offset: int


class FeatureCatalogEntry(BaseModel):
    """One feature in the catalogue, as the API presents it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    leakage_class: LeakageClass
    unit: str | None = None
    source_collection: str
    source_field: str
    selection_rule: str
    derivation: str
    scientific_basis: str
    nullable_reason: str | None = None


class TargetCatalogEntry(BaseModel):
    """One target in the catalogue, as the API presents it."""

    model_config = ConfigDict(extra="forbid")

    name: ResearchTarget
    kind: TargetKind
    label_column: str
    unit: str | None = None
    source_collection: str
    source_field: str
    description: str
    measurement_rule: str
    requires_bin_edges: bool
    classes: list[str] | None = None


class DatasetCatalogResponse(BaseModel):
    """Every column and every label the module can build, and what it will not.

    Served without touching the database. It is the documentation of the feature
    set, and a researcher should be able to read it before there is any data to
    read.
    """

    model_config = ConfigDict(extra="forbid")

    builder_schema_version: int
    leakage_policy: str
    leakage_checks: list[str]
    row_keys: list[dict[str, str]]
    features: list[FeatureCatalogEntry]
    targets: list[TargetCatalogEntry]
    excluded_fields: list[DatasetExcludedFieldRead]
    defaults: dict[str, Any] = Field(
        default_factory=dict,
        description="Default lookback, read cap and minimum horizon, for the request form.",
    )


__all__ = [
    "DEFAULT_PREVIEW_ROWS",
    "MAX_SOURCE_RECORDS_CEILING",
    "CategoricalDistribution",
    "ClassDistribution",
    "ColumnDistribution",
    "DataSourceSummary",
    "DatasetBuildRequest",
    "DatasetBuildResponse",
    "DatasetCatalogResponse",
    "DatasetExcludedFieldRead",
    "DatasetFeatureRead",
    "DatasetListResponse",
    "DatasetPreviewResponse",
    "DatasetRebuildResponse",
    "DatasetRebuildStatus",
    "DatasetReport",
    "DatasetTargetRequest",
    "DuplicateSummary",
    "FeatureCatalogEntry",
    "LeakageAudit",
    "MissingValueSummary",
    "NumericDistribution",
    "ResearchDatasetRead",
    "TargetCatalogEntry",
]
