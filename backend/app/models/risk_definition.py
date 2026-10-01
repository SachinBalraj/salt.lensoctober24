"""Module 10 - a researcher's operational risk criteria, as a versioned document.

This module exists because the words "high", "medium" and "low" carry no
information on their own. A severity label only means something alongside the
measured quantities that produced it, the thresholds those quantities were
compared against, and the reason the threshold is where it is. All three are the
researcher's, not the code's: a label set written into a training script cannot be
argued with, cannot be revised without invalidating every model already fitted to
it, and cannot be cited in a paper as anything other than a constant.

So the label set is **data**, and this module is where it lives.

**Nothing here knows the labels.** There is no ``HIGH``, no ``MEDIUM``, no
``LOW``, and no default vocabulary. A definition arrives with its own class names,
its own ordering, and its own thresholds, and this module's only opinion is about
whether those are internally coherent and whether every one of them is justified
in writing. A researcher may call them ``watch``/``act now``/``lost the season``,
``dilution risk``/``flood risk``, or anything else; the module stores the strings
and never ranks them by guessing which sounds worse.

**Every criterion must be argued for.** :attr:`RiskCriterion.rationale` is
required, not optional, and so is :attr:`RiskDefinition.operational_rationale`. A
threshold with no stated reason is a number a future reader cannot evaluate, and a
risk definition nobody can evaluate is a risk definition nobody can revise
defensibly.

**Versions are immutable and additive.** A definition is written once. There is no
update and no delete; a change means a new document at the next version with
``supersedes_definition_id`` pointing back at its predecessor. This is what makes
``(definition_id, version)`` a usable citation: a model fitted against version 3
will always be describable as "fitted against version 3", even after version 4
exists, and even after the thresholds behind version 3 have turned out to be
wrong.

**Every derived row keeps the rule that derived it.** :class:`RiskEvaluation` is
the per-record provenance block: the label, the definition and version that
produced it, and the value of every variable the criteria read. A training record
that holds a label without its rule is a label nobody can audit, and a training
record that holds a rule without its source variables is a rule that cannot be
re-checked against the pans it fired on.

**A definition is a claim about the future or about the past, never both.**
:attr:`RiskDefinition.label_basis` records which. A label built from quantities
measurable at the anchor can be forecast; a label built from a recorded harvest
outcome can only be described. The distinction is structural rather than a
caveat, and it is re-checked per row by Module 07's causal audit.
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
    LeakageClass,
    RiskCombinator,
    RiskCriterionOperator,
    RiskLabelBasis,
)
from app.services.dataset_versioning import HASH_PATTERN

#: Version of the definition document format.
#:
#: Hashed into every definition. Without it, a document whose fields were
#: reinterpreted could keep the identity of the one it replaced, and an experiment
#: citing that identity would be citing a rule read a different way.
RISK_DEFINITION_SCHEMA_VERSION = 1

#: Label names are compared, sorted, counted and grouped, so they are restricted
#: to characters that survive all four unchanged. A class called "high risk " with
#: a trailing space is a class that silently fails to match one of its own
#: criteria, which is the kind of defect that surfaces only as a class
#: distribution nobody can explain.
RISK_LABEL_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 ._/&-]{0,39}$"

#: The same restriction on the column a definition's label is written to, so a
#: definition cannot introduce a column name a CSV reader would mangle.
LABEL_COLUMN_PATTERN = r"^[A-Za-z0-9._-]+$"

#: The operators that compare against one bound, and the ones that compare against
#: an interval. Kept as data because this pairing is the whole of a criterion's
#: argument syntax, and it is enforced in both directions.
SINGLE_BOUND_OPERATORS: frozenset[RiskCriterionOperator] = frozenset(
    {
        RiskCriterionOperator.LT,
        RiskCriterionOperator.LTE,
        RiskCriterionOperator.GT,
        RiskCriterionOperator.GTE,
        RiskCriterionOperator.EQ,
        RiskCriterionOperator.NEQ,
    }
)

#: The operators that may be applied to a categorical variable. Ordering
#: comparisons against a value like "partial" are not wrong so much as
#: meaningless, and the catalogue refuses them rather than letting a definition
#: sort words.
CATEGORICAL_OPERATORS: frozenset[RiskCriterionOperator] = frozenset(
    {RiskCriterionOperator.EQ, RiskCriterionOperator.NEQ}
)


def _render_bound(bound: float | str | None) -> str:
    """A bound as it should read in a sentence or a CSV cell.

    Numbers are trimmed of trailing zeros so a bound a researcher typed as ``8.0``
    prints as ``8``; categorical values are passed through quoted only if they
    would otherwise be ambiguous.
    """
    if isinstance(bound, str):
        return f"'{bound}'" if " " in bound else bound
    if bound is None:
        return "unset"
    return f"{bound:g}"


class RiskLabel(BaseModel):
    """One class in a definition's vocabulary, and where it sits in the ordering.

    ``rank`` exists because risk is ordinal: a rule that escalates to "act now"
    over "watch" is different from one that escalates the other way, and the
    difference has to be stated rather than inferred from the order the classes
    happen to be written in. Ranks are required to be ``0..n-1`` with no gaps, so
    "the most severe matching criterion" is always well defined.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    name: str = Field(
        min_length=1,
        max_length=40,
        pattern=RISK_LABEL_PATTERN,
        description=(
            "The class name, exactly as it will appear in the training file. "
            "Chosen by the researcher; this module supplies no vocabulary and no "
            "default."
        ),
    )
    rank: int = Field(
        ge=0,
        le=64,
        description=(
            "Position in the severity ordering, 0 being least severe. Must run 0 "
            "to n-1 with no gaps or repeats across the definition's classes."
        ),
    )
    description: str = Field(
        min_length=1,
        max_length=400,
        description=(
            "What this class means operationally, and what an operator should do "
            "about it. Required: a class name that carries its own meaning needs "
            "no explanation, and one that does not is exactly the case that needs "
            "it."
        ),
    )


class RiskCriterion(BaseModel):
    """One comparison against one measurable variable, and what it implies.

    The unit is optional but checked when present. A criterion written against
    millimetres while the variable is in g/L is a plausible-looking rule that
    compares 20 against a salinity, and the class distribution it produces would
    then be a fact about the unit mistake rather than about any pan.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    variable: str = Field(
        min_length=1,
        max_length=64,
        description="A variable from the risk variable catalogue, by name.",
    )
    operator: RiskCriterionOperator = Field(
        description="The comparison to make. A closed vocabulary, not an expression."
    )
    threshold: float | str | None = Field(
        default=None,
        description=(
            "The bound, for a single-value operator. A number for a numeric "
            "variable; a string for a categorical one, so a rule can be written "
            "against a recorded outcome such as 'lost' as readily as against a "
            "salinity. Mixing the two is caught at write time against the variable "
            "catalogue, not here, because this model does not know which catalogue "
            "an entry came from."
        ),
    )
    lower_bound: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description=(
            "The inclusive lower bound, for the interval operators. Numeric only: "
            "an interval over a categorical variable is an ordering nobody agreed "
            "to."
        ),
    )
    upper_bound: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description="The inclusive upper bound, for the interval operators. Numeric only.",
    )
    label: str = Field(
        min_length=1,
        max_length=40,
        pattern=RISK_LABEL_PATTERN,
        description="The class this criterion assigns when it matches.",
    )
    rationale: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "Why this variable, this comparison and this bound. Required on every "
            "criterion: it is the part a reviewer can disagree with, and without it "
            "a definition is a table of numbers."
        ),
    )
    unit: str | None = Field(
        default=None,
        max_length=16,
        description=(
            "Optional assertion of the variable's unit. When given it must match "
            "the catalogue, so a unit mistake is a 422 rather than a plausible "
            "class distribution."
        ),
    )

    @model_validator(mode="after")
    def _bounds_match_the_operator(self) -> RiskCriterion:
        """A criterion carries the arguments its operator actually takes.

        Both directions are enforced. Without the second check an interval
        operator could be handed a threshold that is silently ignored, leaving a
        definition that looks as though it constrains a range while constraining
        a point.
        """
        if self.operator in SINGLE_BOUND_OPERATORS:
            if self.threshold is None:
                raise ValueError(
                    f"operator {self.operator.value!r} compares against a single "
                    "threshold, and none was given"
                )
            if self.lower_bound is not None or self.upper_bound is not None:
                raise ValueError(
                    f"operator {self.operator.value!r} takes a threshold, so "
                    "lower_bound/upper_bound have no meaning here"
                )
            return self
        if self.threshold is not None:
            raise ValueError(
                f"operator {self.operator.value!r} compares against an interval, "
                "so a single threshold would be silently ignored; use lower_bound "
                "and upper_bound"
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
        """The comparison, written out, for a report or a CSV cell."""
        if self.operator in SINGLE_BOUND_OPERATORS:
            return f"{self.variable} {self.operator.value} {_render_bound(self.threshold)}"
        symbol = (
            "between" if self.operator is RiskCriterionOperator.BETWEEN else "outside"
        )
        return (
            f"{self.variable} {symbol} "
            f"[{_render_bound(self.lower_bound)}, {_render_bound(self.upper_bound)}]"
        )


class RiskDefinitionDraft(BaseModel):
    """What a researcher supplies, before the service assigns it an identity.

    Separated from :class:`RiskDefinition` because three of a stored definition's
    fields are the service's to derive and cannot honestly be asked of a client:
    ``specification_hash`` depends on the rest of the document, ``version``
    depends on the stored history, and ``definition_id`` is a database concern.
    A client that had to compute the hash would be reimplementing the service's
    canonical encoding, and a client that guessed it would be storing a rule whose
    cited identity does not describe it.

    This is also the model an HTTP request body validates against, which is why it
    carries none of the audit fields: a rule's author, timestamp and history are
    not things a request can assert.
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
    label_column: str = Field(
        default="label_operational_risk",
        min_length=1,
        max_length=64,
        pattern=LABEL_COLUMN_PATTERN,
        description="The dataset column this definition's label is written to.",
    )
    classes: list[RiskLabel] = Field(
        min_length=2,
        max_length=16,
        description=(
            "The complete label vocabulary, ordered by rank. At least two, because "
            "a rule with one class does not discriminate and a model fitted to it "
            "has nothing to predict."
        ),
    )
    criteria: list[RiskCriterion] = Field(
        min_length=1,
        max_length=32,
        description=(
            "The comparisons, in declaration order. Order is part of the definition "
            "for the first_match combinator and irrelevant for highest_rank, and it "
            "is hashed either way so it cannot drift silently."
        ),
    )
    combinator: RiskCombinator = Field(
        default=RiskCombinator.HIGHEST_RANK,
        description="How several matching criteria resolve to one label.",
    )
    unmatched_label: str | None = Field(
        default=None,
        max_length=40,
        pattern=RISK_LABEL_PATTERN,
        description=(
            "The class to assign when no criterion matches, or null to leave the row "
            "unlabelled with a reason. Null is a real choice and has to be made "
            "explicitly: a row whose criteria all miss has no severity that the "
            "researcher's own rule supports, and quietly assigning it the lowest "
            "class would be the module inventing a judgement nobody made."
        ),
    )
    operational_rationale: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "Why this rule exists and what decision it supports. Required on the "
            "definition as a whole, over and above each criterion's own rationale."
        ),
    )
    authored_by: str | None = Field(
        default=None,
        max_length=120,
        description="Who wrote the rule. Context for a reader, never a credential.",
    )

    @field_validator("name", "authored_by", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> Any:
        return normalize_identifier(value) if isinstance(value, str) else value

    def as_definition(self) -> RiskDefinition:
        """Build the storable document, leaving identity to the service.

        ``variables_used`` and ``label_basis`` are left empty here and filled by
        :func:`~app.services.risk_evaluation.definition_for_variables`, because
        both are derived from the criteria and deriving them once, in one place,
        is what stops the stored document disagreeing with the rules it was built
        from.
        """
        return RiskDefinition(
            name=self.name,
            label_column=self.label_column,
            classes=self.classes,
            criteria=self.criteria,
            combinator=self.combinator,
            unmatched_label=self.unmatched_label,
            operational_rationale=self.operational_rationale,
            authored_by=self.authored_by,
            # A placeholder that satisfies the field's pattern; the service
            # overwrites it with the digest of the content it actually stored.
            specification_hash="sha256:" + "0" * 64,
        )


class RiskDefinition(BaseDocument):
    """One researcher's operational risk criteria, at one version.

    Immutable once written. The only way to change a threshold is to create a new
    version, which is what lets a fitted model be described as "fitted against
    this rule" for as long as the record exists.
    """

    model_config = ConfigDict(validate_assignment=True)

    definition_id: str = Field(
        default_factory=lambda: new_id("risk"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    name: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "A stable handle for the rule, so its versions form a history. Two "
            "versions of the same name are one rule revised, not two rules."
        ),
    )
    version: int = Field(
        default=1,
        ge=1,
        description=(
            "How many versions of this name have been written, in creation order. "
            "Counted per name, not per document."
        ),
    )
    label_column: str = Field(
        default="label_operational_risk",
        min_length=1,
        max_length=64,
        pattern=LABEL_COLUMN_PATTERN,
        description="The dataset column this definition's label is written to.",
    )
    classes: list[RiskLabel] = Field(
        min_length=2,
        max_length=16,
        description=(
            "The complete label vocabulary, ordered by rank. At least two, because "
            "a rule with one class does not discriminate and a model fitted to it "
            "has nothing to predict."
        ),
    )
    criteria: list[RiskCriterion] = Field(
        min_length=1,
        max_length=32,
        description=(
            "The comparisons, in declaration order. Order is part of the definition "
            "for the first_match combinator and irrelevant for highest_rank, and it "
            "is hashed either way so it cannot drift silently."
        ),
    )
    combinator: RiskCombinator = Field(
        default=RiskCombinator.HIGHEST_RANK,
        description="How several matching criteria resolve to one label.",
    )
    unmatched_label: str | None = Field(
        default=None,
        max_length=40,
        pattern=RISK_LABEL_PATTERN,
        description=(
            "The class to assign when no criterion matches, or null to leave the row "
            "unlabelled with a reason. Null is a real choice and has to be made "
            "explicitly: a row whose criteria all miss has no severity that the "
            "researcher's own rule supports, and quietly assigning it the lowest "
            "class would be the module inventing a judgement nobody made."
        ),
    )
    label_basis: RiskLabelBasis = Field(
        default=RiskLabelBasis.PROSPECTIVE,
        description=(
            "Whether the label was knowable before the outcome. Computed from the "
            "variables the criteria read and stored here so a reader never has to "
            "reconstruct it."
        ),
    )
    variables_used: list[str] = Field(
        default_factory=list,
        description=(
            "Every catalogued variable a criterion reads, sorted and de-duplicated. "
            "Derived rather than supplied, and stored so a dataset can report the "
            "variables a label depends on without loading the whole definition."
        ),
    )
    operational_rationale: str = Field(
        min_length=1,
        max_length=1000,
        description=(
            "Why this rule exists and what decision it supports. Required on the "
            "definition as a whole, over and above each criterion's own rationale."
        ),
    )
    authored_by: str | None = Field(
        default=None,
        max_length=120,
        description="Who wrote the rule. Context for a reader, never a credential.",
    )
    schema_version: int = Field(
        default=RISK_DEFINITION_SCHEMA_VERSION,
        ge=1,
        description="The document format version, hashed into the identity.",
    )
    specification_hash: str = Field(
        pattern=HASH_PATTERN,
        description=(
            "SHA-256 over the rule's content. Two definitions with the same hash are "
            "the same rule written twice, and the second write returns the first "
            "rather than producing a version that changes nothing."
        ),
    )
    supersedes_definition_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="The definition this version replaces, when there is one.",
    )
    created_at: UtcDateTime = Field(default_factory=utcnow)

    @field_validator("name", "authored_by", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> Any:
        return normalize_identifier(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _vocabulary_is_coherent(self) -> RiskDefinition:
        """The class list, the criteria and the fallback have to agree.

        Checked here rather than in the service so a definition read back from
        the database cannot hold a criterion pointing at a class that does not
        exist, which is the one defect that leaves a stored rule unevaluable
        while still looking complete.
        """
        names = [label.name for label in self.classes]
        if len(set(names)) != len(names):
            raise ValueError("class names must be unique within a definition")
        ranks = sorted(label.rank for label in self.classes)
        if ranks != list(range(len(self.classes))):
            raise ValueError(
                f"class ranks must be 0 to {len(self.classes) - 1} with no gaps or "
                f"repeats; got {ranks}"
            )
        for criterion in self.criteria:
            if criterion.label not in names:
                raise ValueError(
                    f"the criterion on {criterion.variable!r} assigns "
                    f"{criterion.label!r}, which is not one of this definition's "
                    f"classes: {', '.join(names)}"
                )
        if self.unmatched_label is not None and self.unmatched_label not in names:
            raise ValueError(
                f"unmatched_label {self.unmatched_label!r} is not one of this "
                f"definition's classes: {', '.join(names)}"
            )
        return self

    @property
    def class_names(self) -> tuple[str, ...]:
        """The vocabulary, least severe first."""
        return tuple(
            label.name for label in sorted(self.classes, key=lambda item: item.rank)
        )

    @property
    def class_ranks(self) -> dict[str, int]:
        return {label.name: label.rank for label in self.classes}

    @property
    def class_descriptions(self) -> dict[str, str]:
        return {label.name: label.description for label in self.classes}

    @property
    def is_retrospective(self) -> bool:
        """Whether the rule reads anything measured after the anchor."""
        return self.label_basis is RiskLabelBasis.RETROSPECTIVE

    def as_hashed_specification(self) -> dict[str, Any]:
        """Exactly the content this definition's identity is computed from.

        Deliberately excludes ``name``, ``version``, ``authored_by`` and
        ``created_at``. Those say when the rule was written and who wrote it; the
        identity of a *rule* is what it says, and re-posting an unchanged rule as
        a new version would otherwise produce a second document that changes
        nothing about any label.
        """
        return {
            "schema_version": self.schema_version,
            "label_column": self.label_column,
            "classes": [
                {
                    "name": label.name,
                    "rank": label.rank,
                    "description": label.description,
                }
                for label in sorted(self.classes, key=lambda item: item.rank)
            ],
            "criteria": [
                criterion.model_dump(mode="json") for criterion in self.criteria
            ],
            "combinator": self.combinator.value,
            "unmatched_label": self.unmatched_label,
            "label_basis": self.label_basis.value,
            "operational_rationale": self.operational_rationale,
        }

    @property
    def short_hash(self) -> str:
        """The leading hex characters, without the algorithm prefix."""
        return self.specification_hash.split(":", 1)[1][:12]


class RiskSourceVariable(BaseModel):
    """One variable a definition read, and what it was worth on this row.

    Carried per row rather than per definition because the value is the point: a
    label with its rule but not its inputs cannot be re-checked against the pan
    it was assigned to, and one rule is satisfied by wildly different numbers on
    different rows.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    variable: str = Field(min_length=1, max_length=64)
    value: float | str | None = Field(
        default=None,
        description="The value read, or null when no record supplied one.",
    )
    unit: str | None = Field(default=None, max_length=16)
    leakage_class: LeakageClass = Field(
        description=(
            "How the value relates in time to the anchor. Stored per row so the "
            "causal argument travels with the data rather than with the definition "
            "it came from."
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


class RiskCriterionMatch(BaseModel):
    """One criterion that fired on a row, with the comparison that fired it."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    variable: str = Field(min_length=1, max_length=64)
    operator: RiskCriterionOperator
    comparator: str = Field(
        min_length=1,
        max_length=80,
        description="The comparison written out, so the cell is readable on its own.",
    )
    value: float | str | None = None
    threshold: float | str | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    label: str = Field(min_length=1, max_length=40)


class RiskEvaluation(BaseModel):
    """The label derived for one row, and everything needed to re-derive it.

    This is the block a training record keeps. Without ``definition_id`` and
    ``definition_version`` the label is unattributable; without
    ``source_variables`` the rule cannot be checked against the row it fired on.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    definition_id: str = Field(min_length=1, max_length=64)
    definition_name: str = Field(min_length=1, max_length=120)
    definition_version: int = Field(ge=1)
    specification_hash: str = Field(pattern=HASH_PATTERN)
    label_column: str = Field(min_length=1, max_length=64)
    label: str | None = Field(
        default=None,
        max_length=40,
        description="The assigned class, or null when the rule assigned none.",
    )
    label_rank: int | None = Field(default=None, ge=0)
    label_basis: RiskLabelBasis
    source_variables: list[RiskSourceVariable] = Field(
        default_factory=list,
        description=(
            "Every variable the definition's criteria read, with the value found. "
            "Sorted by name so two runs over the same row produce the same record."
        ),
    )
    matched_criteria: list[RiskCriterionMatch] = Field(
        default_factory=list,
        description=(
            "Every criterion that fired, not only the deciding one. A row that "
            "tripped three criteria and was labelled by the most severe of them is "
            "a different situation from one that tripped exactly one, and a model "
            "trained on both should still be able to tell them apart afterwards."
        ),
    )
    unlabelled_reason: str | None = Field(
        default=None,
        max_length=300,
        description="Why no class could be assigned, when none was.",
    )

    @property
    def is_labelled(self) -> bool:
        return self.label is not None

    @property
    def uses_post_anchor_variable(self) -> bool:
        """Whether any variable read here was measured after the anchor.

        Computed from the recorded leakage classes rather than read off the
        definition, so Module 07's audit can check a derived label row by row
        without having to trust how the definition was classified when written.
        """
        return any(
            not variable.leakage_class.is_known_at_anchor
            for variable in self.source_variables
        )

    def source_variable_map(self) -> dict[str, float | str | None]:
        return {variable.variable: variable.value for variable in self.source_variables}

    def source_variables_text(self) -> str:
        """The variables as one cell, in the style of ``missing_features``.

        Unavailable variables keep their place with the reason where the value
        would be, so a reader can see that a rule was short of an input rather
        than that it was satisfied without one.
        """
        cells: list[str] = []
        for variable in self.source_variables:
            if variable.value is None:
                reason = variable.reason or "no value was available"
                cells.append(f"{variable.variable}=<unset: {reason}>")
            else:
                cells.append(f"{variable.variable}={variable.value}")
        return "; ".join(cells)

    def matched_criteria_text(self) -> str:
        return "; ".join(
            f"{match.comparator} -> {match.label}" for match in self.matched_criteria
        )


class RiskClassShare(BaseModel):
    """How many rows carry one class, and what that class means."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    label: str = Field(min_length=1, max_length=40)
    rank: int = Field(ge=0)
    description: str = Field(min_length=1, max_length=400)
    count: int = Field(ge=0)
    percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        allow_inf_nan=False,
        description=(
            "Null when nothing was labelled, because then there is nothing to "
            "divide by."
        ),
    )


class RiskClassDistribution(BaseModel):
    """The shape of a rule's labels over the stored records, before any training.

    This is the document a researcher is asked to read *before* a model is fitted,
    because the distribution is a fact about the rule and the pans together and
    neither is visible from a fitted model. A rule whose classes come out 97/2/1
    produces an accuracy figure dominated by whichever class happened to be the
    majority, and the only place that is catchable is here.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    definition_id: str = Field(min_length=1, max_length=64)
    definition_name: str = Field(min_length=1, max_length=120)
    definition_version: int = Field(ge=1)
    specification_hash: str = Field(pattern=HASH_PATTERN)
    label_column: str = Field(min_length=1, max_length=64)
    label_basis: RiskLabelBasis = Field(
        description=(
            "Whether these labels were knowable before the outcome. A "
            "retrospective distribution describes what happened; it cannot "
            "forecast it."
        )
    )
    row_count: int = Field(ge=0, description="Rows evaluated, labelled or not.")
    labelled_count: int = Field(ge=0)
    unlabelled_count: int = Field(ge=0)
    unlabelled_reasons: dict[str, int] = Field(
        default_factory=dict,
        description="Why rows went unlabelled, counted by reason.",
    )
    classes: list[RiskClassShare] = Field(
        default_factory=list,
        description=(
            "Every declared class, in rank order, including any that were never "
            "observed. A class missing from the list cannot be told apart from a "
            "class that was forgotten."
        ),
    )
    unassigned_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Rows assigned the fallback class, where the definition declares one."
        ),
    )
    unobserved_classes: list[str] = Field(
        default_factory=list,
        description=(
            "Declared classes no row landed in. A model cannot be evaluated against "
            "a class no event produced, which is a different problem from a class "
            "that is merely rare."
        ),
    )
    populated_class_count: int = Field(
        default=0,
        ge=0,
        description=(
            "How many declared classes received at least one row. Reported "
            "separately from the class list because a rule whose criteria matched "
            "one class out of three has a distribution, and it is not a usable one."
        ),
    )
    imbalance_ratio: float | None = Field(
        default=None,
        ge=1.0,
        allow_inf_nan=False,
        description=(
            "Largest observed class share over the smallest populated one. Null when "
            "a declared class holds no rows at all, because a ratio against zero is "
            "not a number."
        ),
    )
    rarest_class: str | None = None
    rarest_class_count: int | None = Field(default=None, ge=0)
    most_common_class: str | None = None
    most_common_class_count: int | None = Field(default=None, ge=0)
    severely_imbalanced: bool = Field(
        default=False,
        description=(
            "True when the imbalance ratio reaches the documented threshold, or the "
            "rarest populated class holds too few rows to train or evaluate on."
        ),
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="What a researcher should decide about before fitting anything.",
    )
    note: str = Field(
        min_length=1,
        max_length=500,
        description="What this distribution means, in a sentence.",
    )
    generated_at: UtcDateTime = Field(default_factory=utcnow)

    @property
    def is_usable_for_training(self) -> bool:
        """Whether this distribution supports fitting and evaluating a model.

        Four conditions, and each is a different failure rather than a stricter
        version of the same one: nothing was labelled; a declared class received
        no row at all, so nothing can be measured against it; everything landed
        in one class and any model would reproduce a constant; or the split is
        too lopsided or too thinly populated for a per-class figure to mean
        anything.

        An unobserved class counts against usability on its own, independently of
        how healthy the classes that *were* observed look. A 50/50 split between
        two of three declared classes would otherwise pass every numeric test
        here, which is exactly the case where a per-class report would look
        healthy while being silent on a third of the rule's own vocabulary.
        """
        return (
            self.labelled_count > 0
            and not self.unobserved_classes
            and self.populated_class_count >= 2
            and not self.severely_imbalanced
        )


__all__ = [
    "CATEGORICAL_OPERATORS",
    "LABEL_COLUMN_PATTERN",
    "RISK_DEFINITION_SCHEMA_VERSION",
    "RISK_LABEL_PATTERN",
    "SINGLE_BOUND_OPERATORS",
    "RiskClassDistribution",
    "RiskClassShare",
    "RiskCriterion",
    "RiskCriterionMatch",
    "RiskDefinition",
    "RiskDefinitionDraft",
    "RiskEvaluation",
    "RiskLabel",
    "RiskSourceVariable",
]
