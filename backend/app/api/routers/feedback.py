"""Module 14 - Operator feedback.

The stored record of what an operator did with a recommendation: accepted,
modified or rejected, the action they actually took, their note and the moment
they decided. One decision per recommendation, filed against the stored advice
so the record always says which recommendation it answers.

The write path refuses a decision it cannot record honestly, with a 409 and a
code rather than prose alone: ``recommendation_unknown`` when the advice does not
exist, ``recommendation_not_a_decision`` when it is a note that named no action,
and ``decision_already_recorded`` when the recommendation already has one
decision.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse

from app.api.deps import get_db
from app.models.base import normalize_pan_id
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackListResponse,
    FeedbackRead,
    FeedbackRefusalResponse,
)
from app.services.feedback import FeedbackRefusal, FeedbackService

router = APIRouter(prefix="/api/feedback", tags=["14-feedback"])

DESCRIPTION = (
    "The operator's recorded disposition of an issued recommendation — accepted, "
    "modified or rejected — with the action actually taken, a note and the moment "
    "of the decision. Filed one per recommendation and linked to the stored advice, "
    "so a decision row can be rebuilt into the whole evidence chain for research "
    "analysis."
)

ENDPOINTS = [
    "GET  /api/feedback/status",
    "POST /api/feedback/",
    "GET  /api/feedback/?pan_id=...&recommendation_id=...",
    "GET  /api/feedback/{decision_id}",
]

DecisionIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Server-assigned operator decision identifier.",
        examples=["decision-7c6b5a4938271605f4e3d2c1b0a9f8e"],
    ),
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

RecommendationIdQuery = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Restrict to one recommendation.",
        examples=["rec-1f2e3d4c5b6a79887766554433221100"],
    ),
]

DecisionQuery = Annotated[
    str | None,
    Query(
        min_length=1,
        max_length=32,
        description="Restrict to one disposition: accepted, modified or rejected.",
        examples=["accepted"],
    ),
]


def get_feedback_service() -> FeedbackService:
    """Build the service with a lazy database handle, overridable in tests."""
    return FeedbackService(get_db)


ServiceDependency = Annotated[FeedbackService, Depends(get_feedback_service)]


def _refusal_response(exc: FeedbackRefusal) -> JSONResponse:
    """A refusal as a 409 carrying its code.

    409 rather than 404 or 422: the request was well formed, and what cannot be
    recorded is a decision against the evidence on hand. A client branches on the
    code without parsing prose.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=FeedbackRefusalResponse(**exc.as_body()).model_dump(mode="json"),
    )


@router.get("/status", summary="Module 14 implementation status")
async def module_status() -> dict[str, Any]:
    """Report that Module 14 is implemented, and state the decision vocabulary.

    This endpoint touches nothing and cannot fail. A client that polls status
    learns the write path is live and that the dispositions are a closed set of
    three.
    """
    from app.models.enums import OperatorDecisionType

    return {
        "module": "operator-feedback",
        "module_number": 14,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
        "decision_vocabulary": [member.value for member in OperatorDecisionType],
        "decision_vocabulary_is_closed": True,
        "one_per_recommendation": True,
    }


@router.post(
    "/",
    response_model=FeedbackRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record the operator's decision about one recommendation",
)
async def record_feedback(
    payload: FeedbackCreate,
    service: ServiceDependency,
) -> FeedbackRead:
    """Store one operator decision against a stored recommendation.

    The pan and the recommended action are read from the stored recommendation,
    so a record cannot be filed against a different pan than the advice it
    answers. ``actual_action`` is required when the decision is modified or
    rejected; left blank with an acceptance, the record says no physical action
    followed.

    Returns a 409 with a code when the recommendation does not exist
    (``recommendation_unknown``), when it made no decision
    (``recommendation_not_a_decision``), or when it already has one
    (``decision_already_recorded``).
    """
    try:
        return await service.record(payload)
    except FeedbackRefusal as exc:
        return _refusal_response(exc)  # type: ignore[return-value]


@router.get(
    "/",
    response_model=FeedbackListResponse,
    summary="Recorded operator decisions, newest first",
)
async def list_feedback(
    service: ServiceDependency,
    pan_id: PanIdQuery = None,
    recommendation_id: RecommendationIdQuery = None,
    decision: DecisionQuery = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> FeedbackListResponse:
    """Every recorded decision, optionally narrowed by pan, recommendation or disposition.

    Ordered by the moment of decision rather than by creation time, because a
    decision's operational instant is when it was made and the two differ when a
    record is re-saved.
    """
    try:
        rows = await service.list(
            pan_id=normalize_pan_id(pan_id) if pan_id else None,
            recommendation_id=recommendation_id,
            decision=decision,
            limit=limit,
            skip=skip,
        )
    except FeedbackRefusal as exc:
        return _refusal_response(exc)  # type: ignore[return-value]
    return FeedbackListResponse(
        count=len(rows),
        items=rows,
    )


@router.get(
    "/{decision_id}",
    response_model=FeedbackRead,
    summary="One recorded operator decision",
)
async def read_feedback(
    decision_id: DecisionIdPath,
    service: ServiceDependency,
) -> FeedbackRead:
    """A single recorded decision by its server-assigned identifier.

    Returns 404 when no decision has that identifier, which is a normal state for
    advice nobody has answered yet.
    """
    record = await service.get(decision_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No recorded operator decision has the ID “{decision_id}”. A "
                "recommendation is only listed here once an operator has answered it."
            ),
        )
    return record


__all__ = ["DESCRIPTION", "ENDPOINTS", "router"]