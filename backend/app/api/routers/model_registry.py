"""Module 17 - controlled model deployment.

Training a model and putting it into service are two different events here, and
this module is what stands between them.

The researcher inspects every registered model version with its stored evaluation
report beside it, and then chooses. Choosing is a separate request that names the
version, names the person, and states why. Nothing reaches ``deployed`` as a
side effect of anything else -- a retraining run writes a new version in
``experimental`` and stops, and the version currently deployed keeps answering
operational requests until somebody deliberately replaces it.

The lifecycle is experimental -> validated -> deployed -> retired. Validation is
where the evaluation report is read and accepted; nothing may be validated or
deployed without a report on record, and the report's id is stored with the
decision. Retired is terminal.

Replacing a deployed model is acknowledged, never implicit. A deployment that
would displace the incumbent for the same research target is refused with 409
unless the request carries ``supersede: true`` and a ``supersede_reason``; with
them, the incumbent is retired with that reason recorded against it. The
invariant "at most one deployed model per target" is also enforced by a unique
partial index, so a concurrent pair of deployments cannot both believe they won.

Every status change also appends a document to ``model_deployments``, recording
the actor, the reason, the evaluation cited and whatever was displaced. The
current status on a model version says where a model ended up; that history says
whether anybody decided it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from app.api.deps import get_db
from app.models.enums import ModelStatus, ResearchTarget
from app.schemas.registry import (
    DeploymentHistoryEntry,
    DeploymentResultResponse,
    DeployRequestBody,
    ModelVersionRecord,
    RegistryListResponse,
    RegistryOverviewResponse,
    RetireRequestBody,
    ValidateRequestBody,
)
from app.services.deployment import (
    ALLOWED_TRANSITIONS,
    NOT_EVALUATED,
    SUMMARY_METRICS,
    DeploymentRefusal,
    DeploymentService,
)

router = APIRouter(prefix="/api/models", tags=["17-model-registry"])

DESCRIPTION = (
    "Registry of trained model versions with their stored evaluation reports, and "
    "an explicit deployment lifecycle. Only a model version whose status is "
    "deployed may answer operational prediction requests, and a deployed version "
    "is never replaced as a side effect of retraining."
)

ENDPOINTS: list[str] = [
    "GET  /api/models/",
    "GET  /api/models/deployed",
    "GET  /api/models/{model_id}",
    "GET  /api/models/{model_id}/history",
    "POST /api/models/{model_id}/validate",
    "POST /api/models/{model_id}/deploy",
    "POST /api/models/{model_id}/retire",
]

#: What this module will not do, stated on the overview so a client reading only
#: the status response still gets it.
_FORWARD_PATH = (
    f"{ModelStatus.EXPERIMENTAL.value} -> {ModelStatus.VALIDATED.value}"
    f" -> {ModelStatus.DEPLOYED.value}"
)

GUARANTEES: list[str] = [
    (
        f"A model version is created experimental and can only be moved forward: "
        f"{_FORWARD_PATH}. Retired is terminal."
    ),
    (
        "Validation and deployment both require a stored evaluation report, and "
        "the report's id is recorded on the deployment history entry."
    ),
    (
        "A deployment that would displace the model already deployed for the same "
        "target is refused unless the request sets supersede=true and supplies a "
        "supersede_reason."
    ),
    (
        "At most one model version per target holds deployed, enforced by a unique "
        "partial index as well as by the service."
    ),
    (
        "Every lifecycle change appends a model_deployments document recording the "
        "actor, the reason and what was displaced."
    ),
]


def _refusal_response(error: DeploymentRefusal) -> JSONResponse:
    """Render a refused lifecycle request.

    The status code travels on the refusal rather than being derived here, because
    the two conditions are different in kind: 404 for a model version that is not in
    the registry at all, and 409 for a request the service understood and declined.
    A refused deployment is a decision, and a client should be able to tell it apart
    from a typo.
    """
    return JSONResponse(status_code=error.status_code, content=error.as_body())


def get_deployment_service() -> DeploymentService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider rather than called here so an unconfigured
    database raises its 503 inside the handler, after FastAPI has validated the
    request body. A lifecycle request with no reason is a 422 whether or not
    Mongo is reachable.
    """
    return DeploymentService(get_db)


ServiceDependency = Annotated[DeploymentService, Depends(get_deployment_service)]


@router.get("/", summary="Model versions with their stored evaluations")
async def list_models(
    service: ServiceDependency,
    target: Annotated[ResearchTarget | None, Query(description="Filter by target.")] = None,
    lifecycle_status: Annotated[
        ModelStatus | None,
        Query(
            alias="status",
            description=(
                "Filter by lifecycle status. This is the model version's deployment "
                "state, not the HTTP response code."
            ),
        ),
    ] = None,
    evaluated_only: Annotated[
        bool, Query(description="Return only versions that have a stored report.")
    ] = False,
) -> RegistryListResponse:
    """The inspection view: every registered model version and its evaluation.

    A version with no report appears with ``evaluation: null`` rather than being
    filtered out by default, because "trained but never evaluated" is a state a
    researcher needs to see to know that it cannot be deployed. Pass
    ``evaluated_only=true`` when you want the deployable set.
    """
    versions, reports = await service.list_versions(
        target=target, status=lifecycle_status, evaluated_only=evaluated_only
    )
    records = [await service.build_record(model, reports.get(model.model_id)) for model in versions]
    deployed = [record for record in records if record.status is ModelStatus.DEPLOYED]
    statuses: dict[str, int] = {value: 0 for value in ModelStatus}
    for record in records:
        statuses[record.status.value] += 1
    return RegistryListResponse(
        models=records,
        total=len(records),
        deployed=deployed,
        statuses=statuses,
    )


@router.get("/deployed", summary="The models currently permitted to answer requests")
async def list_deployed(service: ServiceDependency) -> RegistryListResponse:
    """Every deployed model version, one per research target at most.

    A separate endpoint because "which model is answering operational requests
    right now" is the question this module exists to make answerable, and it should
    not require a client to page through the whole registry and filter.
    """
    versions = await service.deployed_versions()
    records = [
        await service.build_record(model, await service.evaluation_for(model))
        for model in versions
    ]
    return RegistryListResponse(
        models=records,
        total=len(records),
        deployed=records,
        # Every status present with a count, as on the full listing, so a client can
        # render the same four-column summary from either response. A bare
        # {"deployed": n} here would make the shape of this field depend on which
        # endpoint produced it.
        statuses={
            value.value: sum(
                1 for record in records if record.status is value
            )
            for value in ModelStatus
        },
    )


@router.get("/status", summary="What this module is and what it currently permits")
async def module_status(service: ServiceDependency) -> RegistryOverviewResponse:
    """The overview, including the deployed set named outright.

    ``/status`` rather than a root module endpoint so the root can be the
    registry listing, which is the thing a researcher opens this module to see.
    """
    deployed = await service.deployed_versions()
    all_versions, _ = await service.list_versions()
    counts: dict[str, int] = {value.value: 0 for value in ModelStatus}
    for model in all_versions:
        counts[model.status.value] += 1
    return RegistryOverviewResponse(
        module="model-registry",
        module_number=17,
        description=DESCRIPTION,
        endpoints=ENDPOINTS,
        statuses=[value.value for value in ModelStatus],
        deployed=[
            {
                "model_id": model.model_id,
                "version": model.version,
                "target": model.target.value,
                "algorithm": model.algorithm,
                "deployed_at": model.deployed_at,
                "deployed_by": model.deployed_by,
            }
            for model in deployed
        ],
        counts=counts,
        guarantees=GUARANTEES,
        summary_metrics=list(SUMMARY_METRICS),
        transitions={key.value: [value.value for value in values] for key, values in ALLOWED_TRANSITIONS.items()},
    )


@router.get("/{model_id}", summary="One model version in full")
async def get_model(
    model_id: str,
    service: ServiceDependency,
    version: Annotated[
        str | None,
        Query(description="Exact version. Defaults to the newest version of this model."),
    ] = None,
) -> ModelVersionRecord:
    """The stored record, with its evaluation report and its available transitions.

    The version defaults to the newest on this read path only. A read can safely
    guess which version is interesting; a deployment cannot, which is why the
    write endpoints require the version in the body.
    """
    try:
        model = await service.get_version(model_id, version)
    except DeploymentRefusal as refusal:
        return _refusal_response(refusal)
    report = await service.evaluation_for(model)
    return await service.build_record(model, report)


@router.get("/{model_id}/history", summary="The deployment history of a model version")
async def get_history(
    model_id: str,
    service: ServiceDependency,
    version: Annotated[str | None, Query(description="Exact version. Defaults to all versions.")] = None,
) -> list[DeploymentHistoryEntry]:
    """Every recorded lifecycle step, oldest first.

    Empty for a model that has never been validated, deployed or retired, which is
    the normal state of a freshly trained model and is reported as an empty history
    rather than as a model that went through the lifecycle silently.
    """
    try:
        model = await service.get_version(model_id, version)
    except DeploymentRefusal as refusal:
        return _refusal_response(refusal)
    entries = await service.history(model.model_id, model.version)
    return [DeploymentHistoryEntry.model_validate(entry) for entry in entries]


@router.post(
    "/{model_id}/validate",
    summary="Accept a model's evaluation report",
    response_model=DeploymentResultResponse,
)
async def validate_model(
    model_id: str,
    body: ValidateRequestBody,
    service: ServiceDependency,
) -> DeploymentResultResponse:
    """Move a model from experimental to validated.

    Refused without a stored evaluation report. The decision is recorded with the
    report's id, so the history says which measurement was accepted rather than
    only that someone was.
    """
    try:
        model, entry, _ = await service.validate(
            model_id, version=body.version, reason=body.reason, actor=body.actor
        )
    except DeploymentRefusal as refusal:
        return _refusal_response(refusal)
    return await _result(service, model, entry, superseded=None)


@router.post(
    "/{model_id}/deploy",
    summary="Put a validated model version into operational service",
    response_model=DeploymentResultResponse,
)
async def deploy_model(
    model_id: str,
    body: DeployRequestBody,
    service: ServiceDependency,
) -> DeploymentResultResponse:
    """Move a model from validated to deployed.

    If another version is already deployed for the same target, this is refused
    with 409 unless the request acknowledges the replacement with ``supersede``
    and a ``supersede_reason``. On an acknowledged replacement the incumbent is
    retired with that reason attached and the response returns it under
    ``superseded``, so the caller learns what it displaced in the same request that
    displaced it.
    """
    try:
        model, entry, superseded = await service.deploy(
            model_id,
            version=body.version,
            reason=body.reason,
            actor=body.actor,
            supersede=body.supersede,
            supersede_reason=body.supersede_reason,
        )
    except DeploymentRefusal as refusal:
        return _refusal_response(refusal)
    return await _result(service, model, entry, superseded=superseded)


@router.post(
    "/{model_id}/retire",
    summary="Withdraw a model version from service",
    response_model=DeploymentResultResponse,
)
async def retire_model(
    model_id: str,
    body: RetireRequestBody,
    service: ServiceDependency,
) -> DeploymentResultResponse:
    """Move a deployed model to retired, which is terminal.

    After this the target has no deployed model and operational prediction refuses
    rather than falling back to an earlier version. Falling back would be a silent
    substitution of the kind this module exists to prevent.
    """
    try:
        model, entry, _ = await service.retire(
            model_id, version=body.version, reason=body.reason, actor=body.actor
        )
    except DeploymentRefusal as refusal:
        return _refusal_response(refusal)
    return await _result(service, model, entry, superseded=None)


async def _result(
    service: DeploymentService,
    model: Any,
    entry: Any,
    *,
    superseded: Any,
) -> DeploymentResultResponse:
    """Assemble a transition response from the state the service just wrote.

    Reads the record back rather than describing the change from the values passed
    in, so the client's view of the registry is the stored view. The only value not
    read back is the superseded incumbent, which was itself re-read and is returned
    in its retired state.
    """
    report = await service.evaluation_for(model)
    return DeploymentResultResponse(
        action=entry.action,
        from_status=entry.from_status,
        to_status=entry.to_status,
        model=await service.build_record(model, report),
        history_entry=DeploymentHistoryEntry.model_validate(entry),
        superseded=None
        if superseded is None
        else await service.build_record(superseded, await service.evaluation_for(superseded)),
    )


__all__ = ["GUARANTEES", "NOT_EVALUATED", "get_deployment_service", "router"]
