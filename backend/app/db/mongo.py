from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Any, Self

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import PyMongoError

from app.config import settings
from app.db.errors import DatabaseNotConfiguredError
from app.db.indexes import IndexReport, ensure_indexes

# Re-exported so callers can reach the database-handle resolver through the
# module they already import for MongoDatabase. The definition lives in
# app.db.resolution, which both the repository and the index plan import, and
# which cannot be imported from here at module scope without a cycle.
from app.db.resolution import resolve_database

logger = logging.getLogger("saltlens.db")

__all__ = ["HealthResult", "MongoDatabase", "db", "resolve_database"]


class HealthResult(dict):
    def __await__(self):
        async def _result() -> HealthResult:
            return self

        return _result().__await__()


_UNSET = object()


class MongoDatabase:
    def __init__(
        self,
        uri: Any = _UNSET,
        database_name: str | None = None,
        client_factory: Callable[..., Any] = AsyncIOMotorClient,
    ) -> None:
        self._uri = uri
        self._database_name = database_name
        self._client_factory = client_factory
        self._client: Any | None = None
        self._database: Any | None = None
        self._connected = False
        self._indexes_ready = False
        self._indexes_attempted = False
        self._last_error: str | None = None
        self._last_health: HealthResult | None = None
        self._lock = asyncio.Lock()

    def _effective_uri(self) -> str | None:
        # Distinguish between an explicit ``uri=None`` (meaning: intentionally
        # unconfigured) and the default sentinel which means "use settings".
        if self._uri is _UNSET:
            uri = settings.mongodb_uri
        else:
            # explicit None means the caller requested an unconfigured DB
            uri = self._uri
        if not isinstance(uri, str):
            return None
        return uri.strip() or None

    @property
    def configured(self) -> bool:
        return self._effective_uri() is not None

    @property
    def database_name(self) -> str:
        return self._database_name or settings.database_name

    @property
    def indexes_ready(self) -> bool:
        return self._indexes_ready

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def _safe_db_error(self, exc: Exception) -> str:
        """Log the real failure, return a message safe to show clients.

        The health endpoints are anonymous, so a raw ``PyMongoError`` text must
        not reach a response: it can carry host names, DNS detail and, in some
        drivers, fragments of the connection string. The full exception is kept
        in the server-side log where the diagnosis belongs.
        """
        logger.error(
            "MongoDB operation failed: %s: %s", type(exc).__name__, exc
        )
        return "MongoDB is unreachable."

    def _set_health(
        self,
        status: str,
        *,
        connected: bool,
        indexes_ready: bool,
        error: str | None = None,
        latency_ms: float | None = None,
    ) -> HealthResult:
        result = HealthResult(
            status=status,
            configured=self.configured,
            connected=connected,
            indexes_ready=indexes_ready,
            database=self.database_name,
            latency_ms=latency_ms,
        )
        if error:
            result["error"] = error
        self._connected = connected
        self._indexes_ready = indexes_ready
        self._last_error = error
        self._last_health = result
        return result

    def _reset_without_client(self) -> None:
        self._client = None
        self._database = None
        self._connected = False
        self._indexes_ready = False
        self._indexes_attempted = False

    def _create_client(self) -> None:
        uri = self._effective_uri()
        if not uri:
            raise DatabaseNotConfiguredError("MONGODB_URI is not configured")
        self._client = self._client_factory(
            uri,
            serverSelectionTimeoutMS=settings.mongodb_connect_timeout_ms,
            connectTimeoutMS=settings.mongodb_connect_timeout_ms,
            appname=settings.mongodb_app_name,
        )
        self._database = self._client[self.database_name]

    async def _ping_locked(self) -> tuple[bool, float | None, str | None]:
        if self._client is None or self._database is None:
            return False, None, "MongoDB client is not initialized"
        started = time.perf_counter()
        try:
            await self._client.admin.command("ping")
        except PyMongoError as exc:
            latency = round((time.perf_counter() - started) * 1000, 2)
            return False, latency, self._safe_db_error(exc)
        latency = round((time.perf_counter() - started) * 1000, 2)
        return True, latency, None

    async def _index_locked(self) -> None:
        if self._database is None:
            self._indexes_ready = False
            self._indexes_attempted = True
            return
        if self._indexes_attempted:
            return
        self._indexes_attempted = True
        # Delegate to the index provisioning helper. Prefer the instance
        # wrapper so callers that accidentally call ``db.ensure_indexes()`` get
        # a consistent behaviour rather than an AttributeError.
        try:
            report = await self.ensure_indexes(strict=False)
        except Exception as exc:  # noqa: BLE001 - deliberate: record-and-retry
            # Record a safe error for health reporting and allow retry later.
            # This is an index-provisioning failure and must not be reported as a
            # connection failure: the ping that precedes it just succeeded, and
            # ``_safe_db_error`` returns the connection sentence for any exception
            # that reaches it. Logging before overwriting keeps the real cause in
            # the server-side log while the client sees an accurate sentence.
            logger.error(
                "Index provisioning failed: %s: %s", type(exc).__name__, exc
            )
            self._indexes_ready = False
            self._indexes_attempted = False
            self._last_error = (
                "MongoDB is connected but indexes could not be provisioned"
            )
            return
        self._indexes_ready = report.ready
        if not report.ready:
            self._indexes_attempted = False
            self._last_error = "MongoDB is connected but one or more indexes failed"

    async def ensure_indexes(self, *, strict: bool = False) -> IndexReport:
        """Ensure the application's MongoDB indexes exist.

        This is the single index-initialization path: it provisions the plan in
        :mod:`app.db.indexes` against the database this manager created, using
        Motor's own ``create_index`` on each collection. It is idempotent --
        every spec is named, so re-running against a provisioned cluster
        changes nothing and returns the same report.

        It exists on the manager so the call site has a database to provision
        and no caller has to reach past the manager into the raw handle.
        """
        if self._database is None:
            raise DatabaseNotConfiguredError("MongoDB client is not initialized")
        return await ensure_indexes(self._database, strict=strict)

    async def _connect_locked(self) -> str:
        if not self.configured:
            self._reset_without_client()
            self._set_health(
                "not_configured",
                connected=False,
                indexes_ready=False,
                error=None,
            )
            return "not_configured"
        if self._client is None or self._database is None:
            try:
                self._create_client()
            except DatabaseNotConfiguredError as exc:
                self._reset_without_client()
                self._set_health(
                    "error",
                    connected=False,
                    indexes_ready=False,
                    error=str(exc),
                )
                return "error"
            except (PyMongoError, ValueError) as exc:
                self._reset_without_client()
                self._set_health(
                    "error",
                    connected=False,
                    indexes_ready=False,
                    error=self._safe_db_error(exc),
                )
                return "error"
        if self._connected and self._indexes_attempted:
            return "connected"
        connected, latency, error = await self._ping_locked()
        if not connected:
            self._set_health(
                "error",
                connected=False,
                indexes_ready=False,
                error=error,
                latency_ms=latency,
            )
            return "error"
        await self._index_locked()
        self._set_health(
            "connected",
            connected=True,
            indexes_ready=self._indexes_ready,
            error=self._last_error if not self._indexes_ready else None,
            latency_ms=latency,
        )
        return "connected"

    async def connect(self) -> str:
        async with self._lock:
            return await self._connect_locked()

    async def check_health(self) -> HealthResult:
        if not self.configured:
            async with self._lock:
                await self._connect_locked()
            return self._last_health or self._set_health(
                "not_configured",
                connected=False,
                indexes_ready=False,
                error=None,
            )
        if self._client is None or self._database is None:
            async with self._lock:
                await self._connect_locked()
        if self._client is None or self._database is None:
            return self._last_health or self._set_health(
                "error",
                connected=False,
                indexes_ready=False,
                error="MongoDB client is not initialized",
            )
        async with self._lock:
            connected, latency, error = await self._ping_locked()
            if not connected:
                return self._set_health(
                    "error",
                    connected=False,
                    indexes_ready=False,
                    error=error,
                    latency_ms=latency,
                )
            await self._index_locked()
            return self._set_health(
                "connected",
                connected=True,
                indexes_ready=self._indexes_ready,
                error=self._last_error if not self._indexes_ready else None,
                latency_ms=latency,
            )

    def health(self) -> HealthResult:
        if self._last_health is not None:
            return self._last_health
        if not self.configured:
            return self._set_health(
                "not_configured",
                connected=False,
                indexes_ready=False,
                error=None,
            )
        status = "connected" if self._connected else "disconnected"
        return self._set_health(
            status,
            connected=self._connected,
            indexes_ready=self._indexes_ready,
            error=self._last_error,
        )

    def unavailable_reason(self) -> str | None:
        """Explain why the database cannot be used, or None when it can.

        Kept separate from :meth:`get_db` so status endpoints can report the
        reason instead of raising it.
        """
        if not self.configured:
            return (
                "Database not configured. Set MONGODB_URI in backend/.env "
                "to enable persistence."
            )
        if not self._connected or self._database is None:
            return "Database unavailable. Check the MongoDB Atlas connection health."
        return None

    def get_db(self) -> Any:
        reason = self.unavailable_reason()
        if reason is not None:
            raise HTTPException(status_code=503, detail=reason)
        return self._database

    def get_collection(self, collection_name: str) -> Any:
        database = self.get_db()
        return database[collection_name]

    async def close(self) -> None:
        async with self._lock:
            if self._client is not None:
                self._client.close()
            self._reset_without_client()
            self._last_health = None
            self._last_error = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        await self.close()


def _shared_database() -> MongoDatabase:
    """The database manager every module in this process shares.

    Unconfigured unless the process explicitly asked otherwise. ``tests/conftest.py``
    sets ``SALTLENS_TEST_NO_DATABASE`` before the application is imported, so a
    developer whose ``backend/.env`` points at a real Atlas cluster still runs
    the suite against nothing at all rather than against real records.

    The decision is read from an environment variable rather than from whether
    ``pytest`` is loaded, because both implicit readings fail in the direction
    that matters: a production process that imported ``pytest`` for any reason
    would lose persistence silently, and a test process that imported the
    application before pytest would silently gain a real connection.
    """
    isolated = os.environ.get("SALTLENS_TEST_NO_DATABASE", "").strip().lower()
    if isolated in {"1", "true", "yes"}:
        return MongoDatabase(uri=None)
    return MongoDatabase()


db = _shared_database()
