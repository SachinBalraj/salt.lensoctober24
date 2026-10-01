"""Module 16 - Predicted-vs-actual validation over recorded outcomes.

The write side of this module happened in Module 15: an outcome is recorded with
its predictions and errors, and this router never accepts figures from a client,
because a validation report whose numbers a caller could post would be worth
nothing. Everything here reads.

Three read paths are offered, all over the same five comparison families:

* ``/overview`` -- the five blocks of gated metrics, each beside the count of
  events that produced it and the reasons the rest could not;
* ``/pairs`` -- every event in one comparison, compared or not, so any figure
  in the overview can be checked against the rows it was built from;
* ``/export`` -- the same rows as a downloadable CSV or JSON file for a
  researcher's own analysis.

None of these ever returns a number a metric did not support: a statistic that
was not calculated is marked ``computed=false`` with the reason, never zero.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse

from app.api.deps import OptionalDbDependency, get_db
from app.db.mongo import db
from app.schemas.validation import (
    COMPARISON_KEYS,
    ComparisonKey,
    ValidationOverview,
    ValidationPairsResponse,
)
from app.services.validation import ValidationService

router = APIRouter(prefix="/api/validation", tags=["16-validation"])

DESCRIPTION = (
    "Honest comparison of historical predictions against recorded actual "
    "outcomes; the source of real evaluation metrics."
)

ENDPOINTS = [
    "GET  /api/validation/status",
    "GET  /api/validation/overview?pan_id=...",
    "GET  /api/validation/pairs?comparison=...&pan_id=...",
    "GET  /api/validation/export?comparison=...&format=csv|json&pan_id=...",
]

PanIdQuery = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Restrict the report to one pan. Matched case-insensitively.",
        examples=["pans-2026-a01"],
    ),
]

ComparisonQuery = Annotated[
    ComparisonKey,
    Query(description="Which comparison family to report."),
]
ExportFormatQuery = Annotated[
    Literal["csv", "json"],
    Query(description="The downloadable file format."),
]
LimitQuery = Annotated[
    int,
    Query(ge=1, le=1000, description="Max recorded events considered."),
]


def get_validation_service() -> ValidationService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider so the 503 for an unconfigured database
    is raised inside the route handler rather than at request-construction time.
    """
    return ValidationService(get_db)


ServiceDependency = Annotated[ValidationService, Depends(get_validation_service)]


def get_validation_status_service(
    database: OptionalDbDependency,
) -> ValidationService | None:
    """Build a service for status reporting, or None when there is no database."""
    return ValidationService(database) if database is not None else None


OptionalStatusDependency = Annotated[
    ValidationService | None, Depends(get_validation_status_service)
]


@router.get("/status", summary="Module 16 implementation status")
async def module_status(service: OptionalStatusDependency) -> dict[str, Any]:
    """Report that Module 16 is implemented, with live record totals.

    This endpoint never fails. An unavailable database is reported as
    ``validation.available = false`` rather than raised, because reporting status
    is the one thing it exists to do.
    """
    payload: dict[str, Any] = {
        "module": "predicted-vs-actual",
        "module_number": 16,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
    }
    if service is None:
        payload["validation"] = {
            "available": False,
            "detail": db.unavailable_reason(),
        }
    else:
        payload["validation"] = {
            "available": True,
            **await service.registry_counts(),
        }
    return payload


@router.get(
    "/overview",
    response_model=ValidationOverview,
    summary="The five predicted-vs-actual blocks, with gated metrics",
)
async def validation_overview(
    service: ServiceDependency,
    pan_id: PanIdQuery = None,
    limit: LimitQuery = 1000,
) -> ValidationOverview:
    """Report recorded outcomes against every prediction the project makes.

    Each block states how many events produced its figures and how many could
    not, with the reasons. A metric the data could not support is reported as
    not computed with the reason, never as a zero.
    """
    return await service.overview(pan_id=pan_id, limit=limit)


@router.get(
    "/pairs",
    response_model=ValidationPairsResponse,
    summary="Every event in one comparison, compared or not",
)
async def validation_pairs(
    service: ServiceDependency,
    comparison: ComparisonQuery,
    pan_id: PanIdQuery = None,
    limit: LimitQuery = 1000,
) -> ValidationPairsResponse:
    """Return every event in one comparison, newest first.

    Excluded events appear with ``compared=false`` and a reason, so an event
    that could not be compared is visible rather than silently missing.
    """
    return await service.pairs(comparison=comparison, pan_id=pan_id, limit=limit)


@router.get(
    "/export",
    summary="Download one comparison's rows as CSV or JSON",
)
async def validation_export(
    response: Response,
    service: ServiceDependency,
    comparison: ComparisonQuery,
    output_format: ExportFormatQuery,
    pan_id: PanIdQuery = None,
    limit: LimitQuery = 1000,
) -> StreamingResponse:
    """Stream one comparison's pair rows for a researcher's own analysis.

    The CSV and JSON files carry the same rows as ``/pairs``, so a figure in the
    overview can be re-derived from the file rather than taken on trust.
    """
    media_type, body, filename = await service.export(
        comparison=comparison,
        output_format=output_format,
        pan_id=pan_id,
        limit=limit,
    )
    return StreamingResponse(
        iter([body]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["COMPARISON_KEYS", "DESCRIPTION", "router"]