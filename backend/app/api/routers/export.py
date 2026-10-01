"""Module 18 - Research data export."""

from fastapi import APIRouter

from app.api.routers._common import placeholder_response

router = APIRouter(prefix="/api/export", tags=["18-export"])

DESCRIPTION = (
    "Anonymized CSV/JSON exports of observations, outcomes, predictions "
    "and model runs for research replication."
)


@router.get("/")
def module_status():
    return placeholder_response(
        "research-export",
        18,
        DESCRIPTION,
        [
            "POST /api/export/",
            "GET  /api/export/{export_id}",
        ],
    )