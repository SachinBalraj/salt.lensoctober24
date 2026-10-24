"""Open-Meteo weather provider integration.

Two endpoints, chosen by one shared rule.

The archive endpoint (``archive-api.open-meteo.com``) answers for dates that
have already happened. The forecast endpoint (``api.open-meteo.com``) answers
for now and the near future. Requesting a past date from the forecast service
or a future date from the archive returns an error, so the choice is made by
:func:`classify_weather_type` and never re-derived ad hoc: the router that
stores a record and this provider that fetches it must agree on what a
"historical" and a "forecast" are, or the label on a saved record would
describe an endpoint that was never called.

Naive timestamps coming back from Open-Meteo are local wall-clock times in the
requested timezone. They are tagged ``Asia/Kolkata`` here rather than inherited
from the host machine's zone, so a record does not shift by the timezone of
whatever server fetched it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

import httpx

from app.models.enums import WeatherSource
from app.models.salt_pan import SaltPan
from app.services.weather_providers import (
    ForecastWindow,
    ProviderForecastPoint,
    WeatherProvider,
    WeatherProviderError,
)

#: Reanalysis archive: observed conditions for dates that have already passed.
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

#: Forecast service: current conditions and predictions for the near future.
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

#: The timezone SaltLens interprets operator-supplied date/time in, and the
#: timezone Open-Meteo is asked to report its hourly series in.
REQUEST_TIMEZONE = ZoneInfo("Asia/Kolkata")

HOURLY_FORECAST = (
    "temperature_2m,relative_humidity_2m,precipitation,rain,precipitation_probability"
)

#: The archive has no skill at predicting rain, so it is not asked for a
#: probability. Requesting it returns a column of nulls, which would read as
#: "the archive declined to say" rather than "this dataset has no such field".
HOURLY_ARCHIVE = "temperature_2m,relative_humidity_2m,precipitation,rain"

HISTORICAL = "historical"
FORECAST = "forecast"


def classify_weather_type(
    requested: datetime, now: datetime | None = None
) -> str:
    """Whether an instant calls for the archive or the forecast endpoint.

    Past means historical, current or future means forecast. The same helper is
    used by the fetch router and by this provider, so the weather type recorded
    on a saved row is the endpoint that actually answered, not a second opinion
    formed somewhere else.
    """
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=REQUEST_TIMEZONE)
    if now is None:
        now = datetime.now(requested.tzinfo)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=requested.tzinfo)
    return HISTORICAL if requested < now else FORECAST


def _local_naive_to_aware(value: str) -> datetime | None:
    """Interpret an Open-Meteo hourly time as Asia/Kolkata wall-clock."""
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        try:
            naive = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if naive.tzinfo is not None:
        return naive.astimezone(REQUEST_TIMEZONE)
    return naive.replace(tzinfo=REQUEST_TIMEZONE)


def _as_float(values: list, index: int) -> float | None:
    if index >= len(values) or values[index] is None:
        return None
    try:
        return float(values[index])
    except (TypeError, ValueError):
        return None


class OpenMeteoProvider(WeatherProvider):
    """Open-Meteo weather provider for salt pan coordinates."""

    @property
    def name(self) -> str:
        return "open-meteo"

    @property
    def source(self) -> WeatherSource:
        return WeatherSource.WEATHER_API

    async def available(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    FORECAST_URL,
                    params={
                        "latitude": 0,
                        "longitude": 0,
                        "current": "temperature_2m",
                        "forecast_days": 1,
                    },
                )
                return response.status_code < 500
        except Exception:
            return False

    async def fetch_hourly(
        self,
        pan: SaltPan,
        requested: datetime,
        weather_type: str,
    ) -> ProviderForecastPoint:
        """The single hourly reading closest to ``requested``.

        Used by the fetch-and-review route, which asks for one instant rather
        than a window. Raises :class:`WeatherProviderError` when the endpoint
        cannot answer for that date, so an impossible request reads as a clear
        failure instead of an empty preview.
        """
        self._require_coordinates(pan)
        if requested.tzinfo is None:
            requested = requested.replace(tzinfo=REQUEST_TIMEZONE)

        points = await self._fetch_range(
            pan,
            requested.astimezone(REQUEST_TIMEZONE).date(),
            requested.astimezone(REQUEST_TIMEZONE).date(),
            weather_type,
        )
        if not points:
            raise WeatherProviderError(
                f"Open-Meteo returned no data for {requested.date().isoformat()} "
                f"on the {weather_type} endpoint."
            )
        return min(points, key=lambda p: abs((p.forecast_for - requested).total_seconds()))

    async def fetch_forecast(
        self,
        pan: SaltPan,
        window: ForecastWindow,
    ) -> Sequence[ProviderForecastPoint]:
        """Hourly readings for a window, for the forecast ingest route.

        A window entirely in the past is archive data; anything reaching into
        the future is forecast data, because that is the endpoint that can
        answer for it.
        """
        self._require_coordinates(pan)
        end = self._aware(window.end)
        weather_type = classify_weather_type(end)
        local_start = self._aware(window.start).astimezone(REQUEST_TIMEZONE)
        local_end = end.astimezone(REQUEST_TIMEZONE)
        return await self._fetch_range(pan, local_start.date(), local_end.date(), weather_type)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=REQUEST_TIMEZONE)
        return value

    @staticmethod
    def _require_coordinates(pan: SaltPan) -> None:
        if pan.latitude is None or pan.longitude is None:
            raise WeatherProviderError(
                "Pan has no registered coordinates for weather lookup"
            )

    async def _fetch_range(
        self,
        pan: SaltPan,
        start_date,
        end_date,
        weather_type: str,
    ) -> list[ProviderForecastPoint]:
        is_archive = weather_type == HISTORICAL
        url = ARCHIVE_URL if is_archive else FORECAST_URL
        hourly = HOURLY_ARCHIVE if is_archive else HOURLY_FORECAST
        params = {
            "latitude": pan.latitude,
            "longitude": pan.longitude,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "hourly": hourly,
            "timezone": "Asia/Kolkata",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise WeatherProviderError(f"Failed to contact Open-Meteo: {exc}") from exc

        if response.status_code >= 400:
            raise WeatherProviderError(
                f"Open-Meteo {weather_type} endpoint returned "
                f"{response.status_code} for {start_date.isoformat()}. The date "
                "is outside the range that endpoint answers for."
            )
        try:
            hourly_data = response.json().get("hourly", {})
        except ValueError as exc:
            raise WeatherProviderError(
                f"Invalid response from Open-Meteo: {exc}"
            ) from exc

        times: list[str] = hourly_data.get("time", []) or []
        temps: list = hourly_data.get("temperature_2m", []) or []
        humidity: list = hourly_data.get("relative_humidity_2m", []) or []
        precip: list = hourly_data.get("precipitation", []) or []
        rain: list = hourly_data.get("rain", []) or []
        probability: list = hourly_data.get("precipitation_probability", []) or []

        points: list[ProviderForecastPoint] = []
        for index, raw_time in enumerate(times):
            when = _local_naive_to_aware(raw_time)
            if when is None:
                continue
            rainfall = _as_float(precip, index)
            if rainfall is None:
                rainfall = _as_float(rain, index)
            points.append(
                ProviderForecastPoint(
                    forecast_for=when,
                    rainfall_mm=rainfall,
                    rainfall_probability_percent=_as_float(probability, index),
                    ambient_temperature_c=_as_float(temps, index),
                    humidity_percent=_as_float(humidity, index),
                )
            )
        return points


open_meteo_provider = OpenMeteoProvider()
