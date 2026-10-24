"""Module 2 - Salt-pan registration.

Implements the operator registry: register a pan, list registered pans, open a
single pan, edit its information and deactivate it. Deactivation is a soft
lifecycle transition, not a deletion, so observations, weather, twin states and
outcomes that reference a pan stay intact.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import JSONResponse

from app.api.deps import OptionalDbDependency, get_db
from app.db.mongo import db
from app.schemas.salt_pan import (
    PanStatus,
    SaltPanListResponse,
    SaltPanRead,
    SaltPanRegistration,
    SaltPanUpdate,
)
from app.schemas.whatsapp import (
    WhatsAppLinkRead,
    WhatsAppLinkRequest,
    WhatsAppRefusalResponse,
)
from app.services.salt_pans import SaltPanService
from app.services.whatsapp_delivery import WhatsAppRefusal, WhatsAppService

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
    "POST   /api/pans/{pan_id}/whatsapp-link",
]


def get_whatsapp_link_service(
    database: Annotated[Any, Depends(get_db)],
) -> WhatsAppService:
    """Build the WhatsApp service over the same database handle the pan routes use.

    ``get_db`` is taken as a dependency rather than called here, so the handle is
    resolved at request time and a test that overrides ``get_db`` reaches this
    service too. An unconfigured database still produces this application's 503, and
    a malformed body is still validated to a 422.
    """
    return WhatsAppService(database)


LinkServiceDependency = Annotated[WhatsAppService, Depends(get_whatsapp_link_service)]


def _whatsapp_refusal(exc: WhatsAppRefusal) -> JSONResponse:
    """A contact refusal as a 409 carrying its code.

    409 for the same reason the send endpoint uses it: the request was well formed
    and the pan exists, and what cannot be produced is a link from the contact on
    file. A missing number and an unusable one are states of the data, not a
    malformed request or a missing record, and a client needs to tell them apart.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=WhatsAppRefusalResponse(**exc.as_body()).model_dump(mode="json"),
    )


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


@router.post(
    "/{pan_id}/whatsapp-link",
    response_model=WhatsAppLinkRead,
    summary="A WhatsApp link for the farmer of one pan",
)
async def farmer_whatsapp_link(
    pan_id: PanIdPath,
    payload: WhatsAppLinkRequest,
    service: LinkServiceDependency,
) -> WhatsAppLinkRead:
    """Turn operator-composed text into a ``wa.me`` link addressed to this pan's farmer.

    **Nothing is sent and nothing is recorded.** The call reads the number already on
    file for the pan, percent-encodes the supplied message into WhatsApp's own
    click-to-chat URL and returns it; the operator's browser follows the link and the
    operator presses send in WhatsApp. This route writes no document, so it cannot be
    mistaken for a record that a message was sent — the audited send lives at
    ``POST /api/decisions/{recommendation_id}/whatsapp-send``, which derives its own
    text from the stored recommendation.

    **The recipient is not the caller's to choose freely.** It is the farmer recorded
    on the pan named on the path; there is no phone field in the body, so this is not
    a way to message any number, only a pan that is already on the registry. The
    unmasked number is never returned as a value — it appears only inside the link,
    which is exactly what the send response already does.

    Returns 404 for an unknown pan and 409 with a code for a pan with no contact
    recorded or a stored number that is no longer diallable.
    """
    try:
        link = await service.build_farmer_link(pan_id, payload.message)
    except WhatsAppRefusal as exc:
        return _whatsapp_refusal(exc)  # type: ignore[return-value]
    return WhatsAppLinkRead(
        pan_id=pan_id,
        recipient_contact_reference=link.recipient_contact_reference,
        click_to_chat_url=link.click_to_chat_url,
        message_character_count=link.message_character_count,
    )
