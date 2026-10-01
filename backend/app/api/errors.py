from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.db.errors import PersistenceError

logger = logging.getLogger("saltlens.api")


def _validation_detail(exc: ValidationError) -> list[dict]:
    return [
        {
            "loc": ["body", *[str(part) for part in error["loc"]]],
            "msg": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]


async def validation_error_handler(_: Request, exc: ValidationError) -> JSONResponse:
    """Report domain-level validation failures as 422, matching request bodies.

    The persistence models re-validate invariants that span several fields (for
    example coordinates that must be supplied as a pair). Those checks run
    inside the service, so they are translated into the same 422 contract the
    request-body validators already produce.
    """
    return JSONResponse(status_code=422, content={"detail": _validation_detail(exc)})


async def persistence_error_handler(_: Request, exc: PersistenceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc), "error_code": exc.code},
    )


async def pymongo_error_handler(_: Request, exc: PyMongoError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "MongoDB is temporarily unavailable.",
            "error_code": "database_unavailable",
        },
    )


async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """The catch-all 500: log the real traceback, answer with nothing internal.

    The detail string is deliberately generic so a response can never leak a
    stack trace, a file path or a configured secret. The server-side log carries
    the full exception plus the request that caused it, which is where the
    diagnosis belongs.
    """
    logger.exception(
        "Unhandled %s on %s %s",
        type(exc).__name__,
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal error occurred. The full details, with the "
            "request that caused them, were logged server-side.",
            "error_code": "internal_error",
        },
    )
