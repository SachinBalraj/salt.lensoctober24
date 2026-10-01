"""Module 10 - storing a researcher's risk rule, and keeping its history honest.

This service is the only thing in the project that writes a
:class:`~app.models.risk_definition.RiskDefinition`. Everything else reads one.

**Writing a definition is versioned and additive.** There is no update and no
delete on this service, deliberately. A rule that can be edited in place makes
every model already fitted to it undescribable: the thresholds behind version 2
would be gone, and "the model with 0.83 accuracy" would refer to numbers nobody
could reconstruct. So a change is a new version, ``supersedes_definition_id``
points at its predecessor, and the old document stays exactly as it was written.

**Version numbers are per name, and allocated from the stored history.** A rule
called "monsoon dilution" and a rule called "post-rain loss" are two rules with
independent histories, each numbered from 1. The number is read from the highest
existing version for that name rather than incremented optimistically, so two
concurrent writes cannot both claim to be version 3.

**A rule with the same content is not a new version.** The specification hash
covers exactly what the rule *says* — its classes, criteria, combinator, fallback,
label basis and rationale — and deliberately excludes the name, the version, the
author and the timestamp. Posting an unchanged rule therefore returns the
existing document instead of writing a second one that changes no label and would
make the history read as though the rule had been revised twice. The response
carries ``created: false`` so a caller can tell the difference between writing
something and re-posting it.

**Nothing is inferred on the researcher's behalf.** The service does not supply a
label vocabulary, does not choose a threshold, and does not fill in a rationale.
It does compute two things, because both are derivable and their absence would be
an inconsistency: which variables the criteria read (``variables_used``) and
therefore whether the label is prospective or retrospective (``label_basis``).
A definition whose criteria read a post-anchor measurement is filed as
retrospective whether or not the author said so, so a reader never has to
reconstruct the causal argument from a list of variable names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from app.db.collections import RISK_DEFINITIONS
from app.db.errors import DocumentNotFoundError, DuplicateDocumentError
from app.db.repository import MongoRepository
from app.models.risk_definition import (
    CATEGORICAL_OPERATORS,
    RiskDefinition,
    RiskDefinitionDraft,
)
from app.services.dataset_versioning import content_hash, short_hash
from app.services.risk_catalog import (
    RISK_VARIABLE_CATALOG,
    RiskVariableDefinition,
    resolve_risk_variable,
)
from app.services.risk_evaluation import definition_for_variables

#: The value reported when no definition has been written yet. A string rather
#: than a null, because a dataset target has to be able to cite something and
#: "no definition exists" is a more useful citation than a blank field.
NO_DEFINITION_ID = "none"

#: Returned when a rule is re-posted unchanged. Spelled out rather than inferred
#: from a status code so a caller can branch on it without comparing digests.
REUSED_REASON = "identical_definition_already_stored"

#: How many times a write will re-read the rule's history after losing a race for
#: a version number. One retry resolves an ordinary collision between two writers;
#: the bound is here so a pathologically contended name fails loudly instead of
#: spinning.
MAX_VERSION_ATTEMPTS = 5


@dataclass(frozen=True)
class CreateOutcome:
    """What a write to this service actually did.

    ``created`` is the whole point of the pair. A caller that re-posts a rule
    unchanged has done nothing, and reporting that as a fresh version would put a
    phantom entry in the rule's history.
    """

    definition: RiskDefinition
    created: bool
    reason: str | None = None


class RiskDefinitionError(ValueError):
    """A definition that cannot be stored, with the reason stated.

    A ``ValueError`` rather than a bare ``LookupError`` so a router can turn it
    into a 422 naming the offending field, which is the one response a
    researcher can act on without reading this module's source.
    """


class RiskDefinitionService:
    """Store, version and read operational risk definitions."""

    def __init__(self, database: Any) -> None:
        self._database = database

    def _repository(self) -> MongoRepository[RiskDefinition]:
        return MongoRepository(self._database, RISK_DEFINITIONS, RiskDefinition)

    # -- validation ---------------------------------------------------------

    @staticmethod
    def validate_criteria(definition: RiskDefinition) -> None:
        """Check every criterion against the variable catalogue.

        Three things are refused here rather than at evaluation time, because all
        three are mistakes in the *rule* and not in the data:

        * a variable that is not catalogued, so the rule depends on a quantity
          the project cannot measure;
        * a unit that contradicts the catalogue, so a millimetre bound is never
          silently compared against a salinity;
        * an ordering comparison against a categorical variable, which is not a
          threshold but an accident waiting to happen.
        """
        for criterion in definition.criteria:
            variable = resolve_risk_variable(criterion.variable)
            _check_variable_agreement(criterion.variable, criterion.operator, criterion.threshold, variable)
            if criterion.unit is not None and criterion.unit != variable.unit:
                raise RiskDefinitionError(
                    f"the criterion on {criterion.variable!r} declares unit "
                    f"{criterion.unit!r} but that variable is measured in "
                    f"{variable.unit!r}; a bound in one unit compared against a "
                    "value in another produces a class distribution that is a fact "
                    "about the unit mistake rather than about any pan"
                )

    async def _next_version(self, name: str) -> tuple[int, str | None]:
        """The next version number for a rule name, and the id it supersedes.

        Read from the stored history rather than incremented, so a rule's first
        version is 1 rather than whatever a counter happened to hold.

        This read is not atomic with the insert that follows it, and cannot be made
        so through a read and a write. What makes that safe is the unique index on
        ``(name, version)``: two writers that both compute the same number cannot
        both store it, and the loser retries against the version that won. The
        invariant is therefore enforced by the index rather than claimed here.
        """
        repository = self._repository()
        previous = await repository.find(
            {"name": name}, sort=[("version", -1)], limit=1
        )
        if not previous:
            return 1, None
        newest = previous[0]
        return newest.version + 1, newest.definition_id

    # -- writes -------------------------------------------------------------

    async def create(self, draft: RiskDefinitionDraft) -> CreateOutcome:
        """Store a draft, or return the existing definition if it says the same thing.

        The hash is computed here from the stored content rather than taken from
        the caller, so a client cannot present a rule under a hash that does not
        describe it. A client is never asked to supply one.
        """
        proposed = draft.as_definition()
        self.validate_criteria(proposed)

        complete = definition_for_variables(proposed)
        digest = content_hash(complete.as_hashed_specification())
        complete = complete.model_copy(update={"specification_hash": digest})

        repository = self._repository()
        existing = await repository.find({"specification_hash": digest}, limit=1)
        if existing:
            return CreateOutcome(
                definition=existing[0], created=False, reason=REUSED_REASON
            )

        version, supersedes = await self._next_version(complete.name)
        # A collision on (name, version) means a concurrent writer took this number
        # first. Re-read and try the next one; the bound stops a pathological
        # writer from spinning forever against constant contention.
        for _attempt in range(MAX_VERSION_ATTEMPTS):
            stored = complete.model_copy(
                update={
                    "version": version,
                    "supersedes_definition_id": supersedes,
                }
            )
            try:
                saved = await repository.insert(stored)
            except DuplicateDocumentError:
                # The collision may be on either unique index, and the two mean
                # different things. A duplicate on the content hash is a
                # concurrent write of the *same* rule, and returning the winner is
                # correct: two documents with one hash would be one rule stored
                # twice, and the caller asked for that rule. A duplicate on the
                # version means a *different* rule claimed this number, so this
                # writer retries against the next one.
                raced = await repository.find({"specification_hash": digest}, limit=1)
                if raced:
                    return CreateOutcome(
                        definition=raced[0], created=False, reason=REUSED_REASON
                    )
                version, supersedes = await self._next_version(complete.name)
                continue
            return CreateOutcome(definition=saved, created=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Could not allocate a version for risk definition {complete.name!r} "
                f"after {MAX_VERSION_ATTEMPTS} attempts because other versions of it "
                "were being written at the same time. Read the rule's versions and "
                "write again."
            ),
        )

    # -- reads --------------------------------------------------------------

    async def get(self, definition_id: str) -> RiskDefinition:
        """Return one definition by id, or 404."""
        return await self._repository().get("definition_id", definition_id)

    async def get_by_hash(self, digest: str) -> RiskDefinition:
        """Return the definition with a given specification hash, or 404.

        Lets a dataset cite a rule by content and have that citation resolve,
        which is the direction that matters when a stored manifest is read back
        months later.
        """
        return await self._repository().get("specification_hash", digest)

    async def list_definitions(
        self,
        *,
        name: str | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[RiskDefinition]:
        """List definitions, newest first, optionally narrowed to one rule name."""
        query = {"name": name} if name is not None else None
        return await self._repository().find(
            query, sort=[("created_at", -1)], skip=skip, limit=limit
        )

    async def list_versions(self, name: str) -> list[RiskDefinition]:
        """Every version of one rule, oldest first, so the history reads forwards."""
        return await self._repository().find(
            {"name": name}, sort=[("version", 1)], limit=1000
        )

    async def latest(self, name: str) -> RiskDefinition:
        """The most recent version of a rule, for a caller that wants current state."""
        found = await self.list_definitions(name=name, limit=1)
        if not found:
            raise DocumentNotFoundError(
                f"No risk definition named {name!r} has been written."
            )
        return found[0]

    async def count(self) -> int:
        return await self._repository().count()

    @staticmethod
    def catalog() -> dict[str, Any]:
        """Describe the variables a rule may reference, and the policy applied to
        their distribution.

        Exposed as a function rather than served from a model so the catalogue
        stays the single source of truth: the endpoint returns what the evaluator
        actually reads, not a copy that can drift.
        """
        from app.services.risk_catalog import (
            RARE_CLASS_MINIMUM_ROWS,
            RETROSPECTIVE_VARIABLES,
            SEVERE_IMBALANCE_RATIO,
        )

        return {
            "variables": [
                {
                    "name": variable.name,
                    "kind": variable.kind.value,
                    "unit": variable.unit,
                    "reads_from": variable.reads_from.value,
                    "leakage_class": variable.leakage_class.value,
                    "known_at_anchor": variable.is_known_at_anchor,
                    "source_collection": variable.source_collection,
                    "source_field": variable.source_field,
                    "operational_meaning": variable.operational_meaning,
                    "derivation": variable.derivation,
                    "nullable_reason": variable.nullable_reason,
                    "operands": list(variable.operands),
                }
                for variable in RISK_VARIABLE_CATALOG
            ],
            "retrospective_variables": list(RETROSPECTIVE_VARIABLES),
            "policy": {
                "severe_imbalance_ratio": SEVERE_IMBALANCE_RATIO,
                "rare_class_minimum_rows": RARE_CLASS_MINIMUM_ROWS,
            },
        }


def _check_variable_agreement(
    variable_name: str,
    operator: Any,
    threshold: Any,
    variable: RiskVariableDefinition,
) -> None:
    """Refuse a criterion whose comparison does not suit its variable's kind."""
    from app.models.enums import FeatureKind

    if variable.kind is FeatureKind.CATEGORICAL:
        if operator not in CATEGORICAL_OPERATORS:
            raise RiskDefinitionError(
                f"{variable_name!r} is categorical, so only equality comparisons "
                f"are meaningful; {operator.value!r} orders values that have no "
                "order"
            )
        if not isinstance(threshold, str):
            raise RiskDefinitionError(
                f"the criterion on {variable_name!r} compares a categorical "
                f"variable against {threshold!r}; the bound must be one of the "
                "values that variable can hold, written as a string"
            )
        return
    if isinstance(threshold, str):
        raise RiskDefinitionError(
            f"the criterion on {variable_name!r} compares a numeric variable "
            f"against the string {threshold!r}; the bound must be a number"
        )


def short_identifier(definition: RiskDefinition) -> str:
    """A short, human-quotable handle for a definition.

    Used in log lines and export filenames, never as an identity: a truncated
    hash can collide, and a collision here would be a citation pointing at the
    wrong rule.
    """
    return f"{definition.name}@{definition.version} ({short_hash(definition.specification_hash)})"


__all__ = [
    "NO_DEFINITION_ID",
    "REUSED_REASON",
    "CreateOutcome",
    "RiskDefinitionError",
    "RiskDefinitionService",
    "short_identifier",
]
