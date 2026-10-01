"""Module 09 - scoring a fitted classifier, and refusing to score what cannot be scored.

There is exactly one implementation of "how well did this classifier do on these
rows", and it is this file. Module 08's :func:`app.ml.candidates.evaluate` is a
thin adapter over it, and Module 09's service is the other caller. A second
implementation would be a second set of conclusions about the same data, and the
two would drift in exactly the places that matter: which metrics get computed, and
what happens when one of them cannot be.

The hard part of this module is not the arithmetic. ``accuracy_score`` is one line.
The hard part is the arithmetic's *boundary conditions*, and there are four of them,
each of which is a place where the obvious implementation produces a confident
number that describes nothing.

**ROC-AUC is only defined for a binary problem with both classes present.** Three
distinct failures hide behind that, and they get three distinct reasons:

1. More than two classes. A one-vs-rest AUC is a real and widely used quantity, but
   it is not the same number as a binary curve, so it is reported under its own
   key with its own name rather than folded into ``roc_auc``. Putting a one-vs-rest
   average in the same column as a binary curve is how a comparison table comes to
   compare a three-class model against a two-class one and report the difference as
   a modelling result.
2. Two classes, but only one of them in the evaluated rows. The curve is
   degenerate. Reporting 0.5 here -- the value a coin would score -- is the single
   most flattering thing a metrics module can do, and this one will not do it.
3. A model with no probabilities. Rank-based curves need scores, not labels.

**PR-AUC is defined in more situations than ROC-AUC, and that asymmetry is
deliberate.** Average precision needs at least one positive row; it does not need
negatives. So a held-out set containing only one class can produce a PR-AUC and
cannot produce a ROC-AUC, and this module reports exactly that rather than
suppressing both because one of them failed. Suppressing a metric because a
*different* metric was undefined is how a report ends up thinner than the evidence
allows. The per-class figure follows the same rule: a class with no positives in
the evaluated rows contributes no average precision, is excluded from the
macro-average, and is named in the notes as excluded.

**Precision is undefined for a class the model never predicted.** This one gets the
opposite treatment from ROC-AUC, and the difference is worth stating because it
looks inconsistent. scikit-learn's ``zero_division=0`` is applied, so the macro
figure and the per-class figures stay exactly consistent with each other and with
Module 08's existing numbers -- a reader comparing a Module 09 figure with a
Module 08 one is comparing like with like. But the resulting ``0.0`` is *not* a
measured precision, so every reading produced that way carries a ``reason`` saying
so, and the evaluation's ``notes`` repeat it. The number is comparable and the
caveat travels with it. Replacing it with null would have made the per-class
figures stop summing to the macro figure, which is its own kind of dishonesty.

**A confusion matrix's zeros are ambiguous.** A class in the label space with a
row of zeros did not occur; a class with a column of zeros was never predicted. Both
read as "no errors" in a naive table. The report therefore names
``classes_absent_from_evaluation`` and ``classes_absent_from_evaluation_rows``
separately, and returns ``most_confused_pair`` as null when there is no confusion
so that "nothing was confused" is distinguishable from "the matrix is empty".

The last rule is the one the whole file serves: **every figure is paired with the
number of rows it came from.** ``evaluated_rows`` and ``evaluation_set`` are on the
evaluation, not on the individual readings, because they qualify all of them at
once. A precision of 0.94 computed on 3 rows and a precision of 0.94 computed on
300 are the same string in a table, and the table is the thing most likely to be
quoted.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
from sklearn.base import ClassifierMixin
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    matthews_corrcoef,
    precision_score,
    recall_score,
    roc_auc_score,
)

from app.models.evaluation import (
    MACRO,
    VARIANT_BINARY,
    VARIANT_OVR_MACRO,
    WEIGHTED,
    ClassificationEvaluation,
    ClassScore,
    ConfusionMatrixReport,
    MetricReading,
    computed,
    not_computed,
)

#: The sentence prefix used whenever there is nothing to score. Split out so a
#: caller assembling a message does not have to restate it, and so a test can assert
#: on the constant rather than on a copy of the string in someone else's file.
NO_ROWS = "No rows were evaluated, so no metric can be computed from them."

#: ROC-AUC over more than two classes. Reported separately rather than refused
#: outright -- see the module docstring.
ROC_AUC_NEEDS_BINARY = (
    "ROC-AUC is only defined for a two-class problem, and this model has "
    "{count} classes ({classes}). A one-vs-rest average is reported as "
    "roc_auc_ovr_macro instead, which is a different quantity and is not "
    "comparable with the two-class figure."
)

#: The degenerate-curve case, which is the one this module exists to avoid.
ROC_AUC_NEEDS_BOTH_CLASSES = (
    "ROC-AUC is undefined when the evaluated rows contain only one class, because "
    "the curve has no negatives to rank against. The held-out rows here contain "
    "{present} of the model's {total} classes ({present_names}). Reporting 0.5 "
    "would be reporting the score of a coin."
)

#: No probabilities, so no ranking-based curve.
ROC_AUC_NEEDS_PROBABILITIES = (
    "ROC-AUC is a ranking measure and needs predicted probabilities, which this "
    "model did not produce for the evaluated rows."
)

#: One-vs-rest average precision, per class, and the macro over the classes that
#: have any positives at all.
PR_AUC_OVR_NOTE = (
    "One-vs-rest average precision per class, averaged over the {included} of "
    "{total} classes that appear in the evaluated rows. A class with no positive "
    "rows contributes no curve, so including it would dilute the average with a "
    "value that was never measured."
)

PR_AUC_NO_POSITIVES = (
    "Average precision is undefined for a class with no positive rows in the "
    "evaluated set, so it contributes no figure here."
)

#: The zero-division convention, stated once and repeated everywhere it bites.
ZERO_DIVISION_NOTE = (
    "Precision, recall and F1 use scikit-learn's zero_division=0 convention: a "
    "class with an empty denominator contributes 0.0 rather than being dropped. "
    "That keeps the per-class figures summing to the macro figure and keeps these "
    "numbers comparable with Module 08's, but the 0.0 is a declared convention "
    "and not a measured rate. Every reading produced that way says so in its own "
    "reason field."
)

PREDICTION_FAILED = (
    "The model could not produce predictions for the evaluated rows: {error}. No "
    "metric is reported rather than a partial one, because a figure computed from "
    "some of the rows would describe those rows and be read as describing all of "
    "them."
)


def align_probabilities(
    model: ClassifierMixin, matrix: np.ndarray, classes: np.ndarray
) -> np.ndarray:
    """Class probabilities in the model's own class order, always full width.

    A binary classifier returns one column for the positive class. Reconstructing
    the two-column form here means every downstream metric reads the same ordering,
    and a two-class and a three-class evaluation stay comparable -- which is the
    whole reason a comparison table can hold both.

    This is the function Module 08 already had, moved here so there is one of it.
    The version in ``candidates`` now calls this.
    """
    raw = np.asarray(model.predict_proba(matrix), dtype=float)
    if raw.ndim != 2:
        raise ValueError(
            f"predict_proba returned an array of shape {raw.shape}, which is not a "
            "(rows, classes) matrix. A model that cannot report one probability per "
            "class cannot be given a ranking metric."
        )
    if raw.shape[1] == len(classes):
        return raw
    if raw.shape[1] == 1 and len(classes) == 2:
        # A binary classifier that reports one column is reporting P(classes[1]), and
        # the other column is its complement -- not zero. Filling column 0 with 0.0
        # yields a row that does not sum to one, and scikit-learn's log_loss
        # renormalises such rows to [0, 1] and reports a log loss of 0.0. The figure
        # would be a perfect confidence score for a model that was wrong about a
        # third of the rows, which is the exact failure this module exists to prevent.
        positive = np.clip(raw[:, 0], 0.0, 1.0)
        return np.column_stack([1.0 - positive, positive])
    if raw.shape[1] == 1 and len(classes) == 1:
        # One class, and the column is the probability of it. A constant 1.0 would
        # be the degenerate answer; the model's own figure is kept.
        return raw.astype(float, copy=True)
    raise ValueError(
        f"predict_proba returned {raw.shape[1]} column(s) for a model fitted on "
        f"{len(classes)} classes. The probabilities cannot be aligned to the label "
        "space, and guessing which column is which would misreport every "
        "ranking-based metric."
    )


def _column_of(classes: Sequence[Any]) -> dict[str, int]:
    """Map each class label to the probability column that describes it.

    Built from the model's own ``classes_`` order rather than from the sorted
    report order, because the two are not guaranteed to agree. ``classes_`` is
    sorted for every estimator in scikit-learn today, so today they do -- and a
    report that quietly depended on that would be one release away from computing
    every one-vs-rest curve against the wrong column.
    """
    return {str(value): index for index, value in enumerate(classes)}



def _class_labels(classes: Sequence[Any]) -> list[str]:
    """The label space as strings, sorted, deduplicated.

    The label space is the model's own ``classes_`` rather than the classes that
    happened to appear in the evaluated rows. That difference is what lets the
    confusion matrix have an all-zero row for a class the model could predict but
    the data never produced -- which is a fact about the data, and one the report
    has to be able to state.
    """
    return sorted({str(value) for value in classes})


def _counts(labels: np.ndarray, label_space: Sequence[str]) -> dict[str, int]:
    """Counts per label, with every label-space class present even at zero."""
    as_text = [str(value) for value in np.asarray(labels).ravel()]
    counted = {label: 0 for label in label_space}
    for value in as_text:
        counted[value] = counted.get(value, 0) + 1
    return counted


def _zero_division_reason(denominator: int, quantity: str, label: str) -> str | None:
    """Why a 0.0 was a convention rather than a measurement, or None if it was not."""
    if denominator > 0:
        return None
    if quantity == "precision":
        return (
            f"The model predicted {label!r} zero times on the evaluated rows, so "
            "the denominator of its precision is empty. The 0.0 is the declared "
            "zero_division convention, not a measured rate: it says the class was "
            "never predicted, which is a different fact from being predicted and "
            "being wrong every time."
        )
    return (
        f"The evaluated rows contain no {label!r} cases, so the denominator of its "
        f"{quantity} is empty. The 0.0 is the declared zero_division convention, "
        "not a measured rate."
    )


def _binary_curve_readings(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    classes: Sequence[str],
) -> tuple[MetricReading, MetricReading]:
    """ROC-AUC and PR-AUC for a two-class problem, each with its validity reason.

    Split out because this is the one place where the two curves have genuinely
    different preconditions, and getting that difference right is the reason they
    are computed separately rather than together under one guard.
    """
    present = sorted({str(value) for value in np.asarray(y_true).ravel()})
    binary = y_true == classes[1]

    if len(present) < 2:
        return (
            not_computed(
                ROC_AUC_NEEDS_BOTH_CLASSES.format(
                    present=len(present),
                    total=len(classes),
                    present_names=", ".join(present) or "none",
                )
            ),
            computed(
                average_precision_score(binary, probabilities[:, 1]),
                variant=VARIANT_BINARY,
                reason=(
                    "Average precision over the positive class "
                    f"{classes[1]!r}. It is reported where ROC-AUC is not, because "
                    "it needs only positive rows: there are negatives to rank "
                    "nothing against, but the precision-recall trade-off is still "
                    "defined."
                ),
            ),
        )
    return (
        computed(roc_auc_score(binary, probabilities[:, 1]), variant=VARIANT_BINARY),
        computed(
            average_precision_score(binary, probabilities[:, 1]),
            variant=VARIANT_BINARY,
            reason=f"Average precision over the positive class {classes[1]!r}.",
        ),
    )


def _per_class_scores(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    label_space: Sequence[str],
    column_of: dict[str, int],
    probabilities: np.ndarray | None,
) -> tuple[list[ClassScore], list[str]]:
    """One :class:`ClassScore` per label-space class, plus the classes to note.

    ``zero_division=0`` throughout, so the per-class figures add up to the macro
    figure exactly. Every reading whose denominator was empty carries the reason it
    is 0.0 rather than a measured rate, and the list of such classes is returned so
    the caller can put them in the notes.
    """
    true_text = [str(value) for value in np.asarray(y_true).ravel()]
    pred_text = [str(value) for value in np.asarray(y_pred).ravel()]
    scores: list[ClassScore] = []
    convention_applied: list[str] = []

    for label in label_space:
        support = sum(1 for value in true_text if value == label)
        predicted = sum(1 for value in pred_text if value == label)
        precision_value = float(
            precision_score(
                y_true, y_pred, labels=[label], average="macro", zero_division=0
            )
        )
        recall_value = float(
            recall_score(
                y_true, y_pred, labels=[label], average="macro", zero_division=0
            )
        )
        f1_value = float(
            f1_score(
                y_true, y_pred, labels=[label], average="macro", zero_division=0
            )
        )
        precision_reason = _zero_division_reason(predicted, "precision", label)
        recall_reason = _zero_division_reason(support, "recall", label)
        if precision_reason or recall_reason:
            convention_applied.append(label)

        if probabilities is None:
            average_precision = not_computed(
                "The model produced no class probabilities for these rows, so no "
                "ranking curve can be drawn for this class."
            )
        elif support == 0:
            average_precision = not_computed(PR_AUC_NO_POSITIVES)
        else:
            one_vs_rest = np.asarray(
                [1 if value == label else 0 for value in true_text], dtype=int
            )
            average_precision = computed(
                average_precision_score(
                    one_vs_rest, probabilities[:, column_of[label]]
                ),
                variant=VARIANT_OVR_MACRO,
            )
        scores.append(
            ClassScore(
                label=label,
                support=support,
                predicted=predicted,
                precision=computed(
                    precision_value, reason=precision_reason
                ),
                recall=computed(recall_value, reason=recall_reason),
                f1=computed(
                    f1_value,
                    reason=precision_reason
                    or recall_reason
                    or (
                        None
                        if predicted and support
                        else "F1 combines two denominators that were not both "
                        "non-empty; see this class's precision and recall."
                    ),
                ),
                average_precision=average_precision,
            )
        )
    return scores, convention_applied


def _multiclass_curves(
    y_true: np.ndarray,
    probabilities: np.ndarray,
    label_space: Sequence[str],
    column_of: dict[str, int],
) -> tuple[MetricReading, MetricReading]:
    """One-vs-rest ROC-AUC and PR-AUC for a multi-class problem.

    Averaged over the classes that can support a curve, and the macro carries the
    count so the reader knows how many classes it covers. Both are labelled
    ``one_vs_rest_macro`` in their variant field, which is what keeps them out of
    the same table column as a two-class curve.
    """
    true_text = [str(value) for value in np.asarray(y_true).ravel()]
    aucs: list[float] = []
    aps: list[float] = []
    for index, label in enumerate(label_space):
        one_vs_rest = np.asarray(
            [1 if value == label else 0 for value in true_text], dtype=int
        )
        if one_vs_rest.sum() == 0 or one_vs_rest.sum() == len(one_vs_rest):
            # No positives, or no negatives: the ranking is degenerate for this
            # class. Skipping it is the honest move; the per-class reading says so.
            continue
        aucs.append(
            float(roc_auc_score(one_vs_rest, probabilities[:, column_of[label]]))
        )
        aps.append(
            float(
                average_precision_score(
                    one_vs_rest, probabilities[:, column_of[label]]
                )
            )
        )

    if not aucs:
        reason = (
            "No class has both positive and negative rows in the evaluated set, so "
            "no one-vs-rest curve is defined for any of them."
        )
        return not_computed(reason), not_computed(reason)
    if not aps:
        # Distinct from the AUC case: a class with all-positive rows has no
        # negatives for a curve but does have positives for average precision.
        return (
            computed(
                float(np.mean(aucs)),
                variant=VARIANT_OVR_MACRO,
                reason=f"One-vs-rest ROC-AUC over the {len(aucs)} class(es) that have both positive and negative rows.",
            ),
            not_computed(
                "Every class is either all-positive or all-negative in the evaluated "
                "rows, so average precision is undefined for all of them."
            ),
        )
    return (
        computed(
            float(np.mean(aucs)),
            variant=VARIANT_OVR_MACRO,
            reason=f"One-vs-rest ROC-AUC over the {len(aucs)} class(es) that have both positive and negative rows.",
        ),
        computed(
            float(np.mean(aps)),
            variant=VARIANT_OVR_MACRO,
            reason=PR_AUC_OVR_NOTE.format(included=len(aps), total=len(label_space)),
        ),
    )


def _confusion(
    y_true: np.ndarray, y_pred: np.ndarray, label_space: Sequence[str]
) -> ConfusionMatrixReport:
    """The matrix, with every flavour of zero named.

    ``sklearn`` is given the full label space rather than the observed labels, so a
    class that never occurred still gets its row and column instead of silently
    disappearing from the report. Disappearing is the failure this guards: a reader
    comparing two runs would see a 2x2 and a 3x3 and have to guess whether the
    class was dropped or never existed.

    The label space is text, so ``y_true`` and ``y_pred`` are converted to text
    before they are handed over. Without that, an integer-labelled model (the usual
    case, and the case the dataset catalogue produces for a banded target) is matched
    against ``["0", "1", "2"]`` and matches nothing -- ``confusion_matrix`` returns a
    correctly shaped matrix of zeros with no error, which is the worst possible
    failure here: a report that looks like a model that never got one row right,
    rather than like the bug it is. The rest of this module already compares labels as
    text for the same reason.
    """
    true_text = [str(value) for value in np.asarray(y_true).ravel()]
    pred_text = [str(value) for value in np.asarray(y_pred).ravel()]
    matrix = confusion_matrix(true_text, pred_text, labels=list(label_space))
    absent_everywhere = [
        label
        for index, label in enumerate(label_space)
        if matrix[index].sum() == 0 and matrix[:, index].sum() == 0
    ]
    absent_rows = [
        label for index, label in enumerate(label_space) if matrix[index].sum() == 0
    ]
    most_confused: dict[str, Any] | None = None
    worst = 0
    for actual_index, actual in enumerate(label_space):
        for predicted_index, predicted in enumerate(label_space):
            if actual_index == predicted_index:
                continue
            count = int(matrix[actual_index][predicted_index])
            if count > worst:
                worst = count
                most_confused = {
                    "actual": actual,
                    "predicted": predicted,
                    "count": count,
                }
    return ConfusionMatrixReport(
        labels=list(label_space),
        matrix=[[int(value) for value in row] for row in matrix],
        classes_absent_from_evaluation=absent_everywhere,
        classes_absent_from_evaluation_rows=absent_rows,
        most_confused_pair=most_confused,
    )


def evaluate_classifier(
    *,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    probabilities: np.ndarray | None,
    classes: Sequence[Any],
    evaluation_set: str,
) -> ClassificationEvaluation:
    """Score one fitted classifier on one set of rows.

    ``evaluation_set`` is required and must name the rows. It is the only thing
    standing between a reader and a figure they will quote without its context, and
    the surrounding service passes the same wording to every evaluation it builds
    so the phrase cannot drift between them.

    ``probabilities`` may be ``None``, and then every probability-dependent metric
    is reported as not computed with its reason. That is the path for an estimator
    that was fitted without ``predict_proba``; the alternative -- catching the
    ``AttributeError`` and carrying on -- would leave the ranking metrics missing
    with no explanation attached, which is indistinguishable from a bug.
    """
    label_space = _class_labels(classes)
    if not label_space:
        raise ValueError(
            "The model has an empty label space, so there is nothing to score it "
            "against. A classifier with no classes would report accuracy 1.00 on "
            "any rows at all."
        )
    if not evaluation_set.strip():
        raise ValueError(
            "evaluation_set must name the rows the metrics describe. A figure with "
            "no stated denominator is the one number this module will not publish."
        )
    # Built once and threaded through every per-class computation, so no reading
    # has to guess which probability column belongs to which label.
    column_of = _column_of(classes)

    row_count = len(np.asarray(y_true).ravel())
    notes: list[str] = []

    # -- the no-rows case -------------------------------------------------
    # Handled before anything else because every other reading would be an
    # exception or a division by zero, and because it is the case a careless
    # implementation turns into accuracy 1.00: an empty array scores perfectly
    # under `accuracy_score`.
    if row_count == 0:
        reason = NO_ROWS
        empty = not_computed(reason)
        return ClassificationEvaluation(
            evaluated_rows=0,
            evaluation_set=evaluation_set,
            classes=label_space,
            class_distribution={label: 0 for label in label_space},
            predicted_distribution={label: 0 for label in label_space},
            accuracy=empty,
            balanced_accuracy=empty,
            precision=empty,
            precision_weighted=empty,
            recall=empty,
            recall_weighted=empty,
            f1=empty,
            f1_weighted=empty,
            matthews_corrcoef=empty,
            roc_auc=empty,
            pr_auc=empty,
            brier_score=empty,
            log_loss=empty,
            per_class=[],
            confusion_matrix=None,
            notes=[
                reason,
                (
                    "A confusion matrix is absent rather than all-zero, because an "
                    "empty matrix of the right shape would read as a model that made "
                    "no errors on rows that do not exist."
                ),
            ],
        )

    true_text = [str(value) for value in np.asarray(y_true).ravel()]
    present = sorted(set(true_text))
    class_distribution = _counts(y_true, label_space)
    predicted_distribution = _counts(y_pred, label_space)

    # -- the headline metrics -------------------------------------------
    accuracy = computed(float(accuracy_score(y_true, y_pred)))
    balanced_accuracy = computed(float(balanced_accuracy_score(y_true, y_pred)))
    precision_macro = computed(
        float(precision_score(y_true, y_pred, average=MACRO, zero_division=0))
    )
    recall_macro = computed(
        float(recall_score(y_true, y_pred, average=MACRO, zero_division=0))
    )
    f1_macro = computed(
        float(f1_score(y_true, y_pred, average=MACRO, zero_division=0))
    )
    precision_weighted = computed(
        float(precision_score(y_true, y_pred, average=WEIGHTED, zero_division=0))
    )
    recall_weighted = computed(
        float(recall_score(y_true, y_pred, average=WEIGHTED, zero_division=0))
    )
    f1_weighted = computed(
        float(f1_score(y_true, y_pred, average=WEIGHTED, zero_division=0))
    )
    mcc = computed(float(matthews_corrcoef(y_true, y_pred)))
    notes.append(ZERO_DIVISION_NOTE)

    if len(present) == 1:
        # Balanced accuracy is a mean of per-class recalls, and a class with no
        # rows in the evaluated set has none. `balanced_accuracy_score` handles it
        # by averaging over the observed classes only, which means the figure
        # describes a different label space than the confusion matrix beside it.
        # Worth saying rather than leaving the two to look like the same scope.
        notes.append(
            f"The evaluated rows contain only {present[0]!r}, so the macro, "
            "weighted and per-class figures each cover a different set of classes "
            "than the others. Read them beside the confusion matrix rather than "
            "beside each other."
        )

    # -- calibration -----------------------------------------------------
    if probabilities is None:
        calibration_reason = (
            "The model produced no class probabilities for these rows, so the "
            "calibration and ranking measures below were not computed."
        )
        brier: MetricReading = not_computed(calibration_reason)
        logloss: MetricReading = not_computed(calibration_reason)
    else:
        brier = _brier(y_true, probabilities, label_space)
        logloss = _log_loss(y_true, probabilities, label_space)

    # -- the curves ------------------------------------------------------
    roc_auc: MetricReading
    pr_auc: MetricReading
    if len(label_space) < 2:
        curve_reason = (
            "The model has a single class, so it has nothing to rank against. "
            "ROC-AUC and average precision are both undefined."
        )
        roc_auc = not_computed(curve_reason)
        pr_auc = not_computed(curve_reason)
    elif probabilities is None:
        curve_reason = (
            "ROC-AUC and average precision are ranking measures and need predicted "
            "probabilities, which this model did not produce for these rows."
        )
        roc_auc = not_computed(curve_reason)
        pr_auc = not_computed(curve_reason)
    elif len(label_space) == 2:
        roc_auc, pr_auc = _binary_curve_readings(y_true, probabilities, label_space)
    else:
        roc_auc, pr_auc = _multiclass_curves(
            y_true, probabilities, label_space, column_of
        )
        notes.append(
            "ROC-AUC and average precision are reported as one-vs-rest macro "
            "averages because this is a multi-class problem. They are a different "
            "quantity from the two-class curve and are not comparable with it."
        )

    per_class, convention_classes = _per_class_scores(
        y_true, y_pred, label_space, column_of, probabilities
    )
    if convention_classes:
        notes.append(
            "The zero_division convention produced a 0.0 for: "
            + ", ".join(repr(label) for label in convention_classes)
            + ". See each class's own reason field."
        )
    absent = [
        label for label in label_space if class_distribution.get(label, 0) == 0
    ]
    if absent:
        notes.append(
            "No evaluated row carried these classes, so their recall has no "
            "denominator and their average precision is undefined: "
            + ", ".join(repr(label) for label in absent)
            + "."
        )

    return ClassificationEvaluation(
        evaluated_rows=row_count,
        evaluation_set=evaluation_set,
        classes=label_space,
        class_distribution=class_distribution,
        predicted_distribution=predicted_distribution,
        accuracy=accuracy,
        balanced_accuracy=balanced_accuracy,
        precision=precision_macro,
        precision_weighted=precision_weighted,
        recall=recall_macro,
        recall_weighted=recall_weighted,
        f1=f1_macro,
        f1_weighted=f1_weighted,
        matthews_corrcoef=mcc,
        roc_auc=roc_auc,
        pr_auc=pr_auc,
        brier_score=brier,
        log_loss=logloss,
        per_class=per_class,
        confusion_matrix=_confusion(y_true, y_pred, label_space),
        notes=notes,
    )


def _brier(
    y_true: np.ndarray, probabilities: np.ndarray, label_space: Sequence[str]
) -> MetricReading:
    """Brier score, or the reason it does not exist.

    Only defined for a single-label target. Handled rather than caught: a
    ``ValueError`` from scikit-learn would otherwise be indistinguishable from a
    genuine failure, and the two call for different responses.
    """
    if len(label_space) != 1:
        return not_computed(
            "The Brier score is defined for a single-label target, one probability "
            "per class. This model has "
            f"{len(label_space)} classes, so its per-class probabilities sum to one "
            "and no single squared-error score describes them."
        )
    try:
        return computed(
            float(brier_score_loss(y_true, probabilities, labels=list(label_space)))
        )
    except ValueError as error:
        return not_computed(f"The Brier score could not be computed: {error}")


def _log_loss(
    y_true: np.ndarray, probabilities: np.ndarray, label_space: Sequence[str]
) -> MetricReading:
    """Log loss, or the reason it does not exist.

    Clipped probabilities are rejected rather than clamped, because clamping would
    report a confidence figure derived from a number the model never produced.
    """
    try:
        return computed(
            float(log_loss(y_true, probabilities, labels=list(label_space)))
        )
    except ValueError as error:
        return not_computed(f"Log loss could not be computed: {error}")


def score_estimator(
    estimator: ClassifierMixin,
    matrix: np.ndarray,
    labels: np.ndarray,
    classes: Sequence[Any],
    evaluation_set: str,
) -> ClassificationEvaluation:
    """Predict, align probabilities, and score -- the one path a caller should use.

    Wrapping the three steps together is what stops a caller from scoring a model
    with the wrong class order, or from catching the ``predict_proba`` failure and
    reporting a figure set that is silently missing its curves.

    A prediction failure is not swallowed. It returns a full evaluation with every
    reading absent and the exception's message as the reason, because a partial
    figure set computed from whichever rows happened to predict is worse than no
    figure set: it would be read as complete.
    """
    try:
        predictions = np.asarray(estimator.predict(matrix))
    except Exception as error:  # noqa: BLE001 - reported, not hidden
        reason = PREDICTION_FAILED.format(error=error)
        empty = not_computed(reason)
        return ClassificationEvaluation(
            evaluated_rows=len(np.asarray(labels).ravel()),
            evaluation_set=evaluation_set,
            classes=_class_labels(classes),
            accuracy=empty,
            balanced_accuracy=empty,
            precision=empty,
            precision_weighted=empty,
            recall=empty,
            recall_weighted=empty,
            f1=empty,
            f1_weighted=empty,
            matthews_corrcoef=empty,
            roc_auc=empty,
            pr_auc=empty,
            brier_score=empty,
            log_loss=empty,
            per_class=[],
            confusion_matrix=None,
            notes=[reason],
        )
    try:
        probabilities = align_probabilities(estimator, matrix, np.asarray(classes))
    except (AttributeError, ValueError):
        # An estimator with no predict_proba is legitimate -- a hard-voting
        # classifier, say -- and its ranking metrics are simply unavailable. The
        # reason travels with the absent readings so a reader knows the model was
        # scored, not skipped.
        probabilities = None
    return evaluate_classifier(
        y_true=np.asarray(labels),
        y_pred=predictions,
        probabilities=probabilities,
        classes=classes,
        evaluation_set=evaluation_set,
    )


__all__ = [
    "NO_ROWS",
    "PREDICTION_FAILED",
    "PR_AUC_NO_POSITIVES",
    "PR_AUC_OVR_NOTE",
    "ROC_AUC_NEEDS_BINARY",
    "ROC_AUC_NEEDS_BOTH_CLASSES",
    "ROC_AUC_NEEDS_PROBABILITIES",
    "ZERO_DIVISION_NOTE",
    "align_probabilities",
    "evaluate_classifier",
    "score_estimator",
]
