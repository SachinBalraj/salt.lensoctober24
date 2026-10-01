"""Module 8 - whether a dataset's rows are evidence of anything in the field.

Module 08's sufficiency gate (:mod:`app.ml.sufficiency`) asks whether there is
*enough* real data to train on. It deliberately does not ask whether the data is
*real*, because by the time rows reach that gate they have already been built
from a stored dataset, and the builder is where provenance is decided.

This module closes that gap. Without it, the demonstration pan and the
demonstration outcome sitting in the development database are a trainable
dataset: they satisfy every count in the sufficiency gate, and a model fitted to
them reports a held-out F1 that is arithmetically correct and scientifically
meaningless. Nothing downstream could tell the difference, because a metric does
not carry its own provenance.

**The distinction this module draws is not between "simulated" and "measured".**
It is between a number that describes this salt pan in the field and a number that
describes four rows someone typed to check that a button works. The second is not
a worse measurement; it is not a measurement of the world at all, and the only
honest thing to do with it is label it.

**Nothing here blocks training.** Fitting a model against fixture data is how
this pipeline is tested, and forbidding it would mean the training code could
only ever run against data that does not exist yet. What this module does is
*record* the class on the model version and hand the evaluation layer a refusal
for any claim of real-world performance. A model fitted to demonstration data is
a legitimate thing to have; a model fitted to demonstration data that reports
its score as a real-world result is not, and that is the specific failure this
exists to prevent.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: The value every demonstration flag in this project writes. It is a member of
#: the data-source enums rather than a separate column, so that a reader of any
#: single record can tell what it is without a second lookup.
DEMONSTRATION = "demonstration"

#: The sentence returned when a score is asked for as real-world performance and
#: the model was not fitted on evidence that could support one. Fixed rather than
#: assembled, for the same reason :data:`app.ml.sufficiency.INSUFFICIENT_DATA_MESSAGE`
#: is: callers and tests match on it, and an assembled string drifts.
EVIDENCE_NOT_GENUINE_MESSAGE = (
    "This model's held-out scores were not computed on genuine field evidence, "
    "so they are not a measurement of real-world performance."
)


class EvidenceClass(StrEnum):
    """What kind of world the training rows describe.

    Only :attr:`GENUINE` may be quoted as a real-world result, and
    :attr:`UNKNOWN` is treated as non-evidential on purpose: an unclassifiable row
    is not evidence of anything, and assuming it is genuine would make this module
    fail open.
    """

    #: Every row traces to a measured record of this pan.
    GENUINE = "genuine"
    #: Every row traces to a demonstration or otherwise non-measured record.
    DEMONSTRATION = "demonstration"
    #: Some rows are genuine and some are not. The worst class present wins,
    #: because one genuine row among demonstration rows does not make the model
    #: a description of the field.
    MIXED = "mixed"
    #: At least one row could not be traced to any provenance at all.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EvidenceVerdict:
    """The classification, with the counts behind it.

    Carries its own counts because "not evidential" is only actionable if the
    reader can see *which* rows failed and how many. A bare enum gives them a
    category and no way to start fixing it.
    """

    evidence_class: EvidenceClass
    genuine_rows: int = 0
    demonstration_rows: int = 0
    unverified_rows: int = 0
    reasons: tuple[str, ...] = ()

    @property
    def is_evidential(self) -> bool:
        """Whether these rows can support a real-world performance claim."""
        return self.evidence_class is EvidenceClass.GENUINE

    @property
    def total_rows(self) -> int:
        return self.genuine_rows + self.demonstration_rows + self.unverified_rows

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_class": self.evidence_class.value,
            "is_evidential": self.is_evidential,
            "genuine_rows": self.genuine_rows,
            "demonstration_rows": self.demonstration_rows,
            "unverified_rows": self.unverified_rows,
            "total_rows": self.total_rows,
            "reasons": list(self.reasons),
        }

    def refusal_reason(self) -> str | None:
        """The sentence to refuse on, or ``None`` when the rows are genuine."""
        if self.is_evidential:
            return None
        counts = (
            f"{self.genuine_rows} genuine, {self.demonstration_rows} demonstration, "
            f"{self.unverified_rows} untraceable"
        )
        return (
            f"{EVIDENCE_NOT_GENUINE_MESSAGE} The training rows are classified "
            f"{self.evidence_class.value!r} ({counts} of {self.total_rows} rows)."
        )


def _row_source(row: Any, attribute: str) -> str | None:
    """A row's provenance attribute, read without assuming the row's type.

    Duck-typed on purpose: these rows are ``DatasetRow`` in production and small
    stand-ins in tests, and a check that only works for one of them is a check
    that is not exercised where it is written.
    """
    value = getattr(row, attribute, None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _row_provenance(row: Any, demonstration_targets: frozenset[str]) -> tuple[EvidenceClass, list[str]]:
    """Classify one row, and say why.

    Returns the class directly rather than a list of sentences for the caller to
    interpret. The first version of this inferred the class by searching the
    messages for the word "demonstration", and the message for a typed
    observation says "demonstrate" -- so every demonstration row was silently
    filed as untraceable instead. Prose is for the reader; the branch is decided
    by the value, and never by matching words against it.
    """
    reasons: list[str] = []
    is_demonstration = False
    is_untraceable = False

    observation = _row_source(row, "observation_data_source")
    if observation is None:
        is_untraceable = True
        reasons.append("the row records no observation data source")
    elif observation == DEMONSTRATION:
        is_demonstration = True
        reasons.append(
            "its observation was entered to demonstrate the system, not measured"
        )

    weather = _row_source(row, "weather_source")
    if weather == DEMONSTRATION:
        is_demonstration = True
        reasons.append("its weather record was a demonstration forecast")
    # An absent weather source is deliberately not disqualifying. This gate
    # answers "was any of this typed in to show the system working", and a field
    # nobody filled in is silence rather than a typed row. Treating a missing
    # optional field as fabrication would refuse almost every real dataset and
    # make the gate indistinguishable from a completeness check, which is a
    # different question with a different fix.

    target_id = _row_source(row, "target_source_id")
    if target_id is not None and target_id in demonstration_targets:
        is_demonstration = True
        reasons.append("its label came from a demonstration outcome")

    if is_demonstration:
        return EvidenceClass.DEMONSTRATION, reasons
    if is_untraceable:
        return EvidenceClass.UNKNOWN, reasons
    return EvidenceClass.GENUINE, reasons


def classify_evidence(
    rows: Iterable[Any],
    *,
    demonstration_targets: Iterable[str] = (),
) -> EvidenceVerdict:
    """Classify the evidence behind a set of dataset rows.

    ``demonstration_targets`` is the set of target record ids known to be
    demonstration outcomes. It is passed in rather than looked up, because the
    rows carry the id but not the flag, and this module is a pure function over
    its inputs so that the classification can be tested without a database.

    Severity ordering, applied once at the end so the counts are all reported
    even when the class is decided by the first rule: a single demonstrative row
    among genuine ones yields ``mixed``, because the model has still seen a value
    nobody measured, and ``mixed`` is the class that says "fix the dataset"
    rather than "this is only a test".
    """
    targets = frozenset(str(value) for value in demonstration_targets)

    genuine = 0
    demonstration = 0
    unverified = 0
    examples: list[str] = []

    for row in rows:
        row_class, reasons = _row_provenance(row, targets)
        if row_class is EvidenceClass.GENUINE:
            genuine += 1
        elif row_class is EvidenceClass.DEMONSTRATION:
            demonstration += 1
        else:
            unverified += 1
        if len(examples) < 3:
            row_id = _row_source(row, "row_id") or "a row"
            examples.append(f"{row_id}: {'; '.join(reasons)}")

    total = genuine + demonstration + unverified
    if total == 0:
        return EvidenceVerdict(
            evidence_class=EvidenceClass.UNKNOWN,
            reasons=(
                (
                    "no rows were supplied, so the training data cannot be "
                    "classified as evidence of anything"
                ),
            ),
        )

    if demonstration:
        evidence_class = (
            EvidenceClass.MIXED if genuine else EvidenceClass.DEMONSTRATION
        )
    elif unverified:
        evidence_class = EvidenceClass.UNKNOWN
    else:
        evidence_class = EvidenceClass.GENUINE

    reasons: list[str] = []
    if evidence_class is not EvidenceClass.GENUINE:
        if demonstration:
            reasons.append(
                f"{demonstration} of {total} row(s) trace to demonstration records "
                "rather than measurements of this pan"
            )
        if unverified:
            reasons.append(
                f"{unverified} of {total} row(s) cannot be traced to a provenance "
                "record at all, and are not assumed to be genuine"
            )
        if examples:
            reasons.append("first offending rows -- " + "; ".join(examples))

    return EvidenceVerdict(
        evidence_class=evidence_class,
        genuine_rows=genuine,
        demonstration_rows=demonstration,
        unverified_rows=unverified,
        reasons=tuple(reasons),
    )


def verdict_from_parameters(parameters: dict[str, Any] | None) -> EvidenceVerdict:
    """Rebuild a verdict from what was recorded on a model version.

    Reads back what :func:`classify_evidence` wrote, so the evaluation layer
    judges a registered model by the provenance its training run actually
    recorded rather than by re-deriving it from rows that may since have changed.

    A version that records nothing is ``UNKNOWN`` with an explicit reason, not
    ``GENUINE``. Defaulting to genuine would let a model registered before this
    check existed pass as a field result, which is precisely the quiet upgrade
    that turns an old number into a claim nobody checked.
    """
    stored = (parameters or {}).get("evidence")
    if not isinstance(stored, dict):
        return EvidenceVerdict(
            evidence_class=EvidenceClass.UNKNOWN,
            reasons=(
                (
                    "the model version records no evidence provenance, so whether "
                    "its training rows were genuine cannot be established from the "
                    "registry"
                ),
            ),
        )
    try:
        evidence_class = EvidenceClass(str(stored.get("evidence_class")))
    except ValueError:
        return EvidenceVerdict(
            evidence_class=EvidenceClass.UNKNOWN,
            reasons=(
                "the model version records an unrecognised evidence class "
                f"{stored.get('evidence_class')!r}, so it cannot be treated as "
                "genuine"
            ),
        )
    return EvidenceVerdict(
        evidence_class=evidence_class,
        genuine_rows=int(stored.get("genuine_rows", 0) or 0),
        demonstration_rows=int(stored.get("demonstration_rows", 0) or 0),
        unverified_rows=int(stored.get("unverified_rows", 0) or 0),
        reasons=tuple(str(reason) for reason in stored.get("reasons", ())),
    )


def _outcome_field(outcome: Any, name: str) -> Any:
    """Read one field from a stored outcome, whether it is a model or a mapping.

    Outcomes arrive here as ``ActualOutcome`` models from the repository and as
    plain documents from a query or a test. Reading the flag with ``getattr``
    alone returns ``None`` for a dict, which silently classifies every outcome
    as genuine -- the failure mode this module exists to prevent, reproduced by
    its own helper.
    """
    if isinstance(outcome, dict):
        return outcome.get(name)
    return getattr(outcome, name, None)


def demonstration_target_ids(outcomes: Sequence[Any]) -> frozenset[str]:
    """The ids of the outcomes that are demonstrations.

    Read off whatever the caller has rather than from a database, so the rule
    that identifies a demonstration outcome -- the ``is_demonstration`` flag, set
    by the outcome itself and not inferred here -- stays in the outcome model.
    """
    found: set[str] = set()
    for outcome in outcomes:
        if not _outcome_field(outcome, "is_demonstration"):
            continue
        identifier = _outcome_field(outcome, "outcome_id") or _outcome_field(
            outcome, "id"
        )
        if identifier:
            found.add(str(identifier))
    return frozenset(found)


__all__ = [
    "DEMONSTRATION",
    "EVIDENCE_NOT_GENUINE_MESSAGE",
    "EvidenceClass",
    "EvidenceVerdict",
    "classify_evidence",
    "demonstration_target_ids",
    "verdict_from_parameters",
]
