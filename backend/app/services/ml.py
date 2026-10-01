"""Module 8 - the training service, and the registry it writes to.

This module is where the pipeline meets the database: it turns a stored dataset
into a fitted, persisted, registered model, or it refuses and explains why.

Four things it does that the pipeline deliberately does not, because they are
concerns of the application rather than of the estimator:

**It refuses to train against a dataset that has drifted.** The pipeline is handed
rows; it cannot know whether those rows are the ones its manifest describes. If
the records have changed since the build, a model registered against that dataset
id would cite a version number its own training rows contradict. So the dataset is
materialized, its digest compared, and the run stops on a mismatch.

**It refuses targets the pipeline does not support.** Classification only. A
regression target is rejected by name rather than fitted with a substitute
algorithm nobody approved -- an unrequested model quietly trained under a
requested name is worse than a clear refusal.

**It allocates the version.** The registry's unique key is ``(target, version)``,
so the next version is read from what exists and advanced. Allocated here rather
than inside the pipeline, which has no database and should stay testable without
one.

**It registers a model as a candidate, never a promoted one.** A held-out score is
not a decision to deploy. Nothing in this service can mark a model promoted,
because nothing here has seen how it behaves in operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db.collections import MODEL_VERSIONS
from app.db.repository import MongoRepository
from app.ml.pipeline import (
    DEFAULT_RANDOM_SEED,
    DEFAULT_SPLIT_STRATEGY,
    DEFAULT_TEST_SIZE,
    InsufficientDataError,
    TrainingOutcome,
    train,
)
from app.ml.provenance import (
    EvidenceVerdict,
    classify_evidence,
    demonstration_target_ids,
)
from app.ml.splitting import SPLIT_STRATEGIES
from app.ml.sufficiency import INSUFFICIENT_DATA_MESSAGE, SufficiencyReport
from app.models.base import new_id
from app.models.dataset import ResearchDataset
from app.models.enums import ModelStatus, ResearchTarget
from app.models.model_version import ModelVersion
from app.services.dataset_builder import BuiltDataset
from app.services.dataset_catalog import feature_kinds
from app.services.datasets import DatasetService

#: Where fitted pipelines are written. Under ``ml/models`` because that is the
#: directory the project already reserves for them and ignores in git; a
#: regenerated artifact is not something to review in a diff, whereas the
#: registry record describing it absolutely is.
ARTIFACT_DIRECTORY = Path(__file__).resolve().parents[3] / "ml" / "models"

#: Targets this module will fit. Classification only, by decision rather than
#: omission: the four agreed candidates are classifiers, and a regression target
#: should wait for a regression candidate set that has been chosen on purpose.
SUPPORTED_TARGETS: frozenset[ResearchTarget] = frozenset(
    {
        ResearchTarget.POST_RAIN_SALINITY_BAND,
        ResearchTarget.HARVEST_OUTCOME,
        ResearchTarget.OPERATIONAL_RISK,
    }
)


class DatasetDriftError(RuntimeError):
    """The stored records no longer match the dataset manifest.

    Raised instead of training, because the alternative is registering a model
    against a ``dataset_id`` and ``version`` whose rows it was not fitted to. The
    fix is a rebuild, and the message says so.
    """


class UnsupportedTargetError(ValueError):
    """This module will not fit that target, and will not substitute one."""


class LeakageDetectedError(ValueError):
    """The manifest declares a feature that was not knowable at prediction time.

    Raised at training time rather than left to the builder, so that a manifest
    which has drifted out of agreement with the feature catalogue cannot reach an
    estimator. Refusing is the only option that leaves the reported scores
    meaningful: there is no way to fit the model and then caveat the number.
    """


@dataclass(frozen=True)
class TrainRequest:
    """What the caller asks for. Everything has a defensible default."""

    dataset_id: str
    #: An optional human label for the model, carried to the registry record and
    #: shown on a prediction. Never used to resolve a model.
    name: str | None = None
    split_strategy: str = DEFAULT_SPLIT_STRATEGY
    test_size: float = DEFAULT_TEST_SIZE
    random_seed: int = DEFAULT_RANDOM_SEED
    # Off by default. Random Forest is a candidate in every run and is never
    # chosen automatically; promoting it is a deliberate act, recorded on the
    # model version as ``allow_random_forest``.
    allow_random_forest: bool = False


class MLService:
    """Train, persist and register models from real stored datasets."""

    def __init__(self, database: Any, artifact_directory: Path | None = None) -> None:
        self._database = database
        self._artifact_directory = artifact_directory or ARTIFACT_DIRECTORY
        self._dataset_service = DatasetService(database)

    def _model_repository(self) -> MongoRepository[ModelVersion]:
        return MongoRepository(self._database, MODEL_VERSIONS, ModelVersion)

    async def _evidence_of(self, built: BuiltDataset) -> EvidenceVerdict:
        """Classify the evidence behind a build, reading the outcome flag itself.

        The rows carry the id of the record each label came from but not whether
        that record was a demonstration, so the ids are resolved here against the
        stored outcomes. Only the pans the dataset actually draws on are queried:
        a demonstration outcome belonging to a pan that is not in this dataset
        says nothing about the dataset's own rows.
        """
        pan_ids = sorted({row.pan_id for row in built.rows if row.pan_id})
        if not pan_ids:
            return classify_evidence(built.rows)

        outcomes = await _all_outcomes_for_pans(self._database, pan_ids)
        return classify_evidence(
            built.rows,
            demonstration_targets=demonstration_target_ids(outcomes),
        )

    async def _next_version(self, target: ResearchTarget) -> int:
        """The next free version number for a target.

        One past the highest existing version. A gap left by a deleted document is
        reused rather than preserved, so version numbers stay dense and readable;
        the registry is the record of what exists, not of what was once there.

        Scans the target's versions rather than counting them, because "next" is a
        function of the highest number present, not of how many are. Counting would
        hand out v3 for a registry holding v1 and v7.
        """
        existing = await self._model_repository().find(
            {"target": target.value}, limit=1000
        )
        highest = 0
        for version in existing:
            head = version.version.lstrip("v").split(".")[0]
            try:
                highest = max(highest, int(head))
            except ValueError:
                # A version string that is not semver-shaped cannot be ordered
                # against these, so it is ignored rather than guessed at.
                continue
        return highest + 1

    @staticmethod
    def _check_target(target: ResearchTarget) -> None:
        if target not in SUPPORTED_TARGETS:
            raise UnsupportedTargetError(
                f"Target {target.value!r} is not trainable here. This module fits "
                f"classification targets only ({', '.join(sorted(t.value for t in SUPPORTED_TARGETS))}), "
                "because the agreed candidate set -- logistic regression, decision "
                "tree, random forest, gradient boosting -- is a set of classifiers. "
                "A regression target needs its own candidate set chosen and "
                "recorded first, rather than a substitute fitted under a name "
                "nobody approved."
            )

    @staticmethod
    def _check_leakage(dataset: ResearchDataset) -> None:
        """Re-verify the manifest's leakage claim at training time.

        The builder refuses a post-anchor variable when the dataset is built, so
        a manifest that reached this point should already be clean. Checking
        again is not redundancy for its own sake: this is the last point before
        an estimator is fitted, and a manifest that was hand-edited, written by
        an older builder, or restored from a backup can carry a feature the
        current one would have refused.

        The cost of trusting the manifest instead is invisible in the output. A
        model handed a post-event measurement trains cleanly, scores well, and
        records no warning -- the score is a description of the label leaking
        through a second column, and nothing in the record says so.
        """
        by_name = {
            definition.name: definition for definition in (dataset.features or ())
        }
        offending = sorted(
            name
            for name in dataset.feature_names
            if name in by_name and not by_name[name].leakage_class.is_known_at_anchor
        )
        if not offending:
            return
        raise LeakageDetectedError(
            f"Dataset {dataset.dataset_id} declares feature(s) {offending} as "
            f"measured after the anchor instant, and their leakage class is not "
            "knowable at prediction time. A model fitted to these would be scored "
            "on information it could not have had. Rebuild the dataset against the "
            "current feature catalogue, or remove the column from the manifest and "
            "rebuild, rather than training on it."
        )

    async def train(self, request: TrainRequest) -> ModelVersion:
        """Fit, evaluate, persist and register a model. Or refuse, with a reason.

        The refusal paths are the point as much as the success path. A dataset too
        small, too lopsided, or too few pans produces
        ``InsufficientDataError`` and nothing else: no artifact on disk, no row in
        the registry, no metric anywhere. The module would rather answer "no" than
        return a number that will be believed.
        """
        materialized = await self._dataset_service.materialize(request.dataset_id)
        dataset: ResearchDataset = materialized.dataset
        built: BuiltDataset = materialized.built

        if materialized.drift:
            raise DatasetDriftError(
                f"Dataset {request.dataset_id} no longer matches its manifest. The "
                f"records now hash to {materialized.content_hash}, while the "
                f"manifest records {materialized.recorded_content_hash}. A model "
                "fitted to these rows would cite a dataset version its own training "
                "data contradicts. Rebuild the dataset "
                f"(POST /api/datasets/{request.dataset_id}/rebuild) and train again."
            )

        target = self._target_of(dataset)
        self._check_target(target)

        if target == ResearchTarget.OPERATIONAL_RISK:
            # A risk-labelled dataset is only meaningful if its rows actually carry
            # the rule's labels. The manifest names the rule, and the builder
            # reloads it, but a dataset whose rows came back unlabelled would pass
            # the generic checks and produce a model over an empty target.
            labelled = [row for row in built.rows if row.is_labelled]
            if not labelled:
                raise InsufficientDataError(
                    _unlabelled_risk_report(dataset, built)
                )

        kinds = feature_kinds(dataset.feature_names)
        self._check_leakage(dataset)
        evidence = await self._evidence_of(built)
        model_id = new_id("model")
        next_version = await self._next_version(target)

        outcome: TrainingOutcome = train(
            rows=built.rows,
            feature_names=list(dataset.feature_names),
            feature_kinds=kinds,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.version,
            dataset_content_hash=dataset.content_hash,
            target=target.value,
            artifact_directory=self._artifact_directory,
            next_version=next_version,
            model_id=model_id,
            split_strategy=request.split_strategy,
            test_size=request.test_size,
            random_seed=request.random_seed,
            allow_random_forest=request.allow_random_forest,
        )

        # The target definition travels with the model, not just its name. For a
        # risk target the rule's id, version and specification hash are what make
        # the labels describable, and a model whose provenance is a bare string
        # cannot be re-derived against a revised rule.
        parameters: dict[str, Any] = {
            **outcome.candidate_metrics.get(outcome.algorithm, {}),
            "random_seed": outcome.random_seed,
            "test_size": request.test_size,
            "split_strategy": outcome.split_strategy,
            "allow_random_forest": outcome.allow_random_forest,
            "candidate_metrics": outcome.candidate_metrics,
            "selection_reason": outcome.selection_reason,
            "preprocessing": outcome.preprocessing.as_dict(),
            "split_detail": outcome.split_detail,
            "feature_kinds": kinds,
            # Recorded on every run, including the ones that are only exercising
            # the pipeline. Module 09 reads this back to decide whether a score
            # may be quoted as real-world performance, so a model fitted to
            # fixture data is distinguishable from one fitted to a field
            # campaign without anyone having to remember which was which.
            "evidence": evidence.as_dict(),
        }
        record = ModelVersion(
            model_id=model_id,
            name=request.name,
            version=outcome.version,
            target=target,
            status=ModelStatus.EXPERIMENTAL,
            algorithm=outcome.algorithm,
            framework=f"scikit-learn {outcome.library_versions['scikit-learn']}",
            dataset_id=dataset.dataset_id,
            artifact_path=outcome.artifact_path,
            parameters=parameters,
            metrics=outcome.metrics,
            test_size=request.test_size,
            trained_at=outcome.trained_at,
            random_seed=outcome.random_seed,
            dataset_content_hash=outcome.dataset_content_hash,
            dataset_version=outcome.dataset_version,
            feature_names=outcome.feature_names,
            split_strategy=outcome.split_strategy,
            split_detail=outcome.split_detail,
            library_versions=outcome.library_versions,
            training_duration_seconds=outcome.training_duration_seconds,
            # The record exists because of the run, so it carries the run's
            # timestamp rather than the marginally later instant the document was
            # constructed. That also keeps the audit-time invariant -- created_at
            # is not later than trained_at -- true without loosening the check.
            created_at=outcome.trained_at,
            updated_at=outcome.trained_at,
        )
        return await self._model_repository().insert(record)

    async def dataset_readiness(self) -> dict[str, Any]:
        """The dataset audit behind the training gate, read from the live database.

        The wallet question is how many *genuine labelled* rows exist: outcomes
        that carry an actual rainfall and a measured post-event salinity and are
        not demonstrations of the worked-example kind. Everything else in the
        audit is a count of what is stored, so a reader can see exactly why the
        gate stays shut and can tell a zero apart from a missing figure.

        The verdict re-states the sufficiency gate's thresholds against these
        counts, with the fixed ``INSUFFICIENT_DATA_MESSAGE`` when it is not met.
        It never attempts a fit and never fabricates rows to reach a threshold,
        because reaching a threshold by invention is exactly what the gate exists
        to stop.
        """
        from app.db.collections import (
            ACTUAL_OUTCOMES,
            PAN_OBSERVATIONS,
            RESEARCH_DATASETS,
            RESEARCH_EXPERIMENTS,
            SALT_PANS,
            WEATHER_OBSERVATIONS,
        )
        from app.ml.sufficiency import (
            MIN_CLASSES,
            MIN_DISTINCT_PANS,
            MIN_ROWS_PER_CLASS,
            MIN_ROWS_PER_SPLIT,
            MIN_TOTAL_LABELLED_ROWS,
            MIN_TRAIN_ROWS_PER_CLASS,
        )
        from app.models.actual_outcome import ActualOutcome
        from app.models.experiment import ResearchExperiment
        from app.models.pan_observation import PanObservation
        from app.models.salt_pan import SaltPan
        from app.models.weather import WeatherObservation

        def _repository(collection: str, model: type[Any]) -> MongoRepository[Any]:
            return MongoRepository(self._database, collection, model)

        outcomes = _repository(ACTUAL_OUTCOMES, ActualOutcome)
        genuine_query: dict[str, Any] = {
            "is_demonstration": False,
            "actual_rainfall_mm": {"$ne": None},
            "post_event_salinity_g_l": {"$ne": None},
        }
        labelled_rows = await outcomes.count(genuine_query)
        distinct_pans = len(
            await outcomes.distinct("pan_id", genuine_query)
        )
        counts: dict[str, int] = {
            "registered_pans": await _repository(SALT_PANS, SaltPan).count(),
            "stored_observations": await _repository(
                PAN_OBSERVATIONS, PanObservation
            ).count(),
            "stored_weather": await _repository(
                WEATHER_OBSERVATIONS, WeatherObservation
            ).count(),
            "outcomes_on_file": await outcomes.count(),
            "datasets_on_file": await _repository(
                RESEARCH_DATASETS, ResearchDataset
            ).count(),
            "model_versions_on_file": await self._model_repository().count(),
            "experiments_on_file": await _repository(
                RESEARCH_EXPERIMENTS, ResearchExperiment
            ).count(),
        }
        thresholds: dict[str, int] = {
            "min_total_labelled_rows": MIN_TOTAL_LABELLED_ROWS,
            "min_rows_per_split": MIN_ROWS_PER_SPLIT,
            "min_classes": MIN_CLASSES,
            "min_rows_per_class": MIN_ROWS_PER_CLASS,
            "min_train_rows_per_class": MIN_TRAIN_ROWS_PER_CLASS,
            "min_distinct_pans": MIN_DISTINCT_PANS,
        }
        reasons: list[str] = []
        if labelled_rows < MIN_TOTAL_LABELLED_ROWS:
            reasons.append(
                f"only {labelled_rows} genuine labelled row(s) are on file; at "
                f"least {MIN_TOTAL_LABELLED_ROWS} are required."
            )
        if distinct_pans < MIN_DISTINCT_PANS:
            reasons.append(
                f"the labels belong to {distinct_pans} distinct pan(s); at least "
                f"{MIN_DISTINCT_PANS} are required."
            )
        ready = not reasons
        return {
            "verdict": "ready" if ready else "not_ready",
            "message": INSUFFICIENT_DATA_MESSAGE if not ready else (
                "Sufficient genuine labelled data is on file to open the training "
                "gate; fitting still requires a stored dataset and a deliberate run."
            ),
            "reasons": reasons,
            "genuine_labelled_rows": labelled_rows,
            "distinct_pans_in_labels": distinct_pans,
            "counts": counts,
            "thresholds": thresholds,
            "note": (
                "Operational ML prediction stays unavailable while the gate is "
                "shut, and no model is fitted or registered by this audit. "
                "Genuine rows are measured, non-demonstration outcomes; the "
                "counts above are what exists in the database, not a forecast "
                "of what will."
            ),
        }

    @staticmethod
    def _target_of(dataset: ResearchDataset) -> ResearchTarget:
        """The research target a dataset was built for.

        Read from the dataset's target name rather than inferred from the label
        column or the class count, because the name is the thing the researcher
        chose and everything else is a guess about it.
        """
        return ResearchTarget(dataset.target.name)

    async def list_experiments(
        self, *, target: ResearchTarget | None = None
    ) -> list[ModelVersion]:
        """Registered model versions, newest first.

        Newest first because this is a list of experiments and the recent ones are
        what a reader is looking at. Ordering by the insert rather than by the
        version string, because a version is per-target and "v2" of one target
        says nothing about the order relative to "v2" of another.
        """
        repository = self._model_repository()
        if target is not None:
            found = await repository.find({"target": target.value}, limit=1000)
        else:
            found = await repository.find({}, limit=1000)
        return sorted(found, key=lambda m: m.created_at, reverse=True)

    async def get_experiment(self, model_id: str) -> ModelVersion:
        return await self._model_repository().get("model_id", model_id)

    def status(self) -> dict[str, Any]:
        """What this module does, stated without inflation.

        ``trained`` is False because nothing is trained until a caller asks with a
        dataset that can support it. Reporting otherwise would suggest a model
        exists, and the whole module is arranged so that it does not exist until
        there is data behind it.
        """
        return {
            "module": "ml",
            "number": 8,
            "implemented": True,
            "description": (
                "Trains classification models from real stored datasets and "
                "reports metrics computed on held-out rows. Refuses to train "
                "when the data cannot support a model, and never synthesises data "
                "to make one possible."
            ),
            "trained": False,
            "evidential_models": 0,
            "evidence_policy": (
                "Every training run records whether its rows trace to measured "
                "records of the pan. Module 09 refuses to report the held-out "
                "scores of a model whose rows were not genuine, so a score can "
                "never be quoted as real-world performance on the strength of "
                "having been computed."
            ),
            "candidates": [
                "logistic_regression",
                "decision_tree",
                "random_forest",
                "gradient_boosting",
            ],
            "selection": (
                "Highest macro F1 among automatically selectable candidates. "
                "Random Forest is fitted and scored every run but is never "
                "selected automatically."
            ),
            "split_strategies": list(SPLIT_STRATEGIES),
            "supported_targets": sorted(t.value for t in SUPPORTED_TARGETS),
            "insufficient_data_message": INSUFFICIENT_DATA_MESSAGE,
            "endpoints": {
                "train": "POST /api/ml/train",
                "list_experiments": "GET /api/ml/experiments",
                "read_an_experiment": "GET /api/ml/experiments/{model_id}",
            },
        }


async def _all_outcomes_for_pans(database: Any, pan_ids: list[str]) -> list[Any]:
    """Every stored outcome for the given pans, paged.

    Paged rather than requested in one go: the repository caps a single read at
    1000 documents, so a campaign with more outcomes than that would otherwise be
    classified against its first page only -- and since the question is whether
    any of them is a demonstration, silently missing a page can turn a
    demonstration dataset into a genuine-looking one. A short page is the only
    end-of-data signal this repository offers, so the loop stops there.
    """
    from app.db.collections import ACTUAL_OUTCOMES
    from app.models.actual_outcome import ActualOutcome

    if not pan_ids:
        return []
    repository: MongoRepository[Any] = MongoRepository(
        database, ACTUAL_OUTCOMES, ActualOutcome
    )
    page_size = 1000
    found: list[Any] = []
    page = 0
    while True:
        batch = await repository.find(
            {"pan_id": {"$in": pan_ids}}, skip=page * page_size, limit=page_size
        )
        found.extend(batch)
        if len(batch) < page_size:
            return found
        page += 1


def _unlabelled_risk_report(dataset: ResearchDataset, built: BuiltDataset) -> SufficiencyReport:
    """The refusal returned when a risk dataset's rows carry no labels.

    Carries the same message as every other insufficient-data refusal, so a caller
    handling this condition handles one string rather than two. A caller that got
    "the rule matched nothing" from one path and the standard message from another
    would have to parse prose to tell whether the two were the same situation.
    """
    return SufficiencyReport(
        sufficient=False,
        message=INSUFFICIENT_DATA_MESSAGE,
        reasons=(
            (
                "the risk definition produced no labelled rows over the stored "
                "records, so there is nothing to fit. Review the rule's criteria "
                "and the dataset's pan and time window with "
                "POST /api/risk/definitions/{definition_id}/distribution."
            ),
        ),
        labelled_rows=0,
        total_rows=len(built.rows),
        class_counts={},
        distinct_pans=0,
        feature_count=len(dataset.feature_names),
    )


__all__ = [
    "ARTIFACT_DIRECTORY",
    "SUPPORTED_TARGETS",
    "DatasetDriftError",
    "MLService",
    "TrainRequest",
    "UnsupportedTargetError",
]
