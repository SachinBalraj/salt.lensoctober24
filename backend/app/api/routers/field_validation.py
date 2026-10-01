"""Module 20 - field validation of reported rainfall events.

The API answers the same question the service answers: what did a pan's field
report say happened, what does the rainfall-only mass balance say should have
happened, and — because neither the reported figures nor the transfer volume of
an intervention is treated as verified — what cannot be concluded from the
difference.

Export is server-generated only: the CSV and the PDF are produced on this
server, never in a browser, so a PDF can never be a client-side forgery of a
research artefact.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import Response

from app.api.deps import get_db
from app.schemas.field_validation import (
    EventCorrectionCreate,
    FieldInterventionCreate,
    FieldValidationComparison,
    FieldValidationSummary,
    PreEventObservationLink,
    RainfallEventCreate,
    RainfallTimestampConflictCreate,
    RainfallTimestampResolutionCreate,
    StatusReport,
)
from app.services.field_validation import FieldValidationService

router = APIRouter(prefix="/api/field-validation", tags=["20-field-validation"])

DESCRIPTION = (
    "User-reported rainfall events replayed against the rainfall-only mass "
    "balance, with predicted-vs-reported absolute differences, intervention "
    "flags, an append-only correction history, source-record linking and "
    "CSV/PDF export."
)

ENDPOINTS = [
    "GET   /api/field-validation/status",
    "POST  /api/field-validation/events",
    "GET   /api/field-validation/events",
    "GET   /api/field-validation/events/{event_id}",
    "POST  /api/field-validation/events/{event_id}/interventions",
    "POST  /api/field-validation/events/{event_id}/corrections",
    "POST  /api/field-validation/events/{event_id}/pre-event-link",
    "POST  /api/field-validation/events/{event_id}/rainfall-timestamp-conflict",
    "POST  /api/field-validation/events/{event_id}/rainfall-timestamp-resolution",
    "GET   /api/field-validation/comparisons",
    "GET   /api/field-validation/summary",
    "GET   /api/field-validation/export?format=csv|pdf",
]


def get_field_validation_service() -> FieldValidationService:
    """Build the service with a lazy database handle, like the sibling modules."""
    return FieldValidationService(get_db)


ServiceDependency = Annotated[
    FieldValidationService, Depends(get_field_validation_service)
]

PanIdQuery = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Restrict to one pan's events. Matched case-insensitively.",
        examples=["pan_orginal"],
    ),
]

EventIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="The event to read or append an intervention to.",
        examples=["ev-3f2c1a0e5b7d4c3a9f0e1b2c3d4e5f60"],
    ),
]


@router.get(
    "/status",
    response_model=StatusReport,
    summary="How much field data exists (never fails)",
)
async def module_status(
    service: ServiceDependency,
    pan_id: PanIdQuery = None,
) -> StatusReport:
    """Report readiness: no event on file means the dashboard says so.

    Counting is the one call this endpoint makes, and an unavailable database
    surfaces as the standard 503 rather than a partial workbook, because unlike
    the sibling modules there is nothing else to report here.
    """
    return await service.status(pan_id=pan_id)


@router.post(
    "/events",
    response_model=FieldValidationComparison,
    status_code=status.HTTP_201_CREATED,
    summary="File one reported rainfall event and replay it",
)
async def create_event(
    payload: RainfallEventCreate,
    service: ServiceDependency,
) -> FieldValidationComparison:
    """Store the event exactly as reported and return its replay.

    The pre-event snapshot must carry a salinity and a depth; the post-event
    snapshot may carry either. The event is stored as ``reported`` and nothing
    in this call verifies it. Returns 404 for an unregistered pan and 422 when
    the pre-event snapshot cannot drive the balance.
    """
    event = await service.create_event(payload)
    return await service.get_comparison(event.event_id)


@router.get(
    "/events",
    summary="List filed events, newest first",
)
async def list_events(
    service: ServiceDependency,
    pan_id: PanIdQuery = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[dict[str, Any]]:
    """Return the stored events, optionally narrowed to one pan.

    The raw documents are returned so the form of the report is visible; the
    replay for the same scope is available at ``/comparisons``.
    """
    return [
        event.model_dump(mode="json", by_alias=True)
        for event in await service.list_events(pan_id=pan_id, limit=limit)
    ]


@router.get(
    "/events/{event_id}",
    response_model=FieldValidationComparison,
    summary="The replay for one filed event",
)
async def get_event(
    event_id: EventIdPath,
    service: ServiceDependency,
) -> FieldValidationComparison:
    """Return one event's predicted-vs-reported comparison. 404 if unknown."""
    return await service.get_comparison(event_id)


@router.post(
    "/events/{event_id}/interventions",
    response_model=FieldValidationComparison,
    status_code=status.HTTP_201_CREATED,
    summary="Append one intervention to an existing event",
)
async def add_intervention(
    event_id: EventIdPath,
    payload: FieldInterventionCreate,
    service: ServiceDependency,
) -> FieldValidationComparison:
    """Record an action taken on the pan during an already-filed event.

    The transferred volume may be omitted; the event is then flagged with
    ``unknown_intervention_volumes`` rather than assumed away. The whole event is
    replaced in one write, so the append is atomic. 404 if the event is unknown.
    """
    await service.add_intervention(event_id, payload)
    return await service.get_comparison(event_id)


@router.post(
    "/events/{event_id}/corrections",
    response_model=FieldValidationComparison,
    status_code=status.HTTP_201_CREATED,
    summary="Append one reconciliation step to an event's audit history",
)
async def record_correction(
    event_id: EventIdPath,
    payload: EventCorrectionCreate,
    service: ServiceDependency,
) -> FieldValidationComparison:
    """Reconcile one field of an event, keeping the previous value readable.

    Nothing is edited in place: the response's ``corrections`` array grows by one
    entry carrying the value that was there before, the value that is there now,
    the reason, the author and the evidence. Restating a timestamp requires an
    evidence source and still leaves the instant uncorroborated — only a linked
    stored record corroborates one. This call never sets
    ``independently_verified``.
    """
    await service.record_correction(event_id, payload)
    return await service.get_comparison(event_id)


@router.post(
    "/events/{event_id}/pre-event-link",
    response_model=FieldValidationComparison,
    status_code=status.HTTP_201_CREATED,
    summary="Reconcile the pre-event snapshot against a stored observation",
)
async def link_pre_event_observation(
    event_id: EventIdPath,
    payload: PreEventObservationLink,
    service: ServiceDependency,
) -> FieldValidationComparison:
    """Attach a genuine stored observation as the event's pre-event reading.

    The observation must already exist, belong to the same pan, and predate this
    event's rainfall; the operator must also supply the original record's local
    time, zone, observer and evidence source, which must resolve to the same
    instant the stored observation carries. A later-dated observation — the 30
    September reading of a March pan, say — is refused with 409 and nothing is
    written. Disagreeing figures are refused too rather than averaged.
    """
    await service.link_pre_event_observation(event_id, payload)
    return await service.get_comparison(event_id)


@router.post(
    "/events/{event_id}/rainfall-timestamp-conflict",
    response_model=FieldValidationComparison,
    status_code=status.HTTP_201_CREATED,
    summary="Preserve a second, disagreeing account of when the rain fell",
)
async def record_rainfall_timestamp_conflict(
    event_id: EventIdPath,
    payload: RainfallTimestampConflictCreate,
    service: ServiceDependency,
) -> FieldValidationComparison:
    """Put both accounts of the rainfall instant on the record, changing neither.

    This is not a correction. The event keeps the ``rainfall_timestamp`` it was
    filed with, because that instant is what the stored replay was run against;
    the reported instant, its source and the size of the disagreement are
    appended alongside it. While any such conflict is unresolved the event
    reports ``reconciliation_state = "blocked_by_evidence"`` and cannot be
    presented as reconciled. Reporting an instant that matches the stored one is
    rejected with 422, as there is no disagreement to keep.
    """
    await service.record_rainfall_timestamp_conflict(event_id, payload)
    return await service.get_comparison(event_id)


@router.post(
    "/events/{event_id}/rainfall-timestamp-resolution",
    response_model=FieldValidationComparison,
    status_code=status.HTTP_201_CREATED,
    summary="Replace the rainfall instant with an evidenced one, keeping the old one",
)
async def resolve_rainfall_timestamp(
    event_id: EventIdPath,
    payload: RainfallTimestampResolutionCreate,
    service: ServiceDependency,
) -> FieldValidationComparison:
    """Move the rainfall instant onto evidence, superseding the value it replaces.

    Where the generic corrections endpoint refuses ``rainfall_timestamp``, this
    one moves it -- and only when the caller accounts for what they are displacing.
    The old instant is never deleted: it is retained as an explicitly superseded
    value whose meaning is ``unknown``, alongside the origin that produced it, so
    an auditor can see both that the event once said something else and why it no
    longer does. It is reported as history, not as a second measurement.

    The new instant arrives with the document that dates it and is capped at
    ``operator_reported``: a notebook entry records a time, it does not measure
    one, so this endpoint cannot make an instant instrument-verified and
    ``independently_verified`` stays False.

    A conflict raised about the adopted instant is resolved in favour of it, which
    is what lets the event leave ``blocked_by_evidence``. A conflict about a
    different instant is left open, and re-posting the same resolution is a no-op
    that adds no second correction entry.
    """
    await service.resolve_rainfall_timestamp(event_id, payload)
    return await service.get_comparison(event_id)


@router.get(
    "/comparisons",
    response_model=list[FieldValidationComparison],
    summary="Replays for all (or one pan's) events, newest first",
)
async def list_comparisons(
    service: ServiceDependency,
    pan_id: PanIdQuery = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> list[FieldValidationComparison]:
    """Return each event's predicted-vs-reported comparison."""
    return await service.list_comparisons(pan_id=pan_id, limit=limit)


@router.get(
    "/summary",
    response_model=FieldValidationSummary,
    summary="Mean absolute predicted-vs-reported differences, per unit",
)
async def summary(
    service: ServiceDependency,
    pan_id: PanIdQuery = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
) -> FieldValidationSummary:
    """The roll-up the dashboard shows: counts and mean absolute differences.

    Averages are ``None`` when no event has a reported figure in a unit, never
    zero.
    """
    return await service.summary(pan_id=pan_id, limit=limit)


@router.get(
    "/export",
    summary="Download the field-validation report (CSV or PDF, server-generated)",
)
async def export(
    service: ServiceDependency,
    format: Annotated[
        str, Query(description="'csv' or 'pdf'", examples=["csv"])
    ],
    pan_id: PanIdQuery = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 1000,
) -> Response:
    """Return a downloadable report of every event's replay.

    The CSV and the PDF are both produced here on this server. 422 if ``format``
    is anything else.
    """
    if format not in ("csv", "pdf"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="format must be 'csv' or 'pdf'.",
        )
    media_type, body, filename = await service.export(
        output_format=format, pan_id=pan_id, limit=limit
    )
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )