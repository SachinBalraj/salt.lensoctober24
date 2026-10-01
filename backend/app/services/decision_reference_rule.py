"""A working decision rule that contains no threshold of its own.

**The rule below contains no numbers, and that is the point of it.** Every one of
its criteria names a bound with ``bound_variable`` rather than a literal, so each
comparison is made against a figure the operator configured for the pan being
advised: its harvest target, its flood depth, its dilution floor, its transfer
depth. Nothing here states what a salt pan ought to be harvested at, because this
project has no basis for that claim and a number in this file would be read as one.

The consequence worth stating plainly: **this rule is only as good as the operator
who configures the pans.** A pan with no limits configured will not be advised by
it. Every criterion whose bound is unconfigured is reported as unevaluable and the
fallback is withheld, which is the correct answer for a rule that has been asked a
question about a pan whose limits are unknown -- and is why the alternative,
inventing defaults, was not taken. A default would make this rule always answer,
and it would be answering from numbers this project made up.

**It is a starting point for review, not a validated operational standard.** The
criteria and their rationales are written so an operator can disagree with each
one specifically, which is the only honest description of what a first draft of a
decision rule is. Posting it creates version 1 of a named rule; editing a bound
means editing the operator's own configuration, and editing a criterion means
posting a new version through the ordinary endpoint, where the audit trail records
who changed what.
"""

from __future__ import annotations

from app.models.decision_rule import (
    DecisionActionSpec,
    DecisionCriterion,
    DecisionRuleDraft,
)
from app.models.enums import DecisionAction, RiskCriterionOperator

#: The name this draft is posted under. Separate from the rule's own description
#: so that a reviewer can find the draft, and every revision of it, by name.
REFERENCE_RULE_NAME = "Operator-configured harvest and flood rule"

#: The four actions, ordered by how much they commit the operator to. The rank is
#: what ``highest_rank`` uses when several criteria match at once: draining a pan
#: is a more disruptive instruction than waiting, so it wins a tie.
_ACTION_SPECS: tuple[DecisionActionSpec, ...] = (
    DecisionActionSpec(
        action=DecisionAction.HARVEST_NOW,
        rank=1,
        description=(
            "The brine has reached the salinity this operator set as the harvest "
            "target. The pan is ready, so the value is realised now rather than "
            "left to weather or dilute."
        ),
    ),
    DecisionActionSpec(
        action=DecisionAction.WAIT_AND_RECHECK,
        rank=0,
        description=(
            "Nothing decisive fired. The pan is stable enough to leave alone, and "
            "the right time to act has not arrived or cannot be established from "
            "current evidence."
        ),
    ),
    DecisionActionSpec(
        action=DecisionAction.TRANSFER_BRINE,
        rank=2,
        description=(
            "The pan is at the depth this operator wants to keep while transferring "
            "brine. Removing brine protects the concentration that is being built "
            "up rather than waiting for rain to dilute it."
        ),
    ),
    DecisionActionSpec(
        action=DecisionAction.PROTECT_DRAIN,
        rank=3,
        description=(
            "The pan is at or beyond the depth this operator set as its flood "
            "limit. Draining is the most disruptive action here, and it ranks "
            "highest so that it is never outranked by a less urgent criterion "
            "matching the same reading."
        ),
    ),
)


def reference_rule_draft() -> DecisionRuleDraft:
    """The bound-only draft, as an author would POST it.

    Returned as a draft rather than a stored rule on purpose: this module has no
    database and does not write anything. Whoever calls this decides where the
    rule goes, and the ordinary rule endpoint still assigns the version, the
    identifier and the specification hash, so a rule created from this draft is
    indistinguishable in the audit trail from one an operator typed out in full.
    """
    return DecisionRuleDraft(
        name=REFERENCE_RULE_NAME,
        operational_rationale=(
            "A starting rule that compares the physics against the limits this "
            "operator configured for each pan. It contains no thresholds of its "
            "own: every bound is read from the pan's own configuration, so the "
            "same rule serves pans that disagree about their targets, and a pan "
            "with a limit left unconfigured is reported as unevaluable rather than "
            "advised against a default. "
            "Each criterion below is a comparison the operator's own configuration "
            "makes possible, and each rationale says what the comparison is for. "
            "Review them individually: a first draft of a decision rule is a "
            "proposal, and this project's contribution is that the bounds come from "
            "the operator rather than from the software. The action ranks put "
            "draining above every other action so that a flood is never outranked "
            "by a routine match, and leave waiting as the fallback for a pan where "
            "nothing fired."
        ),
        actions=list(_ACTION_SPECS),
        combinator="highest_rank",
        criteria=[
            DecisionCriterion(
                variable="simulated_post_rain_salinity_g_l",
                operator=RiskCriterionOperator.LTE,
                bound_variable="target_harvest_salinity_g_l",
                action=DecisionAction.HARVEST_NOW,
                rationale=(
                    "Harvest once the salinity the mass balance predicts after the "
                    "forecast rain is at or below the target this operator set for "
                    "the pan. The bound is the pan's own registration field, so a "
                    "pan with a higher target waits longer and one with a lower "
                    "target is harvested sooner, without a second rule."
                ),
            ),
            DecisionCriterion(
                variable="simulated_post_rain_depth_cm",
                operator=RiskCriterionOperator.GTE,
                bound_variable="flood_depth_cm",
                action=DecisionAction.PROTECT_DRAIN,
                rationale=(
                    "Protect the pan once the predicted depth reaches the flood "
                    "limit this operator configured. Compared against the predicted "
                    "post-rain depth rather than the current one, because the "
                    "decision being made is about the water the forecast rain will "
                    "leave in the pan."
                ),
            ),
            DecisionCriterion(
                variable="simulated_post_rain_salinity_g_l",
                operator=RiskCriterionOperator.LT,
                bound_variable="dilution_salinity_g_l",
                action=DecisionAction.WAIT_AND_RECHECK,
                rationale=(
                    "When the predicted salinity falls below the dilution limit this "
                    "operator configured, the rain is destroying value rather than "
                    "concentrating it, and the pan should be left alone and "
                    "re-examined rather than harvested or drained. Ranks below the "
                    "harvest and flood criteria so that it acts as a holding "
                    "position and never outranks a flood."
                ),
            ),
            DecisionCriterion(
                variable="simulated_post_rain_depth_cm",
                operator=RiskCriterionOperator.GTE,
                bound_variable="target_flow_cm",
                action=DecisionAction.TRANSFER_BRINE,
                rationale=(
                    "Transfer brine once the predicted depth is at or above the "
                    "transfer depth this operator configured, so the pan is worked "
                    "at the depth they intend rather than drifting shallower as "
                    "evaporation concentrates it. Ranks below the flood criterion, "
                    "since a pan beyond its flood limit needs draining first."
                ),
            ),
        ],
        unmatched_action=DecisionAction.WAIT_AND_RECHECK,
    )


def unconfigured_bounds(draft: DecisionRuleDraft) -> list[str]:
    """Which bounds this draft needs from a pan before it can advise on one.

    Kept as a function of the draft so a caller that has a pan can ask the question
    directly and be told the answer, instead of discovering a missing bound later
    as a refused recommendation with no indication of what to configure.
    """
    needed: list[str] = []
    for criterion in draft.criteria:
        for name in (criterion.bound_variable, criterion.variable):
            if name is not None and name not in needed:
                needed.append(name)
    return needed
