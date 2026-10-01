"""Module 12 - the quantities an operational decision rule may reference.

A decision rule is a set of comparisons, and a comparison is only meaningful
against a quantity that exists. This module is the answer to "which quantities,
and which part of the system does each come from", and it is deliberately a closed
list for the same reason Module 10's is: a rule that could name any variable would
have no basis on which it could be checked.

**The source is the point of the catalogue.** Every entry declares whether its
value is a recorded twin quantity, a mass-balance output, a model's estimate, or a
static registration property. That single field is what keeps this module's
central promise enforceable rather than aspirational: physics, ML and rules can be
told apart by reading the criteria of any rule, because the evidence each one
leans on is named on the variable it reads.

**No variable is derived from another by arithmetic here.** Unlike Module 10,
where ``expected_salinity_loss_g_l`` is a documented difference between two
catalogued figures, every entry below is a value some component of the system
already computes and stores. The differences a rule might want — the gap between
simulated salinity and the harvest target, say — are left to a criterion
comparison against both quantities rather than baked into a derived column, so the
arithmetic a rule performs stays visible in the rule itself.

**The two prediction variables are declared separately from each other on
purpose.** A model's risk class and its probability are different kinds of claim,
and a class-only model supplies one without the other. Listing them as distinct
variables means a rule that reads the class still works when no probability was
produced, and a rule that requires a probability is automatically unevaluable —
and reported as unevaluable — for a model that does not produce one.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.collections import (
    ML_PREDICTIONS,
    PAN_OBSERVATIONS,
    SALT_PANS,
    SIMULATION_RUNS,
    WEATHER_OBSERVATIONS,
)
from app.models.enums import DecisionVariableSource, FeatureKind

#: Returned as a reading's reason when a rule names a variable the catalogue does
#: not have. The engine's write path refuses such a rule, so reaching this string
#: means the catalogue and the evaluator disagree, which is a defect rather than a
#: user's mistake.
UNKNOWN_VARIABLE_REASON = (
    "no such variable is catalogued; a rule may only reference the published list"
)


@dataclass(frozen=True)
class DecisionVariableDefinition:
    """One quantity a decision criterion may be written against.

    Frozen and data-only, so a stored rule can be validated against this catalogue
    without running any of the code that reads it.
    """

    name: str
    kind: FeatureKind
    unit: str | None
    reads_from: DecisionVariableSource
    source_collection: str
    source_field: str
    operational_meaning: str
    derivation: str
    nullable_reason: str | None = None

    @property
    def is_model_derived(self) -> bool:
        """Whether reading this variable requires a model estimate to exist."""
        return self.reads_from is DecisionVariableSource.PREDICTION

    @property
    def is_simulated(self) -> bool:
        """Whether reading this variable requires the mass balance to have run."""
        return self.reads_from is DecisionVariableSource.SIMULATION


#: Every quantity a decision rule may reference, in the order a reader meets them:
#: the pan as it stands, the pan as the forecast says it will be, what the physics
#: says the rain will do to it, what the model estimates about the event, and
#: finally the static property an author compares all of that against.
DECISION_VARIABLE_CATALOG: tuple[DecisionVariableDefinition, ...] = (
    DecisionVariableDefinition(
        name="current_salinity_g_l",
        kind=FeatureKind.NUMERIC,
        unit="g/L",
        reads_from=DecisionVariableSource.TWIN,
        source_collection=PAN_OBSERVATIONS,
        source_field="salinity_g_l",
        operational_meaning=(
            "The brine concentration at the moment the recommendation is made. A rule "
            "about it is a statement about the pan as it stands."
        ),
        derivation=(
            "Read from the twin's newest pan observation. No earlier reading is "
            "substituted for it, because a stale figure at the decision moment is not "
            "the state the decision is about."
        ),
        nullable_reason=(
            "No pan observation supplied a salinity, so the twin does not hold one. "
            "Module 05 reports the gap rather than filling it."
        ),
    ),
    DecisionVariableDefinition(
        name="current_depth_cm",
        kind=FeatureKind.NUMERIC,
        unit="cm",
        reads_from=DecisionVariableSource.TWIN,
        source_collection=PAN_OBSERVATIONS,
        source_field="water_depth_cm",
        operational_meaning=(
            "Water/brine depth in the pan before any rain. The denominator of the "
            "dilution balance, so a rule that ignores it is ignoring how much water "
            "there is to dilute."
        ),
        derivation="Read from the twin's newest pan observation.",
        nullable_reason=(
            "No pan observation supplied a depth. The mass balance divides by it, so "
            "the physics result is refused as well rather than computed from a guess."
        ),
    ),
    DecisionVariableDefinition(
        name="forecast_rainfall_mm",
        kind=FeatureKind.NUMERIC,
        unit="mm",
        reads_from=DecisionVariableSource.TWIN,
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_mm",
        operational_meaning=(
            "The rain currently forecast for the pan. The quantity the simulation is "
            "asked to resolve, and the one a rule comparing against a dilution "
            "threshold is really about."
        ),
        derivation=(
            "Read from the twin's newest weather forecast, not from an observation. "
            "A rainfall figure the twin holds as a forecast is a projection, and the "
            "catalogue says so by its source."
        ),
        nullable_reason=(
            "No weather forecast was in force, so the twin holds no rainfall depth. "
            "A recommendation may still be issued for a what-if depth supplied "
            "explicitly, and in that case this variable is unread rather than wrong."
        ),
    ),
    DecisionVariableDefinition(
        name="rainfall_probability_percent",
        kind=FeatureKind.NUMERIC,
        unit="%",
        reads_from=DecisionVariableSource.TWIN,
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_probability_percent",
        operational_meaning=(
            "How likely the forecast rain is. A rule can weight a dilution threshold "
            "on whether the rain is expected at all, which is a different judgement "
            "from how much of it is expected."
        ),
        derivation="Read from the twin's newest weather forecast.",
        nullable_reason="No weather forecast supplied a probability.",
    ),
    DecisionVariableDefinition(
        name="target_harvest_salinity_g_l",
        kind=FeatureKind.NUMERIC,
        unit="g/L",
        reads_from=DecisionVariableSource.PAN_REGISTRATION,
        source_collection=SALT_PANS,
        source_field="target_harvest_salinity_g_l",
        operational_meaning=(
            "The salinity at which the pan is meant to be harvested. The static "
            "reference every dilution and harvest criterion is measured against, and "
            "a property of the registration that no event changes."
        ),
        derivation=(
            "Read from the pan's registration record. Required, so it is always "
            "available for a registered pan."
        ),
    ),
    DecisionVariableDefinition(
        name="simulated_post_rain_salinity_g_l",
        kind=FeatureKind.NUMERIC,
        unit="g/L",
        reads_from=DecisionVariableSource.SIMULATION,
        source_collection=SIMULATION_RUNS,
        source_field="outputs.post_rain_salinity_g_l",
        operational_meaning=(
            "The salinity the physics says the forecast rain will leave behind. This "
            "is a computed quantity, not a measurement, and a rule that reads it is "
            "reasoning about what will physically happen rather than about what the "
            "model predicts will be graded."
        ),
        derivation=(
            "Module 06's mass balance over the twin's area, depth, salinity and "
            "forecast rainfall: C1 = C0 x V0 / V1."
        ),
        nullable_reason=(
            "The mass balance could not be run, because the twin supplied no depth "
            "or no salinity. Module 06 refuses to divide by a missing depth rather "
            "than assuming one."
        ),
    ),
    DecisionVariableDefinition(
        name="simulated_salinity_drop_g_l",
        kind=FeatureKind.NUMERIC,
        unit="g/L",
        reads_from=DecisionVariableSource.SIMULATION,
        source_collection=SIMULATION_RUNS,
        source_field="outputs.salinity_drop_g_l",
        operational_meaning=(
            "How far the physics says the rain will pull the salinity down. The "
            "quantity a flood-versus-loss judgement actually turns on: a large drop "
            "dilutes the pan toward its target, and a drop of zero is a warning that "
            "no usable rain is coming."
        ),
        derivation=(
            "The same mass balance, reported as C0 - C1 rather than recomputed from "
            "the two concentrations."
        ),
        nullable_reason="The mass balance could not be run.",
    ),
    DecisionVariableDefinition(
        name="simulated_post_rain_depth_cm",
        kind=FeatureKind.NUMERIC,
        unit="cm",
        reads_from=DecisionVariableSource.SIMULATION,
        source_collection=SIMULATION_RUNS,
        source_field="outputs.post_rain_depth_m",
        operational_meaning=(
            "How deep the pan will be after the forecast rain. The figure a drainage "
            "or protection criterion reads, and the one that answers 'will this pan "
            "overflow' rather than 'will this salt get diluted'."
        ),
        derivation=(
            "The same mass balance's final depth, converted from metres to the "
            "centimetres every pan record uses."
        ),
        nullable_reason="The mass balance could not be run.",
    ),
    DecisionVariableDefinition(
        name="flood_depth_cm",
        kind=FeatureKind.NUMERIC,
        unit="cm",
        reads_from=DecisionVariableSource.PAN_REGISTRATION,
        source_collection=SALT_PANS,
        source_field="risk_thresholds.flood_depth_cm",
        operational_meaning=(
            "The depth at which this operator says to drain the pan. Catalogued as a "
            "variable so a rule can name it as its bound rather than carrying a "
            "number: the figure belongs to the pan and its operator, not to the "
            "rule, and one written rule can then serve pans that disagree about how "
            "deep is too deep."
        ),
        derivation=(
            "Read from the pan's registration record, as that operator configured "
            "it. This project supplies no value for it."
        ),
        nullable_reason=(
            "This operator has not configured a flood depth for this pan, so the "
            "criterion that needs one is unevaluable and the fallback is withheld. "
            "No depth is assumed in its place."
        ),
    ),
    DecisionVariableDefinition(
        name="dilution_salinity_g_l",
        kind=FeatureKind.NUMERIC,
        unit="g/L",
        reads_from=DecisionVariableSource.PAN_REGISTRATION,
        source_collection=SALT_PANS,
        source_field="risk_thresholds.dilution_salinity_g_l",
        operational_meaning=(
            "The salinity below which this operator says the brine is too dilute to "
            "sell. The bound a dilution criterion compares against, and the reason "
            "the mass balance is run at all: rain that would take the pan under this "
            "figure has destroyed value rather than saved it."
        ),
        derivation=(
            "Read from the pan's registration record, as that operator configured "
            "it. This project supplies no value for it."
        ),
        nullable_reason=(
            "This operator has not configured a dilution limit for this pan, so the "
            "criterion that needs one is unevaluable and the fallback is withheld."
        ),
    ),
    DecisionVariableDefinition(
        name="critical_rainfall_24h_mm",
        kind=FeatureKind.NUMERIC,
        unit="mm",
        reads_from=DecisionVariableSource.PAN_REGISTRATION,
        source_collection=SALT_PANS,
        source_field="risk_thresholds.critical_rainfall_24h_mm",
        operational_meaning=(
            "The 24-hour rainfall this operator treats as a threat to the pan. A "
            "bound rather than a derived quantity, so a rule written against it is a "
            "statement about this operation's tolerance for rain."
        ),
        derivation=(
            "Read from the pan's registration record, as that operator configured "
            "it. This project supplies no value for it."
        ),
        nullable_reason=(
            "This operator has not configured a rainfall limit for this pan, so the "
            "criterion that needs one is unevaluable and the fallback is withheld."
        ),
    ),
    DecisionVariableDefinition(
        name="target_flow_cm",
        kind=FeatureKind.NUMERIC,
        unit="cm",
        reads_from=DecisionVariableSource.PAN_REGISTRATION,
        source_collection=SALT_PANS,
        source_field="risk_thresholds.target_flow_cm",
        operational_meaning=(
            "The depth this operator intends to keep while transferring brine. The "
            "bound a transfer criterion reads to decide whether the pan is at the "
            "depth they want it at, rather than at a depth this project invented."
        ),
        derivation=(
            "Read from the pan's registration record, as that operator configured "
            "it. This project supplies no value for it."
        ),
        nullable_reason=(
            "This operator has not configured a transfer depth for this pan, so the "
            "criterion that needs one is unevaluable and the fallback is withheld."
        ),
    ),
    DecisionVariableDefinition(
        name="predicted_risk_class",
        kind=FeatureKind.CATEGORICAL,
        unit=None,
        reads_from=DecisionVariableSource.PREDICTION,
        source_collection=ML_PREDICTIONS,
        source_field="predicted_class",
        operational_meaning=(
            "The risk class the deployed model estimated, named by Module 10's own "
            "rule. A class is a model's estimate of severity, not a measurement, and "
            "a rule reading it is reasoning about a prediction rather than about the "
            "pan."
        ),
        derivation=(
            "The newest stored operational-risk prediction for the pan, read as "
            "written. The engine never scores anything itself."
        ),
        nullable_reason=(
            "No operational-risk prediction is stored for this pan, or the one on "
            "record is not about operational risk. A rule reading this variable is "
            "unevaluable rather than defaulted, and the recommendation says so."
        ),
    ),
    DecisionVariableDefinition(
        name="predicted_risk_probability",
        kind=FeatureKind.NUMERIC,
        unit=None,
        reads_from=DecisionVariableSource.PREDICTION,
        source_collection=ML_PREDICTIONS,
        source_field="probability",
        operational_meaning=(
            "The probability the deployed model attached to that class. Null for a "
            "class-only model, which is why it is a separate variable from the "
            "class: a rule requiring a probability is unevaluable for such a model "
            "and is reported as such."
        ),
        derivation=(
            "The stored prediction's own probability field, passed through "
            "unchanged. Never filled from the rule's agreement, and never derived "
            "from a margin the model never reported."
        ),
        nullable_reason=(
            "The stored prediction reports a class with no probability, or none is "
            "stored. A class-only model is a real case here, not an error."
        ),
    ),
)

_BY_NAME: dict[str, DecisionVariableDefinition] = {
    variable.name: variable for variable in DECISION_VARIABLE_CATALOG
}

#: The widest name and the widest "no value" reason this catalogue can produce,
#: measured rather than assumed. ``Recommendation`` sizes its stored audit text
#: from these, because a rule may read any catalogued variable and the audit trail
#: has to hold whatever the rule actually read. Widening the catalogue therefore
#: widens the bound automatically instead of silently producing records the
#: storage will refuse.
MAX_CATALOGUE_NAME_CHARS = max(len(v.name) for v in DECISION_VARIABLE_CATALOG)
MAX_CATALOGUE_REASON_CHARS = max(
    len(v.nullable_reason or "") for v in DECISION_VARIABLE_CATALOG
)


def resolve_decision_variable(name: str) -> DecisionVariableDefinition:
    """Look up one catalogued variable, or refuse with the names that exist.

    The error lists the catalogue because a rule that names a variable nobody
    defined is a 422 the author can act on, and an empty message would leave them
    guessing.
    """
    variable = _BY_NAME.get(name)
    if variable is not None:
        return variable
    available = ", ".join(sorted(_BY_NAME))
    raise KeyError(f"{name!r} is not a catalogued decision variable. Available: {available}")


def catalogued(name: str) -> bool:
    """Whether a name is in the catalogue, for callers that want a boolean."""
    return name in _BY_NAME


__all__ = [
    "DECISION_VARIABLE_CATALOG",
    "MAX_CATALOGUE_NAME_CHARS",
    "MAX_CATALOGUE_REASON_CHARS",
    "UNKNOWN_VARIABLE_REASON",
    "DecisionVariableDefinition",
    "catalogued",
    "resolve_decision_variable",
]
