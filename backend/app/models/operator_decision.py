from __future__ import annotations

from pydantic import AliasChoices, Field, field_validator, model_validator

from app.models.base import (
    BaseDocument,
    UtcDateTime,
    new_id,
    normalize_identifier,
    normalize_pan_id,
    utcnow,
)
from app.models.enums import DecisionAction, OperatorDecisionType


class OperatorDecision(BaseDocument):
    decision_id: str = Field(
        default_factory=lambda: new_id("decision"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        validation_alias=AliasChoices("decision_id", "feedback_id"),
    )
    recommendation_id: str = Field(min_length=1, max_length=64)
    pan_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")

    #: What the operator chose to do with the advice. ``None`` is an unfinished
    #: record, not a vote for any option.
    decision: OperatorDecisionType | None = None

    #: What the operator actually performed, from the closed action vocabulary.
    #: ``None`` here is a real value: it records that no physical action followed
    #: the advice (an acceptance left blank). The service never fills this in on
    #: the operator's behalf; a modification or rejection must name its own
    #: action, because the whole point of those two is that the recommended one
    #: was not taken.
    actual_action: DecisionAction | None = None

    #: What the advice said, copied here at the moment of the decision.
    #:
    #: A decision is the end of the trace, so it is the record most likely to be
    #: read long after the rest of it has moved or been pruned. Joining the
    #: recommendation on every read would mean the record of what an operator was
    #: *advised*, and of which rule version advised it, disappears the moment the
    #: document it points at does — and with it the only means of telling an
    #: acceptance apart from a coincidence. Copied, it survives, and a reader of
    #: this one document can still say what the advice was.
    recommended_action: DecisionAction | None = None
    rule_name: str | None = Field(default=None, min_length=1, max_length=120)
    rule_version: int | None = Field(default=None, ge=1)
    specification_hash: str | None = Field(default=None, max_length=128)

    #: Whether the advice this decision answers was itself built from
    #: demonstration records, copied rather than joined for the same reason.
    #:
    #: An operator can perfectly well make a real decision about a worked example,
    #: and the record of that judgement is worth keeping. What must not happen is
    #: a decision on demonstration advice sitting in a research export beside real
    #: ones with nothing to tell them apart, so the flag travels with the record
    #: and an export has to read it rather than infer it from source strings.
    #:
    #: Nullable, and that is the point. A plain ``bool`` defaulting to ``False``
    #: cannot tell "the advice was real" apart from "this record was written
    #: before the flag existed", and the second would export as the first --
    #: claiming a real field observation that nobody made. ``None`` means the
    #: record does not say, which the read layer resolves against the live advice
    #: when that is still available and otherwise reports as unknown rather than
    #: guessing. New decisions always carry a definite value.
    is_demonstration: bool | None = Field(
        default=None,
        description=(
            "True when the advice this decision answers was built from "
            "demonstration records. Copied from that advice so a research export "
            "reads the flag rather than inferring it from source strings."
        ),
    )
    demonstration_sources: list[str] = Field(default_factory=list)

    operator_id: str | None = Field(default=None, min_length=1, max_length=64)
    notes: str | None = Field(default=None, max_length=2000)
    timestamp: UtcDateTime | None = Field(
        default=None,
        validation_alias=AliasChoices("timestamp", "decided_at", "event_timestamp"),
    )
    created_at: UtcDateTime = Field(
        default_factory=utcnow,
        validation_alias=AliasChoices("created_at", "sent_at"),
    )

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        return normalize_pan_id(value)

    @field_validator("recommendation_id", "operator_id", mode="before")
    @classmethod
    def _normalize_identifiers(cls, value):
        if value is None:
            return None
        return normalize_identifier(value)

    @model_validator(mode="after")
    def _modification_or_rejection_names_its_action(self) -> OperatorDecision:
        """A changed decision cannot be silent about what was actually done.

        Acceptance is the one disposition that may record no physical action at
        all, so its ``actual_action`` may be left blank. Making a call that
        departs from the advice is exactly when the record needs the action that
        was really taken, or the trace would end in "modified" with no figure
        for what modified meant.
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

    @property
    def decided_at(self) -> UtcDateTime | None:
        return self.timestamp

    @property
    def sent_at(self) -> UtcDateTime:
        return self.created_at


OperatorFeedback = OperatorDecision
