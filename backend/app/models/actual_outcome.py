"""Module 15 - what actually happened, recorded against what was expected.

This is the ground truth of the research project: after a rainfall event or any
other operational event, the operator says what the pan really did. Everything
downstream that claims a model is accurate is measured against the documents in
this module, so the document's own discipline is what makes those claims
checkable.

**A missing actual stays missing.** An operator who could not reach the pan, or
whose refractometer failed, or who has simply not been back yet, records an event
with holes in it. Every hole is stored as ``None`` and reported as ``None``. No
field is interpolated from the previous reading, carried forward from the
forecast, averaged against a neighbouring event, or defaulted to zero. A missing
outcome therefore lowers the number of comparable events in the error statistics
rather than improving the model's apparent accuracy, which is the only behaviour
that makes a validation report worth anything.

**Errors only exist between two real numbers.** The comparison blocks below carry
a signed error, an absolute error and a percentage error, and every one of them
is ``None`` unless both a prediction and a measurement behind it were really
available. The percentage error is additionally ``None`` when the actual figure
is zero, because it divides by that figure; it is never rendered as ``0%``.

**Links are snapshots, not joins.** The prediction this outcome is graded against
depends on a pan observation, a weather forecast, a mass-balance run and possibly
a recommendation. Each of those is resolved when the event is recorded and copied
onto the outcome as a small link block, so a later backfilled observation cannot
retroactively change what an event was compared against.

The prediction itself is never accepted from a client. It is computed here by
Module 06's mass balance from the recorded pre-event state and the recorded
rainfall, and the run that produced it is stored so the arithmetic can be
replayed later without trusting this document's summary of it.
"""

from typing import Literal

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
    normalize_identifier,
    normalize_pan_id,
    utcnow,
)
from app.models.enums import HarvestOutcome
from app.models.pan_observation import (
    PAN_ID_PATTERN,
    SALINITY_RANGE,
    WATER_DEPTH_RANGE,
)
from app.models.weather import RAINFALL_RANGE

#: The kinds of record an outcome is linked to. Each link fills the subset
#: of figures that kind of record actually carries; the rest stay null.
#: ``operator_decision`` is the recorded disposition of the linked
#: recommendation, tying the outcome to the human step as well as the advice.
OutcomeLinkKind = Literal[
    "pan_state",
    "weather_forecast",
    "simulation_run",
    "recommendation",
    "operator_decision",
]

#: The fields an operator can supply, in the order the form reads them.
#:
#: Exported so the HTTP schema, the form and this model cannot drift apart on
#: what "the figures the operator typed" means.
OUTCOME_ACTUAL_FIELDS: tuple[str, ...] = (
    "actual_rainfall_mm",
    "post_event_salinity_g_l",
    "post_event_depth_cm",
    "harvest_outcome",
    "operator_action",
)

#: The rainfall figure each prediction block was computed from. The distinction
#: is the whole point of keeping two of them: a prediction made from the rain
#: that actually fell grades the *simulation*, while a prediction made from the
#: forecast grades the forecast and the simulation together.
SIMULATION_PREDICTION_SOURCE = "actual_rainfall_mm"
FORECAST_PREDICTION_SOURCE = "forecast_rainfall_mm"


class MissingOutcomeInput(BaseModel):
    """One figure a comparison needs and no record supplied.

    Reported rather than raised. An event nobody managed to measure is ordinary
    in the field, and an outcome with holes in it still has to be recordable and
    readable.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    field: str = Field(
        min_length=1,
        max_length=64,
        description="Which figure is unavailable.",
    )
    reason: str = Field(
        min_length=1,
        max_length=300,
        description="Why it is unavailable, in a sentence an operator can act on.",
    )


class OutcomeErrorTerms(BaseModel):
    """One predicted-against-actual comparison for a single event.

    Only the derived figures are stored. The *actual* operand lives on the
    outcome itself, because duplicating it here would give the document two
    answers to "what did the pan really measure" and they could drift apart.

    The two salinity blocks differ only in which rainfall figure produced the
    prediction, which is recorded in :attr:`predicted_from`.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    predicted: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description="The figure the model produced before the event.",
    )
    predicted_from: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Which recorded rainfall figure the prediction was computed from: "
            "'actual_rainfall_mm' grades the simulation on its own, "
            "'forecast_rainfall_mm' grades the forecast with it."
        ),
    )
    signed_error: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description="predicted - actual. Positive means the model ran high.",
    )
    absolute_error: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description="The magnitude of the signed error; this event's contribution to MAE.",
    )
    percentage_error_percent: float | None = Field(
        default=None,
        allow_inf_nan=False,
        description="(signed_error / actual) x 100, or null when actual is 0.",
    )
    percentage_error_reason: str | None = Field(
        default=None,
        max_length=300,
        description="Why the percentage error is null, when it is.",
    )
    missing_inputs: list[MissingOutcomeInput] = Field(default_factory=list)

    @property
    def is_comparable(self) -> bool:
        """Whether both operands existed.

        Derived rather than stored so it cannot drift from the error it
        summarises: an error only exists when a prediction and a measurement
        both did.
        """
        return self.signed_error is not None

    @property
    def missing_fields(self) -> list[str]:
        """The names of the figures this comparison is waiting on."""
        return [entry.field for entry in self.missing_inputs]


def uncompared(predicted_from: str) -> OutcomeErrorTerms:
    """A comparison that could not be made, with nothing filled in.

    Used as the default for every block on a fresh outcome, so a document never
    claims a comparison it did not make.
    """
    return OutcomeErrorTerms(predicted_from=predicted_from)


class OutcomeLink(BaseModel):
    """A pointer to the record an outcome is graded against, with its details.

    Stored rather than re-joined on read. The evidence behind a recorded event
    must not change when a newer observation arrives or a forecast is superseded;
    an outcome is a statement about one moment, and the link says which one.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    kind: OutcomeLinkKind
    record_id: str | None = Field(
        default=None,
        max_length=64,
        description="Identifier of the linked record, when one was found.",
    )
    timestamp: UtcDateTime | None = Field(
        default=None,
        description="The instant the linked record describes.",
    )
    summary: str = Field(
        min_length=1,
        max_length=300,
        description="One sentence naming the record and what it contributes.",
    )
    salinity_g_l: float | None = Field(default=None, allow_inf_nan=False)
    water_depth_cm: float | None = Field(default=None, allow_inf_nan=False)
    rainfall_mm: float | None = Field(default=None, allow_inf_nan=False)
    detail: str | None = Field(
        default=None,
        max_length=500,
        description="Extra context, such as how the record was selected.",
    )

    @property
    def is_resolved(self) -> bool:
        """Whether a record was actually found for this link."""
        return self.record_id is not None


class ActualOutcome(BaseDocument):
    outcome_id: str = Field(
        default_factory=lambda: new_id("outcome"),
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        validation_alias=AliasChoices("outcome_id", "actual_outcome_id"),
    )
    pan_id: str = Field(min_length=1, max_length=64, pattern=PAN_ID_PATTERN)
    event_id: str = Field(min_length=1, max_length=64)

    # -- what the event was expected to be ----------------------------------

    #: The rainfall the forecast expected. Typed by the operator, or left null.
    #: Never copied from the linked forecast record automatically: the operator
    #: is recording what they were told, and the link says what the record says.
    forecast_rainfall_mm: float | None = Field(
        default=None, allow_inf_nan=False, **RAINFALL_RANGE
    )

    # -- what the pan was before the event ----------------------------------

    pre_event_salinity_g_l: float | None = Field(
        default=None, allow_inf_nan=False, **SALINITY_RANGE
    )
    pre_event_depth_cm: float | None = Field(
        default=None, allow_inf_nan=False, **WATER_DEPTH_RANGE
    )

    # -- what actually happened ---------------------------------------------

    actual_rainfall_mm: float | None = Field(
        default=None, allow_inf_nan=False, **RAINFALL_RANGE
    )
    post_event_salinity_g_l: float | None = Field(
        default=None, allow_inf_nan=False, **SALINITY_RANGE
    )
    post_event_depth_cm: float | None = Field(
        default=None, allow_inf_nan=False, **WATER_DEPTH_RANGE
    )
    operator_action: str | None = Field(default=None, max_length=2000)

    #: ``none`` is a recorded outcome ("nothing was harvested") and is not the
    #: same as ``None``, which is "not recorded and not applicable".
    harvest_outcome: HarvestOutcome | None = None

    timestamp: UtcDateTime = Field(
        default_factory=utcnow,
        validation_alias=AliasChoices("timestamp", "recorded_at"),
    )
    created_at: UtcDateTime = Field(default_factory=utcnow)

    # -- where the figures behind this event came from ----------------------

    #: Whether this event rests on demonstration records, and which of them.
    #:
    #: An outcome is the ground truth every accuracy claim is measured against,
    #: so an event graded from a worked example must not reach a field-validation
    #: mean. The flag is derived from the provenance of the records the service
    #: actually linked -- the pan observation and the weather forecast -- rather
    #: than from whether the numbers look plausible, because a demonstration
    #: reading is indistinguishable from a real one by inspection. That is the
    #: point of it being a well-formed reading.
    #:
    #: Naming each contributing source rather than only the whole means an event
    #: assembled from one real and one demonstration record says which was which.
    is_demonstration: bool = False
    demonstration_sources: list[str] = Field(default_factory=list)

    # -- what the event is compared against ---------------------------------

    #: Predicted from the rainfall that actually fell, graded against the
    #: recorded post-event salinity. This is the simulation's own error.
    simulation_error: OutcomeErrorTerms = Field(
        default_factory=lambda: uncompared(SIMULATION_PREDICTION_SOURCE)
    )

    #: Predicted from the recorded forecast rainfall, graded against the same
    #: actual. Any error here is the forecast's and the simulation's together.
    forecast_simulation_error: OutcomeErrorTerms = Field(
        default_factory=lambda: uncompared(FORECAST_PREDICTION_SOURCE)
    )

    #: The forecast against the rainfall that fell, in millimetres.
    rainfall_forecast_error: OutcomeErrorTerms = Field(
        default_factory=lambda: uncompared(FORECAST_PREDICTION_SOURCE)
    )

    # -- the records this outcome is tied to ---------------------------------

    pan_state_link: OutcomeLink | None = None
    weather_forecast_link: OutcomeLink | None = None
    simulation_run_link: OutcomeLink | None = None
    recommendation_link: OutcomeLink | None = None
    #: The operator's recorded decision about the linked recommendation, when one
    #: was filed. What completes the chain advice -> operator decision -> outcome.
    decision_link: OutcomeLink | None = None

    @field_validator("pan_id", mode="before")
    @classmethod
    def _normalize_pan_id(cls, value):
        return normalize_pan_id(value)

    @field_validator("event_id", mode="before")
    @classmethod
    def _normalize_event_id(cls, value):
        return normalize_identifier(value)

    @model_validator(mode="after")
    def _errors_need_both_operands(self) -> "ActualOutcome":
        """An error may only exist where a prediction and a real measurement do.

        This is the invariant that keeps a missing actual from acquiring an
        error. It is enforced on the document rather than trusted from the
        service, so a hand-edited or imported record cannot invent an error for
        a figure nobody measured.
        """
        pairs = (
            ("simulation_error", self.simulation_error, self.post_event_salinity_g_l),
            (
                "forecast_simulation_error",
                self.forecast_simulation_error,
                self.post_event_salinity_g_l,
            ),
            (
                "rainfall_forecast_error",
                self.rainfall_forecast_error,
                self.actual_rainfall_mm,
            ),
        )
        for name, terms, actual in pairs:
            if terms.signed_error is None:
                continue
            if terms.predicted is None or actual is None:
                raise ValueError(
                    f"{name} carries an error without both a prediction and a "
                    "recorded actual value"
                )
        return self

    @property
    def has_actual_measurement(self) -> bool:
        """Whether any measured outcome figure was recorded."""
        return any(
            getattr(self, field) is not None
            for field in ("actual_rainfall_mm", "post_event_salinity_g_l", "post_event_depth_cm")
        )

    @property
    def is_comparable(self) -> bool:
        """Whether this event can grade a salinity prediction at all."""
        return self.simulation_error.is_comparable

    @property
    def is_field_outcome(self) -> bool:
        """Whether this event is eligible for a field-validation mean.

        Both a real measurement *and* a non-demonstration provenance. The two are
        separate requirements: an event can carry a measured salinity and still be
        a worked example, and an event on genuine records can still be awaiting the
        measurement that would make it comparable.
        """
        return self.has_actual_measurement and not self.is_demonstration

    @property
    def event_instant(self) -> UtcDateTime:
        """The alias the rest of the project uses for the instant a record describes."""
        return self.timestamp


__all__ = [
    "FORECAST_PREDICTION_SOURCE",
    "OUTCOME_ACTUAL_FIELDS",
    "SIMULATION_PREDICTION_SOURCE",
    "ActualOutcome",
    "MissingOutcomeInput",
    "OutcomeErrorTerms",
    "OutcomeLink",
    "OutcomeLinkKind",
    "uncompared",
]
