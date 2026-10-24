"""Module 4 - weather data API tests.

These run against in-memory stand-ins for ``weather_observations`` and
``salt_pans``, so the whole request/response contract is exercised without a
live MongoDB.

The behaviours worth locking down here are the ones that keep the record
trustworthy. A hand-entered reading must stay labelled as hand-entered forever.
No request body may claim an automated origin. A provider that is not connected
must produce no data at all rather than a plausible-looking placeholder. And an
unmeasured value must stay null, with a forecast of zero kept distinct from a
missing one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.observations import get_observation_service
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.weather import (
    get_provider_registry,
    get_weather_service,
    get_weather_status_service,
)
from app.main import app
from app.models.enums import WeatherSource
from app.services.observations import ObservationService
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
    "pan_id": "KZN-2026-A01",
    "pan_name": "Northern Evaporation Pan",
    "location": "Port Shepstone",
    "area_m2": 12_500.0,
    "normal_depth_cm": 15.5,
    "target_harvest_salinity_g_l": 285.0,
    "latitude": -30.74,
    "longitude": 30.81,
}

VALID_WEATHER = {
    "pan_id": "kzn-2026-a01",
    "timestamp": "2026-03-14T06:30:00Z",
    "rainfall_mm": 12.4,
    "rainfall_probability_percent": 70.0,
    "ambient_temperature_c": 24.5,
    "humidity_percent": 82.0,
    "source": "manual",
}


def hours_from_now(count: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=count)).isoformat()


class FakeProvider:
    """A provider implementation, so the seam is proven rather than asserted."""

    def __init__(
        self,
        name: str = "testweather",
        *,
        points: list[ProviderForecastPoint] | None = None,
        available: bool = True,
        source: WeatherSource = WeatherSource.WEATHER_API,
        raises: Exception | None = None,
    ) -> None:
        self._name = name
        self._points = points or []
        self._available = available
        self._source = source
        self._raises = raises
        self.calls: list[tuple[Any, ForecastWindow]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def source(self) -> WeatherSource:
        return self._source

    async def available(self) -> bool:
        return self._available

    async def fetch_forecast(
        self, pan: Any, window: ForecastWindow
    ) -> list[ProviderForecastPoint]:
        self.calls.append((pan, window))
        if self._raises is not None:
            raise self._raises
        return self._points


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def registry() -> WeatherProviderRegistry:
    """An isolated, empty registry. The deployment registry really is empty."""
    return WeatherProviderRegistry()


@pytest.fixture()
def weather_client(
    database: FakeDatabase, registry: WeatherProviderRegistry
) -> Any:
    """A TestClient whose weather, pan and observation routes are backed by memory."""
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_observation_service] = lambda: ObservationService(
        database
    )
    app.dependency_overrides[get_weather_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_weather_status_service] = lambda: WeatherService(
        database
    )
    app.dependency_overrides[get_provider_registry] = lambda: registry
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def registered_pan(weather_client: TestClient) -> str:
    response = weather_client.post("/api/pans/", json=VALID_PAN)
    assert response.status_code == 201, response.text
    return response.json()["pan_id"]


def record(client: TestClient, **overrides: Any) -> Any:
    return client.post("/api/weather/", json={**VALID_WEATHER, **overrides})


def forecast_point(**overrides: Any) -> ProviderForecastPoint:
    values = {
        "forecast_for": datetime.now(timezone.utc) + timedelta(hours=12),
        "rainfall_mm": 8.0,
        "rainfall_probability_percent": 55.0,
        "ambient_temperature_c": 22.0,
        "humidity_percent": 70.0,
    }
    values.update(overrides)
    return ProviderForecastPoint(**values)


# -- recording ------------------------------------------------------------


def test_record_weather_stores_every_supplied_field(weather_client, registered_pan):
    body = record(weather_client).json()

    assert body["weather_id"]
    assert body["pan_id"] == registered_pan
    assert body["timestamp"] == "2026-03-14T06:30:00Z"
    assert body["rainfall_mm"] == pytest.approx(12.4)
    assert body["rainfall_probability_percent"] == pytest.approx(70.0)
    assert body["ambient_temperature_c"] == pytest.approx(24.5)
    assert body["humidity_percent"] == pytest.approx(82.0)
    assert body["source"] == "manual"
    assert body["is_forecast"] is False
    assert body["forecast_for"] is None


def test_record_weather_returns_201(weather_client, registered_pan):
    assert record(weather_client).status_code == 201


def test_weather_identifier_is_readable_back(weather_client, registered_pan):
    created = record(weather_client).json()

    response = weather_client.get(f"/api/weather/{created['weather_id']}")
    assert response.status_code == 200
    assert response.json() == created


def test_every_record_is_stored_and_nothing_is_overwritten(weather_client, registered_pan):
    """Weather history is append-only: later entries never replace earlier ones."""
    first = record(weather_client, timestamp="2026-03-10T06:00:00Z", rainfall_mm=2.0).json()
    second = record(weather_client, timestamp="2026-03-11T06:00:00Z", rainfall_mm=4.0).json()

    assert first["weather_id"] != second["weather_id"]
    history = weather_client.get("/api/weather/").json()
    assert history["total"] == 2
    assert weather_client.get(f"/api/weather/{first['weather_id']}").json()["rainfall_mm"] == pytest.approx(2.0)


def test_record_without_a_timestamp_is_stamped_at_save_time(weather_client, registered_pan):
    before = datetime.now(timezone.utc)
    body = record(weather_client, timestamp=None).json()
    after = datetime.now(timezone.utc)

    stored = datetime.fromisoformat(body["timestamp"])
    assert before <= stored <= after


def test_backdated_entry_is_preserved_exactly(weather_client, registered_pan):
    """Backfilling last week is normal and must not shift the recorded instant."""
    body = record(weather_client, timestamp="2025-12-30T04:15:00Z").json()

    assert body["timestamp"] == "2025-12-30T04:15:00Z"
    assert weather_client.get(f"/api/weather/{body['weather_id']}").json()["timestamp"] == "2025-12-30T04:15:00Z"


# -- absence stays absence ------------------------------------------------


def test_unmeasured_fields_are_stored_as_null(weather_client, registered_pan):
    body = record(
        weather_client,
        rainfall_probability_percent=None,
        ambient_temperature_c=None,
        humidity_percent=None,
    ).json()

    assert body["rainfall_mm"] == pytest.approx(12.4)
    assert body["rainfall_probability_percent"] is None
    assert body["ambient_temperature_c"] is None
    assert body["humidity_percent"] is None


def test_a_forecast_of_zero_rain_is_not_the_same_as_a_missing_forecast(
    weather_client, registered_pan
):
    """Zero is a real prediction of no rain; null means nobody said."""
    dry = record(
        weather_client,
        forecast_for=hours_from_now(6),
        rainfall_mm=0.0,
        rainfall_probability_percent=None,
        ambient_temperature_c=None,
        humidity_percent=None,
    ).json()
    unknown = record(
        weather_client,
        forecast_for=hours_from_now(6),
        rainfall_mm=None,
        rainfall_probability_percent=None,
        ambient_temperature_c=None,
        humidity_percent=55.0,
    ).json()

    assert dry["rainfall_mm"] == 0.0
    assert dry["is_forecast"] is True
    assert unknown["rainfall_mm"] is None
    assert unknown["humidity_percent"] == pytest.approx(55.0)


def test_a_record_with_no_values_at_all_is_refused(weather_client, registered_pan):
    response = record(
        weather_client,
        rainfall_mm=None,
        rainfall_probability_percent=None,
        ambient_temperature_c=None,
        humidity_percent=None,
    )
    assert response.status_code == 422
    assert "at least one" in response.text


def test_a_single_value_is_enough(weather_client, registered_pan):
    response = weather_client.post(
        "/api/weather/",
        json={
            "pan_id": registered_pan,
            "timestamp": "2026-03-14T06:30:00Z",
            "rainfall_probability_percent": 80.0,
        },
    )
    assert response.status_code == 201
    assert response.json()["rainfall_probability_percent"] == pytest.approx(80.0)


# -- provenance -----------------------------------------------------------


def test_a_hand_entered_record_is_never_labelled_as_weather_api(
    weather_client, registered_pan
):
    """The core integrity rule: manual entry cannot claim an automated origin."""
    response = record(weather_client, source="weather_api")

    assert response.status_code == 422
    assert "weather_api" in response.text
    assert weather_client.get("/api/weather/").json()["total"] == 0


def test_the_api_source_refusal_explains_where_that_value_may_come_from(
    weather_client, registered_pan
):
    errors = record(weather_client, source="weather_api").json()["detail"]
    assert any(error["loc"][-1] == "source" for error in errors)
    message = " ".join(error["msg"] for error in errors).lower()
    assert "ingest" in message
    assert "manual" in message


def test_a_forecast_ingest_is_the_only_way_to_store_weather_api(
    weather_client, registered_pan, registry
):
    """Manual entry refuses this provenance; the ingest route is what writes it."""
    assert record(weather_client, source="weather_api").status_code == 422

    provider = FakeProvider(points=[forecast_point()])
    registry.register(provider)
    response = weather_client.post(
        f"/api/weather/ingest/{provider.name}", params={"pan_id": registered_pan}
    )

    assert response.status_code == 201
    assert response.json()["observations"][0]["source"] == "weather_api"


def test_manual_source_is_the_default(weather_client, registered_pan):
    response = weather_client.post(
        "/api/weather/", json={"pan_id": registered_pan, "rainfall_mm": 3.0}
    )
    assert response.json()["source"] == "manual"


def test_sensor_source_is_refused_with_409(weather_client, registered_pan):
    """No instrument is connected, so no reading may be attributed to one."""
    response = record(weather_client, source="sensor")

    assert response.status_code == 409
    assert "sensor" in response.text.lower()
    assert weather_client.get("/api/weather/").json()["total"] == 0


def test_the_sensor_refusal_does_not_need_a_database():
    """The provenance guard runs before the pan lookup, so it answers on its own."""
    with TestClient(app) as test_client:
        response = test_client.post("/api/weather/", json={**VALID_WEATHER, "source": "sensor"})
    assert response.status_code == 409


def test_source_survives_a_round_trip(weather_client, registered_pan):
    created = record(weather_client).json()
    history = weather_client.get("/api/weather/").json()["items"]

    assert history[0]["source"] == "manual"
    assert history[0]["weather_id"] == created["weather_id"]


# -- validation -----------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rainfall_mm", -0.1),
        ("rainfall_mm", 5_000),
        ("rainfall_probability_percent", -1),
        ("rainfall_probability_percent", 101),
        ("ambient_temperature_c", 200),
        ("humidity_percent", 101),
    ],
)
def test_out_of_range_values_are_rejected(weather_client, registered_pan, field, value):
    assert record(weather_client, **{field: value}).status_code == 422


@pytest.mark.parametrize("value", [-3.5, -50, 0, 24, 80])
def test_plausible_temperatures_are_accepted(weather_client, registered_pan, value):
    response = record(weather_client, ambient_temperature_c=value)
    assert response.status_code == 201
    assert response.json()["ambient_temperature_c"] == pytest.approx(value)


@pytest.mark.parametrize("value", [0, 0.0, 100, 100.0])
def test_probability_boundaries_are_accepted(weather_client, registered_pan, value):
    assert record(weather_client, rainfall_probability_percent=value).status_code == 201


def test_a_future_observation_timestamp_is_rejected(weather_client, registered_pan):
    response = record(weather_client, timestamp=hours_from_now(48))
    assert response.status_code == 422
    assert "future" in response.text


def test_small_clock_skew_is_tolerated(weather_client, registered_pan):
    response = record(weather_client, timestamp=hours_from_now(0.02))
    assert response.status_code == 201


def test_a_forecast_for_the_past_is_rejected(weather_client, registered_pan):
    response = record(
        weather_client, timestamp=hours_from_now(-2), forecast_for=hours_from_now(-6)
    )
    assert response.status_code == 422
    assert "forecast_for" in response.text


def test_a_forecast_far_beyond_any_range_is_rejected(weather_client, registered_pan):
    response = record(weather_client, forecast_for=hours_from_now(24 * 400))
    assert response.status_code == 422


def test_an_unknown_pan_is_reported_as_404(weather_client):
    response = record(weather_client, pan_id="does-not-exist")
    assert response.status_code == 404
    assert "does-not-exist" in response.text


def test_an_unknown_field_is_rejected(weather_client, registered_pan):
    assert record(weather_client, wind_speed_kph=12).status_code == 422


def test_pan_id_is_matched_case_insensitively(weather_client, registered_pan):
    body = record(weather_client, pan_id=registered_pan.upper()).json()
    assert body["pan_id"] == registered_pan


# -- history --------------------------------------------------------------


def test_history_is_returned_newest_first(weather_client, registered_pan):
    record(weather_client, timestamp="2026-03-10T06:00:00Z")
    record(weather_client, timestamp="2026-03-12T06:00:00Z")
    record(weather_client, timestamp="2026-03-11T06:00:00Z")

    stamps = [item["timestamp"] for item in weather_client.get("/api/weather/").json()["items"]]
    assert stamps == [
        "2026-03-12T06:00:00Z",
        "2026-03-11T06:00:00Z",
        "2026-03-10T06:00:00Z",
    ]


def test_history_is_filtered_by_pan(weather_client, registered_pan):
    weather_client.post(
        "/api/pans/",
        json={**VALID_PAN, "pan_id": "KZN-2026-B02", "pan_name": "Southern Pan"},
    )
    record(weather_client, timestamp="2026-03-10T06:00:00Z", rainfall_mm=1.0)
    record(weather_client, timestamp="2026-03-10T06:00:00Z", rainfall_mm=2.0, pan_id="kzn-2026-b02")

    history = weather_client.get("/api/weather/", params={"pan_id": registered_pan}).json()
    assert history["total"] == 1
    assert history["items"][0]["rainfall_mm"] == pytest.approx(1.0)


def test_history_is_filtered_by_window(weather_client, registered_pan):
    record(weather_client, timestamp="2026-03-10T06:00:00Z")
    record(weather_client, timestamp="2026-03-12T06:00:00Z")
    record(weather_client, timestamp="2026-03-14T06:00:00Z")

    history = weather_client.get(
        "/api/weather/",
        params={"start": "2026-03-11T00:00:00Z", "end": "2026-03-13T00:00:00Z"},
    ).json()
    assert history["total"] == 1
    assert history["items"][0]["timestamp"] == "2026-03-12T06:00:00Z"


def test_history_reports_the_true_total_even_when_paged(weather_client, registered_pan):
    for day in range(1, 6):
        record(weather_client, timestamp=f"2026-03-0{day}T06:00:00Z")

    page = weather_client.get("/api/weather/", params={"limit": 2, "offset": 0}).json()
    assert len(page["items"]) == 2
    assert page["total"] == 5
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert page["earliest_timestamp"] < page["latest_timestamp"]


def test_an_inverted_window_is_rejected(weather_client):
    response = weather_client.get(
        "/api/weather/",
        params={"start": "2026-03-14T00:00:00Z", "end": "2026-03-01T00:00:00Z"},
    )
    assert response.status_code == 422


def test_forecasts_and_observations_share_one_history(weather_client, registered_pan):
    record(weather_client, timestamp="2026-03-14T06:00:00Z", rainfall_mm=3.0)
    record(
        weather_client,
        timestamp=hours_from_now(0),
        forecast_for=hours_from_now(8),
        rainfall_mm=9.0,
    )

    items = weather_client.get("/api/weather/").json()["items"]
    kinds = {item["is_forecast"] for item in items}
    assert kinds == {True, False}
    assert all(item["source"] == "manual" for item in items)


def test_an_empty_history_is_an_empty_list_not_an_error(weather_client):
    history = weather_client.get("/api/weather/").json()
    assert history == {
        "items": [],
        "total": 0,
        "limit": 200,
        "offset": 0,
        "earliest_timestamp": None,
        "latest_timestamp": None,
    }


def test_an_unknown_record_id_is_reported_as_404(weather_client):
    response = weather_client.get("/api/weather/wx-does-not-exist")
    assert response.status_code == 404
    assert "wx-does-not-exist" in response.text


# -- forecast providers ---------------------------------------------------


def test_the_empty_registry_reports_that_no_weather_api_is_connected(weather_client):
    body = weather_client.get("/api/weather/providers").json()

    assert body["registered"] == []
    assert body["count"] == 0
    assert body["manual_entry_available"] is True
    assert "No weather API is connected" in body["detail"]


def test_ingesting_from_an_unregistered_provider_creates_nothing(
    weather_client, registered_pan
):
    response = weather_client.post(
        "/api/weather/ingest/openweather", params={"pan_id": registered_pan}
    )

    assert response.status_code == 404
    assert "openweather" in response.text
    assert weather_client.get("/api/weather/").json()["total"] == 0


def test_a_registered_provider_lets_a_forecast_be_ingested_without_any_other_change(
    weather_client, registered_pan, registry
):
    """Adding a provider is additive: no schema, route or service change needed."""
    provider = FakeProvider(points=[forecast_point(), forecast_point()])
    registry.register(provider)

    body = weather_client.post(
        f"/api/weather/ingest/{provider.name}", params={"pan_id": registered_pan}
    ).json()

    assert body["provider"] == "testweather"
    assert body["source"] == "weather_api"
    assert body["stored"] == 2
    assert body["rejected"] == 0
    assert len(body["observations"]) == 2
    assert all(item["source"] == "weather_api" for item in body["observations"])
    assert all(item["is_forecast"] is True for item in body["observations"])


def test_an_ingested_forecast_is_persisted_with_api_provenance(
    weather_client, registered_pan, registry
):
    provider = FakeProvider(points=[forecast_point(rainfall_mm=14.5)])
    registry.register(provider)

    weather_client.post(f"/api/weather/ingest/{provider.name}?pan_id={registered_pan}")

    history = weather_client.get("/api/weather/").json()
    assert history["total"] == 1
    assert history["items"][0]["rainfall_mm"] == pytest.approx(14.5)
    assert history["items"][0]["source"] == "weather_api"


def test_a_provider_is_handed_the_pan_it_was_asked_about(
    weather_client, registered_pan, registry
):
    provider = FakeProvider(points=[forecast_point()])
    registry.register(provider)

    weather_client.post(
        f"/api/weather/ingest/{provider.name}",
        params={"pan_id": registered_pan, "start": hours_from_now(0), "end": hours_from_now(24)},
    )

    pan, window = provider.calls[0]
    assert pan.pan_id == registered_pan
    assert window.end > window.start


def test_an_unavailable_provider_is_reported_as_503(
    weather_client, registered_pan, registry
):
    provider = FakeProvider(points=[forecast_point()], available=False)
    registry.register(provider)

    response = weather_client.post(
        f"/api/weather/ingest/{provider.name}", params={"pan_id": registered_pan}
    )

    assert response.status_code == 503
    assert provider.calls == []
    assert weather_client.get("/api/weather/").json()["total"] == 0


def test_a_provider_fault_is_reported_as_502_not_as_missing_data(
    weather_client, registered_pan, registry
):
    provider = FakeProvider(raises=WeatherProviderError("upstream timeout"))
    registry.register(provider)

    response = weather_client.post(
        f"/api/weather/ingest/{provider.name}", params={"pan_id": registered_pan}
    )

    assert response.status_code == 502
    assert "upstream timeout" in response.text


def test_points_that_cannot_be_stored_are_counted_not_hidden(
    weather_client, registered_pan, registry
):
    """A provider aiming a forecast at a past instant must not vanish silently."""
    provider = FakeProvider(
        points=[
            forecast_point(),
            forecast_point(forecast_for=datetime.now(timezone.utc) - timedelta(hours=6)),
        ]
    )
    registry.register(provider)

    body = weather_client.post(
        f"/api/weather/ingest/{provider.name}", params={"pan_id": registered_pan}
    ).json()

    assert body["stored"] == 1
    assert body["rejected"] == 1
    assert len(body["observations"]) == 1


def test_an_empty_provider_result_is_reported_honestly(
    weather_client, registered_pan, registry
):
    provider = FakeProvider(points=[])
    registry.register(provider)

    body = weather_client.post(
        f"/api/weather/ingest/{provider.name}", params={"pan_id": registered_pan}
    ).json()

    assert body["stored"] == 0
    assert body["rejected"] == 0
    assert body["observations"] == []


def test_ingesting_for_an_unregistered_pan_is_reported_as_404(
    weather_client, registry
):
    provider = FakeProvider(points=[forecast_point()])
    registry.register(provider)

    response = weather_client.post(
        f"/api/weather/ingest/{provider.name}", params={"pan_id": "does-not-exist"}
    )

    assert response.status_code == 404
    assert provider.calls == [], "an unknown pan must not trigger a paid API call"
    assert weather_client.get("/api/weather/").json()["total"] == 0


def test_a_registered_provider_appears_in_the_providers_list(weather_client, registry):
    registry.register(FakeProvider(name="metoffice"))

    body = weather_client.get("/api/weather/providers").json()
    assert [item["name"] for item in body["registered"]] == ["metoffice"]
    assert body["registered"][0]["source"] == "weather_api"
    assert body["count"] == 1


def test_a_provider_that_claims_the_wrong_provenance_cannot_be_registered(registry):
    """A provider stamping readings as manual or sensor is refused outright."""
    with pytest.raises(ValueError, match="weather_api"):
        registry.register(FakeProvider(source=WeatherSource.MANUAL))
    assert len(registry) == 0


def test_two_providers_cannot_share_a_name(registry):
    registry.register(FakeProvider(name="metoffice"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeProvider(name="metoffice"))


def test_the_forecast_window_defaults_to_the_next_two_days(registry):
    from app.api.routers.weather import DEFAULT_FORECAST_HORIZON, _resolve_window

    window = _resolve_window(None, None)
    assert window.end - window.start == DEFAULT_FORECAST_HORIZON
    assert DEFAULT_FORECAST_HORIZON == timedelta(days=2)


def test_an_inverted_forecast_window_is_rejected(weather_client, registry):
    response = weather_client.post(
        "/api/weather/ingest/metoffice",
        params={"start": hours_from_now(24), "end": hours_from_now(1)},
    )
    assert response.status_code == 422


# -- module contract ------------------------------------------------------


def test_module_status_reports_module_four_as_implemented(weather_client):
    body = weather_client.get("/api/weather/status").json()
    assert body["module"] == "weather"
    assert body["module_number"] == 4
    assert body["status"] == "implemented"
    assert body["manual_entry"]["available"] is True


def test_module_status_is_honest_about_the_connected_api(weather_client):
    """Open-Meteo is registered at startup, so status must say so.

    The test's job is that the status is a real reading of the deployment's
    registry rather than a hard-coded claim either way: sensors are still
    absent, and the weather API is reported exactly as it is wired.
    """
    body = weather_client.get("/api/weather/status").json()
    registered = body["weather_api"]["registered_providers"]
    assert body["weather_api"]["available"] is (len(registered) > 0)
    assert "open-meteo" in registered
    assert body["sensor_ingest"]["available"] is False


def test_module_status_reports_live_record_counts(weather_client, registered_pan):
    record(weather_client)
    record(weather_client)

    assert weather_client.get("/api/weather/status").json()["weather"] == {
        "available": True,
        "total_weather_records": 2,
    }


def test_module_status_reports_an_unavailable_database_without_failing():
    """A status endpoint must not 503: reporting unavailability is its job."""
    with TestClient(app) as test_client:
        response = test_client.get("/api/weather/status")
    assert response.status_code == 200
    weather = response.json()["weather"]
    assert weather["available"] is False
    assert "MONGODB_URI" in weather["detail"]


def test_the_providers_listing_works_without_a_database():
    """Which APIs are connected is a deployment fact, not a stored one."""
    with TestClient(app) as test_client:
        response = test_client.get("/api/weather/providers")
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["registered"]]
    assert "open-meteo" in names


def test_invalid_payload_is_reported_as_422_even_without_a_database():
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/weather/", json={**VALID_WEATHER, "humidity_percent": 500}
        )
    assert response.status_code == 422


def test_persistence_endpoints_return_503_without_a_database():
    """Without MONGODB_URI the module must fail loudly rather than fake weather."""
    with TestClient(app) as test_client:
        assert test_client.get("/api/weather/").status_code == 503
        assert test_client.post("/api/weather/", json=VALID_WEATHER).status_code == 503
        assert test_client.get("/api/weather/wx-1").status_code == 503


def test_weather_routes_appear_in_openapi(weather_client):
    paths = weather_client.get("/openapi.json").json()["paths"]
    assert set(paths["/api/weather/"]) == {"get", "post"}
    assert "get" in paths["/api/weather/{weather_id}"]
    assert "get" in paths["/api/weather/providers"]
    assert "post" in paths["/api/weather/ingest/{provider_name}"]


def test_the_create_contract_documents_every_source(weather_client):
    """The enum in the contract is the whole vocabulary, including demonstration.

    Worth asserting exhaustively rather than by presence: an operator reading the
    OpenAPI document has to be able to see that ``demonstration`` is a source
    SaltLens knows about, and that ``weather_api`` is the one route the typing
    endpoint refuses.
    """
    schema = weather_client.get("/openapi.json").json()
    source = schema["components"]["schemas"]["WeatherObservationCreate"]["properties"]["source"]
    assert source["$ref"].endswith("/WeatherSource")
    enum = schema["components"]["schemas"]["WeatherSource"]["enum"]
    assert set(enum) == {"manual", "sensor", "weather_api", "demonstration"}


def test_whitespace_around_a_pan_id_is_trimmed(weather_client, registered_pan):
    response = record(weather_client, pan_id=f"  {registered_pan}  ")
    assert response.status_code == 201
    assert response.json()["pan_id"] == registered_pan
