"""Module 8 - the gate that decides whether there is enough real data to train on.

One rule governs this module: **the pipeline never invents data to make a model
possible.** Not by synthesising rows, not by duplicating the minority class, not
by resampling a handful of observations until a train/test split can be carved
out of it, and not by falling back to a "baseline" model fitted on a target the
data cannot actually support.

Why that rule rather than a softer one. A model fitted on four rows and reported
at 100% accuracy is not a bad model; it is a number that will be quoted, believed,
and used, and it describes the four rows it saw. A failure that refuses to
produce a number is recoverable. A failure that produces a confident wrong one
is not. So the threshold below is deliberately conservative, and when it is not
met the answer is a specific sentence rather than a weak model or a warning.

The gate counts real, observed rows. It runs before any estimator is constructed
and before any preprocessing statistic is computed, so an insufficient dataset
never gets far enough to leave an artifact behind.

The message is fixed, and the reasons are reported alongside it rather than
substituted for it. Callers and tests match on the message, so it is a constant
rather than an assembled string -- an assembled one would drift the first time
someone wanted it to name a number.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

#: The sentence returned when the data cannot support a model. Fixed, because
#: this is what the application is required to say, and a caller that checks for
#: it should not have to parse a message that changes shape.
INSUFFICIENT_DATA_MESSAGE = (
    "Insufficient labelled data for reliable model training."
)

#: Fewest labelled rows overall.
#:
#: Below this, the held-out split is not a sample from anything: with 30 rows a
#: 20% test set is six observations, and a metric computed on six rows is a
#: description of those six rows.
MIN_TOTAL_LABELLED_ROWS = 80

#: Fewest labelled rows in the smaller of the two splits.
#:
#: The smaller split sets the ceiling, so this is the number that actually decides
#: whether the held-out score means anything. It is above the point where a single
#: held-out row changes a score by a large fraction.
MIN_ROWS_PER_SPLIT = 20

#: Fewest distinct classes. Two is the minimum for classification at all; a
#: single-class dataset produces a model that returns one answer forever, and its
#: accuracy will read 1.00.
MIN_CLASSES = 2

#: Fewest observations of the rarest class.
#:
#: A class with one or two examples is not learnable, and macro-averaging will
#: report a number for it that is really reporting the three other classes. The
#: rare class is where the operational cost of a miss lives, so it is the class
#: worth refusing to run for.
MIN_ROWS_PER_CLASS = 5

#: Fewest rows in the training split per class present there.
#:
#: Distinct from ``MIN_ROWS_PER_CLASS``, which is about the dataset. This one is
#: about the fitted model: a class absent from the training rows is a class the
#: model cannot predict, whatever the dataset as a whole contains.
MIN_TRAIN_ROWS_PER_CLASS = 3

#: Fewest distinct pans.
#:
#: A pan is the unit at which conditions are similar, so a dataset drawing all its
#: rows from two pans is two measurements repeated. Three is the floor at which a
#: model can be evaluated on a pan it did not see at all.
MIN_DISTINCT_PANS = 3

#: Fewest usable features. Below this there is nothing to learn a boundary from.
MIN_FEATURES = 2


@dataclass(frozen=True)
class SufficiencyReport:
    """The counts behind the decision, so a refusal can be acted on.

    A refusal that only says "insufficient" sends the reader back to the dataset
    builder with no idea whether the problem is volume, class balance, or pan
    spread. These counts say which.
    """

    sufficient: bool
    message: str | None
    reasons: tuple[str, ...] = ()
    labelled_rows: int = 0
    total_rows: int = 0
    class_counts: dict[str, int] = field(default_factory=dict)
    distinct_pans: int = 0
    feature_count: int = 0
    split_row_counts: dict[str, int] = field(default_factory=dict)
    split_class_counts: dict[str, dict[str, int]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sufficient": self.sufficient,
            "message": self.message,
            "reasons": list(self.reasons),
            "labelled_rows": self.labelled_rows,
            "total_rows": self.total_rows,
            "class_counts": dict(self.class_counts),
            "distinct_pans": self.distinct_pans,
            "feature_count": self.feature_count,
            "split_row_counts": dict(self.split_row_counts),
            "split_class_counts": {
                split: dict(counts)
                for split, counts in self.split_class_counts.items()
            },
        }


def check_sufficiency(
    *,
    labelled_rows: int,
    class_counts: dict[str, int],
    distinct_pans: int,
    feature_count: int,
    total_rows: int | None = None,
    split_row_counts: dict[str, int] | None = None,
    split_class_counts: dict[str, dict[str, int]] | None = None,
) -> SufficiencyReport:
    """Decide whether this data can support a trainable model.

    Checked in dataset terms first, then in split terms. The dataset-level checks
    answer "is there real data here at all"; the split-level ones answer "is there
    enough of it held back to measure honestly". Every failing check is collected
    rather than returning on the first, so one run reports everything that is
    wrong with the data instead of requiring four runs to discover four problems.
    """
    reasons: list[str] = []

    if feature_count < MIN_FEATURES:
        reasons.append(
            f"only {feature_count} usable feature(s); at least {MIN_FEATURES} are "
            "needed to fit a model that is not a constant"
        )

    if labelled_rows < MIN_TOTAL_LABELLED_ROWS:
        reasons.append(
            f"only {labelled_rows} labelled row(s); at least "
            f"{MIN_TOTAL_LABELLED_ROWS} are needed for a held-out score to mean "
            "anything"
        )

    if len(class_counts) < MIN_CLASSES:
        reasons.append(
            f"{len(class_counts)} class(es) present; a classification model needs "
            f"at least {MIN_CLASSES}"
        )

    for name, count in sorted(class_counts.items()):
        if count < MIN_ROWS_PER_CLASS:
            reasons.append(
                f"class {name!r} has {count} labelled row(s); at least "
                f"{MIN_ROWS_PER_CLASS} are needed before the class is learnable"
            )

    if distinct_pans < MIN_DISTINCT_PANS:
        reasons.append(
            f"{distinct_pans} distinct pan(s); at least {MIN_DISTINCT_PANS} are "
            "needed so a model can be evaluated on a pan it has not seen"
        )

    if split_row_counts:
        smallest = min(split_row_counts.values())
        if smallest < MIN_ROWS_PER_SPLIT:
            reasons.append(
                f"the smaller split holds {smallest} row(s); at least "
                f"{MIN_ROWS_PER_SPLIT} are needed before a score computed on it "
                "describes more than the split itself"
            )

    if split_class_counts:
        for split, counts in sorted(split_class_counts.items()):
            for name, count in sorted(counts.items()):
                if count < MIN_TRAIN_ROWS_PER_CLASS and split == "train":
                    reasons.append(
                        f"class {name!r} has {count} row(s) in the training split; "
                        f"at least {MIN_TRAIN_ROWS_PER_CLASS} are needed or the "
                        "model cannot learn to predict it at all"
                    )

    sufficient = not reasons
    return SufficiencyReport(
        sufficient=sufficient,
        message=None if sufficient else INSUFFICIENT_DATA_MESSAGE,
        reasons=tuple(reasons),
        labelled_rows=labelled_rows,
        total_rows=total_rows if total_rows is not None else labelled_rows,
        class_counts=dict(class_counts),
        distinct_pans=distinct_pans,
        feature_count=feature_count,
        split_row_counts=dict(split_row_counts or {}),
        split_class_counts={
            split: dict(counts) for split, counts in (split_class_counts or {}).items()
        },
    )


def class_counts(labels: Sequence[str] | np.ndarray) -> dict[str, int]:
    """Count labels as strings, sorted by name so the output is stable.

    Sorted because a count report is read and compared, and a report that
    reorders itself between runs makes two identical datasets look different.
    """
    values = [str(label) for label in np.asarray(labels).ravel()]
    unique, counts = np.unique(np.asarray(values, dtype=object), return_counts=True)
    return {str(name): int(count) for name, count in zip(unique, counts)}


__all__ = [
    "INSUFFICIENT_DATA_MESSAGE",
    "MIN_CLASSES",
    "MIN_DISTINCT_PANS",
    "MIN_FEATURES",
    "MIN_ROWS_PER_CLASS",
    "MIN_ROWS_PER_SPLIT",
    "MIN_TOTAL_LABELLED_ROWS",
    "MIN_TRAIN_ROWS_PER_CLASS",
    "SufficiencyReport",
    "check_sufficiency",
    "class_counts",
]
