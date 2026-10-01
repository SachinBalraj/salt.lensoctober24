"""Module 10 - the measurable variables an operational risk rule may reference.

A risk definition is a set of comparisons, and the comparisons are only meaningful
against quantities that exist. This module is the answer to "which quantities, and
where does each one come from", and it is deliberately a closed list.

**The list is closed so a rule cannot invent a measurement.** A criterion may only
name a variable catalogued here. That restriction does three things at once: a
researcher can read the whole basis on which any risk label in this project could
ever rest, a typo in a variable name is a 422 naming the real ones rather than a
column that is null in every row, and no rule can depend on a figure the system
has no way to measure. An open variable space would make every definition
unfalsifiable, because there would always be another quantity the author could
have meant.

**Each variable names its provenance, not just its value.** ``reads_from`` and
``leakage_class`` are the load-bearing fields. They are what let Module 07's
causal audit check a *derived* label row by row — a risk label is computed rather
than measured, so the usual "the target was read after the horizon" check has
nothing to inspect, and the substitute is a per-variable argument about when each
input was knowable. That argument is data here rather than prose in a docstring,
so it can be checked by code.

**The derived variables are compositions of catalogued quantities, and say so.**
``expected_salinity_loss_g_l`` and ``salinity_gap_to_target_g_l`` are differences
between two catalogued figures and nothing else. Their ``operands`` field lists
those figures, so a reader can see what a rule that uses them is really asserting,
and a change to an operand's provenance is visible rather than buried.

**One variable is a post-anchor measurement, and it is here on purpose.**
``harvest_outcome`` is the only observed outcome this project records, and a
researcher is entitled to build a rule around it. Because it is catalogued with
``leakage_class: future``, any definition that reads it is stamped
``retrospective`` and cannot be used to forecast the event it is graded against.
Cataloguing it is not the same as endorsing it; refusing to hide it is.

The class-imbalance policy lives here too, for the same reason the variables do:
it is a documented threshold rather than a literal buried in a conditional, and
both this module and Module 07's report read it from here so the number a
researcher is warned against is the number that was actually applied.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.db.collections import (
    ACTUAL_OUTCOMES,
    PAN_OBSERVATIONS,
    SALT_PANS,
    WEATHER_OBSERVATIONS,
)
from app.models.enums import (
    FeatureKind,
    LeakageClass,
    RiskLabelBasis,
    RiskVariableDerivation,
    RiskVariableSource,
)


@dataclass(frozen=True)
class RiskVariableDefinition:
    """One measurable quantity a risk criterion may be written against.

    Frozen and data-only, so a stored definition can be checked against this
    catalogue without running any of the code that reads it.
    """

    name: str
    kind: FeatureKind
    unit: str | None
    reads_from: RiskVariableSource
    leakage_class: LeakageClass
    source_collection: str
    source_field: str
    operational_meaning: str
    derivation: str
    nullable_reason: str | None = None
    operands: tuple[str, ...] = ()
    #: Which operation turns the source into this variable's value. See
    #: :class:`~app.models.enums.RiskVariableDerivation`; it is data so the
    #: builder's resolver is a match on four cases rather than a pile of
    #: ``if variable == ...`` branches that has to be kept in step by hand.
    derivation_kind: RiskVariableDerivation = RiskVariableDerivation.DIRECT
    #: The Module 07 feature column a DIRECT variable is read from, when it is
    #: not simply its own name.
    builder_column: str | None = None

    @property
    def is_known_at_anchor(self) -> bool:
        """Whether a value of this variable was available at the decision moment."""
        return self.leakage_class.is_known_at_anchor

    @property
    def column(self) -> str:
        """The Module 07 feature column this variable is read from, if any."""
        return self.builder_column or self.name


#: Every quantity an operational risk rule may reference, in the order a reader
#: meets them: the brine as it is, the brine as the forecast says it will be, the
#: gap between those and what harvest needs, the weather responsible, and finally
#: the one variable that is only knowable afterwards.
RISK_VARIABLE_CATALOG: tuple[RiskVariableDefinition, ...] = (
    RiskVariableDefinition(
        name="current_salinity_g_l",
        kind=FeatureKind.NUMERIC,
        unit="g/L",
        reads_from=RiskVariableSource.ROW_FEATURE,
        leakage_class=LeakageClass.AS_OF,
        source_collection=PAN_OBSERVATIONS,
        source_field="salinity_g_l",
        operational_meaning=(
            "The brine concentration at the moment the decision is made. A rule "
            "about it is a statement about the pan as it stands."
        ),
        derivation=(
            "Read from the anchor pan observation, the same value the dataset "
            "carries as current_salinity_g_l."
        ),
        nullable_reason=(
            "The anchor observation did not record a salinity. No previous reading "
            "is substituted for it, because a stale figure at the decision moment "
            "is not the state the decision is about."
        ),
    ),
    RiskVariableDefinition(
        name="expected_post_rain_salinity_g_l",
        kind=FeatureKind.NUMERIC,
        unit="g/L",
        reads_from=RiskVariableSource.ROW_FEATURE,
        leakage_class=LeakageClass.AS_OF,
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_mm",
        operational_meaning=(
            "The salinity the rain currently forecast is expected to leave behind. "
            "This is the quantity a dilution-loss rule is really about."
        ),
        derivation=(
            "Module 06's mass balance run on the anchor salinity, the anchor depth "
            "and the forecast rainfall in force at the anchor. The operational "
            "name for the dataset column physics_predicted_post_rain_salinity_g_l; "
            "it is the same figure, not a second one. The rainfall that actually "
            "fell is never the input, because it is a post-anchor measurement and "
            "using it would make the label a restatement of the answer."
        ),
        nullable_reason=(
            "The balance needs a pan area, a starting depth, a starting salinity "
            "and a forecast rainfall depth. Any one of them missing leaves the "
            "prediction uncomputable, and it is left null rather than run on an "
            "assumed input."
        ),
        derivation_kind=RiskVariableDerivation.DIRECT,
        builder_column="physics_predicted_post_rain_salinity_g_l",
    ),
    RiskVariableDefinition(
        name="expected_salinity_loss_g_l",
        kind=FeatureKind.NUMERIC,
        unit="g/L",
        reads_from=RiskVariableSource.ROW_FEATURE,
        leakage_class=LeakageClass.AS_OF,
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_mm",
        operational_meaning=(
            "How much salinity the pan is expected to lose to the rain in force, "
            "in g/L. The operational measure of a dilution event: a rule written "
            "against a loss can state a limit that holds whatever the brine was "
            "concentrated to beforehand."
        ),
        derivation=(
            "current_salinity_g_l - expected_post_rain_salinity_g_l, in g/L. A "
            "difference of two catalogued figures and nothing else. Reported as a "
            "concentration drop rather than a percentage because a percentage is a "
            "ratio against the starting salinity and is undefined when that is "
            "0 g/L, which is the same reason Module 06 refuses to render it as a "
            "zero."
        ),
        nullable_reason=(
            "Either operand is unavailable. The reason names which one, so a rule "
            "that never fires can be traced to a missing input rather than to a "
            "threshold that was never reached."
        ),
        operands=("current_salinity_g_l", "expected_post_rain_salinity_g_l"),
        derivation_kind=RiskVariableDerivation.DIFFERENCE,
    ),
    RiskVariableDefinition(
        name="target_harvest_salinity_g_l",
        kind=FeatureKind.NUMERIC,
        unit="g/L",
        reads_from=RiskVariableSource.PAN_REGISTRATION,
        leakage_class=LeakageClass.STATIC,
        source_collection=SALT_PANS,
        source_field="target_harvest_salinity_g_l",
        operational_meaning=(
            "The concentration the operator needs the brine at to harvest. It is "
            "the target every dilution judgement is measured against, and it is a "
            "registered property of the pan rather than a measurement of it."
        ),
        derivation=(
            "Read from the pan's registration record, and only where the pan was "
            "registered at or before the anchor instant. A pan registered after "
            "the anchor contributes nothing, because its target was not part of "
            "the information available then."
        ),
        nullable_reason=(
            "The pan had not been registered at the anchor instant, or is not "
            "registered at all."
        ),
        derivation_kind=RiskVariableDerivation.PAN_FIELD,
    ),
    RiskVariableDefinition(
        name="salinity_gap_to_target_g_l",
        kind=FeatureKind.NUMERIC,
        unit="g/L",
        reads_from=RiskVariableSource.ROW_FEATURE,
        leakage_class=LeakageClass.AS_OF,
        source_collection=SALT_PANS,
        source_field="target_harvest_salinity_g_l",
        operational_meaning=(
            "How far the expected post-rain brine falls short of the harvest "
            "target, in g/L. Positive means the rain leaves the pan too dilute to "
            "harvest; negative means it leaves it past the target, which on a pan "
            "worked by evaporation and crystallisation is the concentration side "
            "of the same decision."
        ),
        derivation=(
            "target_harvest_salinity_g_l - expected_post_rain_salinity_g_l, in "
            "g/L. A signed difference, so a rule against it is a two-sided "
            "statement about the same quantity rather than two unrelated "
            "thresholds."
        ),
        nullable_reason=(
            "Either operand is unavailable: the pan had no registered target at the "
            "anchor, or the post-rain salinity was uncomputable."
        ),
        operands=("target_harvest_salinity_g_l", "expected_post_rain_salinity_g_l"),
        derivation_kind=RiskVariableDerivation.DIFFERENCE,
    ),
    RiskVariableDefinition(
        name="forecast_rainfall_mm",
        kind=FeatureKind.NUMERIC,
        unit="mm",
        reads_from=RiskVariableSource.ROW_FEATURE,
        leakage_class=LeakageClass.AS_OF,
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_mm",
        operational_meaning=(
            "Rainfall severity: the depth the forecast in force expected. The "
            "quantity a severity rule is most naturally written against, because "
            "it is both measurable in advance and the direct cause of dilution."
        ),
        derivation=(
            "The rainfall_mm of the newest forecast issued at or before the anchor "
            "instant, selected by issue time. A forecast retrieved after the "
            "anchor, however accurate, was not available to anybody at the moment "
            "being judged."
        ),
        nullable_reason=(
            "No forecast had been issued for this pan at or before the anchor "
            "instant, or the forecast in force carried no rainfall figure. The "
            "rainfall that actually fell is never substituted for it."
        ),
    ),
    RiskVariableDefinition(
        name="forecast_rainfall_probability_percent",
        kind=FeatureKind.NUMERIC,
        unit="percent",
        reads_from=RiskVariableSource.ROW_FEATURE,
        leakage_class=LeakageClass.AS_OF,
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_probability_percent",
        operational_meaning=(
            "How certain the forecast was, as opposed to how deep. A confident "
            "light rain and an uncertain heavy one are different operational "
            "situations, and a severity rule may legitimately need both."
        ),
        derivation="Read from the same forecast record as forecast_rainfall_mm.",
        nullable_reason=(
            "No forecast was in force at the anchor, or it carried no probability "
            "figure."
        ),
    ),
    RiskVariableDefinition(
        name="recent_rainfall_mm",
        kind=FeatureKind.NUMERIC,
        unit="mm",
        reads_from=RiskVariableSource.ROW_FEATURE,
        leakage_class=LeakageClass.AS_OF,
        source_collection=WEATHER_OBSERVATIONS,
        source_field="rainfall_mm",
        operational_meaning=(
            "Rain already recorded in the lookback window. Included because brine "
            "diluted in the last three days is still carrying that dilution, so a "
            "rule about cumulative wetness is about a different risk from one about "
            "a single forecast."
        ),
        derivation=(
            "The sum of rainfall_mm over observed weather records in the lookback "
            "window ending at the anchor. Forecast rows are excluded so a "
            "prediction is not counted as rain."
        ),
        nullable_reason=(
            "The value is 0.0 when the window held no rainfall rows, and 0.0 on an "
            "empty window is an absence of records rather than a measurement of dry "
            "weather."
        ),
    ),
    RiskVariableDefinition(
        name="water_depth_cm",
        kind=FeatureKind.NUMERIC,
        unit="cm",
        reads_from=RiskVariableSource.ROW_FEATURE,
        leakage_class=LeakageClass.AS_OF,
        source_collection=PAN_OBSERVATIONS,
        source_field="water_depth_cm",
        operational_meaning=(
            "Standing brine depth. Sets the brine volume, so it decides how much "
            "rain it takes to dilute meaningfully and is the second term of the "
            "mass balance behind expected_post_rain_salinity_g_l."
        ),
        derivation="Read directly from the anchor pan observation.",
        nullable_reason="The anchor observation did not record a depth.",
    ),
    RiskVariableDefinition(
        name="brine_temperature_c",
        kind=FeatureKind.NUMERIC,
        unit="degC",
        reads_from=RiskVariableSource.ROW_FEATURE,
        leakage_class=LeakageClass.AS_OF,
        source_collection=PAN_OBSERVATIONS,
        source_field="brine_temperature_c",
        operational_meaning=(
            "Brine temperature, which sets saturation: the same concentration is "
            "closer to crystallising in cold brine than in warm. A rule that "
            "ignores it is asserting that the harvest window does not move with the "
            "weather."
        ),
        derivation="Read directly from the anchor pan observation.",
        nullable_reason="The anchor observation did not record a brine temperature.",
    ),
    RiskVariableDefinition(
        name="harvest_outcome",
        kind=FeatureKind.CATEGORICAL,
        unit=None,
        reads_from=RiskVariableSource.RECORDED_OUTCOME,
        leakage_class=LeakageClass.FUTURE,
        source_collection=ACTUAL_OUTCOMES,
        source_field="harvest_outcome",
        operational_meaning=(
            "What came of the harvest, from the closed vocabulary operators already "
            "record. The only genuinely *observed* risk variable in the catalogue, "
            "and the only one that cannot be known before the event it describes."
        ),
        derivation=(
            "The harvest_outcome on the first recorded outcome for the pan at or "
            "after the anchor plus the horizon. A recorded 'none' is a value; a "
            "row whose outcome was never recorded is unlabelled, because a missing "
            "outcome is a gap in the record and not a result."
        ),
        nullable_reason=(
            "No outcome was recorded at or after the anchor plus the horizon. It is "
            "not filled from an earlier event, whose harvest says nothing about "
            "this one."
        ),
        derivation_kind=RiskVariableDerivation.RECORDED_OUTCOME,
    ),
)

RISK_VARIABLE_INDEX: dict[str, RiskVariableDefinition] = {
    variable.name: variable for variable in RISK_VARIABLE_CATALOG
}

#: Variables whose value is a post-anchor measurement. A definition reading any of
#: them is retrospective, and the two are reported separately because a label
#: built from an observed outcome cannot forecast that outcome.
RETROSPECTIVE_VARIABLES: tuple[str, ...] = tuple(
    variable.name
    for variable in RISK_VARIABLE_CATALOG
    if not variable.leakage_class.is_known_at_anchor
)

#: The variables a definition has to be able to read for its own criteria, grouped
#: by where they come from. Exposed so a caller can be told what a definition
#: depends on before building a dataset against it.
def variables_by_source() -> dict[RiskVariableSource, tuple[str, ...]]:
    """Catalogued variable names grouped by where each is read from."""
    grouped: dict[RiskVariableSource, list[str]] = {
        source: [] for source in RiskVariableSource
    }
    for variable in RISK_VARIABLE_CATALOG:
        grouped[variable.reads_from].append(variable.name)
    return {source: tuple(names) for source, names in grouped.items()}


def resolve_risk_variable(name: str) -> RiskVariableDefinition:
    """Return the definition for a variable, or explain that it is unknown.

    Raised with the available names in the message so a caller can turn it
    directly into a 422, the way :func:`~app.services.dataset_catalog.resolve_target`
    does for a research target.
    """
    variable = RISK_VARIABLE_INDEX.get(name)
    if variable is None:
        raise LookupError(
            f"unknown risk variable {name!r}; available variables: "
            + ", ".join(RISK_VARIABLE_INDEX)
        )
    return variable


def label_basis_for(variable_names: tuple[str, ...] | list[str]) -> RiskLabelBasis:
    """Whether a set of variables supports a prospective or retrospective label.

    The rule is a single sentence: a label is retrospective if any input was
    measured after the anchor. A definition mixing both kinds of variable is
    refused at write time rather than being quietly filed under one of them,
    because a model fitted to such a label would appear to forecast while in fact
    being handed the answer.
    """
    return (
        RiskLabelBasis.RETROSPECTIVE
        if any(
            not resolve_risk_variable(name).leakage_class.is_known_at_anchor
            for name in variable_names
        )
        else RiskLabelBasis.PROSPECTIVE
    )


# -- the class-imbalance policy -------------------------------------------------

#: Largest tolerated ratio between the most and least populated classes.
#:
#: Stated as a number rather than derived, and imported by Module 07's report so
#: there is one threshold in the project rather than one per module. Ten to one is
#: the point at which an accuracy figure begins to be mostly a statement about the
#: majority class: at 10:1 a model that predicted the majority class every time
#: would score 91%, which is a real number about the data and a misleading one
#: about the model. It is a threshold and not a rule — a distribution past it can
#: still be worth fitting after rebalancing, and the response is a decision rather
#: than a refusal.
SEVERE_IMBALANCE_RATIO = 10.0

#: Fewest rows the rarest populated class must hold to count as learnable.
#:
#: Below this a per-class figure is not an estimate. It is reported as a warning
#: rather than folded into the ratio, because a rule can be genuinely 30:1 and
#: still support a majority-class baseline, and the two problems have different
#: fixes.
RARE_CLASS_MINIMUM_ROWS = 5

#: The class names this module must never supply itself.
#:
#: Present only so a reader can check it: a grep for "high", "medium" or "low"
#: against the risk module should return this sentence and nothing that assigns
#: one. The vocabulary belongs to the researcher, and a project that has quietly
#: reintroduced a default one has stopped being configurable in the way that
#: matters.
FORBIDDEN_DEFAULT_LABELS = ("high", "medium", "low")

__all__ = [
    "FORBIDDEN_DEFAULT_LABELS",
    "RARE_CLASS_MINIMUM_ROWS",
    "RETROSPECTIVE_VARIABLES",
    "RISK_VARIABLE_CATALOG",
    "RISK_VARIABLE_INDEX",
    "SEVERE_IMBALANCE_RATIO",
    "RiskVariableDefinition",
    "label_basis_for",
    "resolve_risk_variable",
    "variables_by_source",
]
