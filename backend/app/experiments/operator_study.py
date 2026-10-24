"""Phase 10 - the operator usability study: schema, and the reading of it.

A usability study has to be designed before it is run, because designing the
instrument after seeing the results is how a study ends up asking the question
that produced a nice answer. So this module is mostly *schema* plus the gate that
decides whether any result may be reported.

What is being measured is whether an operator can use the recommendation, and
whether they agree with it. Those are three different things and conflating them is
the usual failure:

* **Task completion** -- did they act on the recommendation at all.
* **Agreement** -- do they think it was right.
* **Time to decision** -- did it arrive in time to be useful.

The system has one accepted recommendation. One response cannot support a
usability finding of any kind, so :func:`usability_report` reports
``INSUFFICIENT_EVIDENCE`` and no numbers, and lists what would have to be
collected.

The schema is deliberately uncomfortable about one thing: ``action_taken`` is
separate from ``recommendation_accepted``. A person can accept a recommendation in
the app and physically do nothing, and this project has exactly that case on
record -- the September decision was accepted with a note that no harvesting
occurred. A study that recorded only acceptance would have counted it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.base import UtcDateTime, normalize_datetime, utcnow

#: Responses required before a usability figure is reported.
#:
#: Small on purpose. A usability percentage over a handful of operators is a
#: description of those operators, and the only honest way to present it is with
#: that qualifier attached -- which is what :attr:`UsabilityReport.qualifier`
#: carries.
MIN_RESPONSES = 5

#: Fixed wording, as in the validation metrics: a caller matches on it.
INSUFFICIENT_USABILITY = "Insufficient operator responses"


class Likert(IntEnum):
    """Agreement, 1 to 5.

    An ``IntEnum`` rather than a free integer so that ``Likert(7)`` raises here
    instead of producing a mean over a scale that does not exist.
    """

    STRONGLY_DISAGREE = 1
    DISAGREE = 2
    NEUTRAL = 3
    AGREE = 4
    STRONGLY_AGREE = 5


class ActionTaken(str, Enum):
    """What the operator physically did, which is not what they clicked.

    ``NONE`` exists and is the honest value for an accepted recommendation that was
    not acted on. Without it, the only alternatives would be to record such a case
    as something else or to leave it null, and both would misdescribe it.
    """

    CARRIED_OUT = "carried_out"
    PARTIALLY_CARRIED_OUT = "partially_carried_out"
    NONE = "not_carried_out"
    SUBSTITUTED = "carried_out_something_else"
    NOT_APPLICABLE = "not_applicable"


class DecisionOutcome(str, Enum):
    """The study's result for one recommendation."""

    COMPLETED = "completed"
    NOT_COMPLETED = "not_completed"
    ABANDONED = "abandoned"


class TaskResponse(BaseModel):
    """One operator's response to one recommendation.

    One row per (recommendation, operator). Not one row per operator: an operator
    who responded to three recommendations contributes three observations of
    three different tasks, and averaging those into a single per-operator score
    would weight repeat exposure as if it were depth of experience.
    """

    model_config = ConfigDict(extra="forbid")

    response_id: str = Field(min_length=1, max_length=96)
    recommendation_id: str = Field(min_length=1, max_length=96)
    pan_id: str = Field(min_length=1, max_length=64)

    #: Anonymised. The study is about whether the interface is usable, not about
    #: who used it, and an identifier that could be resolved to a person would be
    #: a disclosure the study does not need.
    operator_code: str = Field(
        min_length=1,
        max_length=32,
        description="A pseudonymous operator identifier. Never a name or an account.",
    )

    #: What the operator saw and did, in their own clock. Recorded, not derived:
    #: the gap between when the app says a recommendation was issued and when a
    #: person says they saw it includes the recording process's own delay.
    shown_at: UtcDateTime
    decided_at: UtcDateTime | None = None

    recommendation_accepted: bool | None = Field(
        default=None,
        description="Whether the operator accepted the recommendation in the app. Null when no decision was recorded.",
    )
    action_taken: ActionTaken | None = Field(
        default=None,
        description="What was physically done. Distinct from acceptance above.",
    )

    decision_latency_seconds: int | None = Field(
        default=None,
        ge=0,
        description="Seconds from shown_at to decided_at. Null when no decision was recorded; never derived silently.",
    )

    #: Whether the recommendation was clear. Null rather than omitted when not
    #: asked, so an unasked question is distinguishable from a zero.
    clarity: Likert | None = None
    #: Whether the operator would follow it again.
    would_follow_again: bool | None = None

    #: Free text kept verbatim. Not summarised or scored here: coding qualitative
    #: responses is a judgement a reader should be able to check.
    comments: str | None = Field(default=None, max_length=4000)

    #: Anonymisation required before this response may be exported or published.
    anonymised: bool = Field(
        default=False,
        description=(
            "Whether identifiers have been removed. A response that is not "
            "anonymised must not leave the operator-study directory."
        ),
    )

    created_at: UtcDateTime = Field(default_factory=utcnow)


class OperatorStudyProtocol(BaseModel):
    """The study as designed, so the design is reviewable before the results are.

    Shipped as data rather than as prose so the instrument and the report cannot
    drift apart: the report renders this, and a changed question shows up as a
    changed protocol.
    """

    model_config = ConfigDict(extra="forbid")

    protocol_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=32)

    research_questions: list[str] = Field(min_length=1)
    population: str = Field(min_length=1)
    recruitment: str = Field(
        min_length=1,
        description="How participants are recruited. Stated because operator experience changes usability.",
    )
    tasks: list[str] = Field(min_length=1)
    procedure: list[str] = Field(min_length=1)
    measures: list[str] = Field(min_length=1)
    #: What this design cannot establish, agreed before the data is collected.
    limitations: list[str] = Field(min_length=1)

    ethics: dict[str, Any] = Field(default_factory=dict)


#: The design this project would run. Written out so the questions are fixed
#: before any answers exist.
DEFAULT_PROTOCOL = OperatorStudyProtocol(
    protocol_id="operator-usability-v1",
    title="SaltLens recommendation usability study",
    version="1.0",
    research_questions=[
        "Can an operator understand a recommendation without a second person explaining it?",
        "Do operators agree with the recommendation, and by how much?",
        "Does the recommendation arrive early enough to be acted on?",
        "What do operators think is missing before they will act?",
    ],
    population=(
        "Salt pan operators who would receive a recommendation in normal use, at "
        "any level of experience. Experience is recorded per participant, because "
        "a first-time user and a fifth-year user are not the same population."
    ),
    recruitment=(
        "Operators approached at the site, consecutively rather than by "
        "convenience, so the sample is not self-selected from those already "
        "interested in the tool."
    ),
    tasks=[
        "Read a recommendation for a named pan and state what it asks you to do.",
        "Decide whether to follow it, and record the decision.",
        "State what information you would need before following it.",
        "Report what you believe the salinity is now, before seeing any figure.",
    ],
    procedure=[
        "Present one recommendation per task, in a fixed order across participants.",
        "Do not explain the interface beyond the written instruction on the card.",
        "Record the operator's own account of what they did, not the app's state.",
        "Record the time the operator first saw the recommendation.",
    ],
    measures=[
        "Task completion: whether the operator carried out the recommended action.",
        "Agreement: a 1-5 rating of whether the recommendation was correct.",
        "Decision latency: seconds from first seeing the recommendation to deciding.",
        (
            "Predicted salinity before any figure is shown, to measure whether the "
            "recommendation changes the operator's expectation."
        ),
    ],
    limitations=[
        (
            "A sample of this size describes these operators; it does not establish "
            "a population usability rate."
        ),
        (
            "Presence of an observer changes behaviour, so task completion measured "
            "this way is an upper bound."
        ),
        "One site and one set of pans, so nothing generalises to other sites.",
        (
            "Not a randomised comparison: operators are not assigned to a "
            "recommendation or to no recommendation, so no causal claim about the "
            "tool is available."
        ),
    ],
)


@dataclass(frozen=True)
class UsabilityReport:
    """Whether any usability figure may be reported, and if not, why."""

    sufficient: bool
    message: str | None
    responses: int
    responses_counted: int
    #: Responses excluded from the aggregate, with the reason for each exclusion.
    exclusions: dict[str, int]
    #: Only present when sufficient. Never computed from a partial set.
    metrics: dict[str, float] = field(default_factory=dict)
    min_responses_required: int = MIN_RESPONSES
    reasons: tuple[str, ...] = ()
    #: Appended to every figure this report produces.
    qualifier: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "sufficient": self.sufficient,
            "message": self.message,
            "responses": self.responses,
            "responses_counted": self.responses_counted,
            "exclusions": dict(self.exclusions),
            "metrics": dict(self.metrics),
            "min_responses_required": self.min_responses_required,
            "reasons": list(self.reasons),
            "qualifier": self.qualifier,
        }


def usability_report(
    responses: Sequence[TaskResponse],
    *,
    min_responses: int = MIN_RESPONSES,
) -> UsabilityReport:
    """Aggregate a set of task responses, or refuse to.

    An unanonymised response is excluded from the aggregate. That is stricter than
    it looks: it means the study reports nothing until the identifiers are dealt
    with, which is the right place to enforce it, because the alternative is a
    report that is fine right up until someone shares it.

    A response is also excluded when it carries no decision, since latency and
    agreement are undefined without one.
    """
    exclusions: dict[str, int] = {}
    counted: list[TaskResponse] = []

    for response in responses:
        if not response.anonymised:
            exclusions["not anonymised, so not counted"] = (
                exclusions.get("not anonymised, so not counted", 0) + 1
            )
            continue
        if response.decided_at is None:
            exclusions["no decision was recorded"] = (
                exclusions.get("no decision was recorded", 0) + 1
            )
            continue
        counted.append(response)

    reasons: list[str] = []
    if len(counted) < min_responses:
        reasons.append(
            f"{len(counted)} usable response(s); at least {min_responses} are "
            "required before a usability figure describes more than the "
            "individuals who gave them."
        )

    if reasons:
        return UsabilityReport(
            sufficient=False,
            message=INSUFFICIENT_USABILITY,
            responses=len(responses),
            responses_counted=len(counted),
            exclusions=exclusions,
            reasons=tuple(reasons),
        )

    latencies = [
        float(response.decision_latency_seconds)
        for response in counted
        if response.decision_latency_seconds is not None
    ]
    agreements = [float(response.clarity) for response in counted if response.clarity is not None]
    completed = sum(
        1 for response in counted if response.action_taken is ActionTaken.CARRIED_OUT
    )
    accepted = sum(1 for response in counted if response.recommendation_accepted)

    metrics: dict[str, float] = {
        "completion_rate": completed / len(counted),
        "acceptance_rate": accepted / len(counted),
    }
    if latencies:
        ordered = sorted(latencies)
        # The 90th percentile by nearest rank rather than by interpolation: with a
        # handful of responses an interpolated percentile implies a precision the
        # sample cannot support, so the nearest observed value is used instead.
        nearest_rank = round(0.9 * (len(ordered) - 1))
        metrics["median_decision_latency_seconds"] = _median(ordered)
        metrics["p90_decision_latency_seconds"] = ordered[
            min(len(ordered) - 1, nearest_rank)
        ]
    if agreements:
        metrics["mean_clarity_rating"] = sum(agreements) / len(agreements)

    return UsabilityReport(
        sufficient=True,
        message=None,
        responses=len(responses),
        responses_counted=len(counted),
        exclusions=exclusions,
        metrics=metrics,
        reasons=(),
        # Not decoration. Without this the figures would be read as a population
        # rate, which a sample of five operators cannot support.
        qualifier=(
            f"A descriptive figure from {len(counted)} responses from "
            f"{len({r.operator_code for r in counted})} operators. It describes "
            "these operators, not the population."
        ),
    )


def _median(ordered: Sequence[float]) -> float:
    """The median of an already-sorted sequence."""
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def latency_seconds(shown_at: datetime, decided_at: datetime) -> int | None:
    """Seconds between two instants, or ``None`` when the order is wrong.

    A negative interval means the decision is stamped before the operator saw the
    recommendation, which is a data error rather than a very fast operator, so it
    returns ``None`` instead of a negative duration that would drag every average
    down.
    """
    shown = normalize_datetime(shown_at)
    decided = normalize_datetime(decided_at)
    delta = (decided - shown).total_seconds()
    if delta < 0:
        return None
    return int(delta)


__all__ = [
    "DEFAULT_PROTOCOL",
    "INSUFFICIENT_USABILITY",
    "MIN_RESPONSES",
    "ActionTaken",
    "DecisionOutcome",
    "Likert",
    "OperatorStudyProtocol",
    "TaskResponse",
    "UsabilityReport",
    "latency_seconds",
    "usability_report",
]
