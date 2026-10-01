"""Module 2 - Salt-pan registration.

Implements the operator registry: register a pan, list registered pans, open a
single pan, edit its information and deactivate it. Deactivation is a soft
lifecycle transition, not a deletion, so observations, weather, twin states and
outcomes that reference a pan stay intact.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import OptionalDbDependency, get_db
from app.db.mongo import db
from app.schemas.salt_pan import (
    PanStatus,
    SaltPanListResponse,
    SaltPanRead,
    SaltPanRegistration,
    SaltPanUpdate,
)
from app.services.salt_pans import SaltPanService

router = APIRouter(prefix="/api/pans", tags=["02-salt-pans"])

DESCRIPTION = "Register, inspect and geolocate salt-pan production units."

PanIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        description="Operator pan identifier. Matched case-insensitively.",
        examples=["pans-2026-a01"],
    ),
]


def get_salt_pan_service() -> SaltPanService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider rather than called here so the
    503 for an unconfigured database is raised inside the route handler,
    after FastAPI has validated the request body.
    """
    return SaltPanService(get_db)


ServiceDependency = Annotated[SaltPanService, Depends(get_salt_pan_service)]

ENDPOINTS = [
    "GET    /api/pans/status",
    "POST   /api/pans/",
    "GET    /api/pans/",
    "GET    /api/pans/{pan_id}",
    "PATCH  /api/pans/{pan_id}",
    "POST   /api/pans/{pan_id}/deactivate",
]


def get_pan_status_service(
    database: OptionalDbDependency,
) -> SaltPanService | None:
    """Build a service for status reporting, or None when there is no database."""
    return SaltPanService(database) if database is not None else None


OptionalStatusDependency = Annotated[
    SaltPanService | None, Depends(get_pan_status_service)
]


@router.get("/status", summary="Module 2 implementation status")
async def module_status(service: OptionalStatusDependency) -> dict[str, Any]:
    """Report that Module 2 is implemented, with live registry counts.

    This endpoint never fails. An unavailable database is reported as
    ``registry.available = false`` rather than raised, because reporting
    status is the one thing it exists to do.
    """
    payload: dict[str, Any] = {
        "module": "salt-pans",
        "module_number": 2,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
    }
    if service is None:
        payload["registry"] = {
            "available": False,
            "detail": db.unavailable_reason(),
        }
    else:
        payload["registry"] = {"available": True, **await service.registry_counts()}
    return payload


@router.post(
    "/",
    response_model=SaltPanRead,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new salt pan",
)
async def register_pan(
    payload: SaltPanRegistration,
    service: ServiceDependency,
) -> SaltPanRead:
    """Register a pan from operator-entered values.

    Returns 409 if the identifier is already registered and 422 if any field
    fails validation (area, depth and target salinity must all be greater than
    zero; latitude and longitude must be in range and supplied together).
    """
    pan = await service.register(payload)
    return SaltPanRead.from_pan(pan)


@router.get(
    "/",
    response_model=SaltPanListResponse,
    summary="List registered salt pans",
)
async def list_pans(
    service: ServiceDependency,
    pan_status: Annotated[
        PanStatus,
        Query(description="Lifecycle filter applied to the registry."),
    ] = PanStatus.ACTIVE,
    search: Annotated[
        str | None,
        Query(max_length=128, description="Case-insensitive substring of ID, name or location."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SaltPanListResponse:
    """Return registered pans, newest lifecycle state first."""
    return await service.list_pans(
        pan_status=pan_status,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{pan_id}",
    response_model=SaltPanRead,
    summary="Open a single salt pan",
)
async def get_pan(pan_id: PanIdPath, service: ServiceDependency) -> SaltPanRead:
    """Return one pan, including deactivated pans."""
    pan = await service.get_pan(pan_id)
    return SaltPanRead.from_pan(pan)


@router.patch(
    "/{pan_id}",
    response_model=SaltPanRead,
    summary="Edit salt-pan information",
)
async def update_pan(
    pan_id: PanIdPath,
    payload: SaltPanUpdate,
    service: ServiceDependency,
) -> SaltPanRead:
    """Apply a partial update.

    The pan identifier is immutable because observations, weather, twin states
    and outcomes all reference it. Coordinates must be supplied as a pair.
    """
    pan = await service.update_pan(pan_id, payload)
    return SaltPanRead.from_pan(pan)


@router.post(
    "/{pan_id}/deactivate",
    response_model=SaltPanRead,
    summary="Deactivate a salt pan",
)
async def deactivate_pan(
    pan_id: PanIdPath,
    service: ServiceDependency,
) -> SaltPanRead:
    """Retire a pan from active operation without discarding its record.

    Returns 409 if the pan is already deactivated.
    """
    pan = await service.deactivate_pan(pan_id)
    return SaltPanRead.from_pan(pan)
