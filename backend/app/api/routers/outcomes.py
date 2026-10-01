"""Module 15 - what actually happened, recorded against what was expected.

The write path accepts the operator's figures and nothing else. There is no
field on this router for a predicted salinity, an error or a mean absolute
error, because the accuracy claims this module exists to support are only worth
anything if nobody but the mass balance can produce them. The prediction is
computed server-side from the recorded pre-event state, and the run behind it is
stored and linked, so any error figure here can be traced back to the arithmetic
that caused it.

The read path is deliberately more forthcoming than the write path. Alongside
each error it returns the figures the event is still waiting on and a sentence
explaining each, because "why is this null" is the question an operator actually
has, and a null with no explanation is indistinguishable from a bug.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import OptionalDbDependency, get_db
from app.db.mongo import db
from app.schemas.outcome import (
    EventOutcomeCreate,
    EventOutcomeListResponse,
    EventOutcomeRead,
    OutcomeComparisonSummary,
)
from app.services.outcomes import OutcomeService

router = APIRouter(prefix="/api/outcomes", tags=["15-outcomes"])

DESCRIPTION = (
    "Ground-truth recording of what actually happened (actual rainfall, measured "
    "brine salinity, operator action, harvest result) and the predicted-against-"
    "actual error it implies."
)

ENDPOINTS = [
    "GET  /api/outcomes/status",
    "POST /api/outcomes/",
    "GET  /api/outcomes/?pan_id=...",
    "GET  /api/outcomes/comparison?pan_id=...",
    "GET  /api/outcomes/{outcome_id}",
]

PanIdQuery = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Restrict to one pan. Matched case-insensitively.",
        examples=["pans-2026-a01"],
    ),
]

OutcomeIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Server-assigned outcome identifier.",
        examples=["outcome-7c6b5a4938271605f4e3d2c1b0a9f8e"],
    ),
]


def get_outcome_service() -> OutcomeService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider rather than called here so the 503 for an
    unconfigured database is raised inside the route handler, after FastAPI has
    validated the request body.
    """
    return OutcomeService(get_db)


ServiceDependency = Annotated[OutcomeService, Depends(get_outcome_service)]


def get_outcome_status_service(
    database: OptionalDbDependency,
) -> OutcomeService | None:
    """Build a service for status reporting, or None when there is no database."""
    return OutcomeService(database) if database is not None else None


OptionalStatusDependency = Annotated[
    OutcomeService | None, Depends(get_outcome_status_service)
]


@router.get("/status", summary="Module 15 implementation status")
async def module_status(service: OptionalStatusDependency) -> dict[str, Any]:
    """Report that Module 15 is implemented, with live outcome totals.

    This endpoint never fails. An unavailable database is reported as
    ``outcomes.available = false`` rather than raised, because reporting status
    is the one thing it exists to do.

    ``comparable_outcomes`` is the count of events that hold a real error. The
    gap between it and ``total_outcomes`` is the backlog: events an operator has
    recorded but not yet been back to measure.
    """
    payload: dict[str, Any] = {
        "module": "actual-outcomes",
        "module_number": 15,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
    }
    if service is None:
        payload["outcomes"] = {
            "available": False,
            "detail": db.unavailable_reason(),
        }
    else:
        payload["outcomes"] = {
            "available": True,
            **await service.registry_counts(),
        }
    return payload


@router.post(
    "/",
    response_model=EventOutcomeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record what actually happened in one event",
)
async def create_outcome(
    payload: EventOutcomeCreate,
    service: ServiceDependency,
) -> EventOutcomeRead:
    """Store one event and grade it against the mass-balance prediction.

    Every measurement is optional on its own, and every one that is left blank
    stays ``null`` through to the response. Nothing is filled from the forecast,
    the previous reading or a neighbouring event. At least one measured figure, a
    harvest result or an operator action is required, because a record that says
    nothing about what happened cannot be compared with anything.

    The prediction is computed here, never supplied by the caller: once from the
    rainfall that actually fell, which grades the simulator, and once from the
    recorded forecast rainfall, which grades the forecast with it. The records the
    event is tied to are resolved for its own timestamp, and the mass-balance run
    behind the prediction is stored and linked.

    Returns 404 if the pan is not registered, and 422 if a supplied reference does
    not exist or belongs to another pan.
    """
    return EventOutcomeRead.from_outcome(await service.create(payload))


@router.get(
    "/",
    response_model=EventOutcomeListResponse,
    summary="List recorded events, newest first",
)
async def list_outcomes(
    service: ServiceDependency,
    pan_id: PanIdQuery = None,
    event_id: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=64,
            description="Only events the operator filed under this name, e.g. one storm.",
            examples=["storm-2026-03-14"],
        ),
    ] = None,
    start: Annotated[
        datetime | None,
        Query(description="Only events at or after this instant (ISO 8601)."),
    ] = None,
    end: Annotated[
        datetime | None,
        Query(description="Only events at or before this instant (ISO 8601)."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000, description="Max events returned.")] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EventOutcomeListResponse:
    """Return stored events, optionally narrowed to a pan, a name or a window.

    Only recorded values are returned. An event whose measurement is missing
    reports null, and is reported as not comparable rather than as an error of
    zero.
    """
    return await service.list_outcomes(
        pan_id=pan_id,
        event_id=event_id,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/comparison",
    response_model=OutcomeComparisonSummary,
    summary="Predicted against actual, aggregated over the recorded events",
)
async def comparison_summary(
    service: ServiceDependency,
    pan_id: PanIdQuery = None,
    limit: Annotated[
        int,
        Query(ge=1, le=1000, description="Max events considered by the statistics."),
    ] = 1000,
) -> OutcomeComparisonSummary:
    """Summarise the model's accuracy over every event in scope.

    Three error families are reported side by side: salinity predicted from the
    rainfall that actually fell, salinity predicted from the forecast rainfall,
    and the forecast rainfall against the rain that fell. Because both salinity
    predictions share a pre-event state, the gap between the first two is the
    error the forecast introduced.

    Only comparable events contribute to a mean. Each statistic reports
    ``compared`` and ``not_comparable`` together, so a mean over two of twenty
    events cannot be read as though the model were right on all twenty. When no
    event in scope is comparable the means are null with the reason attached,
    never zero.
    """
    return await service.comparison(pan_id=pan_id, limit=limit)


@router.get(
    "/{outcome_id}",
    response_model=EventOutcomeRead,
    summary="Open a single recorded event",
)
async def get_outcome(
    outcome_id: OutcomeIdPath,
    service: ServiceDependency,
) -> EventOutcomeRead:
    """Return one event exactly as it was recorded and graded.

    Declared after ``/comparison`` so the literal path is not captured as an
    outcome identifier.
    """
    return EventOutcomeRead.from_outcome(await service.get(outcome_id))
