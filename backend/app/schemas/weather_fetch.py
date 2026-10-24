"""Schemas for the fetch-and-review weather route.

The fetch response carries a short-lived ``fetch_token`` instead of asking the
client to send weather values back for storage. A hand-transported payload
could be edited between preview and save, and a record would then claim
``source=weather_api`` for numbers no API ever produced. The token resolves
server-side to exactly what was fetched, so the only thing an operator
approves is the retrieval itself.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.weather import WeatherObservationRead


class WeatherFetchRequest(BaseModel):
    pan_id: str = Field(min_length=1, max_length=64)
    date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    time: str = Field(pattern=r"^\d{2}:\d{2}$")


class WeatherFetchResult(BaseModel):
    pan_id: str
    pan_name: str | None = None
    latitude: float
    longitude: float
    requested_date: str
    requested_time: str
    requested_at: str
    retrieved_at: str
    weather_type: str  # historical or forecast
    rainfall_mm: float | None = None
    rain_probability_percent: float | None = None
    ambient_temperature_c: float | None = None
    humidity_percent: float | None = None
    provider: str = "open-meteo"
    source_type: str = "weather_api"
    coordinates_used: dict[str, float]


class WeatherFetchResponse(BaseModel):
    success: bool
    data: WeatherFetchResult | None = None
    message: str
    fetch_token: str | None = None


class WeatherFetchSaveRequest(BaseModel):
    """Approve a retrieval by referring to it, never by restating its values."""

    fetch_token: str = Field(min_length=8, max_length=128)


class WeatherFetchSaveResponse(BaseModel):
    """The stored record, plus whether this save created it."""

    record: WeatherObservationRead
    created: bool
    message: str
