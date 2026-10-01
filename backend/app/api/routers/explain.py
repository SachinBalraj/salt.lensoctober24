"""Module 11 - SHAP explainability of real models, over stored predictions.

Every route here either returns attributions the ``shap`` library computed from a
registered artifact, or a refusal carrying a machine-readable reason. There is no
route that returns a plausible-looking attribution, and no field anywhere in the
response that a caller could mistake for a computed value when none was.

**The local route explains the row the model was actually given.** A stored
prediction carries its input snapshot and the content hash of that snapshot, so the
explanation is computed from the snapshot and the snapshot is checked against the
hash first. Rebuilding the row from the pan's current twin would be a different
row by the time anyone reads the explanation, and attributing a stored number to it
would be a false claim about a pan.

**The global route summarises a model version, not a prediction.** It explains the
held-out rows carried in the artifact -- the rows Module 09 scored the model on --
and averages each feature's absolute contribution across them. Stored under a
deterministic id, so asking again refreshes one row rather than accumulating
summaries that cannot be told apart.

**A refusal here is information, not an error to retry.** ``404`` means the
prediction or model version is not there. ``409`` means it is there and cannot be
explained, with a reason: a class-only model has no probability to decompose, an
artifact with no reference sample has no distribution to measure against, a snapshot
that no longer matches its hash is not the row the number came from, and
contributions that do not add up to the probability are not published. Each says
which, so a client does not have to read prose to decide what to do.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse

from app.api.deps import get_db
from app.schemas.explain import (
    ExplainRefusalResponse,
    GlobalImportanceResponse,
    LocalExplanationResponse,
    ModelExplanationListResponse,
)
from app.services.explain import (
    ADDITIVITY_TOLERANCE,
    GLOBAL_ROWS_DEFAULT,
    GLOBAL_ROWS_MAX,
    PERMUTATION_MAX_EVALS,
    ExplanationUnavailable,
    ShapExplanationService,
)

router = APIRouter(prefix="/api/explain", tags=["11-shap"])

DESCRIPTION = (
    "Attribution of a model decision to its input features, by SHAP values computed "
    "from the registered artifact: which features moved the predicted probability up, "
    "which moved it down, and by how much. One explanation per stored prediction, and "
    "one feature-importance summary per model version computed over its held-out rows."
)

#: Stated as a list of things this module will not do, because that is the useful
#: question to ask of an explainability endpoint.
WHAT_THIS_MODULE_DOES_NOT_DO = (
    "Values come from the shap library applied to the registered artifact, or there "
    "is no response. It will not estimate an importance by correlation, will not "
    "substitute a model's coefficients or a permutation of its own for a SHAP value, "
    "will not attribute a class-only model's class to a decision margin that was never "
    "reported, and will not explain a row other than the one stored with the "
    "prediction. An explanation is stored only if its contributions add back up to the "
    f"model's own probability within {ADDITIVITY_TOLERANCE:g}. Every response states "
    "which explainer ran, whether it computed or estimated the values, which shap "
    "version, and the reference distribution they were measured against."
)

QUANTITY_CONTRACT = (
    "Three quantities, reported apart and labelled apart. The model probability is the "
    "estimator's own output and is not an explanation of itself. A SHAP contribution is "
    "measured in probability points against a stated base value, and is not a "
    "probability: it is how far one feature moved the output from what the model would "
    "have said about a typical row. A physical simulation result is Module 06's mass "
    "balance for the pan, held in the input row as an input the model read -- not an "
    "output of the model, and not a contribution."
)


def get_explanation_service() -> ShapExplanationService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider rather than called here, so an unconfigured
    database raises its 503 inside the handler and a malformed path parameter is
    still a 404 regardless of whether Mongo is reachable.
    """
    return ShapExplanationService(get_db)


def get_explanation_status_service() -> ShapExplanationService:
    """The same provider, for the status endpoint.

    Named separately so the status route reads the database -- it reports whether
    anything has been explained -- without depending on the service the compute
    routes use.
    """
    return ShapExplanationService(get_db)


ServiceDependency = Annotated[ShapExplanationService, Depends(get_explanation_service)]
StatusDependency = Annotated[
    ShapExplanationService, Depends(get_explanation_status_service)
]


def _refusal_response(error: ExplanationUnavailable) -> JSONResponse:
    """Render a refusal with the status code its condition implies.

    The code decides, in one place: 404 when the prediction or model version is not
    there, 409 when it is there and cannot be explained. A new code therefore cannot
    arrive without choosing which of the two it belongs to.
    """
    return JSONResponse(
        status_code=error.status_code,
        content=ExplainRefusalResponse(
            detail=error.detail,
            refusal=error.code,
            model_id=error.model_id,
            prediction_id=error.prediction_id,
        ).model_dump(mode="json"),
    )


@router.get("/", summary="Module 11 implementation status")
async def module_status(service: StatusDependency) -> dict[str, Any]:
    """What this module computes, what it refuses, and what it has explained so far."""
    stored = await service.count_explanations()
    return {
        "module": "explain",
        "number": 11,
        "implemented": True,
        "description": DESCRIPTION,
        "explained": stored > 0,
        "stored_explanations": stored,
        "explained_output": "probability",
        "quantity_contract": QUANTITY_CONTRACT,
        "note": WHAT_THIS_MODULE_DOES_NOT_DO,
        "methods": {
            "exact": (
                "shap.TreeExplainer on a tree ensemble, with the artifact's training "
                "sample as the reference distribution and model_output='probability'."
            ),
            "approximate": (
                "shap's permutation explainer on the model's probability, used for "
                f"estimators the tree explainer does not accept, with at most "
                f"{PERMUTATION_MAX_EVALS} evaluations. Responses from it carry "
                "approximate: true."
            ),
            "not_used": (
                "No kernel approximation, no sampled linear explanation, and no "
                "coefficient or correlation figure reported as a SHAP value."
            ),
        },
        "additivity": (
            "Contributions plus the base value must equal the stored probability "
            f"within {ADDITIVITY_TOLERANCE:g}, and the residual is reported as "
            "attribution_error. A wider gap is refused rather than published."
        ),
        "global_summary": {
            "rows": (
                f"Between 1 and {GLOBAL_ROWS_MAX} held-out rows from the artifact, "
                f"{GLOBAL_ROWS_DEFAULT} by default. Each row is explained against its "
                "own predicted class, and the row count actually used is reported."
            ),
            "measure": "Mean absolute SHAP contribution, in probability points.",
        },
        "endpoints": {
            "explain_a_prediction": "POST /api/explain/predictions/{prediction_id}",
            "read_a_prediction": "GET /api/explain/predictions/{prediction_id}",
            "global_importance": (
                "GET /api/explain/models/{model_id}/versions/{version}/importance"
            ),
            "list_for_a_model": (
                "GET /api/explain/models/{model_id}/versions/{version}/explanations"
            ),
        },
    }


@router.post(
    "/predictions/{prediction_id}",
    summary="Explain a stored prediction from its stored input row",
    response_model=None,
    responses={
        200: {
            "model": LocalExplanationResponse,
            "description": (
                "The explanation was recomputed and replaced an existing one for this "
                "prediction. 200 rather than 201, because the row already existed: the "
                "arithmetic is deterministic, so this is one answer refreshed."
            ),
        },
        201: {
            "model": LocalExplanationResponse,
            "description": "The explanation was computed and stored against the prediction.",
        },
        404: {
            "model": ExplainRefusalResponse,
            "description": "No prediction is stored under that id.",
        },
        409: {
            "model": ExplainRefusalResponse,
            "description": (
                "The prediction is there and cannot be explained. The body names which: "
                "a class-only model with no probability to decompose, an artifact that is "
                "not on this host, an artifact with no reference sample, a snapshot that "
                "no longer matches its own hash, a class the artifact does not emit, or "
                "contributions that do not reconcile with the stored probability. No "
                "attribution is returned in any of these cases."
            ),
        },
    },
)
async def explain_prediction(
    prediction_id: str, service: ServiceDependency
) -> Any:
    """Compute and store the SHAP attribution for one stored prediction.

    Called automatically by ``POST /api/risk/predict``, so an operational prediction
    arrives with its explanation already stored. This route exists to compute one
    for a prediction made before, or to refresh one after a library upgrade.
    """
    try:
        body, replaced = await service.explain_prediction(prediction_id)
    except ExplanationUnavailable as error:
        return _refusal_response(error)
    return JSONResponse(
        status_code=status.HTTP_200_OK if replaced else status.HTTP_201_CREATED,
        content=LocalExplanationResponse(**body).model_dump(mode="json"),
    )


@router.get(
    "/predictions/{prediction_id}",
    summary="Read the stored explanation for a prediction",
    response_model=LocalExplanationResponse,
    responses={
        404: {
            "model": ExplainRefusalResponse,
            "description": "No prediction is stored under that id.",
        },
        409: {
            "model": ExplainRefusalResponse,
            "description": (
                "The prediction is there but has no stored explanation. The body says "
                "whether none has been asked for yet, or whether asking cannot help."
            ),
        },
    },
)
async def read_explanation(
    prediction_id: str, service: ServiceDependency
) -> Any:
    """Return the stored attribution, or the reason there is not one.

    Distinguishes "nobody has asked" from "asking will not help", because a client
    that cannot tell them apart retries a request that cannot succeed.
    """
    try:
        body = await service.read_explanation(prediction_id)
    except ExplanationUnavailable as error:
        return _refusal_response(error)
    return LocalExplanationResponse(**body).model_dump(mode="json")


@router.get(
    "/models/{model_id}/versions/{version}/importance",
    summary="Global SHAP feature importance for a model version",
    response_model=GlobalImportanceResponse,
    responses={
        404: {
            "model": ExplainRefusalResponse,
            "description": "No model version is registered under that pair.",
        },
        409: {
            "model": ExplainRefusalResponse,
            "description": (
                "The model version is registered and cannot be summarised: its "
                "estimator emits no probabilities, its artifact carries no held-out "
                "sample, or SHAP refused the estimator. The body names which."
            ),
        },
    },
)
async def global_importance(
    model_id: str,
    version: str,
    service: ServiceDependency,
    rows: Annotated[
        int | None,
        Query(
            ge=1,
            le=GLOBAL_ROWS_MAX,
            description=(
                "Held-out rows to explain. Each row is explained on its own, so the "
                "cost is real; the count actually used is reported in the response."
            ),
        ),
    ] = None,
) -> Any:
    """Mean absolute SHAP contribution per feature, over the model's held-out rows."""
    try:
        body = await service.global_importance(model_id, version, rows=rows)
    except ExplanationUnavailable as error:
        return _refusal_response(error)
    return GlobalImportanceResponse(**body).model_dump(mode="json")


@router.get(
    "/models/{model_id}/versions/{version}/explanations",
    summary="Stored explanations for a model version",
    response_model=ModelExplanationListResponse,
    responses={
        404: {
            "model": ExplainRefusalResponse,
            "description": "No model version is registered under that pair.",
        },
    },
)
async def list_for_model(
    model_id: str, version: str, service: ServiceDependency
) -> Any:
    """Every stored explanation for this model version, newest first.

    The audit view: which predictions the version answered, and for each the method
    that explained it and whether that method was exact.
    """
    try:
        body = await service.list_for_model(model_id, version)
    except ExplanationUnavailable as error:
        return _refusal_response(error)
    return ModelExplanationListResponse(**body).model_dump(mode="json")


__all__ = [
    "DESCRIPTION",
    "QUANTITY_CONTRACT",
    "WHAT_THIS_MODULE_DOES_NOT_DO",
    "get_explanation_service",
    "get_explanation_status_service",
    "router",
]
