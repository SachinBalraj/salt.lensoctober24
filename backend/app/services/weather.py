"""Module 4 - storing and reading weather records.

Records are append-only. Nothing in this module updates or deletes a weather
observation, so the history an operator or a provider contributed stays
intact and auditable.

Provenance is applied in exactly one place per path. Manual entry stamps what
the caller declared, after refusing the one value it must never accept.
Provider ingest does not read provenance from the request at all: it takes the
source from the registered provider and stamps it itself, so no request body
can make a record look machine-retrieved when nothing fetched it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from pydantic import ValidationError

from app.config import settings
from app.db.errors import DocumentNotFoundError
from app.db.indexes import SALT_PANS, WEATHER_OBSERVATIONS
from app.db.repository import MongoRepository
from app.models.base import normalize_pan_id, utcnow
from app.models.enums import WeatherSource
from app.models.salt_pan import SaltPan
from app.models.weather import WeatherObservation
from app.schemas.weather_fetch import WeatherFetchResult
from app.schemas.weather import (
    SENSOR_UNAVAILABLE_MESSAGE,
    WeatherIngestResponse,
    WeatherObservationCreate,
    WeatherObservationListResponse,
    WeatherObservationRead,
    WeatherProviderRead,
    WeatherProvidersResponse,
)
from app.services.weather_providers import (
    ForecastWindow,
    ProviderForecastPoint,
    WeatherProvider,
    WeatherProviderError,
    WeatherProviderRegistry,
    provider_registry,
)


class WeatherService:
    """Manual weather entry, provider forecast ingest, and history retrieval."""

    def __init__(self, database: Any) -> None:
        self._database = database
        self._resolved_pans: MongoRepository[SaltPan] | None = None
        self._resolved_weather: MongoRepository[WeatherObservation] | None = None

    @property
    def _pan_repository(self) -> MongoRepository[SaltPan]:
        if self._resolved_pans is None:
            self._resolved_pans = MongoRepository(
                self._database, SALT_PANS, SaltPan
            )
        return self._resolved_pans

    @property
    def _weather_repository(self) -> MongoRepository[WeatherObservation]:
        if self._resolved_weather is None:
            self._resolved_weather = MongoRepository(
                self._database, WEATHER_OBSERVATIONS, WeatherObservation
            )
        return self._resolved_weather

    # -- guards -----------------------------------------------------------

    @staticmethod
    def _normalized_pan_id(pan_id: str) -> str:
        normalized = normalize_pan_id(pan_id)
        if not normalized:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="pan_id must not be blank.",
            )
        return normalized

    async def _require_registered_pan(self, pan_id: str) -> SaltPan:
        normalized = self._normalized_pan_id(pan_id)
        try:
            return await self._pan_repository.get("pan_id", normalized)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"No registered pan has the ID “{pan_id}”. Register the pan "
                    "before recording weather for it."
                ),
            ) from exc

    @staticmethod
    def _reject_sensor_source(source: WeatherSource) -> None:
        """Refuse a sensor reading while no gateway is connected.

        Attributing a reading to an instrument that is not there is the same
        fabrication as attributing it to an API that was never called.
        """
        if source is not WeatherSource.SENSOR:
            return
        if not settings.sensor_ingest_enabled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=SENSOR_UNAVAILABLE_MESSAGE,
            )

    async def require_registered_pan(self, pan_id: str) -> SaltPan:
        """Public spelling of the registration guard, for routes outside this file."""
        return await self._require_registered_pan(pan_id)

    # -- writes -----------------------------------------------------------

    async def store_fetched_weather(
        self, result: WeatherFetchResult
    ) -> tuple[WeatherObservation, bool]:
        """Store one reviewed Open-Meteo retrieval under ``source=weather_api``.

        The provenance a record carries is decided here, from the retrieval
        this server performed, not from anything a client sent. A fetch result
        for an instant already stored for this pan returns the existing record
        with ``created=False`` so an operator double-clicking Save cannot write
        the same reading twice.

        Timestamp rules: a historical record is dated at the instant it
        describes, because that is when the weather happened. A forecast is
        dated at retrieval and points forward at ``forecast_for``, because the
        prediction existed only from the moment it was fetched.
        """
        pan = await self._require_registered_pan(result.pan_id)
        requested = datetime.fromisoformat(
            result.requested_at.replace("Z", "+00:00")
        ).astimezone(timezone.utc)
        retrieved = datetime.fromisoformat(
            result.retrieved_at.replace("Z", "+00:00")
        ).astimezone(timezone.utc)

        existing = await self._weather_repository.find(
            {
                "pan_id": pan.pan_id,
                "source": WeatherSource.WEATHER_API,
                "provider": result.provider,
                "requested_at": requested,
            },
            limit=1,
        )
        if existing:
            return existing[0], False

        if result.weather_type == "historical":
            timestamp = requested
            forecast_for = None
        else:
            timestamp = min(retrieved, requested)
            forecast_for = requested

        try:
            observation = WeatherObservation(
                pan_id=pan.pan_id,
                timestamp=timestamp,
                forecast_for=forecast_for,
                rainfall_mm=result.rainfall_mm,
                rainfall_probability_percent=result.rain_probability_percent,
                ambient_temperature_c=result.ambient_temperature_c,
                humidity_percent=result.humidity_percent,
                source=WeatherSource.WEATHER_API,
                provider=result.provider,
                latitude=result.latitude,
                longitude=result.longitude,
                weather_type=result.weather_type,
                requested_at=requested,
                retrieved_at=retrieved,
            )
        except (ValidationError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "The retrieved weather contained no usable measurement for "
                    f"this pan: {exc}"
                ),
            ) from exc
        return await self._weather_repository.insert(observation), True

    async def create(self, payload: WeatherObservationCreate) -> WeatherObservation:
        """Store one hand-entered weather record.

        ``source=weather_api`` has already been refused by the request schema,
        so the only two values that reach here are ``manual`` and ``sensor``.
        """
        self._reject_sensor_source(payload.source)
        pan = await self._require_registered_pan(payload.pan_id)

        observation = WeatherObservation(
            pan_id=pan.pan_id,
            timestamp=payload.resolved_timestamp(),
            forecast_for=payload.forecast_for,
            rainfall_mm=payload.rainfall_mm,
            rainfall_probability_percent=payload.rainfall_probability_percent,
            ambient_temperature_c=payload.ambient_temperature_c,
            humidity_percent=payload.humidity_percent,
            source=payload.source,
        )
        return await self._weather_repository.insert(observation)

    async def store_forecast_point(
        self,
        pan_id: str,
        point: ProviderForecastPoint,
        *,
        source: WeatherSource,
    ) -> WeatherObservation | None:
        """Store one point a provider returned, or refuse it.

        Returns ``None`` when the point cannot be stored, for instance a forecast
        aimed at an instant that already passed. The ingest response reports the
        count so a partially usable provider result stays visible instead of
        disappearing.
        """
        if source is not WeatherSource.WEATHER_API:
            raise ValueError(
                "provider readings must be stored with source weather_api; "
                f"got {source!r}"
            )
        try:
            observation = WeatherObservation(
                pan_id=self._normalized_pan_id(pan_id),
                timestamp=utcnow(),
                forecast_for=point.forecast_for,
                rainfall_mm=point.rainfall_mm,
                rainfall_probability_percent=point.rainfall_probability_percent,
                ambient_temperature_c=point.ambient_temperature_c,
                humidity_percent=point.humidity_percent,
                source=source,
            )
        except (ValidationError, ValueError):
            return None
        return await self._weather_repository.insert(observation)

    async def ingest_forecast(
        self,
        provider: WeatherProvider,
        pan_id: str,
        window: ForecastWindow,
    ) -> WeatherIngestResponse:
        """Ask a registered provider for a forecast and store what it returns.

        Every stored record is stamped ``weather_api`` by this method, not by the
        caller. An upstream failure surfaces as 502 so a provider fault is never
        mistaken for an absence of weather data.
        """
        pan = await self._require_registered_pan(pan_id)

        try:
            available = await provider.available()
        except WeatherProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Weather provider “{provider.name}” could not be reached: {exc}",
            ) from exc
        if not available:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"Weather provider “{provider.name}” is registered but reports "
                    "itself unavailable. Check its credentials and connectivity."
                ),
            )

        try:
            points: Sequence[ProviderForecastPoint] = await provider.fetch_forecast(
                pan, window
            )
        except WeatherProviderError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Weather provider “{provider.name}” returned an error: {exc}",
            ) from exc

        stored: list[WeatherObservationRead] = []
        rejected = 0
        for point in points:
            observation = await self.store_forecast_point(
                pan.pan_id, point, source=provider.source
            )
            if observation is None:
                rejected += 1
            else:
                stored.append(WeatherObservationRead.from_observation(observation))

        return WeatherIngestResponse(
            provider=provider.name,
            source=provider.source,
            pan_id=pan.pan_id,
            requested_start=window.start,
            requested_end=window.end,
            stored=len(stored),
            rejected=rejected,
            observations=stored,
        )

    # -- reads ------------------------------------------------------------

    async def get(self, weather_id: str) -> WeatherObservation:
        try:
            return await self._weather_repository.get("weather_id", weather_id)
        except DocumentNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No weather record has the ID “{weather_id}”.",
            ) from exc

    async def list_observations(
        self,
        *,
        pan_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> WeatherObservationListResponse:
        """Return weather records newest first, optionally narrowed to a pan.

        Forecasts are included in the same history as observations, because a
        researcher looking at a pan's weather needs to see what was predicted
        alongside what happened. Each record carries its own ``is_forecast`` and
        ``source`` so the two are never conflated.
        """
        if start is not None and end is not None and start > end:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="start must not be later than end.",
            )

        query: dict[str, Any] = {}
        if pan_id:
            await self._require_registered_pan(pan_id)
            query["pan_id"] = self._normalized_pan_id(pan_id)

        window: dict[str, Any] = {}
        if start is not None:
            window["$gte"] = start
        if end is not None:
            window["$lte"] = end
        if window:
            query["timestamp"] = window

        total = await self._weather_repository.count(query)
        observations = await self._weather_repository.find(
            query, sort=[("timestamp", -1)], limit=limit, skip=offset
        )
        timestamps = [item.timestamp for item in observations]
        return WeatherObservationListResponse(
            items=[WeatherObservationRead.from_observation(item) for item in observations],
            total=total,
            limit=limit,
            offset=offset,
            earliest_timestamp=min(timestamps) if timestamps else None,
            latest_timestamp=max(timestamps) if timestamps else None,
        )

    @staticmethod
    def list_providers(
        registry: WeatherProviderRegistry | None = None,
    ) -> WeatherProvidersResponse:
        """Describe the weather sources this deployment can actually reach.

        The empty registry is the truthful answer for this prototype, and saying
        so is the point: the UI uses it to explain why no forecast can be pulled
        and why records are labelled as hand-entered.
        """
        active = registry if registry is not None else provider_registry
        names = active.names()
        return WeatherProvidersResponse(
            registered=[
                WeatherProviderRead(
                    name=name,
                    source=WeatherSource.WEATHER_API,
                )
                for name in names
            ],
            count=len(active),
            manual_entry_available=True,
            detail=(
                "No weather API is connected. Readings are recorded manually or by "
                "a sensor; the forecast ingest route stays unavailable until a "
                "provider is registered."
                if not names
                else f"{len(names)} weather provider(s) registered; forecasts can be "
                "ingested on demand."
            ),
        )

    async def registry_counts(self) -> dict[str, int]:
        """Weather totals for the module status endpoint."""
        return {"total_weather_records": await self._weather_repository.count()}
