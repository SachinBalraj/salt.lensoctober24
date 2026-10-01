"""Module 09 - the stored result of evaluating one registered model version.

This document is the module's whole reason for existing. An evaluation that lives
only in a response is a number somebody saw once; an evaluation stored against the
``ModelVersion`` it grades is a claim a paper can cite and a reviewer can check.

Four properties are load-bearing, and each of them exists because of a specific way
a stored performance figure goes wrong.

**It is stored against the model version, not floating.** ``model_id`` and
``version`` are on the document, and ``(model_id, version)`` is unique. A figure
that cannot be traced to the exact artifact, the exact dataset revision and the
exact seed is not a result, it is an anecdote with a decimal point.

**The provenance travels with it.** The dataset id, version *and* content hash; the
split strategy with its full detail; the random seed; the library versions; the
feature list; the artifact path; the row count each figure was computed on. All of
it was recorded by Module 08 and is copied here rather than re-derived, so a
disagreement between the registry and the evaluation is impossible by
construction. An evaluation that quoted a different seed than the model it grades
would be the most precise form of a wrong number, because it would be right about
everything except the thing being asked.

**Nothing is filled in.** Every metric is a :class:`MetricReading`, so an
uncomputable one is stored as absent with its reason rather than as a zero. The
evaluator in :mod:`app.ml.evaluation` is what makes that true, and this document
inherits the property by carrying the type rather than a float.

**Re-evaluating a version does not create a second record.** One evaluation per
model version, because the run is deterministic given the seed: a second
evaluation of the same artifact over the same rows produces the same numbers, and
a second document claiming to be a second measurement of the same held-out rows
would overstate how much evidence there is. It is a revision of nothing.

The ``evaluation_id`` is derived from the model id rather than generated, so
constructing this document twice for the same version produces the same identity
and the collision is visible in a test rather than in production.
"""

from __future__ import annotations

import hashlib
from typing import Any, Self

from pydantic import Field, model_validator

from app.models.base import BaseDocument, UtcDateTime, utcnow
from app.models.enums import ResearchTarget
from app.models.evaluation import (
    ClassificationEvaluation,
    CrossValidationReport,
)


def evaluation_id_for(model_id: str, version: str) -> str:
    """The identifier of the evaluation of one model version.

    Derived, not random. A generated id would let two documents both claim to be
    the evaluation of the same version while the unique index on
    ``(model_id, version)`` turns the second write into a 409 the caller cannot
    interpret. Deriving it means the id itself carries the claim, and a duplicate
    is a duplicate under the key a reader would look it up by.
    """
    digest = hashlib.sha256(f"{model_id}@{version}".encode()).hexdigest()
    return f"eval-{digest[:20]}"


class ModelEvaluation(BaseDocument):
    """Every figure computed about one model version, with the rows behind them."""

    evaluation_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description=(
            "Derived from model_id and version by :func:`evaluation_id_for`, never "
            "supplied by a caller. Ignored on input and rewritten below, so a "
            "document constructed with the wrong id cannot be stored under it."
        ),
    )
    model_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64, pattern=r"^v?\d+\.\d+\.\d+$")
    target: ResearchTarget

    #: The model the registry says is registered under this id and version. Null
    #: only for a model that was never trained, which cannot happen through this
    #: module; the field exists so a reader of a stored document is never left
    #: guessing which algorithm the headline figures describe.
    algorithm: str | None = Field(default=None, max_length=128)

    #: The one evaluation of the registered artifact on the recorded held-out rows.
    #: Every other candidate's figures sit in ``candidate_evaluations``.
    evaluation: ClassificationEvaluation

    #: One entry per trained classifier, keyed by algorithm, in candidate order.
    #: The selected model appears here too, so a table can be built from this map
    #: alone and never has to special-case the selected row.
    candidate_evaluations: dict[str, ClassificationEvaluation] = Field(
        default_factory=dict,
    )

    cross_validation: CrossValidationReport

    #: The comparison table, stored rather than derived on read. It is a function
    #: of the two maps above, so storing it is redundant in the strict sense; it
    #: is stored because it is the thing a citation quotes, and a table that is
    #: recomputed by each reader can disagree between readers if any of the
    #: presentation rules below ever change.
    comparison: dict[str, Any] = Field(default_factory=dict)

    #: -- provenance, copied from the ModelVersion ------------------------
    dataset_id: str | None = Field(default=None, max_length=64)
    dataset_version: int | None = Field(default=None, ge=1)
    dataset_content_hash: str | None = Field(default=None, max_length=80)
    artifact_path: str | None = Field(default=None, max_length=512)
    feature_names: list[str] = Field(default_factory=list)
    split_strategy: str | None = Field(default=None, max_length=64)
    split_detail: dict[str, Any] = Field(default_factory=dict)
    random_seed: int | None = Field(default=None, ge=0)
    library_versions: dict[str, str] = Field(default_factory=dict)

    #: Stated once on the record rather than repeated per candidate, because all
    #: of them were computed on the same rows and a per-candidate denominator that
    #: differed would itself be the finding.
    evaluation_set: str = Field(
        max_length=200,
        description="What the rows are, in words. Never blank.",
    )
    evaluated_rows: int = Field(ge=0)
    trained_at: UtcDateTime | None = None
    evaluated_at: UtcDateTime = Field(default_factory=utcnow)

    #: Conventions and caveats that qualify the whole report, concatenated from
    #: each candidate's notes. Kept as a distinct field rather than a property so
    #: that a reader who never opens a candidate can still find them.
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _derive_identity(cls, value: Any) -> Any:
        """Compute the evaluation id from the model it grades.

        Done on the way in rather than on the way out, because ``BaseDocument``
        runs assignment validation and an after-validator that *wrote* the field
        would re-enter itself on every write to the field. Injecting here also
        means a document read back from Mongo has its id checked against the
        ``(model_id, version)`` it claims: a hand-edited document is corrected on
        read rather than trusted.
        """
        if not isinstance(value, dict):
            return value
        values = dict(value)
        model_id = values.get("model_id")
        version = values.get("version")
        if isinstance(model_id, str) and isinstance(version, str):
            values["evaluation_id"] = evaluation_id_for(model_id, version)
        return values

    @model_validator(mode="after")
    def _check_denominators(self) -> Self:
        """The record's own headline must match the evaluation it qualifies.

        ``evaluation_set`` and ``evaluated_rows`` are repeated here because they
        are the two things a citation quotes, and a stored document that
        contradicted the evaluation inside it would be quoting a row count the
        figures were not computed on. Silent in every consumer, so refused here.
        """
        if self.evaluation.evaluation_set != self.evaluation_set:
            raise ValueError(
                f"This record says its figures describe {self.evaluation_set!r} while "
                f"the evaluation itself says {self.evaluation.evaluation_set!r}. A "
                "row count and a row description that disagree with the evaluation "
                "they qualify cannot be cited together."
            )
        if self.evaluation.evaluated_rows != self.evaluated_rows:
            raise ValueError(
                f"This record reports {self.evaluated_rows} evaluated row(s) while "
                f"its evaluation was computed on {self.evaluation.evaluated_rows}. "
                "The headline denominator is the one most likely to be quoted, so it "
                "is not allowed to be a number a reader has to reconcile."
            )
        return self

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def experiment_record(self) -> dict[str, Any]:
        """The machine-readable experiment record written by the export endpoint.

        Not the whole document. The export is for reproduction, and reproduction
        needs the provenance, the numbers and the caveats; it does not need the
        confusion matrix of all four candidates twice over. The full report is
        always available from the API, so nothing here is the only copy.
        """
        return {
            "evaluation_id": self.evaluation_id,
            "model_id": self.model_id,
            "version": self.version,
            "target": self.target.value,
            "algorithm": self.algorithm,
            "evaluated_at": self.evaluated_at,
            "trained_at": self.trained_at,
            "evaluation_set": self.evaluation_set,
            "evaluated_rows": self.evaluated_rows,
            "provenance": {
                "dataset_id": self.dataset_id,
                "dataset_version": self.dataset_version,
                "dataset_content_hash": self.dataset_content_hash,
                "artifact_path": self.artifact_path,
                "feature_names": list(self.feature_names),
                "split_strategy": self.split_strategy,
                "split_detail": dict(self.split_detail),
                "random_seed": self.random_seed,
                "library_versions": dict(self.library_versions),
            },
            "comparison": dict(self.comparison),
            "selected_model": self.evaluation.as_dict(),
            "candidates": {
                algorithm: evaluation.as_dict()
                for algorithm, evaluation in self.candidate_evaluations.items()
            },
            "cross_validation": self.cross_validation.as_dict(),
            "notes": list(self.notes),
        }


# Imported late so the module docstring above stays the first statement of the
# file; ruff's import-order rules and the reader's attention disagree about which
# matters more, and the second one wins here.
__all__ = ["ModelEvaluation", "evaluation_id_for"]
