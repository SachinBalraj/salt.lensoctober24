"""Module 8 - the model candidates, and the rule for choosing between them.

Four candidates are fitted on every eligible run, and the one that is reported as
selected is never Random Forest. That exclusion is a decision, so it is stated
here rather than left to a hyper-parameter default.

**Why Random Forest is excluded from automatic selection.** An ensemble of deep
trees on a few hundred pan observations will usually win a held-out score. It
will not usually have learned anything transferable: the split points are chosen
to separate the particular rows it was given, and a threshold between two nearly
identical salinity readings is a fact about this dataset, not about salinity.
It is included as a benchmark precisely because seeing it win is informative -- it
tells you the data is probably too small for a model to generalise, and that the
gap between the simple and complex models is the number to watch. Making it the
automatic pick would convert that warning into a deployment, and would reward the
model that memorises hardest. It stays a candidate, its score stays in the
comparison, and the caller can promote it deliberately with
``allow_random_forest=True``, which is recorded on the run as a deliberate act
rather than a default.

**Every candidate sees the same preprocessed matrix.** The preprocessing is
fitted once, on the training rows, and shared. Four candidates fitted to four
different encodings would produce a comparison that partly measured the
preprocessing rather than the model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.base import ClassifierMixin
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    brier_score_loss,
    f1_score,
    log_loss,
    matthews_corrcoef,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.tree import DecisionTreeClassifier

from app.ml.preprocessing import library_versions

LOGISTIC_REGRESSION = "logistic_regression"
DECISION_TREE = "decision_tree"
RANDOM_FOREST = "random_forest"
GRADIENT_BOOSTING = "gradient_boosting"

#: Every algorithm a run may fit, in the order they are reported.
CANDIDATE_ALGORITHMS: tuple[str, ...] = (
    LOGISTIC_REGRESSION,
    DECISION_TREE,
    RANDOM_FOREST,
    GRADIENT_BOOSTING,
)

#: Never chosen automatically. See the module docstring for the reasoning.
NOT_AUTO_SELECTABLE: frozenset[str] = frozenset({RANDOM_FOREST})


@dataclass(frozen=True)
class CandidateSpec:
    """One algorithm and the exact hyper-parameters it will be fitted with.

    Hyper-parameters are fixed rather than searched. A grid search over a few
    hundred rows finds the most favourable draw from the noise in the data, and
    the winning number is then a property of the search rather than of the
    dataset. Fixing them also means two runs of the same dataset and seed produce
    identical models, which is the only way "reproducible" can be checked.
    """

    algorithm: str
    parameters: dict[str, Any]

    def build(self, random_seed: int) -> ClassifierMixin:
        if self.algorithm == LOGISTIC_REGRESSION:
            return LogisticRegression(
                max_iter=2000,
                # lbfgs with these limits; without max_iter a separable small
                # dataset converges slowly and warns instead of failing.
                C=1.0,
                solver="lbfgs",
                random_state=random_seed,
            )
        if self.algorithm == DECISION_TREE:
            return DecisionTreeClassifier(
                # Shallow on purpose. Depth is the main thing that turns a tree
                # into a memoriser, and the ceiling is where the model is allowed
                # to look for a genuine interaction.
                max_depth=4,
                min_samples_leaf=5,
                criterion="gini",
                random_state=random_seed,
            )
        if self.algorithm == RANDOM_FOREST:
            return RandomForestClassifier(
                n_estimators=200,
                max_depth=8,
                min_samples_leaf=5,
                class_weight="balanced_subsample",
                n_jobs=1,
                random_state=random_seed,
            )
        if self.algorithm == GRADIENT_BOOSTING:
            return GradientBoostingClassifier(
                n_estimators=100,
                max_depth=3,
                learning_rate=0.1,
                random_state=random_seed,
            )
        raise ValueError(
            f"Unknown algorithm {self.algorithm!r}. A name not in the catalogue is "
            "not run: an unrecognised estimator would train and report metrics "
            "while contributing nothing anyone could check."
        )


def candidate_specs() -> tuple[CandidateSpec, ...]:
    """The four candidates, with their fixed hyper-parameters."""
    return (
        CandidateSpec(
            algorithm=LOGISTIC_REGRESSION,
            parameters={
                "C": 1.0,
                "solver": "lbfgs",
                "max_iter": 2000,
            },
        ),
        CandidateSpec(
            algorithm=DECISION_TREE,
            parameters={
                "max_depth": 4,
                "min_samples_leaf": 5,
                "criterion": "gini",
            },
        ),
        CandidateSpec(
            algorithm=RANDOM_FOREST,
            parameters={
                "n_estimators": 200,
                "max_depth": 8,
                "min_samples_leaf": 5,
                "class_weight": "balanced_subsample",
                "n_jobs": 1,
            },
        ),
        CandidateSpec(
            algorithm=GRADIENT_BOOSTING,
            parameters={
                "n_estimators": 100,
                "max_depth": 3,
                "learning_rate": 0.1,
            },
        ),
    )


def _probabilities(
    model: ClassifierMixin, matrix: np.ndarray, classes: np.ndarray
) -> np.ndarray:
    """Class probabilities aligned to the training classes, in a stable order.

    A model is asked for probabilities in the class order it was trained on. A
    binary classifier returns one column for the positive class only, so
    reconstructing the full two-column form here means every downstream metric
    reads the same ordering, and a two-class and a three-class run stay
    comparable.
    """
    raw = model.predict_proba(matrix)
    if raw.shape[1] == len(classes):
        return raw
    positive = np.zeros((raw.shape[0], len(classes)))
    positive[:, 1] = raw[:, 0]
    return positive


def evaluate(
    model: ClassifierMixin,
    matrix: np.ndarray,
    labels: np.ndarray,
    classes: np.ndarray,
) -> dict[str, float]:
    """Score a fitted candidate on held-out rows.

    Macro-averaged metrics are the default because the class balance in these
    datasets is rarely what anyone would choose. Accuracy on a dataset where 90%
    of rows are one class reads 0.90 for a model that has learned to say that one
    class, so it is reported but not selected on. The balanced measures are the
    ones that move when the minority class is actually learned.
    """
    predictions = model.predict(matrix)
    probabilities = _probabilities(model, matrix, classes)
    scores: dict[str, float] = {
        "accuracy": float(accuracy_score(labels, predictions)),
        "balanced_accuracy": float(balanced_accuracy_score(labels, predictions)),
        "f1_macro": float(f1_score(labels, predictions, average="macro", zero_division=0)),
        "precision_macro": float(
            precision_score(labels, predictions, average="macro", zero_division=0)
        ),
        "recall_macro": float(
            recall_score(labels, predictions, average="macro", zero_division=0)
        ),
        "matthews_corrcoef": float(matthews_corrcoef(labels, predictions)),
    }
    # Brier and log loss need probabilities; a model that cannot produce them is
    # not asked to report a calibration score it did not earn.
    try:
        brier = brier_score_loss(labels, probabilities, labels=list(classes))
    except ValueError:
        brier = None
    if brier is not None:
        scores["brier_score"] = float(brier)
    try:
        scores["log_loss"] = float(
            log_loss(labels, probabilities, labels=list(classes))
        )
    except ValueError:
        pass
    # ROC-AUC is only defined where there is at least one positive and one
    # negative example. A held-out split with a single class reports no AUC
    # rather than a confident 0.5.
    if len(classes) == 2 and len(np.unique(labels)) == 2:
        scores["roc_auc"] = float(roc_auc_score(labels, probabilities[:, 1]))
    return scores


def select_model(
    scored: dict[str, dict[str, float]], allow_random_forest: bool = False
) -> tuple[str | None, str]:
    """Choose the reported model, and say why.

    Selection is on macro F1. It is the measure that penalises a model for
    ignoring a minority class, which is the failure this system is most exposed
    to, and it is defined for any number of classes.

    Ties break on balanced accuracy, then on the candidate's order in
    ``CANDIDATE_ALGORITHMS``, so the choice is a function of the scores alone
    rather than of dictionary order. Random Forest is excluded unless explicitly
    allowed, in which case the run records that it was allowed rather than
    treating its selection as the default path.
    """
    if allow_random_forest:
        eligible = [
            algorithm for algorithm in CANDIDATE_ALGORITHMS if algorithm in scored
        ]
    else:
        eligible = [
            algorithm
            for algorithm in CANDIDATE_ALGORITHMS
            if algorithm in scored and algorithm not in NOT_AUTO_SELECTABLE
        ]
        if not eligible:
            return None, (
                "Only Random Forest produced a score, and Random Forest is never "
                "selected automatically. Re-run with allow_random_forest=True to "
                "promote it deliberately."
            )

    def sort_key(algorithm: str) -> tuple[float, float, int]:
        metrics = scored[algorithm]
        return (
            -metrics.get("f1_macro", 0.0),
            -metrics.get("balanced_accuracy", 0.0),
            CANDIDATE_ALGORITHMS.index(algorithm),
        )

    # min rather than sorted()[0]: the sort key already carries the full
    # tie-break chain, so this returns the same winner without building the list.
    best = min(eligible, key=sort_key)
    if best == RANDOM_FOREST:
        reason = (
            "Random Forest scored highest and was explicitly permitted for this "
            "run. It is not selected automatically."
        )
    else:
        withheld = (
            f"{RANDOM_FOREST} was withheld from selection"
            if RANDOM_FOREST in scored
            else f"{RANDOM_FOREST} did not produce a score"
        )
        reason = (
            f"{best} had the highest macro F1 among the automatically selectable "
            f"candidates ({withheld})."
        )
    return best, reason


__all__ = [
    "CANDIDATE_ALGORITHMS",
    "DECISION_TREE",
    "GRADIENT_BOOSTING",
    "LOGISTIC_REGRESSION",
    "NOT_AUTO_SELECTABLE",
    "RANDOM_FOREST",
    "CandidateSpec",
    "candidate_specs",
    "evaluate",
    "library_versions",
    "select_model",
]
