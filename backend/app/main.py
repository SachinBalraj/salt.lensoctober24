import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from pymongo.errors import PyMongoError

from app.api.errors import (
    internal_error_handler,
    persistence_error_handler,
    pymongo_error_handler,
    validation_error_handler,
)
from app.api.ratelimit import RateLimitMiddleware
from app.api.routers import all_routers
from app.config import (
    configuration_warnings,
    production_config_problems,
    settings,
)
from app.db.errors import PersistenceError
from app.schemas.common import HealthResponse

logger = logging.getLogger("saltlens")

try:
    from app.db.mongo import db
except ImportError:
    db = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    _assert_production_configuration()
    if db is not None:
        await db.connect()
    try:
        yield
    finally:
        if db is not None:
            await db.close()


def _assert_production_configuration() -> None:
    """Fail fast on production with an unsafe configuration; warn in dev.

    A production process that would sign nothing, serve over HTTP or run with no
    database must not start at all, because each of those is a silent trust
    failure rather than an observable one. In development the same checks become
    warnings, so local iteration is not blocked by secrets it does not need.
    """
    problems = production_config_problems(settings)
    if problems:
        raise RuntimeError(
            "SaltLens DSS refuses to start in APP_ENV=production with an unsafe "
            "configuration:\n - " + "\n - ".join(problems)
        )
    for warning in configuration_warnings(settings):
        logger.warning(warning)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "AI-driven digital twin decision support for climate-resilient "
        "salt-pan management. Research prototype - real data only."
    ),
    lifespan=lifespan,
)

# Rate limiting is added first so the CORS middleware stays outermost: an OPTIONS
# preflight that is never subject to the application's own budget, and every
# browser origin is exactly the one configured. The limiter itself is described
# in app/api/ratelimit.py; it is on by default and disabled via config.
if settings.rate_limit_enabled:
    app.add_middleware(RateLimitMiddleware, settings=settings)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(PersistenceError, persistence_error_handler)
app.add_exception_handler(PyMongoError, pymongo_error_handler)
app.add_exception_handler(ValidationError, validation_error_handler)
app.add_exception_handler(Exception, internal_error_handler)


@app.get("/", tags=["meta"])
def root():
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "documentation": "/docs",
        "health": "/api/health",
        "modules": "/api/modules",
    }


@app.get("/api/health", response_model=HealthResponse, tags=["meta"])
async def health():
    if db is None:
        database_health = {
            "status": "unknown",
            "configured": False,
            "connected": False,
            "indexes_ready": False,
        }
    else:
        database_health = await db.check_health()
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
        "database": database_health["status"],
        "database_health": dict(database_health),
        "indexes_ready": database_health.get("indexes_ready", False),
        "python": sys.version.split()[0],
    }


@app.get("/api/health/database", tags=["meta"])
async def database_health():
    if db is None:
        return {
            "status": "unknown",
            "configured": False,
            "connected": False,
            "indexes_ready": False,
        }
    return dict(await db.check_health())


@app.get("/health", tags=["meta"])
def health_simple():
    """Simple application health check."""
    return {"status": "ok"}


@app.get("/api/modules", tags=["meta"])
def modules():
    return {
        "count": len(all_routers),
        "prefixes": sorted({router.prefix for router in all_routers}),
        "note": "Each router exposes a placeholder status endpoint (status=not_implemented).",
    }


for router in all_routers:
    app.include_router(router)

# Register Open-Meteo provider
try:
    from app.services.open_meteo_registration import register_open_meteo
    register_open_meteo()
except Exception:
    pass

