"""The one place that decides what a ``database`` argument actually is.

Two things are passed in that parameter position throughout the project:

1. an already-created database, which is what a test's dependency override
   supplies and what ``MongoDatabase.get_db`` returns, and
2. a zero-argument provider that returns one, which is what the routers pass so
   that an unavailable database raises its 503 inside the route handler, after
   FastAPI has validated the request body.

Both therefore have to be accepted by every layer that reaches into a database,
and the distinction is made once, here, rather than repeated in each service.
The mistake this exists to prevent is calling a database: Motor's database
objects are not callable, so ``database()`` raises ``TypeError`` instead of
returning anything, and the failure surfaces far from its cause.
"""

from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.database import Database


def resolve_database(database_or_provider: Any) -> Any:
    """Return the database, whether it was given as one or as a provider.

    An actual database object is returned untouched even though the check below
    would otherwise treat it as a provider: Motor's databases are not callable,
    so invoking one raises ``TypeError: 'AsyncIOMotorDatabase' object is not
    callable``, and no amount of intent can rescue that.
    """
    if isinstance(database_or_provider, (AsyncIOMotorDatabase, Database)):
        return database_or_provider
    if callable(database_or_provider):
        return database_or_provider()
    return database_or_provider
