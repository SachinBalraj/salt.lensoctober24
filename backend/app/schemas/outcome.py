"""Module 15 - the HTTP contract for recording what actually happened.

The create schema is the operator's side of the record, and it is narrow on
purpose. It accepts the figures the operator typed and nothing else: in
particular there is no field for a predicted salinity, an error or a mean
absolute error, because a client that can post its own answer to "how accurate
was the model" would make the whole module worthless.

The read schema is wider than the model, because a predicted-against-actual
record is only interpretable alongside what it could not be compared against.
Each comparison therefore carries its operands, its errors, and a list of the
figures it is still waiting on, with a sentence saying which record or entry
would supply each one.

Nothing here fills a gap. A missing actual is reported as null in every
direction it could be summarised, and the aggregate statistics count it as a
non-comparable event rather than as a zero error.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.actual_outcome import (
    OUTCOME_ACTUAL_FIELDS,
    ActualOutcome,
    MissingOutcomeInput,
    OutcomeErrorTerms,
    OutcomeLink,
    OutcomeLinkKind,
)
from app.models.enums import HarvestOutcome
from app.models.pan_observation import (
    PAN_ID_PATTERN,
    SALINITY_RANGE,
    WATER_DEPTH_RANGE,
)
from app.models.weather import RAINFALL_RANGE
from app.schemas.observation import MAX_FUTURE_SKEW

#: An operator action is a sentence or two about what a person did, so 2000
#: characters is generous rather than a limit anyone will meet.
MAX_OPERATOR_ACTION_LENGTH = 2000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OutcomeReferencesCreate(BaseModel):
    """Records the operator is explicitly tying this event to.

    Optional. When a reference is absent the service selects the record itself:
    the newest pan observation at or before the event, the forecast that covered
    it, the most recent in-force recommendation. Supplying a reference is how an
    operator points the outcome at, say, the specific what-if run they actually
    looked at before the rain arrived.

    Every identifier is checked against the pan on the payload, so a reference
    to another pan's record is refused rather than quietly recorded.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    observation_id: str | None = Field(
        default=None,
        max_length=64,
        description="The pan observation describing the state before the event.",
        examples=["obs-2f1c9a0e5b7d4c3a9f0e1b2c3d4e5f60"],
    )
    weather_forecast_id: str | None = Field(
        default=None,
        max_length=64,
        description="The weather forecast that covered the event.",
        examples=["wx-9a8b7c6d5e4f30211f0e9d8c7b6a5f40"],
    )
    simulation_run_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "A stored mass-balance run whose prediction this event grades. When "
            "no run is named the service records the run it computed for this "
            "event, or explains why it could not compute one."
        ),
        examples=["run-5e4d3c2b1a0f9e8d7c6b5a4f3e2d1c0b"],
    )
    recommendation_id: str | None = Field(
        default=None,
        max_length=64,
        description="The recommendation that was in force when the event happened.",
        examples=["rec-1f2e3d4c5b6a79887766554433221100"],
    )
    decision_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "The recorded operator decision to tie this event to. When absent the "
            "service links the newest decision filed for the recommendation that "
            "was in force, so advice, how it was received and what happened can be "
            "rebuilt in one row."
        ),
        examples=["decision-7c6b5a4938271605f4e3d2c1b0a9f8e"],
    )


class EventOutcomeCreate(BaseModel):
    """One event, recorded as the operator found it.

    Every measurement is optional individually. What is not optional is that the
    record says *something* about what happened: at least one measured figure,
    a recorded harvest result or a description of the operator's action.
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    pan_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=PAN_ID_PATTERN,
        description="The pan this event happened in. Matched case-insensitively.",
        examples=["pans-2026-a01"],
    )
    event_id: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "The operator's name for this event, e.g. a storm or a gate "
            "operation. Group outcomes by it to compare one storm across pans."
        ),
        examples=["storm-2026-03-14"],
    )
    timestamp: datetime | None = Field(
        default=None,
        description=(
            "When the event happened, ISO 8601. Defaults to the moment the "
            "record is saved. Supply a value to backdate an entry."
        ),
        examples=["2026-03-14T18:00:00Z"],
    )

    forecast_rainfall_mm: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **RAINFALL_RANGE,
        description=(
            "The rainfall the forecast expected. Leave blank if no forecast was "
            "issued; it is never filled in from the forecast record for you."
        ),
    )
    actual_rainfall_mm: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **RAINFALL_RANGE,
        description="The rainfall that actually fell. Zero is a real measurement.",
    )
    pre_event_salinity_g_l: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **SALINITY_RANGE,
        description=(
            "Brine salinity before the event. Without it no prediction can be "
            "computed, and the event cannot grade the simulator."
        ),
    )
    post_event_salinity_g_l: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **SALINITY_RANGE,
        description=(
            "Brine salinity after the event. Leave blank if it was not measured: "
            "the comparison then stays open rather than being closed with a guess."
        ),
    )
    pre_event_depth_cm: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **WATER_DEPTH_RANGE,
        description="Water depth before the event. Needed to compute a prediction.",
    )
    post_event_depth_cm: float | None = Field(
        default=None,
        allow_inf_nan=False,
        **WATER_DEPTH_RANGE,
        description="Water depth after the event.",
    )
    operator_action: str | None = Field(
        default=None,
        max_length=MAX_OPERATOR_ACTION_LENGTH,
        description=(
            "What the operator did about the event, in their own words. This is "
            "the field that explains an event the model cannot."
        ),
    )
    harvest_outcome: HarvestOutcome | None = Field(
        default=None,
        description=(
            "The result of any harvest. `none` records that nothing was "
            "harvested; omitting the field records that no harvest was attempted "
            "or none has been reported yet."
        ),
    )
    references: OutcomeReferencesCreate | None = Field(
        default=None,
        description=(
            "Records to tie this event to explicitly. Omit them and the service "
            "selects the relevant records for the event's own timestamp."
        ),
    )
    is_demonstration: bool = Field(
        default=False,
        description=(
            "Declare this event a worked example rather than a field measurement. "
            "Set it when the figures were entered to demonstrate the system or to "
            "rehearse a form. The service derives the same label from the "
            "provenance of the observation and forecast it links, so this can only "
            "add to what the records already say: declaring false does not turn a "
            "demonstration-backed event into a field measurement."
        ),
        examples=[True, False],
    )

    @model_validator(mode="after")
    def _require_something_recorded(self) -> EventOutcomeCreate:
        if all(getattr(self, field) is None for field in OUTCOME_ACTUAL_FIELDS):
            raise ValueError(
                "Record at least one measured outcome, a harvest result or the "
                "operator action. An event that says nothing about what happened "
                "cannot be compared with anything."
            )
        return self

    @model_validator(mode="after")
    def _reject_future_timestamp(self) -> EventOutcomeCreate:
        if self.timestamp is not None and self.timestamp > _now() + MAX_FUTURE_SKEW:
            raise ValueError(
                "timestamp cannot be in the future. An event describes a moment "
                "that has already happened."
            )
        return self

    def resolved_timestamp(self) -> datetime:
        return self.timestamp if self.timestamp is not None else _now()


class OutcomeLinkRead(BaseModel):
    """One record an outcome is tied to, or the reason there was none."""

    model_config = ConfigDict(from_attributes=True)

    kind: OutcomeLinkKind
    record_id: str | None = None
    timestamp: datetime | None = None
    summary: str
    is_resolved: bool = Field(
        description="False when no record of this kind was found for the event."
    )
    salinity_g_l: float | None = None
    water_depth_cm: float | None = None
    rainfall_mm: float | None = None
    detail: str | None = None

    @classmethod
    def from_link(cls, link: OutcomeLink) -> OutcomeLinkRead:
        return cls(
            kind=link.kind,
            record_id=link.record_id,
            timestamp=link.timestamp,
            summary=link.summary,
            is_resolved=link.is_resolved,
            salinity_g_l=link.salinity_g_l,
            water_depth_cm=link.water_depth_cm,
            rainfall_mm=link.rainfall_mm,
            detail=link.detail,
        )


class OutcomeLinkSetRead(BaseModel):
    """The links to the records this event is compared against.

    Present whether or not a record was found, because "nothing was linked" is
    information: it says the event had no forecast, or no advice in force, or no
    prediction could be computed for it — and no operator decision had been filed.
    """

    model_config = ConfigDict(from_attributes=True)

    pan_state: OutcomeLinkRead | None = None
    weather_forecast: OutcomeLinkRead | None = None
    simulation_run: OutcomeLinkRead | None = None
    recommendation: OutcomeLinkRead | None = None
    operator_decision: OutcomeLinkRead | None = None

    @classmethod
    def from_outcome(cls, outcome: ActualOutcome) -> OutcomeLinkSetRead:
        return cls(
            pan_state=(
                OutcomeLinkRead.from_link(outcome.pan_state_link)
                if outcome.pan_state_link
                else None
            ),
            weather_forecast=(
                OutcomeLinkRead.from_link(outcome.weather_forecast_link)
                if outcome.weather_forecast_link
                else None
            ),
            simulation_run=(
                OutcomeLinkRead.from_link(outcome.simulation_run_link)
                if outcome.simulation_run_link
                else None
            ),
            recommendation=(
                OutcomeLinkRead.from_link(outcome.recommendation_link)
                if outcome.recommendation_link
                else None
            ),
            operator_decision=(
                OutcomeLinkRead.from_link(outcome.decision_link)
                if outcome.decision_link
                else None
            ),
        )


class OutcomeErrorTermsRead(BaseModel):
    """One comparison, with both operands and every error derived from them."""

    model_config = ConfigDict(from_attributes=True)

    predicted: float | None = None
    actual: float | None = Field(
        default=None,
        description="The measured figure, copied from the outcome itself.",
    )
    predicted_from: str = Field(
        description="Which recorded rainfall figure produced the prediction."
    )
    predicted_from_label: str = Field(
        description="That field in words, so the reader need not know the schema."
    )
    unit: str = Field(description="The unit both operands and the errors share.")
    signed_error: float | None = Field(
        default=None,
        description="predicted - actual. Positive means the model ran high.",
    )
    absolute_error: float | None = Field(
        default=None,
        description="|signed_error|; this event's contribution to the mean absolute error.",
    )
    percentage_error_percent: float | None = Field(
        default=None,
        description=(
            "(signed_error / actual) x 100. Null when the actual figure is 0, "
            "where the ratio does not exist."
        ),
    )
    percentage_error_reason: str | None = None
    is_comparable: bool = Field(
        description=(
            "True only when a prediction and a real measurement both existed. "
            "A false value is not a zero error."
        )
    )
    missing_inputs: list[MissingOutcomeInput] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)

    @classmethod
    def from_terms(
        cls,
        terms: OutcomeErrorTerms,
        *,
        actual: float | None,
        unit: str,
        predicted_from_label: str,
    ) -> OutcomeErrorTermsRead:
        return cls(
            predicted=terms.predicted,
            actual=actual,
            predicted_from=terms.predicted_from,
            predicted_from_label=predicted_from_label,
            unit=unit,
            signed_error=terms.signed_error,
            absolute_error=terms.absolute_error,
            percentage_error_percent=terms.percentage_error_percent,
            percentage_error_reason=terms.percentage_error_reason,
            is_comparable=terms.is_comparable,
            missing_inputs=list(terms.missing_inputs),
            missing_fields=terms.missing_fields,
        )


class EventOutcomeRead(BaseModel):
    """A recorded event, exactly as it happened, plus what it is compared to."""

    model_config = ConfigDict(from_attributes=True)

    outcome_id: str
    pan_id: str
    event_id: str
    timestamp: datetime
    created_at: datetime

    forecast_rainfall_mm: float | None = None
    actual_rainfall_mm: float | None = None
    pre_event_salinity_g_l: float | None = None
    pre_event_depth_cm: float | None = None
    post_event_salinity_g_l: float | None = None
    post_event_depth_cm: float | None = None
    operator_action: str | None = None
    harvest_outcome: HarvestOutcome | None = None
    is_demonstration: bool = Field(
        default=False,
        description=(
            "True when this event rests on demonstration records. An event can "
            "carry real measurements and still be a worked example, so this is "
            "reported separately from whether anything was measured."
        ),
    )
    demonstration_sources: list[str] = Field(
        default_factory=list,
        description=(
            "Which linked records were entered to demonstrate the system: "
            "'pre_event_observation_data_source', 'weather_forecast_source' or "
            "'operator_declared'."
        ),
    )

    simulation_error: OutcomeErrorTermsRead = Field(
        description=(
            "Predicted post-event salinity from the rainfall that actually fell, "
            "against the salinity that was measured. The simulator's own error."
        )
    )
    forecast_simulation_error: OutcomeErrorTermsRead = Field(
        description=(
            "Predicted post-event salinity from the recorded forecast rainfall, "
            "against the same measurement. The forecast's and the simulator's "
            "error together."
        )
    )
    rainfall_forecast_error: OutcomeErrorTermsRead = Field(
        description="The forecast rainfall against the rainfall that fell."
    )

    links: OutcomeLinkSetRead
    has_actual_measurement: bool = Field(
        description="Whether any measured outcome figure was recorded at all."
    )
    is_comparable: bool = Field(
        description="Whether this event grades the salinity prediction."
    )

    @classmethod
    def from_outcome(cls, outcome: ActualOutcome) -> EventOutcomeRead:
        return cls(
            outcome_id=outcome.outcome_id,
            pan_id=outcome.pan_id,
            event_id=outcome.event_id,
            timestamp=outcome.timestamp,
            created_at=outcome.created_at,
            forecast_rainfall_mm=outcome.forecast_rainfall_mm,
            actual_rainfall_mm=outcome.actual_rainfall_mm,
            pre_event_salinity_g_l=outcome.pre_event_salinity_g_l,
            pre_event_depth_cm=outcome.pre_event_depth_cm,
            post_event_salinity_g_l=outcome.post_event_salinity_g_l,
            post_event_depth_cm=outcome.post_event_depth_cm,
            operator_action=outcome.operator_action,
            harvest_outcome=outcome.harvest_outcome,
            is_demonstration=outcome.is_demonstration,
            demonstration_sources=list(outcome.demonstration_sources),
            simulation_error=OutcomeErrorTermsRead.from_terms(
                outcome.simulation_error,
                actual=outcome.post_event_salinity_g_l,
                unit="g/L",
                predicted_from_label="the rainfall that actually fell",
            ),
            forecast_simulation_error=OutcomeErrorTermsRead.from_terms(
                outcome.forecast_simulation_error,
                actual=outcome.post_event_salinity_g_l,
                unit="g/L",
                predicted_from_label="the forecast rainfall",
            ),
            rainfall_forecast_error=OutcomeErrorTermsRead.from_terms(
                outcome.rainfall_forecast_error,
                actual=outcome.actual_rainfall_mm,
                unit="mm",
                predicted_from_label="the forecast rainfall",
            ),
            links=OutcomeLinkSetRead.from_outcome(outcome),
            has_actual_measurement=outcome.has_actual_measurement,
            is_comparable=outcome.is_comparable,
        )


class EventOutcomeListResponse(BaseModel):
    """A page of recorded events, newest first."""

    items: list[EventOutcomeRead] = Field(default_factory=list)
    total: int = Field(ge=0, description="Events matching the filter, ignoring paging.")
    limit: int = Field(ge=1, le=1000)
    offset: int = Field(ge=0)
    earliest_timestamp: datetime | None = None
    latest_timestamp: datetime | None = None


class ErrorStatisticsRead(BaseModel):
    """Aggregate error over the events that could be compared.

    ``compared`` and ``not_comparable`` are reported side by side on purpose: a
    mean absolute error over two of twenty events says nothing on its own, and
    hiding the other eighteen would be how a weak model comes to look strong.
    """

    label: str = Field(description="What is being measured, in words.")
    unit: str
    compared: int = Field(
        ge=0, description="Events with both a prediction and a real measurement."
    )
    not_comparable: int = Field(
        ge=0, description="Events that could not be compared, for a stated reason each."
    )
    coverage_percent: float | None = Field(
        default=None,
        ge=0,
        le=100,
        description="compared as a percentage of the events in scope, or null when none.",
    )
    mean_absolute_error: float | None = Field(
        default=None,
        description="The mean of the comparable events' absolute errors, or null.",
    )
    mean_absolute_error_reason: str | None = Field(
        default=None, description="Why the mean is null, when it is."
    )
    bias: float | None = Field(
        default=None,
        description=(
            "The mean of the comparable events' signed errors. Positive means "
            "the model ran high on average."
        ),
    )
    bias_reason: str | None = None
    largest_absolute_error: float | None = Field(
        default=None, description="The worst comparable event, which is worth reading."
    )


class OutcomeComparisonSummary(BaseModel):
    """Predicted against actual, over every event in scope."""

    pan_id: str | None = Field(
        default=None, description="The pan summarised, or null for every pan."
    )
    total_outcomes: int = Field(
        ge=0, description="Recorded events considered by these statistics."
    )
    simulation_error: ErrorStatisticsRead = Field(
        description=(
            "Salinity errors of the predictions made from the rainfall that "
            "actually fell. This is the simulator's accuracy."
        )
    )
    forecast_simulation_error: ErrorStatisticsRead = Field(
        description="Salinity errors of the predictions made from the forecast rainfall."
    )
    rainfall_forecast_error: ErrorStatisticsRead = Field(
        description="How far the forecast rainfall was from the rain that fell."
    )
    events_missing_actual_salinity: int = Field(
        ge=0,
        description=(
            "Events with no recorded post-event salinity. They are excluded from "
            "the salinity statistics and are not counted as zero error."
        ),
    )
    events_with_operator_action: int = Field(
        ge=0,
        description="Events where the operator described what they did about them.",
    )


__all__ = [
    "MAX_FUTURE_SKEW",
    "MAX_OPERATOR_ACTION_LENGTH",
    "ErrorStatisticsRead",
    "EventOutcomeCreate",
    "EventOutcomeListResponse",
    "EventOutcomeRead",
    "OutcomeComparisonSummary",
    "OutcomeErrorTermsRead",
    "OutcomeLinkRead",
    "OutcomeLinkSetRead",
    "OutcomeReferencesCreate",
]
