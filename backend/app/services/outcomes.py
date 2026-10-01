"""Module 15 - recording what happened, and grading what was predicted.

This module turns an event into ground truth. An operator says what the pan
actually did; the service resolves the records the event should be judged
against, computes the prediction the mass balance would have made, and reports
the difference. Everything it does is shaped by one rule:

**Nothing is invented, in either direction.**

A missing actual is not filled, and neither is a missing prediction. If the
pre-event salinity was never recorded, the balance is not run and the event
carries no predicted salinity, because a prediction from an assumed starting
concentration would grade the simulator against a fiction. The same rule
governs the aggregates: an event that cannot be compared lowers the number of
comparable events, and is never counted as a zero error. An error metric that
quietly treats absent outcomes as perfect outcomes measures the recording
discipline rather than the model.

Two predictions are computed, and the difference between them is the point:

* from the rainfall that actually fell, which grades the **simulation** alone;
* from the recorded forecast rainfall, which grades the **forecast** and the
  simulation together.

The same pre-event state feeds both, so the gap between the two error figures
is the error the forecast introduced. Keeping them in one document is what makes
that visible without a second query.

The prediction is not accepted from the caller. It comes from Module 06's mass
balance, the only implementation of that arithmetic, and the run that produced it
is stored as a :class:`~app.models.simulation_run.SimulationRun` and linked from
the outcome, so the arithmetic behind any error figure can be replayed later
without trusting this module's summary of it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import HTTPException, status

from app.db.errors import DocumentNotFoundError
from app.db.indexes import (
    ACTUAL_OUTCOMES,
    OPERATOR_DECISIONS,
    PAN_OBSERVATIONS,
    RECOMMENDATIONS,
    SALT_PANS,
    SIMULATION_RUNS,
    WEATHER_OBSERVATIONS,
)
from app.db.repository import MongoRepository
from app.models.actual_outcome import (
    FORECAST_PREDICTION_SOURCE,
    SIMULATION_PREDICTION_SOURCE,
    ActualOutcome,
    MissingOutcomeInput,
    OutcomeErrorTerms,
    OutcomeLink,
)
from app.models.base import normalize_pan_id, utcnow
from app.models.enums import SimulationStatus
from app.models.operator_decision import OperatorDecision
from app.models.pan_observation import PanObservation
from app.models.recommendation import Recommendation
from app.models.salt_pan import SaltPan
from app.models.simulation_run import SimulationRun
from app.models.weather import WeatherObservation
from app.schemas.outcome import (
    ErrorStatisticsRead,
    EventOutcomeCreate,
    EventOutcomeListResponse,
    EventOutcomeRead,
    OutcomeComparisonSummary,
)
from app.services.mass_balance import (
    MASS_BALANCE_MODEL_NAME,
    SIMULATION_ASSUMPTIONS,
    SIMULATOR_DISCLAIMER,
    MassBalanceService,
    RainfallDilution,
    RainfallDilutionError,
)

#: Below this, two figures are the same number as far as a run's inputs go. The
#: mass balance is a few multiplications, so its own noise is far smaller than
#: this; the tolerance exists to compare floats, not to forgive a scenario.
INPUT_MATCH_TOLERANCE = 1e-9

#: An error smaller than this is zero. Guards against a predicted and an actual
#: figure that agree exactly but not to the last bit, which would otherwise be
#: reported to the operator as an error of 0.0000004 g/L in one direction.
ERROR_ZERO_TOLERANCE = 1e-12

#: How far back to look for a recommendation that was in force at the event.
#: Expired ones are skipped, so the scan needs room to walk past them; the newest
#: few are enough in practice and keep the query bounded.
RECOMMENDATION_SCAN_LIMIT = 20

# -- reasons an operator can act on -----------------------------------------

MISSING_ACTUAL_SALINITY = (
    "No post-event salinity was recorded for this event, so there is nothing to "
    "compare a prediction against. It stays uncompared until an operator "
    "measures it, and is never counted as a zero error."
)

MISSING_ACTUAL_RAINFALL = (
    "No rainfall figure was recorded for this event, so there is nothing to "
    "compare the forecast against. A forecast for an event whose rainfall was "
    "never measured cannot be graded, and is not graded as though it fell."
)

PREDICTION_NEEDS_SALINITY = (
    "The pre-event salinity was not recorded, so the mass balance has no "
    "starting concentration to dilute. Record it on the event, or link the "
    "observation that measured it."
)

PREDICTION_NEEDS_DEPTH = (
    "The pre-event water depth was not recorded. The balance starts from the "
    "initial brine volume, so without a depth there is nothing to dilute."
)

PREDICTION_NEEDS_RAINFALL = (
    "The rainfall was not recorded. The balance dilutes the brine by a rainfall "
    "depth, so with no depth recorded there is no event to predict."
)

PERCENTAGE_ERROR_UNDEFINED_FOR_ZERO_ACTUAL = (
    "Percentage error is undefined when the measured value is 0: it divides by "
    "the measurement, and there is none. The absolute error is still reported."
)

NO_COMPARABLE_EVENTS = (
    "No event in scope has both a prediction and a recorded measurement, so no "
    "mean exists. Events with a missing actual are left out of the average "
    "rather than counted as zero error."
)

#: Which primary key each explicit reference field points at. Spelled out rather
#: than derived from the field name, because ``event_id`` is a caller's own label
#: on an outcome while the other three are server-assigned keys elsewhere.
REFERENCE_KEYS: dict[str, str] = {
    "observation_id": "observation_id",
    "weather_forecast_id": "weather_id",
    "simulation_run_id": "run_id",
    "recommendation_id": "recommendation_id",
    "decision_id": "decision_id",
}

#: The provenance an operator may assert on an event, and the record whose own
#: provenance is checked for each. An operator can declare an event a worked
#: example; they cannot declare a demonstration-backed event a field measurement,
#: so the declaration is one-way.
DEMONSTRATION_SOURCES: tuple[str, ...] = (
    "pre_event_observation_data_source",
    "weather_forecast_source",
    "operator_declared",
)

OPERATOR_DECLARED_SOURCE = "operator_declared"


def demonstration_sources_for(
    observation: PanObservation | None,
    forecast: WeatherObservation | None,
    *,
    operator_declared: bool = False,
) -> list[str]:
    """Which of the records this event rests on were entered to demonstrate the system.

    Read from the provenance each linked record declares, not from the figures
    themselves. A demonstration reading is well-formed, so nothing about the number
    distinguishes it from a sensor log, and a heuristic that tried would either
    discard real events or admit demonstration ones -- the two failures that make
    a validation report worthless.

    An operator may add a declaration but never remove one: a worked example the
    service already recognised cannot be re-presented as a field measurement by
    unticking a box.
    """
    sources: list[str] = []
    for source_field, source in (
        ("pre_event_observation_data_source", getattr(observation, "data_source", None)),
        ("weather_forecast_source", getattr(forecast, "source", None)),
    ):
        value = getattr(source, "value", source)
        if value == "demonstration" and source_field not in sources:
            sources.append(source_field)
    if operator_declared and OPERATOR_DECLARED_SOURCE not in sources:
        sources.append(OPERATOR_DECLARED_SOURCE)
    return sources


@dataclass(frozen=True)
class ErrorStatistics:
    """Aggregate error over the events that could be compared."""

    label: str
    unit: str
    compared: int
    not_comparable: int
    coverage_percent: float | None
    mean_absolute_error: float | None
    mean_absolute_error_reason: str | None
    bias: float | None
    bias_reason: str | None
    largest_absolute_error: float | None

    def to_read(self) -> ErrorStatisticsRead:
        return ErrorStatisticsRead(
            label=self.label,
            unit=self.unit,
            compared=self.compared,
            not_comparable=self.not_comparable,
            coverage_percent=self.coverage_percent,
            mean_absolute_error=self.mean_absolute_error,
            mean_absolute_error_reason=self.mean_absolute_error_reason,
            bias=self.bias,
            bias_reason=self.bias_reason,
            largest_absolute_error=self.largest_absolute_error,
        )


def summarise_errors(
    pairs: Sequence[tuple[OutcomeErrorTerms, float | None]],
    *,
    label: str,
    unit: str,
) -> ErrorStatistics:
    """Reduce per-event comparisons to one set of aggregate figures.

    Only the comparable events contribute. An event whose measurement is missing
    is counted in ``not_comparable`` and left out of every mean, which is the
    whole point: including it would pull the average towards zero and turn
    unrecorded events into evidence that the model was right.
    """
    comparable = [
        (terms.signed_error, terms.absolute_error)
        for terms, actual in pairs
        if terms.is_comparable and actual is not None
    ]
    total = len(pairs)
    not_comparable = total - len(comparable)
    coverage = (len(comparable) / total * 100.0) if total else None

    if not comparable:
        return ErrorStatistics(
            label=label,
            unit=unit,
            compared=0,
            not_comparable=not_comparable,
            coverage_percent=coverage,
            mean_absolute_error=None,
            mean_absolute_error_reason=NO_COMPARABLE_EVENTS,
            bias=None,
            bias_reason=NO_COMPARABLE_EVENTS,
            largest_absolute_error=None,
        )

    signed = [item[0] for item in comparable]
    absolute = [item[1] for item in comparable]
    count = len(comparable)
    return ErrorStatistics(
        label=label,
        unit=unit,
        compared=count,
        not_comparable=not_comparable,
        coverage_percent=coverage,
        mean_absolute_error=sum(absolute) / count,
        mean_absolute_error_reason=None,
        bias=sum(signed) / count,
        bias_reason=None,
        largest_absolute_error=max(absolute),
    )


@dataclass(frozen=True)
class Prediction:
    """What the mass balance made of one event, or why it made nothing.

    ``dilution`` is the balance itself rather than a copy of one figure from it,
    so the run stored for the outcome records exactly the arithmetic that
    produced the prediction on the outcome. It is ``None`` whenever ``predicted``
    is, which is the only other case.
    """

    predicted: float | None = None
    dilution: RainfallDilution | None = None
    missing: list[MissingOutcomeInput] = field(default_factory=list)

    @property
    def is_computable(self) -> bool:
        return self.predicted is not None and self.dilution is not None


def compare(
    *,
    predicted: float | None,
    actual: float | None,
    predicted_from: str,
    missing_inputs: Sequence[MissingOutcomeInput] = (),
    actual_field: str | None = None,
    actual_missing_reason: str | None = None,
) -> OutcomeErrorTerms:
    """Build one predicted-against-actual block.

    An error is produced only when both figures exist, and the percentage error
    only when the measurement it divides by is not zero. Anything else comes back
    as the reasons it could not be computed.

    The actual operand is deliberately not stored here: it lives on the outcome
    itself, and duplicating it would give the document two answers to "what did
    the pan really measure".
    """
    gaps = list(missing_inputs)
    if actual is None:
        if actual_field and actual_missing_reason:
            gaps.append(
                MissingOutcomeInput(field=actual_field, reason=actual_missing_reason)
            )
        return OutcomeErrorTerms(
            predicted=predicted,
            predicted_from=predicted_from,
            missing_inputs=gaps,
        )

    if predicted is None:
        return OutcomeErrorTerms(
            predicted_from=predicted_from,
            missing_inputs=gaps,
        )

    signed = predicted - actual
    if abs(signed) < ERROR_ZERO_TOLERANCE:
        signed = 0.0
    if actual == 0:
        percentage: float | None = None
        percentage_reason: str | None = PERCENTAGE_ERROR_UNDEFINED_FOR_ZERO_ACTUAL
    else:
        percentage = signed / actual * 100.0
        percentage_reason = None

    return OutcomeErrorTerms(
        predicted=predicted,
        predicted_from=predicted_from,
        signed_error=signed,
        absolute_error=abs(signed),
        percentage_error_percent=percentage,
        percentage_error_reason=percentage_reason,
        missing_inputs=gaps,
    )


class OutcomeService:
    """Record events, resolve the records they are judged against, grade them."""

    def __init__(
        self,
        database: Any,
        mass_balance: MassBalanceService | None = None,
    ) -> None:
        self._database = database
        self._mass_balance = mass_balance or MassBalanceService()
        self._resolved_pans: MongoRepository[SaltPan] | None = None
        self._resolved_observations: MongoRepository[PanObservation] | None = None
        self._resolved_weather: MongoRepository[WeatherObservation] | None = None
        self._resolved_outcomes: MongoRepository[ActualOutcome] | None = None
        self._resolved_runs: MongoRepository[SimulationRun] | None = None
        self._resolved_recommendations: MongoRepository[Recommendation] | None = None
        self._resolved_decisions: MongoRepository[OperatorDecision] | None = None

    @property
    def _pan_repository(self) -> MongoRepository[SaltPan]:
        if self._resolved_pans is None:
            self._resolved_pans = MongoRepository(self._database, SALT_PANS, SaltPan)
        return self._resolved_pans

    @property
    def _observation_repository(self) -> MongoRepository[PanObservation]:
        if self._resolved_observations is None:
            self._resolved_observations = MongoRepository(
                self._database, PAN_OBSERVATIONS, PanObservation
            )
        return self._resolved_observations

    @property
    def _weather_repository(self) -> MongoRepository[WeatherObservation]:
        if self._resolved_weather is None:
            self._resolved_weather = MongoRepository(
                self._database, WEATHER_OBSERVATIONS, WeatherObservation
            )
        return self._resolved_weather

    @property
    def _outcome_repository(self) -> MongoRepository[ActualOutcome]:
        if self._resolved_outcomes is None:
            self._resolved_outcomes = MongoRepository(
                self._database, ACTUAL_OUTCOMES, ActualOutcome
            )
        return self._resolved_outcomes

    @property
    def _run_repository(self) -> MongoRepository[SimulationRun]:
        if self._resolved_runs is None:
            self._resolved_runs = MongoRepository(
                self._database, SIMULATION_RUNS, SimulationRun
            )
        return self._resolved_runs

    @property
    def _recommendation_repository(self) -> MongoRepository[Recommendation]:
        if self._resolved_recommendations is None:
            self._resolved_recommendations = MongoRepository(
                self._database, RECOMMENDATIONS, Recommendation
            )
        return self._resolved_recommendations

    @property
    def _decision_repository(self) -> MongoRepository[OperatorDecision]:
        if self._resolved_decisions is None:
            self._resolved_decisions = MongoRepository(
                self._database, OPERATOR_DECISIONS, OperatorDecision
            )
        return self._resolved_decisions

    # -- guards -----------------------------------------------------------

    @staticmethod
    def _normalized_pan_id(pan_id: str) -> str:
        normalized = normalize_pan_id(pan_id)
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="pan_id must not be blank.",
            )
        return normalized

    async def _require_unrecorded_event(self, pan_id: str, event_id: str) -> None:
        """Refuse a second record of an event already on file.

        An outcome is ground truth, and an event that has been measured twice is
        still one event. Allowing both to be stored would let a re-entered figure
        be counted twice in every mean, quietly inflating or deflating the error
        the module exists to measure — and nothing downstream could tell, because
        both documents are individually valid.

        There is no update route by design: a wrong measurement is corrected by
        recording the event afresh under a new `event_id`, not by editing the
        record that an earlier validation run may already have read.
        """
        existing = await self._outcome_repository.find(
            {"pan_id": pan_id, "event_id": event_id}, limit=1
        )
        if not existing:
            return
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Event “{event_id}” is already recorded for pan “{pan_id}” "
                f"(outcome {existing[0].outcome_id}). An event is measured once. If "
                "this measurement is wrong, record the corrected event under a "
                "new event_id rather than replacing a figure an earlier "
                "validation run may already have read."
            ),
        )

    async def _require_registered_pan(self, pan_id: str) -> SaltPan:
        """Return the pan, or explain that the identifier is unknown.

        An event belongs to a registered pan, so an unknown identifier is a 404
        rather than an outcome filed against a pan that does not exist.
        """
        normalized = self._normalized_pan_id(pan_id)
        try:
            return await self._pan_repository.get("pan_id", normalized)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No registered pan has the ID “{pan_id}”. Register the pan "
                    "before recording outcomes for it."
                ),
            ) from exc

    async def _require_same_pan(self, field: str, record_id: str, pan_id: str) -> Any:
        """Load an explicitly referenced record and check it belongs to the pan.

        A reference to another pan's record would silently compare an event
        against evidence from somewhere else, so it is refused rather than
        stored.
        """
        repository = {
            "observation_id": self._observation_repository,
            "weather_forecast_id": self._weather_repository,
            "simulation_run_id": self._run_repository,
            "recommendation_id": self._recommendation_repository,
            "decision_id": self._decision_repository,
        }[field]
        try:
            record = await repository.get(REFERENCE_KEYS[field], record_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{field} “{record_id}” does not exist. Reference a record "
                    "that is stored, or omit the reference and let the record be "
                    "selected for this event's own timestamp."
                ),
            ) from exc
        if record.pan_id != pan_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"{field} “{record_id}” belongs to pan “{record.pan_id}”, not "
                    f"to “{pan_id}”. An event is only compared against records from "
                    "its own pan."
                ),
            )
        return record

    # -- link selection ----------------------------------------------------

    async def _previous_observation(
        self, pan_id: str, at: datetime
    ) -> PanObservation | None:
        """The newest pan observation at or before the event.

        "At or before" rather than simply "newest", because a reading taken after
        the rain is not the state the rain fell on, and a backfilled observation
        from the week after the storm is correctly excluded.
        """
        records = await self._observation_repository.find(
            {"pan_id": pan_id, "timestamp": {"$lte": at}},
            sort=[("timestamp", -1)],
            limit=1,
        )
        return records[0] if records else None

    async def _forecast_covering(
        self, pan_id: str, at: datetime
    ) -> WeatherObservation | None:
        """The last forecast issued for an instant at or before the event.

        Selected by ``forecast_for`` rather than by issue time, so the record
        graded is the one that was pointing at this moment when the moment
        arrived. The query requires ``is_forecast`` rather than filtering after
        the fact, so an observed rainfall figure can never stand in for a
        forecast.
        """
        records = await self._weather_repository.find(
            {
                "pan_id": pan_id,
                "is_forecast": True,
                "forecast_for": {"$lte": at},
            },
            sort=[("forecast_for", -1)],
            limit=1,
        )
        return records[0] if records else None

    async def _recommendation_in_force(
        self, pan_id: str, at: datetime
    ) -> Recommendation | None:
        """The newest advice that had been issued and had not expired.

        Expired recommendations are skipped rather than credited to the event,
        because advice an operator had already been told to disregard is not what
        they acted on.
        """
        records = await self._recommendation_repository.find(
            {"pan_id": pan_id, "issued_at": {"$lte": at}},
            sort=[("issued_at", -1)],
            limit=RECOMMENDATION_SCAN_LIMIT,
        )
        for record in records:
            if record.expires_at is None or record.expires_at > at:
                return record
        return None

    # -- the prediction ----------------------------------------------------

    def _predict(
        self,
        *,
        pan: SaltPan,
        salinity_g_l: float | None,
        depth_cm: float | None,
        rainfall_mm: float | None,
        rainfall_field: str,
    ) -> Prediction:
        """Run the mass balance, or report exactly which input was missing.

        The balance is not run at all when an input is absent. Substituting a
        plausible depth or concentration would produce a confident number whose
        error term measures the guess rather than the physics, which is the one
        result this project must not publish.
        """
        gaps: list[MissingOutcomeInput] = []
        if salinity_g_l is None:
            gaps.append(
                MissingOutcomeInput(
                    field="pre_event_salinity_g_l", reason=PREDICTION_NEEDS_SALINITY
                )
            )
        if depth_cm is None:
            gaps.append(
                MissingOutcomeInput(
                    field="pre_event_depth_cm", reason=PREDICTION_NEEDS_DEPTH
                )
            )
        if rainfall_mm is None:
            gaps.append(
                MissingOutcomeInput(field=rainfall_field, reason=PREDICTION_NEEDS_RAINFALL)
            )
        if gaps:
            return Prediction(predicted=None, dilution=None, missing=gaps)

        try:
            dilution = self._mass_balance.evaluate(
                area_m2=pan.area_m2,
                # The one unit boundary in this module: a pan's depth is recorded
                # in centimetres everywhere else, the balance is in metres.
                initial_depth_m=depth_cm / 100.0,
                initial_salinity_g_l=salinity_g_l,
                rainfall_mm=rainfall_mm,
            )
        except RainfallDilutionError as exc:
            # The balance's own guard, quoted rather than paraphrased, so an
            # operator reads the same explanation here as in the simulator.
            field_name = "pre_event_depth_cm" if depth_cm == 0 else rainfall_field
            return Prediction(
                predicted=None,
                dilution=None,
                missing=[MissingOutcomeInput(field=field_name, reason=str(exc))],
            )

        return Prediction(
            predicted=dilution.post_rain_salinity_g_l,
            dilution=dilution,
            missing=[],
        )

    def _build_run(
        self,
        *,
        pan: SaltPan,
        outcome_id: str,
        event_id: str,
        dilution: RainfallDilution,
        rainfall_from: str,
    ) -> SimulationRun:
        """Store the exact computation behind a prediction.

        A ground-truth record is only as good as the ability to replay the
        prediction it grades, so the inputs and the outputs are kept with the run
        rather than left implicit in this service.
        """
        moment = utcnow()
        return SimulationRun(
            pan_id=pan.pan_id,
            status=SimulationStatus.SUCCEEDED,
            started_at=moment,
            finished_at=moment,
            inputs={
                "area_m2": dilution.area_m2,
                "initial_depth_m": dilution.initial_depth_m,
                "initial_salinity_g_l": dilution.initial_salinity_g_l,
                "rainfall_mm": dilution.rainfall_mm,
                "rainfall_from": rainfall_from,
                "outcome_id": outcome_id,
                "event_id": event_id,
            },
            outputs={
                "post_rain_salinity_g_l": dilution.post_rain_salinity_g_l,
                "salinity_drop_g_l": dilution.salinity_drop_g_l,
                "dilution_percent": dilution.dilution_percent,
                "post_rain_depth_m": dilution.post_rain_depth_m,
                "post_rain_volume_m3": dilution.post_rain_volume_m3,
                "model": MASS_BALANCE_MODEL_NAME,
                "disclaimer": SIMULATOR_DISCLAIMER,
                "assumptions": list(SIMULATION_ASSUMPTIONS),
            },
            # rainfall_cm is deliberately left null. The field is named in
            # centimetres while every weather record in this project stores
            # rainfall in millimetres, and nothing else populates it. The value
            # is in inputs.rainfall_mm, where the unit is in the name.
        )

    @staticmethod
    def _matches_event(
        run: SimulationRun,
        *,
        area_m2: float,
        salinity_g_l: float,
        depth_m: float,
        rainfall_mm: float,
    ) -> bool:
        """Whether a stored run was computed for exactly this event's inputs.

        A run is adopted as the prediction only when its recorded inputs are the
        event's inputs, so a what-if the operator glanced at for a different
        scenario cannot be reported as this event's prediction.
        """

        def close(left: Any, right: float) -> bool:
            return (
                isinstance(left, (int, float))
                and not isinstance(left, bool)
                and abs(float(left) - right) <= INPUT_MATCH_TOLERANCE
            )

        return (
            close(run.inputs.get("area_m2"), area_m2)
            and close(run.inputs.get("initial_salinity_g_l"), salinity_g_l)
            and close(run.inputs.get("initial_depth_m"), depth_m)
            and close(run.inputs.get("rainfall_mm"), rainfall_mm)
        )

    @staticmethod
    def _describe_run_inputs(run: SimulationRun) -> str:
        """Name a run's recorded inputs, in short, for an explanation."""
        return ", ".join(
            f"{name} {value}"
            for name, value in (
                ("salinity", run.inputs.get("initial_salinity_g_l")),
                ("depth", run.inputs.get("initial_depth_m")),
                ("rainfall", run.inputs.get("rainfall_mm")),
            )
            if value is not None
        ) or "inputs that were not recorded"

    # -- link blocks -------------------------------------------------------

    @staticmethod
    def _pan_state_link(observation: PanObservation | None) -> OutcomeLink:
        if observation is None:
            return OutcomeLink(
                kind="pan_state",
                summary=(
                    "No pan observation was recorded at or before this event, so "
                    "the state the event started from is unrecorded."
                ),
            )
        return OutcomeLink(
            kind="pan_state",
            record_id=observation.observation_id,
            timestamp=observation.timestamp,
            summary=(
                f"Pan observation {observation.observation_id} describes the pan "
                "immediately before the event."
            ),
            salinity_g_l=observation.salinity_g_l,
            water_depth_cm=observation.water_depth_cm,
            detail=(
                "The pre-event figures on the event are the operator's own entry. "
                "They are not copied from this record."
            ),
        )

    @staticmethod
    def _forecast_link(forecast: WeatherObservation | None) -> OutcomeLink:
        if forecast is None:
            return OutcomeLink(
                kind="weather_forecast",
                summary=(
                    "No weather forecast covered this event, so there is no "
                    "forecast to grade against what fell."
                ),
            )
        return OutcomeLink(
            kind="weather_forecast",
            record_id=forecast.weather_id,
            timestamp=forecast.forecast_for,
            summary=f"Weather forecast {forecast.weather_id} covered this event.",
            rainfall_mm=forecast.rainfall_mm,
            detail=f"Recorded with source {forecast.source}.",
        )

    @staticmethod
    def _recommendation_link(recommendation: Recommendation | None) -> OutcomeLink:
        if recommendation is None:
            return OutcomeLink(
                kind="recommendation",
                summary=(
                    "No recommendation was in force when this event happened, so "
                    "the outcome is not attributed to any advice."
                ),
            )
        return OutcomeLink(
            kind="recommendation",
            record_id=recommendation.recommendation_id,
            timestamp=recommendation.issued_at,
            summary=f"Recommendation “{recommendation.title}” was in force.",
            detail=f"Priority {recommendation.priority}.",
        )

    async def _decision_for_recommendation(
        self, recommendation: Recommendation | None
    ) -> OperatorDecision | None:
        """The operator's recorded decision about the in-force advice.

        An outcome is the last word on what happened, and the decision is the
        human step between the advice and it, so the chain can be rebuilt in one
        row: advice -> how it was received -> what followed. No decision filed is
        an ordinary state, and is reported as a link that is resolved only when a
        decision exists.
        """
        if recommendation is None:
            return None
        records = await self._decision_repository.find(
            {"recommendation_id": recommendation.recommendation_id}, limit=1
        )
        return records[0] if records else None

    @staticmethod
    def _decision_link(decision: OperatorDecision | None) -> OutcomeLink:
        if decision is None:
            return OutcomeLink(
                kind="operator_decision",
                summary=(
                    "No operator decision was recorded for the recommendation "
                    "that was in force, so the trace stops at the advice."
                ),
            )
        disposition = decision.decision.value if decision.decision is not None else "recorded"
        detail = f"Disposition: {disposition}."
        if decision.actual_action is not None:
            detail += f" Actual action: {decision.actual_action.value}."
        return OutcomeLink(
            kind="operator_decision",
            record_id=decision.decision_id,
            timestamp=decision.timestamp,
            summary=(
                f"Operator decision “{decision.decision_id}” ({disposition}) was "
                "recorded for the recommendation in force."
            ),
            detail=detail,
        )

    # -- writes ------------------------------------------------------------

    async def create(self, payload: EventOutcomeCreate) -> ActualOutcome:
        """Record one event, link it to its evidence and grade it.

        The order matters. The outcome is built and graded first, so the
        identifiers the run needs already exist; the run is stored next; the link
        to it is attached last; and the outcome is written once. Nothing in that
        order can leave a stored document claiming an error it does not have,
        because the model refuses to hold one.
        """
        pan = await self._require_registered_pan(payload.pan_id)
        await self._require_unrecorded_event(pan.pan_id, payload.event_id)
        at = payload.resolved_timestamp()
        references = payload.references

        observation = await self._previous_observation(pan.pan_id, at)
        forecast = await self._forecast_covering(pan.pan_id, at)
        recommendation = await self._recommendation_in_force(pan.pan_id, at)
        referenced_run: SimulationRun | None = None
        referenced_decision: OperatorDecision | None = None

        if references is not None:
            if references.observation_id:
                observation = await self._require_same_pan(
                    "observation_id", references.observation_id, pan.pan_id
                )
            if references.weather_forecast_id:
                forecast = await self._require_same_pan(
                    "weather_forecast_id", references.weather_forecast_id, pan.pan_id
                )
            if references.recommendation_id:
                recommendation = await self._require_same_pan(
                    "recommendation_id", references.recommendation_id, pan.pan_id
                )
            if references.simulation_run_id:
                referenced_run = await self._require_same_pan(
                    "simulation_run_id", references.simulation_run_id, pan.pan_id
                )
            if references.decision_id:
                referenced_decision = await self._require_same_pan(
                    "decision_id", references.decision_id, pan.pan_id
                )
                decision_recommendation = referenced_decision.recommendation_id
                if (
                    recommendation is not None
                    and decision_recommendation != recommendation.recommendation_id
                ):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=(
                            f"decision_id “{referenced_decision.decision_id}” "
                            f"answers recommendation “{decision_recommendation}”, not "
                            f"“{recommendation.recommendation_id}”. An event is only "
                            "tied to a decision about the recommendation that was in "
                            "force for it."
                        ),
                    )

        # The operator's recorded step between the advice and what happened: the
        # explicitly referenced decision, or the decision the in-force advice drew.
        decision = (
            referenced_decision
            if referenced_decision is not None
            else await self._decision_for_recommendation(recommendation)
        )

        # Two balances, not one: the same pre-event state against the rain that
        # fell and against the rain that was forecast. Which of the two the
        # simulator is judged on is a different question from whether the whole
        # chain was right, and answering it needs both.
        simulation = self._predict(
            pan=pan,
            salinity_g_l=payload.pre_event_salinity_g_l,
            depth_cm=payload.pre_event_depth_cm,
            rainfall_mm=payload.actual_rainfall_mm,
            rainfall_field=SIMULATION_PREDICTION_SOURCE,
        )
        forecast_based = self._predict(
            pan=pan,
            salinity_g_l=payload.pre_event_salinity_g_l,
            depth_cm=payload.pre_event_depth_cm,
            rainfall_mm=payload.forecast_rainfall_mm,
            rainfall_field=FORECAST_PREDICTION_SOURCE,
        )

        # Provenance is settled from the records actually linked, so an event is
        # graded as what it is rather than as what the operator called it. The
        # operator's own declaration can only add to this list.
        demonstration_sources = demonstration_sources_for(
            observation, forecast, operator_declared=payload.is_demonstration
        )

        outcome = ActualOutcome(
            pan_id=pan.pan_id,
            event_id=payload.event_id,
            timestamp=at,
            is_demonstration=bool(demonstration_sources),
            demonstration_sources=demonstration_sources,
            forecast_rainfall_mm=payload.forecast_rainfall_mm,
            actual_rainfall_mm=payload.actual_rainfall_mm,
            pre_event_salinity_g_l=payload.pre_event_salinity_g_l,
            pre_event_depth_cm=payload.pre_event_depth_cm,
            post_event_salinity_g_l=payload.post_event_salinity_g_l,
            post_event_depth_cm=payload.post_event_depth_cm,
            operator_action=payload.operator_action,
            harvest_outcome=payload.harvest_outcome,
            simulation_error=compare(
                predicted=simulation.predicted,
                actual=payload.post_event_salinity_g_l,
                predicted_from=SIMULATION_PREDICTION_SOURCE,
                missing_inputs=simulation.missing,
                actual_field="post_event_salinity_g_l",
                actual_missing_reason=MISSING_ACTUAL_SALINITY,
            ),
            forecast_simulation_error=compare(
                predicted=forecast_based.predicted,
                actual=payload.post_event_salinity_g_l,
                predicted_from=FORECAST_PREDICTION_SOURCE,
                missing_inputs=forecast_based.missing,
                actual_field="post_event_salinity_g_l",
                actual_missing_reason=MISSING_ACTUAL_SALINITY,
            ),
            rainfall_forecast_error=compare(
                predicted=payload.forecast_rainfall_mm,
                actual=payload.actual_rainfall_mm,
                predicted_from=FORECAST_PREDICTION_SOURCE,
                actual_field="actual_rainfall_mm",
                actual_missing_reason=MISSING_ACTUAL_RAINFALL,
            ),
            pan_state_link=self._pan_state_link(observation),
            weather_forecast_link=self._forecast_link(forecast),
            recommendation_link=self._recommendation_link(recommendation),
            decision_link=self._decision_link(decision),
        )

        await self._attach_simulation_run(
            outcome,
            pan=pan,
            payload=payload,
            prediction=simulation,
            referenced_run=referenced_run,
        )
        return await self._outcome_repository.insert(outcome)

    async def _attach_simulation_run(
        self,
        outcome: ActualOutcome,
        *,
        pan: SaltPan,
        payload: EventOutcomeCreate,
        prediction: Prediction,
        referenced_run: SimulationRun | None,
    ) -> None:
        """Point the outcome at the run that produced its prediction.

        A referenced run is adopted when it was computed for this event's inputs,
        because then its stored result *is* the prediction. Otherwise the service
        computes and stores its own run and says so: an outcome graded against a
        scenario the operator did not look at would misattribute the error to the
        wrong run.
        """
        if not prediction.is_computable or prediction.dilution is None:
            outcome.simulation_run_link = OutcomeLink(
                kind="simulation_run",
                summary=(
                    "No mass-balance run was recorded for this event, because the "
                    "prediction could not be computed from the recorded figures."
                ),
                detail="; ".join(entry.reason for entry in prediction.missing),
            )
            return

        if referenced_run is not None and self._matches_event(
            referenced_run,
            area_m2=pan.area_m2,
            salinity_g_l=prediction.dilution.initial_salinity_g_l,
            depth_m=prediction.dilution.initial_depth_m,
            rainfall_mm=prediction.dilution.rainfall_mm,
        ):
            outcome.simulation_run_link = OutcomeLink(
                kind="simulation_run",
                record_id=referenced_run.run_id,
                timestamp=referenced_run.created_at,
                summary=(
                    f"The prediction is the result of the referenced run "
                    f"{referenced_run.run_id}, which was computed for this event's "
                    "own inputs."
                ),
                detail=(
                    "Adopted rather than recomputed, so the error is measured "
                    "against the run the operator actually saw."
                ),
            )
            return

        run = self._build_run(
            pan=pan,
            outcome_id=outcome.outcome_id,
            event_id=outcome.event_id,
            dilution=prediction.dilution,
            rainfall_from=SIMULATION_PREDICTION_SOURCE,
        )
        stored = await self._run_repository.insert(run)
        detail = (
            "Computed from this event's pre-event figures and the recorded rainfall."
        )
        if referenced_run is not None:
            detail += (
                f" The referenced run {referenced_run.run_id} was computed for a "
                f"different scenario ({self._describe_run_inputs(referenced_run)}), "
                "so it was not adopted."
            )
        outcome.simulation_run_link = OutcomeLink(
            kind="simulation_run",
            record_id=stored.run_id,
            timestamp=stored.created_at,
            summary=f"Mass-balance run {stored.run_id} produced this prediction.",
            detail=detail,
        )

    # -- reads -------------------------------------------------------------

    async def get(self, outcome_id: str) -> ActualOutcome:
        try:
            return await self._outcome_repository.get("outcome_id", outcome_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No outcome has the ID “{outcome_id}”.",
            ) from exc

    async def list_outcomes(
        self,
        *,
        pan_id: str | None = None,
        event_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> EventOutcomeListResponse:
        """Recorded events newest first, optionally narrowed to a pan and window."""
        if start is not None and end is not None and start > end:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="start must not be later than end.",
            )

        query: dict[str, Any] = {}
        if pan_id:
            await self._require_registered_pan(pan_id)
            query["pan_id"] = self._normalized_pan_id(pan_id)
        if event_id:
            query["event_id"] = event_id

        window: dict[str, Any] = {}
        if start is not None:
            window["$gte"] = start
        if end is not None:
            window["$lte"] = end
        if window:
            query["timestamp"] = window

        total = await self._outcome_repository.count(query)
        outcomes = await self._outcome_repository.find(
            query, sort=[("timestamp", -1)], limit=limit, skip=offset
        )
        timestamps = [outcome.timestamp for outcome in outcomes]
        return EventOutcomeListResponse(
            items=[EventOutcomeRead.from_outcome(item) for item in outcomes],
            total=total,
            limit=limit,
            offset=offset,
            earliest_timestamp=min(timestamps) if timestamps else None,
            latest_timestamp=max(timestamps) if timestamps else None,
        )

    async def comparison(
        self, *, pan_id: str | None = None, limit: int = 1000
    ) -> OutcomeComparisonSummary:
        """Aggregate predicted-against-actual error over the recorded events.

        Every event in scope is counted. Only the comparable ones reach a mean,
        and the difference between the two counts is reported so a reader can see
        how much of the record is still waiting on a measurement.
        """
        query: dict[str, Any] = {}
        if pan_id:
            await self._require_registered_pan(pan_id)
            query["pan_id"] = self._normalized_pan_id(pan_id)
        outcomes = await self._outcome_repository.find(
            query, sort=[("timestamp", -1)], limit=limit
        )

        return OutcomeComparisonSummary(
            pan_id=query.get("pan_id"),
            total_outcomes=len(outcomes),
            simulation_error=summarise_errors(
                [
                    (outcome.simulation_error, outcome.post_event_salinity_g_l)
                    for outcome in outcomes
                ],
                label="Salinity error, predicted from the rainfall that actually fell",
                unit="g/L",
            ).to_read(),
            forecast_simulation_error=summarise_errors(
                [
                    (
                        outcome.forecast_simulation_error,
                        outcome.post_event_salinity_g_l,
                    )
                    for outcome in outcomes
                ],
                label="Salinity error, predicted from the forecast rainfall",
                unit="g/L",
            ).to_read(),
            rainfall_forecast_error=summarise_errors(
                [
                    (outcome.rainfall_forecast_error, outcome.actual_rainfall_mm)
                    for outcome in outcomes
                ],
                label="Forecast rainfall against the rainfall that fell",
                unit="mm",
            ).to_read(),
            events_missing_actual_salinity=sum(
                1 for outcome in outcomes if outcome.post_event_salinity_g_l is None
            ),
            events_with_operator_action=sum(
                1 for outcome in outcomes if outcome.operator_action
            ),
        )

    async def registry_counts(self) -> dict[str, int]:
        """Outcome totals for the module status endpoint.

        The comparable total is counted in the database rather than by loading
        every document, and it counts only outcomes that hold a real error. The
        difference between the two numbers is the recording backlog: events an
        operator still has to go back and measure.
        """
        return {
            "total_outcomes": await self._outcome_repository.count(),
            "comparable_outcomes": await self._outcome_repository.count(
                {"simulation_error.signed_error": {"$ne": None}}
            ),
        }


__all__ = [
    "ERROR_ZERO_TOLERANCE",
    "INPUT_MATCH_TOLERANCE",
    "MISSING_ACTUAL_RAINFALL",
    "MISSING_ACTUAL_SALINITY",
    "NO_COMPARABLE_EVENTS",
    "PERCENTAGE_ERROR_UNDEFINED_FOR_ZERO_ACTUAL",
    "PREDICTION_NEEDS_DEPTH",
    "PREDICTION_NEEDS_RAINFALL",
    "PREDICTION_NEEDS_SALINITY",
    "ErrorStatistics",
    "OutcomeService",
    "Prediction",
    "compare",
    "summarise_errors",
]
