"""Phases 8 and 9 - what could be claimed about the ML and the operations, and what cannot.

Two questions the study asks, and the answers this module is built to give.

**Phase 8, the ML:** would SaltLens's salinity model have predicted held-out field
measurements better than a stated baseline? The infrastructure for that question
already exists in :mod:`app.ml` -- datasets, splits, checkpoints, evaluations. None
of it has ever been run against real data, because there is no real dataset. So the
correct answer today is a readiness report listing exactly what is missing, not a
metric.

**Phase 9, the operations:** did following SaltLens reduce salt loss, or improve
recovery, or save a harvest? This needs a matched pair of periods per pan -- one
where the recommendation was followed and one where it was not -- plus the yield,
recovery and brine-loss figures for each. The system has 11 operator decisions and
0 matched pairs, so this too is a readiness report.

Both reports share a design choice worth stating: **every figure carries the evidence
that would license it, and the report refuses to produce a figure whose evidence is
absent.** :func:`assessment_status` is the single place that decision is made, and it
is driven by a threshold and a count rather than by how good the available numbers
look. A module that reported the best available figure would be reporting the
easiest one to get.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.evaluation import MetricReading

#: Fixed wording for "the study has not been run yet". Matched on by callers, so it
#: does not vary with the size of the gap it describes.
NOT_YET_ASSESSED = "Not yet assessable: the study has not been run"


class AssessmentStatus(str, Enum):
    """Whether a claim can be made, and if not, why not."""

    NOT_ASSESSED = "not_assessed"
    INSUFFICIENT = "insufficient_evidence"
    ASSESSABLE = "assessable"

    @property
    def is_assessable(self) -> bool:
        return self is AssessmentStatus.ASSESSABLE


class ReadinessItem(BaseModel):
    """One precondition, and whether it is met.

    ``detail`` says what specifically is absent, because "the dataset is missing"
    is not actionable while "no labelled rows exist; 0 of the 40 required" tells
    whoever has to go and collect them how much is left to do.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    met: bool
    required: bool = True
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class AssessmentReport(BaseModel):
    """One phase's readiness, and the figures it could produce if it were run."""

    model_config = ConfigDict(extra="forbid")

    phase: str
    title: str
    status: AssessmentStatus
    message: str
    #: Figures that exist. Empty while :attr:`status` is not ``ASSESSABLE``.
    metrics: dict[str, MetricReading] = Field(default_factory=dict)
    #: Every precondition, met or unmet, so the report shows the whole picture
    #: rather than only the first thing that was missing.
    preconditions: list[ReadinessItem] = Field(default_factory=list)
    #: The claim this phase is *not* licensed to make, in the paper's terms.
    prohibited_claims: list[str] = Field(default_factory=list)
    #: What would have to be collected to move to the next status.
    required_next_steps: list[str] = Field(default_factory=list)

    @property
    def blocking(self) -> list[ReadinessItem]:
        return [item for item in self.preconditions if item.required and not item.met]

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "title": self.title,
            "status": self.status.value,
            "message": self.message,
            "metrics": {name: reading.as_dict() for name, reading in self.metrics.items()},
            "preconditions": [item.as_dict() for item in self.preconditions],
            "blocking_preconditions": [item.name for item in self.blocking],
            "prohibited_claims": list(self.prohibited_claims),
            "required_next_steps": list(self.required_next_steps),
        }


def assessment_status(met: Sequence[bool], *, minimum: int) -> tuple[AssessmentStatus, str]:
    """The single decision on whether a claim may be made.

    Two thresholds rather than one boolean, because "some evidence" and "enough
    evidence" are different things and a single flag conflates them. ``minimum``
    is the number of satisfied preconditions that makes a claim licensable; at or
    above it the status is ``ASSESSABLE``, below it ``INSUFFICIENT``.
    """
    satisfied = sum(1 for value in met if value)
    if satisfied >= minimum:
        return AssessmentStatus.ASSESSABLE, (
            f"{satisfied} of {len(met)} preconditions met, which meets the "
            f"threshold of {minimum}."
        )
    if satisfied == 0:
        return AssessmentStatus.NOT_ASSESSED, NOT_YET_ASSESSED
    return AssessmentStatus.INSUFFICIENT, (
        f"{satisfied} of {len(met)} preconditions met; {minimum} are required "
        f"before this phase can support any claim."
    )


# --------------------------------------------------------------------------
# Phase 8 - ML validation
# --------------------------------------------------------------------------


#: Held-out rows required before a model comparison is worth reporting. Below
#: this, a difference in accuracy between two models is a difference between two
#: individual readings.
MIN_HOLDOUT_ROWS = 40

#: Independent field events the held-out set must span. Rows are not events: 40
#: readings from one rainfall event is 40 rows describing one occurrence, and an
#: accuracy figure over them is a statement about that event, not about the model.
MIN_HOLDOUT_EVENTS = 3


@dataclass(frozen=True)
class MLReadinessInput:
    """What exists, as counts. Never as a score.

    Takes counts rather than metrics so that a caller cannot pass in a favourable
    R-squared and have the readiness report treat the model as validated.
    """

    dataset_rows: int = 0
    labelled_rows: int = 0
    split_artifact_present: bool = False
    checkpoint_present: bool = False
    trained_models: int = 0
    held_out_rows: int = 0
    held_out_events: int = 0
    baseline_evaluated: bool = False
    predictions_recorded: int = 0


def ml_readiness_report(state: MLReadinessInput | None = None) -> AssessmentReport:
    """Phase 8's answer: what the ML evidence supports today.

    ``state=None`` means "nothing exists", which is the state this repository is
    in. The default is that, rather than a value read from somewhere, because a
    readiness check that defaulted to anything else would be reporting the
    environment rather than the evidence.
    """
    state = state or MLReadinessInput()

    preconditions = [
        ReadinessItem(
            name="real_labelled_dataset",
            met=state.labelled_rows > 0,
            detail=(
                f"{state.labelled_rows} labelled rows exist"
                f"{f' out of {state.dataset_rows} collected' if state.dataset_rows else ''}. "
                "No row in this project has a machine-readable salinity label "
                "tied to a specific pan, depth and time."
            ),
        ),
        ReadinessItem(
            name="held_out_split",
            met=state.split_artifact_present,
            detail=(
                "The dataset builder in app.ml can produce a split, and no split "
                "artifact has been written, so nothing has been held out from "
                "anything."
            ),
        ),
        ReadinessItem(
            name="trained_checkpoint",
            met=state.checkpoint_present and state.trained_models > 0,
            detail=(
                f"{state.trained_models} trained model(s); ml/models/ contains no "
                "checkpoint. There is no artifact to evaluate."
            ),
        ),
        ReadinessItem(
            name="held_out_rows_sufficient",
            met=state.held_out_rows >= MIN_HOLDOUT_ROWS,
            detail=(
                f"{state.held_out_rows} held-out rows; at least "
                f"{MIN_HOLDOUT_ROWS} are required for a comparison between two "
                "models to be more than a difference between two readings."
            ),
        ),
        ReadinessItem(
            name="held_out_events_sufficient",
            met=state.held_out_events >= MIN_HOLDOUT_EVENTS,
            detail=(
                f"{state.held_out_events} distinct events in the held-out set; at "
                f"least {MIN_HOLDOUT_EVENTS} are required, because rows from one "
                "event describe one occurrence."
            ),
        ),
        ReadinessItem(
            name="baseline_evaluated_under_identical_split",
            met=state.baseline_evaluated,
            detail=(
                "No baseline has been evaluated on the same held-out split. A "
                "model compared against a baseline scored on different data "
                "cannot be said to have beaten it."
            ),
        ),
        ReadinessItem(
            name="predictions_recorded_for_audit",
            met=state.predictions_recorded > 0,
            detail=(
                f"{state.predictions_recorded} predictions recorded. The ml_predictions "
                "collection is empty, so no figure could be re-derived from the "
                "rows behind it."
            ),
        ),
    ]

    status, message = assessment_status(
        [item.met for item in preconditions], minimum=len(preconditions)
    )

    metrics: dict[str, MetricReading] = {}
    if status.is_assessable:
        # Deliberately empty. This function is handed counts, not scores, so
        # there is nothing here that could be reported even if the gates opened;
        # the evaluation itself is app.ml's job and it writes to the registry.
        pass

    return AssessmentReport(
        phase="8",
        title="ML validation against held-out field data",
        status=status,
        message=message,
        metrics=metrics,
        preconditions=preconditions,
        prohibited_claims=[
            "Any accuracy, R-squared, RMSE or error figure for the ML model.",
            "That the model outperforms a baseline, a heuristic or the mass-balance model.",
            "That the model generalises to unseen pans, seasons or salinity regimes.",
            "Any statement that implies a trained model is deployed, because none is.",
        ],
        required_next_steps=[
            (
                "Collect field measurements with pan, depth, time and salinity "
                "recorded together, so a row can be labelled without inference."
            ),
            (
                "Build and commit a split artifact before any model is trained, so "
                "the held-out set is fixed in advance."
            ),
            (
                "Train and checkpoint one model, and evaluate it and a declared "
                "baseline on that same split."
            ),
            (
                "Record every prediction to ml_predictions so each figure has rows "
                "behind it."
            ),
        ],
    )


# --------------------------------------------------------------------------
# Phase 9 - operational loss and salt recovery
# --------------------------------------------------------------------------


#: Matched followed/not-followed pairs per pan before any operational comparison.
#:
#: The design is within-pan and paired rather than a comparison across pans,
#: because pans differ in area, depth and history by more than any decision rule
#: is likely to change. A single pair is one treated period against one untreated
#: one; at 3 the difference is still an observation about those periods rather
#: than an effect, but it is at least more than one period.
MIN_OPERATIONAL_PAIRS = 3


@dataclass(frozen=True)
class OperationalReadinessInput:
    """What exists, as counts.

    ``production_yield_kg`` and ``salt_recovered_kg`` are nullable on purpose. A
    pan with a decision recorded but no yield recorded has no yield, and the
    report has to be able to say so.
    """

    operator_decisions: int = 0
    pairs_where_followed: int = 0
    pairs_where_not_followed: int = 0
    production_yield_kg: float | None = None
    salt_recovered_kg: float | None = None
    brine_loss_kg: float | None = None
    decision_time_written_down: bool = False
    #: Whether a recommended physical action was actually carried out and
    #: recorded as such. Kept as an input rather than a constant so the report
    #: reflects the evidence it is given: hard-coding it to false would make
    #: assessability unreachable even once real action records exist.
    physical_action_confirmed: bool = False


#: Claims that need a matched operational pair. Each states what it needs, so the
#: absence of one is traceable to a specific missing measurement rather than to a
#: general shortfall of data.
OPERATIONAL_CLAIMS: tuple[tuple[str, str], ...] = (
    (
        "Following the recommendation increased salt recovery per pan.",
        (
            "Matched within-pan periods with and without the recommendation "
            "followed, and salt recovered (kg) recorded for each."
        ),
    ),
    (
        "Following the recommendation reduced production loss.",
        (
            "Matched within-pan periods, and production yield (kg) recorded for "
            "each period. Yield per hectare, because pans differ in area."
        ),
    ),
    (
        "Following the recommendation reduced brine loss.",
        (
            "Brine volume (m3) out of the pan and its measured salinity (g/L) for "
            "each matched period, so kg of salt lost can be derived rather than "
            "estimated."
        ),
    ),
    (
        "SaltLens decisions were recorded promptly enough to be actionable.",
        (
            "The wall-clock interval between the recommendation and the operator's "
            "decision written down per decision, not inferred from record "
            "timestamps."
        ),
    ),
)


def operational_readiness_report(
    state: OperationalReadinessInput | None = None,
) -> AssessmentReport:
    """Phase 9's answer: what the operational record supports today.

    The 11 operator decisions in this system are not pairs. They are decisions
    without matched counterparts and without yield, recovery or brine-loss
    figures, so they cannot support a before-and-after statement, and the report
    says that rather than presenting a count of 11 as progress toward one.
    """
    state = state or OperationalReadinessInput()

    pairs = min(state.pairs_where_followed, state.pairs_where_not_followed)

    preconditions = [
        ReadinessItem(
            name="matched_pairs_per_pan",
            met=pairs >= MIN_OPERATIONAL_PAIRS,
            detail=(
                f"{state.pairs_where_followed} followed and "
                f"{state.pairs_where_not_followed} not-followed periods matched "
                f"({pairs} usable pair(s)); at least {MIN_OPERATIONAL_PAIRS} "
                "matched pairs are required per pan."
            ),
        ),
        ReadinessItem(
            name="production_yield_recorded",
            met=state.production_yield_kg is not None,
            detail=(
                "No production yield has been recorded for any period. Yield is the "
                "denominator of a production claim, and without it a claim of "
                "improved production has nothing to improve on."
            ),
        ),
        ReadinessItem(
            name="salt_recovery_recorded",
            met=state.salt_recovered_kg is not None,
            detail=(
                "No salt recovery figure exists. Salt recovered (kg) is the "
                "quantity an operational claim is about, so its absence is the "
                "whole of the gap."
            ),
        ),
        ReadinessItem(
            name="brine_loss_recorded",
            met=state.brine_loss_kg is not None,
            detail=(
                "No brine loss has been recorded. A loss claim needs the volume "
                "removed and its salinity at the time of removal, not a volume "
                "alone."
            ),
        ),
        ReadinessItem(
            name="decision_latency_written_down",
            met=state.decision_time_written_down,
            detail=(
                "The interval from recommendation to decision is derived from "
                "record timestamps rather than written down. Derived latency "
                "includes whatever delay the recording process itself had."
            ),
        ),
        ReadinessItem(
            name="physical_action_confirmed",
            met=state.physical_action_confirmed,
            detail=(
                "No recommended physical action has been confirmed as performed. "
                "The one accepted recommendation in this system carries the note "
                "that no physical harvesting occurred. An accepted decision is not "
                "a performed action, and treating the two as the same would "
                "manufacture a treated period that never existed."
                if not state.physical_action_confirmed
                else "A recommended physical action is recorded as performed."
            ),
        ),
    ]

    status, message = assessment_status(
        [item.met for item in preconditions], minimum=len(preconditions)
    )

    return AssessmentReport(
        phase="9",
        title="Operational loss, salt recovery and decision latency",
        status=status,
        message=message,
        metrics={},
        preconditions=preconditions,
        prohibited_claims=[
            f"{claim}" for claim, _ in OPERATIONAL_CLAIMS
        ]
        + [
            "That the system reduced loss, improved recovery or increased production.",
            "That a decision's acceptance indicates the recommended action happened.",
            "That 11 recorded decisions constitute operational evidence of benefit.",
        ],
        required_next_steps=[
            (
                "Record, per pan, matched periods with and without the "
                "recommendation followed, so the comparison is within-pan."
            ),
            (
                "Record production yield (kg) and salt recovered (kg) per period, "
                "with the pan area they apply to."
            ),
            (
                "Record brine volume removed together with its salinity at removal, "
                "so salt loss in kg is derived from two measurements rather than "
                "estimated."
            ),
            (
                "Write down the time the operator saw the recommendation and the "
                "time they decided."
            ),
            (
                "Distinguish an accepted decision from a performed physical action "
                "in the record."
            ),
        ],
    )


def claim_requirements() -> list[dict[str, str]]:
    """Each operational claim, with what it would need.

    Exposed so a paper or dashboard can print the requirement next to the claim
    it belongs to, rather than leaving the reader to guess why a claim is absent.
    """
    return [{"claim": claim, "requires": requirement} for claim, requirement in OPERATIONAL_CLAIMS]


def aggregate_runs(
    metrics: Iterable[MetricReading],
) -> MetricReading:
    """Combine several readings into one, refusing to do it if any is absent.

    A mean over readings where some are ``computed=False`` would silently treat
    the absent ones as zero -- the exact substitution this project forbids. So
    this returns an uncomputed reading naming what was missing.
    """
    collected = list(metrics)
    missing = [reading.reason or "unspecified" for reading in collected if not reading.computed]
    if missing:
        return MetricReading(
            value=None,
            computed=False,
            reason=(
                f"{len(missing)} of {len(collected)} input readings were not "
                f"computed, so no aggregate exists. First reason: {missing[0]}"
            ),
        )
    values = [reading.value for reading in collected if reading.value is not None]
    if not values:
        return MetricReading(value=None, computed=False, reason="no inputs were supplied")
    if any(not math.isfinite(value) for value in values):
        return MetricReading(value=None, computed=False, reason="an input value was not finite")
    return MetricReading(value=sum(values) / len(values), computed=True)


@dataclass(frozen=True)
class EvidenceGap:
    """One thing the study needs and does not have."""

    phase: str
    gap: str
    consequence: str
    blocked_claims: tuple[str, ...] = ()
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "gap": self.gap,
            "consequence": self.consequence,
            "blocked_claims": list(self.blocked_claims),
            "source": self.source,
        }


#: Every gap this study currently has, in the order a reader should meet them.
EVIDENCE_GAPS: tuple[EvidenceGap, ...] = (
    EvidenceGap(
        phase="6",
        gap=(
            "No independently verified clean rainfall event. The one recorded "
            "event has operator-reported rainfall, a brine transfer with no "
            "recorded volume, and a post-event reading from the same person who "
            "reported the rain."
        ),
        consequence=(
            "MAE, RMSE and bias cannot be reported at all. The minimum is 3 clean "
            "verified events across 2 pans; the registry holds 0."
        ),
        blocked_claims=(
            "Any simulator accuracy figure.",
            "Any statement that SaltLens predicted an observed outcome.",
        ),
        source="experiments/events/records/ev-249b575de8234ff09b341e525ef5d4a1.json",
    ),
    EvidenceGap(
        phase="6",
        gap=(
            "No rain-gauge record. The 90 mm March total rests on the same field "
            "notebook as the post-event reading, and the earlier 19:30 IST instant "
            "had no supporting record and was superseded."
        ),
        consequence=(
            "Rainfall cannot be separated from the reading it is compared against, "
            "so the replay is a retrospective arithmetic check rather than a "
            "prediction graded against an independent input."
        ),
        blocked_claims=(
            "That the simulator was driven by an independently measured input.",
        ),
        source="North Pan field notebook, via the rainfall_timestamps audit history",
    ),
    EvidenceGap(
        phase="6",
        gap="No transfer or drainage volumes for any intervention.",
        consequence=(
            "An intervention's contribution to a salinity change cannot be "
            "separated from rainfall dilution, so an intervention-affected event "
            "can never be accuracy evidence."
        ),
        blocked_claims=(
            "Any accuracy figure drawn from an event with an intervention.",
        ),
        source="experiments/events/records/ev-249b575de8234ff09b341e525ef5d4a1.json",
    ),
    EvidenceGap(
        phase="7",
        gap=(
            "One event, on one pan, with a forecast-based September demonstration "
            "that explicitly involved no physical action."
        ),
        consequence=(
            "A multi-event study is not yet a study. Nothing here is a case series, "
            "let alone an inferential validation."
        ),
        blocked_claims=(
            "Any generalisation across events, pans or seasons.",
            "Any statistical significance claim.",
        ),
        source="experiments/events/",
    ),
    EvidenceGap(
        phase="8",
        gap=(
            "No ML training data, no labels, no split artifact, no checkpoint. "
            "app/ml and ml/ are infrastructure with nothing run through them."
        ),
        consequence=(
            "No ML metric of any kind exists. The readiness report says so rather "
            "than reporting a placeholder."
        ),
        blocked_claims=(
            "Any ML accuracy, R-squared or error figure.",
            "Any claim of improvement over a baseline.",
        ),
        source="ml/models/ (empty), ml_predictions (empty collection)",
    ),
    EvidenceGap(
        phase="9",
        gap=(
            "No matched followed/not-followed periods, and no production yield, "
            "salt recovery or brine loss figures. 11 operator decisions exist with "
            "no outcomes."
        ),
        consequence=(
            "No operational benefit can be quantified, in either direction. The "
            "figure being absent is not evidence that the effect is zero."
        ),
        blocked_claims=(
            "Any claim of reduced loss, improved recovery or increased production.",
            "Any before-and-after comparison.",
        ),
        source="operator_decisions collection (11 records, no outcomes)",
    ),
    EvidenceGap(
        phase="14",
        gap=(
            "Equation documentation is internally inconsistent: "
            "docs/evidence/equations_and_assumptions.md states a PSU conversion near "
            "1.25 while scripts/normalize_sources.py uses PSU_TO_G_L = 1.05."
        ),
        consequence=(
            "A reader following the documentation would recompute salinity "
            "differently from the system. One of the two is wrong and which one is "
            "not established."
        ),
        blocked_claims=(
            "Any figure a reviewer would reproduce from the documentation.",
        ),
        source="docs/evidence/equations_and_assumptions.md, scripts/normalize_sources.py",
    ),
    EvidenceGap(
        phase="14",
        gap=(
            "The salt-mass conservation check is tautological: mass_balance.py "
            "computes post-rain mass from initial mass and then checks that the "
            "two agree."
        ),
        consequence=(
            "The check cannot fail, so it provides no evidence that mass is "
            "conserved. It is a restatement of the assumption, not a test of it."
        ),
        blocked_claims=(
            "Any claim that the model was validated for mass conservation.",
        ),
        source="backend/app/services/mass_balance.py",
    ),
)


def evidence_gap_report() -> list[dict[str, Any]]:
    """Every gap, serialisable, for the final report and the dashboard."""
    return [gap.as_dict() for gap in EVIDENCE_GAPS]


__all__ = [
    "EVIDENCE_GAPS",
    "MIN_HOLDOUT_EVENTS",
    "MIN_HOLDOUT_ROWS",
    "MIN_OPERATIONAL_PAIRS",
    "NOT_YET_ASSESSED",
    "OPERATIONAL_CLAIMS",
    "AssessmentReport",
    "AssessmentStatus",
    "EvidenceGap",
    "MLReadinessInput",
    "OperationalReadinessInput",
    "ReadinessItem",
    "aggregate_runs",
    "assessment_status",
    "claim_requirements",
    "evidence_gap_report",
    "ml_readiness_report",
    "operational_readiness_report",
]
