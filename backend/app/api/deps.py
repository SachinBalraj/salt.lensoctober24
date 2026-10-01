from typing import Annotated, Any

from fastapi import Depends, HTTPException

from app.db.mongo import db


def get_db() -> Any:
    """Return the database, raising 503 when persistence is unavailable."""
    return db.get_db()


def get_optional_db() -> Any | None:
    """Return the database, or None when persistence is unavailable.

    Unlike :func:`get_db` this never raises, so callers whose job is to
    report availability can do so instead of failing. It must be used as a
    FastAPI dependency so the value stays overridable in tests.
    """
    try:
        return db.get_db()
    except HTTPException:
        return None


db_dependency = Depends(get_db)

OptionalDbDependency = Annotated[Any | None, Depends(get_optional_db)]
