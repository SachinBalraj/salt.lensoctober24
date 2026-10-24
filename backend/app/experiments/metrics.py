"""Phase 6 - validation metrics, computed from real observations only.

Four figures, on the definitions the study specifies:

.. code-block:: text

    AE    = |observed - predicted|
    MAE   = mean(|observed - predicted|)
    RMSE  = sqrt(mean((observed - predicted)^2))
    Bias  = mean(predicted - observed)

``Bias`` is signed and positive means the prediction ran high. That sign is the
reason it is the most informative of the four on a small set: with one event, MAE
and RMSE both collapse to the same number and say nothing about direction, while
bias does.

Two rules decide which events may enter an aggregate, and both are enforced here
rather than left to the caller.

**Only clean events.** An intervention-affected event has an unquantifiable
intervention contribution; an incomplete event has unquantifiable missing
evidence. Either would put a confounded number in a denominator and label it
simulator error. The excluded events are counted with their reasons, never
dropped silently — a denominator that quietly shrinks is how a bad result becomes
a good-looking one.

**Only genuine measurements.** Every pair must carry a non-null predicted figure
*and* a non-null observed figure. A missing observation is excluded, never
treated as an error of zero.

And when the surviving set does not clear :data:`MIN_CLEAN_EVENTS_FOR_VALIDATION`
on both count and pan diversity, the answer is
:data:`INSUFFICIENT_VALIDATION_MESSAGE` and **no number at all** — every reading
comes back ``computed=False`` with that reason. Reporting an MAE over one event
is not a weak result, it is a wrong one: MAE, RMSE and bias over n=1 are all
functions of a single absolute deviation, and quoting them lends the authority of
an aggregate to one measurement.

**Which is the state this project is in.** The registry holds one event, and it
classifies as ``INCOMPLETE_EVIDENCE_EVENT``. Every metric below therefore returns
``computed=False``. That is the correct answer, not a missing feature.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from pydantic import Field

from app.experiments.classifier import ClassificationResult, classify_event
from app.experiments.models import ExperimentEvent
from app.models.evaluation import MetricReading as EvaluationMetricReading

#: Returned when the evidence cannot support a validation figure. Fixed, not
#: assembled: a caller should be able to match on it, and a message whose wording
#: changed with the count would break that.
INSUFFICIENT_VALIDATION_MESSAGE = "Insufficient independently verified events"

#: Fewest clean, independently verified events before any figure is reported.
#:
#: Deliberately the smallest number that is not one. At n=1 the three aggregate
#: statistics are three names for the same absolute deviation. Even at the
#: threshold the result is a case series over a handful of events on a small
#: number of pans -- it is not an inferential validation and must not be described
#: as one. The threshold exists to stop single measurements being quoted as
#: aggregates, not to license a statistical claim.
MIN_CLEAN_EVENTS_FOR_VALIDATION = 3

#: Fewest distinct pans among those events.
#:
#: A pan is the unit at which conditions are similar, so three events on one pan
#: are one measurement taken three times. Two is the floor at which "this also
#: held on another pan" becomes a statement about the system rather than about a
#: single site. Matches the reasoning behind ``MIN_DISTINCT_PANS`` in
#: ``app.ml.sufficiency``, applied to events rather than rows.
MIN_DISTINCT_PANS_FOR_VALIDATION = 2


class MetricReading(EvaluationMetricReading):
    """One metric, and whether it exists.

    Subclasses :class:`app.models.evaluation.MetricReading` rather than restating
    it, so the invariants come from the one validator in the system: a reading
    marked computed must carry a finite value, a reading not computed must carry
    no value and must say why. A second definition of the same discipline is a
    second set of invariants to keep in step.

    Adds ``unit`` and ``n``. The unit because a bare number labelled only "MAE"
    cannot be checked, and the count because every figure computed here is a mean
    over a named number of pairs, and a mean without its denominator is the error
    this project keeps having to catch elsewhere.
    """

    unit: str | None = Field(
        default=None, description="The unit of ``value``. Null when no value exists."
    )
    n: int = Field(
        default=0,
        ge=0,
        description=(
            "How many pairs the figure was computed over. Zero when nothing was "
            "computed, so an absent figure cannot look like a mean over nothing."
        ),
    )

    def as_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "computed": self.computed,
            "reason": self.reason,
            "unit": self.unit,
            "n": self.n,
        }


@dataclass(frozen=True)
class QuantityReport:
    """Every figure for one measured quantity, over one set of events.

    ``absolute_errors`` is kept alongside the aggregates so a reader can
    recompute MAE and RMSE from the raw deviations rather than trusting them.
    """

    quantity: str
    unit: str
    events_considered: int
    pairs_compared: int
    excluded: int
    exclusion_reasons: dict[str, int]
    absolute_errors: tuple[float, ...]
    mean_absolute_error: MetricReading
    root_mean_squared_error: MetricReading
    mean_bias: MetricReading

    def as_dict(self) -> dict[str, object]:
        return {
            "quantity": self.quantity,
            "unit": self.unit,
            "events_considered": self.events_considered,
            "pairs_compared": self.pairs_compared,
            "excluded": self.excluded,
            "exclusion_reasons": dict(self.exclusion_reasons),
            "absolute_errors": list(self.absolute_errors),
            "absolute_error_mean": self.mean_absolute_error.as_dict(),
            "mae": self.mean_absolute_error.as_dict(),
            "rmse": self.root_mean_squared_error.as_dict(),
            "bias": self.mean_bias.as_dict(),
        }


@dataclass(frozen=True)
class ValidationReport:
    """The whole Phase 6 result: sufficiency, counts, and both quantities."""

    sufficient: bool
    message: str | None
    reasons: tuple[str, ...]
    clean_events: int
    intervention_affected_events: int
    incomplete_events: int
    independently_verified_events: int
    total_events: int
    distinct_pans: tuple[str, ...]
    min_clean_events_required: int = MIN_CLEAN_EVENTS_FOR_VALIDATION
    min_distinct_pans_required: int = MIN_DISTINCT_PANS_FOR_VALIDATION
    salinity: QuantityReport | None = None
    depth: QuantityReport | None = None
    per_event: tuple[dict[str, object], ...] = ()
    classifications: tuple[ClassificationResult, ...] = field(default=())

    def as_dict(self) -> dict[str, object]:
        return {
            "sufficient": self.sufficient,
            "message": self.message,
            "reasons": list(self.reasons),
            "total_events": self.total_events,
            "clean_rainfall_events": self.clean_events,
            "intervention_affected_events": self.intervention_affected_events,
            "incomplete_evidence_events": self.incomplete_events,
            "independently_verified_events": self.independently_verified_events,
            "distinct_pans": list(self.distinct_pans),
            "thresholds": {
                "min_clean_events": self.min_clean_events_required,
                "min_distinct_pans": self.min_distinct_pans_required,
            },
            "salinity": self.salinity.as_dict() if self.salinity else None,
            "depth": self.depth.as_dict() if self.depth else None,
            "per_event": [dict(row) for row in self.per_event],
            "classifications": [item.as_dict() for item in self.classifications],
        }


def absolute_error(observed: float, predicted: float) -> float:
    """``AE = |observed - predicted|``."""
    return abs(observed - predicted)


def mean_absolute_error(pairs: Sequence[tuple[float, float]]) -> float | None:
    """``MAE = mean(|observed - predicted|)``, or ``None`` for an empty set."""
    if not pairs:
        return None
    return sum(absolute_error(observed, predicted) for observed, predicted in pairs) / len(pairs)


def root_mean_squared_error(pairs: Sequence[tuple[float, float]]) -> float | None:
    """``RMSE = sqrt(mean((observed - predicted)^2))``, or ``None`` when empty."""
    if not pairs:
        return None
    squared = sum((observed - predicted) ** 2 for observed, predicted in pairs)
    return math.sqrt(squared / len(pairs))


def mean_bias(pairs: Sequence[tuple[float, float]]) -> float | None:
    """``Bias = mean(predicted - observed)``, or ``None`` when empty.

    Signed: positive means the prediction ran high, i.e. the pan came out less
    salty than predicted. Computed on signed differences, never on absolute ones
    -- averaging absolute errors and calling the result a bias is a common and
    silent way to lose the only directional information in the set.
    """
    if not pairs:
        return None
    return sum(predicted - observed for observed, predicted in pairs) / len(pairs)


def _withheld(unit: str, n: int, reason: str) -> MetricReading:
    return MetricReading(value=None, computed=False, reason=reason, unit=unit, n=n)


def _quantity_report(
    *,
    quantity: str,
    unit: str,
    events: Sequence[ExperimentEvent],
    classifications: Sequence[ClassificationResult],
    predicted_field: str,
    observed_field: str,
    sufficient: bool,
    insufficiency_reason: str | None,
) -> QuantityReport:
    """Every figure for one quantity over the qualifying events.

    ``events`` must already be restricted to clean, verified events; the
    restriction is not repeated here so that the count of what was considered and
    the count of what was excluded cannot drift apart between the two.
    """
    pairs: list[tuple[float, float]] = []
    errors: list[float] = []
    excluded = 0
    reasons: dict[str, int] = {}

    for event in events:
        predicted = getattr(event, predicted_field)
        observed = getattr(event, observed_field)
        if predicted is None or observed is None:
            excluded += 1
            missing = (
                f"{quantity}: no predicted figure was recorded"
                if predicted is None
                else f"{quantity}: no observed figure was recorded"
            )
            reasons[missing] = reasons.get(missing, 0) + 1
            continue
        pairs.append((float(observed), float(predicted)))
        errors.append(absolute_error(float(observed), float(predicted)))

    n = len(pairs)
    if not sufficient:
        reason = insufficiency_reason or INSUFFICIENT_VALIDATION_MESSAGE
        return QuantityReport(
            quantity=quantity,
            unit=unit,
            events_considered=len(events),
            pairs_compared=n,
            excluded=excluded,
            exclusion_reasons=reasons,
            absolute_errors=tuple(errors),
            mean_absolute_error=_withheld(unit, n, reason),
            root_mean_squared_error=_withheld(unit, n, reason),
            mean_bias=_withheld(unit, n, reason),
        )

    if n == 0:
        # The sufficiency gate counts qualifying events, and an event can qualify
        # while carrying no observed figure for this particular quantity. Clearing
        # the gate therefore does not by itself make this quantity computable, and
        # the reason for that is the missing pair rather than the gate.
        reason = f"No observed {quantity} figure is available on any qualifying event."
        return QuantityReport(
            quantity=quantity,
            unit=unit,
            events_considered=len(events),
            pairs_compared=0,
            excluded=excluded,
            exclusion_reasons=reasons,
            absolute_errors=(),
            mean_absolute_error=_withheld(unit, 0, reason),
            root_mean_squared_error=_withheld(unit, 0, reason),
            mean_bias=_withheld(unit, 0, reason),
        )

    return QuantityReport(
        quantity=quantity,
        unit=unit,
        events_considered=len(events),
        pairs_compared=n,
        excluded=excluded,
        exclusion_reasons=reasons,
        absolute_errors=tuple(errors),
        mean_absolute_error=MetricReading(
            value=mean_absolute_error(pairs), computed=True, reason=None, unit=unit, n=n
        ),
        root_mean_squared_error=MetricReading(
            value=root_mean_squared_error(pairs), computed=True, reason=None, unit=unit, n=n
        ),
        mean_bias=MetricReading(
            value=mean_bias(pairs), computed=True, reason=None, unit=unit, n=n
        ),
    )


def validate_events(events: Iterable[ExperimentEvent]) -> ValidationReport:
    """The full Phase 6 report over a registry of real events.

    Returns a report in every case. When the evidence is insufficient the report
    says so in :attr:`ValidationReport.message` and withholds every number; it
    does not return ``None``, because "there is no report" and "the report says
    the data cannot support one" are different things and a caller must be able
    to tell them apart.
    """
    recorded = list(events)
    classifications = [classify_event(event) for event in recorded]

    clean = [
        event
        for event, result in zip(recorded, classifications)
        if result.is_verified
    ]
    intervention_affected = sum(
        1 for result in classifications if result.intervention_affected
    )
    incomplete = sum(
        1
        for result in classifications
        if result.category.value == "INCOMPLETE_EVIDENCE_EVENT"
    )
    clean_count = sum(
        1 for result in classifications if result.category.value == "CLEAN_RAINFALL_EVENT"
    )
    distinct_pans = tuple(sorted({event.pan_id for event in clean}))

    reasons: list[str] = []
    if len(clean) < MIN_CLEAN_EVENTS_FOR_VALIDATION:
        reasons.append(
            f"{len(clean)} independently verified clean rainfall event(s); at "
            f"least {MIN_CLEAN_EVENTS_FOR_VALIDATION} are needed before MAE, "
            "RMSE and bias describe anything other than individual deviations."
        )
    if len(distinct_pans) < MIN_DISTINCT_PANS_FOR_VALIDATION:
        reasons.append(
            f"{len(distinct_pans)} distinct pan(s) among those events; at least "
            f"{MIN_DISTINCT_PANS_FOR_VALIDATION} are needed so the result is "
            "not one site measured repeatedly."
        )
    sufficient = not reasons
    insufficiency_reason = (
        None if sufficient else INSUFFICIENT_VALIDATION_MESSAGE + "."
    )

    salinity = _quantity_report(
        quantity="salinity",
        unit="g/L",
        events=clean,
        classifications=classifications,
        predicted_field="predicted_salinity_g_l",
        observed_field="observed_salinity_g_l",
        sufficient=sufficient,
        insufficiency_reason=insufficiency_reason,
    )
    depth = _quantity_report(
        quantity="depth",
        unit="cm",
        events=clean,
        classifications=classifications,
        predicted_field="predicted_depth_cm",
        observed_field="observed_depth_cm",
        sufficient=sufficient,
        insufficiency_reason=insufficiency_reason,
    )

    per_event = [
        _per_event_row(event, result) for event, result in zip(recorded, classifications)
    ]

    return ValidationReport(
        sufficient=sufficient,
        message=None if sufficient else INSUFFICIENT_VALIDATION_MESSAGE,
        reasons=tuple(reasons),
        clean_events=clean_count,
        intervention_affected_events=intervention_affected,
        incomplete_events=incomplete,
        independently_verified_events=len(clean),
        total_events=len(recorded),
        distinct_pans=distinct_pans,
        salinity=salinity,
        depth=depth,
        per_event=tuple(per_event),
        classifications=tuple(classifications),
    )


def _per_event_row(event: ExperimentEvent, result: ClassificationResult) -> dict[str, object]:
    """One row of the per-event table, with the deviations it can support.

    The deviations are computed whenever both figures exist, even for an excluded
    event, because the raw deviation is a fact about the record and hiding it
    would make the exclusion look like a judgement about a number the reader
    cannot see. Whether it may be *averaged* is the classifier's decision, and
    the ``included_in_metrics`` flag says so separately.
    """
    salinity_pair = (
        (event.observed_salinity_g_l, event.predicted_salinity_g_l)
        if event.observed_salinity_g_l is not None
        and event.predicted_salinity_g_l is not None
        else None
    )
    depth_pair = (
        (event.observed_depth_cm, event.predicted_depth_cm)
        if event.observed_depth_cm is not None and event.predicted_depth_cm is not None
        else None
    )
    return {
        "event_id": event.event_id,
        "pan_id": event.pan_id,
        "date": event.date.isoformat(),
        "category": result.category.value,
        "intervention_affected": result.intervention_affected,
        "included_in_metrics": result.is_verified,
        "predicted_salinity_g_l": event.predicted_salinity_g_l,
        "observed_salinity_g_l": event.observed_salinity_g_l,
        "salinity_absolute_error_g_l": absolute_error(*salinity_pair)
        if salinity_pair
        else None,
        "salinity_bias_g_l": salinity_pair[1] - salinity_pair[0] if salinity_pair else None,
        "predicted_depth_cm": event.predicted_depth_cm,
        "observed_depth_cm": event.observed_depth_cm,
        "depth_absolute_error_cm": absolute_error(*depth_pair) if depth_pair else None,
        "depth_bias_cm": depth_pair[1] - depth_pair[0] if depth_pair else None,
        "reasons": list(result.reasons),
    }


__all__ = [
    "INSUFFICIENT_VALIDATION_MESSAGE",
    "MIN_CLEAN_EVENTS_FOR_VALIDATION",
    "MIN_DISTINCT_PANS_FOR_VALIDATION",
    "MetricReading",
    "QuantityReport",
    "ValidationReport",
    "absolute_error",
    "mean_absolute_error",
    "mean_bias",
    "root_mean_squared_error",
    "validate_events",
]
