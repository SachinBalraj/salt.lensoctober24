"""Module 12 - deriving one pan's action from a rule, keeping the whole argument.

This module is the decision counterpart to
:mod:`app.services.risk_evaluation`, and it follows the same discipline for the
same reason: a recommendation that holds an action without the rule and its inputs
is advice nobody can audit, and one that holds a rule without its source variables
is a rule nobody can re-check against the pan it fired on.

**The engine decides nothing here.** :func:`evaluate_rule` takes a written rule
and the readings a caller was able to resolve, and returns an action or a stated
reason why none could be assigned. Which readings exist is the caller's business —
it is the part of the system that knows what the twin, the mass balance and the
model actually said.

**A criterion whose variable is unread does not match, and is not a miss.** The
two are different, and conflating them is how a rule appears to have been
evaluated on pans it never read. A criterion over an absent variable is skipped and
named in :attr:`DecisionEvaluation.undecided_reason`, and the fallback action is
withheld whenever any criterion went unread — because a fallback that absorbs
exactly the pans with the thinnest evidence is the failure this module exists to
prevent.

**Every matching criterion is recorded, not only the deciding one.** A pan that
tripped three criteria and was resolved by the most severe of them is a different
situation from one that tripped exactly one, and an audit six months later should
still be able to tell them apart.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.models.decision_rule import (
    DecisionCriterion,
    DecisionCriterionMatch,
    DecisionEvaluation,
    DecisionInputReading,
    DecisionRule,
)
from app.models.enums import (
    DecisionAction,
    DecisionVariableSource,
    FeatureKind,
    RiskCombinator,
    RiskCriterionOperator,
)
from app.services.decision_catalog import (
    DecisionVariableDefinition,
    resolve_decision_variable,
)

#: Why a comparison could not be made at all. Distinct from "the variable held no
#: value", which is reported as a missing input rather than an unusable one.
UNUSABLE_VALUE_REASON = "the value cannot be compared by this operator"


def _compare(
    criterion: DecisionCriterion,
    variable: DecisionVariableDefinition,
    value: Any,
    bound: Any = None,
) -> tuple[bool, str | None]:
    """Apply one criterion's comparison, and say why if it could not be applied.

    ``bound`` is the already-resolved single bound: a literal ``threshold`` read
    off the criterion, or the value of a ``bound_variable`` looked up by the
    caller. Resolving it here rather than inside this function is what lets an
    absent bound be reported as an unread variable -- the same treatment a missing
    input gets -- instead of being discovered mid-comparison.

    Returns ``(matched, unusable_reason)``, and the two are exclusive for the same
    reason they are in Module 10: a comparison either produced an answer or it
    could not be made, and reporting a comparison that could not be made as one
    that produced ``False`` makes a rule look stricter than it is.
    """
    if variable.kind is FeatureKind.CATEGORICAL:
        if criterion.operator not in (
            RiskCriterionOperator.EQ,
            RiskCriterionOperator.NEQ,
        ):
            return False, UNUSABLE_VALUE_REASON
        if not isinstance(bound, str):
            return False, UNUSABLE_VALUE_REASON
        text = str(value)
        if criterion.operator is RiskCriterionOperator.EQ:
            return text == bound, None
        return text != bound, None

    if isinstance(bound, str) or bound is None:
        return False, UNUSABLE_VALUE_REASON
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, UNUSABLE_VALUE_REASON
    number = float(value)

    if criterion.operator is RiskCriterionOperator.LT:
        return number < bound, None
    if criterion.operator is RiskCriterionOperator.LTE:
        return number <= bound, None
    if criterion.operator is RiskCriterionOperator.GT:
        return number > bound, None
    if criterion.operator is RiskCriterionOperator.GTE:
        return number >= bound, None
    if criterion.operator is RiskCriterionOperator.EQ:
        return number == bound, None
    if criterion.operator is RiskCriterionOperator.NEQ:
        return number != bound, None
    if criterion.operator is RiskCriterionOperator.BETWEEN:
        return criterion.lower_bound <= number <= criterion.upper_bound, None
    return (number < criterion.lower_bound or number > criterion.upper_bound), None


def referenced_variables(rule: DecisionRule) -> list[str]:
    """Every variable the rule's criteria name, in declaration order.

    Distinct from :attr:`DecisionRule.variables_used`, which is the sorted
    de-duplicated list stored on the rule. A rule read back from before a field
    existed still has criteria, so the evaluator falls back to these rather than
    refusing to read it.
    """
    seen: list[str] = []
    for criterion in rule.criteria:
        if criterion.variable not in seen:
            seen.append(criterion.variable)
        # A bound named by variable is read as surely as the value it is compared
        # against, so it has to be resolved and recorded with the rest. Leaving it
        # out would make a rule that depends on the operator's configured target
        # look as though it read only the one variable.
        if (
            criterion.bound_variable is not None
            and criterion.bound_variable not in seen
        ):
            seen.append(criterion.bound_variable)
    return seen


def complete_rule(rule: DecisionRule) -> DecisionRule:
    """Fill the derived fields a stored rule carries, from the criteria themselves.

    ``variables_used`` and ``sources_used`` are recomputed here rather than trusted
    from the draft, so a rule cannot be stored claiming one set of inputs while
    reading another. Deriving them in one place is what stops the stored document
    disagreeing with the rules it was built from.
    """
    variables = sorted(
        {
            name
            for criterion in rule.criteria
            for name in (criterion.variable, criterion.bound_variable)
            if name is not None
        }
    )
    sources: list[DecisionVariableSource] = []
    for name in variables:
        source = resolve_decision_variable(name).reads_from
        if source not in sources:
            sources.append(source)
    return rule.model_copy(update={"variables_used": variables, "sources_used": sources})


def _fired_criteria(
    rule: DecisionRule, matches: Sequence[DecisionCriterionMatch]
) -> list[DecisionCriterion]:
    """The criteria that produced ``matches``, in the same order.

    Two criteria on the same variable can both fire, so this is not a lookup by
    variable. It pairs by position instead, which holds because ``matches`` is
    built by walking ``rule.criteria`` in order and appending only on a match --
    the same reason the match list is already the audit record it claims to be.
    """
    fired: list[DecisionCriterion] = []
    remaining = list(matches)
    for criterion in rule.criteria:
        for match in remaining:
            if (
                match.variable == criterion.variable
                and match.action == criterion.action
                and match.operator == criterion.operator
            ):
                fired.append(criterion)
                remaining.remove(match)
                break
    return fired


def evaluate_rule(
    rule: DecisionRule,
    readings: Mapping[str, DecisionInputReading] | None = None,
) -> DecisionEvaluation:
    """Derive one pan's action from a rule, keeping the whole argument.

    ``readings`` holds the variables the caller could resolve, keyed by catalogue
    name. Variables the rule reads but the caller could not resolve are filled in
    from the catalogue as unread, carrying the catalogue's own reason, so the
    evaluation is complete whether or not the caller had an answer.
    """
    supplied = dict(readings or {})
    input_readings: list[DecisionInputReading] = []
    for name in rule.variables_used or referenced_variables(rule):
        catalogue_entry = resolve_decision_variable(name)
        reading = supplied.get(name)
        if reading is None:
            input_readings.append(
                DecisionInputReading(
                    variable=name,
                    value=None,
                    unit=catalogue_entry.unit,
                    source=catalogue_entry.reads_from,
                    reason=catalogue_entry.nullable_reason
                    or "no value was available for this variable",
                )
            )
            continue
        input_readings.append(
            reading.model_copy(
                update={"variable": name, "source": catalogue_entry.reads_from}
            )
        )
    input_readings.sort(key=lambda entry: entry.variable)

    values = {entry.variable: entry.value for entry in input_readings}
    ranks = rule.action_ranks
    matches: list[DecisionCriterionMatch] = []
    unusable: list[str] = []
    missing: list[str] = []

    for criterion in rule.criteria:
        catalogue_entry = resolve_decision_variable(criterion.variable)
        value = values.get(criterion.variable)
        if value is None:
            missing.append(criterion.variable)
            continue
        # Resolve the bound before comparing. A criterion that named a bound
        # variable the caller could not resolve is treated exactly like a missing
        # input -- the operator's target is part of the evidence, so a rule that
        # needs it and did not get it has not been evaluated, and the fallback is
        # withheld along with every other unevaluated criterion.
        if criterion.bound_variable is not None:
            bound = values.get(criterion.bound_variable)
            if bound is None:
                missing.append(criterion.bound_variable)
                continue
        else:
            bound = criterion.threshold
        matched, reason = _compare(criterion, catalogue_entry, value, bound)
        if reason is not None:
            unusable.append(f"{criterion.variable}: {reason}")
            continue
        if not matched:
            continue
        matches.append(
            DecisionCriterionMatch(
                variable=criterion.variable,
                source=catalogue_entry.reads_from,
                operator=criterion.operator,
                comparator=criterion.comparator_text(),
                value=value,
                # The resolved figure, not the literal. These coincide for a
                # threshold criterion and differ for a bound one, where the
                # number is a property of this pan rather than of the rule.
                threshold=bound,
                bound_variable=criterion.bound_variable,
                lower_bound=criterion.lower_bound,
                upper_bound=criterion.upper_bound,
                action=criterion.action,
            )
        )

    action: DecisionAction | None = None
    if matches:
        if rule.combinator is RiskCombinator.HIGHEST_RANK:
            action = max((match.action for match in matches), key=lambda a: ranks[a])
        else:
            action = matches[0].action
    # The fallback means "no criterion fired", which is only a statement the rule
    # can make about a pan it managed to read. A criterion whose variable held no
    # value was never evaluated, so on a pan that skipped one the rule knows
    # nothing and the fallback is withheld.
    elif not unusable and not missing and rule.unmatched_action is not None:
        action = rule.unmatched_action

    undecided_reason: str | None = None
    if action is None:
        if unusable:
            undecided_reason = "; ".join(unusable)
        elif missing:
            undecided_reason = (
                "the rule could not be evaluated: "
                + ", ".join(sorted(set(missing)))
                + " held no value, and the fallback action was withheld rather than "
                "assigned to a pan the rule never read"
            )
        else:
            undecided_reason = (
                "no criterion matched and the rule assigns no fallback action; every "
                "criterion was evaluated"
            )

    return DecisionEvaluation(
        rule_id=rule.rule_id,
        rule_name=rule.name,
        rule_version=rule.version,
        specification_hash=rule.specification_hash,
        action=action,
        action_rank=ranks[action] if action is not None else None,
        input_readings=input_readings,
        criterion_rationales={
            match.variable: criterion.rationale
            for match, criterion in zip(matches, _fired_criteria(rule, matches))
        },
        matched_criteria=matches,
        undecided_reason=undecided_reason,
    )


def rule_uses_ml(rule: DecisionRule) -> bool:
    """Whether the rule's criteria actually read a model estimate.

    Checked against the catalogue rather than the stored ``uses_ml`` flag, because
    the flag is a claim and this is the fact; the write path compares the two so a
    rule cannot be stored asserting the opposite of what it does.
    """
    return any(
        resolve_decision_variable(criterion.variable).is_model_derived
        for criterion in rule.criteria
    )


def rule_uses_physics(rule: DecisionRule) -> bool:
    """Whether the rule's criteria read a mass-balance output."""
    return any(
        resolve_decision_variable(criterion.variable).is_simulated
        for criterion in rule.criteria
    )


def sources_named_by(rule: DecisionRule) -> Sequence[DecisionVariableSource]:
    return tuple(rule.sources_used)


__all__ = [
    "UNUSABLE_VALUE_REASON",
    "complete_rule",
    "evaluate_rule",
    "referenced_variables",
    "rule_uses_ml",
    "rule_uses_physics",
    "sources_named_by",
]
