"""Fetch weather from Open-Meteo, review it, then save it with honest provenance.

Two routes, one handover.

``POST /api/weather/fetch`` calls Open-Meteo and returns what came back for
review. Nothing is stored. The response carries a ``fetch_token`` that the
server remembers for a short while, bound to exactly the values it fetched.

``POST /api/weather/fetch/save`` takes only that token. The operator approves
the retrieval; they do not restate it. The record is written with
``source=weather_api``, the provider name, the pan's coordinates, the weather
type and both timestamps stamped by this server — so no request body can make a
typed number look machine-retrieved, and no client-side edit between preview
and save can end up in the research record.

Manual entry is untouched: ``POST /api/weather/`` still stores hand-entered
records as ``manual``, and its schema still refuses ``weather_api``.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.routers.weather import RegistryDependency, ServiceDependency
from app.models.enums import WeatherSource
from app.schemas.weather import WeatherObservationRead
from app.schemas.weather_fetch import (
    WeatherFetchRequest,
    WeatherFetchResponse,
    WeatherFetchResult,
    WeatherFetchSaveRequest,
    WeatherFetchSaveResponse,
)
from app.services.open_meteo import (
    REQUEST_TIMEZONE,
    classify_weather_type,
    open_meteo_provider,
)
from app.services.weather import WeatherService
from app.services.weather_providers import WeatherProviderError

router = APIRouter(prefix="/api/weather", tags=["04-weather-fetch"])

#: How long a retrieval stays approvable. Long enough for an operator to read
#: the preview, short enough that a stale token does not outlive the weather.
FETCH_TOKEN_TTL_SECONDS = 1800

#: Oldest date SaltLens will ask the archive for, and the furthest ahead it
#: will ask the forecast service. Beyond either end Open-Meteo answers 400,
#: which would surface as an upstream fault rather than a plain bad request.
MIN_REQUEST_DATE = datetime(1940, 1, 1, tzinfo=timezone.utc)
MAX_LOOKAHEAD_DAYS = 400

# token -> (result, expires_at_epoch). Server-side only: the client never
# echoes values back, so this cache is the source of truth for what was fetched.
_FETCH_TOKENS: dict[str, tuple[WeatherFetchResult, float]] = {}


def _issue_token(result: WeatherFetchResult) -> str:
    now = time.monotonic()
    expired = [
        token
        for token, (_, expires_at) in _FETCH_TOKENS.items()
        if expires_at <= now
    ]
    for token in expired:
        _FETCH_TOKENS.pop(token, None)
    token = secrets.token_urlsafe(32)
    _FETCH_TOKENS[token] = (result, now + FETCH_TOKEN_TTL_SECONDS)
    return token


@router.post(
    "/fetch",
    response_model=WeatherFetchResponse,
    summary="Fetch weather from Open-Meteo for review",
)
async def fetch_weather_from_provider(
    payload: WeatherFetchRequest,
    service: ServiceDependency,
    registry: RegistryDependency,
) -> WeatherFetchResponse:
    """Ask Open-Meteo for one instant of weather over a registered pan.

    Nothing is stored. The returned data is a preview carrying a token that
    ``POST /fetch/save`` can redeem for a stored, API-labelled record.
    """
    pan = await service.require_registered_pan(payload.pan_id)

    if not pan.is_active:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Pan “{pan.pan_id}” is retired. Reactivate it before fetching "
                "weather for it."
            ),
        )
    if pan.latitude is None or pan.longitude is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Weather cannot be fetched because this pan has no registered coordinates.",
        )
    if not (-90 <= pan.latitude <= 90) or not (-180 <= pan.longitude <= 180):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid coordinates for pan.",
        )

    try:
        requested = datetime.fromisoformat(f"{payload.date}T{payload.time}")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid date or time: {exc}",
        ) from exc
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=REQUEST_TIMEZONE)
    requested_utc = requested.astimezone(timezone.utc)

    now_utc = datetime.now(timezone.utc)
    if requested_utc < MIN_REQUEST_DATE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Dates before 1940-01-01 are outside the archive's range.",
        )
    if requested_utc > now_utc + timedelta(days=MAX_LOOKAHEAD_DAYS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Dates more than 400 days ahead are outside the forecast "
                "service's range."
            ),
        )

    weather_type = classify_weather_type(requested_utc, now_utc)

    provider = registry.get(open_meteo_provider.name) or open_meteo_provider
    try:
        point = await provider.fetch_hourly(pan, requested_utc, weather_type)
    except WeatherProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Weather could not be retrieved from Open-Meteo: {exc}",
        ) from exc

    retrieved_at = now_utc
    result = WeatherFetchResult(
        pan_id=pan.pan_id,
        pan_name=pan.pan_name,
        latitude=pan.latitude,
        longitude=pan.longitude,
        requested_date=payload.date,
        requested_time=payload.time,
        requested_at=requested_utc.isoformat().replace("+00:00", "Z"),
        retrieved_at=retrieved_at.isoformat().replace("+00:00", "Z"),
        weather_type=weather_type,
        rainfall_mm=point.rainfall_mm,
        rain_probability_percent=point.rainfall_probability_percent,
        ambient_temperature_c=point.ambient_temperature_c,
        humidity_percent=point.humidity_percent,
        provider=provider.name,
        source_type=WeatherSource.WEATHER_API.value,
        coordinates_used={"latitude": pan.latitude, "longitude": pan.longitude},
    )
    token = _issue_token(result)

    return WeatherFetchResponse(
        success=True,
        data=result,
        message="Weather retrieved successfully. Review it, then save it to store the record.",
        fetch_token=token,
    )


@router.post(
    "/fetch/save",
    response_model=WeatherFetchSaveResponse,
    summary="Save a reviewed Open-Meteo retrieval",
)
async def save_fetched_weather(
    payload: WeatherFetchSaveRequest,
    service: ServiceDependency,
    registry: RegistryDependency,
) -> WeatherFetchSaveResponse:
    """Redeem a fetch token for a stored record, stamped ``weather_api``.

    The values written are the ones this server fetched, held in its own
    cache — the request carries no weather numbers at all. A token that has
    expired or was never issued is refused, and a retrieval already saved for
    the same pan and instant is returned rather than written twice.
    """
    entry = _FETCH_TOKENS.get(payload.fetch_token)
    if entry is None or entry[1] <= time.monotonic():
        _FETCH_TOKENS.pop(payload.fetch_token, None)
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=(
                "This fetched weather result has expired or was never issued. "
                "Fetch the weather again to save it."
            ),
        )
    result = entry[0]

    observation, created = await service.store_fetched_weather(result)
    return WeatherFetchSaveResponse(
        record=WeatherObservationRead.from_observation(observation),
        created=created,
        message=(
            "Weather record saved."
            if created
            else "This weather reading was already saved; the existing record is returned."
        ),
    )
