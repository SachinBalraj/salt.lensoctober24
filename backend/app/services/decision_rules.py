"""Module 12 - storing an operational decision rule, and keeping its history honest.

This service is the only thing in the project that writes a
:class:`~app.models.decision_rule.DecisionRule`. Everything else reads one, and
the engine will not issue a recommendation without citing the exact version it
read.

**Writing a rule is versioned and additive, for Module 10's reason.** A rule that
can be edited in place makes every recommendation already issued under it
unreconstructable: the thresholds behind version 2 would be gone, and "the
recommendation to transfer brine" would refer to numbers nobody could reproduce. A
change is a new version, ``supersedes_rule_id`` points at the predecessor, and the
old document stays exactly as it was written.

**A rule with the same content is not a new version.** The specification hash
covers exactly what the rule *says* — its actions, criteria, combinator, fallback,
whether it reads the model, and its rationale — and deliberately excludes the
name, version, author and timestamp. Posting an unchanged rule returns the
existing document instead of writing a second one that changes no recommendation
and would make the history read as though the rule had been revised twice.

**Three claims are checked against the catalogue before a rule is stored.** A
criterion naming a variable nobody defined is refused, because a rule that depends
on a quantity the system cannot produce cannot be checked. A criterion declaring a
unit the catalogue contradicts is refused, because a bound in one unit compared
against a value in another is a rule about a unit mistake. And a rule whose
``uses_ml`` flag disagrees with whether its criteria actually read a prediction
variable is refused, because that flag is what tells a reader whether a deployed
model was load-bearing for an irrigation instruction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status

from app.db.collections import DECISION_RULES
from app.db.errors import DocumentNotFoundError, DuplicateDocumentError
from app.db.repository import MongoRepository
from app.models.decision_rule import DecisionRule, DecisionRuleDraft
from app.models.enums import DecisionAction, FeatureKind
from app.services.dataset_versioning import content_hash
from app.services.decision_catalog import resolve_decision_variable
from app.services.decision_evaluation import complete_rule, rule_uses_ml

#: Returned when a rule is re-posted unchanged. Spelled out rather than inferred
#: from a status code so a caller can branch on it without comparing digests.
REUSED_REASON = "identical_rule_already_stored"

#: How many times a write will re-read the rule's history after losing a race for a
#: version number. One retry resolves an ordinary collision between two writers; the
#: bound is here so a pathologically contended name fails loudly instead of spinning.
MAX_VERSION_ATTEMPTS = 5


@dataclass(frozen=True)
class CreateOutcome:
    """What a write to this service actually did.

    ``created`` is the whole point of the pair: a caller that re-posts a rule
    unchanged has done nothing, and reporting that as a fresh version would put a
    phantom entry in the rule's history.
    """

    rule: DecisionRule
    created: bool
    reason: str | None = None


class DecisionRuleError(ValueError):
    """A rule that cannot be stored, with the reason stated.

    A ``ValueError`` so a router can turn it into a 422 naming the offending field,
    which is the one response an author can act on without reading this module.
    """


class DecisionRuleService:
    """Store, version and read operational decision rules."""

    def __init__(self, database: Any) -> None:
        self._database = database

    def _repository(self) -> MongoRepository[DecisionRule]:
        return MongoRepository(self._database, DECISION_RULES, DecisionRule)

    # -- validation ---------------------------------------------------------

    @staticmethod
    def validate_criteria(rule: DecisionRule) -> None:
        """Check every criterion against the variable catalogue.

        Three things are refused here rather than at evaluation time, because all
        three are mistakes in the *rule* and not in the data: an uncatalogued
        variable, a unit that contradicts the catalogue, and an ordering comparison
        against a categorical variable.
        """
        for criterion in rule.criteria:
            variable = resolve_decision_variable(criterion.variable)
            _check_operator_agreement(criterion, variable)
            if criterion.bound_variable is not None:
                _check_bound_variable(criterion, variable)
            if criterion.unit is not None and criterion.unit != variable.unit:
                raise DecisionRuleError(
                    f"the criterion on {criterion.variable!r} declares unit "
                    f"{criterion.unit!r} but that variable is measured in "
                    f"{variable.unit!r}; a bound in one unit compared against a value "
                    "in another produces a recommendation that is a fact about the "
                    "unit mistake rather than about any pan"
                )
            if variable.kind is FeatureKind.CATEGORICAL and not isinstance(
                criterion.threshold, str
            ):
                raise DecisionRuleError(
                    f"the criterion on {criterion.variable!r} compares a categorical "
                    "variable against a non-string threshold; a risk class is a label, "
                    "so it is compared for equality against a quoted class name"
                )
        declared = rule_uses_ml(rule)
        if declared != rule.uses_ml:
            raise DecisionRuleError(
                f"uses_ml is {rule.uses_ml} but the criteria "
                f"{'do' if declared else 'do not'} read a prediction variable. The "
                "flag is what tells a reader whether a deployed model was "
                "load-bearing for this rule, so it has to agree with the criteria"
            )

    async def _next_version(self, name: str) -> tuple[int, str | None]:
        """The next version number for a rule name, and the id it supersedes.

        Read from the stored history rather than incremented, so a rule's first
        version is 1 rather than whatever a counter happened to hold. The read and
        the insert that follows are not atomic, and what makes the pair safe is the
        unique index on ``(name, version)``: two writers that compute the same
        number cannot both store it, and the loser retries against the winner.
        """
        repository = self._repository()
        previous = await repository.find({"name": name}, sort=[("version", -1)], limit=1)
        if not previous:
            return 1, None
        newest = previous[0]
        return newest.version + 1, newest.rule_id

    # -- writes -------------------------------------------------------------

    async def create(self, draft: DecisionRuleDraft) -> CreateOutcome:
        """Store a draft, or return the existing rule if it says the same thing.

        The hash is computed here from the stored content rather than taken from
        the caller, so a client cannot present a rule under a hash that does not
        describe it. A client is never asked to supply one.
        """
        proposed = draft.as_rule()

        # The catalogue-derived fields are filled before validation, because the
        # uses_ml cross-check needs the criteria resolved and the hash must cover
        # what is actually stored.
        complete = complete_rule(proposed)
        self.validate_criteria(complete)

        digest = content_hash(complete.as_hashed_specification())
        complete = complete.model_copy(update={"specification_hash": digest})

        repository = self._repository()
        existing = await repository.find({"specification_hash": digest}, limit=1)
        if existing:
            return CreateOutcome(rule=existing[0], created=False, reason=REUSED_REASON)

        version, supersedes = await self._next_version(complete.name)
        for _attempt in range(MAX_VERSION_ATTEMPTS):
            stored = complete.model_copy(
                update={"version": version, "supersedes_rule_id": supersedes}
            )
            try:
                saved = await repository.insert(stored)
            except DuplicateDocumentError:
                # The collision may be on either unique index, and the two mean
                # different things. A duplicate on the content hash is a concurrent
                # write of the *same* rule, and returning the winner is correct. A
                # duplicate on the version means a *different* rule claimed this
                # number, so this writer retries against the next one.
                raced = await repository.find({"specification_hash": digest}, limit=1)
                if raced:
                    return CreateOutcome(rule=raced[0], created=False, reason=REUSED_REASON)
                version, supersedes = await self._next_version(complete.name)
                continue
            return CreateOutcome(rule=saved, created=True)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Could not allocate a version for decision rule {complete.name!r} "
                f"after {MAX_VERSION_ATTEMPTS} attempts because other versions of it "
                "were being written at the same time. Read the rule's versions and "
                "write again."
            ),
        )

    # -- reads --------------------------------------------------------------

    async def get(self, rule_id: str) -> DecisionRule:
        """Return one rule by id, or 404."""
        return await self._repository().get("rule_id", rule_id)

    async def list_rules(
        self,
        *,
        name: str | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[DecisionRule]:
        """List rules, newest first, optionally narrowed to one rule's name."""
        query = {"name": name} if name is not None else None
        return await self._repository().find(
            query, sort=[("created_at", -1)], skip=skip, limit=limit
        )

    async def list_versions(self, name: str) -> list[DecisionRule]:
        """Every version of one rule, oldest first, so the history reads forwards."""
        return await self._repository().find({"name": name}, sort=[("version", 1)], limit=1000)

    async def latest(self, name: str) -> DecisionRule:
        """The most recent version of a rule, for a caller that wants current state."""
        found = await self.list_rules(name=name, limit=1)
        if not found:
            raise DocumentNotFoundError(
                f"No decision rule named {name!r} has been written."
            )
        return found[0]

    async def count(self) -> int:
        return await self._repository().count()

    @staticmethod
    def catalog() -> dict[str, Any]:
        """Describe the variables a rule may reference, served from the same
        catalogue the evaluator reads, so what is published is what is enforced."""
        from app.services.decision_catalog import DECISION_VARIABLE_CATALOG

        return {
            "variables": [
                {
                    "name": variable.name,
                    "kind": variable.kind.value,
                    "unit": variable.unit,
                    "reads_from": variable.reads_from.value,
                    "source_collection": variable.source_collection,
                    "source_field": variable.source_field,
                    "operational_meaning": variable.operational_meaning,
                    "derivation": variable.derivation,
                    "nullable_reason": variable.nullable_reason,
                    "requires_model": variable.is_model_derived,
                    "requires_simulation": variable.is_simulated,
                }
                for variable in DECISION_VARIABLE_CATALOG
            ],
            "actions": [action.value for action in DecisionAction],
            "policy": {
                "action_vocabulary_is_closed": True,
                "max_actions_per_rule": 4,
            },
        }


def _check_bound_variable(
    criterion: Any, variable: Any
) -> None:
    """Refuse a bound that names something a number cannot be read from.

    The three refusals are all ways the author meant to compare against a bound
    that does not exist, which would otherwise surface as an undecided
    recommendation on the day it mattered rather than as a rejected rule:

    * a name the catalogue does not have, which would raise at evaluation;
    * a categorical variable, which is a label and so has no order to compare
      against, the same objection ``_check_operator_agreement`` raises for a
      string threshold on a numeric variable;
    * a variable measured in a different unit, where the comparison would be a
      fact about the unit mistake rather than about any pan.
    """
    bound = resolve_decision_variable(criterion.bound_variable)
    if bound.kind is FeatureKind.CATEGORICAL:
        raise DecisionRuleError(
            f"bound_variable {criterion.bound_variable!r} is categorical, so it "
            f"cannot be used as the bound for {criterion.variable!r}; "
            f"{bound.unit!r} is a label and has no order to compare against"
        )
    if bound.unit != variable.unit:
        raise DecisionRuleError(
            f"the criterion on {criterion.variable!r} is measured in "
            f"{variable.unit!r} but its bound_variable "
            f"{criterion.bound_variable!r} is measured in {bound.unit!r}; comparing "
            "across units produces a recommendation that is a fact about the unit "
            "mistake rather than about any pan"
        )


def _check_operator_agreement(criterion: Any, variable: Any) -> None:
    """Refuse a criterion whose comparison does not suit its variable's kind.

    Two mismatches are caught, both at write time rather than at evaluation time. A
    comparison that cannot succeed should never be stored as a live criterion,
    because a rule holding one reads as a decision the operator can rely on, and
    would instead surface as an unusable input on the day it mattered.
    """
    from app.models.risk_definition import CATEGORICAL_OPERATORS

    if variable.kind is FeatureKind.CATEGORICAL:
        if criterion.operator not in CATEGORICAL_OPERATORS:
            raise DecisionRuleError(
                f"{variable.name!r} is categorical, so only equality comparisons are "
                f"meaningful; {criterion.operator.value!r} orders values that have no "
                "order"
            )
        return

    # A numeric variable may be compared with any operator, but never against a
    # string: '300' and 300 are not the same value, and silently coercing one to
    # the other would let a rule fire on a comparison the author did not write.
    if isinstance(criterion.threshold, str):
        raise DecisionRuleError(
            f"{variable.name!r} is numeric, so the string threshold "
            f"{criterion.threshold!r} cannot be compared against it; write the "
            f"number itself"
        )


__all__ = [
    "MAX_VERSION_ATTEMPTS",
    "REUSED_REASON",
    "CreateOutcome",
    "DecisionRuleError",
    "DecisionRuleService",
]
