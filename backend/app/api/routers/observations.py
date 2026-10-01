"""Module 3 - pan observation / manual data entry.

The operator opens a registered pan and records what the pan looks like right
now. Manual entry is complete and works end to end. Sensor ingestion is
present as a route so the architecture is visible and testable, but it reports
itself as unavailable until a real gateway is connected, because no sensor
ships with this prototype.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.deps import OptionalDbDependency, get_db
from app.config import settings
from app.db.mongo import db
from app.schemas.observation import (
    PanObservationCreate,
    PanObservationListResponse,
    PanObservationRead,
)
from app.services.observations import ObservationService

router = APIRouter(prefix="/api/observations", tags=["03-observations"])

DESCRIPTION = (
    "Operator-entered pan observations (depth, salinity, brine stage) "
    "and future IoT sensor ingestion endpoint."
)

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

ObservationIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Server-assigned observation identifier.",
        examples=["obs-2f1c9a0e5b7d4c3a9f0e1b2c3d4e5f60"],
    ),
]


def get_observation_service() -> ObservationService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider rather than called here so the 503 for
    an unconfigured database is raised inside the route handler, after FastAPI
    has validated the request body.
    """
    return ObservationService(get_db)


ServiceDependency = Annotated[ObservationService, Depends(get_observation_service)]


def get_observation_status_service(
    database: OptionalDbDependency,
) -> ObservationService | None:
    """Build a service for status reporting, or None when there is no database."""
    return ObservationService(database) if database is not None else None


OptionalStatusDependency = Annotated[
    ObservationService | None, Depends(get_observation_status_service)
]

ENDPOINTS = [
    "GET  /api/observations/status",
    "POST /api/observations/",
    "GET  /api/observations/?pan_id=...",
    "GET  /api/observations/{observation_id}",
    "POST /api/observations/ingest/sensor  (unavailable: no gateway connected)",
]


@router.get("/status", summary="Module 3 implementation status")
async def module_status(service: OptionalStatusDependency) -> dict[str, Any]:
    """Report that Module 3 is implemented, with live observation totals.

    This endpoint never fails. An unavailable database is reported as
    ``observations.available = false`` rather than raised, because reporting
    status is the one thing it exists to do.
    """
    payload: dict[str, Any] = {
        "module": "observations",
        "module_number": 3,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
        "sensor_ingest": {
            "available": settings.sensor_ingest_enabled,
            "detail": (
                "A sensor gateway is configured."
                if settings.sensor_ingest_enabled
                else "No sensor gateway is connected. Readings can only be recorded "
                "manually or as imports; data_source='sensor' is refused with 409."
            ),
        },
    }
    if service is None:
        payload["observations"] = {
            "available": False,
            "detail": db.unavailable_reason(),
        }
    else:
        payload["observations"] = {
            "available": True,
            **await service.registry_counts(),
        }
    return payload


@router.post(
    "/",
    response_model=PanObservationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record an observation for a registered pan",
)
async def create_observation(
    payload: PanObservationCreate,
    service: ServiceDependency,
) -> PanObservationRead:
    """Store one reading and advance the pan's latest observed state.

    The timestamp defaults to now and is preserved exactly as supplied, so a
    reading can be backdated. Fields the operator left blank stay null: they
    are never defaulted, averaged or carried forward. Returns 404 if the pan is
    not registered and 409 if ``data_source`` is ``sensor`` while no gateway is
    connected.
    """
    observation = await service.create(payload)
    return PanObservationRead.from_observation(observation)


@router.get(
    "/",
    response_model=PanObservationListResponse,
    summary="List pan observations, newest first",
)
async def list_observations(
    service: ServiceDependency,
    pan_id: PanIdQuery = None,
    start: Annotated[
        datetime | None,
        Query(description="Only readings at or after this instant (ISO 8601)."),
    ] = None,
    end: Annotated[
        datetime | None,
        Query(description="Only readings at or before this instant (ISO 8601)."),
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=1000, description="Max readings returned.")
    ] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PanObservationListResponse:
    """Return stored readings, optionally narrowed to a pan and a time window.

    Only recorded values are returned. No gaps are filled, no values are
    interpolated, and a reading with a null field simply reports null.
    """
    return await service.list_observations(
        pan_id=pan_id,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/ingest/sensor",
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Sensor ingestion (reserved, not active)",
)
async def ingest_sensor() -> dict[str, Any]:
    """Reserved for a future sensor gateway. Always reports itself unavailable.

    The route exists so the ingest contract has a home and so the absence of a
    connected instrument is explicit rather than silently absent. It does not
    accept readings while no gateway is configured. Declared before the
    parameterised route so the literal path always wins.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=(
            "Sensor ingestion is not implemented. No sensor gateway is connected "
            "to this deployment, so no reading can be attributed to an instrument. "
            "Record observations manually through POST /api/observations/."
        ),
    )


@router.get(
    "/{observation_id}",
    response_model=PanObservationRead,
    summary="Open a single observation",
)
async def get_observation(
    observation_id: ObservationIdPath,
    service: ServiceDependency,
) -> PanObservationRead:
    """Return one stored reading exactly as it was recorded."""
    return PanObservationRead.from_observation(await service.get(observation_id))
