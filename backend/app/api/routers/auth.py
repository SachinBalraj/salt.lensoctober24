"""Module 1 - Authentication."""

from fastapi import APIRouter

from app.api.routers._common import placeholder_response

router = APIRouter(prefix="/api/auth", tags=["01-authentication"])

DESCRIPTION = "User registration, login, access-token issuance and refresh."


@router.get("/")
def module_status():
    return placeholder_response(
        "authentication",
        1,
        DESCRIPTION,
        [
            "POST /api/auth/register",
            "POST /api/auth/login",
            "POST /api/auth/refresh",
            "GET  /api/auth/me",
        ],
    )