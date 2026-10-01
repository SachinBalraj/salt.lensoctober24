"""Module 16 - compare stored predictions against recorded outcomes.

Everything this module reports is derived from :class:`ActualOutcome` records
and the documents those records are linked to. It never re-runs a model and it
never invents a figure: a contamination of the validation set is impossible,
because the predictions being graded are the ones that were actually stored
before the events happened.

Five comparison families are built from the same outcome records:

* **forecast vs actual rainfall** (mm) -- the rainfall the forecast expected
  against the rain that fell;
* **simulated vs actual salinity** (g/L) -- the mass balance run on the rain
  that actually fell, against the salinity that was measured;
* **forecast-simulated vs actual salinity** (g/L) -- the same balance run on the
  recorded forecast, so its error is the forecast's and the simulation's
  together;
* **predicted risk vs outcome** -- the risk class a stored model estimate cited
  against the same risk rule applied to what actually happened;
* **recommended vs operator action** -- the action the in-force recommendation
  advised against the action the operator recorded taking.

The metric discipline is the project's own: a reading that was not calculated is
``computed=false`` with a reason, never a number; every aggregate travels beside
the count of events that produced it and the reasons the rest could not; and a
single-class or single-event series withholds the figures that would only
measure a constant.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sklearn.metrics import average_precision_score, roc_auc_score

from app.db.collections import (
    ACTUAL_OUTCOMES,
    ML_PREDICTIONS,
    OPERATOR_DECISIONS,
    RECOMMENDATIONS,
    RISK_DEFINITIONS,
    SALT_PANS,
)
from app.db.repository import MongoRepository
from app.models.actual_outcome import ActualOutcome, OutcomeErrorTerms
from app.models.enums import DecisionAction, LeakageClass
from app.models.evaluation import (
    VARIANT_BINARY,
    MetricReading,
    computed,
    not_computed,
)
from app.models.ml_prediction import MLPrediction
from app.models.operator_decision import OperatorDecision
from app.models.recommendation import Recommendation
from app.models.risk_definition import RiskDefinition, RiskSourceVariable
from app.models.salt_pan import SaltPan
from app.schemas.validation import (
    COMPARISON_KIND_CLASSIFICATION,
    COMPARISON_KIND_CONTINUOUS,
    ClassificationComparison,
    ConfusionMatrix,
    ContinuousComparison,
    ExcludedReason,
    PerClassScore,
    ValidationOverview,
    ValidationPairRow,
    ValidationPairsResponse,
    ValidationScope,
)
from app.services.csv_security import sanitize_prose_cell
from app.services.outcomes import (
    MISSING_ACTUAL_RAINFALL,
    MISSING_ACTUAL_SALINITY,
    NO_COMPARABLE_EVENTS,
)
from app.services.risk_evaluation import evaluate_definition

# -- reasons an event could not be compared ---------------------------------

NO_RECOMMENDATION_IN_FORCE = (
    "No recommendation was in force for this event, so no predicted risk was "
    "recorded against it."
)
RECOMMENDATION_UNREAD = (
    "The recommendation the outcome is linked to could not be read, so no "
    "prediction and no action are on the record for it."
)
PHYSICS_AND_RULES_NO_MODEL = (
    "The recommendation in force was decided by physics and rules, so no model "
    "estimate was consulted and no predicted risk class is on the record."
)
PREDICTION_UNREAD = (
    "The model estimate the recommendation cited could not be read, so the "
    "predicted risk class is not on the record."
)
PREDICTION_WITHOUT_DEFINITION = (
    "The prediction the recommendation cited cannot be tied to a risk "
    "definition, so its classes cannot be re-derived from what actually happened."
)
DEFINITION_UNREAD = (
    "The risk definition the prediction was made under could not be read, so the "
    "rule cannot be re-applied to what actually happened."
)
PREDICTED_CLASS_OMITTED = (
    "The stored prediction recorded no risk class, so there is nothing to grade."
)
PREDICTED_CLASS_OUTSIDE_VOCABULARY = (
    "The predicted risk class is not part of the risk definition's vocabulary, "
    "so it cannot be graded against a label the rule can produce."
)
RECOMMENDATION_CARRIES_NO_ACTION = (
    "The recommendation in force carried no advised action, so there is nothing "
    "to compare the operator's call against."
)
NO_OPERATOR_DECISION = (
    "No operator decision was recorded for the recommendation in force, so the "
    "action actually taken is not on the record."
)
DECISION_UNREAD = (
    "The operator decision the outcome is linked to could not be read, so the "
    "action actually taken is not on the record."
)
DECISION_WITHOUT_ACTION = (
    "The recorded operator decision did not fill in the action taken, so there "
    "is nothing to compare against the advised one."
)

NO_SALINITY_PREDICTION = (
    "The salinity prediction was not computed, so there is nothing to compare "
    "against the measured value."
)
NO_FORECAST_RAINFALL = (
    "No forecast rainfall was recorded for this event, so there is no forecast "
    "to grade against the rain that fell."
)

# -- why a metric was withheld ----------------------------------------------

RMSE_NEEDS_TWO = (
    "RMSE is only reported over at least two comparable events; a single event "
    "cannot support a typical-error figure."
)
R2_NEEDS_TWO = (
    "R-squared is only reported over at least two comparable events, because a "
    "line through one point cannot be evaluated for explanatory power."
)
R2_NEEDS_VARIANCE = (
    "R-squared is only reported when the measured values actually vary; every "
    "comparable event measured the same figure, so a constant explains them all "
    "perfectly and no number would say anything."
)
ACCURACY_NEEDS_TWO = (
    "Accuracy is only reported over at least two compared events; a single event "
    "cannot support a rate."
)
RATES_NEED_TWO_CLASSES = (
    "Every compared event carried the same actual class, so any rate would "
    "report a constant majority rather than agreement worth a number."
)
CURVES_NEED_BINARY_VOCABULARY = (
    "A ranking curve is only reported for a two-class rule, because only the "
    "probability of the predicted class is stored and a one-vs-rest curve over "
    "more classes cannot be reconstructed from it."
)
CURVES_NEED_TWO_CLASSES = (
    "A ranking curve needs both classes to appear among the actual outcomes, so "
    "nothing can be ranked."
)
NO_PROBABILITY_FOR_ACTIONS = (
    "No probability is stored for an advised action, so no ranking curve is "
    "possible over action recommendations."
)
PER_CLASS_PRECISION_UNDEFINED = (
    "This class was never predicted, so nothing was offered to be right or wrong "
    "and there is no precision to report."
)
PER_CLASS_RECALL_UNDEFINED = (
    "No compared event actually carried this class, so recall cannot be measured."
)
PER_CLASS_F1_UNDEFINED = (
    "Neither a prediction of nor a real occurrence of this class was compared, "
    "so no F1 exists."
)

DEFAULT_DIFFERENT_DEFINITION = (
    "A prediction made under a different version of the risk rule cannot share a "
    "confusion matrix with the headline rule, so this event is reported in the "
    "pairs view but left out of the aggregate."
)

#: The advised-action vocabulary, in declaration order, derived from the enum so
#: it cannot drift from the closed list the decision modules enforce.
DECISION_ACTIONS: tuple[str, ...] = tuple(action.value for action in DecisionAction)

_COMPARISON_META: dict[str, tuple[str, str, str, str]] = {
    "forecast_vs_actual_rainfall": (
        COMPARISON_KIND_CONTINUOUS,
        "mm",
        "Forecast rainfall vs actual rainfall",
        (
            "The rainfall the forecast expected against the rain that actually "
            "fell. The forecast's own error, in the units a forecast is read in."
        ),
    ),
    "simulated_vs_actual_salinity": (
        COMPARISON_KIND_CONTINUOUS,
        "g/L",
        "Simulated salinity vs actual salinity",
        (
            "The mass-balance prediction made from the rain that actually fell "
            "against the post-event salinity that was measured. The simulator's "
            "own error."
        ),
    ),
    "forecast_simulated_vs_actual_salinity": (
        COMPARISON_KIND_CONTINUOUS,
        "g/L",
        "Forecast-simulated salinity vs actual salinity",
        (
            "The same balance started from the recorded forecast rainfall, "
            "graded against the same measurement. Its error is the forecast's "
            "and the simulator's together."
        ),
    ),
    "predicted_risk_vs_outcome": (
        COMPARISON_KIND_CLASSIFICATION,
        None,
        "Predicted risk vs recorded outcome",
        (
            "The risk class a stored model estimate cited, against the same risk "
            "rule re-applied to what actually happened. Graded under the exact "
            "definition version the prediction was made under."
        ),
    ),
    "recommended_vs_operator_action": (
        COMPARISON_KIND_CLASSIFICATION,
        None,
        "Recommended action vs operator action",
        (
            "The action the recommendation in force advised against the action "
            "the operator recorded taking. Advice, disposition and outcome in "
            "one graded chain."
        ),
    ),
}

CONTINUOUS_EXPORT_COLUMNS = (
    "outcome_id",
    "pan_id",
    "event_id",
    "timestamp",
    "compared",
    "reason",
    "predicted_number",
    "actual_number",
    "signed_error",
)
CLASSIFICATION_EXPORT_COLUMNS = (
    "outcome_id",
    "pan_id",
    "event_id",
    "timestamp",
    "compared",
    "reason",
    "predicted_class",
    "actual_class",
    "probability",
    "risk_definition_id",
    "risk_definition_name",
    "risk_definition_version",
)


@dataclass
class _ClassificationContext:
    """The documents a classification pair may need, loaded once per report.

    Loaded lazily in a single pass over the outcome ids so N+1 lookups do not
    multiply per event; the records are keyed by their identifiers.
    """

    recommendations: dict[str, Recommendation] = field(default_factory=dict)
    decisions: dict[str, OperatorDecision] = field(default_factory=dict)
    predictions: dict[str, MLPrediction] = field(default_factory=dict)
    definitions: dict[tuple[str, int], RiskDefinition] = field(default_factory=dict)
    pans: dict[str, SaltPan] = field(default_factory=dict)


def _cfm_gate_rows(pairs: list[tuple[float, float]]) -> dict[str, MetricReading]:
    """The four continuous metrics, each gated on the rows that support it.

    MAE and MBE are facts about the series however short it is; RMSE needs a
    second event to be a judgement about a spread rather than a restatement of
    one error; R-squared additionally needs the measured values to vary, or
    every model would be rated perfect against a constant.
    """
    n = len(pairs)
    if n == 0:
        return {
            "mean_absolute_error": not_computed(NO_COMPARABLE_EVENTS),
            "root_mean_squared_error": not_computed(NO_COMPARABLE_EVENTS),
            "r_squared": not_computed(NO_COMPARABLE_EVENTS),
            "mean_bias_error": not_computed(NO_COMPARABLE_EVENTS),
        }
    errors = [predicted - actual for predicted, actual in pairs]
    mae = computed(sum(abs(error) for error in errors) / n)
    mbe = computed(sum(errors) / n)
    rmse: MetricReading
    if n < 2:
        rmse = not_computed(RMSE_NEEDS_TWO)
    else:
        rmse = computed(math.sqrt(sum(error * error for error in errors) / n))
    r2: MetricReading
    if n < 2:
        r2 = not_computed(R2_NEEDS_TWO)
    else:
        mean_actual = sum(actual for _, actual in pairs) / n
        ss_res = sum(error * error for error in errors)
        ss_tot = sum(
            (actual - mean_actual) * (actual - mean_actual) for _, actual in pairs
        )
        if math.isclose(ss_tot, 0.0, abs_tol=1e-12):
            r2 = not_computed(R2_NEEDS_VARIANCE)
        else:
            r2 = computed(1.0 - ss_res / ss_tot)
    return {
        "mean_absolute_error": mae,
        "root_mean_squared_error": rmse,
        "r_squared": r2,
        "mean_bias_error": mbe,
    }


def _continuous_block(
    *,
    key: str,
    rows: list[ValidationPairRow],
) -> ContinuousComparison:
    compared = [row for row in rows if row.compared]
    excluded = [row for row in rows if not row.compared]
    meta = _COMPARISON_META[key]
    readings = _cfm_gate_rows(
        [
            (row.predicted_number, row.actual_number)
            for row in compared
            if row.predicted_number is not None and row.actual_number is not None
        ]
    )
    return ContinuousComparison(
        key=key,
        title=meta[2],
        unit=meta[1],
        description=meta[3],
        n_compared=len(compared),
        n_excluded=len(excluded),
        excluded_reasons=_count_reasons(excluded),
        mean_absolute_error=readings["mean_absolute_error"],
        root_mean_squared_error=readings["root_mean_squared_error"],
        r_squared=readings["r_squared"],
        mean_bias_error=readings["mean_bias_error"],
        notes=[],
    )


def _count_reasons(rows: list[ValidationPairRow]) -> list[ExcludedReason]:
    counts = Counter(row.reason for row in rows if row.reason)
    return [
        ExcludedReason(reason=reason, count=count)
        for reason, count in sorted(
            counts.items(), key=lambda item: (-item[1], item[0])
        )
    ]


def _salinity_pair(
    outcome: ActualOutcome,
    block: OutcomeErrorTerms,
) -> tuple[float | None, float | None, str | None]:
    """The pair for a salinity block, and why there is none when there is none.

    ``actual`` is the measured post-event salinity; ``predicted`` is the stored
    balance of the block. The reason is drawn from the block's own record: the
    assignment never concludes from the predicted figure alone.
    """
    predicted = block.predicted
    actual = outcome.post_event_salinity_g_l
    if block.signed_error is not None:
        return predicted, actual, None
    if actual is None:
        return predicted, None, MISSING_ACTUAL_SALINITY
    if block.missing_inputs:
        reasons = sorted(missing.reason for missing in block.missing_inputs)
        return None, actual, "; ".join(reasons) + " " + NO_SALINITY_PREDICTION
    return None, actual, NO_SALINITY_PREDICTION


def _rainfall_pair(outcome: ActualOutcome) -> tuple[float | None, float | None, str | None]:
    """The forecast against the rain that fell, and why not when not."""
    predicted = outcome.forecast_rainfall_mm
    actual = outcome.actual_rainfall_mm
    if predicted is not None and actual is not None:
        return predicted, actual, None
    if predicted is None and actual is None:
        return None, None, (
            "Neither the forecast rainfall nor the rain that fell was recorded "
            "for this event, so the forecast cannot be graded."
        )
    if predicted is None:
        return None, actual, NO_FORECAST_RAINFALL
    return predicted, None, MISSING_ACTUAL_RAINFALL


def _outcome_readings(
    outcome: ActualOutcome, pan: SaltPan | None
) -> dict[str, RiskSourceVariable]:
    """The risk rule's variables, read from an outcome's recorded figures.

    Everything is derived from what the event recorded and the pan's
    registration -- never from the stored prediction itself, so the actual label
    is a second reading of the event rather than a restatement of the answer.
    Variables a rule asks for that this event cannot supply are simply absent;
    :func:`evaluate_definition` turns each into an unavailable variable with the
    catalogue's reason, which is how a rule that cannot be evaluated on the
    record stays honest about it.
    """

    def reading(name: str, value: float | str | None, unit: str | None) -> RiskSourceVariable:
        return RiskSourceVariable(
            variable=name,
            value=value,
            unit=unit,
            leakage_class=LeakageClass.AS_OF,
        )

    current = outcome.pre_event_salinity_g_l
    depth = outcome.pre_event_depth_cm
    forecast = outcome.forecast_rainfall_mm
    expected = outcome.forecast_simulation_error.predicted
    target = pan.target_harvest_salinity_g_l if pan is not None else None

    readings: dict[str, RiskSourceVariable] = {}
    if current is not None:
        readings["current_salinity_g_l"] = reading("current_salinity_g_l", current, "g/L")
    if depth is not None:
        readings["water_depth_cm"] = reading("water_depth_cm", depth, "cm")
    if forecast is not None:
        readings["forecast_rainfall_mm"] = reading("forecast_rainfall_mm", forecast, "mm")
    if expected is not None:
        readings["expected_post_rain_salinity_g_l"] = reading(
            "expected_post_rain_salinity_g_l", expected, "g/L"
        )
        if current is not None:
            readings["expected_salinity_loss_g_l"] = reading(
                "expected_salinity_loss_g_l", current - expected, "g/L"
            )
        if target is not None:
            readings["salinity_gap_to_target_g_l"] = reading(
                "salinity_gap_to_target_g_l", target - expected, "g/L"
            )
    if target is not None:
        readings["target_harvest_salinity_g_l"] = reading(
            "target_harvest_salinity_g_l", target, "g/L"
        )
    if outcome.harvest_outcome is not None:
        readings["harvest_outcome"] = reading(
            "harvest_outcome", outcome.harvest_outcome.value, None
        )
    return readings


def _classification_readings(
    *,
    actuals: list[str],
    predicted: list[str],
    classes: list[str],
    positive_class: str | None,
    probabilities: list[float | None],
) -> tuple[
    dict[str, MetricReading],
    list[PerClassScore],
    ConfusionMatrix | None,
    list[str],
]:
    """All classification metrics, each gated on what actually occurred.

    ``classes`` is the label space the matrix and the per-class rows are written
    against (a risk rule's full vocabulary, or the four actions), so a class the
    events never produced appears as zeros with the reason, never as a gap.
    """
    n = len(actuals)
    true_counts = Counter(actuals)
    pred_counts = Counter(predicted)
    observed = sorted(set(actuals) | set(predicted))
    notes: list[str] = []

    readings: dict[str, MetricReading] = {}

    accuracy: MetricReading
    if n < 2:
        accuracy = not_computed(ACCURACY_NEEDS_TWO)
    elif len(true_counts) < 2:
        accuracy = not_computed(RATES_NEED_TWO_CLASSES)
    else:
        accuracy = computed(
            sum(a == p for a, p in zip(actuals, predicted, strict=True)) / n * 100.0
        )
    readings["accuracy"] = accuracy

    def _denominator_counts() -> tuple[list[str], list[str], list[str]]:
        """Observed classes with a defined precision, recall and F1 denominator."""
        defined_precision = [c for c in observed if pred_counts[c] > 0]
        defined_recall = [c for c in observed if true_counts[c] > 0]
        defined_f1 = [
            c for c in observed if pred_counts[c] > 0 and true_counts[c] > 0
        ]
        return defined_precision, defined_recall, defined_f1

    def _cells(labels: list[str]) -> dict[str, tuple[float, float, float]]:
        cells: dict[str, tuple[float, float, float]] = {}
        for label in labels:
            tp = sum(
                1 for a, p in zip(actuals, predicted, strict=True) if a == label and p == label
            )
            fp = pred_counts[label] - tp
            fn = true_counts[label] - tp
            cells[label] = (tp, fp, fn)
        return cells

    macro_gate = len(true_counts) >= 2
    if not macro_gate:
        readings["precision"] = not_computed(RATES_NEED_TWO_CLASSES)
        readings["recall"] = not_computed(RATES_NEED_TWO_CLASSES)
        readings["f1"] = not_computed(RATES_NEED_TWO_CLASSES)
    else:
        defined_p, defined_r, defined_f = _denominator_counts()
        cells = _cells(observed)
        precisions = [cells[c][0] / (cells[c][0] + cells[c][1]) for c in defined_p]
        recalls = [cells[c][0] / (cells[c][0] + cells[c][2]) for c in defined_r]
        f1s: list[float] = []
        for c in defined_f:
            tp, fp, fn = cells[c]
            recall = tp / (tp + fn)
            precision = tp / (tp + fp)
            f1s.append(2 * precision * recall / (precision + recall))
        readings["precision"] = (
            computed(sum(precisions) / len(precisions) * 100.0)
            if defined_p
            else not_computed(RATES_NEED_TWO_CLASSES)
        )
        readings["recall"] = (
            computed(sum(recalls) / len(recalls) * 100.0)
            if defined_r
            else not_computed(RATES_NEED_TWO_CLASSES)
        )
        readings["f1"] = (
            computed(sum(f1s) / len(f1s) * 100.0)
            if defined_f
            else not_computed(RATES_NEED_TWO_CLASSES)
        )
        for name, defined, total in (
            ("precision", defined_p, observed),
            ("recall", defined_r, observed),
            ("f1", defined_f, observed),
        ):
            if len(defined) < len(total):
                missing = sorted(set(total) - set(defined))
                notes.append(
                    f"The macro {name} averages {name} only over the "
                    f"{len(defined)} observed classes where it is defined; "
                    f"{', '.join(missing)} had no {'prediction' if name in ('precision', 'f1') else 'occurrence'} to measure."
                )

    positive_positive = positive_class
    if positive_positive is not None:
        binary = len(classes) == 2
        both_real = len(true_counts) >= 2
        all_probabilities = n > 0 and all(
            probability is not None for probability in probabilities
        )
        if not binary:
            readings["roc_auc"] = not_computed(CURVES_NEED_BINARY_VOCABULARY)
            readings["pr_auc"] = not_computed(CURVES_NEED_BINARY_VOCABULARY)
        elif not both_real:
            readings["roc_auc"] = not_computed(CURVES_NEED_TWO_CLASSES)
            readings["pr_auc"] = not_computed(CURVES_NEED_TWO_CLASSES)
        elif not all_probabilities:
            missing_probs = sum(1 for p in probabilities if p is None)
            reason = (
                "A ranking curve needs the stored probability of every compared "
                f"event; {missing_probs} carried none, so the ranking is "
                "incomplete and no curve is reported."
            )
            readings["roc_auc"] = not_computed(reason)
            readings["pr_auc"] = not_computed(reason)
        else:
            scores = [
                probability if p == positive_positive else 1.0 - probability
                for p, probability in zip(predicted, probabilities, strict=True)
            ]
            binary_truth = [1 if a == positive_positive else 0 for a in actuals]
            readings["roc_auc"] = computed(
                roc_auc_score(binary_truth, scores),
                variant=VARIANT_BINARY,
                reason=f"the positive class is {positive_positive!r}",
            )
            readings["pr_auc"] = computed(
                average_precision_score(binary_truth, scores),
                variant=VARIANT_BINARY,
                reason=f"the positive class is {positive_positive!r}",
            )
            notes.append(
                f"roc_auc and pr_auc treat {positive_positive!r} as the positive "
                "class and reconstruct its probability from the stored "
                "probability of the predicted class."
            )
    else:
        readings["roc_auc"] = not_computed(NO_PROBABILITY_FOR_ACTIONS)
        readings["pr_auc"] = not_computed(NO_PROBABILITY_FOR_ACTIONS)

    per_class: list[PerClassScore] = []
    for label in classes:
        support = true_counts[label]
        predicted_count = pred_counts[label]
        tp = sum(
            1 for a, p in zip(actuals, predicted, strict=True) if a == label and p == label
        )
        fp = predicted_count - tp
        fn = support - tp
        precision: MetricReading
        recall: MetricReading
        f1_reading: MetricReading
        if predicted_count == 0:
            precision = not_computed(PER_CLASS_PRECISION_UNDEFINED)
        else:
            precision = computed(tp / (tp + fp) * 100.0)
        if support == 0:
            recall = not_computed(PER_CLASS_RECALL_UNDEFINED)
        else:
            recall = computed(tp / (tp + fn) * 100.0)
        if predicted_count == 0 and support == 0:
            f1_reading = not_computed(PER_CLASS_F1_UNDEFINED)
        elif predicted_count == 0:
            f1_reading = not_computed(PER_CLASS_PRECISION_UNDEFINED)
        elif support == 0:
            f1_reading = not_computed(PER_CLASS_RECALL_UNDEFINED)
        else:
            denom = precision.value + recall.value
            f1_reading = computed(2 * precision.value * recall.value / denom)
        per_class.append(
            PerClassScore(
                label=label,
                support=support,
                predicted=predicted_count,
                precision=precision,
                recall=recall,
                f1=f1_reading,
            )
        )

    matrix: ConfusionMatrix | None = None
    if n >= 1:
        classes_absent = [
            c for c in classes if true_counts[c] == 0 and pred_counts[c] == 0
        ]
        matrix = ConfusionMatrix(
            labels=list(classes),
            matrix=[
                [
                    sum(
                        1
                        for a, p in zip(actuals, predicted, strict=True)
                        if a == actual_label and p == predicted_label
                    )
                    for predicted_label in classes
                ]
                for actual_label in classes
            ],
            classes_absent_from_evaluation=classes_absent,
        )

    return readings, per_class, matrix, notes


def _classification_block(
    *,
    key: str,
    rows: list[ValidationPairRow],
    classes: list[str],
    positive_class: str | None,
    definition_id: str | None = None,
    definition_name: str | None = None,
    definition_version: int | None = None,
) -> ClassificationComparison:
    meta = _COMPARISON_META[key]
    compared = [row for row in rows if row.compared]
    excluded = [row for row in rows if not row.compared]
    actuals = [row.actual_class for row in compared if row.actual_class is not None]
    predicted = [
        row.predicted_class for row in compared if row.predicted_class is not None
    ]
    probabilities = [row.probability for row in compared]
    readings, per_class, matrix, notes = _classification_readings(
        actuals=actuals,
        predicted=predicted,
        classes=classes,
        positive_class=positive_class,
        probabilities=probabilities,
    )
    notes.append(
        "A class never predicted has no precision and a class never observed has "
        "no recall; those per-class figures are reported as not computed rather "
        "than as zero."
    )
    return ClassificationComparison(
        key=key,
        title=meta[2],
        description=meta[3],
        classes=list(classes),
        n_compared=len(compared),
        n_excluded=len(excluded),
        excluded_reasons=_count_reasons(excluded),
        predicted_distribution=dict(Counter(predicted)),
        actual_distribution=dict(Counter(actuals)),
        accuracy=readings["accuracy"],
        precision=readings["precision"],
        recall=readings["recall"],
        f1=readings["f1"],
        roc_auc=readings["roc_auc"],
        pr_auc=readings["pr_auc"],
        per_class=per_class,
        confusion_matrix=matrix,
        notes=notes,
        definition_id=definition_id,
        definition_name=definition_name,
        definition_version=definition_version,
    )


def _empty_classification_block(*, key: str) -> ClassificationComparison:
    meta = _COMPARISON_META[key]
    return ClassificationComparison(
        key=key,
        title=meta[2],
        description=meta[3],
        classes=[],
        n_compared=0,
        n_excluded=0,
        excluded_reasons=[],
        predicted_distribution={},
        actual_distribution={},
        accuracy=not_computed(NO_COMPARABLE_EVENTS),
        precision=not_computed(NO_COMPARABLE_EVENTS),
        recall=not_computed(NO_COMPARABLE_EVENTS),
        f1=not_computed(NO_COMPARABLE_EVENTS),
        roc_auc=not_computed(NO_COMPARABLE_EVENTS),
        pr_auc=not_computed(NO_COMPARABLE_EVENTS),
        per_class=[],
        confusion_matrix=None,
        notes=[
            (
                "A class never predicted has no precision and a class never "
                "observed has no recall; those per-class figures are reported as "
                "not computed rather than as zero."
            )
        ],
    )


class ValidationService:
    """Build the predicted-vs-actual report from recorded outcomes."""

    def __init__(self, database: Any) -> None:
        self._database = database

    @property
    def _outcome_repository(self) -> MongoRepository[ActualOutcome]:
        return MongoRepository(self._database, ACTUAL_OUTCOMES, ActualOutcome)

    @property
    def _recommendation_repository(self) -> MongoRepository[Recommendation]:
        return MongoRepository(self._database, RECOMMENDATIONS, Recommendation)

    @property
    def _decision_repository(self) -> MongoRepository[OperatorDecision]:
        return MongoRepository(self._database, OPERATOR_DECISIONS, OperatorDecision)

    @property
    def _prediction_repository(self) -> MongoRepository[MLPrediction]:
        return MongoRepository(self._database, ML_PREDICTIONS, MLPrediction)

    @property
    def _definition_repository(self) -> MongoRepository[RiskDefinition]:
        return MongoRepository(self._database, RISK_DEFINITIONS, RiskDefinition)

    @property
    def _pan_repository(self) -> MongoRepository[SaltPan]:
        return MongoRepository(self._database, SALT_PANS, SaltPan)

    async def registry_counts(self) -> dict[str, int]:
        """Totals for the module status endpoint."""
        return {
            "total_outcomes": await self._outcome_repository.count(),
            "comparable_outcomes": await self._outcome_repository.count(
                {"simulation_error.signed_error": {"$ne": None}}
            ),
            "forecast_errors": await self._outcome_repository.count(
                {"rainfall_forecast_error.signed_error": {"$ne": None}}
            ),
            "risk_predictions": await self._prediction_repository.count(),
            "operator_decisions": await self._decision_repository.count(),
        }

    async def _load_outcomes(
        self, pan_id: str | None, limit: int
    ) -> list[ActualOutcome]:
        query: dict[str, Any] = {}
        if pan_id:
            query["pan_id"] = pan_id.lower()
        return await self._outcome_repository.find(
            query, sort=[("timestamp", -1)], limit=limit
        )

    async def _classification_context(
        self, outcomes: list[ActualOutcome]
    ) -> _ClassificationContext:
        recommendations: dict[str, Recommendation] = {}
        decisions: dict[str, OperatorDecision] = {}
        prediction_ids: set[str] = set()
        pan_ids: set[str] = set()

        for outcome in outcomes:
            if outcome.recommendation_link is not None and outcome.recommendation_link.record_id:
                recommendations.setdefault(outcome.recommendation_link.record_id, None)
            if outcome.decision_link is not None and outcome.decision_link.record_id:
                decisions.setdefault(outcome.decision_link.record_id, None)
            pan_ids.add(outcome.pan_id)

        if recommendations:
            loaded = await self._recommendation_repository.find(
                {"recommendation_id": {"$in": list(recommendations)}},
                limit=1000,
            )
            recommendations.update({rec.recommendation_id: rec for rec in loaded})
        if decisions:
            loaded = await self._decision_repository.find(
                {"decision_id": {"$in": list(decisions)}}, limit=1000
            )
            decisions.update({dec.decision_id: dec for dec in loaded})
        if pan_ids:
            pans = await self._pan_repository.find(
                {"pan_id": {"$in": sorted(pan_ids)}}, limit=1000
            )
        else:
            pans = []
        pans_by_id = {pan.pan_id: pan for pan in pans}

        predictions: dict[str, MLPrediction] = {}
        for recommendation in recommendations.values():
            if recommendation is not None and recommendation.ml is not None:
                prediction_ids.add(recommendation.ml.prediction_id)
        if prediction_ids:
            loaded = await self._prediction_repository.find(
                {"prediction_id": {"$in": sorted(prediction_ids)}}, limit=1000
            )
            predictions.update({pred.prediction_id: pred for pred in loaded})

        definition_keys: set[tuple[str, int]] = set()
        for prediction in predictions.values():
            provenance = prediction.input_snapshot or {}
            definition_id = provenance.get("risk_definition_id")
            definition_version = provenance.get("risk_definition_version")
            if definition_id and isinstance(definition_version, int):
                definition_keys.add((definition_id, definition_version))
        definitions: dict[tuple[str, int], RiskDefinition] = {}
        for definition_id, version in sorted(definition_keys):
            found = await self._definition_repository.find(
                {"definition_id": definition_id, "version": version},
                sort=[("created_at", 1)],
                limit=1,
            )
            if found:
                definitions[(definition_id, version)] = found[0]

        return _ClassificationContext(
            recommendations=recommendations,
            decisions=decisions,
            predictions=predictions,
            definitions=definitions,
            pans=pans_by_id,
        )

    # -- continuous comparisons ---------------------------------------------

    def _continuous_rows(
        self, key: str, outcomes: list[ActualOutcome]
    ) -> list[ValidationPairRow]:
        rows: list[ValidationPairRow] = []
        for outcome in outcomes:
            if key == "forecast_vs_actual_rainfall":
                predicted, actual, reason = _rainfall_pair(outcome)
            elif key == "simulated_vs_actual_salinity":
                predicted, actual, reason = _salinity_pair(
                    outcome, outcome.simulation_error
                )
            else:
                predicted, actual, reason = _salinity_pair(
                    outcome, outcome.forecast_simulation_error
                )
            compared = predicted is not None and actual is not None
            rows.append(
                ValidationPairRow(
                    outcome_id=outcome.outcome_id,
                    pan_id=outcome.pan_id,
                    event_id=outcome.event_id,
                    timestamp=outcome.timestamp,
                    compared=compared,
                    reason=None if compared else reason,
                    predicted_number=predicted,
                    actual_number=actual,
                    signed_error=(
                        predicted - actual if compared and predicted is not None and actual is not None else None
                    ),
                )
            )
        return rows

    # -- the risk comparison -------------------------------------------------

    def _risk_rows(
        self, outcomes: list[ActualOutcome], context: _ClassificationContext
    ) -> list[ValidationPairRow]:
        rows: list[ValidationPairRow] = []
        for outcome in outcomes:
            row = self._risk_row(outcome, context)
            rows.append(row)
        return rows

    def _risk_row(
        self, outcome: ActualOutcome, context: _ClassificationContext
    ) -> ValidationPairRow:
        base = ValidationPairRow(
            outcome_id=outcome.outcome_id,
            pan_id=outcome.pan_id,
            event_id=outcome.event_id,
            timestamp=outcome.timestamp,
            compared=False,
        )
        link = outcome.recommendation_link
        if link is None or not link.record_id:
            base.reason = NO_RECOMMENDATION_IN_FORCE
            return base
        recommendation = context.recommendations.get(link.record_id)
        if recommendation is None:
            base.reason = RECOMMENDATION_UNREAD
            return base
        if recommendation.ml is None:
            base.reason = PHYSICS_AND_RULES_NO_MODEL
            return base
        summary = recommendation.ml
        prediction = context.predictions.get(summary.prediction_id)
        provenance = prediction.input_snapshot if prediction is not None else {}
        definition_id = provenance.get("risk_definition_id")
        definition_version = provenance.get("risk_definition_version")
        if prediction is None:
            base.reason = PREDICTION_UNREAD
            return base
        if not definition_id or not isinstance(definition_version, int):
            base.reason = PREDICTION_WITHOUT_DEFINITION
            return base
        definition = context.definitions.get((definition_id, definition_version))
        if definition is None:
            base.reason = DEFINITION_UNREAD
            return base
        base.risk_definition_id = definition.definition_id
        base.risk_definition_name = definition.name
        base.risk_definition_version = definition.version
        if summary.predicted_class is None:
            base.reason = PREDICTED_CLASS_OMITTED
            return base
        evaluation = evaluate_definition(
            definition,
            _outcome_readings(outcome, context.pans.get(outcome.pan_id)),
        )
        if not evaluation.is_labelled:
            base.reason = evaluation.unlabelled_reason or (
                "The risk rule could not be evaluated against what actually "
                "happened, so no actual label exists to grade the prediction."
            )
            return base
        if summary.predicted_class not in definition.class_names:
            base.reason = PREDICTED_CLASS_OUTSIDE_VOCABULARY
            return base
        base.compared = True
        base.predicted_class = summary.predicted_class
        base.actual_class = evaluation.label
        base.probability = summary.probability
        return base

    def _risk_block(
        self, outcomes: list[ActualOutcome], context: _ClassificationContext
    ) -> ClassificationComparison:
        rows = self._risk_rows(outcomes, context)
        compared = [row for row in rows if row.compared]
        if not compared:
            block = _empty_classification_block(key="predicted_risk_vs_outcome")
            block.n_excluded = len(rows)
            block.excluded_reasons = _count_reasons(rows)
            return block
        by_definition: Counter[tuple[str, int]] = Counter(
            (row.risk_definition_id, row.risk_definition_version)
            for row in compared
        )
        headline_key = max(
            by_definition, key=lambda item: (by_definition[item], item)
        )
        headline = context.definitions[headline_key]
        kept: list[ValidationPairRow] = []
        regrouped: list[ValidationPairRow] = []
        for row in compared:
            key = (row.risk_definition_id, row.risk_definition_version)
            if key == headline_key:
                kept.append(row)
            else:
                row.compared = False
                row.reason = (
                    f"Predicted under risk definition {row.risk_definition_name!r} "
                    f"(version {row.risk_definition_version}), which is not the "
                    f"headline rule {headline.name!r} (version {headline.version}); "
                    f"{DEFAULT_DIFFERENT_DEFINITION}"
                )
                regrouped.append(row)
        block = _classification_block(
            key="predicted_risk_vs_outcome",
            rows=kept,
            classes=list(headline.class_names),
            positive_class=list(headline.class_names)[-1],
            definition_id=headline.definition_id,
            definition_name=headline.name,
            definition_version=headline.version,
        )
        block.n_excluded = len(rows) - len(kept)
        block.excluded_reasons = _count_reasons(
            [row for row in rows if not row.compared]
        )
        if regrouped:
            names = sorted({row.reason for row in regrouped})
            block.notes.append(
                "; ".join(
                    f"{sum(1 for r in regrouped if r.reason == name)} comparable "
                    f"event(s) {name}"
                    for name in names
                )
            )
        return block

    # -- the action comparison -----------------------------------------------

    def _action_rows(
        self, outcomes: list[ActualOutcome], context: _ClassificationContext
    ) -> list[ValidationPairRow]:
        rows: list[ValidationPairRow] = []
        for outcome in outcomes:
            base = ValidationPairRow(
                outcome_id=outcome.outcome_id,
                pan_id=outcome.pan_id,
                event_id=outcome.event_id,
                timestamp=outcome.timestamp,
                compared=False,
            )
            link = outcome.recommendation_link
            if link is None or not link.record_id:
                base.reason = NO_RECOMMENDATION_IN_FORCE
                rows.append(base)
                continue
            recommendation = context.recommendations.get(link.record_id)
            if recommendation is None:
                base.reason = RECOMMENDATION_UNREAD
                rows.append(base)
                continue
            if recommendation.action is None:
                base.reason = RECOMMENDATION_CARRIES_NO_ACTION
                rows.append(base)
                continue
            decision_link = outcome.decision_link
            if decision_link is None or not decision_link.record_id:
                base.reason = NO_OPERATOR_DECISION
                rows.append(base)
                continue
            decision = context.decisions.get(decision_link.record_id)
            if decision is None:
                base.reason = DECISION_UNREAD
                rows.append(base)
                continue
            if decision.actual_action is None:
                base.reason = DECISION_WITHOUT_ACTION
                rows.append(base)
                continue
            base.compared = True
            base.predicted_class = recommendation.action.value
            base.actual_class = decision.actual_action.value
            rows.append(base)
        return rows

    # -- overview and drill-down --------------------------------------------

    async def overview(
        self, *, pan_id: str | None = None, limit: int = 1000
    ) -> ValidationOverview:
        outcomes = await self._load_outcomes(pan_id, limit)
        context = await self._classification_context(outcomes)
        total = len(outcomes)

        rainfall_rows = self._continuous_rows("forecast_vs_actual_rainfall", outcomes)
        simulated_rows = self._continuous_rows("simulated_vs_actual_salinity", outcomes)
        forecast_simulated_rows = self._continuous_rows(
            "forecast_simulated_vs_actual_salinity", outcomes
        )
        action_rows = self._action_rows(outcomes, context)

        return ValidationOverview(
            scope=ValidationScope(
                pan_id=(pan_id.lower() if pan_id else None),
                total_outcomes=total,
            ),
            forecast_vs_actual_rainfall=_continuous_block(
                key="forecast_vs_actual_rainfall",
                rows=rainfall_rows,
            ),
            simulated_vs_actual_salinity=_continuous_block(
                key="simulated_vs_actual_salinity",
                rows=simulated_rows,
            ),
            forecast_simulated_vs_actual_salinity=_continuous_block(
                key="forecast_simulated_vs_actual_salinity",
                rows=forecast_simulated_rows,
            ),
            predicted_risk_vs_outcome=self._risk_block(outcomes, context),
            recommended_vs_operator_action=_classification_block(
                key="recommended_vs_operator_action",
                rows=action_rows,
                classes=list(DECISION_ACTIONS),
                positive_class=None,
            ),
        )

    async def pairs(
        self,
        *,
        comparison: str,
        pan_id: str | None = None,
        limit: int = 1000,
    ) -> ValidationPairsResponse:
        outcomes = await self._load_outcomes(pan_id, limit)
        kind, unit, _, _ = _COMPARISON_META[comparison]
        if kind == COMPARISON_KIND_CONTINUOUS:
            rows = self._continuous_rows(comparison, outcomes)
        elif comparison == "predicted_risk_vs_outcome":
            context = await self._classification_context(outcomes)
            rows = self._risk_rows(outcomes, context)
        else:
            context = await self._classification_context(outcomes)
            rows = self._action_rows(outcomes, context)
        return ValidationPairsResponse(
            comparison=comparison,
            kind=kind,
            unit=unit,
            pan_id=(pan_id.lower() if pan_id else None),
            total_outcomes=len(outcomes),
            items=rows,
        )

    async def export(
        self,
        *,
        comparison: str,
        output_format: str,
        pan_id: str | None = None,
        limit: int = 1000,
    ) -> tuple[str, str, str]:
        """Return (media_type, body, filename) for a downloadable pair view."""
        response = await self.pairs(
            comparison=comparison, pan_id=pan_id, limit=limit
        )
        records = [row.model_dump(mode="json") for row in response.items]
        filename = f"validation-{comparison}"
        if output_format == "csv":
            columns = (
                CONTINUOUS_EXPORT_COLUMNS
                if response.kind == COMPARISON_KIND_CONTINUOUS
                else CLASSIFICATION_EXPORT_COLUMNS
            )
            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer, fieldnames=list(columns), extrasaction="ignore"
            )
            writer.writeheader()
            writer.writerows(
                {
                    key: sanitize_prose_cell(value)
                    if key in ("reason", "risk_definition_name")
                    else value
                    for key, value in record.items()
                }
                for record in records
            )
            return "text/csv; charset=utf-8", buffer.getvalue(), f"{filename}.csv"
        body = json.dumps(records, default=str, indent=2)
        return "application/json", body, f"{filename}.json"


__all__ = [
    "DECISION_ACTIONS",
    "ValidationService",
]