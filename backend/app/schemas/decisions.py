"""Request and response shapes for Module 12, the decision-support engine.

**The field names are the operator's checklist.** A recommendation is returned
with ``recommendation``, ``reason``, ``inputs_used``, ``simulation_result``,
``ml_result``, ``rule_version`` and ``timestamp``, because those are the seven
things a person has to be able to find on a recommendation before acting on it.
The vocabulary is not incidental: a response whose fields were named after the
database columns would force every reader to know the storage layout to answer
"what is being recommended, and on what evidence".

**The three kinds of evidence are three fields and never one.** ``simulation_result``
is physics and says ``is_ml: false``; ``ml_result`` is a model's estimate and is
null when there was none; ``inputs_used`` says where each input came from. A client
that wants to colour the numbers differently can, and a client that ignores the
split still cannot misread one as the other.

**``ml_result: null`` is a documented state, not a failure.** When it is null the
response carries ``basis`` of ``physics_and_rules`` and ``notice`` containing the
exact label this project requires of that case. The model may have been absent, may
have been class-only, or may not have been consulted because the rule reads no
prediction variable; ``notice`` says which, because those are three different
operational situations and an operator's response to them differs.

**There is no confidence field on the recommendation.** A rule either matched
decisively or it did not, and a number in that position would be read beside
``ml_result.confidence`` as though the two were the same kind of quantity. The
model's own confidence appears only inside ``ml_result``, where it is the model's.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.decision_rule import DecisionRule, DecisionRuleDraft
from app.models.enums import DecisionAction, DecisionBasis, RecommendationPriority
from app.models.recommendation import (
    DecisionPredictionSummary,
    DecisionSimulationSummary,
    DecisionTwinSummary,
    Recommendation,
)
from app.services.decision_rules import REUSED_REASON
from app.services.mass_balance import SIMULATION_ASSUMPTIONS, SIMULATOR_DISCLAIMER

#: The action vocabulary, published so a client can build its control without
#: hard-coding the four strings. It is closed, and this list is the whole of it.
DECISION_ACTIONS: tuple[str, ...] = tuple(action.value for action in DecisionAction)


# -- the catalogue ----------------------------------------------------------


class DecisionVariableResponse(BaseModel):
    """One quantity a rule may reference, and where its value comes from."""

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal["numeric", "categorical"]
    unit: str | None = None
    reads_from: str = Field(
        description=(
            "Which part of the system the value comes from: 'twin' for a recorded "
            "observation or forecast, 'simulation' for a mass-balance output, "
            "'prediction' for a model's estimate, or 'pan_registration' for a static "
            "property."
        )
    )
    source_collection: str
    source_field: str
    operational_meaning: str
    derivation: str
    nullable_reason: str | None = None
    requires_model: bool
    requires_simulation: bool


class DecisionCatalogResponse(BaseModel):
    """Everything a rule author may reference, served from the enforced catalogue."""

    model_config = ConfigDict(extra="forbid")

    variables: list[DecisionVariableResponse]
    actions: list[str]
    policy: dict[str, Any]


# -- the rules --------------------------------------------------------------


class DecisionActionResponse(BaseModel):
    """One action in a rule's vocabulary, with its rank and declared urgency."""

    model_config = ConfigDict(extra="forbid")

    action: DecisionAction
    rank: int
    description: str
    priority: RecommendationPriority


class DecisionCriterionResponse(BaseModel):
    """One stored comparison, written out so it is readable without the source.

    Serves two views over the same thing. On a rule it describes what the author
    wrote; on a recommendation it describes what actually fired, and the two
    differ in exactly the fields that matter for an audit: ``value`` and
    ``threshold`` are then resolved for one pan rather than merely specified, and
    ``bound_variable`` says whether the bound was a number the rule asserted or a
    field the operator configured.

    That difference is why the criteria are published as structured fields at all.
    ``DecisionAdviceResponse.matched_criteria`` remains a sentence for a reader;
    this is what lets a client render a threshold without parsing prose, and lets a
    reviewer see which of the two kinds of bound produced the action.
    """

    model_config = ConfigDict(extra="forbid")

    variable: str
    operator: str
    comparator: str
    action: DecisionAction
    #: Required on a rule, where every criterion must justify itself. Null on a
    #: recommendation read back from an evaluation stored before the rationale was
    #: copied onto the match, rather than reconstructed from a rule that may since
    #: have been revised.
    rationale: str | None = None
    unit: str | None = None
    threshold: float | str | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    value: float | str | None = Field(
        default=None,
        description="The value found, on a recommendation. Null on a stored rule.",
    )
    source: str | None = Field(
        default=None,
        description=(
            "Which part of the system supplied the value. Null on a stored rule, "
            "where the variable itself already names its source in the catalogue."
        ),
    )
    bound_variable: str | None = Field(
        default=None,
        description=(
            "The configured field the bound was read from, when the rule named one. "
            "Its presence is what distinguishes a figure the operator chose from a "
            "figure the rule asserted, which cannot be recovered from the resolved "
            "threshold alone."
        ),
    )

    @classmethod
    def from_match(cls, match: Any, rationale: str | None = None) -> DecisionCriterionResponse:
        """A fired criterion, with its bound resolved for the pan it fired on."""
        return cls(
            variable=match.variable,
            source=match.source.value,
            operator=match.operator.value,
            comparator=match.comparator,
            value=match.value,
            threshold=match.threshold,
            bound_variable=match.bound_variable,
            lower_bound=match.lower_bound,
            upper_bound=match.upper_bound,
            action=match.action,
            rationale=rationale,
        )


class DecisionRuleRead(BaseModel):
    """A stored rule version, as served."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    name: str
    version: int
    actions: list[DecisionActionResponse]
    criteria: list[DecisionCriterionResponse]
    combinator: str
    unmatched_action: DecisionAction | None
    uses_ml: bool
    variables_used: list[str]
    sources_used: list[str]
    operational_rationale: str
    authored_by: str | None
    schema_version: int
    specification_hash: str
    short_hash: str
    supersedes_rule_id: str | None
    created_at: datetime

    @classmethod
    def from_rule(cls, rule: DecisionRule) -> DecisionRuleRead:
        return cls(
            rule_id=rule.rule_id,
            name=rule.name,
            version=rule.version,
            actions=[
                DecisionActionResponse(
                    action=entry.action,
                    rank=entry.rank,
                    description=entry.description,
                    priority=entry.priority,
                )
                for entry in sorted(rule.actions, key=lambda item: item.rank)
            ],
            criteria=[
                DecisionCriterionResponse(
                    variable=criterion.variable,
                    operator=criterion.operator.value,
                    comparator=criterion.comparator_text(),
                    action=criterion.action,
                    rationale=criterion.rationale,
                    unit=criterion.unit,
                    threshold=criterion.threshold,
                    lower_bound=criterion.lower_bound,
                    upper_bound=criterion.upper_bound,
                    # Carried through, because a stored rule whose criteria all read
                    # their bounds from configuration must not be served looking as
                    # though it asserted no bound at all. Every threshold would come
                    # back null and the only trace of where each came from would be
                    # the comparator string, which a client would have to parse.
                    bound_variable=criterion.bound_variable,
                )
                for criterion in rule.criteria
            ],
            combinator=rule.combinator.value,
            unmatched_action=rule.unmatched_action,
            uses_ml=rule.uses_ml,
            variables_used=list(rule.variables_used),
            sources_used=[source.value for source in rule.sources_used],
            operational_rationale=rule.operational_rationale,
            authored_by=rule.authored_by,
            schema_version=rule.schema_version,
            specification_hash=rule.specification_hash,
            short_hash=rule.short_hash,
            supersedes_rule_id=rule.supersedes_rule_id,
            created_at=rule.created_at,
        )


class DecisionRuleListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    rules: list[DecisionRuleRead]


class DecisionRuleCreateResponse(BaseModel):
    """The outcome of a rule write, distinguishing a write from a re-post."""

    model_config = ConfigDict(extra="forbid")

    created: bool
    reason: str | None = Field(
        default=None,
        description=(
            f"Set to {REUSED_REASON!r} when the identical rule already existed, so a "
            "caller can tell one rule from the same rule written twice."
        ),
    )
    rule: DecisionRuleRead

    @classmethod
    def from_outcome(cls, outcome: Any) -> DecisionRuleCreateResponse:
        return cls(
            created=outcome.created,
            reason=outcome.reason,
            rule=DecisionRuleRead.from_rule(outcome.rule),
        )


class DecisionRuleVersionsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int
    rules: list[DecisionRuleRead]


# -- the recommendation -----------------------------------------------------


class DecisionInputResponse(BaseModel):
    """One variable the rule read, with the value found or the reason there was none."""

    model_config = ConfigDict(extra="forbid")

    variable: str
    value: float | str | None = None
    unit: str | None = None
    source: str = Field(
        description=(
            "Where the value came from: 'twin', 'simulation', 'prediction' or "
            "'pan_registration'. The separation the module is built on, restated on "
            "every input."
        )
    )
    available: bool
    reason: str | None = None


class DecisionTwinResponse(BaseModel):
    """The recorded state the recommendation was made from."""

    model_config = ConfigDict(extra="forbid")

    twin_id: str
    as_of: datetime
    provenance: str
    salinity_g_l: float | None = None
    water_depth_cm: float | None = None
    forecast_rainfall_mm: float | None = None
    observation_id: str | None = None
    observation_timestamp: datetime | None = None
    weather_forecast_id: str | None = None
    weather_forecast_timestamp: datetime | None = None
    missing_fields: list[str] = Field(default_factory=list)
    is_demonstration: bool = Field(
        default=False,
        description=(
            "True when any record behind this recommendation was entered to "
            "demonstrate the workflow. A client should show a DEMONSTRATION ONLY "
            "label on the strength of this flag."
        ),
    )
    demonstration_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Which inputs were demonstrations, named by the twin field that carried "
            "the demonstration source. Empty for a real recommendation."
        ),
    )

    @classmethod
    def from_summary(cls, summary: DecisionTwinSummary) -> DecisionTwinResponse:
        return cls(**summary.model_dump())


class DecisionSimulationResponse(BaseModel):
    """What the mass balance computed, with its assumptions and disclaimer attached."""

    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    model_name: str
    is_ml: bool = Field(
        description="Always false. Present so a client can assert on it rather than assume it.",
    )
    rainfall_mm: float
    rainfall_source: str
    initial_salinity_g_l: float
    initial_depth_cm: float
    post_rain_salinity_g_l: float
    post_rain_depth_cm: float
    salinity_drop_g_l: float
    dilution_percent: float | None = None
    dilution_percent_reason: str | None = None
    assumptions: list[str] = Field(default_factory=lambda: list(SIMULATION_ASSUMPTIONS))
    disclaimer: str = SIMULATOR_DISCLAIMER

    @classmethod
    def from_summary(cls, summary: DecisionSimulationSummary) -> DecisionSimulationResponse:
        return cls(**summary.model_dump())


class DecisionMlResponse(BaseModel):
    """The stored model estimate, exactly as the model reported it."""

    model_config = ConfigDict(extra="forbid")

    prediction_id: str
    model_id: str | None = None
    model_version: str
    target: str
    predicted_class: str | None = None
    probability: float | None = None
    confidence: float | None = None
    probability_is_null_reason: str | None = None
    predicted_at: datetime
    twin_as_of: datetime | None = Field(
        default=None,
        description=(
            "The instant of the twin state the estimate was made from. Distinct from "
            "``predicted_at``, which is when the model was run."
        ),
    )
    input_snapshot_id: str | None = None
    matches_recommendation_snapshot: bool = Field(
        description=(
            "Whether the model was given exactly the twin state this recommendation "
            "used. False means the estimate is older than the state, which is worth "
            "knowing and is not a reason to withhold the recommendation."
        )
    )
    explanation_id: str | None = Field(
        default=None,
        description=(
            "The Module 11 explanation of this prediction, when one exists. Null when "
            "the model was not explained."
        ),
    )

    @classmethod
    def from_summary(
        cls, summary: DecisionPredictionSummary, *, explanation_id: str | None = None
    ) -> DecisionMlResponse:
        return cls(**{**summary.model_dump(), "explanation_id": explanation_id})


class DecisionAdviceResponse(BaseModel):
    """One recommendation, with every piece of evidence the operator needs.

    The seven fields an operator must find before acting are named as the project
    names them: ``recommendation``, ``reason``, ``inputs_used``,
    ``simulation_result``, ``ml_result``, ``rule_version`` and ``timestamp``. The
    identifiers around them exist so a client can link back to the records they
    came from, and ``basis`` and ``notice`` say in words what kind of
    recommendation this is.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation: DecisionAction = Field(
        description=(
            "The operational action recommended. One of exactly four: "
            + ", ".join(f"'{action}'" for action in DECISION_ACTIONS)
            + "."
        )
    )
    reason: str = Field(
        description="Why this action, written from the criteria that matched."
    )
    inputs_used: list[DecisionInputResponse] = Field(
        description=(
            "Every variable the rule read, with the value found or the reason there "
            "was none, and where each came from."
        )
    )
    simulation_result: DecisionSimulationResponse = Field(
        description=(
            "The physics answer: what the forecast rain does to this pan's salinity "
            "and depth. Computed by the mass balance, not by a model."
        )
    )
    ml_result: DecisionMlResponse | None = Field(
        default=None,
        description=(
            "The model's answer, copied from the stored prediction. Null when no "
            "operational-risk prediction was read, and see ``basis`` and ``notice`` "
            "for the exact statement of that case."
        ),
    )
    rule_version: str = Field(
        description=(
            "The rule that produced this action, as 'name vN' plus its content hash, "
            "so a recommendation cites the exact rule content and not only its name."
        )
    )
    timestamp: datetime = Field(description="When the recommendation was issued.")

    basis: DecisionBasis
    notice: str
    recommendation_id: str
    pan_id: str
    priority: RecommendationPriority
    rule_id: str
    rule_name: str
    rule_version_number: int
    specification_hash: str
    matched_criteria: str
    criteria: list[DecisionCriterionResponse] = Field(
        default_factory=list,
        description=(
            "Every criterion that fired, as structured fields, each with the bound "
            "as it resolved for this pan and the configured field it was read from "
            "where the rule named one. Empty when the action came from the rule's "
            "declared fallback, in which case ``matched_criteria`` says so."
        ),
    )
    twin: DecisionTwinResponse
    title: str
    body: str
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def _ml_and_basis_agree(self) -> DecisionAdviceResponse:
        """The basis and the ML block have to tell the same story.

        Checked at the response boundary as well as on the stored document, because
        a client renders the basis as a label and reads the ML block as evidence; a
        response where the two disagreed would be showing the reader a claim its own
        contents contradict.
        """
        if self.basis is DecisionBasis.PHYSICS_AND_ML and self.ml_result is None:
            raise ValueError(
                "basis is physics_and_ml but ml_result is null, so the response "
                "claims a model-informed recommendation while carrying no model "
                "evidence"
            )
        if self.basis is DecisionBasis.PHYSICS_AND_RULES and self.ml_result is not None:
            raise ValueError(
                "basis is physics_and_rules but ml_result is present, so the response "
                "claims the model was not read while carrying its estimate"
            )
        return self

    @classmethod
    def from_recommendation(cls, stored: Recommendation) -> DecisionAdviceResponse:
        """Render a stored recommendation from the record itself.

        ``inputs_used`` comes from the stored evaluation rather than from the text
        beside it, because the source of each reading — twin, simulation, prediction
        or registration — is the field a client needs in order to colour the evidence
        correctly, and text cannot carry it without a grammar invented to recover it.
        """
        return cls(
            recommendation=stored.action,
            reason=stored.reason,
            inputs_used=[
                DecisionInputResponse(
                    variable=reading.variable,
                    value=reading.value,
                    unit=reading.unit,
                    source=reading.source.value,
                    available=reading.is_available,
                    reason=reading.reason,
                )
                for reading in stored.evaluation.input_readings
            ],
            simulation_result=DecisionSimulationResponse.from_summary(stored.simulation),
            ml_result=(
                DecisionMlResponse.from_summary(
                    stored.ml, explanation_id=stored.explanation_id
                )
                if stored.ml is not None
                else None
            ),
            rule_version=(
                f"{stored.rule_name} v{stored.rule_version} "
                f"({stored.specification_hash})"
            ),
            timestamp=stored.issued_at,
            basis=stored.basis,
            notice=stored.notice,
            recommendation_id=stored.recommendation_id,
            pan_id=stored.pan_id,
            priority=stored.priority,
            rule_id=stored.rule_id,
            rule_name=stored.rule_name,
            rule_version_number=stored.rule_version,
            specification_hash=stored.specification_hash,
            matched_criteria=stored.matched_criteria_text,
            criteria=[
                DecisionCriterionResponse.from_match(
                    match, rationale=_rationale_for(stored, match.variable)
                )
                for match in (stored.evaluation.matched_criteria if stored.evaluation else [])
            ],
            twin=DecisionTwinResponse.from_summary(stored.twin),
            title=stored.title,
            body=stored.body,
            expires_at=stored.expires_at,
        )


def _rationale_for(stored: Any, variable: str) -> str | None:
    """The author's stated reason for a criterion, from the stored evaluation.

    The evaluation keeps which criteria fired but not what the author said about
    each one, and the rationale is the part a reviewer can disagree with -- so it
    is looked up from the rule that produced this recommendation when the rule is
    still available, and omitted rather than guessed when it is not.
    """
    evaluation = getattr(stored, "evaluation", None)
    if evaluation is None:
        return None
    return evaluation.criterion_rationales.get(variable)


class DecisionAdviceRequest(BaseModel):
    """What to advise on, and how far to reach for evidence.

    Carries no features, no model and no thresholds. The state comes from the twin,
    the physics from the mass balance, the model estimate from a stored prediction
    and the action from a written rule, and a request that could supply any of them
    would be able to state a recommendation's inputs as well as its conclusion.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pan_id: str = Field(min_length=1, max_length=64, examples=["PAN-001"])
    as_of: datetime | None = Field(
        default=None,
        description=(
            "Advise as the system believed at this instant, using the newest twin "
            "state recorded at or before it. Omit for the current state."
        ),
    )
    rainfall_mm: float | None = Field(
        default=None,
        ge=0,
        allow_inf_nan=False,
        description=(
            "An explicit what-if rainfall depth for the simulation, in millimetres. "
            "Omit to use the depth the twin's own forecast supplies; when given, the "
            "source is recorded as a request rather than a forecast."
        ),
    )
    rule_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Which stored rule version to apply. Omit for the most recently written "
            "rule of any name."
        ),
    )

    @field_validator("as_of")
    @classmethod
    def as_of_must_carry_an_offset(cls, value: datetime | None) -> datetime | None:
        """Reject a timestamp that does not say what timezone it is in.

        Every instant this request is compared against is an aware datetime, because
        that is how the twin states are stored. Comparing an aware instant to a
        naive one is not an approximation the engine can make: without an offset
        there is no way to know whether the caller meant UTC, the pan's local time,
        or their own, and each of those can select a different twin state. Guessing
        UTC would quietly answer a different question from the one asked, so a
        caller who omits the offset is refused instead.
        """
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError(
                "as_of must include a timezone offset, such as "
                "'2026-09-01T00:00:00Z' or '2026-09-01T05:30:00+05:30', so the "
                "instant can be compared with the recorded twin states"
            )
        return value


class DecisionLatestResponse(BaseModel):
    """The newest recommendation for a pan, or an explicit statement that none exists."""

    model_config = ConfigDict(extra="forbid")

    found: bool = Field(
        description=(
            "False when the pan has no recommendation on record. A normal state for a "
            "pan nobody has asked about, and distinct from a failure."
        )
    )
    advice: DecisionAdviceResponse | None


class DecisionLogResponse(BaseModel):
    """The advice log, newest first, optionally narrowed to a pan or an action."""

    model_config = ConfigDict(extra="forbid")

    count: int
    advice: list[DecisionAdviceResponse]


class DecisionRefusalResponse(BaseModel):
    """A recommendation that could not be issued, with a code to branch on."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        description=(
            "One of: no_twin_state, simulation_inputs_missing, no_rule_defined, "
            "rule_undecided, insufficient_evidence. The last is raised when the "
            "evidence exists but is too old to support a decision, and is kept "
            "distinct from rule_undecided so a client can tell 'the rule could not "
            "be evaluated' from 'the reading was not current'."
        )
    )
    detail: str
    pan_id: str | None = None
    rule_id: str | None = None


__all__ = [
    "DECISION_ACTIONS",
    "DecisionActionResponse",
    "DecisionAdviceRequest",
    "DecisionAdviceResponse",
    "DecisionCatalogResponse",
    "DecisionCriterionResponse",
    "DecisionInputResponse",
    "DecisionLatestResponse",
    "DecisionLogResponse",
    "DecisionMlResponse",
    "DecisionRefusalResponse",
    "DecisionRuleCreateResponse",
    "DecisionRuleDraft",
    "DecisionRuleListResponse",
    "DecisionRuleRead",
    "DecisionRuleVersionsResponse",
    "DecisionSimulationResponse",
    "DecisionTwinResponse",
    "DecisionVariableResponse",
]
