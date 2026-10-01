"""Module 5 - pan-specific digital twin.

The twin is assembled from records that already exist, so this router is mostly
plumbing. Its one substantive responsibility is refusing to present a gap as a
value: ``GET /api/twin/{pan_id}/state`` always returns a body, and that body
lists what it could not fill in.

Reading state is side-effect free. Storing a snapshot is a separate, explicit
``POST /refresh``, because a page view should not manufacture history, and a
change log built on that history should only contain real changes.
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.deps import OptionalDbDependency, get_db
from app.db.mongo import db
from app.schemas.digital_twin import (
    DigitalTwinHistoryResponse,
    DigitalTwinRefreshResponse,
    DigitalTwinStateRead,
)
from app.services.digital_twin import DigitalTwinService

router = APIRouter(prefix="/api/twin", tags=["05-digital-twin"])

DESCRIPTION = (
    "Per-pan digital-twin state: current physical state joined from the newest "
    "observation, the newest relevant weather record and the pan's static "
    "registration. Values are copied, never inferred."
)

ENDPOINTS = [
    "GET  /api/twin/status",
    "GET  /api/twin/{pan_id}/state",
    "POST /api/twin/{pan_id}/refresh",
    "GET  /api/twin/{pan_id}/history",
]

PanIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="A registered pan. Matched case-insensitively.",
        examples=["pans-2026-a01"],
    ),
]


def get_digital_twin_service() -> DigitalTwinService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider rather than called here so the 503 for an
    unconfigured database is raised inside the handler.
    """
    return DigitalTwinService(get_db)


ServiceDependency = Annotated[DigitalTwinService, Depends(get_digital_twin_service)]


def get_digital_twin_status_service(
    database: OptionalDbDependency,
) -> DigitalTwinService | None:
    """Build a service for status reporting, or None when there is no database."""
    return DigitalTwinService(database) if database is not None else None


OptionalStatusDependency = Annotated[
    DigitalTwinService | None, Depends(get_digital_twin_status_service)
]


@router.get("/status", summary="Module 5 implementation status")
async def module_status(service: OptionalStatusDependency) -> dict[str, Any]:
    """Report that Module 5 is implemented, with live twin snapshot counts.

    This endpoint never fails. An unavailable database is reported as
    ``twin.available = false`` rather than raised.
    """
    payload: dict[str, Any] = {
        "module": "digital-twin",
        "module_number": 5,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
        "assembly": {
            "available": True,
            "detail": (
                "A twin joins the newest pan observation, the newest observed "
                "weather record, the newest weather forecast and the pan's static "
                "properties. Every value is copied from a stored record; nothing "
                "is inferred, averaged or carried forward. Quantities with no "
                "source are returned as null and listed in missing_inputs."
            ),
        },
    }
    if service is None:
        payload["twin"] = {"available": False, "detail": db.unavailable_reason()}
    else:
        payload["twin"] = {"available": True, **await service.registry_counts()}
    return payload


@router.get(
    "/{pan_id}/state",
    response_model=DigitalTwinStateRead,
    summary="The pan's current known state",
)
async def get_twin_state(
    pan_id: PanIdPath,
    service: ServiceDependency,
) -> DigitalTwinStateRead:
    """Assemble the twin from the newest records, without storing anything.

    The response always succeeds for a registered pan, even when nothing has been
    measured yet. In that case every quantity is null, ``is_complete`` is false
    and ``missing_inputs`` names the records that would fill the gaps.

    Each contributing record is reported with its own ``timestamp``, ``source``
    and ``age_seconds``, so a caller can tell a fresh twin from one assembled
    this second out of evidence days old.
    """
    assembly = await service.get_state(pan_id)
    return DigitalTwinStateRead.from_state(
        assembly.state, pan_name=assembly.pan.pan_name
    )


@router.post(
    "/{pan_id}/refresh",
    response_model=DigitalTwinRefreshResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Reassemble the twin and store a snapshot",
)
async def refresh_twin_state(
    pan_id: PanIdPath,
    service: ServiceDependency,
) -> DigitalTwinRefreshResponse:
    """Recompute the twin and write it to the snapshot history.

    ``changed`` reports whether any tracked quantity or contributing record
    differs from the previous snapshot, so a consumer building a change log can
    skip the refreshes that found nothing new. ``previous_twin_id`` is null on
    the first snapshot for a pan.
    """
    assembly, previous, changed = await service.refresh(pan_id)
    return DigitalTwinRefreshResponse(
        state=DigitalTwinStateRead.from_state(
            assembly.state, pan_name=assembly.pan.pan_name
        ),
        stored=True,
        changed=changed,
        previous_twin_id=previous.twin_id if previous is not None else None,
    )


@router.get(
    "/{pan_id}/history",
    response_model=DigitalTwinHistoryResponse,
    summary="Stored twin snapshots, newest first",
)
async def list_twin_history(
    pan_id: PanIdPath,
    service: ServiceDependency,
    start: Annotated[
        datetime | None,
        Query(description="Only snapshots assembled at or after this instant."),
    ] = None,
    end: Annotated[
        datetime | None,
        Query(description="Only snapshots assembled at or before this instant."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000, description="Max snapshots returned.")] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DigitalTwinHistoryResponse:
    """Return the stored snapshots for a pan, newest first.

    Snapshots are written by ``POST /{pan_id}/refresh``, so an empty history means
    the twin has been read but never refreshed. The response still reports the
    true ``total`` so a caller can tell a truncated page from a full history.
    """
    if start is not None and end is not None and start > end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start must not be later than end.",
        )

    pan, snapshots, total = await service.list_history(pan_id, limit=limit, offset=offset)
    items = [
        DigitalTwinStateRead.from_state(snapshot, pan_name=pan.pan_name)
        for snapshot in snapshots
    ]
    timestamps = [item.timestamp for item in items]
    return DigitalTwinHistoryResponse(
        pan_id=pan.pan_id,
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        earliest_timestamp=min(timestamps) if timestamps else None,
        latest_timestamp=max(timestamps) if timestamps else None,
    )
