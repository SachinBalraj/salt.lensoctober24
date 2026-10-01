"""Module 5 - digital-twin API tests.

These run against in-memory stand-ins for ``salt_pans``, ``pan_observations``,
``weather_observations`` and ``digital_twin_states``, so the whole
request/response contract is exercised without a live MongoDB.

The behaviours worth locking down are the ones that keep the twin honest. It
must select the newest record by the instant it describes rather than by the
order it was typed. It must never blend an older reading into a newer one to
fill a hole. It must keep a forecast out of the observed quantities and an
observation out of the forecast quantities. And when it cannot fill something,
it has to say so rather than substituting a plausible number.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.digital_twin import (
    get_digital_twin_service,
    get_digital_twin_status_service,
)
from app.api.routers.observations import get_observation_service
from app.api.routers.pans import get_salt_pan_service
from app.api.routers.weather import get_weather_service
from app.main import app
from app.services.digital_twin import DigitalTwinService
from app.services.observations import ObservationService
from app.services.salt_pans import SaltPanService
from app.services.weather import WeatherService
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


def at(days: float, hours: float = 0.0) -> str:
    """An ISO instant relative to now, for backdating and ordering."""
    moment = datetime.now(timezone.utc) + timedelta(days=days, hours=hours)
    return moment.isoformat()


# -- selection of the newest valid inputs -------------------------------


def test_the_twin_uses_the_newest_observation_by_the_instant_it_describes(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-3), salinity_g_l=100.0)
    record(twin_client, timestamp=at(-2), salinity_g_l=200.0)
    record(twin_client, timestamp=at(-1), salinity_g_l=300.0)

    state = twin_state(twin_client)

    assert state["salinity_g_l"] == 300.0
    assert state["observation"]["timestamp"].startswith(at(-1)[:10])


def test_a_backfilled_older_reading_does_not_become_the_current_state(
    twin_client: TestClient, registered_pan: str
) -> None:
    """A reading entered today but describing last week must not win."""
    record(twin_client, timestamp=at(-1), salinity_g_l=300.0)
    record(twin_client, timestamp=at(-30), salinity_g_l=42.0)

    assert twin_state(twin_client)["salinity_g_l"] == 300.0


def test_the_newest_observation_is_selected_regardless_of_insertion_order(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-2), salinity_g_l=200.0)
    record(twin_client, timestamp=at(-1), salinity_g_l=300.0)
    record(twin_client, timestamp=at(-3), salinity_g_l=100.0)

    assert twin_state(twin_client)["salinity_g_l"] == 300.0


def test_a_forecast_never_supplies_an_observed_quantity(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-1), salinity_g_l=300.0)
    forecast(twin_client, timestamp=at(0), forecast_for=at(0, 6), rainfall_mm=40.0)

    state = twin_state(twin_client)

    assert state["salinity_g_l"] == 300.0
    # The forecast's rainfall belongs to the forecast slot, not to the pan's
    # observed condition.
    assert state["forecast_rainfall_mm"] == 40.0


def test_a_forecast_alone_does_not_populate_observed_conditions(
    twin_client: TestClient, registered_pan: str
) -> None:
    forecast(
        twin_client,
        timestamp=at(0),
        forecast_for=at(0, 6),
        rainfall_mm=12.0,
        ambient_temperature_c=19.0,
        humidity_percent=55.0,
    )

    state = twin_state(twin_client)

    assert state["forecast_rainfall_mm"] == 12.0
    # Ambient conditions are only taken from a record that reports what
    # happened. A prediction is not evidence about the present.
    assert state["ambient_temperature_c"] is None
    assert state["humidity_percent"] is None


def test_an_observed_rainfall_figure_does_not_become_a_forecast(
    twin_client: TestClient, registered_pan: str
) -> None:
    weather(twin_client, timestamp=at(-1), rainfall_mm=7.5)

    state = twin_state(twin_client)

    assert state["forecast_rainfall_mm"] is None
    assert "forecast_rainfall_mm" in state["missing_fields"]


def test_the_newest_observed_weather_supplies_ambient_conditions(
    twin_client: TestClient, registered_pan: str
) -> None:
    weather(twin_client, timestamp=at(-2), ambient_temperature_c=20.0, humidity_percent=50.0)
    weather(twin_client, timestamp=at(-1), ambient_temperature_c=27.5, humidity_percent=61.0)

    state = twin_state(twin_client)

    assert state["ambient_temperature_c"] == 27.5
    assert state["humidity_percent"] == 61.0
    assert state["weather"]["source"] == "manual"


def test_the_newest_forecast_supplies_the_forecast_quantities(
    twin_client: TestClient, registered_pan: str
) -> None:
    forecast(
        twin_client, timestamp=at(-2), forecast_for=at(0, 3), rainfall_mm=5.0,
        rainfall_probability_percent=30.0,
    )
    forecast(
        twin_client, timestamp=at(-1), forecast_for=at(0, 9), rainfall_mm=18.0,
        rainfall_probability_percent=80.0,
    )

    state = twin_state(twin_client)

    assert state["forecast_rainfall_mm"] == 18.0
    assert state["rainfall_probability_percent"] == 80.0


def test_observations_of_another_pan_are_never_used(
    twin_client: TestClient, registered_pan: str
) -> None:
    other = twin_client.post("/api/pans/", json={**VALID_PAN, "pan_id": "KZN-2026-B02"})
    assert other.status_code == 201, other.text

    record(twin_client, pan_id="kzn-2026-b02", timestamp=at(-1), salinity_g_l=999.0)
    record(twin_client, pan_id=registered_pan, timestamp=at(-2), salinity_g_l=150.0)

    assert twin_state(twin_client)["salinity_g_l"] == 150.0


# -- the twin must not invent -------------------------------------------


def test_a_pan_with_nothing_recorded_yields_a_twin_of_gaps(
    twin_client: TestClient, registered_pan: str
) -> None:
    state = twin_state(twin_client)

    assert state["is_complete"] is False
    assert state["observation"] is None
    assert state["weather"] is None
    assert state["weather_forecast"] is None
    assert state["salinity_g_l"] is None
    assert set(state["missing_fields"]) == {
        "salinity_g_l",
        "water_depth_cm",
        "brine_temperature_c",
        "ambient_temperature_c",
        "humidity_percent",
        "forecast_rainfall_mm",
        "rainfall_probability_percent",
    }


def test_a_partial_reading_is_not_completed_from_an_older_one(
    twin_client: TestClient, registered_pan: str
) -> None:
    """The newest reading governs, even when it omits what the older one had."""
    record(
        twin_client,
        timestamp=at(-2),
        salinity_g_l=250.0,
        water_depth_cm=12.0,
        brine_temperature_c=30.0,
    )
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)

    state = twin_state(twin_client)

    assert state["salinity_g_l"] == 260.0
    # The older depth and temperature are deliberately not carried forward: a
    # stale figure presented as current is a fabrication.
    assert state["water_depth_cm"] is None
    assert state["brine_temperature_c"] is None
    assert set(state["missing_fields"]) >= {"water_depth_cm", "brine_temperature_c"}


def test_every_gap_names_the_record_that_would_fill_it(
    twin_client: TestClient, registered_pan: str
) -> None:
    state = twin_state(twin_client)

    reasons = {entry["field"]: entry for entry in state["missing_inputs"]}
    assert reasons["salinity_g_l"]["expected_from"] == "observation"
    assert reasons["forecast_rainfall_mm"]["expected_from"] == "weather_forecast"
    assert "no pan observation has been recorded" in reasons["salinity_g_l"]["reason"].lower()
    assert "no weather forecast has been recorded" in reasons["forecast_rainfall_mm"]["reason"].lower()


def test_a_record_that_omits_a_figure_says_so_rather_than_saying_it_is_missing_entirely(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)

    reasons = {entry["field"]: entry for entry in twin_state(twin_client)["missing_inputs"]}

    assert "did not include water depth" in reasons["water_depth_cm"]["reason"]


def test_a_measured_zero_is_kept_distinct_from_an_unmeasured_value(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(
        twin_client,
        timestamp=at(-1),
        salinity_g_l=0.0,
        water_depth_cm=0.0,
        brine_temperature_c=0.0,
    )
    weather(
        twin_client, timestamp=at(-1), ambient_temperature_c=0.0, humidity_percent=0.0
    )
    forecast(
        twin_client,
        timestamp=at(-1),
        forecast_for=at(0, 6),
        rainfall_mm=0.0,
        rainfall_probability_percent=0.0,
    )

    state = twin_state(twin_client)

    assert state["salinity_g_l"] == 0.0
    assert state["water_depth_cm"] == 0.0
    assert state["brine_temperature_c"] == 0.0
    assert state["ambient_temperature_c"] == 0.0
    assert state["humidity_percent"] == 0.0
    assert state["forecast_rainfall_mm"] == 0.0
    assert state["rainfall_probability_percent"] == 0.0
    assert state["missing_fields"] == []
    assert state["is_complete"] is True


def test_the_observation_supplies_ambient_conditions_when_no_weather_exists(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(
        twin_client, timestamp=at(-1), salinity_g_l=260.0,
        ambient_temperature_c=28.0, humidity_percent=64.0,
    )

    state = twin_state(twin_client)

    assert state["ambient_temperature_c"] == 28.0
    assert state["humidity_percent"] == 64.0
    assert state["weather"] is None


def test_weather_wins_over_the_observation_for_ambient_conditions(
    twin_client: TestClient, registered_pan: str
) -> None:
    """Weather is the record of account for ambient conditions."""
    record(twin_client, timestamp=at(-1), ambient_temperature_c=15.0, humidity_percent=30.0)
    weather(twin_client, timestamp=at(-1), ambient_temperature_c=26.0, humidity_percent=70.0)

    state = twin_state(twin_client)

    assert state["ambient_temperature_c"] == 26.0
    assert state["humidity_percent"] == 70.0


def test_static_properties_come_from_the_registration(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)

    state = twin_state(twin_client)

    assert state["pan_area_m2"] == 12_500.0
    assert state["location"] == "Port Shepstone"
    assert state["pan_name"] == "Northern Evaporation Pan"


# -- provenance and age --------------------------------------------------


def test_the_twin_reports_the_source_of_every_input(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)
    weather(twin_client, timestamp=at(-1), ambient_temperature_c=26.0)
    forecast(twin_client, timestamp=at(0), forecast_for=at(0, 6), rainfall_mm=3.0)

    state = twin_state(twin_client)

    assert state["observation"]["source"] == "manual"
    assert state["weather"]["source"] == "manual"
    assert state["weather_forecast"]["source"] == "manual"


def test_the_twin_reports_the_age_of_each_input(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-2), salinity_g_l=260.0)
    weather(twin_client, timestamp=at(-1), ambient_temperature_c=26.0)

    state = twin_state(twin_client)

    # Two days and one day, give or take the seconds spent running the test.
    assert 170_000 < state["observation"]["age_seconds"] < 176_000
    assert 85_000 < state["weather"]["age_seconds"] < 90_000


def test_an_absent_input_reports_no_age_rather_than_zero(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)

    state = twin_state(twin_client)

    assert state["observation"]["age_seconds"] is not None
    assert state["weather"] is None


def test_the_assembly_time_is_distinct_from_the_age_of_the_evidence(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-5), salinity_g_l=260.0)

    state = twin_state(twin_client)

    assembled = datetime.fromisoformat(state["timestamp"].replace("Z", "+00:00"))
    observed = datetime.fromisoformat(
        state["observation"]["timestamp"].replace("Z", "+00:00")
    )
    assert assembled > observed


# -- guards --------------------------------------------------------------


def test_an_unregistered_pan_is_reported_as_not_found(
    twin_client: TestClient, registered_pan: str
) -> None:
    response = twin_client.get("/api/twin/does-not-exist/state")

    assert response.status_code == 404
    assert "no registered pan" in response.json()["detail"].lower()


def test_the_pan_id_is_matched_case_insensitively(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)

    response = twin_client.get("/api/twin/KZN-2026-A01/state")

    assert response.status_code == 200
    assert response.json()["pan_id"] == "kzn-2026-a01"


def test_a_blank_pan_id_is_refused(
    twin_client: TestClient, registered_pan: str
) -> None:
    assert twin_client.get("/api/twin/%20/state").status_code in (404, 422)


# -- snapshots -----------------------------------------------------------


def test_reading_the_twin_stores_nothing(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)

    assert twin_client.get(f"/api/twin/{registered_pan}/state").status_code == 200

    history = twin_client.get(f"/api/twin/{registered_pan}/history").json()
    assert history["total"] == 0


def test_a_refresh_stores_a_snapshot(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)

    response = twin_client.post(f"/api/twin/{registered_pan}/refresh")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["stored"] is True
    assert body["previous_twin_id"] is None
    assert body["state"]["salinity_g_l"] == 260.0


def test_snapshots_accumulate_in_the_history(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-2), salinity_g_l=250.0)
    twin_client.post(f"/api/twin/{registered_pan}/refresh")
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)
    twin_client.post(f"/api/twin/{registered_pan}/refresh")

    history = twin_client.get(f"/api/twin/{registered_pan}/history").json()

    assert history["total"] == 2
    assert [item["salinity_g_l"] for item in history["items"]] == [260.0, 250.0]


def test_a_refresh_that_finds_nothing_new_is_reported_as_unchanged(
    twin_client: TestClient, registered_pan: str
) -> None:
    """A change log must not fill with no-op entries."""
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)
    first = twin_client.post(f"/api/twin/{registered_pan}/refresh").json()
    second = twin_client.post(f"/api/twin/{registered_pan}/refresh").json()

    assert first["changed"] is True
    assert second["changed"] is False
    assert second["previous_twin_id"] == first["state"]["twin_id"]


def test_a_refresh_after_a_new_reading_is_reported_as_changed(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-2), salinity_g_l=250.0)
    twin_client.post(f"/api/twin/{registered_pan}/refresh")
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)

    assert twin_client.post(f"/api/twin/{registered_pan}/refresh").json()["changed"] is True


def test_a_stored_snapshot_keeps_the_provenance_it_was_built_from(
    twin_client: TestClient, registered_pan: str
) -> None:
    record(twin_client, timestamp=at(-2), salinity_g_l=250.0)
    twin_client.post(f"/api/twin/{registered_pan}/refresh")
    record(twin_client, timestamp=at(-1), salinity_g_l=260.0)
    twin_client.post(f"/api/twin/{registered_pan}/refresh")

    items = twin_client.get(f"/api/twin/{registered_pan}/history").json()["items"]

    # The older snapshot must still show the reading it was built from, not the
    # newer one that has since arrived.
    assert items[1]["salinity_g_l"] == 250.0
    assert items[1]["observation"]["id"] != items[0]["observation"]["id"]


def test_the_history_of_an_unregistered_pan_is_reported_as_not_found(
    twin_client: TestClient, registered_pan: str
) -> None:
    assert twin_client.get("/api/twin/nope/history").status_code == 404


def test_an_inverted_history_window_is_refused(
    twin_client: TestClient, registered_pan: str
) -> None:
    response = twin_client.get(
        f"/api/twin/{registered_pan}/history", params={"start": at(0), "end": at(-1)}
    )

    assert response.status_code == 422


def test_an_empty_history_is_an_empty_list_not_an_error(
    twin_client: TestClient, registered_pan: str
) -> None:
    history = twin_client.get(f"/api/twin/{registered_pan}/history").json()

    assert history["items"] == []
    assert history["total"] == 0
    assert history["earliest_timestamp"] is None


# -- module status -------------------------------------------------------


def test_module_status_reports_module_five_as_implemented(
    twin_client: TestClient,
) -> None:
    body = twin_client.get("/api/twin/status").json()

    assert body["module"] == "digital-twin"
    assert body["module_number"] == 5
    assert body["status"] == "implemented"


def test_module_status_states_that_nothing_is_inferred(
    twin_client: TestClient,
) -> None:
    detail = twin_client.get("/api/twin/status").json()["assembly"]["detail"]

    assert "inferred, averaged or carried forward" in detail
    assert "missing_inputs" in detail


def test_module_status_reports_live_snapshot_counts(
    twin_client: TestClient, registered_pan: str
) -> None:
    twin_client.post(f"/api/twin/{registered_pan}/refresh")

    assert twin_client.get("/api/twin/status").json()["twin"]["total_twin_states"] == 1


def test_module_status_works_without_a_database(
    client_without_database: TestClient,
) -> None:
    body = client_without_database.get("/api/twin/status").json()

    assert body["status"] == "implemented"
    assert body["twin"]["available"] is False


def test_persistence_endpoints_return_503_without_a_database(
    client_without_database: TestClient,
) -> None:
    for response in (
        client_without_database.get("/api/twin/kzn-2026-a01/state"),
        client_without_database.get("/api/twin/kzn-2026-a01/history"),
        client_without_database.post("/api/twin/kzn-2026-a01/refresh"),
    ):
        assert response.status_code == 503, response.text


def test_twin_routes_appear_in_openapi(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/twin/{pan_id}/state" in paths
    assert "/api/twin/{pan_id}/history" in paths
    assert "/api/twin/{pan_id}/refresh" in paths


def test_the_state_contract_documents_every_required_quantity(
    client: TestClient,
) -> None:
    properties = client.get("/openapi.json").json()["components"]["schemas"][
        "DigitalTwinStateRead"
    ]["properties"]

    for field in (
        "pan_id",
        "timestamp",
        "salinity_g_l",
        "water_depth_cm",
        "brine_temperature_c",
        "ambient_temperature_c",
        "humidity_percent",
        "pan_area_m2",
        "location",
        "forecast_rainfall_mm",
        "rainfall_probability_percent",
        "observation",
        "weather",
        "weather_forecast",
        "missing_inputs",
    ):
        assert field in properties, field


# -- fixtures and helpers ------------------------------------------------


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def twin_client(database: FakeDatabase) -> Any:
    """A TestClient whose pan, observation, weather and twin routes use memory."""
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_observation_service] = lambda: ObservationService(
        database
    )
    app.dependency_overrides[get_weather_service] = lambda: WeatherService(database)
    app.dependency_overrides[get_digital_twin_service] = lambda: DigitalTwinService(
        database
    )
    app.dependency_overrides[get_digital_twin_status_service] = lambda: DigitalTwinService(
        database
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def client_without_database() -> Any:
    """A TestClient with no database at all, for the 503 and status paths."""

    def unavailable() -> Any:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="database unavailable")

    app.dependency_overrides[get_db] = unavailable
    app.dependency_overrides[get_optional_db] = lambda: None
    app.dependency_overrides[get_digital_twin_status_service] = lambda: None
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def registered_pan(twin_client: TestClient) -> str:
    response = twin_client.post("/api/pans/", json=VALID_PAN)
    assert response.status_code == 201, response.text
    return response.json()["pan_id"]


def record(client: TestClient, **overrides: Any) -> Any:
    payload = {
        "pan_id": "kzn-2026-a01",
        "timestamp": at(-1),
        "salinity_g_l": 250.0,
        **overrides,
    }
    return client.post("/api/observations/", json=payload)


def weather(client: TestClient, **overrides: Any) -> Any:
    payload = {"pan_id": "kzn-2026-a01", "timestamp": at(-1), **overrides}
    return client.post("/api/weather/", json=payload)


def forecast(client: TestClient, **overrides: Any) -> Any:
    payload = {
        "pan_id": "kzn-2026-a01",
        "timestamp": at(0),
        "forecast_for": at(0, 6),
        **overrides,
    }
    return client.post("/api/weather/", json=payload)


def twin_state(client: TestClient, pan_id: str = "kzn-2026-a01") -> dict[str, Any]:
    response = client.get(f"/api/twin/{pan_id}/state")
    assert response.status_code == 200, response.text
    return response.json()
