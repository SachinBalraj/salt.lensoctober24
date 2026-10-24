"""Weather fetch-and-save API tests.

These cover the review handover added for Open-Meteo: ``POST /api/weather/fetch``
issues a short-lived token for a retrieval, and ``POST /api/weather/fetch/save``
redeems it. The invariants worth locking down:

- fetching stores nothing, and the preview is the only thing a token can be
  redeemed for -- so a typed number can never be labelled ``weather_api``;
- provenance is stamped by the server from the retrieval it performed;
- the same instant is never written twice;
- a token that expired or was never issued is refused, not silently accepted;
- a past instant is filed as historical and a future one as a forecast;
- manual entry is untouched and still refuses ``weather_api``.

The live provider is replaced through the provider registry, so no test talks
to Open-Meteo or to MongoDB.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.weather import get_provider_registry, get_weather_service
from app.api.routers.weather_fetch import _FETCH_TOKENS
from app.main import app
from app.models.enums import WeatherSource
from app.services.salt_pans import SaltPanService
from app.services.weather import WeatherService
from app.services.weather_providers import (
    ForecastWindow,
    ProviderForecastPoint,
    WeatherProviderError,
    WeatherProviderRegistry,
)
from tests.fakes import FakeDatabase

VALID_PAN = {
    "pan_id": "KZN-2026-F01",
    "pan_name": "Fetch Test Pan",
    "location": "Port Shepstone",
    "area_m2": 9_000.0,
    "normal_depth_cm": 15.0,
    "target_harvest_salinity_g_l": 280.0,
    "latitude": -30.74,
    "longitude": 30.81,
}

PAN_WITHOUT_COORDINATES = {
    "pan_id": "KZN-2026-F02",
    "pan_name": "Coordinateless Pan",
    "location": "Salem",
    "area_m2": 5_000.0,
    "normal_depth_cm": 12.0,
    "target_harvest_salinity_g_l": 275.0,
}


class FakeHourlyProvider:
    """Stands in for Open-Meteo: answers one instant, records what it was asked."""

    def __init__(
        self,
        name: str = "open-meteo",
        *,
        point: ProviderForecastPoint | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._name = name
        self._point = point
        self._raises = raises
        self.calls: list[tuple[str, datetime, str]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def source(self) -> WeatherSource:
        return WeatherSource.WEATHER_API

    async def available(self) -> bool:
        return True

    async def fetch_forecast(
        self, pan: Any, window: ForecastWindow
    ) -> list[ProviderForecastPoint]:
        return []

    async def fetch_hourly(
        self, pan: Any, requested: datetime, weather_type: str
    ) -> ProviderForecastPoint:
        self.calls.append((pan.pan_id, requested, weather_type))
        if self._raises is not None:
            raise self._raises
        if self._point is not None:
            return self._point
        return ProviderForecastPoint(
            forecast_for=requested,
            rainfall_mm=4.2,
            rainfall_probability_percent=60.0,
            ambient_temperature_c=26.5,
            humidity_percent=74.0,
        )


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def provider() -> FakeHourlyProvider:
    return FakeHourlyProvider()


@pytest.fixture()
def registry(provider: FakeHourlyProvider) -> WeatherProviderRegistry:
    """A registry holding only the fake, so the live provider is never reached."""
    registry = WeatherProviderRegistry()
    registry.register(provider)
    return registry


@pytest.fixture()
def fetch_client(
    database: FakeDatabase, registry: WeatherProviderRegistry
) -> Any:
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_weather_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_provider_registry] = lambda: registry
    _FETCH_TOKENS.clear()
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    _FETCH_TOKENS.clear()


@pytest.fixture()
def registered_pan(fetch_client: TestClient) -> str:
    response = fetch_client.post("/api/pans/", json=VALID_PAN)
    assert response.status_code == 201, response.text
    return response.json()["pan_id"]


def fetch(
    client: TestClient, pan_id: str, date: str, time: str = "14:00"
) -> Any:
    return client.post(
        "/api/weather/fetch",
        json={"pan_id": pan_id, "date": date, "time": time},
    )


def save(client: TestClient, token: str) -> Any:
    return client.post("/api/weather/fetch/save", json={"fetch_token": token})


def past_date(days: int = 300) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


def future_date(days: int = 2) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()


# -- fetching -------------------------------------------------------------

def test_fetch_stores_nothing(fetch_client, registered_pan):
    """A preview is a preview: only save may write."""
    response = fetch(fetch_client, registered_pan, past_date())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["fetch_token"]
    history = fetch_client.get("/api/weather/").json()
    assert history["total"] == 0


def test_fetch_reports_provider_weather_type_and_coordinates(
    fetch_client, registered_pan
):
    body = fetch(fetch_client, registered_pan, past_date()).json()

    data = body["data"]
    assert data["provider"] == "open-meteo"
    assert data["weather_type"] == "historical"
    assert data["source_type"] == "weather_api"
    assert data["coordinates_used"] == {"latitude": -30.74, "longitude": 30.81}
    assert data["requested_at"].endswith("Z")
    assert data["retrieved_at"].endswith("Z")


def test_a_past_instant_is_fetched_as_historical(fetch_client, registered_pan, provider):
    fetch(fetch_client, registered_pan, past_date())

    _, requested, weather_type = provider.calls[0]
    assert weather_type == "historical"
    assert requested < datetime.now(timezone.utc)


def test_a_future_instant_is_fetched_as_a_forecast(fetch_client, registered_pan, provider):
    fetch(fetch_client, registered_pan, future_date())

    _, requested, weather_type = provider.calls[0]
    assert weather_type == "forecast"
    assert requested > datetime.now(timezone.utc)


def test_fetch_requires_a_registered_pan(fetch_client):
    response = fetch(fetch_client, "no-such-pan", past_date())

    assert response.status_code == 404
    assert "No registered pan" in response.json()["detail"]


def test_fetch_refuses_a_pan_without_coordinates(fetch_client):
    fetch_client.post("/api/pans/", json=PAN_WITHOUT_COORDINATES)

    response = fetch(fetch_client, PAN_WITHOUT_COORDINATES["pan_id"], past_date())

    assert response.status_code == 422
    assert "no registered coordinates" in response.json()["detail"]


def test_fetch_refuses_a_retired_pan(fetch_client, registered_pan):
    fetch_client.post(f"/api/pans/{registered_pan}/deactivate")

    response = fetch(fetch_client, registered_pan, past_date())

    assert response.status_code == 409
    assert "retired" in response.json()["detail"]


def test_fetch_refuses_an_impossible_date(fetch_client, registered_pan):
    response = fetch(fetch_client, registered_pan, "1900-01-01")

    assert response.status_code == 422
    assert "1940" in response.json()["detail"]


def test_fetch_refuses_a_date_far_beyond_the_forecast_range(fetch_client, registered_pan):
    response = fetch(fetch_client, registered_pan, "2030-01-01")

    assert response.status_code == 422
    assert "400 days" in response.json()["detail"]


def test_fetch_refuses_an_unparseable_date(fetch_client, registered_pan):
    response = fetch(fetch_client, registered_pan, "2026-13-45")

    assert response.status_code == 422
    assert "Invalid date or time" in response.json()["detail"]


def test_an_upstream_failure_is_reported_as_a_gateway_fault(
    fetch_client, registered_pan, provider
):
    provider._raises = WeatherProviderError("the archive has no data for that date")

    response = fetch(fetch_client, registered_pan, past_date())

    assert response.status_code == 502
    assert "no data for that date" in response.json()["detail"]


# -- saving ---------------------------------------------------------------

def test_saving_stamps_api_provenance_from_the_server(fetch_client, registered_pan):
    token = fetch(fetch_client, registered_pan, past_date()).json()["fetch_token"]

    response = save(fetch_client, token)

    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert response.json()["created"] is True
    assert record["source"] == "weather_api"
    assert record["provider"] == "open-meteo"
    assert record["weather_type"] == "historical"
    assert record["latitude"] == -30.74
    assert record["longitude"] == 30.81
    assert record["requested_at"]
    assert record["retrieved_at"]
    assert record["rainfall_mm"] == pytest.approx(4.2)


def test_a_saved_historical_record_is_dated_at_the_instant_it_describes(
    fetch_client, registered_pan
):
    date = past_date()
    token = fetch(fetch_client, registered_pan, date).json()["fetch_token"]

    record = save(fetch_client, token).json()["record"]

    assert record["timestamp"] == record["requested_at"]
    assert record["forecast_for"] is None
    assert record["is_forecast"] is False


def test_a_saved_forecast_is_dated_at_retrieval_and_points_forward(
    fetch_client, registered_pan
):
    date = future_date()
    token = fetch(fetch_client, registered_pan, date).json()["fetch_token"]

    record = save(fetch_client, token).json()["record"]

    assert record["forecast_for"] == record["requested_at"]
    assert record["timestamp"] <= record["forecast_for"]
    assert record["is_forecast"] is True


def test_saving_the_same_retrieval_twice_writes_one_record(
    fetch_client, registered_pan
):
    token = fetch(fetch_client, registered_pan, past_date()).json()["fetch_token"]

    first = save(fetch_client, token).json()
    second = save(fetch_client, token).json()

    assert first["created"] is True
    assert second["created"] is False
    assert second["record"]["weather_id"] == first["record"]["weather_id"]
    history = fetch_client.get("/api/weather/").json()
    assert history["total"] == 1


def test_a_forged_token_is_refused(fetch_client):
    response = save(fetch_client, "a" * 32)

    assert response.status_code == 410
    assert "expired or was never issued" in response.json()["detail"]


def test_an_expired_token_is_refused(fetch_client, registered_pan):
    token = fetch(fetch_client, registered_pan, past_date()).json()["fetch_token"]
    result, _ = _FETCH_TOKENS[token]
    _FETCH_TOKENS[token] = (result, 0.0)  # rewind the expiry to the distant past

    response = save(fetch_client, token)

    assert response.status_code == 410
    assert "expired or was never issued" in response.json()["detail"]


def test_the_save_request_carries_a_token_and_no_weather_values(
    fetch_client, registered_pan
):
    """The contract itself blocks a typed number from reaching the store."""
    token = fetch(fetch_client, registered_pan, past_date()).json()["fetch_token"]

    response = fetch_client.post(
        "/api/weather/fetch/save",
        json={
            "fetch_token": token,
            "rainfall_mm": 999.9,
            "source": "manual",
            "ambient_temperature_c": -40.0,
        },
    )

    assert response.status_code == 200, response.text
    record = response.json()["record"]
    assert record["rainfall_mm"] == pytest.approx(4.2)
    assert record["ambient_temperature_c"] == pytest.approx(26.5)
    assert record["source"] == "weather_api"


# -- manual entry stays manual --------------------------------------------


def test_manual_entry_still_stores_hand_entered_records(
    fetch_client, registered_pan
):
    response = fetch_client.post(
        "/api/weather/",
        json={
            "pan_id": registered_pan,
            "timestamp": "2026-03-14T06:30:00Z",
            "rainfall_mm": 12.4,
            "source": "manual",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["source"] == "manual"
    assert response.json()["provider"] is None


def test_manual_entry_still_refuses_to_claim_weather_api(
    fetch_client, registered_pan
):
    response = fetch_client.post(
        "/api/weather/",
        json={
            "pan_id": registered_pan,
            "timestamp": "2026-03-14T06:30:00Z",
            "rainfall_mm": 12.4,
            "source": "weather_api",
        },
    )

    assert response.status_code == 422
    assert "weather_api" in response.text
