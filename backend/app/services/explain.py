"""Module 11 - SHAP explainability of stored predictions and of model versions.

Two questions, one machinery:

*Local.*  For one stored prediction: which features moved the model's probability
away from what the model would have said about a typical row, and by how much. The
row explained is the prediction's **stored input snapshot**, not a row rebuilt here
from the twin. The snapshot is content-addressed, so the attribution is provably
about the same row that produced the number, even after the pan has moved on.
Rebuilding the row would risk explaining a *different* row and attributing the
result to a stored number, which is the one thing an explanation must never do.

*Global.*  For one model version: which features carry the most weight across many
rows. The rows are the held-out sample carried in the artifact -- the rows Module 09
scored the model on -- so the summary describes general behaviour on unseen data
rather than the rows the model was fitted to.

**The explained quantity is the model's predicted probability.** A SHAP value is
measured in the units of the output it decomposes, and the only output this project
shows an operator is a probability, so that is what is decomposed. For the tree
ensembles Module 08 fits, the exact tree explainer does it in probability space
directly. For a linear model the exact linear explainer decomposes the decision
margin instead -- a real quantity, but not the number on screen, and an
explanation of a number nobody was shown answers the wrong question -- so this
module uses the permutation explainer there, which decomposes the probability
itself and records that it estimated rather than computed the values.

**Nothing here invents a value.** Every number in a stored explanation is what the
``shap`` library computed from the registered artifact. Four guards enforce that,
and each refuses rather than adjusts:

* the artifact must carry a reference distribution, because a Shapley value is
  defined relative to one, and a reference invented at explain time would silently
  decide the numbers;
* the stored snapshot must still hash to the ``input_snapshot_id`` the prediction
  recorded, or the row is not the row the number came from;
* the model must emit the class the prediction reported, or there is no probability
  of that class to decompose;
* the contributions must add back up to the stored probability, within
  :data:`ADDITIVITY_TOLERANCE`. An attribution that does not reconcile with the
  number it explains is not stored, however plausible it looks.

**When SHAP cannot be computed, that is the answer.** A prediction from an
estimator with no ``predict_proba`` has no probability to decompose; an artifact
with no reference sample has no reference to measure against; an explainer that
refuses the estimator is a fact about the library, not something to cover over with
correlation coefficients called importances. Each produces a named reason and no
attributions.

**Three quantities, never mixed.** The model's probability, the SHAP contributions
measured in probability units against a stated base value, and the physical
simulation result the row carried as an *input*. They are labelled apart in the
stored record and in every response, because a mass-balance figure, a probability
and a contribution to that probability are three different claims about a pan.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.db.collections import EXPLANATIONS, ML_PREDICTIONS, MODEL_VERSIONS
from app.db.errors import DocumentNotFoundError, DuplicateDocumentError
from app.db.repository import MongoRepository
from app.models import Explanation, FeatureAttribution, MLPrediction, ModelVersion
from app.models.explanation import (
    EXPLAINED_PROBABILITY,
    KIND_GLOBAL,
    KIND_LOCAL,
    PHYSICS_SOURCE,
    SOURCE_IMPUTED,
    SOURCE_UNKNOWN,
    classify_source,
)
from app.services.artifact_paths import ArtifactPathError, resolve_artifact_path
from app.services.dataset_versioning import content_hash
from app.services.ml import ARTIFACT_DIRECTORY

#: Refusal codes. Distinct from the prediction module's codes, because these are
#: about a model and a row rather than about a request, and a client branching on
#: ``artifact_unavailable`` deserves to know which module said so.
NO_SUCH_PREDICTION = "no_such_prediction"
NO_SUCH_MODEL_VERSION = "no_such_model_version"
NOTHING_STORED = "explanation_not_stored"
ARTIFACT_UNAVAILABLE = "artifact_unavailable"
NO_REFERENCE_DISTRIBUTION = "no_reference_distribution"
NO_NUMERIC_OUTPUT = "model_reports_no_probability"
SNAPSHOT_MISMATCH = "input_snapshot_mismatch"
CLASS_NOT_IN_ARTIFACT = "class_not_in_artifact"
FEATURE_MAPPING_UNAVAILABLE = "feature_mapping_unavailable"
ADDITIVITY_FAILED = "attribution_does_not_reconcile"
COMPUTATION_FAILED = "shap_computation_failed"

#: How far the contributions may miss the model's own output before the attribution
#: is refused rather than stored. The exact tree explainer lands within 1e-8, and the
#: sampled explainer reconciles by construction. A residual larger than this means
#: the contributions and the probability are not describing the same computation,
#: which is a bug to fix rather than a caveat to publish.
ADDITIVITY_TOLERANCE = 1e-3

#: Held-out rows covered by a global summary unless the caller asks for fewer. Each
#: row is explained on its own, so the cost is real; the count actually used is
#: reported rather than assumed.
GLOBAL_ROWS_DEFAULT = 25
GLOBAL_ROWS_MAX = 50

#: Evaluation budget for the sampled explainer, which estimates the Shapley values
#: by permutation rather than computing them. Recorded in the response, so an
#: approximation is never presented as an exact attribution.
PERMUTATION_MAX_EVALS = 200

#: The estimator kinds the exact tree explainer handles. Named here rather than
#: inferred, because "does it have ``tree_`` attributes" is a guess about a library's
#: internals and this list is the project's own statement of what it fits.
TREE_ESTIMATORS = (
    "sklearn.tree._classes.DecisionTreeClassifier",
    "sklearn.ensemble._forest.RandomForestClassifier",
    "sklearn.ensemble._gb.GradientBoostingClassifier",
)


class ExplanationUnavailable(RuntimeError):
    """Explainability that cannot be produced, with its code and its reason.

    Every one of these is a case where the alternative is a number with no
    derivation. The module's job is to say which quantity is missing and why,
    because "no attribution" and "an attribution of zero" are different claims and
    only one of them is true.
    """

    def __init__(
        self,
        detail: str,
        *,
        code: str,
        model_id: str | None = None,
        prediction_id: str | None = None,
        status_code: int = 409,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.model_id = model_id
        self.prediction_id = prediction_id
        #: 404 when the thing asked about is not there, 409 when it is there and
        #: cannot be explained. Carried on the exception so the router never has to
        #: re-derive it from the code.
        self.status_code = status_code

    def as_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"detail": self.detail, "refusal": self.code}
        if self.model_id is not None:
            body["model_id"] = self.model_id
        if self.prediction_id is not None:
            body["prediction_id"] = self.prediction_id
        return body


@dataclass(frozen=True)
class _RawShap:
    """One explainer's output, before it is collapsed to declared features.

    ``columns`` is indexed by *transformed* column, which is not the same as the
    model's feature list once a categorical feature has been one-hot encoded. The
    collapse is a sum over a feature's own columns, which is exact: additivity is a
    property of the transformed vector, and the columns of one feature partition it.
    """

    columns: np.ndarray
    base_value: float
    model_output: float
    method: str
    approximate: bool
    shap_version: str


@dataclass(frozen=True)
class _Explanation:
    """A computed attribution, in declared-feature units."""

    values: dict[str, float]
    base_value: float
    model_output: float
    method: str
    approximate: bool
    shap_version: str


class _ColumnMap:
    """Which transformed column belongs to which declared feature."""

    def __init__(self, grouping: dict[str, list[int]]) -> None:
        self._grouping = grouping

    @property
    def features(self) -> list[str]:
        return list(self._grouping)

    def collapse(self, transformed: np.ndarray) -> dict[str, float]:
        """Sum a transformed-column vector into one value per declared feature."""
        vector = np.asarray(transformed, dtype=float).ravel()
        return {
            feature: float(sum(vector[index] for index in indexes))
            for feature, indexes in self._grouping.items()
        }


def _column_map(preprocessing: Any, feature_names: list[str]) -> _ColumnMap | None:
    """Map transformed columns to features, or ``None`` if they cannot be mapped.

    ``None`` is a refusal rather than a guess. Attributing a one-hot column to the
    wrong feature produces a confident explanation of the wrong thing, and there is
    no safe way to continue without the mapping.
    """
    try:
        produced = [str(name) for name in preprocessing.get_feature_names_out()]
    except (AttributeError, TypeError, ValueError, NotImplementedError):
        # The fitted pipeline does not name its output columns, so there is no
        # mapping to group them by. A preprocessor that raises something else is a
        # bug in the pipeline, and that is worth surfacing rather than swallowing.
        return None

    grouping: dict[str, list[int]] = {name: [] for name in feature_names}
    # Longest feature name first, so a feature whose name prefixes another's cannot
    # swallow the other's columns.
    ordered = sorted(feature_names, key=len, reverse=True)
    for index, name in enumerate(produced):
        tail = name.split("__", 1)[1] if "__" in name else name
        for feature in ordered:
            if tail == feature or tail.startswith(f"{feature}_"):
                grouping[feature].append(index)
                break
        else:
            return None
    if any(not indexes for indexes in grouping.values()):
        return None
    return _ColumnMap(grouping)


def _finite(value: Any, *, code: str = COMPUTATION_FAILED) -> float:
    number = float(value)
    if not np.isfinite(number):
        raise ExplanationUnavailable(
            f"{code} SHAP returned a non-finite value ({number!r}). A non-finite "
            "attribution is not an explanation, so none is returned.",
            code=code,
        )
    return number


def _tree_shap(
    estimator: Any, background: np.ndarray, row: np.ndarray, class_index: int
) -> _RawShap:
    """Exact probability-space SHAP from the tree explainer.

    Raises whatever the library raises: the caller decides what a failure means, and
    a refusal reason that names the library's own complaint is more useful than one
    that hides it.
    """
    import shap

    explainer = shap.TreeExplainer(
        estimator, data=background, model_output="probability"
    )
    values = np.asarray(explainer.shap_values(row), dtype=float)
    base = np.atleast_1d(np.asarray(explainer.expected_value, dtype=float))
    output = _finite(estimator.predict_proba(row)[0][class_index])

    if values.ndim == 3:
        # (rows, transformed columns, classes)
        columns = values[0, :, class_index]
        base_value = _finite(base[class_index])
    elif values.ndim == 2 and base.size > 1:
        columns = values[0, :]
        base_value = _finite(base[class_index])
    elif values.ndim == 2:
        # A binary tree explains the positive class in a single block.
        columns = values[0, :]
        base_value = _finite(base[0])
    else:
        raise ValueError(
            f"the tree explainer returned an array of shape {values.shape}, which "
            "matches neither one class per block nor a single class"
        )
    return _RawShap(
        columns=np.asarray(columns, dtype=float),
        base_value=base_value,
        model_output=output,
        method="tree_explainer",
        approximate=False,
        shap_version=str(shap.__version__),
    )


def _permutation_shap(
    estimator: Any, background: np.ndarray, row: np.ndarray, class_index: int
) -> _RawShap:
    """Sampled Shapley values in probability space, for any classifier with one.

    Used for estimators the tree explainer does not handle, logistic regression
    among them. The values are estimated by permutation rather than computed, and
    the record says so: an approximation presented as an exact attribution is the
    failure this module exists to prevent, and the reader is entitled to know which
    of the two they are looking at.
    """
    import shap

    def probability(values: np.ndarray) -> np.ndarray:
        return estimator.predict_proba(values)[:, class_index]

    # The masker needs at least 2 * features + 1 evaluations to enumerate the
    # coalitions; a budget below that floor would be raised silently by the library
    # or refused, so the floor is applied here where it is visible.
    budget = max(PERMUTATION_MAX_EVALS, 2 * background.shape[1] + 1)
    explainer = shap.Explainer(
        probability, background, algorithm="permutation", max_evals=budget, seed=0
    )
    result = explainer(row)
    columns = np.asarray(result.values, dtype=float).ravel()
    base = np.atleast_1d(np.asarray(result.base_values, dtype=float)).ravel()
    return _RawShap(
        columns=columns,
        base_value=_finite(base[0]),
        model_output=_finite(estimator.predict_proba(row)[0][class_index]),
        method="permutation_explainer",
        approximate=True,
        shap_version=str(shap.__version__),
    )


def _is_tree(estimator: Any) -> bool:
    """Whether this estimator is one the project's training pipeline fits."""
    return f"{type(estimator).__module__}.{type(estimator).__qualname__}" in (
        TREE_ESTIMATORS
    )


def _global_explanation_id(model_id: str, version: str) -> str:
    """A deterministic id for a model's global summary.

    Derived from the pair so a re-run replaces one row rather than accumulating
    them: two summaries of the same model version would be two answers to one
    question, and nothing would say which supersedes which. Falls back to a hash of
    the pair when the names are too long for the id pattern.
    """
    candidate = f"exp-global-{model_id}-{version}"
    if len(candidate) <= 64:
        return candidate
    digest = content_hash([model_id, version]).split(":", 1)[-1][:32]
    return f"exp-global-{digest}"


def _background_record(
    artifact: dict[str, Any], key: str, matrix: np.ndarray
) -> dict[str, Any]:
    """Describe the reference distribution, so an attribution states what it is against."""
    reference = artifact.get("shap_reference")
    metadata = reference if isinstance(reference, dict) else {}
    return {
        "source": (
            "training_split_of_the_fitted_dataset"
            if key == "background"
            else "held_out_split_scored_by_module_09"
        ),
        "rows": len(matrix),
        "width": int(matrix.shape[1]) if matrix.ndim > 1 else 0,
        "train_rows": metadata.get("train_rows"),
        "holdout_rows": metadata.get("holdout_rows"),
        "selection": metadata.get("selection"),
    }


def _direction(value: float) -> str:
    if value > 0:
        return "increases_risk"
    if value < 0:
        return "decreases_risk"
    return "neutral"


def _is_simulated_physics(source: str | None) -> bool:
    return source == PHYSICS_SOURCE


class ShapExplanationService:
    """Compute, store and read SHAP attributions for real models and real rows."""

    def __init__(self, database: Any, *, artifact_directory: Path | None = None) -> None:
        self._database = database
        self._artifact_directory = artifact_directory or ARTIFACT_DIRECTORY

    def _explanations(self) -> MongoRepository[Explanation]:
        return MongoRepository(self._database, EXPLANATIONS, Explanation)

    def _predictions(self) -> MongoRepository[MLPrediction]:
        return MongoRepository(self._database, ML_PREDICTIONS, MLPrediction)

    def _models(self) -> MongoRepository[ModelVersion]:
        return MongoRepository(self._database, MODEL_VERSIONS, ModelVersion)

    async def count_explanations(self, query: dict[str, Any] | None = None) -> int:
        """How many explanations are stored, optionally of one kind.

        Read by the status endpoint, which reports whether anything has been
        explained yet. It never reports a number it has not counted.
        """
        return await self._explanations().count(query)

    # -- loading ----------------------------------------------------------

    async def _prediction(self, prediction_id: str) -> MLPrediction:
        """The stored prediction, or the reason there is nothing to explain."""
        try:
            prediction = await self._predictions().get("prediction_id", prediction_id)
        except DocumentNotFoundError as exc:
            raise ExplanationUnavailable(
                f"{NO_SUCH_PREDICTION} No prediction is stored under {prediction_id!r}. A "
                "SHAP value is a statement about a row a model was actually given, so "
                "there is nothing to explain for a prediction that does not exist.",
                code=NO_SUCH_PREDICTION,
                prediction_id=prediction_id,
                status_code=404,
            ) from exc
        return self._require_a_probability(prediction)

    @staticmethod
    def _require_a_probability(prediction: MLPrediction) -> MLPrediction:
        """Refuse a prediction whose estimator reported no number to decompose.

        Held apart from reading the record, because a caller that already holds the
        row -- the prediction service does, immediately after writing it -- must be
        held to the same rule as one that fetched it. A guard that only some paths
        pass is a guard that a class-only model will eventually slip past, and the
        slip shows up as a probability-space attribution of a decision margin.
        """
        if prediction.probability is None or prediction.predicted_class is None:
            raise ExplanationUnavailable(
                f"{NO_NUMERIC_OUTPUT} Prediction {prediction.prediction_id} reports a class "
                f"({prediction.predicted_class!r}) but no probability, because the estimator "
                "that produced it has no `predict_proba`. A SHAP value is a contribution "
                "measured in the units of the model output, and this prediction has no "
                "number for one to be measured in. The class is passed on unchanged and "
                "no attribution is reported, rather than attributing one to a decision "
                "margin the operator never saw.",
                code=NO_NUMERIC_OUTPUT,
                model_id=prediction.model_id,
                prediction_id=prediction.prediction_id,
            )
        return prediction

    async def _model(self, model_id: str, version: str) -> ModelVersion:
        found = await self._models().find({"model_id": model_id, "version": version})
        if not found:
            raise ExplanationUnavailable(
                f"{NO_SUCH_MODEL_VERSION} No model version {model_id!r} {version!r} is "
                "registered. An attribution has to name the model that produced the number "
                "it accounts for, so none is produced.",
                code=NO_SUCH_MODEL_VERSION,
                model_id=model_id,
                status_code=404,
            )
        return found[0]

    def _load_artifact(self, model: ModelVersion) -> dict[str, Any]:
        """Load the registered artifact, or explain why nothing can be explained."""
        try:
            path = resolve_artifact_path(
                self._artifact_directory, model.artifact_path or ""
            )
        except ArtifactPathError as exc:
            raise ExplanationUnavailable(
                f"{ARTIFACT_UNAVAILABLE} The registry records an artifact path for "
                f"model {model.model_id} {model.version} that resolves outside the "
                "artifact directory, so it is refused.",
                code=ARTIFACT_UNAVAILABLE,
                model_id=model.model_id,
            ) from exc
        if not path.exists():
            raise ExplanationUnavailable(
                f"{ARTIFACT_UNAVAILABLE} The registry records {model.artifact_path} for "
                f"model {model.model_id} {model.version}, and no artifact is there on this "
                "host. Attributing a prediction to a refitted stand-in would be an "
                "attribution to a model that never made it, so none is reported.",
                code=ARTIFACT_UNAVAILABLE,
                model_id=model.model_id,
            )
        try:
            return joblib.load(path)
        except Exception as exc:
            raise ExplanationUnavailable(
                f"{ARTIFACT_UNAVAILABLE} The artifact at {path} could not be loaded: {exc}. "
                "The library versions this model was fitted with are recorded on the model "
                "version.",
                code=ARTIFACT_UNAVAILABLE,
                model_id=model.model_id,
            ) from exc

    @staticmethod
    def _estimator_parts(
        artifact: dict[str, Any], model: ModelVersion
    ) -> tuple[Any, Any]:
        estimator = artifact.get("estimator")
        preprocessing = artifact.get("preprocessing")
        if estimator is None or preprocessing is None:
            raise ExplanationUnavailable(
                f"{ARTIFACT_UNAVAILABLE} The artifact for model {model.model_id} "
                f"{model.version} does not contain both an estimator and the preprocessing "
                "it was fitted with, so there is nothing to attribute.",
                code=ARTIFACT_UNAVAILABLE,
                model_id=model.model_id,
            )
        return estimator, preprocessing

    def _reference(
        self, artifact: dict[str, Any], key: str, model: ModelVersion
    ) -> np.ndarray:
        """The reference sample an explainer measures against.

        Absent for an artifact written before the sample was carried, and refused
        rather than replaced: the training sample is the distribution the model's
        medians, scaler and category list came from, and substituting another would
        attribute the model to a distribution it was not fitted on.
        """
        reference = artifact.get("shap_reference")
        matrix = reference.get(key) if isinstance(reference, dict) else None
        if matrix is None or len(np.asarray(matrix)) == 0:
            which = "training" if key == "background" else "held-out"
            raise ExplanationUnavailable(
                f"{NO_REFERENCE_DISTRIBUTION} The artifact for model {model.model_id} "
                f"{model.version} carries no {which} reference sample. A Shapley value is a "
                "contribution measured against a reference distribution, and this artifact "
                "does not record one, so the measurement would have to be made against a "
                "distribution chosen at explain time. That choice would decide the numbers "
                f"and it is not this module's to make silently. Retrain {model.model_id} to "
                "store the sample with the model.",
                code=NO_REFERENCE_DISTRIBUTION,
                model_id=model.model_id,
            )
        return np.asarray(matrix, dtype=float)

    @staticmethod
    def _classes(artifact: dict[str, Any], estimator: Any) -> list[str]:
        classes = [str(label) for label in artifact.get("classes", [])]
        if not classes:
            classes = [str(label) for label in getattr(estimator, "classes_", [])]
        return classes

    # -- the computation --------------------------------------------------

    def _explain_row(
        self,
        *,
        estimator: Any,
        background: np.ndarray,
        row: np.ndarray,
        class_index: int,
        mapping: _ColumnMap,
    ) -> _Explanation:
        """Run the first explainer that can take this estimator, in probability space.

        The tree explainer is tried first: it is exact and fast for the ensembles
        Module 08 fits. Everything else with a ``predict_proba`` goes to the
        permutation explainer, which is slower and estimated, and says so.

        Both decompose the model's probability for one class, so the contributions
        add back up to the number the prediction reported. Neither is asked to
        decompose a margin, a logit or a distance: those are quantities this project
        never shows an operator, and an explanation of one would not explain the
        number on screen.
        """
        attempts: list[str] = []
        raw: _RawShap | None = None
        strategies: list[tuple[str, Any]] = []
        if _is_tree(estimator):
            strategies.append(("tree_explainer", _tree_shap))
        strategies.append(("permutation_explainer", _permutation_shap))
        for name, strategy in strategies:
            try:
                raw = strategy(estimator, background, row, class_index)
                break
            except ExplanationUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001 -- the next strategy decides
                attempts.append(f"{name}: {type(exc).__name__}: {exc}")
        if raw is None:
            raise ExplanationUnavailable(
                f"{COMPUTATION_FAILED} SHAP could not explain this model. Tried: "
                + ("; ".join(attempts) or "no explainer accepted the estimator")
                + ". No attribution is reported.",
                code=COMPUTATION_FAILED,
            )
        return _Explanation(
            values=mapping.collapse(raw.columns),
            base_value=raw.base_value,
            model_output=raw.model_output,
            method=raw.method,
            approximate=raw.approximate,
            shap_version=raw.shap_version,
        )

    @staticmethod
    def _attributions(
        *,
        features: list[str],
        values: dict[str, float],
        row_values: dict[str, Any] | None,
        sources: dict[str, str] | None,
        absent: dict[str, str] | None,
    ) -> list[FeatureAttribution]:
        """One stored attribution per declared feature, in the model's own order.

        Every declared feature appears, including one whose contribution is exactly
        zero. A list that quietly dropped a feature would read as "this feature did
        not matter", when the truth is "this feature was not examined" -- and those
        are different sentences.
        """
        absent = absent or {}
        sources = sources or {}
        records: list[FeatureAttribution] = []
        for feature in features:
            kind = classify_source(feature, sources.get(feature), feature in absent)
            raw = (row_values or {}).get(feature)
            records.append(
                FeatureAttribution(
                    feature=feature,
                    # None for an imputed column, even though the preprocessor put
                    # the training median there: that median is the model's, and
                    # reporting it as this pan's value would invent a measurement.
                    feature_value=None
                    if kind == SOURCE_IMPUTED or raw is None
                    else _finite(raw),
                    shap_value=_finite(values.get(feature, 0.0)),
                    source=sources.get(feature),
                    source_kind=kind,
                    imputed=kind == SOURCE_IMPUTED,
                )
            )
        return records

    async def _store(self, document: Explanation) -> Explanation:
        """Insert the explanation, or replace the one already there for its key.

        One explanation per prediction, because the arithmetic is deterministic: a
        second row for the same prediction would be a second answer to a question
        already answered, with nothing to choose between them. The unique partial
        index on ``(prediction_id, kind=local)`` is what enforces that in the
        cluster; the replace path is what makes a re-run a refresh rather than an
        accumulation.

        The key is looked up rather than assumed, and that is the whole subtlety:
        a freshly computed document carries a new ``explanation_id``, so replacing
        *by* that id finds nothing and the second write would sit beside the first.
        For a local explanation the key is the prediction it belongs to, and for a
        global summary the id is derived from the model version, so both are found
        by the thing that identifies them rather than by a value that has just
        changed.
        """
        repository = self._explanations()
        try:
            return await repository.insert(document)
        except DuplicateDocumentError:
            pass
        key = (
            {"prediction_id": document.prediction_id}
            if document.prediction_id is not None
            else {"explanation_id": document.explanation_id}
        )
        try:
            return await repository.replace_where(key, document)
        except DocumentNotFoundError:  # pragma: no cover - the key was just read
            # Raced with a delete. The insert is the right answer, and the unique
            # index arbitrates if another writer got there first.
            return await repository.insert(document)

    # -- local ------------------------------------------------------------

    async def explain_prediction(
        self, prediction_id: str, *, stored: MLPrediction | None = None
    ) -> tuple[dict[str, Any], bool]:
        """Explain one stored prediction from its own stored input row.

        Returns the response and whether an existing row was replaced. The row is the
        prediction's ``input_snapshot``, verified against the ``input_snapshot_id``
        the prediction recorded: that check is the difference between explaining a
        stored number and explaining a row that merely looks like it. If the snapshot
        no longer hashes to the recorded id, the two are no longer the same thing and
        the attribution would be about the wrong one.

        ``stored`` is the just-written record when the caller made the prediction and
        is reading it back here is only round-trip. It is the same row either way --
        a write is immediately readable -- and passing it says so, rather than making
        the explanation depend on a second lookup succeeding.
        """
        prediction = self._require_a_probability(
            stored or await self._prediction(prediction_id)
        )
        model_id = prediction.model_id
        if not model_id:
            raise ExplanationUnavailable(
                f"{NO_SUCH_MODEL_VERSION} Prediction {prediction.prediction_id} records no "
                "model id, and a model version is not identifiable from a version string "
                "alone. There is no artifact to attribute the number to, so no attribution "
                "is reported.",
                code=NO_SUCH_MODEL_VERSION,
                prediction_id=prediction.prediction_id,
            )
        model = await self._model(model_id, prediction.model_version)
        artifact = self._load_artifact(model)
        estimator, preprocessing = self._estimator_parts(artifact, model)
        background = self._reference(artifact, "background", model)

        snapshot = prediction.input_snapshot or {}
        features = snapshot.get("features")
        names = list(model.feature_names)
        recorded = prediction.input_snapshot_id
        if not snapshot or not isinstance(features, dict) or not names:
            raise ExplanationUnavailable(
                f"{SNAPSHOT_MISMATCH} Prediction {prediction.prediction_id} stores "
                f"{len(features) if isinstance(features, dict) else 0} snapshotted features "
                f"against the {len(names)} its model declared. Without the row the model was "
                "given there is nothing to attribute, and reconstructing it from the twin "
                "now would risk explaining a different row than the number came from.",
                code=SNAPSHOT_MISMATCH,
                model_id=model_id,
                prediction_id=prediction.prediction_id,
            )
        if recorded and content_hash(snapshot) != recorded:
            raise ExplanationUnavailable(
                f"{SNAPSHOT_MISMATCH} The stored snapshot for prediction "
                f"{prediction.prediction_id} no longer hashes to the {recorded!r} it was "
                "stored under. The row and the id that names it have diverged, so "
                "attributing this model output to that row would be a claim about a row "
                "that cannot be checked.",
                code=SNAPSHOT_MISMATCH,
                model_id=model_id,
                prediction_id=prediction.prediction_id,
            )

        mapping = _column_map(preprocessing, names)
        if mapping is None:
            raise ExplanationUnavailable(
                f"{FEATURE_MAPPING_UNAVAILABLE} The preprocessed columns of model "
                f"{model_id} {model.version} could not be matched back to its declared "
                "features. A one-hot column attributed to the wrong feature would be a "
                "confident explanation of the wrong thing, so no attribution is reported.",
                code=FEATURE_MAPPING_UNAVAILABLE,
                model_id=model_id,
                prediction_id=prediction.prediction_id,
            )

        classes = self._classes(artifact, estimator)
        try:
            class_index = classes.index(str(prediction.predicted_class))
        except ValueError as exc:
            raise ExplanationUnavailable(
                f"{CLASS_NOT_IN_ARTIFACT} Prediction {prediction.prediction_id} reports "
                f"class {prediction.predicted_class!r}, which the artifact for model "
                f"{model_id} {model.version} does not emit; it emits {classes}. There is no "
                "probability of that class to decompose, so none is invented.",
                code=CLASS_NOT_IN_ARTIFACT,
                model_id=model_id,
                prediction_id=prediction.prediction_id,
            ) from exc

        frame = pd.DataFrame.from_records(
            [{name: features.get(name) for name in names}]
        ).reindex(columns=names)
        transformed = np.asarray(preprocessing.transform(frame), dtype=float)
        shap_values = self._explain_row(
            estimator=estimator,
            background=background,
            row=transformed,
            class_index=class_index,
            mapping=mapping,
        )

        attributions = self._attributions(
            features=names,
            values=shap_values.values,
            row_values={name: features.get(name) for name in names},
            sources=snapshot.get("feature_sources") or {},
            absent=snapshot.get("features_absent") or {},
        )
        total = shap_values.base_value + sum(item.shap_value for item in attributions)
        error = abs(float(prediction.probability) - total)
        if error > ADDITIVITY_TOLERANCE:
            raise ExplanationUnavailable(
                f"{ADDITIVITY_FAILED} The contributions for prediction "
                f"{prediction.prediction_id} add to their base value as {total:.6f}, while "
                f"the stored probability is {float(prediction.probability):.6f} -- a gap of "
                f"{error:.6g}, beyond the {ADDITIVITY_TOLERANCE:g} this module will publish. "
                "An attribution that does not add up to the number it explains is not "
                "stored, so this one is reported as unavailable.",
                code=ADDITIVITY_FAILED,
                model_id=model_id,
                prediction_id=prediction.prediction_id,
            )

        document = Explanation(
            prediction_id=prediction.prediction_id,
            pan_id=prediction.pan_id,
            model_id=model.model_id,
            model_version=model.version,
            kind=KIND_LOCAL,
            target_class=str(prediction.predicted_class),
            explained_output=EXPLAINED_PROBABILITY,
            model_output=float(prediction.probability),
            base_value=shap_values.base_value,
            attribution_error=error,
            method=shap_values.method,
            approximate=shap_values.approximate,
            shap_version=shap_values.shap_version,
            background=_background_record(artifact, "background", background),
            input_snapshot_id=prediction.input_snapshot_id,
            attributions=attributions,
            timestamp=prediction.created_at,
            is_simulated=False,
        )
        existing = await self._explanations().find(
            {"prediction_id": prediction.prediction_id, "kind": KIND_LOCAL}, limit=1
        )
        stored = await self._store(document)
        return (
            to_local_response(stored, model=model, prediction=prediction),
            bool(existing),
        )

    async def read_explanation(self, prediction_id: str) -> dict[str, Any]:
        """Read the stored explanation for a prediction, or say there is not one.

        The reason is specific. "Not stored" and "unavailable" are different answers
        -- the first means nobody has asked, the second means asking will not help --
        and a client that cannot tell them apart will keep retrying.
        """
        prediction = await self._prediction(prediction_id)
        found = await self._explanations().find(
            {"prediction_id": prediction.prediction_id, "kind": KIND_LOCAL},
            sort=[("created_at", -1)],
            limit=1,
        )
        if not found:
            raise ExplanationUnavailable(
                f"{NOTHING_STORED} No explanation is stored for prediction "
                f"{prediction.prediction_id}. Ask for one with POST "
                f"/api/explain/predictions/{prediction.prediction_id}, and it will be "
                "computed from the stored input row and stored against the prediction and "
                "the model version that produced it.",
                code=NOTHING_STORED,
                model_id=prediction.model_id,
                prediction_id=prediction.prediction_id,
            )
        model: ModelVersion | None = None
        if found[0].model_id:
            registered = await self._models().find(
                {"model_id": found[0].model_id, "version": found[0].model_version}
            )
            model = registered[0] if registered else None
        return to_local_response(found[0], model=model, prediction=prediction)

    # -- global -----------------------------------------------------------

    async def global_importance(
        self, model_id: str, version: str, *, rows: int | None = None
    ) -> dict[str, Any]:
        """Summarise a model version's attributions over its held-out rows.

        The rows are the held-out sample in the artifact, in the order the split
        produced them, capped at :data:`GLOBAL_ROWS_MAX`. Each row is explained
        against its *own* predicted class, because that is the probability an
        operator would have been shown for it; averaging rows of different classes
        would describe a number this model does not produce for most of them.

        Stored under a deterministic id, so re-running refreshes one row rather than
        accumulating summaries that cannot be told apart.
        """
        model = await self._model(model_id, version)
        artifact = self._load_artifact(model)
        estimator, preprocessing = self._estimator_parts(artifact, model)
        if not hasattr(estimator, "predict_proba"):
            raise ExplanationUnavailable(
                f"{NO_NUMERIC_OUTPUT} Model {model_id} {version} has an estimator with no "
                "`predict_proba`, so it emits classes and no probabilities. There is no "
                "model output to attribute, so no feature importance is reported.",
                code=NO_NUMERIC_OUTPUT,
                model_id=model_id,
            )
        background = self._reference(artifact, "background", model)
        holdout = self._reference(artifact, "holdout", model)
        names = list(model.feature_names)
        mapping = _column_map(preprocessing, names)
        if mapping is None:
            raise ExplanationUnavailable(
                f"{FEATURE_MAPPING_UNAVAILABLE} The preprocessed columns of model "
                f"{model_id} {version} could not be matched back to its declared features, "
                "so no importance can be attributed to a feature.",
                code=FEATURE_MAPPING_UNAVAILABLE,
                model_id=model_id,
            )

        limit = max(1, min(int(rows or GLOBAL_ROWS_DEFAULT), GLOBAL_ROWS_MAX))
        matrix = holdout[:limit]
        classes = self._classes(artifact, estimator)
        totals: dict[str, float] = {name: 0.0 for name in names}
        explained_classes: dict[str, int] = {}
        methods: set[str] = set()
        approximate = False
        shap_version = "unrecorded"
        explained = 0
        for index in range(len(matrix)):
            row = matrix[index : index + 1]
            predicted = str(estimator.predict(row)[0])
            if predicted not in classes:
                continue
            explained_classes[predicted] = explained_classes.get(predicted, 0) + 1
            values = self._explain_row(
                estimator=estimator,
                background=background,
                row=row,
                class_index=classes.index(predicted),
                mapping=mapping,
            )
            explained += 1
            methods.add(values.method)
            approximate = approximate or values.approximate
            shap_version = values.shap_version
            for name, value in values.values.items():
                totals[name] = totals.get(name, 0.0) + abs(value)

        if explained == 0:
            raise ExplanationUnavailable(
                f"{COMPUTATION_FAILED} None of the {len(matrix)} held-out rows carried by "
                f"the artifact for model {model_id} {version} could be explained, so no "
                "importance is reported.",
                code=COMPUTATION_FAILED,
                model_id=model_id,
            )

        attributions = [
            FeatureAttribution(
                feature=name,
                # A global summary's attribution *is* the mean absolute
                # contribution: the row-by-row values it averages are not about any
                # one pan, so no single feature value belongs beside it.
                feature_value=None,
                shap_value=_finite(totals[name] / explained),
                source=None,
                source_kind=SOURCE_UNKNOWN,
                imputed=False,
            )
            for name in names
        ]
        document = Explanation(
            explanation_id=_global_explanation_id(model.model_id, model.version),
            prediction_id=None,
            model_id=model.model_id,
            model_version=model.version,
            kind=KIND_GLOBAL,
            explained_output=EXPLAINED_PROBABILITY,
            model_output=None,
            base_value=None,
            attribution_error=None,
            method="+".join(sorted(methods)),
            approximate=approximate,
            shap_version=shap_version,
            background=_background_record(artifact, "holdout", matrix),
            rows_explained=explained,
            attributions=attributions,
            is_simulated=False,
        )
        stored = await self._store(document)
        return to_global_response(
            stored, model=model, explained_classes=explained_classes
        )

    async def list_for_model(self, model_id: str, version: str) -> dict[str, Any]:
        """The stored explanations for a model version, newest first.

        The audit view: which predictions this version answered, and for each, the
        method that explained it and whether that method was exact.
        """
        await self._model(model_id, version)
        found = await self._explanations().find(
            {"model_id": model_id, "model_version": version},
            sort=[("created_at", -1)],
            limit=200,
        )
        return {
            "model_id": model_id,
            "model_version": version,
            "count": len(found),
            "explanations": [
                {
                    "explanation_id": item.explanation_id,
                    "kind": item.kind,
                    "prediction_id": item.prediction_id,
                    "pan_id": item.pan_id,
                    "target_class": item.target_class,
                    "explained_output": item.explained_output,
                    "model_output": item.model_output,
                    "base_value": item.base_value,
                    "attribution_error": item.attribution_error,
                    "method": item.method,
                    "approximate": item.approximate,
                    "shap_version": item.shap_version,
                    "rows_explained": item.rows_explained,
                    "created_at": item.created_at,
                    "is_simulated": item.is_simulated,
                }
                for item in found
            ],
        }


def to_local_response(
    document: Explanation,
    *,
    model: ModelVersion | None,
    prediction: MLPrediction,
) -> dict[str, Any]:
    """One stored explanation, in the shape a client reads.

    Three quantities, three labels, never mixed: the model's own probability; the
    SHAP contributions, measured in probability units against a stated base value;
    and the physical simulation results the row carried as inputs. The last is
    included because a pan's risk is often driven by a mass-balance figure an
    operator will recognise, and showing it beside the attribution -- as what it
    is, an input the model read -- is the difference between an explanation and a
    second, competing number.
    """
    attributions = [
        {
            "feature": item.feature,
            "feature_value": item.feature_value,
            "shap_value": item.shap_value,
            "direction": _direction(item.shap_value),
            "source": item.source,
            "source_kind": item.source_kind,
            "imputed": item.imputed,
        }
        for item in document.attributions
    ]
    increasing = sorted(
        (item for item in attributions if item["shap_value"] > 0),
        key=lambda item: item["shap_value"],
        reverse=True,
    )
    decreasing = sorted(
        (item for item in attributions if item["shap_value"] < 0),
        key=lambda item: item["shap_value"],
    )
    snapshot = prediction.input_snapshot or {}
    snapshotted = snapshot.get("features") or {}
    sources = snapshot.get("feature_sources") or {}
    physics = {
        name: snapshotted[name]
        for name in sorted(snapshotted)
        if _is_simulated_physics(sources.get(name)) and snapshotted[name] is not None
    }
    return {
        "explanation_id": document.explanation_id,
        "prediction_id": document.prediction_id,
        "pan_id": document.pan_id,
        "model_id": document.model_id,
        "model_version": document.model_version,
        "model_name": model.name if model is not None else None,
        "algorithm": model.algorithm if model is not None else None,
        "kind": document.kind,
        "created_at": document.created_at,
        "target_class": document.target_class,
        "input_snapshot_id": document.input_snapshot_id,
        "twin_id": snapshot.get("twin_id"),
        "twin_as_of": snapshot.get("twin_as_of"),
        "quantities": {
            "model_probability": {
                "label": "Model probability",
                "value": document.model_output,
                "unit": "probability",
                "note": (
                    "The model's own output for the predicted class, from the registered "
                    "artifact. SHAP does not change it and is not part of it."
                ),
            },
            "shap_contribution": {
                "label": "SHAP contribution",
                "unit": "probability points",
                "base_value": document.base_value,
                "explained_output": document.explained_output,
                "target_class": document.target_class,
                "note": (
                    "Each value is how far that one feature moved the probability away from "
                    "the model's expected value over its training rows. Contributions plus "
                    "base value equal the probability above; the residual is reported as "
                    "attribution_error."
                ),
            },
            "physical_simulation": {
                "label": "Physical simulation result",
                "unit": "as named by each quantity",
                "results": physics,
                "note": (
                    "Module 06's mass balance for this pan and forecast, as held in the "
                    "input row. It is an input the model read, not an output of the model, "
                    "and it is not a SHAP contribution."
                ),
            },
        },
        "model_probability": document.model_output,
        "base_value": document.base_value,
        "attribution_error": document.attribution_error,
        "method": document.method,
        "approximate": document.approximate,
        "shap_version": document.shap_version,
        "background": document.background,
        "attributions": attributions,
        "top_increasing": increasing[:TOP_FACTORS],
        "top_decreasing": decreasing[:TOP_FACTORS],
        "is_simulated": document.is_simulated,
    }


def to_global_response(
    document: Explanation,
    *,
    model: ModelVersion,
    explained_classes: dict[str, int],
) -> dict[str, Any]:
    """One stored global summary, in the shape a client reads."""
    ranked = sorted(
        document.attributions, key=lambda item: item.shap_value, reverse=True
    )
    return {
        "explanation_id": document.explanation_id,
        "model_id": document.model_id,
        "model_version": document.model_version,
        "model_name": model.name,
        "algorithm": model.algorithm,
        "kind": document.kind,
        "created_at": document.created_at,
        "explained_output": document.explained_output,
        "rows_explained": document.rows_explained,
        "explained_classes": explained_classes,
        "method": document.method,
        "approximate": document.approximate,
        "shap_version": document.shap_version,
        "background": document.background,
        "measure": {
            "label": "Mean absolute SHAP contribution",
            "unit": "probability points",
            "note": (
                "For each held-out row, that feature's contribution to its own predicted "
                "class, averaged in absolute value across the rows explained. A ranking of "
                "influence on this model's probabilities -- not a causal claim, and not a "
                "model coefficient."
            ),
        },
        "features": [
            {
                "feature": item.feature,
                "mean_abs_shap": item.shap_value,
                "rank": index + 1,
            }
            for index, item in enumerate(ranked)
        ],
        "is_simulated": document.is_simulated,
    }


#: How many factors each direction reports. A list long enough to hide the top of it
#: is a list nobody reads, and the ranking is what the reader came for.
TOP_FACTORS = 5


__all__ = [
    "ADDITIVITY_TOLERANCE",
    "ARTIFACT_UNAVAILABLE",
    "COMPUTATION_FAILED",
    "GLOBAL_ROWS_DEFAULT",
    "GLOBAL_ROWS_MAX",
    "TOP_FACTORS",
    "ExplanationUnavailable",
    "ShapExplanationService",
    "to_global_response",
    "to_local_response",
]
