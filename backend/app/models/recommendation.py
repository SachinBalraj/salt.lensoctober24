"""Module 12 - one stored operational recommendation, with the evidence behind it.

A recommendation in this project is not a sentence. It is a claim that a specific
pan, in a specific recorded state, under a specific written rule, warranted a
specific action — and the record has to be able to demonstrate each of those four
things, because a recommendation an operator cannot trace is indistinguishable
from advice somebody made up.

**The four kinds of evidence are separate fields, never one blob.** Twin state,
the mass balance, the model's estimate and the rule's own argument are stored in
their own blocks with their own identifiers. They are merged into a single
``inputs`` dict only in the sense that a reader can see all of them at once; the
structure is what stops a simulated salinity from being read as a measured one, or
a model's probability from being read as a rule's certainty.

**The action is one of four, and it is not the model's to choose.** The
:class:`~app.models.enums.DecisionAction` vocabulary is closed. Which action a pan
gets is a function of the rule, and the rule is data with a version; the model
contributes an estimate that a criterion may read, never an action.

**A recommendation without a model estimate is a legitimate recommendation.** It
carries :attr:`Recommendation.basis` of ``physics_and_rules`` and the exact notice
required of that case, so an operator reading it cannot mistake a rule-based
judgement for a model-informed one. There is deliberately no ``confidence`` field
derived from the rule: a rule that matched is either decisive or it is not, and a
number there would invite comparison with ``ml_result.confidence`` and imply a
likelihood nobody computed.
"""

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_pan_id,
    utcnow,
)
from app.models.decision_rule import (
    MAX_CATALOGUE_NAME_CHARS,
    MAX_CATALOGUE_REASON_CHARS,
    MAX_COMPARATOR_CHARS,
    MAX_CRITERIA_PER_RULE,
    DecisionEvaluation,
)
from app.models.enums import DecisionAction, DecisionBasis, RecommendationPriority
from app.services.mass_balance import SIMULATION_ASSUMPTIONS, SIMULATOR_DISCLAIMER

#: The widest the engine's own audit text can get, derived from the rule bounds
#: rather than chosen.
#:
#: One input reading prints as ``<variable>=<unset: <reason>>`` or
#: ``<variable>=<value>``, joined to its neighbours by ``"; "``; one match prints as
#: ``<comparator> -> <action>``, joined the same way. The two audit cells are the
#: widest of these because the unset form carries a reason, and ``reason`` on the
#: recommendation quotes the input cell in full, which makes it the widest field
#: here.
#:
#: These limits exist to bound a single stored document, and the point of deriving
#: them is that the widest rule the schema accepts can still be recorded. Sized by
#: hand they were not: a 32-criterion rule whose criteria all matched produced more
#: than 2000 characters, and because the limit is enforced on assignment the
#: overflow surfaced as a 422 blaming the request's ``pan_id`` -- a legal answer
#: the server could not store, reported as a malformed request. The alternative to
#: raising the limit, truncating, would discard the very record the module exists to
#: keep, so the bound moves instead and the structured ``input_readings`` and
#: ``matched_criteria`` remain the record these cells render.
_AUDIT_SEPARATOR = 2
_MAX_INPUT_READINGS_TEXT = (
    MAX_CRITERIA_PER_RULE
    * (MAX_CATALOGUE_NAME_CHARS + len("=<unset: >") + MAX_CATALOGUE_REASON_CHARS)
    + _AUDIT_SEPARATOR * (MAX_CRITERIA_PER_RULE - 1)
)
_MAX_MATCHED_CRITERIA_TEXT = MAX_CRITERIA_PER_RULE * (
    MAX_COMPARATOR_CHARS + len(" -> ") + max(len(action.value) for action in DecisionAction)
) + _AUDIT_SEPARATOR * (MAX_CRITERIA_PER_RULE - 1)
#: Room for the input cell plus the twin, simulation, rule and model sentences the
#: engine writes around it.
MAX_REASON_CHARS = _MAX_INPUT_READINGS_TEXT + 2000


class DecisionTwinSummary(BaseModel):
    """The recorded state the recommendation was made from.

    The twin is not a summary of the recommendation's inputs; it is the object the
    other two blocks were derived from, so its identity is what makes the rest
    checkable. ``as_of`` is the twin's own assembly time, distinct from the
    timestamps of the underlying records.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    twin_id: str = Field(min_length=1, max_length=64)
    as_of: UtcDateTime = Field(validation_alias=AliasChoices("as_of", "timestamp"))
    provenance: str = Field(
        min_length=1,
        max_length=32,
        description="How the twin was assembled: observed, assimilated or simulated.",
    )
    salinity_g_l: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    water_depth_cm: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    forecast_rainfall_mm: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    observation_id: str | None = Field(default=None, max_length=64)
    observation_timestamp: UtcDateTime | None = None
    weather_forecast_id: str | None = Field(default=None, max_length=64)
    weather_forecast_timestamp: UtcDateTime | None = None
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Twin quantities no record supplied, so a gap is visible on the record.",
    )
    is_demonstration: bool = Field(
        default=False,
        description=(
            "True when any record behind this recommendation was entered to "
            "demonstrate the workflow rather than read from this pan. A client is "
            "expected to label the recommendation on the strength of this flag "
            "rather than infer it from the individual source strings, so that the "
            "label cannot be forgotten by a caller that only looked at the "
            "readings it happened to display."
        ),
    )
    demonstration_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Which inputs were demonstrations, named by their own source value. "
            "Empty for a recommendation built from real records."
        ),
    )


class DecisionSimulationSummary(BaseModel):
    """What the mass balance computed for this pan, at this rainfall.

    Carries the run id so the arithmetic can be replayed from the stored run rather
    than taken on trust, and the assumptions and disclaimer so the limits travel
    with the number rather than living in a docstring. ``is_ml`` is false and is
    present explicitly: a consumer that checks the flag should not have to infer
    that no model was involved.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    run_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "The stored simulation run this was read from. Null only when the run "
            "could not be persisted, in which case the inputs below are still the "
            "complete record of the computation."
        ),
    )
    model_name: str = Field(
        min_length=1,
        max_length=64,
        description="The balance that produced the figures, named as a model version.",
    )
    rainfall_mm: float = Field(ge=0, allow_inf_nan=False)
    rainfall_source: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Where the rainfall depth came from: the twin's stored forecast, or an "
            "explicit what-if depth supplied with the request."
        ),
    )
    initial_salinity_g_l: float = Field(ge=0, allow_inf_nan=False)
    initial_depth_cm: float = Field(gt=0, allow_inf_nan=False)
    post_rain_salinity_g_l: float = Field(ge=0, allow_inf_nan=False)
    post_rain_depth_cm: float = Field(gt=0, allow_inf_nan=False)
    salinity_drop_g_l: float = Field(allow_inf_nan=False)
    dilution_percent: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description=(
            "Null when the starting salinity was zero, which leaves the ratio "
            "undefined. Paired with ``dilution_percent_reason`` so the absence is "
            "explained rather than rendered as a zero."
        ),
    )
    dilution_percent_reason: str | None = Field(default=None, max_length=200)
    is_ml: bool = Field(
        default=False,
        description="Always false. Present so a consumer can assert on it.",
    )
    assumptions: list[str] = Field(default_factory=lambda: list(SIMULATION_ASSUMPTIONS))
    disclaimer: str = Field(default=SIMULATOR_DISCLAIMER, min_length=1, max_length=500)


class DecisionPredictionSummary(BaseModel):
    """What the stored model estimated, exactly as the model reported it.

    A copy of a stored prediction, not a new inference: the engine never scores
    anything. ``probability`` and ``confidence`` are null for a class-only model,
    and are never replaced by a rule's own agreement with the action, because a
    number in either field is read as a likelihood the model produced.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    prediction_id: str = Field(min_length=1, max_length=64)
    model_id: str | None = Field(default=None, max_length=64)
    model_version: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    predicted_class: str | None = Field(default=None, max_length=128)
    probability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    probability_is_null_reason: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Why the probability is absent, when it is. A class-only model reports a "
            "label and no likelihood, and the difference between 'no likelihood' and "
            "'likelihood of zero' has to be stated."
        ),
    )
    #: When the model was run, matching Module 10's own published meaning of
    #: ``predicted_at``. Kept apart from :attr:`twin_as_of`, which is the instant of
    #: the state the model was given: a prediction is normally made long after the
    #: state it describes, and a reader checking the two needs them apart.
    predicted_at: UtcDateTime = Field(
        validation_alias=AliasChoices("predicted_at", "timestamp"),
    )
    #: The instant of the twin state the estimate was made from, as recorded in the
    #: prediction's own snapshot. Distinct from ``predicted_at`` for the same reason.
    twin_as_of: UtcDateTime | None = Field(default=None)
    input_snapshot_id: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "The content hash of the snapshot the model was given. Reported so a "
            "reader can tell whether the estimate was made from the same twin state "
            "this recommendation used."
        ),
    )
    #: True when the estimate was made from the same twin snapshot this
    #: recommendation is based on, and false when it was made from an earlier state.
    #: Not a freshness judgement: a reader decides what staleness means, but the
    #: mismatch has to be visible.
    matches_recommendation_snapshot: bool = Field(
        default=False,
        description=(
            "Whether the prediction's snapshot id equals this recommendation's twin "
            "summary id. A recommendation may cite a slightly older estimate, but it "
            "must not present it as if it came from the current state."
        ),
    )


class Recommendation(BaseDocument):
    recommendation_id: str = Field(
        default_factory=lambda: new_id("rec"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    pan_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")

    #: The action. One of exactly four. Set on a decision record, and absent on a
    #: plain note: the two kinds of document this model carries are distinguished
    #: below, and neither is required to masquerade as the other.
    action: DecisionAction | None = Field(
        default=None,
        description=(
            "The operational action recommended, from the closed vocabulary. Null on "
            "a note that carries no decision."
        ),
    )
    #: The rule's own argument, in one sentence a human wrote. Derived from the
    #: matched criteria rather than composed fresh, so the stored reason is always a
    #: restatement of the rule that fired and never an independent judgement.
    reason: str | None = Field(
        default=None,
        max_length=MAX_REASON_CHARS,
        description="Why this action, written from the criteria that matched.",
    )
    basis: DecisionBasis | None = Field(
        default=None,
        description=(
            "Whether a model's estimate was read. ``physics_and_rules`` means it was "
            "not, and the record carries the notice required of that case."
        ),
    )
    notice: str | None = Field(
        default=None,
        # Raised from 300 to fit the model label, the demonstration label and the
        # evidence-age statement together. It was sized for the first of those
        # alone, before the other two existed, and the cap was then enforced by a
        # 422 at the API boundary -- so a longer notice did not merely read
        # badly, it made the endpoint refuse to return a valid recommendation.
        # A field that truncates a *required* label is worse than a long one, so
        # the cap is set to admit the full statement and nothing more.
        max_length=900,
        description=(
            "The exact statement of what this recommendation was built from. For a "
            "recommendation without a model estimate this is the project's required "
            "label, verbatim. Carries the demonstration label and the reading's age "
            "as well, so that neither has to be reconstructed by the reader."
        ),
    )

    # -- the three kinds of evidence, kept apart ----------------------------
    twin: DecisionTwinSummary | None = Field(
        default=None,
        description="The recorded state the recommendation was made from.",
    )
    simulation: DecisionSimulationSummary | None = Field(
        default=None,
        description="The mass-balance result for this pan at the rainfall used.",
    )
    ml: DecisionPredictionSummary | None = Field(
        default=None,
        description=(
            "The stored model estimate, or null when none was available. Null is the "
            "honest representation of a missing model, and it is what the "
            "``physics_and_rules`` basis means."
        ),
    )
    explanation_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "The Module 11 explanation of ``ml``, when one exists. Present so the "
            "operator can see which features moved the estimate this recommendation "
            "considered; null when the model was not explained, which is not a reason "
            "to withhold the recommendation."
        ),
    )

    # -- the rule, by identity --------------------------------------------
    rule_id: str | None = Field(default=None, max_length=64)
    rule_name: str | None = Field(default=None, max_length=120)
    rule_version: int | None = Field(default=None, ge=1)
    #: The rule's evaluation for this pan, kept structured rather than only as text.
    #: It is the authoritative record of which variables were read, what each was
    #: worth and which criteria fired; the two text fields below are renderings of
    #: it for a log line, and a client that needs a value reads it from here.
    evaluation: DecisionEvaluation | None = None
    specification_hash: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "The content hash of the rule version that produced this action, so a "
            "recommendation cites the exact rule content and not only a name."
        ),
    )
    matched_criteria_text: str = Field(
        default="",
        max_length=_MAX_MATCHED_CRITERIA_TEXT,
        description="Every criterion that fired, written out, for the audit log.",
    )
    input_readings_text: str = Field(
        default="",
        max_length=_MAX_INPUT_READINGS_TEXT,
        description="Every variable the rule read, with the value found or the reason not.",
    )

    # -- operator-facing summary, retained from the original scaffold --------
    priority: RecommendationPriority = RecommendationPriority.MEDIUM
    title: str = Field(
        min_length=1,
        max_length=128,
        description="The action label, verbatim, as the headline of the recommendation.",
    )
    body: str = Field(
        min_length=1,
        max_length=MAX_REASON_CHARS,
        description=(
            "A human-readable summary: the action, the reason and the headline "
            "figures. The structured blocks above remain the authoritative record."
        ),
    )
    rationale: str = Field(
        min_length=1,
        max_length=4000,
        description="The operational rationale recorded against the rule that fired.",
    )
    issued_at: UtcDateTime = Field(
        default_factory=utcnow,
        validation_alias=AliasChoices("issued_at", "timestamp"),
    )
    created_at: UtcDateTime = Field(default_factory=utcnow)
    expires_at: UtcDateTime | None = None

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        return normalize_pan_id(value)

    @model_validator(mode="after")
    def _decision_evidence_is_all_or_nothing(self) -> "Recommendation":
        """A record is either a complete decision, or a note with no decision at all.

        This model is written by two callers with different jobs. Module 12's engine
        stores a decision, and that decision is worthless unless the twin, the
        simulation and the rule that produced it are all on the record. Module 8's
        outcome grading stores a plain operator note, which legitimately has no
        action and no evidence behind it.

        The danger is the middle: a record with an action but no twin, or a rule id
        with no evaluation, is a document that reads as auditable and is not. Rather
        than require the decision fields outright and so exclude notes, the evidence
        is required *as a set*. A partial set is refused, so no consumer can meet a
        half-built decision record and mistake it for a whole one.
        """
        fields = {
            "action": self.action,
            "reason": self.reason,
            "basis": self.basis,
            "notice": self.notice,
            "twin": self.twin,
            "simulation": self.simulation,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "rule_version": self.rule_version,
            "evaluation": self.evaluation,
            "specification_hash": self.specification_hash,
        }
        present = sorted(name for name, value in fields.items() if value is not None)
        if not present:
            return self
        missing = sorted(set(fields) - set(present))
        if missing:
            raise ValueError(
                "a recommendation is either a complete decision or a note with no "
                f"decision; found {', '.join(present)} but missing {', '.join(missing)}"
            )
        return self

    @model_validator(mode="after")
    def _basis_matches_the_evidence(self) -> "Recommendation":
        """The stated basis has to agree with the evidence actually attached.

        A record claiming ``physics_and_ml`` with no ``ml`` block, or claiming
        ``physics_and_rules`` while carrying one, is a record that would be read as
        evidence of a kind it does not have. Both directions are checked, because a
        consumer has to be able to trust the basis without first inspecting the
        blocks. A note carries no basis, so it is exempt.
        """
        if self.basis is None:
            return self
        if self.basis is DecisionBasis.PHYSICS_AND_ML and self.ml is None:
            raise ValueError(
                "basis is physics_and_ml but no ml result is attached; a "
                "recommendation cannot claim a model's estimate it does not carry"
            )
        if self.basis is DecisionBasis.PHYSICS_AND_RULES and self.ml is not None:
            raise ValueError(
                "basis is physics_and_rules but an ml result is attached; a "
                "recommendation that read a model's estimate must say so"
            )
        return self

    @model_validator(mode="after")
    def _validate_expiry(self) -> "Recommendation":
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be later than issued_at")
        return self

    @property
    def timestamp(self) -> UtcDateTime:
        """The instant the recommendation was issued. An alias, not a second field."""
        return self.issued_at

    @property
    def is_decision(self) -> bool:
        """True when this record carries a rule-backed decision rather than a note.

        The single question a consumer asks before reading any of the evidence
        fields, since on a note they are all null.
        """
        return self.action is not None

    @property
    def is_ml_informed(self) -> bool:
        return self.basis is DecisionBasis.PHYSICS_AND_ML

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= utcnow()


__all__ = [
    "DecisionPredictionSummary",
    "DecisionSimulationSummary",
    "DecisionTwinSummary",
    "Recommendation",
]
