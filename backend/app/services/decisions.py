"""Module 12 - the decision-support engine: twin + physics + ML + rule -> one action.

This is the module that answers the operational question, and its whole design
follows from the fact that three different kinds of knowledge are being combined
and must not be confused.

**The three questions are separate, and the engine keeps them separate.**

* *What is estimated risk?* — the deployed model's answer, read from a stored
  prediction. The engine never scores anything: it reads the newest stored
  operational-risk prediction for the pan and copies what the model reported. A
  pan with no such prediction still gets a recommendation, and the recommendation
  says in the required words that the model was not consulted.
* *What may physically happen to salinity under rainfall?* — the mass balance's
  answer, computed here from the twin's own recorded state by Module 06's
  arithmetic, and stored as a :class:`~app.models.simulation_run.SimulationRun` so
  the numbers can be replayed.
* *What operational action is recommended according to defined rules?* — the
  written rule's answer, and the only one that produces an action.

**The engine's own opinion is nil.** It resolves variables, hands them to the
rule, stores what came back and refuses what it cannot support. It holds no
thresholds, no default action and no ordering of its own, because a threshold
written into this file could not be revised without invalidating every
recommendation already issued.

**A refusal is information, not a failure to retry.** A pan with no twin state
cannot be advised on, because the twin is where the physics starts. A twin with no
depth or no salinity cannot be simulated, because Module 06 refuses to divide by
nothing, and a recommendation whose required "simulation result" field would have
to be invented is exactly the failure this module exists to prevent. A rule that
cannot be evaluated on the readings available withholds its fallback rather than
recommending an action to a pan it never read.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.db.collections import (
    DIGITAL_TWIN_STATES,
    EXPLANATIONS,
    ML_PREDICTIONS,
    RECOMMENDATIONS,
    SALT_PANS,
    SIMULATION_RUNS,
)
from app.db.repository import MongoRepository
from app.models.base import normalize_pan_id, utcnow
from app.models.decision_rule import (
    DecisionEvaluation,
    DecisionInputReading,
    DecisionRule,
)
from app.models.digital_twin_state import DigitalTwinState
from app.models.enums import (
    DecisionAction,
    DecisionBasis,
    DecisionVariableSource,
    MlTarget,
    SimulationStatus,
)
from app.models.explanation import Explanation
from app.models.ml_prediction import MLPrediction
from app.models.recommendation import (
    DecisionPredictionSummary,
    DecisionSimulationSummary,
    DecisionTwinSummary,
    Recommendation,
)
from app.models.salt_pan import SaltPan
from app.models.simulation_run import SimulationRun
from app.services.decision_evaluation import evaluate_rule
from app.services.decision_rules import DecisionRuleService
from app.services.mass_balance import (
    MASS_BALANCE_MODEL_NAME,
    SIMULATOR_DISCLAIMER,
    MassBalanceService,
    RainfallDilutionError,
)

#: The operator's configurable limits, as catalogue variable name -> the attribute
#: of :class:`~app.models.salt_pan.RiskThresholds` holding it, with its unit.
#: Declared as data so adding a threshold to the registration model and to the
#: catalogue is the only edit needed to make a rule able to name it.
RISK_THRESHOLD_READINGS: dict[str, tuple[str, str]] = {
    "flood_depth_cm": ("flood_depth_cm", "cm"),
    "dilution_salinity_g_l": ("dilution_salinity_g_l", "g/L"),
    "critical_rainfall_24h_mm": ("critical_rainfall_24h_mm", "mm"),
    "target_flow_cm": ("target_flow_cm", "cm"),
}

#: The exact statement required on a recommendation that did not read a model
#: estimate. Verbatim, capitalisation and em dash included, because this string is
#: the project's agreed wording and a paraphrase would be a different claim: a
#: client rendering "ML unavailable" is asserting something weaker than the system
#: actually knows, which is that the model *was* available and was not consulted, or
#: was consulted and reported nothing usable.
ML_UNAVAILABLE_LABEL = "Physics/rule-based recommendation — ML prediction unavailable."

#: The statement paired with the required label, naming why the model was not read.
#: Kept as a second field rather than folded into the label so the required wording
#: stays exactly as specified and the machine-specific reason is still reported.
#: Why no model estimate was read, in each case this can happen. Each value is a
#: complete sentence that stands on its own: the reason is shown after the required
#: label as a following sentence, and is also used alone as a field value, so it
#: cannot be written as a fragment that only reads when preceded by the label.
ML_UNAVAILABLE_REASONS: dict[str, str] = {
    "no_prediction_stored": (
        "No operational-risk prediction is stored for this pan, so no model estimate "
        "was read. The recommendation rests on the recorded twin state, the mass "
        "balance and the written rule alone."
    ),
    "class_only_model": (
        "The stored prediction is from a class-only model, so it reported a risk "
        "class with no probability. The class is reported as the model stated it; no "
        "likelihood is shown because none was produced."
    ),
    "rule_does_not_read_ml": (
        "The rule applied here declares no prediction variable, so the stored model "
        "estimate was not read. A rule that does not consult the model must not have "
        "the model's answer attached to it as though it had."
    ),
}

#: The risk target the engine reads. Module 10 owns the definition of the classes;
#: a salinity model is a different question and is not consulted here.
OPERATIONAL_RISK_TARGET = MlTarget.OPERATIONAL_RISK

#: ``MASS_BALANCE_MODEL_NAME`` is imported from the physics service rather than
#: declared here. These summaries quote the simulator's model, and a second
#: literal for that string would be a second thing to forget to change when the
#: model is renamed.


class DecisionRefusal(RuntimeError):
    """A recommendation that cannot be issued, with a machine-readable reason.

    Carries a code rather than only prose so a client can decide what to do without
    reading the message, in the same way Module 10's prediction refusals do. The
    codes are the module's contract:

    ``no_twin_state``
        The pan has no recorded state. The physics starts from the twin, so there
        is nothing to reason about.
    ``simulation_inputs_missing``
        The twin holds no depth or no salinity, so the mass balance cannot run.
        A recommendation whose "simulation result" would have to be invented is
        refused rather than issued.
    ``no_rule_defined``
        No decision rule has been written, so there is no defined rule to apply.
        The engine has no thresholds of its own to fall back on, by design.
    ``insufficient_evidence``
        The evidence the rule needs was not there. Either the newest reading is
        older than the operator's ``max_observation_age_hours``, or an input the
        rule reads — commonly an operator limit left unconfigured — could not be
        read at all, so no criterion that needed it could be compared. Nothing is
        substituted for the missing value.
    ``rule_undecided``
        The rule was fully evaluated and matched nothing it cared about, with no
        declared fallback. It declines to name an action, and the recommendation
        would be the engine's judgement rather than the rule's.
    """

    def __init__(
        self,
        detail: str,
        *,
        code: str,
        rule_id: str | None = None,
        pan_id: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.rule_id = rule_id
        self.pan_id = pan_id

    def as_body(self) -> dict[str, Any]:
        """The refusal as an HTTP body: a code a client branches on, and prose."""
        body: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.rule_id is not None:
            body["rule_id"] = self.rule_id
        if self.pan_id is not None:
            body["pan_id"] = self.pan_id
        return body


class DecisionEngineService:
    """Issue, read and list operational recommendations.

    Constructed with a database and nothing else. The mass-balance service is
    injected for tests but holds no state, so the default is the real one: the
    arithmetic is a pure function of its inputs and a substitutable one would only
    be a way to be wrong quietly.
    """

    def __init__(
        self,
        database: Any,
        *,
        rules: DecisionRuleService | None = None,
        mass_balance: MassBalanceService | None = None,
    ) -> None:
        self._database = database
        self._rules = rules or DecisionRuleService(database)
        self._mass_balance = mass_balance or MassBalanceService()

    @staticmethod
    def _normalized(pan_id: str) -> str:
        """The stored form of a pan id.

        Pan ids are matched case-insensitively everywhere in this project and
        stored lowercased. The engine normalizes on the way in rather than
        relying on the caller, because a lookup that silently missed on case would
        report "no twin state" for a pan that plainly has one — a refusal whose
        stated reason is false.
        """
        return normalize_pan_id(pan_id)

    def _repository(self) -> MongoRepository[Recommendation]:
        return MongoRepository(self._database, RECOMMENDATIONS, Recommendation)

    def _simulation_repository(self) -> MongoRepository[SimulationRun]:
        return MongoRepository(self._database, SIMULATION_RUNS, SimulationRun)

    def _prediction_repository(self) -> MongoRepository[MLPrediction]:
        return MongoRepository(self._database, ML_PREDICTIONS, MLPrediction)

    def _pan_repository(self) -> MongoRepository[SaltPan]:
        return MongoRepository(self._database, SALT_PANS, SaltPan)

    def _explanation_repository(self) -> MongoRepository[Explanation]:
        return MongoRepository(self._database, EXPLANATIONS, Explanation)

    async def _explanation_for(self, prediction: MLPrediction) -> str | None:
        """The stored explanation of this prediction, or None if it has none.

        The link is by id and nothing else is invented: a SHAP attribution Module 11
        already wrote for this exact prediction is cited, and the absence of one is
        recorded as absence. This never computes an attribution and never cites an
        explanation belonging to a different prediction or model version -- an
        explanation that does not describe the estimate the rule read would be a
        claim about the evidence that is simply untrue, and a more useful record
        than a plausible wrong one.
        """
        stored = await self._explanation_repository().find(
            {
                "prediction_id": prediction.prediction_id,
                "model_id": prediction.model_id,
                "model_version": prediction.model_version,
            },
            limit=1,
        )
        return stored[0].explanation_id if stored else None

    # -- the pieces, gathered ----------------------------------------------

    async def _twin_for(
        self, pan_id: str, as_of: datetime | None
    ) -> DigitalTwinState:
        """The recorded state the recommendation is made from.

        With no ``as_of`` this is the pan's current twin, which is the ordinary
        operational question. With one, the newest state recorded at or before it,
        so a recommendation anchored at a past instant is made from what the system
        believed then rather than from today's view of a past moment.
        """
        repository = MongoRepository(self._database, DIGITAL_TWIN_STATES, DigitalTwinState)
        if as_of is not None:
            history = await repository.find(
                {"pan_id": pan_id}, sort=[("as_of", 1)], limit=1000
            )
            eligible = [state for state in history if state.as_of <= as_of]
            if not eligible:
                oldest = min((state.as_of for state in history), default=None)
                raise DecisionRefusal(
                    f"Pan {pan_id!r} has no digital twin state recorded at or before "
                    f"{as_of.isoformat()}. The oldest state on record is "
                    f"{oldest.isoformat() if oldest else 'none'}. A recommendation "
                    "anchored earlier than the system's first belief about the pan "
                    "would be reasoned from nothing.",
                    code="no_twin_state",
                    pan_id=pan_id,
                )
            return max(eligible, key=lambda state: state.as_of)

        latest = await repository.find({"pan_id": pan_id}, sort=[("as_of", -1)], limit=1)
        if not latest:
            raise DecisionRefusal(
                f"Pan {pan_id!r} has no digital twin state. The physics this engine "
                "combines starts from the twin, so there is no recorded state to "
                "reason from. Record one through Module 05 "
                "(POST /api/twin/{pan_id}/refresh) and the endpoint will answer. No "
                "state is assembled from raw records in its place, because a "
                "recommendation made from a substitute state is a recommendation "
                "about a pan that does not exist.",
                code="no_twin_state",
                pan_id=pan_id,
            )
        return latest[0]

    async def _pan_for(self, pan_id: str) -> SaltPan:
        found = await self._pan_repository().find({"pan_id": pan_id}, limit=1)
        if not found:
            raise DecisionRefusal(
                f"Pan {pan_id!r} is not a registered pan, so it has no area or target "
                "harvest salinity for the mass balance and no thresholds for a rule to "
                "compare against.",
                code="no_twin_state",
                pan_id=pan_id,
            )
        return found[0]

    async def _simulate(
        self,
        *,
        pan: SaltPan,
        twin: DigitalTwinState,
        rainfall_mm: float | None,
    ) -> DecisionSimulationSummary:
        """Run the mass balance over the twin's own state, and store the run.

        The rainfall is the twin's stored forecast unless the caller supplied a
        what-if depth, and either way the source is recorded on the summary: a
        forecast that happened to be in force and a figure a person typed into a
        request are different claims, and a recommendation that did not say which
        would be quoting one as the other.

        The four inputs are read from records, not assumed. A twin with no depth or
        no salinity is refused, because every number in the balance is a product or
        a quotient of them and a substituted figure would be a fabricated one.
        """
        missing: list[str] = []
        if twin.estimated_salinity_g_l is None:
            missing.append(
                f"salinity_g_l (the twin's newest observation supplied no salinity: "
                f"{_gap_reason(twin, 'estimated_salinity_g_l')})"
            )
        if twin.water_depth_cm is None:
            missing.append(
                f"water_depth_cm (the twin's newest observation supplied no depth: "
                f"{_gap_reason(twin, 'water_depth_cm')})"
            )
        if missing:
            raise DecisionRefusal(
                f"The mass balance cannot be run for pan {pan.pan_id!r}: "
                + "; ".join(missing)
                + ". A recommendation must carry a simulation result, and this one "
                "would have to be invented. Record a salinity and a depth through "
                "Module 02 and ask again.",
                code="simulation_inputs_missing",
                pan_id=pan.pan_id,
            )

        if rainfall_mm is None:
            depth = twin.forecast_rainfall_mm
            if depth is None:
                raise DecisionRefusal(
                    f"Pan {pan.pan_id!r} has no forecast rainfall on record, so there "
                    "is no rainfall depth to resolve, and the request supplied none. "
                    "Supply rainfall_mm for an explicit what-if, or record a weather "
                    "forecast so the simulation uses the depth that is actually "
                    "forecast.",
                    code="simulation_inputs_missing",
                    pan_id=pan.pan_id,
                )
            source = "twin.forecast_rainfall_mm"
        else:
            depth = rainfall_mm
            source = "request.rainfall_mm"

        # The guard above established both are present; the local names keep the
        # narrowing visible to a type checker without re-testing at runtime.
        starting_salinity = float(twin.estimated_salinity_g_l)
        starting_depth_cm = float(twin.water_depth_cm)

        try:
            dilution = self._mass_balance.evaluate(
                area_m2=pan.area_m2,
                initial_depth_m=starting_depth_cm / 100.0,
                initial_salinity_g_l=starting_salinity,
                rainfall_mm=depth,
            )
        except RainfallDilutionError as exc:
            # The guards above make this unreachable for a twin holding positive
            # readings, but the balance is a public function and the engine refuses
            # rather than assuming: a stored recommendation must not exist for a
            # simulation that raised.
            raise DecisionRefusal(
                f"The mass balance refused the inputs for pan {pan.pan_id!r}: {exc}",
                code="simulation_inputs_missing",
                pan_id=pan.pan_id,
            ) from exc

        moment = utcnow()
        run = SimulationRun(
            pan_id=pan.pan_id,
            status=SimulationStatus.SUCCEEDED,
            started_at=moment,
            finished_at=moment,
            inputs={
                "area_m2": dilution.area_m2,
                "initial_depth_m": dilution.initial_depth_m,
                "initial_depth_cm": starting_depth_cm,
                "initial_salinity_g_l": dilution.initial_salinity_g_l,
                "rainfall_mm": dilution.rainfall_mm,
                "rainfall_from": source,
                "twin_id": twin.twin_id,
                "twin_as_of": twin.as_of,
                "module": "12_decision_engine",
            },
            outputs={
                "post_rain_salinity_g_l": dilution.post_rain_salinity_g_l,
                "post_rain_depth_m": dilution.post_rain_depth_m,
                "post_rain_depth_cm": dilution.post_rain_depth_m * 100.0,
                "salinity_drop_g_l": dilution.salinity_drop_g_l,
                "dilution_percent": dilution.dilution_percent,
                "post_rain_volume_m3": dilution.post_rain_volume_m3,
                "salt_mass_conserved": dilution.salt_mass_conserved,
                "model": MASS_BALANCE_MODEL_NAME,
                "disclaimer": SIMULATOR_DISCLAIMER,
            },
        )
        stored = await self._simulation_repository().insert(run)

        summary = DecisionSimulationSummary(
            run_id=stored.run_id,
            model_name=MASS_BALANCE_MODEL_NAME,
            rainfall_mm=dilution.rainfall_mm,
            rainfall_source=source,
            initial_salinity_g_l=dilution.initial_salinity_g_l,
            initial_depth_cm=starting_depth_cm,
            post_rain_salinity_g_l=dilution.post_rain_salinity_g_l,
            post_rain_depth_cm=dilution.post_rain_depth_m * 100.0,
            salinity_drop_g_l=dilution.salinity_drop_g_l,
            dilution_percent=dilution.dilution_percent,
            dilution_percent_reason=dilution.dilution_percent_reason,
        )
        return summary

    async def _prediction_for(self, pan_id: str) -> MLPrediction | None:
        """The newest stored operational-risk prediction for the pan, or None.

        Read rather than requested, and that is the load-bearing decision of this
        module. Asking Module 10 to predict here would make a recommendation
        endpoint into a second prediction path, and the two would then be free to
        disagree about what the model would have said — with an operator acting on
        one of them.

        A prediction from an older twin state is still returned, because a slightly
        stale estimate is evidence and hiding it would be worse. The summary records
        whether its snapshot matches the twin this recommendation used, so the
        reader can see that it does not.
        """
        found = await self._prediction_repository().find(
            {"pan_id": pan_id, "target": OPERATIONAL_RISK_TARGET.value},
            sort=[("timestamp", -1), ("created_at", -1)],
            limit=1,
        )
        return found[0] if found else None

    # -- the readings the rule consumes -------------------------------------

    @staticmethod
    def _readings(
        *,
        rule: DecisionRule,
        twin: DigitalTwinState,
        pan: SaltPan,
        simulation: DecisionSimulationSummary,
        prediction: MLPrediction | None,
    ) -> dict[str, DecisionInputReading]:
        """Resolve every variable the rule reads, from real recorded values.

        Each of the three sources is read only for the variables the rule
        actually names, so a rule that reads no prediction variable never causes a
        prediction to be consulted, and one that reads no simulation variable does
        not have a mass-balance figure presented to it as an input.
        """
        readings: dict[str, DecisionInputReading] = {}
        for name in rule.variables_used:
            if name == "current_salinity_g_l":
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=twin.estimated_salinity_g_l,
                    unit="g/L",
                    source=DecisionVariableSource.TWIN,
                    reason=None
                    if twin.estimated_salinity_g_l is not None
                    else "the twin holds no salinity",
                )
            elif name == "current_depth_cm":
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=twin.water_depth_cm,
                    unit="cm",
                    source=DecisionVariableSource.TWIN,
                    reason=None
                    if twin.water_depth_cm is not None
                    else "the twin holds no water depth",
                )
            elif name == "forecast_rainfall_mm":
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=twin.forecast_rainfall_mm,
                    unit="mm",
                    source=DecisionVariableSource.TWIN,
                    reason=None
                    if twin.forecast_rainfall_mm is not None
                    else "no weather forecast was in force, so the twin holds none",
                )
            elif name == "rainfall_probability_percent":
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=twin.rainfall_probability_percent,
                    unit="%",
                    source=DecisionVariableSource.TWIN,
                    reason=None
                    if twin.rainfall_probability_percent is not None
                    else "no weather forecast supplied a probability",
                )
            elif name == "target_harvest_salinity_g_l":
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=pan.target_harvest_salinity_g_l,
                    unit="g/L",
                    source=DecisionVariableSource.PAN_REGISTRATION,
                )
            elif name in RISK_THRESHOLD_READINGS:
                # The operator's own limits, read from the registration record and
                # passed through unchanged. A limit nobody configured arrives as a
                # reading with no value and a reason that says who has to set it,
                # which is what makes the criterion that needs it unevaluable
                # rather than silently compared against zero.
                attribute, unit = RISK_THRESHOLD_READINGS[name]
                value = getattr(pan.risk_thresholds, attribute, None)
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=value,
                    unit=unit,
                    source=DecisionVariableSource.PAN_REGISTRATION,
                    reason=None
                    if value is not None
                    else (
                        f"no {name} is configured for this pan; set it on the pan's "
                        "risk thresholds. No value is assumed in its place, and a "
                        "criterion that needs one is not evaluated"
                    ),
                )
            elif name == "simulated_post_rain_salinity_g_l":
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=simulation.post_rain_salinity_g_l,
                    unit="g/L",
                    source=DecisionVariableSource.SIMULATION,
                )
            elif name == "simulated_salinity_drop_g_l":
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=simulation.salinity_drop_g_l,
                    unit="g/L",
                    source=DecisionVariableSource.SIMULATION,
                )
            elif name == "simulated_post_rain_depth_cm":
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=simulation.post_rain_depth_cm,
                    unit="cm",
                    source=DecisionVariableSource.SIMULATION,
                )
            elif name == "predicted_risk_class":
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=prediction.predicted_class if prediction is not None else None,
                    unit=None,
                    source=DecisionVariableSource.PREDICTION,
                    reason=None
                    if prediction is not None and prediction.predicted_class
                    else "no stored operational-risk prediction supplied a class",
                )
            elif name == "predicted_risk_probability":
                reason = None
                if prediction is None:
                    reason = "no stored operational-risk prediction supplied a probability"
                elif prediction.probability is None:
                    reason = (
                        "the stored prediction reports a class with no probability, "
                        "so there is no likelihood to compare"
                    )
                readings[name] = DecisionInputReading(
                    variable=name,
                    value=prediction.probability if prediction is not None else None,
                    unit=None,
                    source=DecisionVariableSource.PREDICTION,
                    reason=reason,
                )
        return readings

    # -- the recommendation -------------------------------------------------

    async def advise(
        self,
        pan_id: str,
        *,
        as_of: datetime | None = None,
        rainfall_mm: float | None = None,
        rule_id: str | None = None,
    ) -> Recommendation:
        """Issue one recommendation for a pan, and store it.

        The sequence is deliberate and is the module's whole argument: gather the
        recorded state, resolve the physics against it, read whatever the model has
        already said, hand all of it to the written rule, then store the action
        together with every input that produced it.
        """
        pan_id = self._normalized(pan_id)
        rule = await self._resolve_rule(rule_id)
        pan = await self._pan_for(pan_id)
        twin = await self._twin_for(pan_id, as_of)
        # Checked before any physics is resolved. A rule evaluated against a stale
        # reading is a rule evaluated correctly about the wrong hour, and the work
        # of computing it would only make the refusal harder to spot.
        self._check_evidence_is_current(pan=pan, twin=twin, as_of=as_of)
        simulation = await self._simulate(pan=pan, twin=twin, rainfall_mm=rainfall_mm)
        prediction = await self._prediction_for(pan_id)

        readings = self._readings(
            rule=rule,
            twin=twin,
            pan=pan,
            simulation=simulation,
            prediction=prediction,
        )
        evaluation: DecisionEvaluation = evaluate_rule(rule, readings)
        if evaluation.action is None:
            self._refuse_undecided(
                evaluation=evaluation, rule=rule, pan_id=pan_id
            )

        ml_summary, basis, notice = self._evidence_block(
            evaluation=evaluation,
            rule=rule,
            prediction=prediction,
            twin=twin,
        )
        # Cited only when the rule actually read a prediction, and only for the
        # exact prediction and model version it read.
        explanation_id = (
            await self._explanation_for(prediction)
            if ml_summary is not None and prediction is not None
            else None
        )

        # A recommendation assembled from a demonstration reading is a
        # demonstration, and the label travels on the stored record so that every
        # later reader of it -- the dashboard, the log, an export -- inherits the
        # same statement rather than each deciding for itself whether the reading
        # behind it counted as real.
        demonstration_sources = _demonstration_sources(twin)
        notice = _provenance_notice(
            notice=notice,
            pan=pan,
            twin=twin,
            demonstration_sources=demonstration_sources,
        )

        action = evaluation.action
        recommendation = Recommendation(
            pan_id=pan_id,
            action=action,
            reason=_reason_text(evaluation),
            basis=basis,
            notice=notice,
            twin=DecisionTwinSummary(
                twin_id=twin.twin_id,
                as_of=twin.as_of,
                provenance=twin.provenance.value,
                salinity_g_l=twin.estimated_salinity_g_l,
                water_depth_cm=twin.water_depth_cm,
                forecast_rainfall_mm=twin.forecast_rainfall_mm,
                observation_id=twin.observation_id,
                observation_timestamp=twin.observation_timestamp,
                weather_forecast_id=twin.weather_forecast_id,
                weather_forecast_timestamp=twin.weather_forecast_timestamp,
                missing_fields=list(twin.missing_fields),
                is_demonstration=bool(demonstration_sources),
                demonstration_sources=demonstration_sources,
            ),
            simulation=simulation,
            ml=ml_summary,
            explanation_id=explanation_id,
            rule_id=rule.rule_id,
            rule_name=rule.name,
            rule_version=rule.version,
            specification_hash=rule.specification_hash,
            evaluation=evaluation,
            matched_criteria_text=evaluation.matched_criteria_text(),
            input_readings_text=evaluation.input_readings_text(),
            priority=rule.priority_of(action),
            title=action.value,
            body=_body_text(
                action=action,
                evaluation=evaluation,
                simulation=simulation,
                ml=ml_summary,
            ),
            rationale=rule.operational_rationale,
        )
        return await self._repository().insert(recommendation)

    @staticmethod
    def _refuse_undecided(
        *,
        evaluation: DecisionEvaluation,
        rule: DecisionRule,
        pan_id: str,
    ) -> None:
        """Refuse an evaluation that named no action, saying which kind it was.

        Two different things end with no action, and a client needs to tell them
        apart. One is a rule that was fully evaluated and matched nothing it cared
        about, with no fallback declared: the engine declines to invent an action,
        which is a statement about the rule. The other is a rule that could not be
        evaluated at all, because an input it needs — very often an operator limit
        left unconfigured — was never read. That is a statement about the evidence,
        and it is reported as insufficient evidence so nobody reads the first kind
        as though more were missing than was.

        The distinction is drawn from the recorded readings rather than from the
        reason string, so it cannot drift out of step with what was actually read.
        """
        missing = sorted(
            entry.variable
            for entry in evaluation.input_readings
            if not entry.is_available
        )
        if missing:
            named = ", ".join(missing)
            raise DecisionRefusal(
                f"The rule {rule.name!r} version {rule.version} cannot be applied to pan "
                f"{pan_id!r} because the evidence it needs is not there: {named} could "
                "not be read. Comparing against an absent value would mean choosing one "
                "on the author's behalf, so no action is named. "
                f"{evaluation.undecided_reason or ''}".strip(),
                code="insufficient_evidence",
                rule_id=rule.rule_id,
                pan_id=pan_id,
            )
        raise DecisionRefusal(
            f"The rule {rule.name!r} version {rule.version} could not recommend an "
            f"action for pan {pan_id!r}. {evaluation.undecided_reason} A "
            "recommendation naming an action no criterion and no declared fallback "
            "supports would be this module's judgement rather than the rule's.",
            code="rule_undecided",
            rule_id=rule.rule_id,
            pan_id=pan_id,
        )

    @staticmethod
    def _check_evidence_is_current(
        *,
        pan: SaltPan,
        twin: DigitalTwinState,
        as_of: datetime | None,
    ) -> None:
        """Refuse to recommend from a reading the operator has declared too old.

        The limit is the pan's own ``max_observation_age_hours``. **A pan with no
        limit configured is never refused here**, and that is deliberate: a
        freshness window is a judgement about how fast a salt pan changes, this
        project has no basis to make that judgement, and inventing one would put a
        number in front of an operator that reads as though it meant something. The
        absence is instead visible -- :meth:`_evidence_block` states on the
        recommendation that no freshness limit is configured, so the reader knows
        the absence of a refusal is not the same as a freshness check having
        passed.

        The clock runs from ``as_of`` when the caller anchored the request there,
        and from now otherwise. Measuring against the request's own instant is the
        only way a historical replay can be checked: the same rule re-run for the
        same past hour has to pass or fail for the same reasons it did the first
        time, and a limit measured against today would fail every replay of an old
        pan for the wrong reason.
        """
        limit_hours = pan.risk_thresholds.max_observation_age_hours
        if limit_hours is None:
            return
        observed_at = twin.observation_timestamp
        if observed_at is None:
            # A twin with no reading behind it has nothing to be old. The
            # simulation branch below is the one that decides whether that is
            # allowed to reach a recommendation.
            return
        reference = as_of or utcnow()
        age_hours = (reference - observed_at).total_seconds() / 3600.0
        if age_hours <= limit_hours:
            return
        raise DecisionRefusal(
            f"Pan {pan.pan_id!r} has not been read for "
            f"{age_hours:.1f} h, which is beyond the {limit_hours:g} h this operator "
            f"configured as the oldest reading that may support a recommendation "
            f"(newest reading: {observed_at.isoformat()}). A rule applied to water "
            "this old would decide correctly about the wrong hour, so this is "
            "reported as insufficient evidence rather than answered. Record a fresh "
            "reading, or raise the limit on the pan if the current age genuinely "
            "reflects how this pan is monitored.",
            code="insufficient_evidence",
            pan_id=pan.pan_id,
        )

    async def _resolve_rule(self, rule_id: str | None) -> DecisionRule:
        """The rule to apply: the one named, or the most recently written one.

        With no ``rule_id`` the engine takes the most recently written rule of any
        name -- ordered by ``created_at``, newest first -- which is the one an
        operator has most recently reasoned about. It does not carry a "default"
        rule of its own, because a default nobody chose is a rule nobody can
        revise.

        Where several rule families exist, this is a *choice among rules*, not a
        fallback, and it is left visible rather than hidden: the response carries
        ``rule_name``, ``rule_version`` and ``rule_specification_hash``, so a reader
        can see which rule answered and open exactly that version. A caller that
        wants no ambiguity names the rule it means with ``rule_id``, and that is the
        form Module 8's operator-facing surface should use when it presents a
        specific written rule to an operator.
        """
        if rule_id is not None:
            return await self._rules.get(rule_id)
        found = await self._rules.list_rules(limit=1)
        if not found:
            raise DecisionRefusal(
                "No decision rule has been written, so there is no defined rule to "
                "apply. This engine holds no thresholds and no default action of its "
                "own, by design: a rule that could not be revised without invalidating "
                "every recommendation already issued would not be a rule. Write one "
                "through POST /api/decisions/rules and the endpoint will answer.",
                code="no_rule_defined",
            )
        return found[0]

    @staticmethod
    def _evidence_block(
        *,
        evaluation: DecisionEvaluation,
        rule: DecisionRule,
        prediction: MLPrediction | None,
        twin: DigitalTwinState,
    ) -> tuple[DecisionPredictionSummary | None, DecisionBasis, str]:
        """The ML block, the basis it implies, and the notice that must accompany it.

        The three cases are separated because they are three different claims:

        * the rule reads no prediction variable, so the stored estimate was
          deliberately not consulted — the model may well have an answer, and
          attaching it would present a rule's reasoning as model-informed;
        * no prediction is stored, so there was nothing to read;
        * a prediction is stored and the rule reads it, and it either carries a
          probability or it is a class-only model that reported a label and no
          likelihood. The class is passed through as the model stated it, and the
          missing probability is named rather than filled.
        """
        reads_ml = any(
            entry.source is DecisionVariableSource.PREDICTION
            for entry in evaluation.input_readings
        )
        if not reads_ml:
            return None, DecisionBasis.PHYSICS_AND_RULES, (
                f"{ML_UNAVAILABLE_LABEL} {ML_UNAVAILABLE_REASONS['rule_does_not_read_ml']}"
            )
        if prediction is None:
            return None, DecisionBasis.PHYSICS_AND_RULES, (
                f"{ML_UNAVAILABLE_LABEL} {ML_UNAVAILABLE_REASONS['no_prediction_stored']}"
            )

        class_only = prediction.probability is None
        summary = DecisionPredictionSummary(
            prediction_id=prediction.prediction_id,
            model_id=prediction.model_id,
            model_version=prediction.model_version,
            target=MlTarget(prediction.target).value,
            predicted_class=prediction.predicted_class,
            probability=prediction.probability,
            confidence=prediction.confidence,
            probability_is_null_reason=(
                ML_UNAVAILABLE_REASONS["class_only_model"] if class_only else None
            ),
            predicted_at=prediction.created_at,
            twin_as_of=_snapshot_instant(prediction, "twin_as_of"),
            input_snapshot_id=prediction.input_snapshot_id,
            matches_recommendation_snapshot=snapshot_matches_twin(prediction, twin),
        )
        notice = (
            f"{ML_UNAVAILABLE_LABEL} {ML_UNAVAILABLE_REASONS['class_only_model']}"
            if class_only
            else (
                f"Physics and the deployed model {prediction.model_version} were both "
                f"read; the action follows the rule {rule.name!r} version "
                f"{rule.version}, which consumed both."
            )
        )
        # A class-only model still produced an estimate, so the recommendation is
        # model-informed and its basis says so. The required label is about the
        # absent *probability*, and is attached to that fact rather than to the
        # whole recommendation being physics-only.
        return summary, DecisionBasis.PHYSICS_AND_ML, notice

    # -- reads --------------------------------------------------------------

    async def latest(self, pan_id: str) -> Recommendation | None:
        """The most recent recommendation issued for a pan, or None.

        None rather than 404-shaped refusal: "no recommendation has been issued for
        this pan" is a normal state for a pan nobody has asked about yet, and the
        caller needs to be able to tell it from a failure.
        """
        found = await self._repository().find(
            {"pan_id": self._normalized(pan_id)},
            sort=[("issued_at", -1), ("created_at", -1)],
            limit=1,
        )
        return found[0] if found else None

    async def list_for_pan(
        self, pan_id: str, *, limit: int = 20, skip: int = 0
    ) -> list[Recommendation]:
        """A pan's recommendation history, newest first."""
        return await self._repository().find(
            {"pan_id": self._normalized(pan_id)},
            sort=[("issued_at", -1), ("created_at", -1)],
            skip=skip,
            limit=limit,
        )

    async def log(
        self,
        *,
        pan_id: str | None = None,
        action: str | None = None,
        limit: int = 50,
        skip: int = 0,
    ) -> list[Recommendation]:
        """The advice log across pans, newest first, optionally narrowed.

        Ordered by issue time rather than creation time because a recommendation's
        operational moment is when it was issued; the two differ when a request is
        replayed, and the log is read as a sequence of advice an operator received.
        """
        query: dict[str, Any] = {}
        if pan_id is not None:
            query["pan_id"] = self._normalized(pan_id)
        if action is not None:
            query["action"] = action
        return await self._repository().find(
            query, sort=[("issued_at", -1), ("created_at", -1)], skip=skip, limit=limit
        )

    async def get(self, recommendation_id: str) -> Recommendation:
        """One recommendation by id, or 404."""
        return await self._repository().get("recommendation_id", recommendation_id)

    async def count(self) -> int:
        return await self._repository().count()


# -- text builders ----------------------------------------------------------


def _provenance_notice(
    *,
    notice: str,
    pan: SaltPan,
    twin: DigitalTwinState,
    demonstration_sources: Sequence[str],
) -> str:
    """Add the statements a reader needs before acting on this recommendation.

    Three things are appended, and each is an absence or a caveat that the
    recommendation's own figures do not carry:

    * whether the reading behind it was a demonstration, which has to be said on
      the recommendation and not left to a client that might not look;
    * the age of the newest reading, because a decision carries no visible sign of
      how old the water it was made about is;
    * that no freshness limit is configured at all, when that is the case. This is
      the one worth stating explicitly: the engine did not check the reading's age
      because nobody told it what age to refuse, and silence on the subject would
      otherwise read as a check that passed.
    """
    parts = [notice]
    if demonstration_sources:
        named = ", ".join(demonstration_sources)
        parts.append(
            f"DEMONSTRATION ONLY: built from demonstration data ({named}). This is a "
            "worked example, not a reading of this pan, and must not be acted on."
        )
    limit_hours = pan.risk_thresholds.max_observation_age_hours
    observed_at = twin.observation_timestamp
    if observed_at is None:
        parts.append(
            "No reading timestamp is available for this recommendation, so its "
            "evidence could not be age-checked."
        )
    elif limit_hours is None:
        age_hours = (utcnow() - observed_at).total_seconds() / 3600.0
        parts.append(
            f"No freshness limit is configured for this pan, so this reading's age "
            f"({age_hours:.1f} h) was not checked against one. Set "
            "max_observation_age_hours on the pan to have stale evidence refused."
        )
    else:
        age_hours = (utcnow() - observed_at).total_seconds() / 3600.0
        parts.append(
            f"Newest reading is {age_hours:.1f} h old, within the {limit_hours:g} h "
            "this operator configured as the limit."
        )
    return " ".join(parts)


def _demonstration_sources(twin: DigitalTwinState) -> list[str]:
    """Which of the twin's own inputs were entered to demonstrate the system.

    Read from the recorded provenance of each contributing record rather than from
    whether the values look plausible, because a demonstration reading is
    indistinguishable from a real one by inspection -- that is the point of it
    being a well-formed reading. Naming each one also means a recommendation
    assembled from one real and one demonstration record says which was which,
    instead of being labelled only at the level of the whole.
    """
    sources: list[str] = []
    for field, source in (
        ("observation_data_source", twin.observation_data_source),
        ("weather_source", twin.weather_source),
        ("weather_forecast_source", twin.weather_forecast_source),
    ):
        value = getattr(source, "value", source)
        if value == "demonstration" and field not in sources:
            sources.append(field)
    return sources


def _snapshot_instant(prediction: MLPrediction, field: str) -> Any:
    """One instant recorded inside a prediction's own input snapshot.

    Read from the snapshot rather than from a sibling field because the snapshot is
    what the model was actually given; a value read from anywhere else would be
    this module's reconstruction of the model's input rather than the input itself.
    Anything absent or unparseable comes back as None, which is reported rather
    than filled.
    """
    raw = prediction.input_snapshot.get(field)
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def snapshot_matches_twin(prediction: MLPrediction, twin: DigitalTwinState) -> bool:
    """Whether a stored prediction was made from exactly this twin state.

    Module 10 records the twin it scored in the prediction's own snapshot, so the
    comparison is exact rather than inferred from timestamps: a twin id match means
    the model saw these readings, and anything else means it did not.

    An old prediction that does not match is still evidence, and the engine keeps
    it. What it must not do is present it as though it described the current state,
    which is why this boolean is on the summary.
    """
    return str(prediction.input_snapshot.get("twin_id", "")) == twin.twin_id


def _reason_text(evaluation: DecisionEvaluation) -> str:
    """The stored reason, written from the criteria that fired.

    Derived rather than composed, so the reason on a stored recommendation is always
    a restatement of the rule that produced it. A reason the module wrote in its own
    words would be a second, unauditable statement of the same decision.
    """
    if not evaluation.matched_criteria:
        return (
            f"No criterion of rule {evaluation.rule_name!r} version "
            f"{evaluation.rule_version} matched, and the rule's declared fallback "
            f"action was assigned."
        )
    fired = "; ".join(
        f"{match.comparator} (read {match.value} from {match.source.value})"
        for match in evaluation.matched_criteria
    )
    combinator = "the most severe match decided" if len(evaluation.matched_criteria) > 1 else "the match decided"
    return (
        f"Rule {evaluation.rule_name!r} version {evaluation.rule_version}: {fired} "
        f"— {combinator}."
    )


def _body_text(
    *,
    action: DecisionAction,
    evaluation: DecisionEvaluation,
    simulation: DecisionSimulationSummary,
    ml: DecisionPredictionSummary | None,
) -> str:
    """The human-readable summary, for an operator reading the record as text.

    Built from the same figures the structured blocks carry, so the prose and the
    fields cannot disagree; it is a rendering, not a second opinion.
    """
    parts = [
        f"Recommended action: {action.value}.",
        (
            f"Physics: {simulation.rainfall_mm:g} mm of rain over a pan starting at "
            f"{simulation.initial_salinity_g_l:g} g/L and "
            f"{simulation.initial_depth_cm:g} cm leaves "
            f"{simulation.post_rain_salinity_g_l:g} g/L at "
            f"{simulation.post_rain_depth_cm:g} cm depth "
            f"({simulation.rainfall_source})."
        ),
    ]
    if ml is not None:
        if ml.probability is not None:
            parts.append(
                f"Model estimate: {ml.model_version} returned "
                f"{ml.predicted_class!r} at probability {ml.probability:g}."
            )
        else:
            parts.append(
                f"Model estimate: {ml.model_version} returned "
                f"{ml.predicted_class!r} with no probability."
            )
    else:
        parts.append("Model estimate: none was read for this recommendation.")
    parts.append(f"Inputs: {evaluation.input_readings_text() or 'none recorded'}.")
    return " ".join(parts)


def _gap_reason(twin: DigitalTwinState, field: str) -> str:
    """Why a twin quantity is absent, in the twin's own words where it has them."""
    for entry in twin.missing_inputs:
        if entry.field == field:
            return entry.reason
    return f"no record supplied {field}"


__all__ = [
    "MASS_BALANCE_MODEL_NAME",
    "ML_UNAVAILABLE_LABEL",
    "ML_UNAVAILABLE_REASONS",
    "OPERATIONAL_RISK_TARGET",
    "DecisionEngineService",
    "DecisionRefusal",
]
