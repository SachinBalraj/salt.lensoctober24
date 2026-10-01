"""Module 12 - the operational decision rule, as a versioned document.

Module 10 established the argument this module follows: a threshold with no
stated reason is a number nobody can evaluate, and a rule nobody can evaluate is
a rule nobody can revise defensibly. So the decision rule is **data**, written
once, versioned, and carrying its own reasoning — and the engine in
:mod:`app.services.decisions` holds no opinion about when to harvest a pan.

**The four actions are the vocabulary; the rules that reach them are not.** The
engine will only ever emit a :class:`~app.models.enums.DecisionAction`, and
there are exactly four. What decides between them is data, exactly as a risk
label's severity classes are: the thresholds, the variables, the order, and a
written argument for each. A rule that could be edited in place would make every
recommendation already issued under it unreconstructable, so a change is a new
version and ``supersedes_rule_id`` points at the predecessor.

**A criterion reads one variable from a closed catalogue, and the catalogue
records where each comes from.** The source is load-bearing rather than
decorative: it is what keeps the three kinds of evidence separable, so a reader
can tell at a glance whether a rule leaned on what was measured, on what the
mass balance computed, or on what a model estimated. A rule that wants a model
estimate has to name a ``prediction`` variable, and one that wants physics has to
name a ``simulation`` variable; neither can be reached by accident.

**Every recommendation keeps the evaluation that produced it.**
:class:`DecisionEvaluation` is the per-pan provenance block: the action, the
rule and version that chose it, and the value of every variable the criteria
read. A recommendation that holds an action without the rule is a piece of advice
nobody can audit, and one that holds a rule without its source variables is a
rule that cannot be re-checked against the pan it fired on.

**A variable that could not be read is recorded as unread.** It is not imputed,
not defaulted, and not treated as a match. A rule that cannot be evaluated says
so, because a fallback action quietly absorbing exactly those pans — the ones
with the thinnest evidence — is how a data gap turns into an operational
decision.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_identifier,
    utcnow,
)
from app.models.enums import (
    DecisionAction,
    DecisionVariableSource,
    RecommendationPriority,
    RiskCombinator,
    RiskCriterionOperator,
)
from app.models.risk_definition import (
    CATEGORICAL_OPERATORS,
    SINGLE_BOUND_OPERATORS,
)
from app.services.dataset_versioning import HASH_PATTERN

#: Version of the decision-rule document format.
#:
#: Hashed into every rule, for the reason Module 10 hashes its schema version: a
#: document whose fields were reinterpreted must not keep the identity of the one
#: it replaced, or a recommendation citing version 3 would be citing a rule read a
#: different way.
DECISION_RULE_SCHEMA_VERSION = 1

#: The action is stored in a column and compared, sorted and counted, so it is
#: matched against the closed vocabulary rather than a free string. The pattern is
#: loose only enough for the vocabulary's own punctuation, notably the slash in
#: ``PROTECT / DRAIN``.
ACTION_PATTERN = r"^[A-Za-z][A-Za-z0-9 /]{0,39}$"

#: The bounds other documents derive their own storage limits from, named here
#: because the limits are only meaningful alongside the numbers they come from.
#: ``recommendation.py`` sizes its audit text from these so that the widest rule
#: this module accepts can still be recorded; see ``MAX_REASON_CHARS`` there.
MAX_CRITERIA_PER_RULE = 32
#: The comparator a criterion can render, bounded by its own field.
MAX_COMPARATOR_CHARS = 80
#: The longest name and the longest "no value" reason the closed catalogue can
#: produce, measured from it by ``test_the_audit_storage_bounds_cover_the_catalogue``
#: in ``tests/test_decisions.py``.
#:
#: These are stated here as numbers because ``app.models`` cannot import the
#: catalogue module to measure them: that module reaches the db layer, and a model
#: importing it closes a cycle. The test is what keeps them honest, so a widened
#: catalogue fails there rather than producing records the storage refuses.
MAX_CATALOGUE_NAME_CHARS = 32
MAX_CATALOGUE_REASON_CHARS = 208


class DecisionActionSpec(BaseModel):
    """One action in a rule's vocabulary, and where it sits in the ordering.

    Mirrors :class:`~app.models.risk_definition.RiskLabel` for a reason that is
    not stylistic: an action ladder is ordinal, and a rule that escalates to
    ``PROTECT / DRAIN`` over ``WAIT AND RECHECK`` is a different rule from one
    that escalates the other way. The ranks are required to run ``0..n-1`` with
    no gaps, so "the most severe matching criterion wins" is always well defined
    rather than depending on declaration order.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    action: DecisionAction = Field(
        description=(
            "Which operational action this entry describes. One of the four the "
            "project is willing to recommend; the engine emits nothing else."
        ),
    )
    rank: int = Field(
        ge=0,
        le=8,
        description=(
            "Position in the severity ordering, 0 being least severe. Must run 0 "
            "to n-1 with no gaps or repeats across the rule's actions."
        ),
    )
    description: str = Field(
        min_length=1,
        max_length=400,
        description=(
            "What this action means operationally and what the operator is being "
            "asked to do. Required: an action an operator cannot act on without "
            "being told what it means is exactly the one that needs explaining."
        ),
    )
    priority: RecommendationPriority = Field(
        default=RecommendationPriority.MEDIUM,
        description=(
            "How urgent the action is, carried onto the recommendation. Separate "
            "from rank because urgency and severity are different questions: the "
            "most severe match is not always the most time-critical."
        ),
    )


class DecisionCriterion(BaseModel):
    """One comparison against one measurable variable, and what it implies.

    The operator vocabulary is Module 10's, reused rather than reinvented: the
    arguments a threshold can take are the same arguments, and a second
    comparison language in this project would be a second thing to check.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    variable: str = Field(
        min_length=1,
        max_length=64,
        description="A variable from the decision variable catalogue, by name.",
    )
    operator: RiskCriterionOperator = Field(
        description="The comparison to make. A closed vocabulary, not an expression.",
    )
    threshold: float | str | None = Field(
        default=None,
        description=(
            "The bound, for a single-value operator. A number for a numeric "
            "variable; a string for a categorical one, so a rule can be written "
            "against a model's risk class as readily as against a salinity."
        ),
    )
    lower_bound: float | None = Field(default=None, allow_inf_nan=False)
    upper_bound: float | None = Field(default=None, allow_inf_nan=False)
    bound_variable: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "A variable from the decision catalogue to use as the bound, instead of "
            "a literal in ``threshold``. This is how a rule stays free of invented "
            "numbers: it reads the limit the operator configured for this pan, so "
            "the same written rule serves every pan at its own target rather than "
            "asserting one target for all of them."
        ),
    )
    action: DecisionAction = Field(
        description="The action this criterion assigns when it matches.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "Why this variable, this comparison and this bound. Required on every "
            "criterion: it is the part a reviewer can disagree with, and without it "
            "a decision rule is a table of numbers."
        ),
    )
    unit: str | None = Field(
        default=None,
        max_length=16,
        description=(
            "Optional assertion of the variable's unit. When given it must match "
            "the catalogue, so a unit mistake is a 422 rather than a recommendation "
            "nobody can interpret."
        ),
    )

    @model_validator(mode="after")
    def _bounds_match_the_operator(self) -> DecisionCriterion:
        """A criterion carries the arguments its operator actually takes.

        Enforced in both directions for the reason given in
        :meth:`RiskCriterion._bounds_match_the_operator`: an interval operator
        handed a silently-ignored threshold is a rule that looks as though it
        constrains a range while constraining a point.
        """
        if self.operator in SINGLE_BOUND_OPERATORS:
            if self.bound_variable is not None:
                if self.bound_variable == self.variable:
                    raise ValueError(
                        f"bound_variable {self.bound_variable!r} is the variable being "
                        "tested, which would make the criterion compare a value with "
                        "itself"
                    )
                if self.threshold is not None:
                    raise ValueError(
                        f"operator {self.operator.value!r} compares against a single "
                        "bound; give either threshold or bound_variable, not both"
                    )
                if self.lower_bound is not None or self.upper_bound is not None:
                    raise ValueError(
                        f"operator {self.operator.value!r} takes one bound, so "
                        "lower_bound/upper_bound have no meaning here"
                    )
                return self
            if self.threshold is None:
                raise ValueError(
                    f"operator {self.operator.value!r} compares against a single "
                    "bound, and neither threshold nor bound_variable was given"
                )
            if self.lower_bound is not None or self.upper_bound is not None:
                raise ValueError(
                    f"operator {self.operator.value!r} takes a threshold, so "
                    "lower_bound/upper_bound have no meaning here"
                )
            return self
        if self.bound_variable is not None:
            raise ValueError(
                f"operator {self.operator.value!r} compares against an interval, and "
                "bound_variable names a single value; use lower_bound and upper_bound"
            )
        if self.threshold is not None:
            raise ValueError(
                f"operator {self.operator.value!r} compares against an interval, so "
                "a single threshold would be silently ignored; use lower_bound and "
                "upper_bound"
            )
        if self.lower_bound is None or self.upper_bound is None:
            raise ValueError(
                f"operator {self.operator.value!r} needs both lower_bound and "
                "upper_bound"
            )
        if self.upper_bound <= self.lower_bound:
            raise ValueError(
                "upper_bound must be greater than lower_bound, otherwise the "
                "interval is empty and the criterion can never match"
            )
        return self

    def comparator_text(self) -> str:
        """The comparison, written out, for the recommendation and the audit log."""
        from app.models.risk_definition import _render_bound

        if self.operator in SINGLE_BOUND_OPERATORS:
            # A bound named by variable reads as its own name, not as a number.
            # Printing the resolved figure here would assert that the number is
            # part of the rule, when it is a property of one pan that the rule
            # merely asked for.
            bound = self.bound_variable if self.bound_variable is not None else (
                _render_bound(self.threshold)
            )
            return f"{self.variable} {self.operator.value} {bound}"
        symbol = "between" if self.operator is RiskCriterionOperator.BETWEEN else "outside"
        return (
            f"{self.variable} {symbol} "
            f"[{_render_bound(self.lower_bound)}, {_render_bound(self.upper_bound)}]"
        )


class DecisionRuleDraft(BaseModel):
    """What an author supplies, before the service assigns it an identity.

    Separated from :class:`DecisionRule` for Module 10's reason: three of a stored
    rule's fields are the service's to derive and cannot honestly be asked of a
    client — ``specification_hash`` depends on the rest of the document, ``version``
    depends on the stored history, and ``rule_id`` is a database concern. This is
    also the model an HTTP request body validates against, which is why it carries
    none of the audit fields.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "A stable handle for the rule, so its versions form a history. Two "
            "versions of the same name are one rule revised, not two rules."
        ),
    )
    actions: list[DecisionActionSpec] = Field(
        min_length=1,
        max_length=4,
        description=(
            "The actions this rule may recommend, ordered by rank. At least one; a "
            "rule naming all four is usual, and the engine still emits only these."
        ),
    )
    criteria: list[DecisionCriterion] = Field(
        min_length=1,
        max_length=32,
        description=(
            "The comparisons, in declaration order. Order is part of the rule for "
            "the first_match combinator and irrelevant for highest_rank, and it is "
            "hashed either way so it cannot drift silently."
        ),
    )
    combinator: RiskCombinator = Field(
        default=RiskCombinator.HIGHEST_RANK,
        description="How several matching criteria resolve to one action.",
    )
    unmatched_action: DecisionAction | None = Field(
        default=None,
        description=(
            "The action to assign when no criterion matches, or null to withhold a "
            "recommendation with a reason. Null is a real choice: a pan whose "
            "criteria all miss has no action the author's own rule supports, and "
            "quietly assigning it the mildest action would be the engine inventing "
            "a judgement nobody made."
        ),
    )
    operational_rationale: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "Why this rule exists and what operational decision it supports. "
            "Required on the rule as a whole, over and above each criterion's own "
            "rationale."
        ),
    )
    authored_by: str | None = Field(default=None, max_length=120)
    #: Whether this rule is allowed to read a model's estimate. Declared rather than
    #: inferred from the criteria, because "should this rule consult the model at
    #: all" is an operational decision with consequences — it determines whether a
    #: deployed model is load-bearing for an irrigation instruction — and burying it
    #: in a criterion list would make it invisible to the person making it.
    uses_ml: bool = Field(
        default=False,
        description=(
            "True when the rule reads at least one prediction variable. The service "
            "refuses a rule whose criteria contradict this, and a rule that leaves "
            "it false while reading a prediction is refused too."
        ),
    )

    @field_validator("name", "authored_by", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> Any:
        return normalize_identifier(value) if isinstance(value, str) else value

    def as_rule(self) -> DecisionRule:
        """Build the storable document, leaving identity to the service.

        ``variables_used`` and ``sources_used`` are left empty here and filled by
        :func:`~app.services.decision_evaluation.complete_rule`, because both are
        derived from the criteria and deriving them in one place is what stops the
        stored document disagreeing with the rules it was built from.
        """
        return DecisionRule(
            name=self.name,
            actions=self.actions,
            criteria=self.criteria,
            combinator=self.combinator,
            unmatched_action=self.unmatched_action,
            operational_rationale=self.operational_rationale,
            authored_by=self.authored_by,
            uses_ml=self.uses_ml,
            # A placeholder satisfying the field's pattern; the service overwrites
            # it with the digest of the content it actually stored.
            specification_hash="sha256:" + "0" * 64,
        )


class DecisionRule(BaseDocument):
    """One operational decision rule, at one version.

    Immutable once written. The only way to change a threshold is to create a new
    version, which is what lets a recommendation issued today still be describable
    as "recommended under version 3" for as long as the record exists.
    """

    model_config = ConfigDict(validate_assignment=True)

    rule_id: str = Field(
        default_factory=lambda: new_id("decrule"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(
        default=1,
        ge=1,
        description=(
            "How many versions of this name have been written, in creation order. "
            "Counted per name, not per document."
        ),
    )
    actions: list[DecisionActionSpec] = Field(min_length=1, max_length=4)
    criteria: list[DecisionCriterion] = Field(min_length=1, max_length=32)
    combinator: RiskCombinator = RiskCombinator.HIGHEST_RANK
    unmatched_action: DecisionAction | None = None
    operational_rationale: str = Field(min_length=1, max_length=1000)
    authored_by: str | None = Field(default=None, max_length=120)
    uses_ml: bool = Field(
        default=False,
        description=(
            "Whether this rule reads a model's estimate. Stored rather than inferred "
            "so a reader can see which recommendations were load-bearing on a "
            "deployed model without re-reading the criteria."
        ),
    )
    variables_used: list[str] = Field(
        default_factory=list,
        description=(
            "Every catalogued variable a criterion reads, sorted and de-duplicated. "
            "Derived rather than supplied, and stored so a recommendation can report "
            "its inputs without loading the whole rule."
        ),
    )
    sources_used: list[DecisionVariableSource] = Field(
        default_factory=list,
        description=(
            "Which kinds of evidence the criteria read. The separation the module "
            "exists to preserve, restated per rule."
        ),
    )
    schema_version: int = Field(default=DECISION_RULE_SCHEMA_VERSION, ge=1)
    specification_hash: str = Field(
        pattern=HASH_PATTERN,
        description=(
            "SHA-256 over the rule's content. Two rules with the same hash are the "
            "same rule written twice, and the second write returns the first rather "
            "than producing a version that changes nothing."
        ),
    )
    supersedes_rule_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="The rule this version replaces, when there is one.",
    )
    created_at: UtcDateTime = Field(default_factory=utcnow)

    @field_validator("name", "authored_by", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> Any:
        return normalize_identifier(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _vocabulary_is_coherent(self) -> DecisionRule:
        """The action list, the criteria and the fallback have to agree.

        Checked on the model rather than in the service so a rule read back from
        the database cannot hold a criterion pointing at an action it does not
        declare, which is the one defect that leaves a stored rule unevaluable
        while still looking complete.
        """
        declared = [entry.action for entry in self.actions]
        if len(set(declared)) != len(declared):
            raise ValueError("actions must be unique within a rule")
        ranks = sorted(entry.rank for entry in self.actions)
        if ranks != list(range(len(self.actions))):
            raise ValueError(
                f"action ranks must be 0 to {len(self.actions) - 1} with no gaps or "
                f"repeats; got {ranks}"
            )
        for criterion in self.criteria:
            if criterion.action not in declared:
                names = ", ".join(entry.action.value for entry in self.actions)
                raise ValueError(
                    f"the criterion on {criterion.variable!r} assigns "
                    f"{criterion.action.value!r}, which is not one of this rule's "
                    f"actions: {names}"
                )
        if self.unmatched_action is not None and self.unmatched_action not in declared:
            names = ", ".join(entry.action.value for entry in self.actions)
            raise ValueError(
                f"unmatched_action {self.unmatched_action.value!r} is not one of this "
                f"rule's actions: {names}"
            )
        return self

    @property
    def action_names(self) -> tuple[str, ...]:
        """The actions, least severe first."""
        return tuple(
            entry.action.value for entry in sorted(self.actions, key=lambda e: e.rank)
        )

    @property
    def action_ranks(self) -> dict[DecisionAction, int]:
        return {entry.action: entry.rank for entry in self.actions}

    @property
    def action_specs(self) -> dict[DecisionAction, DecisionActionSpec]:
        return {entry.action: entry for entry in self.actions}

    @property
    def short_hash(self) -> str:
        return self.specification_hash.split(":", 1)[1][:12]

    def priority_of(self, action: DecisionAction) -> RecommendationPriority:
        """The urgency declared for one action, defaulting to medium if undeclared.

        The action must be declared for the rule to have been evaluable into it at
        all, so the default is unreachable in practice; it exists so a reader of
        this method cannot be handed a KeyError by a rule that somehow names an
        action outside its own list.
        """
        spec = self.action_specs.get(action)
        return spec.priority if spec is not None else RecommendationPriority.MEDIUM

    def as_hashed_specification(self) -> dict[str, Any]:
        """Exactly the content this rule's identity is computed from.

        Deliberately excludes ``name``, ``version``, ``authored_by`` and
        ``created_at``: those say when the rule was written and who wrote it, while
        the identity of a *rule* is what it says. Re-posting an unchanged rule as a
        new version would otherwise produce a second document that changes no
        recommendation and would make the history read as though the rule had been
        revised twice.
        """
        return {
            "schema_version": self.schema_version,
            "actions": [
                {
                    "action": entry.action.value,
                    "rank": entry.rank,
                    "description": entry.description,
                    "priority": entry.priority.value,
                }
                for entry in sorted(self.actions, key=lambda item: item.rank)
            ],
            "criteria": [criterion.model_dump(mode="json") for criterion in self.criteria],
            "combinator": self.combinator.value,
            "unmatched_action": (
                self.unmatched_action.value if self.unmatched_action else None
            ),
            "uses_ml": self.uses_ml,
            "operational_rationale": self.operational_rationale,
        }


class DecisionInputReading(BaseModel):
    """One variable a rule read, and what it was worth for this pan.

    Carried per recommendation rather than per rule because the value is the point:
    an action with its rule but not its inputs cannot be re-checked against the pan
    it was issued for, and one rule is satisfied by wildly different numbers on
    different pans.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    variable: str = Field(min_length=1, max_length=64)
    value: float | str | None = Field(
        default=None,
        description="The value read, or null when nothing supplied one.",
    )
    unit: str | None = Field(default=None, max_length=16)
    source: DecisionVariableSource = Field(
        description=(
            "Which part of the system the value came from. Stored per recommendation "
            "so the evidence behind an action can be audited without reloading the "
            "rule's catalogue."
        )
    )
    reason: str | None = Field(
        default=None,
        max_length=300,
        description="Why the value is null, when it is.",
    )

    @property
    def is_available(self) -> bool:
        return self.value is not None


class DecisionCriterionMatch(BaseModel):
    """One criterion that fired for this pan, with the comparison that fired it."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    variable: str = Field(min_length=1, max_length=64)
    source: DecisionVariableSource
    operator: RiskCriterionOperator
    comparator: str = Field(
        min_length=1,
        max_length=80,
        description="The comparison written out, so the cell is readable on its own.",
    )
    value: float | str | None = None
    threshold: float | str | None = None
    bound_variable: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "The variable the bound was read from, when the criterion named one. "
            "Kept beside the resolved ``threshold`` so the audit can show both the "
            "figure that decided and the configured field it came from."
        ),
    )
    lower_bound: float | None = None
    upper_bound: float | None = None
    action: DecisionAction


class DecisionEvaluation(BaseModel):
    """The action derived for one pan, and everything needed to re-derive it.

    The block a recommendation keeps. Without ``rule_id`` and ``rule_version`` the
    action is unattributable; without ``input_readings`` the rule cannot be checked
    against the pan it fired on.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    rule_id: str = Field(min_length=1, max_length=64)
    rule_name: str = Field(min_length=1, max_length=120)
    rule_version: int = Field(ge=1)
    specification_hash: str = Field(pattern=HASH_PATTERN)
    action: DecisionAction | None = Field(
        default=None,
        description="The recommended action, or null when the rule could not assign one.",
    )
    action_rank: int | None = Field(default=None, ge=0)
    input_readings: list[DecisionInputReading] = Field(
        default_factory=list,
        description=(
            "Every variable the rule's criteria read, with the value found. Sorted by "
            "name so two runs over the same pan produce the same record."
        ),
    )
    criterion_rationales: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "The author's stated reason for each criterion that fired, keyed by "
            "variable. Copied here because the rule it came from may later be "
            "revised, and an audit six months on has to show the reasoning as it "
            "stood when the decision was taken, not as the current version reads."
        ),
    )
    matched_criteria: list[DecisionCriterionMatch] = Field(
        default_factory=list,
        description=(
            "Every criterion that fired, not only the deciding one. A pan that "
            "tripped three criteria and was resolved by the most severe of them is a "
            "different situation from one that tripped exactly one, and the record "
            "should still be able to tell them apart afterwards."
        ),
    )
    undecided_reason: str | None = Field(
        default=None,
        max_length=400,
        description="Why no action could be assigned, when none was.",
    )

    @property
    def is_decided(self) -> bool:
        return self.action is not None

    def reading_map(self) -> dict[str, float | str | None]:
        return {entry.variable: entry.value for entry in self.input_readings}

    def input_readings_text(self) -> str:
        """The inputs as one cell, in the style of ``missing_features``.

        Unread variables keep their place with the reason where the value would be,
        so a reader can see that a rule was short of an input rather than that it
        was satisfied without one.
        """
        cells: list[str] = []
        for entry in self.input_readings:
            if entry.value is None:
                reason = entry.reason or "no value was available"
                cells.append(f"{entry.variable}=<unset: {reason}>")
            else:
                cells.append(f"{entry.variable}={entry.value}")
        return "; ".join(cells)

    def matched_criteria_text(self) -> str:
        return "; ".join(
            f"{match.comparator} -> {match.action.value}" for match in self.matched_criteria
        )


__all__ = [
    "ACTION_PATTERN",
    "CATEGORICAL_OPERATORS",
    "DECISION_RULE_SCHEMA_VERSION",
    "DecisionActionSpec",
    "DecisionCriterion",
    "DecisionCriterionMatch",
    "DecisionEvaluation",
    "DecisionInputReading",
    "DecisionRule",
    "DecisionRuleDraft",
]
