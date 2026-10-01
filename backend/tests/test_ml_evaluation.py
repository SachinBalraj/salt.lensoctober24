"""Module 09 - what the metric layer is forbidden from doing.

Each test here corresponds to a way a performance figure can be produced that is
technically computable and materially misleading. The suite is weighted towards the
negative cases on purpose: the honest paths are easy to get right by accident, and a
metric library is judged by what it refuses to say.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier

from app.ml.cross_validation import (
    BLOCKED_TEMPORAL,
    LEAVE_PANS_OUT,
    STRATIFIED_KFOLD,
    _blocked_temporal_folds,
    aggregate_folds,
    plan_cross_validation,
    run_cross_validation,
)
from app.ml.evaluation import align_probabilities, evaluate_classifier, score_estimator
from app.models.evaluation import (
    ClassificationEvaluation,
    CrossValidationFold,
    MetricReading,
    not_computed,
)


def _binary_problem(rows: int = 120, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    features = rng.normal(size=(rows, 3))
    labels = np.asarray(["no", "yes"])[(features[:, 0] > 0).astype(int)]
    return features, labels


def _multiclass_problem(rows: int = 180, seed: int = 1) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    features = rng.normal(size=(rows, 3))
    labels = np.asarray(["low", "mid", "high"])[
        np.clip(((features[:, 0] + 1.5) * 1.2).astype(int), 0, 2)
    ]
    return features, labels


class TestMetricReadingCannotLie:
    def test_a_value_without_computed_true_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            MetricReading(value=0.91, computed=False, reason="present")

    def test_computed_true_with_no_value_is_rejected(self) -> None:
        # The one combination that would let a caller publish a number that does not
        # exist, by asserting it was computed while supplying nothing to compute.
        with pytest.raises(ValidationError):
            MetricReading(value=None, computed=True, reason=None)

    def test_an_absent_metric_must_say_why(self) -> None:
        with pytest.raises(ValidationError):
            MetricReading(value=None, computed=False, reason=None)

    def test_zero_is_a_value_and_is_accepted(self) -> None:
        # 0.0 is a measurement. Treating it as "missing" would erase the most
        # informative result a classifier can produce.
        assert MetricReading(value=0.0, computed=True, reason="nothing was right").value == 0.0

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_values_are_rejected(self, value: float) -> None:
        with pytest.raises(ValidationError):
            MetricReading(value=value, computed=True, reason=None)

    def test_no_blanket_unit_interval_bound_is_imposed(self) -> None:
        # Deliberate, and the reason is log_loss: cross-entropy on a confidently
        # wrong row is 4.7, not 1.4. A blanket `le=1` guard here would reject a
        # correct figure and tempt a caller into clamping it. Individual metrics are
        # bounded by construction in app.ml.evaluation instead.
        assert MetricReading(value=4.7, computed=True, reason="a bad row").value == 4.7

    def test_a_negative_value_is_still_accepted_for_the_metrics_that_allow_one(self) -> None:
        # Also deliberate. Matthews correlation runs from -1 to 1, and a model worse
        # than random on a two-class problem scores about -0.3. A `ge=0` guard would
        # reject that correct figure. Any real bound belongs with the metric that
        # needs it, not on the shared envelope.
        assert MetricReading(value=-0.31, computed=True, reason=None).value == -0.31


class TestBinaryProbabilityAlignment:
    def test_one_probability_column_becomes_the_positive_class(self) -> None:
        # A binary classifier that reports one column is reporting P(classes[1]).
        # Column 0 must be its *complement*, not zero: a row that does not sum to
        # one gets renormalised by scikit-learn's log_loss into [0, 1], which reads
        # as a flawless confidence score.
        class Dummy:
            def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
                return np.asarray([[0.8], [0.35], [0.6], [0.05]])

        aligned = align_probabilities(Dummy(), np.zeros((4, 2)), ["no", "yes"])
        assert aligned.shape == (4, 2)
        np.testing.assert_allclose(aligned[:, 1], [0.8, 0.35, 0.6, 0.05])
        np.testing.assert_allclose(aligned[:, 0], [0.2, 0.65, 0.4, 0.95])
        np.testing.assert_allclose(aligned.sum(axis=1), 1.0)

    def test_a_single_class_model_keeps_its_own_probability(self) -> None:
        class Dummy:
            def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
                return np.asarray([[0.4], [0.9]])

        aligned = align_probabilities(Dummy(), np.zeros((2, 1)), ["only"])
        np.testing.assert_allclose(aligned[:, 0], [0.4, 0.9])

    def test_out_of_range_probabilities_are_clipped_not_wrapped(self) -> None:
        class Dummy:
            def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
                return np.asarray([[1.4], [-0.2]])

        aligned = align_probabilities(Dummy(), np.zeros((2, 2)), ["no", "yes"])
        np.testing.assert_allclose(aligned[:, 1], [1.0, 0.0])
        np.testing.assert_allclose(aligned.sum(axis=1), 1.0)

    def test_a_column_count_that_matches_the_label_space_is_kept(self) -> None:
        class Dummy:
            def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
                return np.tile(np.asarray([[0.2, 0.3, 0.5]]), (len(matrix), 1))

        aligned = align_probabilities(Dummy(), np.zeros((2, 3)), ["a", "b", "c"])
        np.testing.assert_allclose(aligned[0], [0.2, 0.3, 0.5])

    def test_a_mismatched_column_count_is_refused_rather_than_guessed(self) -> None:
        class Dummy:
            def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
                return np.zeros((len(matrix), 5))

        with pytest.raises(ValueError, match="cannot be aligned"):
            align_probabilities(Dummy(), np.zeros((3, 3)), ["a", "b", "c"])


class TestBinaryCurves:
    def test_a_perfectly_separable_problem_scores_one(self) -> None:
        # Genuinely separable, not merely "two Gaussian blobs near a threshold":
        # a margin of 6 standard deviations is what makes 1.0 the true answer.
        features = np.asarray([[-3.0], [3.0]] * 30)
        labels = np.asarray(["no", "yes"] * 30)
        model = LogisticRegression().fit(features, labels)
        report = score_estimator(
            model, features, labels, np.unique(labels), "the held-out rows"
        )
        assert report.roc_auc.computed
        assert report.roc_auc.value == pytest.approx(1.0)
        assert report.roc_auc.variant == "binary_positive_class"
        assert report.accuracy.value == pytest.approx(1.0)

    def test_roc_auc_is_refused_when_the_evaluated_rows_hold_one_class(self) -> None:
        # Reporting 0.5 here would be reporting the score of a coin as if it were a
        # property of the model.
        features, labels = _binary_problem()
        model = LogisticRegression().fit(features, labels)
        report = score_estimator(
            model,
            features[:12],
            np.asarray(["yes"] * 12),
            np.unique(labels),
            "the held-out rows",
        )
        assert not report.roc_auc.computed
        assert report.roc_auc.value is None
        assert "one class" in report.roc_auc.reason

    def test_average_precision_survives_a_single_class_where_roc_auc_does_not(self) -> None:
        # The asymmetry is the point: average precision needs only positives, so it
        # stays defined on a slice ROC-AUC cannot describe.
        features, labels = _binary_problem()
        model = LogisticRegression().fit(features, labels)
        report = score_estimator(
            model,
            features[:12],
            np.asarray(["yes"] * 12),
            np.unique(labels),
            "the held-out rows",
        )
        assert not report.roc_auc.computed
        assert report.pr_auc.computed
        assert 0.0 <= report.pr_auc.value <= 1.0

    def test_a_model_without_probabilities_loses_the_ranking_metrics(self) -> None:
        features, labels = _binary_problem()
        model = LinearSVC().fit(features, labels)
        report = score_estimator(
            model, features, labels, np.unique(labels), "the held-out rows"
        )
        assert report.accuracy.computed
        assert not report.roc_auc.computed
        assert not report.pr_auc.computed
        assert not report.brier_score.computed
        assert "probabilit" in report.roc_auc.reason


class TestMulticlassCurves:
    def test_curves_are_labelled_one_vs_rest(self) -> None:
        features, labels = _multiclass_problem()
        model = RandomForestClassifier(n_estimators=20, random_state=0).fit(
            features, labels
        )
        report = score_estimator(
            model, features, labels, np.unique(labels), "the held-out rows"
        )
        assert len(report.classes) == 3
        assert report.roc_auc.variant == "one_vs_rest_macro"
        assert any("one-vs-rest" in note for note in report.notes)

    def test_per_class_average_precision_uses_its_own_probability_column(self) -> None:
        # A per-class curve computed against the wrong column still produces a number
        # in [0, 1]. Only a hand-checked expectation catches it, so the expectation is
        # computed here from the estimator's own probability matrix rather than
        # asserted as a range.
        features, labels = _multiclass_problem()
        model = DecisionTreeClassifier(max_depth=3, random_state=0).fit(
            features, labels
        )
        report = score_estimator(
            model, features, labels, np.unique(labels), "the held-out rows"
        )
        probabilities = model.predict_proba(features)
        for index, class_score in enumerate(report.per_class):
            assert class_score.average_precision.computed
            # Column order follows model.classes_, which is the sorted label space
            # here, and report.per_class is emitted in that same order.
            assert class_score.label == str(model.classes_[index])
            # Recompute this class's curve from the estimator's own column. Comparing
            # against the exact figure is what distinguishes "used the right column"
            # from "returned some number in range": a wrong column is off by a
            # different amount for each class, and no range assertion would see it.
            expected = average_precision_score(
                labels == model.classes_[index], probabilities[:, index]
            )
            assert class_score.average_precision.value == pytest.approx(expected)

    def test_every_class_in_the_label_space_appears_even_if_never_predicted(self) -> None:
        # A class the model never predicts still occupies a row and column of the
        # confusion matrix. Omitting it would quietly shrink the label space and make
        # the matrix disagree with the macro figures computed beside it.
        features, labels = _multiclass_problem()
        model = DecisionTreeClassifier(max_depth=1, random_state=0).fit(features, labels)
        report = score_estimator(
            model, features, labels, np.unique(labels), "the held-out rows"
        )
        assert [entry.label for entry in report.per_class] == sorted(set(labels))
        assert report.confusion_matrix is not None
        assert report.confusion_matrix.labels == sorted(set(labels))
        assert len(report.confusion_matrix.matrix) == 3


class TestDegenerateInputs:
    def test_no_rows_computes_nothing(self) -> None:
        report = evaluate_classifier(
            y_true=np.asarray([], dtype=object),
            y_pred=np.asarray([], dtype=object),
            probabilities=None,
            classes=["a", "b"],
            evaluation_set="nothing",
        )
        assert not report.accuracy.computed
        assert report.confusion_matrix is None

    def test_an_unnamed_evaluation_set_is_refused(self) -> None:
        # "Accuracy 0.87" with no stated denominator is the figure this module exists
        # to prevent.
        with pytest.raises(ValueError, match="denominator"):
            evaluate_classifier(
                y_true=np.asarray(["a", "b"]),
                y_pred=np.asarray(["a", "b"]),
                probabilities=None,
                classes=["a", "b"],
                evaluation_set="   ",
            )

    def test_a_constant_predictor_is_reported_as_such(self) -> None:
        features, labels = _binary_problem()
        model = DummyClassifier(strategy="most_frequent").fit(features, labels)
        report = score_estimator(
            model, features, labels, np.unique(labels), "the held-out rows"
        )
        assert report.accuracy.computed
        # A constant predictor must not score 0.5 on ROC-AUC by accident.
        assert report.roc_auc.computed
        assert report.roc_auc.value == pytest.approx(0.5)

    def test_macro_figures_equal_the_mean_of_the_per_class_figures(self) -> None:
        # The two are computed by different code paths, so this is the check that
        # keeps the comparison table from disagreeing with the class table beneath it.
        features, labels = _multiclass_problem()
        model = DecisionTreeClassifier(max_depth=3, random_state=0).fit(
            features, labels
        )
        report = score_estimator(
            model, features, labels, np.unique(labels), "the held-out rows"
        )
        for metric in ("precision", "recall", "f1"):
            headline = getattr(report, metric).value
            per_class = [getattr(entry, metric).value for entry in report.per_class]
            assert headline == pytest.approx(float(np.mean(per_class)))

    def test_per_class_figures_sum_to_the_confusion_matrix(self) -> None:
        features, labels = _multiclass_problem()
        model = RandomForestClassifier(n_estimators=10, random_state=0).fit(
            features, labels
        )
        report = score_estimator(
            model, features, labels, np.unique(labels), "the held-out rows"
        )
        matrix = np.asarray(report.confusion_matrix.matrix)
        for index, class_score in enumerate(report.per_class):
            assert class_score.support == int(matrix[:, index].sum())
            assert class_score.predicted == int(matrix[index, :].sum())


class TestZeroDivisionIsLabelled:
    def test_a_class_that_is_never_predicted_reports_zero_precision_with_a_reason(self) -> None:
        features, labels = _multiclass_problem()
        model = DecisionTreeClassifier(max_depth=1, random_state=0).fit(features, labels)
        report = score_estimator(
            model, features, labels, np.unique(labels), "the held-out rows"
        )
        for class_score in report.per_class:
            if class_score.predicted == 0:
                assert class_score.precision.value == 0.0
                assert class_score.precision.reason is not None
                assert "convention" in class_score.precision.reason

    def test_a_class_absent_from_the_evaluation_rows_reports_zero_recall_with_a_reason(
        self,
    ) -> None:
        features, labels = _multiclass_problem()
        model = DecisionTreeClassifier(max_depth=2, random_state=0).fit(
            features, labels
        )
        held = np.asarray([value for value in labels if value != "high"])
        report = score_estimator(
            model,
            features[: len(held)],
            held,
            np.unique(labels),
            "the held-out rows",
        )
        high = next(entry for entry in report.per_class if entry.label == "high")
        assert high.support == 0
        assert high.recall.value == 0.0
        assert high.recall.reason is not None
        assert "high" in report.confusion_matrix.classes_absent_from_evaluation_rows


class TestCrossValidationDesign:
    def test_the_design_follows_the_split_strategy(self) -> None:
        counts = {"a": 200, "b": 200}
        assert plan_cross_validation(
            split_strategy="random", labelled_rows=400,
            class_counts_by_class=counts, distinct_pans=9,
        ).method == STRATIFIED_KFOLD
        assert plan_cross_validation(
            split_strategy="grouped_by_pan", labelled_rows=400,
            class_counts_by_class=counts, distinct_pans=9,
        ).method == LEAVE_PANS_OUT
        for strategy in ("chronological", "per_pan_chronological"):
            assert plan_cross_validation(
                split_strategy=strategy, labelled_rows=400,
                class_counts_by_class=counts, distinct_pans=9,
            ).method == BLOCKED_TEMPORAL

    def test_an_unknown_strategy_is_refused_rather_than_defaulted(self) -> None:
        # Defaulting would silently answer a question about unseen rows when the
        # caller asked one about unseen pans.
        with pytest.raises(ValueError, match="Unknown split strategy"):
            plan_cross_validation(
                split_strategy="by_vibes", labelled_rows=400,
                class_counts_by_class={"a": 200}, distinct_pans=9,
            )

    def test_blocked_temporal_never_trains_on_a_later_row(self) -> None:
        for rows, splits in ((20, 3), (12, 2), (13, 5)):
            for train, test in _blocked_temporal_folds(np.arange(rows), splits):
                assert len(test) > 0
                assert not set(train.tolist()) & set(test.tolist())
                assert len(train) == len(set(train.tolist()))
                assert train.max() < test.min()

    def test_blocked_temporal_window_expands(self) -> None:
        sizes = [
            len(train)
            for train, _ in _blocked_temporal_folds(np.arange(20), 3)
        ]
        assert sizes == sorted(sizes)
        assert sizes[0] > 0, "the first fold must not train on an empty window"


class TestCrossValidationRefusal:
    def test_too_little_data_is_refused_with_the_counts(self) -> None:
        plan = plan_cross_validation(
            split_strategy="random", labelled_rows=20,
            class_counts_by_class={"a": 10, "b": 10}, distinct_pans=3,
        )
        assert not plan.performed
        assert any("20 labelled row" in reason for reason in plan.reasons)

    def test_a_single_fold_is_refused(self) -> None:
        # One fold produces a number wearing the label of a distribution.
        plan = plan_cross_validation(
            split_strategy="random", labelled_rows=400,
            class_counts_by_class={"a": 200, "b": 200}, distinct_pans=9,
            requested_splits=1,
        )
        assert not plan.performed
        assert any("at least 2" in reason for reason in plan.reasons)

    def test_more_folds_than_rare_class_rows_is_refused(self) -> None:
        plan = plan_cross_validation(
            split_strategy="random", labelled_rows=400,
            class_counts_by_class={"a": 380, "b": 4}, distinct_pans=9,
            requested_splits=5,
        )
        assert not plan.performed
        assert any("rarest class" in reason for reason in plan.reasons)

    def test_folds_too_small_to_mean_anything_are_refused(self) -> None:
        plan = plan_cross_validation(
            split_strategy="chronological", labelled_rows=100,
            class_counts_by_class={"a": 50, "b": 50}, distinct_pans=9,
        )
        assert not plan.performed

    def test_a_refusal_is_reported_not_raised(self) -> None:
        frame = pd.DataFrame(
            {
                "pan_id": ["P1"] * 10,
                "anchor_timestamp": pd.date_range("2023-01-01", periods=10, freq="D"),
                "f1": np.arange(10, dtype=float),
            }
        )
        report = run_cross_validation(
            frame=frame,
            labels=np.asarray(["a", "b"] * 5, dtype=object),
            feature_names=["f1"],
            feature_kinds={"f1": "numeric"},
            split_strategy="random",
            random_seed=1,
        )
        assert not report.performed
        assert report.reason is not None
        assert report.method is not None, "the declined design should still be named"
        assert report.folds == []


def _stub_evaluation(f1: MetricReading) -> ClassificationEvaluation:
    """A complete evaluation with only the f1 reading varying.

    Every metric is required, and a fold report that omitted the unused ones would
    make these aggregation tests depend on which fields the aggregate happens to
    read -- so the rest are filled with a real reading.
    """
    filled: dict[str, object] = {
        "evaluation_set": "a fold",
        "evaluated_rows": 20,
        "classes": ["a", "b"],
        "class_distribution": {"a": 10, "b": 10},
        "accuracy": {"value": 0.8, "computed": True, "reason": None},
        "f1": f1.model_dump(mode="json"),
    }
    for name in (
        "balanced_accuracy",
        "precision",
        "precision_weighted",
        "recall",
        "recall_weighted",
        "f1_weighted",
        "matthews_corrcoef",
        "roc_auc",
        "pr_auc",
        "brier_score",
        "log_loss",
    ):
        filled[name] = {"value": 0.75, "computed": True, "reason": None}
    return ClassificationEvaluation.model_validate(filled)


class TestFoldAggregation:
    def test_a_metric_missing_from_a_fold_is_named_not_averaged_away(self) -> None:
        def fold(number: int, computable: bool) -> CrossValidationFold:
            reading = (
                MetricReading(value=0.75, computed=True, reason=None)
                if computable
                else not_computed("the fold held one class only")
            )
            return CrossValidationFold(
                fold=number,
                train_rows=40,
                test_rows=20,
                evaluation=_stub_evaluation(reading),
            )

        aggregate = aggregate_folds([fold(1, True), fold(2, False), fold(3, False)])["f1"]
        assert aggregate.folds_aggregated == 1
        assert aggregate.folds_omitted == [2, 3]
        assert aggregate.mean.reason is not None
        # The shortfall is stated on the mean itself, so a reader who sees only the
        # mean still learns it is not a three-fold mean.
        assert "1 of 3" in aggregate.mean.reason
        assert "2 of 3" not in aggregate.mean.reason

    def test_one_fold_gives_no_standard_deviation(self) -> None:
        features, labels = _binary_problem()
        model = DecisionTreeClassifier(random_state=0).fit(features, labels)
        evaluation = score_estimator(
            model, features[:20], labels[:20], np.unique(labels), "fold 1 of 1"
        )
        aggregate = aggregate_folds(
            [CrossValidationFold(fold=1, train_rows=40, test_rows=20, evaluation=evaluation)]
        )["f1"]
        assert not aggregate.standard_deviation.computed
        assert aggregate.standard_deviation.reason is not None

    def test_identical_folds_give_a_zero_spread(self) -> None:
        features, labels = _binary_problem()
        model = DecisionTreeClassifier(random_state=0).fit(features, labels)
        evaluation = score_estimator(
            model, features[:20], labels[:20], np.unique(labels), "a fold"
        )
        fold = CrossValidationFold(
            fold=1, train_rows=40, test_rows=20, evaluation=evaluation
        )
        aggregate = aggregate_folds([fold, fold.model_copy(deep=True)])["f1"]
        assert aggregate.standard_deviation.computed
        assert aggregate.standard_deviation.value == pytest.approx(0.0)


def _pan_frame(pans: int, rows: int, seed: int = 5) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    records = []
    clock = 0
    for pan in range(pans):
        for _ in range(rows):
            records.append(
                {
                    "pan_id": f"PAN{pan:02d}",
                    "anchor_timestamp": pd.Timestamp("2023-01-01")
                    + pd.Timedelta(hours=clock),
                    "f1": rng.normal(),
                    "f2": rng.normal(),
                }
            )
            clock += 1
    frame = pd.DataFrame(records)
    labels = np.where(frame["f1"] + rng.normal(scale=0.4, size=len(frame)) > 0, "up", "down")
    return frame, labels.astype(object)


class TestCrossValidationExecution:
    def test_leave_pans_out_never_splits_a_pan_across_folds(self) -> None:
        frame, labels = _pan_frame(pans=8, rows=25)
        report = run_cross_validation(
            frame=frame, labels=labels, feature_names=["f1", "f2"],
            feature_kinds={"f1": "numeric", "f2": "numeric"},
            split_strategy="grouped_by_pan", random_seed=2,
        )
        assert report.performed
        assert report.evaluates_unseen_pans is True
        for entry in report.per_algorithm.values():
            for fold in entry.folds:
                assert not set(fold.train_pans) & set(fold.test_pans)
        covered = set()
        for entry in report.per_algorithm.values():
            for fold in entry.folds:
                covered |= set(fold.test_pans)
        assert covered == set(frame["pan_id"]), "every pan should be tested exactly once"

    def test_temporal_folds_are_ordered_in_time(self) -> None:
        frame, labels = _pan_frame(pans=4, rows=40)
        report = run_cross_validation(
            frame=frame, labels=labels, feature_names=["f1", "f2"],
            feature_kinds={"f1": "numeric", "f2": "numeric"},
            split_strategy="chronological", random_seed=2,
        )
        assert report.performed
        assert report.shuffled is False
        for entry in report.per_algorithm.values():
            for fold in entry.folds:
                assert fold.train_time_range["latest"] <= fold.test_time_range["earliest"]

    def test_random_forest_is_scored_but_never_auto_selectable(self) -> None:
        frame, labels = _pan_frame(pans=6, rows=30)
        report = run_cross_validation(
            frame=frame, labels=labels, feature_names=["f1", "f2"],
            feature_kinds={"f1": "numeric", "f2": "numeric"},
            split_strategy="random", random_seed=2,
        )
        forest = report.per_algorithm["random_forest"]
        assert forest.folds, "the forest should still be fitted and scored"
        assert not forest.auto_selectable
        assert forest.selection_note is not None

    def test_every_candidate_reports_every_fold(self) -> None:
        frame, labels = _pan_frame(pans=5, rows=30)
        report = run_cross_validation(
            frame=frame, labels=labels, feature_names=["f1", "f2"],
            feature_kinds={"f1": "numeric", "f2": "numeric"},
            split_strategy="random", random_seed=2,
        )
        assert len(report.per_algorithm) == 4
        for entry in report.per_algorithm.values():
            assert len(entry.folds) == report.n_splits

    def test_the_design_chosen_matches_the_requested_strategy(self) -> None:
        frame, labels = _pan_frame(pans=6, rows=30)
        for strategy, method in (
            ("random", STRATIFIED_KFOLD),
            ("grouped_by_pan", LEAVE_PANS_OUT),
            ("chronological", BLOCKED_TEMPORAL),
            ("per_pan_chronological", BLOCKED_TEMPORAL),
        ):
            report = run_cross_validation(
                frame=frame, labels=labels, feature_names=["f1", "f2"],
                feature_kinds={"f1": "numeric", "f2": "numeric"},
                split_strategy=strategy, random_seed=2,
            )
            assert report.method == method
            assert report.matched_split_strategy == strategy
            assert report.rationale is not None
