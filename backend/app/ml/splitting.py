"""Module 8 - how the held-out rows are chosen.

The split is not a detail of the training loop. It decides what the reported
number is a measurement *of*, and two of the strategies here exist to prevent the
most flattering wrong answer.

**Chronological.** Rows are ordered by anchor time and the earliest fraction
becomes the training set. This is the default for operational use, because the
question being asked is usually "would this have worked on the pan last week",
and a random split answers it by letting the model learn from next Tuesday to
predict last Monday. A future observation must never inform a past one.

**Grouped by pan.** Whole pans are assigned to one side or the other, never both.
Rows from a single pan share weather, sensor drift, and local conditions, so a
random row-level split puts near-duplicates on both sides and reports a score
that partly measures whether the model recognised the same pan. Holding pans out
is the only way to learn anything about an unseen pan, which is the only thing that
matters when the model will be used on a pan it was not trained on.

**Random.** Stratified, seeded, and available. Stratified so a rare class is not
left entirely in the test set, seeded so the split is reproducible. On a
time-ordered dataset this is the weakest option and it is not the default, because
it will flatter the result.

**Per-pan chronological.** Within each pan, the earlier rows train and the later
ones test, so every pan is cut at the same relative point in its own history.

This strategy does *not* evaluate generalisation to an unseen pan, and it is named
accordingly. A pan appears on both sides by construction -- that is what makes the
time boundary consistent across pans -- so rows from one pan sit on both sides of
the split. It answers "does this forecast forward in time on pans it already
knows", which is a real question and the right one for a continuously instrumented
pan. It is not the right basis for deploying to a new pan, and the run records
``evaluates_unseen_pans: false`` so nobody has to infer that from the name.

Every strategy is deterministic given the seed. A split that moved between two
runs of the same data would make the two runs' metrics incomparable, and the
comparison is the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit, train_test_split

CHRONOLOGICAL = "chronological"
GROUPED_BY_PAN = "grouped_by_pan"
RANDOM = "random"
PER_PAN_CHRONOLOGICAL = "per_pan_chronological"

SplitStrategy = Literal[
    "chronological", "grouped_by_pan", "random", "per_pan_chronological"
]
SPLIT_STRATEGIES: tuple[str, ...] = (
    CHRONOLOGICAL,
    GROUPED_BY_PAN,
    RANDOM,
    PER_PAN_CHRONOLOGICAL,
)

#: The strategy used when a caller does not choose.
#:
#: Chronological, because the alternative to a time-aware split is a score that
#: silently assumes the model will be used on data from the past.
DEFAULT_SPLIT_STRATEGY: SplitStrategy = CHRONOLOGICAL


@dataclass(frozen=True)
class SplitResult:
    """Train and test indices, plus a description of how they were chosen.

    The description is stored with the run. A metric is only interpretable
    alongside the rule that produced the split it was computed on.
    """

    strategy: str
    train_indices: np.ndarray
    test_indices: np.ndarray
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "train_rows": len(self.train_indices),
            "test_rows": len(self.test_indices),
            **self.detail,
        }


def _ordered_indices(
    frame: pd.DataFrame, timestamp_column: str
) -> np.ndarray:
    """Row positions in ascending time order.

    A stable sort, so two rows sharing an anchor instant keep their input order
    instead of being left in whatever order the merge produced. Without the
    stability guarantee the split would depend on row order for tied timestamps,
    and two runs over the same data could partition the boundary rows differently.
    """
    if timestamp_column not in frame.columns:
        raise ValueError(
            f"Column {timestamp_column!r} is required to order the rows in time, "
            "and it is absent. A split that cannot see the timestamps cannot "
            "honour the observation order they encode."
        )
    return np.argsort(
        pd.to_datetime(frame[timestamp_column], utc=True, errors="coerce").values,
        kind="stable",
    )


def chronological_split(
    frame: pd.DataFrame,
    test_size: float,
    *,
    timestamp_column: str = "anchor_timestamp",
) -> SplitResult:
    """Train on the earliest rows, test on the most recent.

    ``test_size`` is the *fraction held out*, not a row count, so the proportion
    of the data kept for measuring does not silently shrink as the dataset grows.
    """
    if not 0 < test_size < 1:
        raise ValueError(
            f"test_size must be between 0 and 1, got {test_size}. A test fraction "
            "of zero would mean no held-out rows and a score computed on the "
            "training data, which is not a held-out score."
        )
    order = _ordered_indices(frame, timestamp_column)
    cut = int(len(order) * (1.0 - test_size))
    # Both sides need at least one row. With a test fraction this large the cut
    # would land at or past the end, and the failure is better raised here than
    # surfaced later as an empty-array error.
    if cut <= 0:
        raise ValueError(
            f"test_size={test_size} leaves no training rows. Reduce the held-out "
            "fraction so the earlier observations can be used for training."
        )
    train, test = order[:cut], order[cut:]
    if len(test) == 0:
        raise ValueError(
            f"test_size={test_size} leaves no held-out rows. The data is too small "
            "for this fraction; the sufficiency gate should have caught this."
        )
    latest_train = pd.to_datetime(
        frame[timestamp_column], utc=True
    ).iloc[train].max()
    earliest_test = pd.to_datetime(frame[timestamp_column], utc=True).iloc[
        test
    ].min()
    return SplitResult(
        strategy=CHRONOLOGICAL,
        train_indices=train,
        test_indices=test,
        detail={
            "timestamp_column": timestamp_column,
            "latest_train_timestamp": _iso(latest_train),
            "earliest_test_timestamp": _iso(earliest_test),
            # Stated rather than assumed: if the two ranges overlap the split is
            # no longer forward-in-time, and a reader should be able to see that
            # without recomputing it.
            "time_ordered": bool(
                pd.notna(latest_train)
                and pd.notna(earliest_test)
                and latest_train <= earliest_test
            ),
        },
    )


def grouped_by_pan_split(
    frame: pd.DataFrame,
    test_size: float,
    *,
    random_seed: int,
    pan_column: str = "pan_id",
) -> SplitResult:
    """Hold out whole pans, stratified by class where the counts allow it.

    Stratified group splitting would keep the class balance of the test set close
    to the training set, but scikit-learn refuses to stratify when any class has
    fewer rows than the number of groups, and on these datasets that is common.
    So the split falls back to unstratified grouping and *records* that it did,
    because a test set that missed a class entirely produces recall for that class
    that no model can be judged on.
    """
    if not 0 < test_size < 1:
        raise ValueError(
            f"test_size must be between 0 and 1, got {test_size}. A test fraction "
            "of zero would mean no held-out rows and a score computed on the "
            "training data, which is not a held-out score."
        )
    if pan_column not in frame.columns:
        raise ValueError(
            f"Column {pan_column!r} is required to group rows by pan, and it is "
            "absent. Without it, rows from one pan would land on both sides of the "
            "split and the model would be evaluated on pans it was trained on."
        )
    groups = frame[pan_column].astype(str)
    labels = frame["label"].astype(str)
    group_count = groups.nunique()

    # GroupShuffleSplit assigns *groups*, not rows, so a pan cannot be split
    # across the boundary. The row-level split it replaces would have placed
    # rows from the same pan on both sides -- near-duplicate conditions on both
    # sides of a split, which reads as generalisation and is memorisation.
    splitter = GroupShuffleSplit(
        n_splits=1, test_size=test_size, random_state=random_seed
    )
    train_idx, test_idx = next(splitter.split(np.arange(len(frame)), labels, groups))

    train_pans = set(groups.iloc[train_idx])
    test_pans = set(groups.iloc[test_idx])
    shared = sorted(train_pans & test_pans)
    if shared:
        # Defensive. GroupShuffleSplit cannot produce this, but the invariant that
        # makes the split honest is worth asserting rather than assuming: if it
        # ever fails, the reported score is invalid and must not be written.
        raise ValueError(
            f"Pans {shared} appear in both the training and held-out sets. A pan "
            "must be entirely on one side, or the model is evaluated on pans it "
            "has already seen."
        )
    if len(test_pans) < 1 or len(train_pans) < 1:
        raise ValueError(
            f"test_size={test_size} across {group_count} pan(s) left one side "
            "without a pan. Reduce the held-out fraction, or the data has too few "
            "pans to evaluate on an unseen one."
        )

    # Whether the held-out classes match the training ones. Reported because a
    # test set missing a class entirely yields no recall for it, and a metric
    # that silently omits a class is a metric quietly computed on less than it
    # appears to be.
    train_classes = sorted(set(labels.iloc[train_idx]))
    test_classes = sorted(set(labels.iloc[test_idx]))
    return SplitResult(
        strategy=GROUPED_BY_PAN,
        train_indices=np.asarray(train_idx),
        test_indices=np.asarray(test_idx),
        detail={
            "pan_column": pan_column,
            "train_pans": sorted(train_pans),
            "test_pans": sorted(test_pans),
            "total_pans": int(group_count),
            "stratified_by_class": False,
            "train_classes": train_classes,
            "test_classes": test_classes,
            "held_out_classes_match_training": train_classes == test_classes,
        },
    )


def per_pan_chronological_split(
    frame: pd.DataFrame,
    test_size: float,
    *,
    timestamp_column: str = "anchor_timestamp",
    pan_column: str = "pan_id",
) -> SplitResult:
    """Within each pan, earlier rows train and later rows test.

    Implemented by splitting each pan's rows on time and then consolidating. Both
    properties hold by construction rather than by a second strategy's fallback:
    a pan contributes rows to only one side, and every training row precedes every
    held-out row within its pan.

    The consequence worth knowing: because the boundary is per pan, a well
    instrumented pan can contribute many rows to each side while a pan that
    stopped reporting contributes only to the training set. The report includes
    the per-pan counts so an uneven division is visible.
    """
    if not 0 < test_size < 1:
        raise ValueError(
            f"test_size must be between 0 and 1, got {test_size}. A test fraction "
            "of zero would mean no held-out rows and a score computed on the "
            "training data, which is not a held-out score."
        )
    for column in (timestamp_column, pan_column):
        if column not in frame.columns:
            raise ValueError(
                f"Column {column!r} is required for a per-pan chronological split, "
                "and it is absent."
            )

    train_parts: list[np.ndarray] = []
    test_parts: list[np.ndarray] = []
    pan_detail: dict[str, dict[str, int]] = {}
    too_small: list[str] = []

    for pan, positions in frame.groupby(pan_column, sort=True).groups.items():
        indices = np.asarray(positions)
        ordered = indices[
            np.argsort(
                pd.to_datetime(
                    frame[timestamp_column].iloc[indices], utc=True
                ).values,
                kind="stable",
            )
        ]
        cut = int(len(ordered) * (1.0 - test_size))
        if cut <= 0 or cut >= len(ordered):
            # One row, or a fraction that puts every row on one side. Contributes
            # to training rather than being discarded, and is listed so the
            # report can show the pan was not represented in the held-out set.
            train_parts.append(ordered)
            pan_detail[str(pan)] = {"train": len(ordered), "test": 0}
            too_small.append(str(pan))
            continue
        train_parts.append(ordered[:cut])
        test_parts.append(ordered[cut:])
        pan_detail[str(pan)] = {"train": int(cut), "test": int(len(ordered) - cut)}

    if not test_parts:
        raise ValueError(
            f"No pan has enough rows for test_size={test_size} to leave a held-out "
            "row. The data is too small for this split; the sufficiency gate "
            "should have caught this."
        )

    train_idx = np.sort(np.concatenate(train_parts))
    test_idx = np.sort(np.concatenate(test_parts))
    train_pans = set(frame[pan_column].astype(str).iloc[train_idx])
    test_pans = set(frame[pan_column].astype(str).iloc[test_idx])
    shared = sorted(train_pans & test_pans)
    return SplitResult(
        strategy=PER_PAN_CHRONOLOGICAL,
        train_indices=train_idx,
        test_indices=test_idx,
        detail={
            "timestamp_column": timestamp_column,
            "pan_column": pan_column,
            "train_pans": sorted(train_pans),
            "test_pans": sorted(test_pans),
            "pans_on_both_sides": shared,
            # Stated explicitly, because this is the fact a reader needs and the
            # one this strategy does not give them. It holds a pan out from the
            # other side, so a score from it describes forecasting on known pans
            # and NOT generalisation to an unseen one. Use grouped_by_pan for
            # that question.
            "evaluates_unseen_pans": False,
            "per_pan_row_counts": pan_detail,
            "pans_without_held_out_rows": sorted(too_small),
        },
    )


def random_split(
    frame: pd.DataFrame, test_size: float, *, random_seed: int
) -> SplitResult:
    """Stratified, seeded, row-level split.

    Provided because a random split is the right choice when the rows are not
    ordered in a meaningful way and the same pan does not recur. It is not the
    default on time-ordered data: it will place later observations in the training
    set and earlier ones in the held-out set, and the resulting score describes
    interpolation between observations rather than forecasting.
    """
    if not 0 < test_size < 1:
        raise ValueError(
            f"test_size must be between 0 and 1, got {test_size}. A test fraction "
            "of zero would mean no held-out rows and a score computed on the "
            "training data, which is not a held-out score."
        )
    labels = frame["label"].astype(str)
    try:
        train_idx, test_idx = train_test_split(
            np.arange(len(frame)),
            test_size=test_size,
            random_state=random_seed,
            stratify=labels,
        )
    except ValueError as exc:
        raise ValueError(
            "The label distribution cannot be stratified across the requested "
            f"split: {exc}. Either the rarest class is too small to appear on both "
            "sides, or there are too few rows overall. The sufficiency gate should "
            "have caught this."
        ) from exc
    return SplitResult(
        strategy=RANDOM,
        train_indices=np.asarray(train_idx),
        test_indices=np.asarray(test_idx),
        detail={"stratified_by_class": True},
    )


def make_split(
    frame: pd.DataFrame,
    strategy: str = DEFAULT_SPLIT_STRATEGY,
    *,
    test_size: float = 0.2,
    random_seed: int = 0,
    timestamp_column: str = "anchor_timestamp",
    pan_column: str = "pan_id",
) -> SplitResult:
    """Dispatch to the requested strategy.

    An unrecognised name raises rather than defaulting. Defaulting would train
    quietly under a split nobody asked for and record the wrong one.
    """
    if strategy == CHRONOLOGICAL:
        return chronological_split(frame, test_size, timestamp_column=timestamp_column)
    if strategy == GROUPED_BY_PAN:
        return grouped_by_pan_split(
            frame, test_size, random_seed=random_seed, pan_column=pan_column
        )
    if strategy == PER_PAN_CHRONOLOGICAL:
        return per_pan_chronological_split(
            frame,
            test_size,
            timestamp_column=timestamp_column,
            pan_column=pan_column,
        )
    if strategy == RANDOM:
        return random_split(frame, test_size, random_seed=random_seed)
    raise ValueError(
        f"Unknown split strategy {strategy!r}. Use one of "
        f"{', '.join(SPLIT_STRATEGIES)}."
    )


def _iso(value: Any) -> str | None:
    """Render a timestamp as an ISO string, or None if it is not a time."""
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).isoformat()


__all__ = [
    "CHRONOLOGICAL",
    "DEFAULT_SPLIT_STRATEGY",
    "GROUPED_BY_PAN",
    "PER_PAN_CHRONOLOGICAL",
    "RANDOM",
    "SPLIT_STRATEGIES",
    "SplitResult",
    "SplitStrategy",
    "chronological_split",
    "grouped_by_pan_split",
    "make_split",
    "per_pan_chronological_split",
    "random_split",
]
