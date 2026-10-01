"""Module 4 - weather data for a salt pan.

Manual entry is the working path: an operator records the rain, temperature and
humidity they actually observed, and the record is stored labelled ``manual``
permanently. The UI renders that label next to every record, so a hand-entered
figure can never be mistaken for something a service retrieved.

The forecast API path is built and waiting but has nothing behind it. A
:class:`~app.services.weather_providers.WeatherProvider` is all that has to be
implemented and registered for ``POST /api/weather/ingest/{provider}`` to work
against a real service; until then the route reports honestly that no provider
is connected instead of returning an invented forecast.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.deps import OptionalDbDependency, get_db
from app.config import settings
from app.db.mongo import db
from app.schemas.weather import (
    WeatherIngestResponse,
    WeatherObservationCreate,
    WeatherObservationListResponse,
    WeatherObservationRead,
    WeatherProvidersResponse,
)
from app.services.weather import WeatherService
from app.services.weather_providers import (
    PROVIDER_NAME_PATTERN,
    ForecastWindow,
    WeatherProviderRegistry,
    provider_registry,
)

router = APIRouter(prefix="/api/weather", tags=["04-weather"])

DESCRIPTION = (
    "Weather observations and forecasts for a salt pan, with the source of every "
    "record stored and displayed."
)

#: Default forecast window for an ingest with no explicit period: the next two
#: days, which is the range an operator can actually act on.
DEFAULT_FORECAST_HORIZON = timedelta(days=2)

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

WeatherIdPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Server-assigned weather record identifier.",
        examples=["wx-2f1c9a0e5b7d4c3a9f0e1b2c3d4e5f60"],
    ),
]

ProviderNamePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=63,
        pattern=PROVIDER_NAME_PATTERN,
        description="Name of a registered weather provider.",
        examples=["openweather"],
    ),
]


def get_weather_service() -> WeatherService:
    """Build the service with a lazy database handle.

    ``get_db`` is passed as a provider rather than called here so the 503 for an
    unconfigured database is raised inside the handler, after FastAPI has
    validated the request body.
    """
    return WeatherService(get_db)


ServiceDependency = Annotated[WeatherService, Depends(get_weather_service)]


def get_weather_status_service(
    database: OptionalDbDependency,
) -> WeatherService | None:
    """Build a service for status reporting, or None when there is no database."""
    return WeatherService(database) if database is not None else None


OptionalStatusDependency = Annotated[
    WeatherService | None, Depends(get_weather_status_service)
]


def get_provider_registry() -> WeatherProviderRegistry:
    """The deployment-wide provider registry, overridable in tests."""
    return provider_registry


RegistryDependency = Annotated[
    WeatherProviderRegistry, Depends(get_provider_registry)
]

ENDPOINTS = [
    "GET  /api/weather/status",
    "GET  /api/weather/providers",
    "POST /api/weather/",
    "GET  /api/weather/?pan_id=...",
    "GET  /api/weather/{weather_id}",
    "POST /api/weather/ingest/{provider_name}  (unavailable: no provider connected)",
]


@router.get("/status", summary="Module 4 implementation status")
async def module_status(service: OptionalStatusDependency) -> dict[str, Any]:
    """Report that Module 4 is implemented, with live record and provider counts.

    This endpoint never fails. An unavailable database is reported as
    ``weather.available = false`` rather than raised, because reporting status
    is the one thing it exists to do.
    """
    providers = WeatherService.list_providers()
    payload: dict[str, Any] = {
        "module": "weather",
        "module_number": 4,
        "status": "implemented",
        "description": DESCRIPTION,
        "endpoints": ENDPOINTS,
        "manual_entry": {
            "available": True,
            "detail": (
                "Weather records can be entered by hand. Every such record is "
                "stored and displayed with source='manual'."
            ),
        },
        "weather_api": {
            "available": providers.count > 0,
            "registered_providers": [item.name for item in providers.registered],
            "detail": providers.detail,
        },
        "sensor_ingest": {
            "available": settings.sensor_ingest_enabled,
            "detail": (
                "A weather sensor gateway is configured."
                if settings.sensor_ingest_enabled
                else "No weather sensor gateway is connected. source='sensor' is "
                "refused with 409."
            ),
        },
    }
    if service is None:
        payload["weather"] = {
            "available": False,
            "detail": db.unavailable_reason(),
        }
    else:
        payload["weather"] = {
            "available": True,
            **await service.registry_counts(),
        }
    return payload


@router.get(
    "/providers",
    response_model=WeatherProvidersResponse,
    summary="List connected weather APIs",
)
async def list_providers(
    registry: RegistryDependency,
) -> WeatherProvidersResponse:
    """Report which weather APIs this deployment can reach.

    The list is empty on a prototype with no weather API connected, which is the
    honest answer and the reason the UI labels every record as hand-entered.
    """
    return WeatherService.list_providers(registry)


@router.post(
    "/",
    response_model=WeatherObservationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a weather observation for a registered pan",
)
async def create_weather_observation(
    payload: WeatherObservationCreate,
    service: ServiceDependency,
) -> WeatherObservationRead:
    """Store one hand-entered weather record.

    ``timestamp`` defaults to now and is preserved exactly as supplied, so a
    record can be backdated to the moment the weather was actually observed.
    Fields left blank stay null: they are never defaulted, averaged or carried
    forward.

    ``source`` accepts ``manual`` or ``sensor``. ``weather_api`` is rejected with
    422, because only the forecast ingest route may set that provenance and only
    after a provider has returned data. Returns 404 if the pan is not registered
    and 409 if ``source`` is ``sensor`` while no gateway is connected.
    """
    observation = await service.create(payload)
    return WeatherObservationRead.from_observation(observation)


@router.get(
    "/",
    response_model=WeatherObservationListResponse,
    summary="List weather records, newest first",
)
async def list_weather_observations(
    service: ServiceDependency,
    pan_id: PanIdQuery = None,
    start: Annotated[
        datetime | None,
        Query(description="Only records at or after this instant (ISO 8601)."),
    ] = None,
    end: Annotated[
        datetime | None,
        Query(description="Only records at or before this instant (ISO 8601)."),
    ] = None,
    limit: Annotated[
        int, Query(ge=1, le=1000, description="Max records returned.")
    ] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WeatherObservationListResponse:
    """Return stored records, optionally narrowed to a pan and a time window.

    Forecasts and observations share one history, because comparing what was
    predicted against what happened is the point of keeping both. Each record
    reports its own ``is_forecast`` and ``source``.
    """
    return await service.list_observations(
        pan_id=pan_id,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/ingest/{provider_name}",
    response_model=WeatherIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Fetch a forecast from a registered weather API",
)
async def ingest_forecast(
    provider_name: ProviderNamePath,
    service: ServiceDependency,
    registry: RegistryDependency,
    pan_id: Annotated[
        str,
        Query(
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9._-]+$",
            description="Pan to fetch a forecast for. Matched case-insensitively.",
            examples=["pans-2026-a01"],
        ),
    ],
    start: Annotated[
        datetime | None,
        Query(description="Start of the forecast window (ISO 8601). Defaults to now."),
    ] = None,
    end: Annotated[
        datetime | None,
        Query(
            description=(
                "End of the forecast window (ISO 8601). Defaults to 48 hours "
                "after the start."
            )
        ),
    ] = None,
) -> WeatherIngestResponse:
    """Store the forecast a registered provider returns for one pan.

    This is the only path that writes ``source="weather_api"``, and it sets that
    value itself from the provider rather than from the request. When no provider
    is registered the route answers 404 and no record is created, so an absent
    API can never leave a record that looks like a successful fetch.
    """
    provider = registry.get(provider_name)
    if provider is None:
        registered = registry.names()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No weather provider named “{provider_name}” is registered. "
                + (
                    "No weather API is connected to this deployment, so forecasts "
                    "cannot be fetched. Record weather manually through "
                    "POST /api/weather/."
                    if not registered
                    else "Registered providers: " + ", ".join(registered) + "."
                )
            ),
        )

    window = _resolve_window(start, end)
    return await service.ingest_forecast(provider, pan_id, window)


def _resolve_window(start: datetime | None, end: datetime | None) -> ForecastWindow:
    """Build the requested forecast window, defaulting to the next two days."""
    resolved_start = start if start is not None else datetime.now(timezone.utc)
    resolved_end = (
        end if end is not None else resolved_start + DEFAULT_FORECAST_HORIZON
    )
    if resolved_end < resolved_start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end must not be earlier than start.",
        )
    return ForecastWindow(start=resolved_start, end=resolved_end)


@router.get(
    "/{weather_id}",
    response_model=WeatherObservationRead,
    summary="Open a single weather record",
)
async def get_weather_observation(
    weather_id: WeatherIdPath,
    service: ServiceDependency,
) -> WeatherObservationRead:
    """Return one stored record exactly as it was recorded, source included."""
    return WeatherObservationRead.from_observation(await service.get(weather_id))
