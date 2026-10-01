"""Module 13 - Operator dashboard."""

from fastapi import APIRouter

from app.api.routers._common import placeholder_response

router = APIRouter(prefix="/api/dashboard", tags=["13-dashboard"])

DESCRIPTION = "Aggregated views of pans, alerts and signals for the operator UI."


@router.get("/")
def module_status():
    return placeholder_response(
        "operator-dashboard",
        13,
        DESCRIPTION,
        [
            "GET /api/dashboard/overview",
            "GET /api/dashboard/alerts",
            "GET /api/dashboard/pan-summary/{pan_id}",
        ],
    )