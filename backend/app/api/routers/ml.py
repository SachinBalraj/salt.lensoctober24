"""Module 8 - training from real datasets, and refusing when the data is thin.

The interesting behaviour here is the refusal.

``POST /api/ml/train`` either trains, or returns 422 carrying the exact sentence
``"Insufficient labelled data for reliable model training."`` When it refuses it
writes no artifact and registers no model version. There is no partial success and
no "trained on what was available" -- a model fitted to a subset would report a
score computed on that subset and would be indistinguishable, in the registry, from
one fitted properly.

Two other refusals exist and are told apart by a ``refusal`` field rather than by
prose, because they call for different responses:

``dataset_drift``
    The records no longer hash to what the manifest recorded. The model would cite
    a dataset version its own training rows contradict. Rebuild, then train.
``unsupported_target``
    A regression target. The agreed candidate set is four classifiers, and this
    module will not fit a regression model under a name nobody approved.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response

from app.api.deps import get_db
from app.db.errors import DocumentNotFoundError
from app.ml.pipeline import InsufficientDataError
from app.ml.splitting import SPLIT_STRATEGIES
from app.ml.sufficiency import INSUFFICIENT_DATA_MESSAGE
from app.models.enums import ResearchTarget
from app.schemas.ml import (
    ExperimentListResponse,
    ExperimentSummary,
    InsufficientDataResponse,
    TrainRequestBody,
    TrainResponse,
)
from app.services.ml import (
    SUPPORTED_TARGETS,
    DatasetDriftError,
    MLService,
    TrainRequest,
    UnsupportedTargetError,
)

router = APIRouter(prefix="/api/ml", tags=["08-ml-training"])

DESCRIPTION = (
    "Trains classification models from real stored datasets. Every reported "
    "metric is computed on held-out rows from the model that was fitted. Refuses "
    "to train when the data cannot support a model and never synthesises data to "
    "make one possible."
)

#: Stated as a set of refusals rather than as a description, because the useful
#: question about a training endpoint is what it will decline to do. The first
#: sentence used to read "Model training is not implemented in this module",
#: which contradicted the ``implemented: true`` directly above it.
WHAT_THIS_MODULE_DOES_NOT_DO = (
    "This module accepts a real stored dataset, checks that it can support a "
    "model, splits it without leaking information between the training and "
    "held-out sides, fits four classification candidates, and reports each one's "
    "held-out scores. It will not synthesise rows, resample a rare class, borrow "
    "labels, or return a model fitted to fewer rows than the sufficiency gate "
    "requires. Random Forest is fitted and scored on every run but is never "
    "selected automatically, because on the data volumes this system holds it "
    "wins by memorising."
)


def get_ml_service() -> MLService:
    """Build the service with a lazy database handle.

    Same shape as the other routers' providers: ``get_db`` is passed rather than
    called, so an unconfigured database raises its 503 inside the handler and a
    malformed request is still a 422 regardless of whether Mongo is reachable.
    """
    return MLService(get_db)


ServiceDependency = Annotated[MLService, Depends(get_ml_service)]


@router.get("/", summary="Module 8 implementation status")
def module_status() -> dict[str, Any]:
    """What this module does, and what it refuses to do."""
    return {
        "module": "ml",
        "number": 8,
        "implemented": True,
        "description": DESCRIPTION,
        # False because nothing is trained until a caller supplies a dataset that
        # can support one. Saying otherwise would imply a model already exists.
        "trained": False,
        # Stated separately from `trained` because the two can differ, and
        # conflating them is how a fixture-fitted score ends up quoted as a field
        # result. A model can be trained against demonstration data -- that is how
        # this pipeline is tested -- and it will be a perfectly good model that
        # says nothing about any salt pan. This count is the number of models
        # whose training rows were genuine, and it is zero.
        "evidential_models": 0,
        "evidence_policy": (
            "Every training run records whether its rows trace to measured "
            "records of the pan, and Module 09 refuses to report the held-out "
            "scores of a model whose rows were not genuine. A score can therefore "
            "never be quoted as real-world performance merely for having been "
            "computed."
        ),
        "candidates": [
            "logistic_regression",
            "decision_tree",
            "random_forest",
            "gradient_boosting",
        ],
        "selection_rule": (
            "Highest macro F1 among automatically selectable candidates. Random "
            "Forest is always fitted and scored, and never selected unless the "
            "request sets allow_random_forest."
        ),
        "split_strategies": list(SPLIT_STRATEGIES),
        "supported_targets": sorted(t.value for t in SUPPORTED_TARGETS),
        "insufficient_data_message": INSUFFICIENT_DATA_MESSAGE,
        "note": WHAT_THIS_MODULE_DOES_NOT_DO,
        "endpoints": {
            "train": "POST /api/ml/train",
            "list_experiments": "GET /api/ml/experiments",
            "read_an_experiment": "GET /api/ml/experiments/{model_id}",
            "readiness": "GET /api/ml/readiness",
        },
    }


@router.get("/readiness", summary="Dataset audit behind the training gate")
async def dataset_readiness(service: ServiceDependency) -> dict[str, Any]:
    """Report how many genuine labelled rows exist and whether the gate opens.

    The verdict is computed from the live database, never estimated: the counts
    are what is stored, the thresholds are Module 8's own sufficiency numbers,
    and the fixed ``INSUFFICIENT_DATA_MESSAGE`` is returned when the data cannot
    support a model. Nothing is fitted or registered by this endpoint, and while
    the gate stays shut operational ML prediction remains unavailable.
    """
    return await service.dataset_readiness()


@router.post(
    "/train",
    summary="Fit and register a model from a stored dataset",
    status_code=status.HTTP_201_CREATED,
    # Declared rather than inferred: the success and refusal paths return
    # different shapes, and a union return annotation is not something FastAPI can
    # build a response model from. Naming both under `responses` documents them
    # in the OpenAPI schema, which is the point of declaring them.
    response_model=None,
    responses={
        201: {"model": TrainResponse, "description": "A model was fitted and registered."},
        # The refusal is documented as a first-class outcome, not an error detail.
        # A client reading this contract should see that 422 is an expected
        # response meaning "this dataset cannot support a model", which is a
        # different thing from a 422 meaning "your request was malformed".
        422: {
            "model": InsufficientDataResponse,
            "description": (
                "The dataset cannot support a model, or its target is not trainable "
                "here. Nothing was written."
            ),
        },
        409: {
            "model": InsufficientDataResponse,
            "description": (
                "The stored records no longer match the dataset manifest, so a "
                "model fitted now would cite a version its own rows contradict."
            ),
        },
    },
)
async def train_model(
    body: TrainRequestBody, service: ServiceDependency
) -> Response:
    """Train, evaluate on held-out rows, persist, and register.

    Returns 201 with the full comparison when the data supports a model. Returns
    422 with ``"Insufficient labelled data for reliable model training."`` when it
    does not, having written nothing at all.

    The response carries every candidate's scores, not only the selected model's.
    Fitting four candidates is only worth doing if the comparison is visible, and
    the gap between the simple models and Random Forest is the number that says
    whether the dataset is large enough to support a model at all.
    """
    request = TrainRequest(
        dataset_id=body.dataset_id,
        name=body.name,
        split_strategy=body.split_strategy,
        test_size=body.test_size,
        random_seed=body.random_seed,
        allow_random_forest=body.allow_random_forest,
    )
    try:
        record = await service.train(request)
    except InsufficientDataError as exc:
        # 422, not 400 or 409. The request was well-formed; the data it named
        # cannot support a model, which is a different situation from a malformed
        # body and is told apart by `refusal` in the body.
        #
        # `detail` is the exact fixed sentence and nothing else. The reasons are a
        # separate field, because a client that checks for the sentence should not
        # have to parse a message whose shape changes with the dataset.
        report = exc.report
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=InsufficientDataResponse(
                detail=exc.message,
                reasons=list(report.reasons),
                dataset_id=body.dataset_id,
                labelled_rows=report.labelled_rows,
                class_counts=report.class_counts,
                distinct_pans=report.distinct_pans,
                refusal="insufficient_data",
            ).model_dump(mode="json"),
        )
    except DatasetDriftError as exc:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=InsufficientDataResponse(
                detail=str(exc),
                refusal="dataset_drift",
                dataset_id=body.dataset_id,
            ).model_dump(mode="json"),
        )
    except UnsupportedTargetError as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=InsufficientDataResponse(
                detail=str(exc),
                refusal="unsupported_target",
                dataset_id=body.dataset_id,
            ).model_dump(mode="json"),
        )
    except (ValueError, LookupError) as exc:
        # A split that cannot be made, or a feature the catalogue does not know.
        # These are caller errors -- a held-out fraction that leaves no training
        # rows, an unknown split strategy -- so they are 422s naming the problem,
        # not 500s. The schema already bounds test_size to (0, 1), which is not
        # the same as bounding it to something this dataset can satisfy.
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=InsufficientDataResponse(
                detail=str(exc),
                refusal="invalid_request",
                dataset_id=body.dataset_id,
            ).model_dump(mode="json"),
        )

    response = TrainResponse(
        model_id=record.model_id,
        version=record.version,
        target=record.target,
        algorithm=record.algorithm or "",
        status=record.status.value,
        metrics=record.metrics,
        candidate_metrics=dict(record.parameters.get("candidate_metrics", {})),
        selection_reason=str(record.parameters.get("selection_reason", "")),
        allow_random_forest=bool(record.parameters.get("allow_random_forest", False)),
        random_seed=record.random_seed or 0,
        split_strategy=record.split_strategy or body.split_strategy,
        split_detail=record.split_detail,
        feature_names=record.feature_names,
        preprocessing=dict(record.parameters.get("preprocessing", {})),
        dataset_id=record.dataset_id or body.dataset_id,
        dataset_version=record.dataset_version or 1,
        dataset_content_hash=record.dataset_content_hash or "",
        library_versions=record.library_versions,
        trained_at=record.trained_at or record.created_at,
        training_duration_seconds=record.training_duration_seconds or 0.0,
        artifact_path=record.artifact_path or "",
    )
    # Serialised explicitly because response_model is disabled for this route: the
    # success and refusal bodies are different shapes and a union return type is
    # not a model FastAPI can build.
    return JSONResponse(
        status_code=status.HTTP_201_CREATED, content=response.model_dump(mode="json")
    )


@router.get("/experiments", summary="List registered model versions")
async def list_experiments(
    service: ServiceDependency,
    target: Annotated[
        ResearchTarget | None,
        Query(description="Restrict to one research target."),
    ] = None,
) -> ExperimentListResponse:
    """Registered model versions, newest first.

    Every model this module has ever registered, including the ones whose
    algorithm is Random Forest. The registry is a record of what was fitted, and
    filtering a fitted model out of the list would make the record incomplete.
    """
    records = await service.list_experiments(target=target)
    return ExperimentListResponse(
        experiments=[
            ExperimentSummary(
                model_id=record.model_id,
                version=record.version,
                target=record.target,
                status=record.status.value,
                algorithm=record.algorithm,
                dataset_id=record.dataset_id,
                dataset_version=record.dataset_version,
                metrics=record.metrics,
                feature_names=record.feature_names,
                split_strategy=record.split_strategy,
                random_seed=record.random_seed,
                library_versions=record.library_versions,
                trained_at=record.trained_at,
                training_duration_seconds=record.training_duration_seconds,
                artifact_path=record.artifact_path,
            )
            for record in records
        ],
        count=len(records),
        available_targets=sorted(t.value for t in SUPPORTED_TARGETS),
        split_strategies=list(SPLIT_STRATEGIES),
    )


@router.get(
    "/experiments/{model_id}",
    summary="Read one registered model version",
    responses={404: {"description": "No model with that id."}},
)
async def read_experiment(
    model_id: str, service: ServiceDependency
) -> ExperimentSummary:
    """The full record for one model version, including its provenance.

    Everything needed to say what was fitted to what: the dataset version and
    content hash, the features, the split, the seed, the hyper-parameters, the
    library versions, and the scores of all four candidates.
    """
    try:
        record = await service.get_experiment(model_id)
    except DocumentNotFoundError as exc:
        # A 404 here means the model id is wrong, not that training is
        # unavailable. The two were once conflated by this route reporting a
        # placeholder, and a client could not tell them apart.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No model version with id {model_id!r}.",
        ) from exc
    return ExperimentSummary(
        model_id=record.model_id,
        version=record.version,
        target=record.target,
        status=record.status.value,
        algorithm=record.algorithm,
        dataset_id=record.dataset_id,
        dataset_version=record.dataset_version,
        metrics=record.metrics,
        feature_names=record.feature_names,
        split_strategy=record.split_strategy,
        random_seed=record.random_seed,
        library_versions=record.library_versions,
        trained_at=record.trained_at,
        training_duration_seconds=record.training_duration_seconds,
        artifact_path=record.artifact_path,
    )
