"""Module 19 - the append-only ledger of completed research experiments.

An experiment is a record of a finished run: the dataset it was built from
(by Module 07 identity, name and version), the observations and pans it covered,
the date range of the data, the feature set, the target definition, the
train/test strategy, the random seed, the model, its hyperparameters, the
metrics the run produced, and the software versions that produced them. Storing
all of it beside the result is what lets another researcher re-derive the
result rather than take it on trust.

Three properties are deliberate.

**The ledger is append-only, by construction.** An experiment is a *claim to
have measured something*; it cannot be updated in place without erasing the
claim that was made, so there is no update and no delete. Changing how a run
was made means recording a new experiment. Nothing is overwritten, and past
results stay comparable to whatever is recorded next.

**Two identical submissions are one experiment.** The ``content_hash`` is
SHA-256 over a canonical encoding of everything a researcher submitted, in the
same encoding the dataset builder uses, and it is unique across the collection.
Re-posting the identical record returns the stored document with
``created=false`` rather than writing a second one, because an experiment that
describes the same data, the same configuration, the same seed and the same
result twice is one experiment, and a ledger that cannot say ``created=false``
would silently grow duplicates that looked like independent reproductions. The
unique index is the backstop; the service check is the ordinary path.

**The identity excludes only what the service assigned.** ``experiment_id``,
``timestamp`` and ``content_hash`` are the service's record-keeping, so they are
left out of the hash payload. Everything submitted is in it, including ``notes``
and the recorded metrics: two runs that differ in any recorded respect are two
experiments.

The reproducibility manifest is not a second document. It is this record
serialised through :class:`ExperimentManifest`, with the schema version and the
stored hash attached, so a manifest read in ten years still knows which
canonical encoding it was built from. Nothing in the manifest is computed from
anything other than the stored record, and the record is immutable, so there is
nothing for the manifest and the ledger to disagree about.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import ConfigDict, Field, StringConstraints, field_validator

from app.models.base import BaseDocument, UtcDateTime, new_id, utcnow
from app.services.dataset_versioning import HASH_PATTERN

#: The reproducibility-manifest schema, unchanged for the lifetime of the module
#: so a reader can tell a manifest version from the bytes instead of from the
#: collection that happens to hold it.
EXPERIMENT_MANIFEST_VERSION = "1.0"

#: The document id prefix, so experiment identifiers are instantly
#: distinguishable from dataset, model and row identifiers.
EXPERIMENT_ID_PREFIX = "exp"

FeatureName = Annotated[
    str,
    StringConstraints(min_length=1, max_length=100, strip_whitespace=True),
]

#: What a hyperparameter may be. Models configure themselves with booleans,
#: integers, floats, short strings and explicit nulls; anything wider is not a
#: hyperparameter, it is an artefact, and has no business in an identity that
#: other researchers recompute.
HyperparameterValue = int | float | str | bool | None


class ResearchExperiment(BaseDocument):
    """One recorded experiment, immutable once written."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    experiment_id: str = Field(
        pattern=r"^[A-Za-z0-9._-]+$",
        description=(
            "The id the service assigned. A URL-safe natural key, distinct from "
            "the content hash because the hash is about the content and this is "
            "about the record."
        ),
    )
    dataset_version: int = Field(
        ge=1,
        description="The Module 07 version of the dataset the run was built on.",
    )
    dataset_hash: str = Field(
        pattern=HASH_PATTERN,
        description=(
            "The Module 07 content hash of the dataset the run was built on, so a "
            "result names its data by the same identity the dataset builder "
            "computed."
        ),
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
        description=(
            "The feature set, in the order the model consumed it. Order is part "
            "of the identity because a model trained on a reordered feature "
            "matrix is not obviously the same model."
        ),
    )
    target: str = Field(
        min_length=1,
        max_length=120,
        description="The dataset target the run predicted.",
    )
    target_definition_version: int = Field(
        ge=1,
        description=(
            "The version of the target definition, so the label a model was "
            "fitted to can be cited rather than assumed."
        ),
    )
    train_test_strategy: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "How the split was made, in words (\"80/20 chronological holdout\", "
            "\"10-fold shuffled\", \"leave-one-pan-out\"). A strategy is a "
            "description because two researchers rarely mean the same thing by "
            "the same token; the words are the reproducibility."
        ),
    )
    random_seed: int = Field(
        ge=0,
        description="The seed that pinned every shuffle and initialisation.",
    )
    model: str = Field(
        min_length=1,
        max_length=120,
        description="The model type as the researcher names it (\"random-forest\", ...).",
    )
    hyperparameters: dict[str, HyperparameterValue] = Field(
        min_length=1,
        max_length=64,
        description=(
            "Every hyperparameter that did not come from a default, by name. An "
            "identity that named only the model would make two runs that differ "
            "only in configuration look the same."
        ),
    )
    metrics: dict[str, float] = Field(
        min_length=1,
        max_length=64,
        description=(
            "The recorded metrics the run produced, by name. Values are floats so "
            "a manifest column is always a number and never a readback artifact."
        ),
    )
    software_versions: dict[str, str] = Field(
        min_length=1,
        max_length=64,
        description=(
            "Software and library versions that produced the result, by name "
            "(\"python\", \"sklearn\", \"pandas\"). The environment is part of the "
            "reproducibility claim."
        ),
    )
    notes: str | None = Field(
        default=None,
        max_length=2000,
        description="Free text on the run; part of the identity like everything else.",
    )
    content_hash: str = Field(
        pattern=HASH_PATTERN,
        description=(
            "SHA-256 over the canonical encoding of everything this record "
            "submitted, computed by the service. The ledger keeps one experiment "
            "per unique hash."
        ),
    )
    timestamp: UtcDateTime = Field(
        default_factory=utcnow,
        description="When the experiment was recorded, in UTC.",
    )
    manifest_version: str = Field(
        default=EXPERIMENT_MANIFEST_VERSION,
        description="The reproducibility-manifest schema this record serialises to.",
    )

    @field_validator("features", "target", "model", "train_test_strategy", mode="before")
    @classmethod
    def _normalise_identifiers(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return [str(item).strip() for item in value]
        return value

    @property
    def short_hash(self) -> str:
        """The first 12 hex characters of the content hash, for display."""
        return self.content_hash.split(":", 1)[-1][:12]


def build_experiment_id() -> str:
    """A fresh natural key for an experiment record."""
    return new_id(EXPERIMENT_ID_PREFIX)