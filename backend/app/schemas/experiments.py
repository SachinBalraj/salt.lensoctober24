"""Module 19 - HTTP contracts for the research experiment ledger.

The create request carries everything a finished run *is*: the dataset by Module
07 identity, the observations and pans it spanned, the date range of the data,
the feature set, the target and its definition version, the train/test strategy,
the seed, the model, its hyperparameters, the metrics and the software versions.
None of the derived fields — ``experiment_id``, ``content_hash``, ``timestamp``,
``manifest_version`` — can be supplied, because each is the service's accounting
and a client that guessed them would be recording an identity that does not
describe the record.
"""

from __future__ import annotations

from datetime import date, datetime
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.experiment import FeatureName, HyperparameterValue
from app.schemas.common import FiniteFloat
from app.services.dataset_versioning import HASH_PATTERN

MANIFEST_HASH_ALGORITHM = "sha256"
MANIFEST_CANONICAL_FORM = "sorted-keys-stable-json"


class ExperimentCreate(BaseModel):
    """Everything a researcher records about one completed run."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    dataset_version: int = Field(
        ge=1,
        description="The Module 07 version of the dataset the run was built on.",
    )
    dataset_hash: str = Field(
        pattern=HASH_PATTERN,
        description="The Module 07 content hash of the dataset the run was built on.",
    )
    n_observations: int = Field(
        ge=0,
        description="Rows in the dataset this run actually used.",
    )
    n_pans: int = Field(
        ge=0,
        description="Distinct pans the dataset spans.",
    )
    date_start: date | None = Field(
        default=None,
        description="First day covered by the data, inclusive.",
    )
    date_end: date | None = Field(
        default=None,
        description="Last day covered by the data, inclusive.",
    )
    features: list[FeatureName] = Field(
        min_length=1,
        max_length=128,
        description="The feature set, in the order the model consumed it.",
    )
    target: str = Field(
        min_length=1,
        max_length=120,
        description="The dataset target the run predicted.",
    )
    target_definition_version: int = Field(
        ge=1,
        description="The version of the target definition the label was built from.",
    )
    train_test_strategy: str = Field(
        min_length=1,
        max_length=200,
        description='How the split was made, in words ("80/20 chronological holdout").',
    )
    random_seed: int = Field(
        ge=0,
        description="The seed that pinned every shuffle and initialisation.",
    )
    model: str = Field(
        min_length=1,
        max_length=120,
        description="The model type, as the researcher names it.",
    )
    hyperparameters: dict[str, HyperparameterValue] = Field(
        min_length=1,
        max_length=64,
        description="Every hyperparameter that did not come from a default, by name.",
    )
    metrics: dict[str, FiniteFloat] = Field(
        min_length=1,
        max_length=64,
        description="The recorded metrics the run produced, by name.",
    )
    software_versions: dict[str, str] = Field(
        min_length=1,
        max_length=64,
        description="Software and library versions that produced the result, by name.",
    )
    notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Free text on the run; part of the identity like everything else.",
    )

    @model_validator(mode="after")
    def _date_range_reads_forwards(self) -> ExperimentCreate:
        if (
            self.date_start is not None
            and self.date_end is not None
            and self.date_start > self.date_end
        ):
            raise ValueError("date_start must not be later than date_end.")
        return self

    @field_validator("hyperparameters")
    @classmethod
    def _finite_hyperparameter_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        for name, number in value.items():
            if isinstance(number, float) and not isfinite(number):
                raise ValueError(
                    f"hyperparameter {name!r} must be a finite number."
                )
        return value


class ExperimentRecordContent(BaseModel):
    """The submitted content of one experiment - the exact hashed payload.

    What a reproducibility manifest carries in ``record``. Deliberately the
    create shape minus nothing and the read shape minus its derived fields: the
    manifest's integrity claim is that re-hashing this object reproduces
    ``content_hash``, which holds only if the two are measured on the same
    bytes.
    """

    model_config = ConfigDict(extra="forbid")

    dataset_version: int
    dataset_hash: str
    n_observations: int
    n_pans: int
    date_start: date | None
    date_end: date | None
    features: list[str]
    target: str
    target_definition_version: int
    train_test_strategy: str
    random_seed: int
    model: str
    hyperparameters: dict[str, Any]
    metrics: dict[str, float]
    software_versions: dict[str, str]
    notes: str | None

    @classmethod
    def from_experiment(cls, experiment: object) -> ExperimentRecordContent:
        return cls(
            dataset_version=experiment.dataset_version,
            dataset_hash=experiment.dataset_hash,
            n_observations=experiment.n_observations,
            n_pans=experiment.n_pans,
            date_start=experiment.date_start,
            date_end=experiment.date_end,
            features=list(experiment.features),
            target=experiment.target,
            target_definition_version=experiment.target_definition_version,
            train_test_strategy=experiment.train_test_strategy,
            random_seed=experiment.random_seed,
            model=experiment.model,
            hyperparameters=dict(experiment.hyperparameters),
            metrics=dict(experiment.metrics),
            software_versions=dict(experiment.software_versions),
            notes=experiment.notes,
        )


class ExperimentRead(BaseModel):
    """One stored experiment, exactly as it will be reproduced.

    A separate model from the document so the HTTP shape stays stable while the
    stored representation carries the Mongo ``_id`` a reader has no use for.
    """

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    dataset_version: int
    dataset_hash: str
    n_observations: int
    n_pans: int
    date_start: date | None
    date_end: date | None
    features: list[str]
    target: str
    target_definition_version: int
    train_test_strategy: str
    random_seed: int
    model: str
    hyperparameters: dict[str, Any]
    metrics: dict[str, float]
    software_versions: dict[str, str]
    notes: str | None
    content_hash: str
    short_hash: str
    timestamp: datetime
    manifest_version: str

    @classmethod
    def from_experiment(cls, experiment: object) -> ExperimentRead:
        return cls(
            experiment_id=experiment.experiment_id,
            dataset_version=experiment.dataset_version,
            dataset_hash=experiment.dataset_hash,
            n_observations=experiment.n_observations,
            n_pans=experiment.n_pans,
            date_start=experiment.date_start,
            date_end=experiment.date_end,
            features=list(experiment.features),
            target=experiment.target,
            target_definition_version=experiment.target_definition_version,
            train_test_strategy=experiment.train_test_strategy,
            random_seed=experiment.random_seed,
            model=experiment.model,
            hyperparameters=dict(experiment.hyperparameters),
            metrics=dict(experiment.metrics),
            software_versions=dict(experiment.software_versions),
            notes=experiment.notes,
            content_hash=experiment.content_hash,
            short_hash=experiment.short_hash,
            timestamp=experiment.timestamp,
            manifest_version=experiment.manifest_version,
        )


class ExperimentCreateResponse(BaseModel):
    """What a write to the ledger did.

    ``created`` is the field that matters. Re-posting an identical experiment has
    done nothing, and reporting it as a new record would put an entry in the
    ledger for a reproduction that never happened separately.
    """

    model_config = ConfigDict(extra="forbid")

    created: bool = Field(
        description=(
            "True when a new experiment was recorded. False when the identical "
            "record already existed, in which case the stored document is "
            "returned unchanged."
        )
    )
    reason: str | None = Field(
        default=None,
        description="Why the record was not written anew, when it was not.",
    )
    experiment: ExperimentRead


class ExperimentListResponse(BaseModel):
    """A page of the experiment ledger, newest first."""

    model_config = ConfigDict(extra="forbid")

    items: list[ExperimentRead] = Field(default_factory=list)
    total: int = Field(ge=0, description="Experiments matching the filter, ignoring paging.")
    limit: int = Field(ge=1, le=1000)
    offset: int = Field(ge=0)
    earliest_timestamp: datetime | None = None
    latest_timestamp: datetime | None = None


class ExperimentManifest(BaseModel):
    """The reproducibility manifest for one experiment.

    A self-contained document: the experiment_id and timestamp of the record,
    the stored content hash, the canonical encoding that hash was computed over,
    and ``record`` - the submitted content of the experiment, byte for byte the
    payload the hash was computed from. Another researcher verifies the ledger
    by re-hashing ``record`` with the stated canonical form and comparing the
    result to ``content_hash``; the derived fields of the read shape are not in
    ``record`` precisely because they are not in the hash.
    """

    model_config = ConfigDict(extra="forbid")

    manifest_version: str = Field(
        description="Which manifest schema produced these bytes.",
    )
    experiment_id: str
    timestamp: datetime
    content_hash: str = Field(
        pattern=HASH_PATTERN,
        description="The stored identity hash, recomputable from ``record``.",
    )
    hash_algorithm: str = Field(
        default=MANIFEST_HASH_ALGORITHM,
        description="The algorithm the identity hash was computed with.",
    )
    canonical_form: str = Field(
        default=MANIFEST_CANONICAL_FORM,
        description="The canonical encoding the identity hash is computed over.",
    )
    record: ExperimentRecordContent = Field(
        description="The submitted experiment, byte for byte the hashed payload.",
    )

    @classmethod
    def from_experiment(cls, experiment: object) -> ExperimentManifest:
        return cls(
            manifest_version=experiment.manifest_version,
            experiment_id=experiment.experiment_id,
            timestamp=experiment.timestamp,
            content_hash=experiment.content_hash,
            record=ExperimentRecordContent.from_experiment(experiment),
        )


def ledger_csv_columns() -> list[str]:
    """The flat column layout of a whole-ledger CSV export.

    Nested facts (features, hyperparameters, metrics, software versions) are
    encoded per row as JSON strings, so a widely shaped ledger stays rectangular
    and any reader can parse the nested fields back out.
    """
    return [
        "experiment_id",
        "timestamp",
        "dataset_version",
        "dataset_hash",
        "n_observations",
        "n_pans",
        "date_start",
        "date_end",
        "features",
        "target",
        "target_definition_version",
        "train_test_strategy",
        "random_seed",
        "model",
        "hyperparameters",
        "metrics",
        "software_versions",
        "notes",
        "content_hash",
    ]


def metric_table_columns() -> list[str]:
    """The columns of a per-experiment CSV: one metric per row."""
    return ["metric", "value"]