"""Module 09 - cross-validation, and the choice of whether the data supports it.

Three designs, chosen by the dataset's structure rather than applied by default,
because a cross-validation that ignores the structure of the data it is resampling
produces a number that will be quoted and will mean the wrong thing.

**Blocked temporal** (``chronological``, ``per_pan_chronological``)
    Contiguous blocks in time, each scored on a block later than everything it
    trained on. Expanding window, never shuffled. Shuffling a time-ordered dataset
    inside a CV loop is the single most common way a cross-validated score becomes
    meaningless: it trains on next week to predict last Tuesday, and reports the
    result as evidence the model generalises. :mod:`app.ml.splitting` exists to
    prevent exactly that, and a CV design that undid it would make every
    cross-validated figure in the system a description of interpolation between
    observations rather than of forecasting.

**Leave pans out** (``grouped_by_pan``)
    Whole pans assigned to one fold or the other, never both. This is the design
    that answers the question a deployed model actually faces, because a new pan
    is a new pan. It is also the design with the highest variance on these dataset
    sizes, and that is stated on the report rather than smoothed over.

**Stratified k-fold** (``random``)
    Row-level, seeded, stratified by class. Offered because it is the right answer
    when rows are not time-ordered and no pan contributes near-duplicates. It is
    not applied to time-ordered data by default, and the report says which design
    ran.

**Refusal is the designed outcome, and it is reported rather than hidden.** The task
this module was given is "where dataset size permits", and the interesting half is
what happens when it does not. Cross-validation divides the data you have; asking
for five folds of a dataset that supports one means each fold's score describes
two rows. So the same thresholds Module 08 uses to decide a dataset can be trained
on at all are applied here, and a refusal is stored on the model version with its
counts. A reader then learns that cross-validation was considered and declined,
rather than inferring from an absent field that nobody thought about it. The
alternative -- running five folds anyway -- produces a table of numbers with no
spread and a mean that is an average of noise.

**Random Forest is included and marked ineligible.** It is fitted and scored on
every fold, exactly as in the single-split run, and ``auto_selectable`` is carried
per candidate so a comparison table cannot present it as being in the running.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold, StratifiedKFold

from app.ml.candidates import CANDIDATE_ALGORITHMS, NOT_AUTO_SELECTABLE, CandidateSpec
from app.ml.evaluation import score_estimator
from app.ml.preprocessing import (
    apply_preprocessing,
    classify_columns,
    fit_preprocessing,
)
from app.ml.splitting import (
    CHRONOLOGICAL,
    GROUPED_BY_PAN,
    PER_PAN_CHRONOLOGICAL,
    RANDOM,
)
from app.ml.sufficiency import (
    MIN_ROWS_PER_SPLIT,
    MIN_TOTAL_LABELLED_ROWS,
    class_counts,
)
from app.models.evaluation import (
    AlgorithmCrossValidation,
    CrossValidationFold,
    CrossValidationReport,
    MetricAggregate,
    computed,
    not_computed,
)

#: The three designs, named. Stored on the report so a reader knows which question
#: the spread of folds was answering.
BLOCKED_TEMPORAL = "blocked_temporal"
LEAVE_PANS_OUT = "leave_pans_out"
STRATIFIED_KFOLD = "stratified_kfold"

#: Most folds this module will run. Five is the point past which the estimate
#: stops improving appreciably on a few hundred rows and the cost per candidate
#: keeps rising linearly -- and with four candidates, the fit count is
#: ``n_splits * 4``.
MAX_SPLITS = 5

#: Fewest folds that can produce a spread at all. Two gives a range; one gives a
#: single number wearing the label of a distribution.
MIN_SPLITS = 2

#: Which split strategy each design is derived from, and why. A strategy with no
#: design attached raises rather than defaulting to a random one: defaulting would
#: silently replace a question about unseen pans with a question about unseen rows.
DESIGN_FOR_STRATEGY: dict[str, str] = {
    CHRONOLOGICAL: BLOCKED_TEMPORAL,
    PER_PAN_CHRONOLOGICAL: BLOCKED_TEMPORAL,
    GROUPED_BY_PAN: LEAVE_PANS_OUT,
    RANDOM: STRATIFIED_KFOLD,
}

DESIGN_RATIONALE: dict[str, str] = {
    BLOCKED_TEMPORAL: (
        "Contiguous blocks in time, each scored on a block strictly later than "
        "everything it was trained on, with an expanding training window. Rows are "
        "never shuffled, so no fold trains on an observation later than the one it "
        "is scored on. This answers the same question the single split answers, and "
        "adds the spread across several cut points instead of one."
    ),
    LEAVE_PANS_OUT: (
        "Whole pans are assigned to one fold or the other and never appear on both "
        "sides, so each fold's score is a score on pans the model has never seen. "
        "This is the design that matches deployment, and it is also the one with "
        "the widest spread on a few pans: a single held-out pan is a single pan, "
        "and the fold-to-fold range below is the honest size of that uncertainty."
    ),
    STRATIFIED_KFOLD: (
        "Seeded, row-level, stratified by class. Appropriate when rows are not "
        "ordered in time and no pan contributes near-duplicate rows. On "
        "time-ordered data it is the weakest of the three and is not the default; "
        "it is derived from a 'random' split strategy precisely so that asking for "
        "it is a deliberate statement about the data."
    ),
}

DESIGN_EVALUATES_UNSEEN_PANS: dict[str, bool | None] = {
    BLOCKED_TEMPORAL: None,
    LEAVE_PANS_OUT: True,
    STRATIFIED_KFOLD: None,
}

DESIGN_SHUFFLED: dict[str, bool] = {
    BLOCKED_TEMPORAL: False,
    LEAVE_PANS_OUT: False,
    STRATIFIED_KFOLD: True,
}

#: The metrics summarised across folds. Exactly the six the comparison table shows,
#: so a cross-validation aggregate cannot grow a column the single-split table does
#: not have.
AGGREGATED_METRICS: tuple[str, ...] = (
    "accuracy",
    "precision",
    "recall",
    "f1",
    "roc_auc",
    "pr_auc",
)

#: The sentence a refusal carries. Matches Module 08's, deliberately: the
#: condition is the same one -- there is not enough real labelled data for a
#: held-out figure to mean anything -- and a caller handling it should handle one
#: string rather than two that differ only in wording.
INSUFFICIENT_DATA_MESSAGE = (
    "Insufficient labelled data for reliable model training."
)


@dataclass(frozen=True)
class CrossValidationPlan:
    """What cross-validation will do, or why it will not.

    Resolved before any estimator is fitted, so a refusal costs nothing and leaves
    no partial folds behind for a reader to mistake for results.
    """

    performed: bool
    method: str | None
    n_splits: int | None
    reasons: tuple[str, ...] = ()
    detail: dict[str, Any] | None = None


def plan_cross_validation(
    *,
    split_strategy: str,
    labelled_rows: int,
    class_counts_by_class: dict[str, int],
    distinct_pans: int,
    requested_splits: int | None = None,
) -> CrossValidationPlan:
    """Decide the design and the fold count, or refuse with the counts that failed.

    Every failing check is collected rather than returned on the first, so one call
    reports everything that is wrong with the data -- the same choice
    :func:`app.ml.sufficiency.check_sufficiency` makes, for the same reason.
    """
    if split_strategy not in DESIGN_FOR_STRATEGY:
        raise ValueError(
            f"Unknown split strategy {split_strategy!r}, so no cross-validation "
            f"design follows from it. Use one of {', '.join(DESIGN_FOR_STRATEGY)}."
        )
    method = DESIGN_FOR_STRATEGY[split_strategy]
    reasons: list[str] = []

    if labelled_rows < MIN_TOTAL_LABELLED_ROWS:
        reasons.append(
            f"only {labelled_rows} labelled row(s); at least "
            f"{MIN_TOTAL_LABELLED_ROWS} are needed before dividing them into folds "
            "means anything"
        )

    smallest_class = min(class_counts_by_class.values(), default=0)
    # A fold that misses the rare class entirely produces no recall for it, and a
    # macro figure computed without it covers a different label space each time.
    # Requiring one example of the rarest class per fold is the floor at which
    # stratified designs can deliver that; the actual per-fold coverage is recorded
    # afterwards regardless, because a design can still fall short.
    upper = min(
        MAX_SPLITS,
        smallest_class if smallest_class > 0 else MIN_SPLITS,
        distinct_pans if method == LEAVE_PANS_OUT else MAX_SPLITS,
    )
    n_splits = upper

    if requested_splits is not None:
        if requested_splits < MIN_SPLITS:
            reasons.append(
                f"{requested_splits} fold(s) were requested; at least {MIN_SPLITS} "
                "are needed before a spread across folds exists to report"
            )
        elif requested_splits > MAX_SPLITS:
            reasons.append(
                f"{requested_splits} folds were requested and this module runs at "
                f"most {MAX_SPLITS}. Beyond that the estimate stops improving on a "
                "few hundred rows while the fit count keeps rising, and the spread "
                "reported is mostly the spread of the resampling itself."
            )
        elif smallest_class and requested_splits > smallest_class:
            reasons.append(
                f"{requested_splits} folds were requested but the rarest class has "
                f"only {smallest_class} labelled row(s). Every fold would leave that "
                "class out of at least one side, and a macro figure missing a class "
                "is not comparable with one that has it."
            )
        n_splits = min(requested_splits, upper) if upper > 0 else requested_splits

    if n_splits < MIN_SPLITS:
        reasons.append(
            f"the data supports {max(n_splits, 0)} fold(s) at most; at least "
            f"{MIN_SPLITS} are needed"
        )
        n_splits = None
    elif smallest_class and n_splits > smallest_class:
        reasons.append(
            f"the rarest class has {smallest_class} row(s), fewer than the {n_splits} "
            "folds planned, so at least one fold cannot be scored against it"
        )
        n_splits = None

    if method == LEAVE_PANS_OUT and distinct_pans < MIN_SPLITS:
        reasons.append(
            f"{distinct_pans} distinct pan(s); at least {MIN_SPLITS} are needed "
            "before any pan can be held out from any other"
        )
        n_splits = None

    # The smallest fold's share of the data, which is what actually bounds a
    # fold's score. Blocked temporal folds a row count n_splits+1 ways, so its test
    # blocks are smaller than the n_splits divisor suggests; using the wrong one here
    # would wave through a design whose folds hold one row each.
    divisor = n_splits + 1 if method == BLOCKED_TEMPORAL else n_splits
    if n_splits and labelled_rows // divisor < MIN_ROWS_PER_SPLIT:
        reasons.append(
            f"each of {n_splits} test folds would hold about {labelled_rows // divisor} "
            f"row(s); at least {MIN_ROWS_PER_SPLIT} are needed before a score "
            "computed on a fold describes more than that fold"
        )
        n_splits = None

    if reasons:
        return CrossValidationPlan(
            performed=False, method=method, n_splits=None, reasons=tuple(reasons)
        )
    return CrossValidationPlan(
        performed=True,
        method=method,
        n_splits=n_splits,
        detail={
            "shuffled": DESIGN_SHUFFLED[method],
            "evaluates_unseen_pans": DESIGN_EVALUATES_UNSEEN_PANS[method],
            "rationale": DESIGN_RATIONALE[method],
            "smallest_class_rows": smallest_class,
        },
    )


def _blocked_temporal_folds(
    order: np.ndarray, n_splits: int
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window folds over time-ordered row positions.

    The ordered rows are cut into ``n_splits + 1`` contiguous blocks and fold *k*
    trains on the first *k* blocks and is scored on block *k + 1*. The expanding
    window is deliberate over a sliding one: a sliding window would let a fold drop
    the oldest training rows and thereby test a model trained on a different amount
    of history than its neighbours, which turns the fold-to-fold spread into a
    measure of window length rather than of the data.

    Hand-rolled rather than delegating to ``TimeSeriesSplit`` so the fold boundaries
    and the per-fold class coverage can be reported: those boundaries are what a
    reader needs in order to judge whether the folds were comparable at all.
    """
    blocks = np.array_split(order, n_splits + 1)
    folds: list[tuple[np.ndarray, np.ndarray]] = []
    # blocks[0] seeds the history, so fold 1 trains on it and is scored on blocks[1].
    # Training on an empty window would be a fold whose score describes nothing.
    history: list[np.ndarray] = [blocks[0]]
    for block in blocks[1:]:
        folds.append((np.sort(np.concatenate(history)), np.sort(block)))
        history.append(block)
    return folds


def _fold_indices(
    method: str,
    frame: pd.DataFrame,
    n_splits: int,
    random_seed: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Train/test index pairs for one design, in fold order.

    ``frame`` must carry ``pan_id``, ``anchor_timestamp`` and ``label``.
    """
    positions = np.arange(len(frame))
    if method == BLOCKED_TEMPORAL:
        # Stable, so two rows sharing an anchor instant keep their input order
        # rather than being left in whatever order the merge produced. The same
        # argument as in app.ml.splitting, and the same consequence if dropped: the
        # split would depend on row order for tied timestamps.
        order = np.argsort(
            pd.to_datetime(frame["anchor_timestamp"], utc=True, errors="coerce").values,
            kind="stable",
        )
        return _blocked_temporal_folds(order, n_splits)
    if method == LEAVE_PANS_OUT:
        groups = frame["pan_id"].astype(str).to_numpy()
        splitter = GroupKFold(n_splits=n_splits)
        return [
            (np.sort(train), np.sort(test))
            for train, test in splitter.split(positions, frame["label"], groups)
        ]
    if method == STRATIFIED_KFOLD:
        splitter = StratifiedKFold(
            n_splits=n_splits, shuffle=True, random_state=random_seed
        )
        return [
            (np.sort(train), np.sort(test))
            for train, test in splitter.split(
                positions, frame["label"].astype(str)
            )
        ]
    raise ValueError(
        f"Unknown cross-validation design {method!r}. The three designs are "
        f"{', '.join(sorted(set(DESIGN_FOR_STRATEGY.values())))}."
    )


def _time_range(frame: pd.DataFrame, indices: np.ndarray) -> dict[str, str | None]:
    """Earliest and latest anchor instant over ``indices``, as ISO strings."""
    if len(indices) == 0:
        return {"earliest": None, "latest": None}
    values = pd.to_datetime(
        frame["anchor_timestamp"].iloc[indices], utc=True, errors="coerce"
    )
    if values.notna().sum() == 0:
        return {"earliest": None, "latest": None}
    return {"earliest": values.min().isoformat(), "latest": values.max().isoformat()}


def aggregate_folds(
    folds: Sequence[CrossValidationFold],
) -> dict[str, MetricAggregate]:
    """Mean, spread and range per headline metric, with the folds each covers.

    ``folds_omitted`` is the point of this function. A mean over three of five
    folds is a real number, and presented alone it reads as a five-fold mean with
    narrow agreement. Naming the folds that could not produce the metric -- which
    are exactly the folds where a class was missing from the test rows -- is the
    difference between a spread a reader can trust and one they cannot.

    The standard deviation is a *sample* deviation (``ddof=1``), because the folds
    are a sample of possible cuts rather than the whole population of them. With one
    fold there is no spread to report, and the reading says so rather than
    returning 0.0, which would be indistinguishable from five folds that agreed
    exactly.
    """
    aggregates: dict[str, MetricAggregate] = {}
    for metric in AGGREGATED_METRICS:
        values: list[float] = []
        omitted: list[int] = []
        first_reason: str | None = None
        for fold in folds:
            reading = getattr(fold.evaluation, metric, None)
            if reading is None or not reading.computed or reading.value is None:
                omitted.append(fold.fold)
                first_reason = first_reason or (reading.reason if reading else None)
                continue
            values.append(reading.value)
        if not values:
            aggregates[metric] = MetricAggregate(
                mean=not_computed(
                    first_reason
                    or "No fold produced this metric, so there is nothing to average."
                ),
                standard_deviation=not_computed(
                    first_reason or "No fold produced this metric."
                ),
                minimum=not_computed(
                    first_reason or "No fold produced this metric."
                ),
                maximum=not_computed(
                    first_reason or "No fold produced this metric."
                ),
                folds_aggregated=0,
                folds_omitted=omitted,
                reason=first_reason,
            )
            continue
        array = np.asarray(values, dtype=float)
        if len(values) == 1:
            spread = not_computed(
                f"Only one of {len(folds)} folds produced this metric, so a standard "
                "deviation would be 0.0 -- the spread of a single number, which is "
                "not a measurement of anything. The minimum and maximum below are "
                "the value itself."
            )
        else:
            spread = computed(float(np.std(array, ddof=1)))
        aggregates[metric] = MetricAggregate(
            mean=computed(float(np.mean(array)), reason=_coverage_reason(metric, len(values), len(folds))),
            standard_deviation=spread,
            minimum=computed(float(np.min(array))),
            maximum=computed(float(np.max(array))),
            folds_aggregated=len(values),
            folds_omitted=omitted,
            reason=first_reason,
        )
    return aggregates


def _coverage_reason(metric: str, included: int, total: int) -> str | None:
    """Say on the mean itself when it covers fewer folds than were run."""
    if included == total:
        return None
    return (
        f"Mean over {included} of {total} folds. The remaining "
        f"{total - included} fold(s) could not produce {metric} and are listed in "
        f"folds_omitted, so this is not a {total}-fold mean."
    )


def run_cross_validation(
    *,
    frame: pd.DataFrame,
    labels: np.ndarray,
    feature_names: Sequence[str],
    feature_kinds: dict[str, str],
    split_strategy: str,
    random_seed: int,
    requested_splits: int | None = None,
    specs: Sequence[CandidateSpec] | None = None,
) -> CrossValidationReport:
    """Refit every candidate across folds and score each one.

    The preprocessing is refitted inside every fold, on that fold's training rows
    only. This is the same invariant Module 08's pipeline holds, and it is the
    reason a fold is written as several lines rather than one call: a median or a
    category list computed once over the whole dataset and reused across folds puts
    held-out information into every fold's training features, and every fold's
    score comes out slightly better than the model deserves. The leak is invisible
    in the output, which is exactly what makes it dangerous.

    Returns a report in both directions. A refusal is a report with
    ``performed=False`` and a reason, never an exception, because it is stored
    alongside the single-split evaluation and a reader needs to see that
    cross-validation was declined for a stated reason rather than skipped.
    """
    plan = plan_cross_validation(
        split_strategy=split_strategy,
        labelled_rows=len(labels),
        class_counts_by_class=class_counts(labels),
        distinct_pans=int(frame["pan_id"].astype(str).nunique())
        if "pan_id" in frame.columns
        else 0,
        requested_splits=requested_splits,
    )
    if not plan.performed or plan.n_splits is None or plan.method is None:
        return CrossValidationReport(
            performed=False,
            reason=INSUFFICIENT_DATA_MESSAGE,
            matched_split_strategy=split_strategy,
            # The design that would have been used is named even in the refusal, so
            # a reader can see the question that was asked and declined rather than
            # only that nothing happened.
            method=DESIGN_FOR_STRATEGY.get(split_strategy),
            folds=[],
            aggregate={},
        )
    detail = plan.detail or {}
    split_frame = pd.DataFrame(
        {
            "pan_id": [str(value) for value in frame["pan_id"]],
            "anchor_timestamp": list(frame["anchor_timestamp"]),
            "label": [str(value) for value in labels],
        }
    )
    folds_by_design = _fold_indices(
        plan.method, split_frame, plan.n_splits, random_seed
    )
    numeric_columns, categorical_columns = classify_columns(feature_names, feature_kinds)
    chosen = tuple(specs) if specs is not None else tuple(
        CandidateSpec(algorithm=algorithm, parameters={})
        for algorithm in CANDIDATE_ALGORITHMS
    )

    per_algorithm: dict[str, AlgorithmCrossValidation] = {}
    for spec in chosen:
        folds: list[CrossValidationFold] = []
        for number, (train_index, test_index) in enumerate(folds_by_design, start=1):
            if len(test_index) == 0 or len(train_index) == 0:
                # Reachable only through a hand-asked fold count that divides badly.
                # Recorded as a fold with no evaluation rather than skipped, so the
                # fold count in the report always matches the design.
                continue
            train_frame = frame.iloc[train_index]
            test_frame = frame.iloc[test_index]
            pipeline, _ = fit_preprocessing(
                train_frame, numeric_columns, categorical_columns
            )
            train_matrix = apply_preprocessing(pipeline, train_frame)
            test_matrix = apply_preprocessing(pipeline, test_frame)
            train_labels = np.asarray(labels)[train_index]
            test_labels = np.asarray(labels)[test_index]
            classes = np.unique(train_labels)
            if len(test_labels) == 0:
                continue
            estimator = spec.build(random_seed)
            estimator.fit(train_matrix, train_labels)
            evaluation = score_estimator(
                estimator,
                test_matrix,
                test_labels,
                classes,
                f"fold {number} of {plan.n_splits} of the {plan.method} cross-validation",
            )
            folds.append(
                CrossValidationFold(
                    fold=number,
                    train_rows=len(train_index),
                    test_rows=len(test_index),
                    train_pans=sorted(set(split_frame["pan_id"].iloc[train_index])),
                    test_pans=sorted(set(split_frame["pan_id"].iloc[test_index])),
                    train_time_range=_time_range(split_frame, train_index),
                    test_time_range=_time_range(split_frame, test_index),
                    evaluation=evaluation,
                )
            )
        per_algorithm[spec.algorithm] = AlgorithmCrossValidation(
            algorithm=spec.algorithm,
            auto_selectable=spec.algorithm not in NOT_AUTO_SELECTABLE,
            selection_note=_selection_note(spec.algorithm),
            folds=folds,
            aggregate=aggregate_folds(folds),
        )

    # The report's own fold list and aggregate are the *selected* candidate's, when
    # one can be named, so a reader who does not want to pick a candidate still has
    # a headline. Deliberately not a "best": it is the candidate the documented
    # Module 08 rule names, and the reason travels with it.
    headline = _headline_candidate(per_algorithm)
    report_folds = per_algorithm[headline].folds if headline else []
    report_aggregate = per_algorithm[headline].aggregate if headline else {}

    return CrossValidationReport(
        performed=True,
        reason=None,
        method=plan.method,
        n_splits=plan.n_splits,
        shuffled=detail.get("shuffled"),
        evaluates_unseen_pans=detail.get("evaluates_unseen_pans"),
        matched_split_strategy=split_strategy,
        rationale=detail.get("rationale"),
        folds=report_folds,
        aggregate=report_aggregate,
        per_algorithm=per_algorithm,
    )


def _selection_note(algorithm: str) -> str:
    """Why a candidate is or is not eligible for automatic selection."""
    if algorithm in NOT_AUTO_SELECTABLE:
        return (
            "Fitted and scored on every fold, and reported here, but never selected "
            "automatically: on these data volumes an ensemble of deep trees wins a "
            "held-out score by memorising, and making that the default would turn a "
            "warning about dataset size into a deployment. It can be promoted "
            "deliberately, and the request is recorded on the model version."
        )
    return "Eligible for automatic selection under the documented macro-F1 rule."


def _headline_candidate(
    per_algorithm: dict[str, AlgorithmCrossValidation]
) -> str | None:
    """The candidate the documented rule names, or None if the rule names none.

    Uses Module 08's own rule -- highest mean macro F1 among automatically
    selectable candidates, ties broken on balanced accuracy then candidate order --
    so the fold-level choice cannot disagree with the single-split choice made by
    the same rule. Returns ``None`` rather than the highest scorer overall when
    every scorer is ineligible, because "the best of the ones that cannot be
    selected" is not an answer the rule gives.
    """
    eligible = [
        entry
        for entry in per_algorithm.values()
        if entry.auto_selectable and entry.folds
    ]
    if not eligible:
        return None
    ordered = sorted(
        eligible,
        key=lambda entry: (
            -(
                entry.aggregate.get("f1").mean.value
                if entry.aggregate.get("f1")
                and entry.aggregate["f1"].mean.value is not None
                else float("-inf")
            ),
            CANDIDATE_ALGORITHMS.index(entry.algorithm)
            if entry.algorithm in CANDIDATE_ALGORITHMS
            else len(CANDIDATE_ALGORITHMS),
        ),
    )
    return ordered[0].algorithm if ordered else None


__all__ = [
    "AGGREGATED_METRICS",
    "BLOCKED_TEMPORAL",
    "DESIGN_FOR_STRATEGY",
    "DESIGN_RATIONALE",
    "INSUFFICIENT_DATA_MESSAGE",
    "LEAVE_PANS_OUT",
    "MAX_SPLITS",
    "MIN_SPLITS",
    "STRATIFIED_KFOLD",
    "CrossValidationPlan",
    "aggregate_folds",
    "plan_cross_validation",
    "run_cross_validation",
]
