"""Module 10 - deriving a risk label from a definition, and reporting the result.

Two pure functions live here, and neither touches the database.

:func:`evaluate_definition` applies one definition to one row's readings and
returns the :class:`~app.models.risk_definition.RiskEvaluation` that travels with
the training record. :func:`build_class_distribution` takes a set of those
evaluations and returns the pre-training distribution report.

**The evaluator decides nothing.** It does not choose a vocabulary, fill in a
threshold, or fall back to a class of its own. Everything it knows comes from the
definition it is handed, and it reports which criteria matched rather than only
which class won — so a row that tripped three criteria and resolved to the most
severe of them is distinguishable afterwards from a row that tripped exactly one.

**A criterion that cannot be evaluated is skipped, not guessed at.** If the
variable a criterion reads has no value on this row, the criterion does not match
and the row stays unlabelled with the reason attached. Treating "no reading" as
"did not match" and then defaulting the row to the lowest class would manufacture
a severity the researcher's rule never asserted, and would do it on exactly the
rows where the data is thinnest, which is where a fabricated label is least
defensible and hardest to notice.

**Every variable the definition reads appears in the output, valued or not.** A
definition reading four variables on a row where one is missing records all four,
with the reason on the one that is empty. A shorter list would quietly hide which
input was absent, and a rule short of an input looks identical to a rule that
never consulted one.

**The distribution is reported before any model exists.** That is the point of
this function. Class balance is a joint property of a rule and a set of pans,
visible in neither the rule nor the data alone, and it is the last place a 97/2/1
split can be noticed before an accuracy figure is quoted as though it meant
something. The imbalance thresholds come from :mod:`app.services.risk_catalog`, so
Module 07's dataset report and this one apply the same policy rather than each
carrying a literal.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from app.models.enums import FeatureKind, RiskCombinator, RiskCriterionOperator
from app.models.risk_definition import (
    RiskClassDistribution,
    RiskClassShare,
    RiskCriterion,
    RiskCriterionMatch,
    RiskDefinition,
    RiskEvaluation,
    RiskSourceVariable,
)
from app.services.risk_catalog import (
    RARE_CLASS_MINIMUM_ROWS,
    SEVERE_IMBALANCE_RATIO,
    RiskVariableDefinition,
    label_basis_for,
    resolve_risk_variable,
)

#: How a criterion read a value it could not use. Attached to the row rather than
#: swallowed, so a rule that never fires can be diagnosed from the data.
UNUSABLE_VALUE_REASON = (
    "the value read could not be compared with the criterion's bound, so the "
    "criterion was not evaluated"
)

#: The most severe matching criterion wins under this combinator. Rank is the
#: definition's own ordering, so "most severe" is whatever the researcher said it
#: was and never this module's judgement of which class sounds worst.
def _highest_rank_label(matches: Sequence[RiskCriterionMatch], ranks: Mapping[str, int]) -> str:
    return max((match.label for match in matches), key=lambda label: ranks[label])


def _compare(
    criterion: RiskCriterion,
    variable: RiskVariableDefinition,
    value: Any,
) -> tuple[bool, str | None]:
    """Apply one criterion's comparison, and say why if it could not be applied.

    Returns ``(matched, unusable_reason)``. The two are exclusive: a comparison
    either produced an answer or it could not be made, and conflating them is
    what makes a rule look stricter than it is.
    """
    if variable.kind is FeatureKind.CATEGORICAL:
        if criterion.operator not in (RiskCriterionOperator.EQ, RiskCriterionOperator.NEQ):
            return False, UNUSABLE_VALUE_REASON
        if not isinstance(criterion.threshold, str):
            return False, UNUSABLE_VALUE_REASON
        text = str(value)
        if criterion.operator is RiskCriterionOperator.EQ:
            return text == criterion.threshold, None
        return text != criterion.threshold, None

    if isinstance(criterion.threshold, str) or criterion.threshold is None:
        return False, UNUSABLE_VALUE_REASON
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, UNUSABLE_VALUE_REASON
    number = float(value)

    if criterion.operator is RiskCriterionOperator.LT:
        return number < criterion.threshold, None
    if criterion.operator is RiskCriterionOperator.LTE:
        return number <= criterion.threshold, None
    if criterion.operator is RiskCriterionOperator.GT:
        return number > criterion.threshold, None
    if criterion.operator is RiskCriterionOperator.GTE:
        return number >= criterion.threshold, None
    if criterion.operator is RiskCriterionOperator.EQ:
        return number == criterion.threshold, None
    if criterion.operator is RiskCriterionOperator.NEQ:
        return number != criterion.threshold, None
    if criterion.operator is RiskCriterionOperator.BETWEEN:
        return criterion.lower_bound <= number <= criterion.upper_bound, None
    return (number < criterion.lower_bound or number > criterion.upper_bound), None


def evaluate_definition(
    definition: RiskDefinition,
    readings: Mapping[str, RiskSourceVariable] | None = None,
) -> RiskEvaluation:
    """Derive one row's label from a definition, keeping the whole argument.

    ``readings`` holds the variables the caller could resolve, keyed by catalogue
    name. Variables the definition reads but the caller could not resolve are
    filled in from the catalogue as unavailable, carrying the catalogue's own
    reason, so the evaluation is complete whether or not the caller had an answer.
    """
    supplied = dict(readings or {})
    source_variables: list[RiskSourceVariable] = []
    for name in definition.variables_used or _referenced_variables(definition):
        catalogue_entry = resolve_risk_variable(name)
        reading = supplied.get(name)
        if reading is None:
            source_variables.append(
                RiskSourceVariable(
                    variable=name,
                    value=None,
                    unit=catalogue_entry.unit,
                    leakage_class=catalogue_entry.leakage_class,
                    reason=catalogue_entry.nullable_reason
                    or "no record supplied a value for this variable",
                )
            )
            continue
        source_variables.append(
            reading.model_copy(update={"variable": name, "leakage_class": catalogue_entry.leakage_class})
        )
    source_variables.sort(key=lambda entry: entry.variable)

    values = {entry.variable: entry.value for entry in source_variables}
    ranks = definition.class_ranks
    matches: list[RiskCriterionMatch] = []
    unusable: list[str] = []
    missing: list[str] = []

    for criterion in definition.criteria:
        catalogue_entry = resolve_risk_variable(criterion.variable)
        value = values.get(criterion.variable)
        if value is None:
            missing.append(criterion.variable)
            continue
        matched, reason = _compare(criterion, catalogue_entry, value)
        if reason is not None:
            unusable.append(f"{criterion.variable}: {reason}")
            continue
        if not matched:
            continue
        matches.append(
            RiskCriterionMatch(
                variable=criterion.variable,
                operator=criterion.operator,
                comparator=criterion.comparator_text(),
                value=value,
                threshold=criterion.threshold,
                lower_bound=criterion.lower_bound,
                upper_bound=criterion.upper_bound,
                label=criterion.label,
            )
        )

    label: str | None = None
    if matches:
        if definition.combinator is RiskCombinator.HIGHEST_RANK:
            label = _highest_rank_label(matches, ranks)
        else:
            label = matches[0].label
    # The fallback means "no criterion fired", which is only a statement the rule
    # can make about a row it managed to read. A criterion whose variable held no
    # value was never evaluated, so on a row that skipped one the rule knows
    # nothing and the fallback is withheld -- otherwise the fallback would absorb
    # exactly the rows with the thinnest data, which is where an assumed class is
    # least defensible.
    elif not unusable and not missing and definition.unmatched_label is not None:
        label = definition.unmatched_label

    unlabelled_reason: str | None = None
    if label is None:
        if unusable:
            unlabelled_reason = "; ".join(unusable)
        elif missing:
            unlabelled_reason = (
                "the definition could not be evaluated: "
                + ", ".join(sorted(set(missing)))
                + " held no value, and the fallback class was withheld rather than "
                "assigned to a row the rule never read"
            )
        else:
            unlabelled_reason = (
                "no criterion matched and the definition assigns no fallback class; "
                "every criterion was evaluated"
            )

    return RiskEvaluation(
        definition_id=definition.definition_id,
        definition_name=definition.name,
        definition_version=definition.version,
        specification_hash=definition.specification_hash,
        label_column=definition.label_column,
        label=label,
        label_rank=ranks[label] if label is not None else None,
        label_basis=definition.label_basis,
        source_variables=source_variables,
        matched_criteria=matches,
        unlabelled_reason=unlabelled_reason,
    )


def _referenced_variables(definition: RiskDefinition) -> list[str]:
    """The distinct variables a definition's criteria read, in declared order.

    Used when ``variables_used`` has not been filled in yet, so an evaluation is
    still complete for a definition that has not been through the write service.
    """
    seen: list[str] = []
    for criterion in definition.criteria:
        if criterion.variable not in seen:
            seen.append(criterion.variable)
    return seen


def build_class_distribution(
    definition: RiskDefinition,
    evaluations: Sequence[RiskEvaluation],
) -> RiskClassDistribution:
    """Report how a definition's classes fall over the stored records.

    Every declared class appears, including any that were never observed, because
    "the rarest class holds no rows" and "the rarest class holds three rows" are
    different problems and only one of them is visible in a list of what turned up.
    """
    rows = list(evaluations)
    counts = Counter(
        evaluation.label for evaluation in rows if evaluation.label is not None
    )
    labelled = sum(counts.values())
    unlabelled = len(rows) - labelled
    reasons = Counter(
        evaluation.unlabelled_reason or "unlabelled for an unrecorded reason"
        for evaluation in rows
        if evaluation.label is None
    )

    classes: list[RiskClassShare] = []
    for label in sorted(definition.classes, key=lambda item: item.rank):
        count = counts.get(label.name, 0)
        classes.append(
            RiskClassShare(
                label=label.name,
                rank=label.rank,
                description=label.description,
                count=count,
                percent=(count / labelled * 100.0) if labelled else None,
            )
        )

    populated = [entry for entry in classes if entry.count > 0]
    unobserved = [entry.label for entry in classes if entry.count == 0]
    rarest = min(populated, key=lambda entry: entry.count) if populated else None
    most_common = max(populated, key=lambda entry: entry.count) if populated else None
    # Null rather than infinite when a declared class is empty, because "the rarest
    # class holds no rows" is a fact and "it is infinitely rare" is a division by
    # zero. The empty class is reported separately in unobserved_classes.
    ratio = (
        most_common.count / rarest.count
        if rarest is not None and most_common is not None and rarest.count > 0
        else None
    )
    unassigned = counts.get(definition.unmatched_label, 0) if definition.unmatched_label else 0

    warnings = _distribution_warnings(
        definition=definition,
        rows=len(rows),
        labelled=labelled,
        unlabelled=unlabelled,
        classes=classes,
        unobserved=unobserved,
        rarest=rarest,
        most_common=most_common,
        ratio=ratio,
        unassigned=unassigned,
    )
    return RiskClassDistribution(
        definition_id=definition.definition_id,
        definition_name=definition.name,
        definition_version=definition.version,
        specification_hash=definition.specification_hash,
        label_column=definition.label_column,
        label_basis=definition.label_basis,
        row_count=len(rows),
        labelled_count=labelled,
        unlabelled_count=unlabelled,
        unlabelled_reasons=dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
        classes=classes,
        unassigned_count=unassigned,
        unobserved_classes=unobserved,
        populated_class_count=len(populated),
        imbalance_ratio=ratio,
        rarest_class=rarest.label if rarest else None,
        rarest_class_count=rarest.count if rarest else None,
        most_common_class=most_common.label if most_common else None,
        most_common_class_count=most_common.count if most_common else None,
        severely_imbalanced=bool(
            (ratio is not None and ratio >= SEVERE_IMBALANCE_RATIO)
            or (rarest is not None and rarest.count < RARE_CLASS_MINIMUM_ROWS)
        ),
        warnings=warnings,
        note=_distribution_note(
            definition=definition,
            rows=len(rows),
            labelled=labelled,
            unlabelled=unlabelled,
            classes=classes,
            unobserved=unobserved,
            rarest=rarest,
            ratio=ratio,
            unassigned=unassigned,
        ),
    )


def _distribution_warnings(
    *,
    definition: RiskDefinition,
    rows: int,
    labelled: int,
    unlabelled: int,
    classes: Sequence[RiskClassShare],
    unobserved: Sequence[str],
    rarest: RiskClassShare | None,
    most_common: RiskClassShare | None,
    ratio: float | None,
    unassigned: int,
) -> list[str]:
    """The decisions a researcher owes themselves before fitting anything.

    Phrased as decisions rather than errors. An imbalanced split is an ordinary
    property of operational data and is often worth fitting after rebalancing; the
    module's job is to make it a choice rather than a surprise found in a score
    afterwards.
    """
    warnings: list[str] = []
    if not rows:
        warnings.append(
            "No rows were evaluated, so this rule's labels have not been seen against "
            "any pan. Nothing about its usefulness can be claimed yet."
        )
        return warnings
    if labelled == 0:
        warnings.append(
            "No row received a label. The rule either did not match the recorded "
            "data or assigns no fallback class, and no label was invented to make "
            "the distribution look populated."
        )
    if len([entry for entry in classes if entry.count > 0]) < 2 and labelled:
        warnings.append(
            f"All {labelled} labelled row(s) landed in one class. There is nothing to "
            "learn: any model would reproduce a constant, and this is a fact about "
            "the rule and the pans together rather than about a model."
        )
    if unobserved:
        warnings.append(
            "These declared classes were never observed: "
            + ", ".join(unobserved)
            + ". A model cannot be evaluated against a class no event produced."
        )
    if ratio is not None and ratio >= SEVERE_IMBALANCE_RATIO and rarest and most_common:
        warnings.append(
            f"The rarest class holds {rarest.count} row(s) and the most common "
            f"{most_common.count}, a ratio of {ratio:.1f}:1, at or beyond the "
            f"{SEVERE_IMBALANCE_RATIO:.0f}:1 threshold. An accuracy figure on this "
            "split would mostly be reporting the majority class; consider "
            "rebalancing, or restricting the target to the classes that occurred."
        )
    if rarest is not None and rarest.count < RARE_CLASS_MINIMUM_ROWS:
        warnings.append(
            f"The rarest populated class, {rarest.label!r}, holds {rarest.count} "
            f"row(s), below the {RARE_CLASS_MINIMUM_ROWS} at which a per-class figure "
            "is an estimate rather than a single observation."
        )
    if unlabelled:
        warnings.append(
            f"{unlabelled} of {rows} row(s) received no label and cannot be trained "
            "on. They are counted rather than dropped so the gap stays visible."
        )
    if unassigned and labelled and unassigned == labelled:
        warnings.append(
            f"Every labelled row fell through to the fallback class {definition.unmatched_label!r}. "
            "The rule's criteria matched nothing, so the label carries no information "
            "from the definition."
        )
    if definition.is_retrospective:
        warnings.append(
            "This rule reads a measurement taken after the anchor, so these labels "
            "describe what happened. A model fitted to them cannot be used to "
            "anticipate the event it is graded against."
        )
    return warnings


def _distribution_note(
    *,
    definition: RiskDefinition,
    rows: int,
    labelled: int,
    unlabelled: int,
    classes: Sequence[RiskClassShare],
    unobserved: Sequence[str],
    rarest: RiskClassShare | None,
    ratio: float | None,
    unassigned: int,
) -> str:
    if not rows:
        return (
            "No rows were evaluated, so there is no distribution to report and every "
            "figure below is null rather than zero."
        )
    if not labelled:
        return (
            "No row received a label, so no class can be described. The reasons are "
            "counted in unlabelled_reasons and none of them was filled in by guesswork."
        )
    populated = [entry for entry in classes if entry.count > 0]
    if len(populated) < 2:
        only = populated[0].label if populated else "no class"
        return (
            f"All {labelled} labelled row(s) landed in {only!r}. A single populated "
            "class supports no model, and the cause is in the rule or the recorded "
            "data rather than in the choice of learner."
        )
    if unobserved:
        return (
            f"{len(unobserved)} of {len(classes)} declared classes were never observed: "
            + ", ".join(unobserved)
            + ". This is a fact about the pans and the period, and it bounds what "
            "any model fitted here could be evaluated against."
        )
    if ratio is not None and ratio >= SEVERE_IMBALANCE_RATIO and rarest:
        return (
            f"The rarest class {rarest.label!r} holds {rarest.count} row(s) against "
            f"{max(entry.count for entry in populated)} in the most common, a ratio of "
            f"{ratio:.1f}:1. A model that always predicted the majority class would "
            f"score about {max(entry.count for entry in populated) / labelled * 100.0:.0f}%."
        )
    if unlabelled:
        return (
            f"{labelled} labelled row(s) across {len(populated)} class(es), with "
            f"{unlabelled} row(s) left unlabelled. The unlabelled rows are kept in "
            "the report so the coverage of the rule is not overstated."
        )
    if unassigned:
        return (
            f"{labelled} labelled row(s) across {len(populated)} class(es), of which "
            f"{unassigned} came from the fallback class rather than from a criterion."
        )
    ratio_text = f" with a rarest-to-commonest ratio of {ratio:.1f}:1" if ratio else ""
    return (
        f"{labelled} labelled row(s) across {len(populated)} observed class(es)"
        f"{ratio_text}."
    )


def definition_for_variables(definition: RiskDefinition) -> RiskDefinition:
    """Return the definition with ``variables_used`` filled from its criteria.

    Idempotent, and the only place a definition's dependency list is derived. A
    definition read straight from the database is already complete; one built in a
    test or a notebook may not be, and evaluating it should still record every
    variable it read.
    """
    referenced = _referenced_variables(definition)
    if definition.variables_used == sorted(set(referenced)):
        return definition
    return definition.model_copy(
        update={
            "variables_used": sorted(set(referenced)),
            "label_basis": label_basis_for(referenced),
        }
    )


__all__ = [
    "UNUSABLE_VALUE_REASON",
    "build_class_distribution",
    "definition_for_variables",
    "evaluate_definition",
]
