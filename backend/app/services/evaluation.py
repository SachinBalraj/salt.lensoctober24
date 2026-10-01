"""Module 09 - the evaluation service: score the registered model, then store what it found.

Module 08 fitted four candidates and reported a handful of floats per candidate.
This service is the one that turns "a model was fitted" into "here is exactly how
well it did, on which rows, with which caveats, and stored against the version that
did it". It sits between three things that already exist and refuses to duplicate
any of them: the registry (:mod:`app.services.ml`), the scorer
(:mod:`app.ml.evaluation`) and the resampler (:mod:`app.ml.cross_validation`).

**The split is reproduced, not re-chosen.** The evaluation rebuilds the same
held-out rows the training run used, from the recorded strategy, test fraction and
seed, and scores the registered artifact on exactly those rows. Re-splitting would
be a different experiment reported under a familiar name, and the resulting figure
would be uncorrelated with the one already on the model version -- which is worse
than having no figure, because both would be cited. Everything the reproduction
needs was recorded by Module 08 for precisely this; where it is missing the service
refuses rather than substituting a default, because a default split is a different
experiment.

**The candidates other than the selected one are refitted, and this is stated.**
Module 08 persists only the selected estimator -- the artifact is a
self-sufficient ``(preprocessor, estimator, classes)`` triple. So a comparison over
all four candidates refits the other three from the recorded seed and fixed
hyper-parameters, on the reproduced training rows, through the *persisted*
preprocessor. That is a deterministic reconstruction, not a new measurement, and
the returned payload says so in words rather than leaving the reader to assume the
figures came out of the registered file. It is also checked: the reconstruction's
scores are compared against Module 08's own recorded candidate metrics, and a
mismatch is reported as a caveat rather than quietly averaged over.

**Refusals are outcomes, not exceptions.** A drifted dataset, a missing artifact, a
model registered without the provenance needed to reproduce its split, a split that
no longer divides the rebuilt data the way it did: each produces an
:class:`EvaluationRefusal` with a machine-readable ``refusal`` code and the counts
that caused it, and each writes nothing. The alternative -- an evaluation with
figures computed from whatever was available -- is the one thing this module must
not produce.

**One evaluation per model version.** The run is deterministic given the seed, so a
second evaluation of the same artifact over the same rows produces the same numbers;
a second document claiming to be a second measurement of the same rows would
overstate how much evidence exists. Re-running replaces the record and says that it
did, so the API can report ``recomputed`` honestly instead of implying a new run
happened.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.db.collections import MODEL_EVALUATIONS, MODEL_VERSIONS
from app.db.errors import DocumentNotFoundError, DuplicateDocumentError
from app.db.repository import MongoRepository
from app.ml.candidates import candidate_specs
from app.ml.comparison import build_comparison, flatten_readings
from app.ml.cross_validation import run_cross_validation
from app.ml.evaluation import score_estimator
from app.ml.pipeline import DEFAULT_TEST_SIZE, build_feature_frame, label_frame
from app.ml.preprocessing import apply_preprocessing
from app.ml.provenance import (
    EVIDENCE_NOT_GENUINE_MESSAGE,
    verdict_from_parameters,
)
from app.ml.splitting import SPLIT_STRATEGIES, make_split
from app.models.base import utcnow
from app.models.enums import ResearchTarget
from app.models.evaluation import (
    ClassificationEvaluation,
    EvaluationRefusal,
)
from app.models.model_evaluation import ModelEvaluation
from app.models.model_version import ModelVersion
from app.services.artifact_paths import ArtifactPathError, resolve_artifact_path
from app.services.csv_security import sanitize_prose_cell
from app.services.dataset_catalog import feature_kinds
from app.services.datasets import DatasetService
from app.services.ml import ARTIFACT_DIRECTORY

logger = logging.getLogger("saltlens.ml.evaluation")

#: The wording of the rows every candidate's figures describe. Passed to every
#: scorer so the phrase cannot drift between the headline evaluation and the
#: comparison rows -- a table where the selected row and the candidate rows name
#: different denominators is a table whose columns do not line up.
EVALUATION_SET = (
    "the held-out rows of the recorded split, reproduced from the model version's "
    "split strategy, test fraction and seed"
)

#: Why the comparison's non-selected candidates are refitted rather than loaded.
#: Attached to the stored record, because the alternative -- a reader assuming every
#: figure came from a registered artifact -- is the misunderstanding this sentence
#: exists to prevent.
CANDIDATE_RECONSTRUCTION_NOTE = (
    "Only the selected candidate's estimator is persisted as an artifact. The other "
    "candidates' figures here were produced by refitting them from the same recorded "
    "seed, the same fixed hyper-parameters and the same persisted preprocessing "
    "pipeline, on the same reproduced training rows, and scoring them on the same "
    "reproduced held-out rows. That reconstruction is deterministic, so the figures "
    "are the ones the training run would report, but they were not read from a "
    "registered artifact and should not be cited as though they were."
)

#: The module's own refusal sentence, used when the model version lacks the
#: provenance needed to reproduce its split. Distinct from Module 08's
#: insufficient-data sentence: nothing here is wrong with the data, so reusing that
#: message would send a reader off to gather rows when the fix is a re-registration.
NOT_EVALUABLE = "This model version cannot be evaluated from the records it carries."

ARTIFACT_UNAVAILABLE = "The model artifact is not available on this host."

#: Tolerance for the reconstruction check against Module 08's recorded metrics.
#: Small but not zero: floating-point results from a refit are not guaranteed
#: bit-identical under a different thread scheduling on a parallelised estimator,
#: and a check that fires on 1e-15 of noise would train the reader to ignore it.
RECONSTRUCTION_TOLERANCE = 1e-9

#: Module 08's metric names mapped onto this module's. Explicit rather than derived
#: so that a rename on either side shows up as a stopped comparison here instead of
#: silently comparing nothing.
RECONSTRUCTION_KEYS: dict[str, str] = {
    "accuracy": "accuracy",
    "balanced_accuracy": "balanced_accuracy",
    "f1": "f1_macro",
    "precision": "precision_macro",
    "recall": "recall_macro",
    "matthews_corrcoef": "matthews_corrcoef",
}


class EvaluationRefusalError(RuntimeError):
    """The model version cannot be evaluated, and nothing was written.

    Carries the refusal as data so the router can return it as a body with the
    machine-readable code intact, rather than as a sentence in a ``detail`` field a
    client has to match on prose.

    ``not_found`` is separate from the refusal code because the HTTP status depends on
    it and the status is a router concern. "No model with that id" and "a model with
    that id, which cannot be evaluated" share a domain code -- neither is a
    condition of the data -- and differ in whether the answer is 404 or 422. Deciding
    that here would put a status code in the domain; deciding it from a flag lets the
    router own the mapping while the refusal stays a statement about the records.
    """

    def __init__(self, refusal: EvaluationRefusal, *, not_found: bool = False) -> None:
        super().__init__(refusal.detail)
        self.refusal = refusal
        self.not_found = not_found


@dataclass
class EvaluatedComparison:
    """The result of one evaluation run, and whether it replaced a stored report."""

    document: ModelEvaluation
    comparison: dict[str, Any]
    recomputed: bool = False
    caveats: list[str] = field(default_factory=list)


class EvaluationService:
    """Evaluate registered model versions and store the reports."""

    def __init__(
        self,
        database: Any,
        *,
        artifact_directory: Path | None = None,
    ) -> None:
        self._database = database
        # Defaults to the same directory the pipeline writes to, so a service built
        # without an explicit path reads the artifacts Module 08 actually produced
        # rather than a parallel set. One definition of where artifacts live.
        self._artifact_directory = artifact_directory or ARTIFACT_DIRECTORY
        self._dataset_service = DatasetService(database)

    # -- wiring ----------------------------------------------------------

    def _model_repository(self) -> MongoRepository[ModelVersion]:
        return MongoRepository(self._database, MODEL_VERSIONS, ModelVersion)

    def _evaluation_repository(self) -> MongoRepository[ModelEvaluation]:
        return MongoRepository(self._database, MODEL_EVALUATIONS, ModelEvaluation)

    # -- the run ---------------------------------------------------------

    async def evaluate(
        self,
        model_id: str,
        *,
        cross_validation_splits: int | None = None,
    ) -> EvaluatedComparison:
        """Evaluate one registered model version and store the report.

        Raises :class:`EvaluationRefusalError` -- carrying the refusal as data --
        when the version cannot be evaluated. Returns the stored document otherwise,
        with ``recomputed`` set when an existing report was replaced rather than
        written for the first time.
        """
        version = await self._load_model(model_id)
        refusal = self._check_evaluable(version)
        if refusal is not None:
            raise EvaluationRefusalError(refusal)

        materialized = await self._dataset_service.materialize(version.dataset_id)
        dataset = materialized.dataset
        built = materialized.built

        if materialized.drift:
            raise EvaluationRefusalError(
                EvaluationRefusal(
                    detail=(
                        f"Dataset {version.dataset_id} no longer matches the manifest "
                        f"the model was trained against. The records now hash to "
                        f"{materialized.content_hash} while the model cites "
                        f"{version.dataset_content_hash}. An evaluation over these "
                        "rows would be attached to a dataset revision the model was "
                        "not fitted to."
                    ),
                    refusal="dataset_drift",
                    model_id=model_id,
                    dataset_id=version.dataset_id,
                    reasons=[
                        f"the model cites dataset hash {version.dataset_content_hash}",
                        f"the records now hash to {materialized.content_hash}",
                    ],
                )
            )

        artifact = self._load_artifact(version)
        split, matrices, classes, row_meta, cross_frame = self._reproduce_split(
            version, dataset, built, artifact["preprocessing"]
        )
        caveats: list[str] = []

        # -- the registered artifact, scored on the reproduced held-out rows --
        evaluation = score_estimator(
            artifact["estimator"],
            matrices["test"],
            row_meta["test_labels"],
            np.asarray(artifact["classes"]),
            EVALUATION_SET,
        )

        # -- every candidate, for the comparison ---------------------------
        candidate_evaluations = self._evaluate_candidates(
            version, artifact, matrices, row_meta, classes, evaluation
        )
        caveats.extend(self._check_reconstruction(version, candidate_evaluations))

        # -- cross-validation, where the data supports it ------------------
        report = self._cross_validate(
            version,
            cross_frame,
            row_meta,
            split,
            cross_validation_splits,
        )
        if report.performed:
            caveats.append(
                f"Cross-validation ran as {report.method} over {report.n_splits} "
                "folds. The fold means below are measured on different rows from the "
                "single held-out figure above and answer the same question at "
                "different cut points, which is why the two are reported in separate "
                "columns rather than one."
            )
        else:
            caveats.append(
                f"Cross-validation was not performed: {report.reason} The single "
                "held-out figure therefore stands with no spread across folds beside "
                "it. One measurement is not a distribution, and a reader should not "
                "read agreement into it."
            )

        # -- the comparison table -----------------------------------------
        selected = version.algorithm
        comparison = build_comparison(
            candidate_evaluations=candidate_evaluations,
            selected_algorithm=selected,
            cross_validation=report,
            model_id=version.model_id,
            version=version.version,
        )
        caveats.append(CANDIDATE_RECONSTRUCTION_NOTE)

        document = ModelEvaluation(
            model_id=version.model_id,
            version=version.version,
            target=version.target,
            algorithm=version.algorithm,
            evaluation=evaluation,
            candidate_evaluations=candidate_evaluations,
            cross_validation=report,
            comparison=comparison,
            dataset_id=version.dataset_id,
            dataset_version=version.dataset_version,
            dataset_content_hash=version.dataset_content_hash,
            artifact_path=version.artifact_path,
            feature_names=list(version.feature_names or dataset.feature_names),
            split_strategy=split.strategy,
            split_detail=split.as_dict(),
            random_seed=version.random_seed,
            library_versions=dict(version.library_versions),
            evaluation_set=evaluation.evaluation_set,
            evaluated_rows=evaluation.evaluated_rows,
            trained_at=version.trained_at,
            evaluated_at=utcnow(),
            notes=list(caveats),
        )

        stored, recomputed = await self._store(document)
        if recomputed:
            logger.info(
                "Replaced the stored evaluation for %s %s rather than adding a second "
                "one. The run is deterministic given the recorded seed, so this is a "
                "refresh of one measurement and not a second one.",
                version.model_id,
                version.version,
            )
        return EvaluatedComparison(
            document=stored,
            comparison=comparison,
            recomputed=recomputed,
            caveats=caveats,
        )

    # -- reading ---------------------------------------------------------

    async def get_evaluation(self, model_id: str) -> ModelEvaluation:
        """The stored evaluation for one model id.

        Keyed on ``model_id`` rather than ``(model_id, version)`` because the
        registry's own key is ``model_id`` and a caller holding an id should not
        also have to know which version it resolved to; the version is on the
        document for the case where they do.
        """
        try:
            return await self._evaluation_repository().get("model_id", model_id)
        except DocumentNotFoundError as exc:
            raise EvaluationRefusalError(
                EvaluationRefusal(
                    detail=(
                        f"No evaluation has been run for model {model_id!r}. Run "
                        "POST /api/evaluation/run first: nothing is evaluated until a "
                        "caller asks, and a model merely existing is not evidence "
                        "that it has been scored."
                    ),
                    refusal="not_evaluable",
                    model_id=model_id,
                    reasons=["no stored evaluation for this model id"],
                ),
                not_found=True,
            ) from exc

    async def list_evaluations(
        self, *, target: ResearchTarget | None = None, limit: int = 200
    ) -> list[ModelEvaluation]:
        """Stored evaluations, newest first, for the comparison view."""
        repository = self._evaluation_repository()
        query = {"target": target.value} if target is not None else {}
        found = await repository.find(
            query, sort=[("evaluated_at", -1)], limit=limit
        )
        return sorted(found, key=lambda record: record.evaluated_at, reverse=True)

    async def list_models(self) -> list[ModelVersion]:
        """Registered model versions, newest first.

        Includes models that have never been evaluated, because the picker's list
        is a list of what *can* be evaluated and a model silently missing from it
        would be indistinguishable from one that does not exist.
        """
        found = await self._model_repository().find({}, limit=1000)
        return sorted(found, key=lambda record: record.created_at, reverse=True)

    def status(self) -> dict[str, Any]:
        """What this module does, stated without inflation.

        ``evaluated`` is False because nothing is evaluated until a caller names a
        model version to score. Reporting otherwise would suggest a report exists,
        and the whole module is arranged so that one does not exist until asked for.
        """
        return {
            "module": "evaluation",
            "number": 9,
            "implemented": True,
            "description": (
                "Scores every trained classifier on the rows it was held out on, "
                "per class and in aggregate, with confusion matrices and a "
                "structure-matched cross-validation where the data supports one. "
                "Stores the report against the model version it describes."
            ),
            "evaluated": False,
            "comparison_columns": [
                "model",
                "accuracy",
                "precision",
                "recall",
                "f1",
                "roc_auc",
                "pr_auc",
            ],
            "cross_validation_designs": [
                "blocked_temporal",
                "leave_pans_out",
                "stratified_kfold",
            ],
            "refusals": [
                "not_evaluable",
                "dataset_drift",
                "artifact_unavailable",
                "insufficient_data",
                "invalid_request",
            ],
            "does_not": (
                "This module does not rank candidates by any rule of its own, does "
                "not call a model best, and does not report a metric that was not "
                "computed. An absent metric is stored absent with its reason and "
                "renders as absent everywhere it appears, including in the export."
            ),
            "endpoints": {
                "run": "POST /api/evaluation/run",
                "read_one": "GET /api/evaluation/{model_id}/metrics",
                "compare": "GET /api/evaluation/compare",
                "export": "GET /api/evaluation/{model_id}/export",
            },
        }

    # -- internals -------------------------------------------------------

    async def _load_model(self, model_id: str) -> ModelVersion:
        """The registered model version, or a refusal that says it is missing."""
        try:
            return await self._model_repository().get("model_id", model_id)
        except DocumentNotFoundError as exc:
            raise EvaluationRefusalError(
                EvaluationRefusal(
                    detail=(
                        f"No model version is registered under {model_id!r}. There is "
                        "nothing to evaluate: this module scores models that were "
                        "fitted and registered, and will not stand one up on demand."
                    ),
                    refusal="not_evaluable",
                    model_id=model_id,
                    reasons=["no model version with that id"],
                ),
                not_found=True,
            ) from exc

    @staticmethod
    def _check_evaluable(version: ModelVersion) -> EvaluationRefusal | None:
        """Every condition that must hold before a figure can be computed.

        All collected in one place and all reported at once, for the same reason
        :func:`app.ml.sufficiency.check_sufficiency` collects all of its failures: a
        caller told about one missing field at a time has to guess which of the rest
        are also missing.
        """
        reasons: list[str] = []
        if not version.artifact_path:
            reasons.append(
                "the model version records no artifact_path, so there is no fitted "
                "estimator to score"
            )
        if not version.dataset_id:
            reasons.append(
                "the model version records no dataset_id, so the rows it was "
                "evaluated on cannot be rebuilt"
            )
        if not version.split_strategy:
            reasons.append(
                "the model version records no split_strategy, so the held-out rows "
                "cannot be reproduced; a fresh split would be a different experiment "
                "reported under a familiar name"
            )
        elif version.split_strategy not in SPLIT_STRATEGIES:
            reasons.append(
                f"the model version records split_strategy "
                f"{version.split_strategy!r}, which is not one of "
                f"{', '.join(SPLIT_STRATEGIES)}, so its rows cannot be reproduced"
            )
        if not version.feature_names:
            reasons.append(
                "the model version records no feature_names, so the feature frame "
                "cannot be rebuilt in the order the artifact was fitted to"
            )
        # The gate above asks whether the score could be computed. This asks
        # whether computing it would say anything about the world. A model fitted
        # to the demonstration pan satisfies every structural requirement above
        # and will produce a perfectly well-formed F1 that describes four typed
        # rows; the numbers being present is not the same as them being evidence.
        verdict = verdict_from_parameters(version.parameters)
        if not verdict.is_evidential:
            reasons.append(verdict.refusal_reason() or EVIDENCE_NOT_GENUINE_MESSAGE)
        if not reasons:
            return None
        return EvaluationRefusal(
            detail=NOT_EVALUABLE,
            refusal="not_evaluable",
            model_id=version.model_id,
            dataset_id=version.dataset_id,
            reasons=reasons,
        )

    def _load_artifact(self, version: ModelVersion) -> dict[str, Any]:
        """The persisted ``(preprocessor, estimator, classes)`` triple, or a refusal.

        Reads the file Module 08 wrote rather than refitting anything, so the
        headline figure is about the registered artifact. A missing file is a
        refusal and not an empty evaluation: the model exists as a registry row, and
        reporting metrics for it would be reporting on something that is not there.
        """
        try:
            path = resolve_artifact_path(
                self._artifact_directory, version.artifact_path or ""
            )
        except ArtifactPathError as exc:
            raise EvaluationRefusalError(
                EvaluationRefusal(
                    detail=(
                        f"{ARTIFACT_UNAVAILABLE} The registry records an artifact "
                        f"path for {version.model_id} {version.version} that "
                        "resolves outside the artifact directory, so it is refused."
                    ),
                    refusal="artifact_unavailable",
                    model_id=version.model_id,
                    dataset_id=version.dataset_id,
                    reasons=["artifact path escapes the artifact directory"],
                )
            ) from exc
        if not path.exists():
            raise EvaluationRefusalError(
                EvaluationRefusal(
                    detail=(
                        f"{ARTIFACT_UNAVAILABLE} The registry records "
                        f"{version.artifact_path} for {version.model_id} "
                        f"{version.version}, and that path does not exist on this "
                        "host. The evaluation is refused rather than produced from a "
                        "refitted model, because a figure attributed to a registered "
                        "artifact has to come from that artifact."
                    ),
                    refusal="artifact_unavailable",
                    model_id=version.model_id,
                    dataset_id=version.dataset_id,
                    reasons=[f"no file at {version.artifact_path}"],
                )
            )
        try:
            loaded = joblib.load(path)
        except Exception as exc:
            raise EvaluationRefusalError(
                EvaluationRefusal(
                    detail=(
                        f"{ARTIFACT_UNAVAILABLE} The file at {path} could not be "
                        f"loaded: {exc}. A model fitted by one library version is not "
                        "reliably loadable by another, and the versions the artifact "
                        "was fitted with are recorded on the model version."
                    ),
                    refusal="artifact_unavailable",
                    model_id=version.model_id,
                    dataset_id=version.dataset_id,
                    reasons=[f"joblib.load failed: {exc}"],
                )
            ) from exc
        missing = [
            key
            for key in ("preprocessing", "estimator", "classes")
            if key not in loaded
        ]
        if missing:
            raise EvaluationRefusalError(
                EvaluationRefusal(
                    detail=(
                        f"{ARTIFACT_UNAVAILABLE} The file at {path} has no "
                        f"{', '.join(missing)} entry, so it is not an artifact this "
                        "module wrote. It will not be scored under a description that "
                        "does not apply to it."
                    ),
                    refusal="artifact_unavailable",
                    model_id=version.model_id,
                    dataset_id=version.dataset_id,
                    reasons=[f"artifact is missing: {', '.join(missing)}"],
                )
            )
        return loaded

    def _reproduce_split(
        self,
        version: ModelVersion,
        dataset: Any,
        built: Any,
        preprocessing: Any,
    ) -> tuple[Any, dict[str, Any], np.ndarray, dict[str, Any], pd.DataFrame]:
        """Rebuild the labelled rows and split them exactly as training did.

        Returns the split, the two preprocessed matrices, the training label space,
        the row bookkeeping the scorer needs, and the untransformed feature frame the
        cross-validator resamples.

        Every input to the split comes from the model version -- strategy, fraction,
        seed -- so the held-out rows are the rows Module 08 held out, not a fresh cut
        that happens to be the same size. The *preprocessing* is the persisted
        pipeline rather than a refit, for the same reason: refitting it would
        re-learn imputation statistics and produce a matrix the registered estimator
        was never fitted on.
        """
        features = list(version.feature_names or dataset.feature_names)
        frame = build_feature_frame(built.rows, features)
        meta, labels = label_frame(built.rows)
        labelled = frame.loc[meta.index].reset_index(drop=True)
        meta = meta.reset_index(drop=True)

        if len(labels) == 0:
            raise EvaluationRefusalError(
                EvaluationRefusal(
                    detail=(
                        f"{NOT_EVALUABLE} The rebuilt dataset {version.dataset_id} "
                        "yields no labelled rows, so the held-out set the model was "
                        "scored on cannot be reconstructed."
                    ),
                    refusal="insufficient_data",
                    model_id=version.model_id,
                    dataset_id=version.dataset_id,
                    reasons=[
                        "the rebuilt dataset contains no labelled rows",
                        f"{len(built.rows)} row(s) rebuilt in total",
                    ],
                )
            )

        split_source = meta.copy()
        split_source["label"] = [str(value) for value in labels]
        test_size = version.test_size or DEFAULT_TEST_SIZE
        try:
            split = make_split(
                split_source,
                version.split_strategy or "chronological",
                test_size=test_size,
                random_seed=version.random_seed or 0,
            )
        except ValueError as exc:
            # The split no longer divides the rebuilt data. Reported with the reason
            # rather than substituted, because substituting a working split is how a
            # model ends up reported against rows nobody chose.
            raise EvaluationRefusalError(
                EvaluationRefusal(
                    detail=(
                        f"{NOT_EVALUABLE} The recorded split "
                        f"({version.split_strategy}, test_size={test_size}, "
                        f"seed={version.random_seed}) no longer divides the rebuilt "
                        f"dataset: {exc}"
                    ),
                    refusal="insufficient_data",
                    model_id=version.model_id,
                    dataset_id=version.dataset_id,
                    reasons=[str(exc), f"{len(labels)} labelled row(s) rebuilt"],
                )
            ) from exc

        train_index = np.asarray(split.train_indices)
        test_index = np.asarray(split.test_indices)
        all_labels = np.asarray(labels)
        train_matrix = apply_preprocessing(preprocessing, labelled.iloc[train_index])
        test_matrix = apply_preprocessing(preprocessing, labelled.iloc[test_index])
        # Classes from the training rows alone, exactly as training did. A class
        # that appears only in the held-out rows has no weights in the artifact and
        # must not become a column the scoring assumes exists.
        classes = np.unique(all_labels[train_index])

        # The structural columns travel with the feature frame so the cross-validator
        # can see the same pan and instant the split saw. `remainder="drop"` in the
        # preprocessor means they are never fed to an estimator as features; they are
        # here to be *split* on, which is a different job and needs them present.
        cross_frame = labelled.copy()
        cross_frame["pan_id"] = meta["pan_id"].to_numpy()
        cross_frame["anchor_timestamp"] = meta["anchor_timestamp"].to_numpy()

        return (
            split,
            {"train": train_matrix, "test": test_matrix},
            classes,
            {
                "all_labels": all_labels,
                "train_labels": all_labels[train_index],
                "test_labels": all_labels[test_index],
            },
            cross_frame,
        )

    def _evaluate_candidates(
        self,
        version: ModelVersion,
        artifact: dict[str, Any],
        matrices: dict[str, Any],
        row_meta: dict[str, Any],
        classes: np.ndarray,
        selected_evaluation: ClassificationEvaluation,
    ) -> dict[str, ClassificationEvaluation]:
        """One evaluation per candidate, every one on the same held-out rows.

        The selected candidate is the persisted artifact, scored by the caller. The
        others are refitted from the recorded seed through the persisted
        preprocessing, on the reproduced training rows. Both paths end at an
        identical :func:`app.ml.evaluation.score_estimator` call on identical rows, so
        the table's rows are comparable by construction rather than by assertion --
        and the table can be built from one map without special-casing the selected
        row.

        A candidate that is not named on the model version is still evaluated and
        still appears. The registry's ``algorithm`` is one string, and a comparison
        that dropped the three candidates it failed to mention would be a
        comparison of a filtered set presented as a complete one.
        """
        results: dict[str, ClassificationEvaluation] = {}
        for spec in candidate_specs():
            if spec.algorithm == version.algorithm:
                results[spec.algorithm] = selected_evaluation
                continue
            estimator = spec.build(version.random_seed or 0)
            estimator.fit(matrices["train"], row_meta["train_labels"])
            results[spec.algorithm] = score_estimator(
                estimator,
                matrices["test"],
                row_meta["test_labels"],
                classes,
                EVALUATION_SET,
            )
        if version.algorithm and version.algorithm not in results:
            # The registry names an algorithm this candidate set does not contain.
            # The selected row is still reported, under the name the registry gave
            # it, and the table says the candidate set did not produce it.
            logger.warning(
                "Model %s %s names algorithm %r, which is not in the candidate set; "
                "it is reported under the registry's name and the other candidates "
                "beside it.",
                version.model_id,
                version.version,
                version.algorithm,
            )
            results[version.algorithm] = selected_evaluation
        return results

    def _check_reconstruction(
        self,
        version: ModelVersion,
        candidate_evaluations: dict[str, ClassificationEvaluation],
    ) -> list[str]:
        """Compare this module's figures against Module 08's own recorded scores.

        Module 08 stored a handful of floats per candidate. Those and this module's
        figures are two computations of the same thing, and if they disagree the
        honest response is to say so rather than to publish one of them as though the
        other did not exist. A disagreement inside the tolerance is floating-point
        noise from a refit and is not worth a caveat; a disagreement outside it is
        worth exactly one, and it names the metric and both figures.
        """
        recorded = version.parameters.get("candidate_metrics") or {}
        if not isinstance(recorded, dict) or not recorded:
            return []
        caveats: list[str] = []
        for algorithm, evaluation in sorted(candidate_evaluations.items()):
            from_training = recorded.get(algorithm)
            if not isinstance(from_training, dict):
                continue
            for attribute, recorded_key in RECONSTRUCTION_KEYS.items():
                expected = from_training.get(recorded_key)
                reading = getattr(evaluation, attribute, None)
                if expected is None or reading is None:
                    continue
                if not reading.computed or reading.value is None:
                    continue
                if abs(float(expected) - reading.value) <= RECONSTRUCTION_TOLERANCE:
                    continue
                caveats.append(
                    f"{algorithm}.{attribute}: this module computed "
                    f"{reading.value:.6f} where the training run recorded "
                    f"{float(expected):.6f}. Both are reported rather than one being "
                    "preferred, because a disagreement between two computations of "
                    "the same quantity is a fact about the record and a reader should "
                    "see it instead of having it resolved silently."
                )
        return caveats

    def _cross_validate(
        self,
        version: ModelVersion,
        cross_frame: pd.DataFrame,
        row_meta: dict[str, Any],
        split: Any,
        requested_splits: int | None,
    ) -> Any:
        """Cross-validate, or return the refusal report explaining why it was declined.

        ``cross_frame`` is the *untransformed* labelled feature frame with the pan
        and anchor columns attached, because the resampler refits its own
        preprocessing inside every fold -- the same invariant Module 08 holds, and the
        reason each fold's score is worth reading. Positions line up with
        ``all_labels`` because both come from one ``label_frame`` pass over the same
        rows, and the columns it adds are dropped by ``remainder="drop"`` before any
        estimator sees them.
        """
        features = list(version.feature_names)
        return run_cross_validation(
            frame=cross_frame,
            labels=row_meta["all_labels"],
            feature_names=features,
            feature_kinds=feature_kinds(features),
            split_strategy=split.strategy,
            random_seed=version.random_seed or 0,
            requested_splits=requested_splits,
            specs=candidate_specs(),
        )

    async def _store(
        self, document: ModelEvaluation
    ) -> tuple[ModelEvaluation, bool]:
        """Insert the report, or replace the one already there for this version.

        One evaluation per model version, because the run is deterministic. A
        replace rather than a delete-then-insert, so a reader never observes a gap
        where the report was, and so the unique index on ``(model_id, version)`` is
        never transiently satisfied by nothing.
        """
        repository = self._evaluation_repository()
        try:
            return await repository.insert(document), False
        except DuplicateDocumentError:
            pass
        try:
            return (
                await repository.replace(
                    "evaluation_id", document.evaluation_id, document
                ),
                True,
            )
        except DocumentNotFoundError:
            # Raced with a delete. The insert is the right answer, and the unique
            # index will arbitrate if another writer got there first.
            return await repository.insert(document), False


def export_record(document: ModelEvaluation) -> dict[str, Any]:
    """The machine-readable experiment record for the JSON export.

    The full report, plus the flat per-candidate table and the criterion that chose
    the selected row. Everything a reader needs to reproduce the run or to argue with
    its numbers, and nothing that was not computed: the flat table is built from the
    same readings the API serves, so the export cannot contain a metric the response
    omitted, or omit one the response included.
    """
    record = document.experiment_record()
    record["comparison_table"] = flatten_readings(document.comparison)
    return record


def export_csv(document: ModelEvaluation) -> str:
    """The stored report's comparison table as CSV.

    A thin wrapper over :func:`csv_from_comparison` so the router and the export
    format agree, and so the CSV can be tested against a table rather than against a
    fully stored document it does not otherwise care about.
    """
    return csv_from_comparison(document.comparison)


def csv_from_comparison(comparison: Mapping[str, Any]) -> str:
    """The comparison table as CSV, with the reasons in their own columns.

    An absent metric is an empty cell. Never 0, and never 0.0, because a CSV is
    opened in a spreadsheet where a numeric column is summed by a click and a reason
    column is not read: a 0 in that column would be aggregated into a headline the
    data does not support. The ``*_computed`` and ``*_reason`` columns beside it are
    what make the emptiness interpretable rather than merely empty.
    """
    table = flatten_readings(comparison)
    if not table:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer, fieldnames=list(table[0].keys()), extrasaction="ignore"
    )
    writer.writeheader()
    for record in table:
        writer.writerow(
            {
                key: (
                    "" if value is None else value
                )
                for key, value in record.items()
            }
            | {
                key: sanitize_prose_cell(value)
                for key, value in record.items()
                if key.endswith("_reason") and value is not None
            }
        )
    return buffer.getvalue()


__all__ = [
    "ARTIFACT_UNAVAILABLE",
    "CANDIDATE_RECONSTRUCTION_NOTE",
    "EVALUATION_SET",
    "NOT_EVALUABLE",
    "RECONSTRUCTION_KEYS",
    "RECONSTRUCTION_TOLERANCE",
    "EvaluatedComparison",
    "EvaluationRefusalError",
    "EvaluationService",
    "csv_from_comparison",
    "export_csv",
    "export_record",
]
