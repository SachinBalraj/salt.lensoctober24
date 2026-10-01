"""The absence path, on its own.

Every other test in Module 9's suite runs against a dataset whose held-out rows carry
all three classes, so every metric comes back computed and the assertions about
absence pass vacuously -- they never execute the branch that matters. That is a
dangerous way to test a module whose central claim is about what it *refuses* to
report, so the case is built here explicitly, with real estimators and real refusals.

**Three distinctions are pinned down, and each has its own test.**

1. A metric that could not be calculated is absent, and stays absent all the way to an
   empty cell in the CSV with its reason in the column beside it. If any step of that
   path substituted ``0.0``, the metric would read as a measurement of zero
   performance, which is a claim the data does not make.
2. A metric that *was* calculated and came out zero is reported as ``0.0``, with the
   convention that produced the zero named in its own reason. Collapsing this into
   case 1 would discard real measurements; collapsing case 1 into this would invent
   them. The clearest instance is a single class in a single-class holdout, where
   precision and recall of that class are on opposite sides of the line.
3. ROC-AUC and PR-AUC do not always fail together. On a two-class problem a
   single-class holdout still leaves average precision defined; on a three-class
   problem it does not. A module that refused both everywhere would be hiding real
   measurements, which is its own kind of dishonesty, so the asymmetry is tested
   rather than assumed.
"""

from __future__ import annotations

import csv
import io

import numpy as np
import pytest
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier

from app.ml.comparison import COMPARISON_COLUMNS, build_comparison, flatten_readings
from app.ml.evaluation import score_estimator
from app.models.evaluation import computed, not_computed
from app.services.evaluation import csv_from_comparison

#: Three classes, so the fit is a real classification fit rather than a degenerate one.
TRAIN_X = np.array(
    [
        [float(a), float(b), float(c)]
        for a in (0, 1, 2)
        for b in (0, 1, 2)
        for c in (0, 1, 2)
    ]
)
TRAIN_Y = np.array([a for a in (0, 1, 2) for _ in range(9)])

#: The held-out rows all carry class 1. Nothing here is contrived except the thing
#: being tested: a holdout that cannot support a curve is a situation that occurs
#: whenever one pan's evaluation window sits inside a single band.
HOLDOUT_X = np.array([[1.0, 1.0, 1.0], [1.0, 0.0, 1.0], [0.0, 1.0, 1.0]])
HOLDOUT_Y = np.array([1, 1, 1])

#: A two-class problem, for the cases where the ROC/PR asymmetry is observable.
TWO_CLASS_X = np.array([[float(a), float(b)] for a in (0, 1) for b in (0, 1, 2, 3)])
TWO_CLASS_Y = np.array([0, 0, 0, 0, 1, 1, 1, 1])


@pytest.fixture()
def three_class_single_class_holdout() -> object:
    """A real scoring of a real fit on a three-class holdout holding one class."""
    estimator = LogisticRegression(max_iter=200).fit(TRAIN_X, TRAIN_Y)
    return score_estimator(
        estimator,
        HOLDOUT_X,
        HOLDOUT_Y,
        classes=[0, 1, 2],
        evaluation_set="the held-out rows",
    )


# -- 1. an absent metric stays absent ------------------------------------


def test_both_curves_are_refused_when_no_class_has_both_kinds_of_row(
    three_class_single_class_holdout,
):
    """Three classes, one of them present: no class has positives *and* negatives.

    This is the case that is easy to get wrong in the other direction. A holdout of
    one class out of three leaves the present class with no negatives and the absent
    classes with no positives, so there is no one-vs-rest curve for any of them and
    neither metric exists. Reporting either one would be reporting a number derived
    from a class that never appeared.
    """
    for reading in (three_class_single_class_holdout.roc_auc, three_class_single_class_holdout.pr_auc):
        assert reading.computed is False
        assert reading.value is None
        # The reason names the actual condition, not just "n/a": a reader who wants
        # to fix it needs to know which condition failed.
        assert "positive and negative" in reading.reason


def test_the_metrics_that_do_not_need_a_curve_are_still_reported(
    three_class_single_class_holdout,
):
    """Refusing the curves must not take the rest of the report down with them.

    A module that refused everything when a curve was undefined would report less
    than it knows, and the reader could not tell the difference between "this model
    scored nothing" and "this module declined to score".
    """
    evaluation = three_class_single_class_holdout
    assert evaluation.accuracy.computed is True
    assert evaluation.f1.computed is True
    assert evaluation.balanced_accuracy.computed is True


def test_an_absent_metric_never_carries_a_zero_in_the_table(
    three_class_single_class_holdout,
):
    comparison = build_comparison(
        candidate_evaluations={"logistic_regression": three_class_single_class_holdout},
        selected_algorithm="logistic_regression",
    )
    row = comparison["rows"][0]
    for column in COMPARISON_COLUMNS:
        # The table's cells are plain dicts, because the table is persisted as a
        # document. The invariant has to hold in that stored form, not only on the
        # model object it came from.
        reading = row["metrics"][column]
        assert isinstance(reading, dict)
        assert reading["computed"] is (reading["value"] is not None)
    assert row["metrics"]["roc_auc"]["computed"] is False
    assert row["metrics"]["roc_auc"]["value"] is None
    assert row["metrics"]["roc_auc"]["reason"]


def test_one_refused_metric_does_not_make_the_rows_incomparable(
    three_class_single_class_holdout,
):
    """Comparability is about the rows, not about whether every cell has a number.

    A table that refused to show two rows because one of them is missing a curve
    would be hiding the difference that is there. The missing cell is reported per
    cell; whether the rows share a denominator is a separate question, and it has a
    separate answer.
    """
    comparison = build_comparison(
        candidate_evaluations={"logistic_regression": three_class_single_class_holdout},
        selected_algorithm="logistic_regression",
    )
    assert comparison["comparable"]["same_row_count"] is True
    assert comparison["comparable"]["same_evaluation_set"] is True
    assert comparison["comparable"]["caveats"] == []


def test_the_csv_cell_is_empty_rather_than_zero(three_class_single_class_holdout):
    comparison = build_comparison(
        candidate_evaluations={"logistic_regression": three_class_single_class_holdout},
        selected_algorithm="logistic_regression",
    )
    rows = list(csv.DictReader(io.StringIO(csv_from_comparison(comparison))))
    assert len(rows) == 1
    row = rows[0]
    # The empty cell and the reason beside it. A 0 here would be summed by whoever
    # opens the file, and a sum of refusals is a number the data never produced.
    assert row["roc_auc"] == ""
    assert row["roc_auc_computed"] == "False"
    assert row["roc_auc_reason"]
    # The measured ones are present as numbers, so the column is not simply empty.
    assert row["accuracy"] != ""
    assert row["accuracy_computed"] == "True"


def test_the_flattened_records_agree_with_the_table(
    three_class_single_class_holdout,
):
    comparison = build_comparison(
        candidate_evaluations={"logistic_regression": three_class_single_class_holdout},
        selected_algorithm="logistic_regression",
    )
    records = flatten_readings(comparison)
    assert len(records) == 1
    record = records[0]
    assert record["roc_auc"] is None
    assert record["roc_auc_computed"] is False
    assert (
        record["roc_auc_reason"]
        == comparison["rows"][0]["metrics"]["roc_auc"]["reason"]
    )


# -- 2. a measured zero is not a refusal --------------------------------


def test_one_class_splits_into_a_measurement_a_convention_and_a_refusal(
    three_class_single_class_holdout,
):
    """The sharpest instance of the distinction, on one class in one holdout.

    Class ``"0"`` never appears in the holdout, yet its three rate metrics and its
    average precision come out three different ways, and the difference is whether a
    denominator exists:

      * **precision** -- the model did predict ``"0"`` (once) and was wrong, so the
        denominator is non-empty and 0.0 is a *measured* rate. No reason needed.
      * **recall** -- the denominator is the number of rows carrying ``"0"``, which is
        zero, so there is no rate to measure. A 0.0 is still reported, but it is
        reported as a declared convention and says so in its own reason field, so a
        reader averaging it in knows it is not a rate anyone observed.
      * **average precision** -- there are no positive rows at all, so the quantity is
        undefined rather than empty. Nothing is reported, and the reason says why.

    Collapsing any two of these would be wrong in a different direction: reporting the
    recall as measured invents a rate, reporting the precision as undefined discards
    one, and reporting average precision as 0.0 invents a curve.
    """
    per_class = {
        entry.label: entry for entry in three_class_single_class_holdout.per_class
    }
    absent = per_class["0"]
    assert absent.support == 0
    # Measured: a real prediction, a real miss.
    assert absent.predicted > 0
    assert absent.precision.computed is True
    assert absent.precision.value == 0.0
    # Declared convention: empty denominator, zero reported, and named as a convention.
    assert absent.recall.computed is True
    assert absent.recall.value == 0.0
    assert "convention" in (absent.recall.reason or "")
    assert "not a measured rate" in (absent.recall.reason or "")
    # Refusal: the quantity does not exist, so nothing is reported.
    assert absent.average_precision.computed is False
    assert absent.average_precision.value is None
    assert "no positive rows" in absent.average_precision.reason


def test_a_class_never_predicted_at_all_names_its_empty_precision_denominator(
    three_class_single_class_holdout,
):
    """The other direction: nothing predicted it either, so precision has no
    denominator to divide by. The reason distinguishes the two empty denominators,
    because "no true cases" and "no predictions" are different diagnoses."""
    per_class = {
        entry.label: entry for entry in three_class_single_class_holdout.per_class
    }
    unpredicted = per_class["2"]
    assert unpredicted.support == 0
    assert unpredicted.predicted == 0
    assert unpredicted.precision.computed is True
    assert unpredicted.precision.value == 0.0
    assert "zero times" in (unpredicted.precision.reason or "")
    # And it is still not a curve.
    assert unpredicted.average_precision.computed is False


def test_a_refusal_and_a_measured_zero_are_different_readings():
    """The distinction the reading type exists for, stated as a test.

    These are what a bare float would collapse into one value.
    """
    refused = not_computed("no class was present in the holdout")
    measured_zero = computed(0.0, reason="zero_division=0")
    assert refused.value is None and measured_zero.value == 0.0
    assert refused.computed is not measured_zero.computed
    # And the convention is visible on the zero, so a reader can tell it from a
    # measured zero by the reason alone.
    assert "zero_division" in (measured_zero.reason or "")


def test_a_confusion_matrix_totals_the_evaluated_rows_on_an_integer_labelled_model():
    """A regression guard for a real defect, not a hypothetical one.

    The label space is text while a banded target arrives as integers. Handed the
    raw integers against ``["0", "1", "2"]``, ``confusion_matrix`` matches nothing and
    returns a correctly shaped matrix of zeros with no error at all -- a report
    reading as a model that never got one row right, rather than as the bug it is.
    The total is the cheapest assertion that catches it.
    """
    estimator = LogisticRegression(max_iter=200).fit(TRAIN_X, TRAIN_Y)
    evaluation = score_estimator(
        estimator,
        HOLDOUT_X,
        HOLDOUT_Y,
        classes=[0, 1, 2],
        evaluation_set="the held-out rows",
    )
    matrix = evaluation.confusion_matrix
    assert matrix is not None
    assert sum(sum(row) for row in matrix.matrix) == evaluation.evaluated_rows
    # The class that was in the holdout has a non-empty row; the others do not.
    assert sum(matrix.matrix[1]) == len(HOLDOUT_Y)
    assert sum(matrix.matrix[0]) == 0
    # And the absences are named separately from each other, because "no row carried
    # this class" and "this class was in the data but never predicted" are different
    # facts about the same model.
    assert evaluation.classes == ["0", "1", "2"]
    assert matrix.classes_absent_from_evaluation == ["2"]
    assert matrix.classes_absent_from_evaluation_rows == ["0", "2"]


def test_a_confusion_matrix_agrees_with_the_rows_whose_actual_class_it_names(
    three_class_single_class_holdout,
):
    matrix = three_class_single_class_holdout.confusion_matrix
    assert matrix is not None
    # Each row counts one true class, so a row that sums to more than the number of
    # rows carrying that class would be double-counting. The model's most confused
    # pair, if any, is off-diagonal and real.
    for index, label in enumerate(matrix.labels):
        support = sum(
            1 for value in HOLDOUT_Y if str(value) == label
        )
        assert sum(matrix.matrix[index]) == support
    if matrix.most_confused_pair is not None:
        actual, predicted = matrix.most_confused_pair["actual"], matrix.most_confused_pair["predicted"]
        assert actual != predicted
        assert matrix.most_confused_pair["count"] == matrix.matrix[
            matrix.labels.index(actual)
        ][matrix.labels.index(predicted)]


# -- 3. the ROC/PR asymmetry --------------------------------------------


def test_average_precision_survives_a_single_class_holdout_where_roc_does_not():
    """Two classes, one of them present: the positive class still has positives.

    Average precision needs positives and does not need negatives, so it stays
    defined on a slice ROC-AUC cannot describe. Refusing both would hide a real
    measurement.
    """
    estimator = LogisticRegression(max_iter=200).fit(TWO_CLASS_X, TWO_CLASS_Y)
    evaluation = score_estimator(
        estimator,
        TWO_CLASS_X[:4],
        np.asarray([1, 1, 1, 1]),
        classes=[0, 1],
        evaluation_set="the held-out rows",
    )
    assert evaluation.roc_auc.computed is False
    assert evaluation.roc_auc.value is None
    assert "one class" in evaluation.roc_auc.reason
    assert evaluation.pr_auc.computed is True
    assert 0.0 <= evaluation.pr_auc.value <= 1.0


def test_every_estimator_agrees_about_what_is_measurable_on_one_holdout():
    """If one estimator could produce a curve here and another could not, the
    refusal would be an artefact of the estimator rather than a property of the data.
    It is a property of the data, so all of them must refuse it."""
    for estimator in (
        LogisticRegression(max_iter=200).fit(TRAIN_X, TRAIN_Y),
        DecisionTreeClassifier(random_state=0).fit(TRAIN_X, TRAIN_Y),
        RandomForestClassifier(n_estimators=10, random_state=0).fit(TRAIN_X, TRAIN_Y),
    ):
        evaluation = score_estimator(
            estimator,
            HOLDOUT_X,
            HOLDOUT_Y,
            classes=[0, 1, 2],
            evaluation_set="the held-out rows",
        )
        assert evaluation.roc_auc.computed is False
        assert evaluation.roc_auc.value is None
        assert evaluation.pr_auc.computed is False


# -- a partly absent row still belongs in the table ---------------------


def test_a_candidate_missing_its_ranking_metrics_is_shown_beside_the_others():
    """A row is not dropped for being partly absent, and it is not ranked as if whole.

    Two candidates scored on the *same* rows, one of which was fitted without
    ``predict_proba``. The second has no ROC-AUC and no PR-AUC, because a curve needs
    scores to rank and it has none to give -- while its accuracy and F1 are measured
    like anyone else's. Dropping the row would hide a candidate that was in fact
    fitted and scored; ranking it as though its missing cells were zeros would put it
    below models that were genuinely worse.

    Same rows for both, so the shared denominator is real and the only difference
    between the rows is the missing metrics.
    """
    rows_X = np.vstack([TRAIN_X[:3], HOLDOUT_X])
    rows_Y = np.array([0, 0, 0, 1, 1, 1])
    with_probabilities = score_estimator(
        LogisticRegression(max_iter=200).fit(TRAIN_X, TRAIN_Y),
        rows_X,
        rows_Y,
        classes=[0, 1, 2],
        evaluation_set="held-out rows",
    )
    without_probabilities = score_estimator(
        LinearSVC().fit(TRAIN_X, TRAIN_Y),
        rows_X,
        rows_Y,
        classes=[0, 1, 2],
        evaluation_set="held-out rows",
    )
    assert without_probabilities.roc_auc.computed is False
    assert without_probabilities.pr_auc.computed is False
    assert without_probabilities.accuracy.computed is True

    comparison = build_comparison(
        candidate_evaluations={
            "logistic_regression": with_probabilities,
            "decision_tree": without_probabilities,
        },
        selected_algorithm="logistic_regression",
    )
    assert [row["model"] for row in comparison["rows"]] == [
        "logistic_regression",
        "decision_tree",
    ]
    degraded = comparison["rows"][1]
    assert degraded["metrics"]["roc_auc"]["computed"] is False
    assert degraded["metrics"]["pr_auc"]["computed"] is False
    assert degraded["metrics"]["accuracy"]["computed"] is True
    # The refusal is discoverable from the row itself, not only by fetching the
    # detail view and comparing it against this table.
    assert degraded["metrics"]["roc_auc"]["reason"]
    # The rows share a denominator, so the table says so and remains a comparison.
    assert comparison["comparable"]["same_row_count"] is True
    assert comparison["comparable"]["same_evaluation_set"] is True
