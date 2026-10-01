"""Module 19 - the append-only ledger of completed research experiments.

Every finished run is recorded with everything that produced it - the dataset by
Module 07 identity, the observations and pans, the date range, the features, the
target definition, the split, the seed, the model, its hyperparameters, the
metrics and the software versions - and that record is what another researcher
re-derives the result from.

Three absences are deliberate and load-bearing.

**There is no update and no delete.** An experiment is a claim to have measured
something; editing or removing it would quietly rewrite the claim that was made.
Correcting a run means recording a new experiment, and the old one stays, so a
comparison of results five experiments later still has every earlier result to
compare against.

**Results are never overwritten.** The content hash - SHA-256 over a canonical
encoding of everything the researcher submitted - is unique, so re-posting an
identical experiment returns the stored record with `created=false` rather than
writing a second one that looks like an independent reproduction. Two runs that
differ in any recorded respect hash differently and are two experiments.

**The manifest is read off the record, never stored separately.** A second
document would drift from the ledger the moment a reader disagreed with either
one; here there is one immutable record and the manifest is its serialisation,
hash and canonical form, so the two cannot disagree.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import OptionalDbDependency, get_db
from app.db.errors import DocumentNotFoundError
from app.db.mongo import db
from app.schemas.experiments import (
    ExperimentCreate,
    ExperimentCreateResponse,
    ExperimentListResponse,
    ExperimentManifest,
    ExperimentRead,
)
from app.services.experiments import ExperimentService

router = APIRouter(prefix="/api/experiments", tags=["19-research-experiments"])

DESCRIPTION = (
    "Append-only ledger of completed research runs: dataset identity, features, "
    "target definition, split, seed, model, hyperparameters, metrics and the "
    "software versions that produced them, each with a reproducibility manifest. "
    "Results are never overwritten: an identical submission returns the stored "
    "experiment."
)

ENDPOINTS = [
    "GET  /api/experiments/status",
    "POST /api/experiments/",
    "GET  /api/experiments/",
    "GET  /api/experiments/export?output_format=csv|json&model=...",
    "GET  /api/experiments/{experiment_id}",
    "GET  /api/experiments/{experiment_id}/manifest",
    "GET  /api/experiments/{experiment_id}/export?output_format=csv|json",
]


def get_experiment_service() -> ExperimentService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider rather than called here so an unconfigured
    database raises its 503 inside the handler, after FastAPI has validated the
    request body. A malformed experiment is a 422 whether or not Mongo is
    reachable.
    """
    return ExperimentService(get_db)


ServiceDependency = Annotated[ExperimentService, Depends(get_experiment_service)]


def get_experiment_status_service(
    database: OptionalDbDependency,
) -> ExperimentService | None:
    """Build a service for status reporting, or None when there is no database."""
    return ExperimentService(database) if database is not None else None


OptionalStatusDependency = Annotated[
    ExperimentService | None, Depends(get_experiment_status_service)
]

ModelQuery = Annotated[
    str | None,
    Query(min_length=1, max_length=120, description="Narrow the ledger to one model."),
]
TargetQuery = Annotated[
    str | None,
    Query(min_length=1, max_length=120, description="Narrow the ledger to one target."),
]
DatasetHashQuery = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=72,
        description="Narrow the ledger to one dataset content hash.",
    ),
]
LimitQuery = Annotated[
    int,
    Query(ge=1, le=1000, description="Max experiments returned."),
]
OffsetQuery = Annotated[
    int,
    Query(ge=0, description="How many experiments to skip before the page."),
]
ExportFormatQuery = Annotated[
    Literal["csv", "json"],
    Query(description="The downloadable file format."),
]


@router.get("/status", summary="Module 19 implementation status")
async def module_status(service: OptionalStatusDependency) -> dict[str, Any]:
    """Report that Module 19 is implemented, with live ledger totals.

    This endpoint never fails. An unavailable database is reported as
    ``experiments.available = false`` rather than raised, because reporting
    status is the one thing it exists to do.
    """
    payload: dict[str, Any] = {
        "module": "research-experiments",
        "module_number": 19,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
    }
    if service is None:
        payload["experiments"] = {
            "available": False,
            "detail": db.unavailable_reason(),
        }
    else:
        payload["experiments"] = {
            "available": True,
            **await service.status_counts(),
        }
    return payload


@router.post(
    "/",
    response_model=ExperimentCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record one completed experiment",
)
async def create_experiment(
    payload: ExperimentCreate,
    response: Response,
    service: ServiceDependency,
) -> ExperimentCreateResponse:
    """Store one finished run, at the next row of the ledger.

    Returns 201 when a new experiment was recorded and 200 when the identical
    record already existed. The second case is not a lesser success: posting the
    same experiment twice is one experiment, and a second row would be an
    artefact of the retry rather than a reproduction.

    The identity is the content hash, computed here and never accepted from the
    request, so a client cannot file a run under an identity that does not
    describe it. Nothing here overwrites anything.
    """
    outcome = await service.create(payload)
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return ExperimentCreateResponse(
        created=outcome.created,
        reason=outcome.reason,
        experiment=ExperimentRead.from_experiment(outcome.experiment),
    )


@router.get(
    "/",
    response_model=ExperimentListResponse,
    summary="List the experiment ledger, newest first",
)
async def list_experiments(
    service: ServiceDependency,
    model: ModelQuery = None,
    target: TargetQuery = None,
    dataset_hash: DatasetHashQuery = None,
    limit: LimitQuery = 50,
    offset: OffsetQuery = 0,
) -> ExperimentListResponse:
    """Every recorded experiment, newest first, optionally narrowed.

    Filters are the facts a reader scans for - model, target, dataset - and the
    response totals are drawn over the filtered set, so a count is meaningful
    next to the page that produced it.
    """
    return await service.list_experiments(
        model=model,
        target=target,
        dataset_hash=dataset_hash,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/export",
    summary="Download the experiment ledger as CSV or JSON",
)
async def export_ledger(
    response: Response,
    service: ServiceDependency,
    output_format: ExportFormatQuery,
    model: ModelQuery = None,
    target: TargetQuery = None,
    dataset_hash: DatasetHashQuery = None,
    limit: LimitQuery = 1000,
) -> StreamingResponse:
    """Stream the whole ledger for a researcher's own analysis.

    Every row is one experiment, so a stored result can be re-derived from the
    file rather than taken from a screen. Declared before ``/{experiment_id}`` so
    the literal path is never captured as an experiment identifier.
    """
    media_type, body, filename = await service.export_ledger(
        model=model,
        target=target,
        dataset_hash=dataset_hash,
        limit=limit,
        output_format=output_format,
    )
    return StreamingResponse(
        iter([body]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{experiment_id}",
    response_model=ExperimentRead,
    summary="Open one recorded experiment",
)
async def get_experiment(
    experiment_id: str,
    service: ServiceDependency,
) -> ExperimentRead:
    """One experiment, exactly as it will be reproduced."""
    try:
        experiment = await service.get(experiment_id)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No experiment matched {experiment_id!r}.",
        ) from None
    return ExperimentRead.from_experiment(experiment)


@router.get(
    "/{experiment_id}/manifest",
    response_model=ExperimentManifest,
    summary="The reproducibility manifest for one experiment",
)
async def get_manifest(
    experiment_id: str,
    service: ServiceDependency,
) -> ExperimentManifest:
    """The self-contained JSON document for one experiment.

    Carries the schema version, the stored content hash, the canonical encoding
    it was computed over, and the entire record, so another researcher can
    recompute the hash from the bytes and verify the ledger without trusting a
    field.
    """
    try:
        experiment = await service.get(experiment_id)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No experiment matched {experiment_id!r}.",
        ) from None
    return ExperimentManifest.from_experiment(experiment)


@router.get(
    "/{experiment_id}/export",
    summary="Download one experiment's results as CSV or JSON",
)
async def export_experiment(
    experiment_id: str,
    response: Response,
    service: ServiceDependency,
    output_format: ExportFormatQuery,
) -> StreamingResponse:
    """Download one experiment's results.

    JSON is the reproducibility manifest, so the file is exactly what another
    researcher would need to re-derive the result. CSV is the metrics table, one
    metric per row, for pasting into a table without re-keying it. Declared
    after ``/export`` so the ledger export's literal path is not captured.
    """
    try:
        experiment = await service.get(experiment_id)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No experiment matched {experiment_id!r}.",
        ) from None
    media_type, body, filename = await service.export_one(experiment, output_format)
    return StreamingResponse(
        iter([body]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["DESCRIPTION", "ENDPOINTS", "router"]