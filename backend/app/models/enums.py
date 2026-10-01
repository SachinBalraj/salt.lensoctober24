from enum import Enum
from typing import Any


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value

    @classmethod
    def coerce(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError:
                return value
        return value


class ResearchDataSource(StrEnum):
    MANUAL = "manual"
    SENSOR = "sensor"
    IMPORT = "import"
    #: A value entered to show the system working, not a reading of this pan.
    #: It is a member of this enum rather than a separate flag so the exclusion
    #: that keeps it out of research data is a filter on one field, and so a
    #: reader of any single record can tell what it is without a second lookup.
    DEMONSTRATION = "demonstration"


class WeatherSource(StrEnum):
    """How a weather record reached SaltLens.

    The member is ``weather_api`` rather than ``api`` because "API" on its own
    is ambiguous once several providers can be connected, and the stored value
    appears verbatim in the operator UI and in exported research data.
    """

    MANUAL = "manual"
    WEATHER_API = "weather_api"
    SENSOR = "sensor"
    #: A forecast typed in to demonstrate the workflow. Kept distinct from
    #: ``weather_api`` on purpose: that member is the project's claim that a
    #: provider issued the forecast, and an operator must not be able to assert
    #: it by typing. ``manual`` says a person entered a forecast; this says the
    #: forecast is not one anybody issued at all.
    DEMONSTRATION = "demonstration"


class SimulationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TwinProvenance(StrEnum):
    OBSERVED = "observed"
    ASSIMILATED = "assimilated"
    SIMULATED = "simulated"


class MlTarget(StrEnum):
    SALINITY_G_L = "salinity_g_l"
    SALINITY = "salinity_g_l"
    WATER_DEPTH_CM = "water_depth_cm"
    HARVEST_YIELD = "harvest_yield"
    #: A class defined by a researcher's risk rule rather than a measured quantity.
    #: Present because an operational prediction's target is a rule's class, and
    #: recording it as a salinity figure would state a measurement that was never
    #: made. The value is the class name; the rule that defines it travels on the
    #: prediction record.
    OPERATIONAL_RISK = "operational_risk"


class ResearchTarget(StrEnum):
    """A quantity Module 07 can be asked to learn.

    These are deliberately more specific than :class:`MlTarget`. An ML target
    names a field; a research target names a field *and* the moment it is
    measured at, because "salinity" is not a prediction target until somebody
    says which salinity, taken when, relative to the state being predicted.
    """

    POST_RAIN_SALINITY_G_L = "post_rain_salinity_g_l"
    POST_RAIN_DEPTH_CM = "post_rain_depth_cm"
    SALINITY_CHANGE_G_L = "salinity_change_g_l"
    POST_RAIN_SALINITY_BAND = "post_rain_salinity_band"
    HARVEST_OUTCOME = "harvest_outcome"
    OPERATIONAL_RISK = "operational_risk"


class RiskCombinator(StrEnum):
    """How a definition turns several matched criteria into one label.

    A rule with more than one criterion has to say what happens when several
    match, because leaving it implicit is how a dataset ends up labelled two
    different ways by two runs of the same definition.
    """

    #: The most severe matching criterion decides. The usual choice for risk:
    #: a row that trips any high criterion is high.
    HIGHEST_RANK = "highest_rank"
    #: The first matching criterion in declared order decides, and the order is
    #: therefore part of the definition. Suited to a decision ladder where the
    #: earlier criteria are the ones an operator acts on first.
    FIRST_MATCH = "first_match"


class RiskCriterionOperator(StrEnum):
    """The comparison one risk criterion makes.

    A closed vocabulary rather than an expression language. A rule that could
    hold arbitrary code would be a rule nobody could check, and its version
    number would mean nothing.
    """

    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"
    EQ = "eq"
    NEQ = "neq"
    #: Inclusive on both ends: ``lower <= value <= upper``.
    BETWEEN = "between"
    #: Strictly outside the closed interval: ``value < lower or value > upper``.
    OUTSIDE = "outside"


class RiskLabelBasis(StrEnum):
    """Whether a label could have been assigned before the outcome was known.

    The distinction decides whether a model fitted to the label is usable at
    all, so it is stored on the definition and on every derived row rather than
    left for a reader to infer from which variables appear in the criteria.
    """

    #: Every criterion reads a variable that was measurable or forecastable at
    #: the row's anchor instant. A model can be fitted to this and deployed
    #: before the event.
    PROSPECTIVE = "prospective"
    #: At least one criterion reads a measurement taken after the anchor. The
    #: label describes what happened rather than what was knowable, so a model
    #: fitted to it cannot be used to anticipate the event it is graded against.
    RETROSPECTIVE = "retrospective"


class RiskVariableSource(StrEnum):
    """Where one measurable risk variable is read from.

    Recorded per variable so the causal argument is a property of the catalogue
    rather than a claim in prose: it is what lets the audit check a derived
    label's provenance row by row instead of trusting the definition.
    """

    #: A Module 07 row feature, which the builder already bounds by the anchor.
    ROW_FEATURE = "row_feature"
    #: A static property of the registered pan, e.g. its target harvest salinity.
    PAN_REGISTRATION = "pan_registration"
    #: A measurement recorded after the anchor, e.g. a recorded harvest outcome.
    RECORDED_OUTCOME = "recorded_outcome"


class RiskVariableDerivation(StrEnum):
    """How a risk variable's value is arrived at, once its source is known.

    A closed vocabulary rather than a formula, for the same reason the operators
    are: a rule that could hold arbitrary arithmetic would be a rule nobody could
    check, and its derived values could not be re-derived from a stored row. Every
    derivation here is one operation a reader can perform by hand from the same
    two figures the row already carries.
    """

    #: Read straight from the named Module 07 feature column.
    DIRECT = "direct"
    #: The first operand minus the second. Signed, so a rule against the result
    #: states its direction explicitly rather than relying on operand order being
    #: guessed right.
    DIFFERENCE = "difference"
    #: Read from a field on the pan's registration record.
    PAN_FIELD = "pan_field"
    #: Read from a recorded outcome at or after the horizon.
    RECORDED_OUTCOME = "recorded_outcome"


class TargetKind(StrEnum):
    REGRESSION = "regression"
    CLASSIFICATION = "classification"


class LeakageClass(StrEnum):
    """How a column relates in time to the instant a row is anchored at.

    Recorded per feature so the leakage argument is a property of the catalog
    rather than a claim in a docstring nobody reads.
    """

    #: Measured or forecast at or before the anchor instant: knowable then.
    AS_OF = "as_of"
    #: A registered pan property that no event changes.
    STATIC = "static"
    #: Measured after the anchor instant. Never a feature; always excluded.
    FUTURE = "future"

    @property
    def is_known_at_anchor(self) -> bool:
        """Whether a value of this class was available when the decision was made."""
        return self in (LeakageClass.AS_OF, LeakageClass.STATIC)


class FeatureKind(StrEnum):
    NUMERIC = "numeric"
    CATEGORICAL = "categorical"


class ModelStatus(StrEnum):
    """Where a trained model version stands in its life.

    The four values are the deployment lifecycle and nothing else. A model is
    fitted ``experimental``, is ``validated`` once an evaluation report exists and
    somebody has accepted that report, is ``deployed`` while it is the one
    allowed to answer operational requests, and is ``retired`` once it is not.

    There is no fifth value meaning "deployed, probably". Operational access is
    decided by ``deployed`` and by nothing else, which is why a candidate that
    scored well is still ``experimental``: a score is not a decision.
    """

    EXPERIMENTAL = "experimental"
    VALIDATED = "validated"
    DEPLOYED = "deployed"
    RETIRED = "retired"

    @property
    def is_terminal(self) -> bool:
        """Whether this is the last status a model version can hold.

        Retired is terminal on purpose. Re-deploying a retired version would
        reopen a question that was already answered, and doing it quietly is the
        failure this vocabulary exists to prevent; a model that deserves another
        chance is retrained and re-evaluated as a new version.
        """
        return self is ModelStatus.RETIRED


class ModelDeploymentAction(StrEnum):
    """One recorded step of a model's deployment history.

    Append-only, one document per step, so "the deployed model was replaced"
    is a fact with a timestamp and a reason behind it rather than an
    observation about which row happens to carry ``deployed``.
    """

    VALIDATED = "validated"
    DEPLOYED = "deployed"
    RETIRED = "retired"


class RecommendationPriority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DecisionAction(StrEnum):
    """The four operational actions this project is willing to recommend.

    A closed vocabulary, and the member values are the strings an operator reads.
    ``HARVEST_NOW`` is stored and served as ``HARVEST NOW``: the underscore
    carries meaning in Python and would be a typo-looking artefact in a briefing
    an operator acts on, so the gap between the two is deliberate and the values
    are the contract.

    Nothing here is a default. There is no ``DEFAULT`` member and no ranking
    implied by declaration order, because "what should the engine do if it does
    not know" is a question for a written rule rather than for an enum. A rule
    that needs a fallback says which action it falls back to, and the absence of
    a criterion is reported as an absence rather than absorbed by whichever
    member happens to be written last.
    """

    HARVEST_NOW = "HARVEST NOW"
    TRANSFER_BRINE = "TRANSFER BRINE"
    PROTECT_DRAIN = "PROTECT / DRAIN"
    WAIT_AND_RECHECK = "WAIT AND RECHECK"

    @property
    def is_inaction(self) -> bool:
        """Whether this action changes the pan, or only defers the question.

        Kept as data rather than a list in a rule, so a report can ask which
        actions are physically invasive without a second hard-coded table that
        could drift from the enum.
        """
        return self in (DecisionAction.HARVEST_NOW, DecisionAction.TRANSFER_BRINE,
                        DecisionAction.PROTECT_DRAIN)


class DecisionVariableSource(StrEnum):
    """Which part of the system a rule variable is read from.

    Recorded per variable so the separation the project insists on is a property
    of the catalogue rather than a claim in prose: twin state is what was
    recorded, ``simulation`` is what the mass balance computed, and ``prediction``
    is what a model estimated. A rule that mixes them is doing the thing this
    module exists to keep separable, and a reader can see that it does from the
    variable names alone.
    """

    #: A Module 05 twin quantity: a stored observation, a stored forecast, or a
    #: static registration property.
    TWIN = "twin"
    #: A Module 06 mass-balance output, computed from the twin's own state.
    SIMULATION = "simulation"
    #: A stored Module 10 operational-risk prediction, or its probability.
    PREDICTION = "prediction"
    #: A property of the registered pan, e.g. its target harvest salinity.
    PAN_REGISTRATION = "pan_registration"


class DecisionBasis(StrEnum):
    """What a recommendation was actually built from, stated rather than implied.

    The distinction the project refuses to blur is between a recommendation that
    had a model's estimate in hand and one that did not. Both are legitimate and
    both can be acted on, but they are not the same claim, and an operator shown
    one while believing the other has been handed a decision justified by
    evidence that was never consulted.
    """

    #: Physics and ML were both read; the rule consumed a simulated quantity and
    #: at least one model estimate.
    PHYSICS_AND_ML = "physics_and_ml"
    #: The model was not read — absent, not class-only, or explicitly declined.
    #: The exact label required of this case is
    #: :data:`~app.services.decisions.ML_UNAVAILABLE_LABEL`.
    PHYSICS_AND_RULES = "physics_and_rules"


class OperatorDecisionType(StrEnum):
    ACCEPTED = "accepted"
    MODIFIED = "modified"
    REJECTED = "rejected"


OperatorDecisionStatus = OperatorDecisionType
OperatorDecision = OperatorDecisionType


class HarvestOutcome(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"
    LOST = "lost"
