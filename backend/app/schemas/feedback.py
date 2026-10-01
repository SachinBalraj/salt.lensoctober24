"""Module 14 - Operator feedback request and response schemas.

The record an operator makes after reading a recommendation: which side of the
advice they landed on (accepted / modified / rejected), the action they actually
took, their note, and the moment they decided. That document is the seventh step
of the reconstructed event chain, and it carries the identifiers of the earlier
steps so a single decision row re-links the whole trace.

The service layer refuses to record feedback in three cases, each named by a
code a client can branch on rather than prose: the recommendation is unknown
(``recommendation_unknown``), it is a note that made no decision
(``recommendation_not_a_decision``), or a decision for it already exists
(``decision_already_recorded``).
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import DecisionAction, DecisionBasis, OperatorDecisionType
from app.models.operator_decision import OperatorDecision
from app.models.recommendation import Recommendation
from app.schemas.observation import MAX_FUTURE_SKEW

#: A decision note is a sentence or two, so 2000 characters is generous.
MAX_NOTE_LENGTH = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FeedbackCreate(BaseModel):
    """What the operator decided about one recommendation, and what they did.

    ``recommendation_id`` is the only identifier the client supplies. The pan and
    the recommended action are read from the stored recommendation, so a feedback
    record cannot be filed against a different pan than the advice it answers.

    ``actual_action`` comes from the same closed vocabulary the recommendation
    uses, and names only what was actually performed. Omit it with an accepted
    decision and the record says no physical action followed; supply it and that
    is what gets filed. A modified or rejected decision must name its own action,
    because the record exists to say how the operator actually departed from the
    advice.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    recommendation_id: str = Field(
        min_length=1,
        max_length=64,
        description="The stored recommendation this feedback answers.",
        examples=["rec-1f2e3d4c5b6a79887766554433221100"],
    )
    decision: OperatorDecisionType = Field(
        description=(
            "How the operator disposed of the recommendation: accepted, modified "
            "or rejected."
        ),
        examples=["accepted", "modified", "rejected"],
    )
    actual_action: DecisionAction | None = Field(
        default=None,
        description=(
            "The action actually performed, from the closed vocabulary. Required "
            "for a modified or rejected decision; a blank value with an acceptance "
            "records that no physical action followed."
        ),
        examples=["TRANSFER BRINE"],
    )
    operator_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Who decided, so a study can group decisions by operator.",
        examples=["op-2026-team-3"],
    )
    notes: str | None = Field(
        default=None,
        max_length=MAX_NOTE_LENGTH,
        description="The operator's own words on why, written in the field.",
    )
    timestamp: datetime | None = Field(
        default=None,
        description=(
            "When the decision was made, ISO 8601. Defaults to the moment the "
            "record is saved; supply a value to backdate an entry."
        ),
        examples=["2026-03-14T12:30:00Z"],
    )

    @model_validator(mode="after")
    def _reject_future_timestamp(self) -> FeedbackCreate:
        if self.timestamp is not None and self.timestamp > _now() + MAX_FUTURE_SKEW:
            raise ValueError(
                "timestamp cannot be in the future. A decision describes a moment "
                "that has already happened."
            )
        return self

    @model_validator(mode="after")
    def _modification_or_rejection_names_its_action(self) -> FeedbackCreate:
        """A departure from the advice must say what was done instead.

        Mirrors the stored document's own rule so the refusal is a 422 at the
        request boundary rather than an unhandled validation error deep in the
        service.
        """
        if self.decision in (
            OperatorDecisionType.MODIFIED,
            OperatorDecisionType.REJECTED,
        ) and self.actual_action is None:
            raise ValueError(
                "actual_action is required when the decision is MODIFIED or "
                "REJECTED: the record must say what the operator actually did "
                "instead of the recommended action."
            )
        return self

    def resolved_timestamp(self) -> datetime:
        return self.timestamp if self.timestamp is not None else _now()


class FeedbackRecommendationRead(BaseModel):
    """The recommendation a decision answers, with the identifiers of its trace.

    Every earlier stage of the chain carries its own id and instant so a decision
    row is self-describing: a research export that reads one document can rebuild
    observation -> twin -> simulation -> prediction -> explanation ->
    recommendation from here, without a second query.
    """

    model_config = ConfigDict(from_attributes=True)

    recommendation_id: str
    action: DecisionAction | None
    issued_at: datetime
    basis: DecisionBasis | None
    priority: str | None = None

    # The three kinds of evidence the advice carried, by identity.
    simulation_run_id: str | None = None
    ml_prediction_id: str | None = None
    explanation_id: str | None = None

    # The recorded state the advice was made from.
    twin_id: str | None = None
    twin_as_of: datetime | None = None
    observation_id: str | None = None
    observation_timestamp: datetime | None = None
    weather_forecast_id: str | None = None

    # The rule, by identity, so the decision cites what fired.
    rule_name: str | None = None
    rule_version: int | None = None
    specification_hash: str | None = None

    @classmethod
    def from_recommendation(cls, recommendation: Recommendation) -> FeedbackRecommendationRead:
        twin = recommendation.twin
        simulation = recommendation.simulation
        ml = recommendation.ml
        return cls(
            recommendation_id=recommendation.recommendation_id,
            action=recommendation.action,
            issued_at=recommendation.issued_at,
            basis=recommendation.basis,
            priority=recommendation.priority,
            simulation_run_id=simulation.run_id if simulation else None,
            ml_prediction_id=ml.prediction_id if ml else None,
            explanation_id=recommendation.explanation_id,
            twin_id=twin.twin_id if twin else None,
            twin_as_of=twin.as_of if twin else None,
            observation_id=twin.observation_id if twin else None,
            observation_timestamp=twin.observation_timestamp if twin else None,
            weather_forecast_id=twin.weather_forecast_id if twin else None,
            rule_name=recommendation.rule_name,
            rule_version=recommendation.rule_version,
            specification_hash=recommendation.specification_hash,
        )


class FeedbackRead(BaseModel):
    """One recorded operator decision, as stored and rendered.

    ``actual_action`` is the closed-vocabulary figure of what was really done. It
    is ``None`` when the operator recorded no physical action (an acceptance left
    blank), equals ``recommended_action`` when the operator recorded the same
    action the advice named, and departs from it for a modification or rejection.

    The rule version and the demonstration flag are properties of *this* document,
    copied in when the decision was recorded, so they answer for the advice even
    if the recommendation it cites is gone. ``recommendation`` is the live join for
    the deeper trace and is ``None`` when that document no longer exists — its
    absence costs the reader the extra identifiers, not the decision itself.
    """

    model_config = ConfigDict(from_attributes=True)

    decision_id: str
    recommendation_id: str
    pan_id: str
    decision: OperatorDecisionType
    actual_action: DecisionAction | None
    recommended_action: DecisionAction | None = Field(
        description=(
            "The action the stored recommendation advised, copied at the moment "
            "of the decision so an acceptance stays distinguishable from a "
            "coincidence even if the recommendation is later removed."
        )
    )
    rule_name: str | None = Field(
        default=None,
        description="The rule version that produced the advice, copied at decision time.",
    )
    rule_version: int | None = None
    specification_hash: str | None = None
    is_demonstration: bool | None = Field(
        default=None,
        description=(
            "True when the advice this decision answers was built from "
            "demonstration records, false when it was not, and null when the "
            "record does not say -- either it predates the flag or its "
            "recommendation is gone. An export must treat null as unknown, not as "
            "false: 'not demonstration' is a claim about provenance, and guessing "
            "it would put a worked example in a table of field results."
        ),
    )
    demonstration_sources: list[str] = Field(default_factory=list)
    operator_id: str | None = None
    notes: str | None = None
    timestamp: datetime = Field(
        description="When the operator decided, in the trace's event order."
    )
    created_at: datetime = Field(description="When the record was stored.")
    recommendation: FeedbackRecommendationRead | None

    @classmethod
    def from_decision(
        cls, decision: OperatorDecision, recommendation: Recommendation | None
    ) -> FeedbackRead:
        return cls(
            decision_id=decision.decision_id,
            recommendation_id=decision.recommendation_id,
            pan_id=decision.pan_id,
            decision=decision.decision,
            actual_action=decision.actual_action,
            # The decision's own copy, not the join's. A record that predates this
            # field has none, and the join is then the only place left to find it,
            # so the live advice fills the gap without becoming the source of
            # truth for a decision that does have a copy.
            recommended_action=(
                decision.recommended_action
                if decision.recommended_action is not None
                else (recommendation.action if recommendation is not None else None)
            ),
            rule_name=decision.rule_name
            or (recommendation.rule_name if recommendation is not None else None),
            rule_version=decision.rule_version
            if decision.rule_version is not None
            else (recommendation.rule_version if recommendation is not None else None),
            specification_hash=decision.specification_hash
            or (
                recommendation.specification_hash
                if recommendation is not None
                else None
            ),
            # Resolved, not assumed. A record written before the flag existed has
            # ``None`` here, and the live advice is then the only place left that
            # knows the answer. When neither does, it stays ``None`` and says so --
            # see the field's description. A decision that does carry a copy is
            # never overridden by the join, because the advice may since have been
            # recomputed against different evidence.
            is_demonstration=(
                decision.is_demonstration
                if decision.is_demonstration is not None
                else (
                    recommendation.twin.is_demonstration
                    if recommendation is not None
                    else None
                )
            ),
            demonstration_sources=(
                list(decision.demonstration_sources)
                if decision.demonstration_sources
                else (
                    list(recommendation.twin.demonstration_sources)
                    if recommendation is not None
                    else []
                )
            ),
            operator_id=decision.operator_id,
            notes=decision.notes,
            timestamp=decision.timestamp,
            created_at=decision.created_at,
            recommendation=(
                FeedbackRecommendationRead.from_recommendation(recommendation)
                if recommendation is not None
                else None
            ),
        )


class FeedbackListResponse(BaseModel):
    """Recorded decisions, newest first, optionally narrowed."""

    model_config = ConfigDict(extra="forbid")

    count: int
    items: list[FeedbackRead]


class FeedbackRefusalResponse(BaseModel):
    """A decision that could not be recorded, with a code to branch on."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        description=(
            "One of: recommendation_unknown, recommendation_not_a_decision, "
            "decision_already_recorded."
        )
    )
    detail: str
    recommendation_id: str | None = None


__all__ = [
    "MAX_NOTE_LENGTH",
    "FeedbackCreate",
    "FeedbackListResponse",
    "FeedbackRead",
    "FeedbackRecommendationRead",
    "FeedbackRefusalResponse",
]