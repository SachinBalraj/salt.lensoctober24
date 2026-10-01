"""Module 3 - pan observation API tests.

These run against in-memory stand-ins for ``pan_observations`` and
``salt_pans``, so the whole request/response contract is exercised without a
live MongoDB. The behaviours worth locking down here are the ones that keep the
record honest: unmeasured fields stay null, backdated readings never masquerade
as the current state, and a reading cannot claim to come from an instrument that
is not connected.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.observations import (
    get_observation_service,
    get_observation_status_service,
)
from app.api.routers.pans import get_salt_pan_service
from app.main import app
from app.services.observations import ObservationService
from app.services.salt_pans import SaltPanService
from tests.fakes import FakeDatabase

VALID_PAN = {
    "pan_id": "KZN-2026-A01",
    "pan_name": "Northern Evaporation Pan",
    "location": "Port Shepstone",
    "area_m2": 12_500.0,
    "normal_depth_cm": 15.5,
    "target_harvest_salinity_g_l": 285.0,
}

VALID_OBSERVATION = {
    "pan_id": "kzn-2026-a01",
    "timestamp": "2026-03-14T06:30:00Z",
    "salinity_g_l": 281.4,
    "water_depth_cm": 12.5,
    "brine_temperature_c": 31.2,
    "humidity_percent": 62.0,
    "data_source": "manual",
    "note": "Midday reading after the morning pump-down.",
}

# A fixed instant the future-skew check is measured against.
NOW = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture()
def observation_client(database: FakeDatabase) -> Any:
    """A TestClient whose observation and pan routes are backed by memory."""
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    app.dependency_overrides[get_observation_service] = lambda: ObservationService(
        database
    )
    app.dependency_overrides[get_observation_status_service] = (
        lambda: ObservationService(database)
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture()
def registered_pan(observation_client: TestClient) -> str:
    response = observation_client.post("/api/pans/", json=VALID_PAN)
    assert response.status_code == 201, response.text
    return response.json()["pan_id"]


def record(
    client: TestClient, **overrides: Any
) -> Any:
    return client.post("/api/observations/", json={**VALID_OBSERVATION, **overrides})


# -- recording ------------------------------------------------------------


def test_record_observation_stores_every_measured_field(observation_client, registered_pan):
    body = record(observation_client).json()

    assert body["observation_id"]
    assert body["pan_id"] == registered_pan
    assert body["timestamp"] == "2026-03-14T06:30:00Z"
    assert body["salinity_g_l"] == pytest.approx(281.4)
    assert body["water_depth_cm"] == pytest.approx(12.5)
    assert body["brine_temperature_c"] == pytest.approx(31.2)
    assert body["humidity_percent"] == pytest.approx(62.0)
    assert body["ambient_temperature_c"] is None
    assert body["data_source"] == "manual"
    assert body["note"] == "Midday reading after the morning pump-down."


def test_record_observation_returns_201(observation_client, registered_pan):
    assert record(observation_client).status_code == 201


def test_observation_identifier_is_readable_back(observation_client, registered_pan):
    created = record(observation_client).json()

    response = observation_client.get(
        f"/api/observations/{created['observation_id']}"
    )
    assert response.status_code == 200
    assert response.json() == created


def test_pan_reference_is_matched_case_insensitively(observation_client, registered_pan):
    response = record(observation_client, pan_id="KZN-2026-A01")
    assert response.status_code == 201, response.text
    assert response.json()["pan_id"] == registered_pan


def test_unmeasured_fields_are_stored_as_null_not_defaulted(
    observation_client, registered_pan
):
    """A field the operator did not measure must stay null, not be invented."""
    response = observation_client.post(
        "/api/observations/",
        json={"pan_id": registered_pan, "salinity_g_l": 280.0},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["salinity_g_l"] == pytest.approx(280.0)
    assert body["water_depth_cm"] is None
    assert body["brine_temperature_c"] is None
    assert body["humidity_percent"] is None
    assert body["ambient_temperature_c"] is None


def test_nulls_survive_a_round_trip_through_storage(
    observation_client, registered_pan
):
    created = observation_client.post(
        "/api/observations/",
        json={"pan_id": registered_pan, "water_depth_cm": 9.0},
    ).json()

    stored = observation_client.get(
        f"/api/observations/{created['observation_id']}"
    ).json()
    assert stored["water_depth_cm"] == pytest.approx(9.0)
    assert stored["salinity_g_l"] is None
    assert stored["brine_temperature_c"] is None

    listed = observation_client.get("/api/observations/").json()["items"][0]
    assert listed["salinity_g_l"] is None
    assert listed["humidity_percent"] is None


def test_timestamp_defaults_to_now_when_omitted(observation_client, registered_pan):
    response = observation_client.post(
        "/api/observations/", json={"pan_id": registered_pan, "salinity_g_l": 280.0}
    )
    assert response.status_code == 201
    assert response.json()["timestamp"]


def test_backdated_reading_is_accepted_and_preserved_exactly(
    observation_client, registered_pan
):
    response = record(observation_client, timestamp="2025-11-02T04:00:00Z")
    assert response.status_code == 201, response.text
    assert response.json()["timestamp"] == "2025-11-02T04:00:00Z"


def test_two_readings_may_share_a_timestamp(observation_client, registered_pan):
    """Backfilled day-granularity data must not collide with itself."""
    assert record(observation_client).status_code == 201
    assert record(observation_client).status_code == 201
    assert observation_client.get("/api/observations/").json()["total"] == 2


def test_record_observation_requires_a_registered_pan(observation_client):
    response = record(observation_client, pan_id="never-registered")
    assert response.status_code == 404
    assert "Register the pan" in response.json()["detail"]


def test_record_observation_for_a_deactivated_pan_is_allowed(observation_client, registered_pan):
    """Field observations often arrive after a pan has been retired."""
    observation_client.post(f"/api/pans/{registered_pan}/deactivate")
    assert record(observation_client).status_code == 201


# -- validation -----------------------------------------------------------


def test_observation_requires_at_least_one_measurement(observation_client, registered_pan):
    response = observation_client.post(
        "/api/observations/", json={"pan_id": registered_pan, "note": "Nothing to add."}
    )
    assert response.status_code == 422
    assert "at least one measured value" in response.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("salinity_g_l", -1),
        ("salinity_g_l", 501),
        ("water_depth_cm", -0.1),
        ("water_depth_cm", 1001),
        ("brine_temperature_c", -21),
        ("brine_temperature_c", 121),
        ("humidity_percent", -0.5),
        ("humidity_percent", 100.5),
        ("ambient_temperature_c", -51),
        ("ambient_temperature_c", 81),
    ],
)
def test_observation_rejects_implausible_measurements(
    observation_client, registered_pan, field, value
):
    response = observation_client.post(
        "/api/observations/", json={"pan_id": registered_pan, field: value}
    )
    assert response.status_code == 422, response.text
    assert field in response.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("salinity_g_l", 0),
        ("salinity_g_l", 500),
        ("water_depth_cm", 0),
        ("water_depth_cm", 1000),
        ("humidity_percent", 0),
        ("humidity_percent", 100),
        ("brine_temperature_c", -20),
        ("brine_temperature_c", 120),
        ("ambient_temperature_c", -50),
        ("ambient_temperature_c", 80),
    ],
)
def test_observation_accepts_range_boundaries(
    observation_client, registered_pan, field, value
):
    response = observation_client.post(
        "/api/observations/", json={"pan_id": registered_pan, field: value}
    )
    assert response.status_code == 201, response.text


def test_observation_rejects_future_timestamps(observation_client, registered_pan, monkeypatch):
    monkeypatch.setattr("app.schemas.observation._now", lambda: NOW)
    future = "2099-01-01T00:00:00Z"
    response = observation_client.post(
        "/api/observations/",
        json={"pan_id": registered_pan, "salinity_g_l": 280.0, "timestamp": future},
    )
    assert response.status_code == 422
    assert "future" in response.text


def test_observation_tolerates_a_small_clock_skew(observation_client, registered_pan, monkeypatch):
    """Field devices can be a couple of minutes ahead without being wrong."""
    monkeypatch.setattr("app.schemas.observation._now", lambda: NOW)
    response = observation_client.post(
        "/api/observations/",
        json={
            "pan_id": registered_pan,
            "salinity_g_l": 280.0,
            "timestamp": "2026-03-14T12:03:00Z",
        },
    )
    assert response.status_code == 201, response.text


def test_observation_rejects_unknown_fields(observation_client, registered_pan):
    response = observation_client.post(
        "/api/observations/",
        json={"pan_id": registered_pan, "salinity_g_l": 280.0, "rainfall_mm": 4.0},
    )
    assert response.status_code == 422


def test_observation_rejects_an_unknown_data_source(observation_client, registered_pan):
    response = observation_client.post(
        "/api/observations/",
        json={"pan_id": registered_pan, "salinity_g_l": 280.0, "data_source": "drone"},
    )
    assert response.status_code == 422


def test_observation_rejects_an_overlong_note(observation_client, registered_pan):
    response = observation_client.post(
        "/api/observations/",
        json={"pan_id": registered_pan, "salinity_g_l": 280.0, "note": "x" * 501},
    )
    assert response.status_code == 422


# -- data provenance ------------------------------------------------------


def test_sensor_reading_is_refused_while_no_gateway_is_connected(
    observation_client, registered_pan
):
    response = record(observation_client, data_source="sensor")
    assert response.status_code == 409
    assert "sensor" in response.json()["detail"].lower()


def test_refused_sensor_reading_is_not_stored(observation_client, registered_pan):
    record(observation_client, data_source="sensor")
    assert observation_client.get("/api/observations/").json()["total"] == 0


def test_import_provenance_is_accepted(observation_client, registered_pan):
    response = record(observation_client, data_source="import")
    assert response.status_code == 201, response.text
    assert response.json()["data_source"] == "import"


def test_data_source_defaults_to_manual(observation_client, registered_pan):
    response = observation_client.post(
        "/api/observations/", json={"pan_id": registered_pan, "salinity_g_l": 280.0}
    )
    assert response.json()["data_source"] == "manual"


def test_sensor_ingest_route_reports_itself_unavailable(observation_client):
    response = observation_client.post("/api/observations/ingest/sensor", json={})
    assert response.status_code == 501
    assert "No sensor gateway is connected" in response.json()["detail"]


def test_status_advertises_the_absent_gateway(observation_client):
    body = observation_client.get("/api/observations/status").json()
    assert body["sensor_ingest"]["available"] is False
    assert "No sensor gateway" in body["sensor_ingest"]["detail"]


# -- latest observed state ------------------------------------------------


def test_first_reading_becomes_the_pans_latest_state(observation_client, registered_pan):
    created = record(observation_client).json()

    pan = observation_client.get(f"/api/pans/{registered_pan}").json()
    latest = pan["latest_observation"]
    assert latest["observation_id"] == created["observation_id"]
    assert latest["timestamp"] == created["timestamp"]
    assert latest["salinity_g_l"] == pytest.approx(281.4)
    assert latest["data_source"] == "manual"


def test_latest_state_starts_empty_for_a_new_pan(observation_client, registered_pan):
    pan = observation_client.get(f"/api/pans/{registered_pan}").json()
    assert pan["latest_observation"] is None


def test_latest_state_moves_forward_with_a_newer_reading(
    observation_client, registered_pan
):
    first = record(observation_client, timestamp="2026-03-14T06:30:00Z").json()
    second = record(observation_client, timestamp="2026-03-14T09:00:00Z").json()

    latest = observation_client.get(f"/api/pans/{registered_pan}").json()[
        "latest_observation"
    ]
    assert latest["observation_id"] == second["observation_id"]
    assert first["observation_id"] != second["observation_id"]


def test_backfilled_reading_does_not_displace_a_newer_latest_state(
    observation_client, registered_pan
):
    """The registry must not display last week as if it were the current state."""
    record(observation_client, timestamp="2026-03-14T09:00:00Z", salinity_g_l=300.0)
    record(observation_client, timestamp="2026-03-01T09:00:00Z", salinity_g_l=250.0)

    latest = observation_client.get(f"/api/pans/{registered_pan}").json()[
        "latest_observation"
    ]
    assert latest["salinity_g_l"] == pytest.approx(300.0)
    assert latest["timestamp"] == "2026-03-14T09:00:00Z"


def test_backfilled_reading_is_still_recorded_in_history(observation_client, registered_pan):
    record(observation_client, timestamp="2026-03-14T09:00:00Z")
    record(observation_client, timestamp="2026-03-01T09:00:00Z")

    body = observation_client.get("/api/observations/").json()
    assert body["total"] == 2
    assert [item["timestamp"] for item in body["items"]] == [
        "2026-03-14T09:00:00Z",
        "2026-03-01T09:00:00Z",
    ]


def test_latest_state_carries_only_the_newest_measurement_of_that_reading(
    observation_client, registered_pan
):
    record(
        observation_client,
        timestamp="2026-03-14T09:00:00Z",
        salinity_g_l=None,
        water_depth_cm=7.5,
    )
    latest = observation_client.get(f"/api/pans/{registered_pan}").json()[
        "latest_observation"
    ]
    assert latest["water_depth_cm"] == pytest.approx(7.5)
    assert latest["salinity_g_l"] is None


def test_latest_state_survives_a_pan_edit(observation_client, registered_pan):
    """Editing a pan must not silently discard the observation history pointer."""
    created = record(observation_client).json()
    observation_client.patch(f"/api/pans/{registered_pan}", json={"pan_name": "Renamed"})

    pan = observation_client.get(f"/api/pans/{registered_pan}").json()
    assert pan["pan_name"] == "Renamed"
    assert pan["latest_observation"]["observation_id"] == created["observation_id"]


def test_latest_state_survives_deactivation(observation_client, registered_pan):
    record(observation_client)
    observation_client.post(f"/api/pans/{registered_pan}/deactivate")

    pan = observation_client.get(f"/api/pans/{registered_pan}").json()
    assert pan["is_active"] is False
    assert pan["latest_observation"] is not None


def test_latest_state_is_tracked_per_pan(observation_client, registered_pan):
    observation_client.post("/api/pans/", json={**VALID_PAN, "pan_id": "KZN-2026-B02"})
    record(observation_client, pan_id=registered_pan, salinity_g_l=280.0)
    record(observation_client, pan_id="kzn-2026-b02", salinity_g_l=310.0)

    first = observation_client.get(f"/api/pans/{registered_pan}").json()
    second = observation_client.get("/api/pans/kzn-2026-b02").json()
    assert first["latest_observation"]["salinity_g_l"] == pytest.approx(280.0)
    assert second["latest_observation"]["salinity_g_l"] == pytest.approx(310.0)


# -- history --------------------------------------------------------------


def test_history_is_empty_for_a_pan_with_no_readings(observation_client, registered_pan):
    body = observation_client.get(f"/api/observations/?pan_id={registered_pan}").json()
    assert body == {
        "items": [],
        "total": 0,
        "limit": 200,
        "offset": 0,
        "earliest_timestamp": None,
        "latest_timestamp": None,
    }


def test_history_is_ordered_newest_first(observation_client, registered_pan):
    for stamp in ("2026-03-01T06:00:00Z", "2026-03-14T06:00:00Z", "2026-03-07T06:00:00Z"):
        record(observation_client, timestamp=stamp)

    body = observation_client.get("/api/observations/").json()
    assert [item["timestamp"] for item in body["items"]] == [
        "2026-03-14T06:00:00Z",
        "2026-03-07T06:00:00Z",
        "2026-03-01T06:00:00Z",
    ]
    assert body["earliest_timestamp"] == "2026-03-01T06:00:00Z"
    assert body["latest_timestamp"] == "2026-03-14T06:00:00Z"


def test_history_can_be_narrowed_to_one_pan(observation_client, registered_pan):
    observation_client.post("/api/pans/", json={**VALID_PAN, "pan_id": "KZN-2026-B02"})
    record(observation_client, pan_id=registered_pan)
    record(observation_client, pan_id="kzn-2026-b02")

    body = observation_client.get(f"/api/observations/?pan_id={registered_pan}").json()
    assert body["total"] == 1
    assert body["items"][0]["pan_id"] == registered_pan


def test_history_pan_filter_is_case_insensitive(observation_client, registered_pan):
    record(observation_client)
    assert observation_client.get("/api/observations/?pan_id=KZN-2026-A01").json()[
        "total"
    ] == 1


def test_history_filter_for_an_unregistered_pan_returns_404(observation_client):
    response = observation_client.get("/api/observations/?pan_id=ghost-pan")
    assert response.status_code == 404


def test_history_can_be_narrowed_to_a_time_window(observation_client, registered_pan):
    for stamp in ("2026-03-01T06:00:00Z", "2026-03-10T06:00:00Z", "2026-03-20T06:00:00Z"):
        record(observation_client, timestamp=stamp)

    body = observation_client.get(
        "/api/observations/?start=2026-03-05T00:00:00Z&end=2026-03-15T00:00:00Z"
    ).json()
    assert [item["timestamp"] for item in body["items"]] == ["2026-03-10T06:00:00Z"]


def test_history_window_bounds_are_inclusive(observation_client, registered_pan):
    record(observation_client, timestamp="2026-03-10T06:00:00Z")
    body = observation_client.get(
        "/api/observations/?start=2026-03-10T06:00:00Z&end=2026-03-10T06:00:00Z"
    ).json()
    assert body["total"] == 1


def test_history_rejects_an_inverted_window(observation_client):
    response = observation_client.get(
        "/api/observations/?start=2026-03-15T00:00:00Z&end=2026-03-01T00:00:00Z"
    )
    assert response.status_code == 422


def test_history_paging_reports_the_true_total(observation_client, registered_pan):
    for index in range(5):
        record(observation_client, timestamp=f"2026-03-0{index + 1}T06:00:00Z")

    body = observation_client.get("/api/observations/?limit=2&offset=0").json()
    assert body["total"] == 5
    assert body["limit"] == 2
    assert len(body["items"]) == 2

    tail = observation_client.get("/api/observations/?limit=2&offset=4").json()
    assert len(tail["items"]) == 1


def test_history_rejects_out_of_range_paging(observation_client):
    assert observation_client.get("/api/observations/?limit=0").status_code == 422
    assert observation_client.get("/api/observations/?limit=1001").status_code == 422
    assert observation_client.get("/api/observations/?offset=-1").status_code == 422


def test_history_spans_every_pan_by_default(observation_client, registered_pan):
    observation_client.post("/api/pans/", json={**VALID_PAN, "pan_id": "KZN-2026-B02"})
    record(observation_client, pan_id=registered_pan)
    record(observation_client, pan_id="kzn-2026-b02")

    assert observation_client.get("/api/observations/").json()["total"] == 2


def test_open_unknown_observation_returns_404(observation_client):
    response = observation_client.get("/api/observations/obs-does-not-exist")
    assert response.status_code == 404


# -- module contract ------------------------------------------------------


def test_module_status_reports_module_three_as_implemented(observation_client):
    body = observation_client.get("/api/observations/status").json()
    assert body["module"] == "observations"
    assert body["module_number"] == 3
    assert body["status"] == "implemented"


def test_module_status_reports_live_observation_counts(observation_client, registered_pan):
    record(observation_client)
    record(observation_client)

    assert observation_client.get("/api/observations/status").json()["observations"] == {
        "available": True,
        "total_observations": 2,
    }


def test_module_status_reports_an_unavailable_database_without_failing():
    """A status endpoint must not 503: reporting unavailability is its job."""
    with TestClient(app) as test_client:
        response = test_client.get("/api/observations/status")
    assert response.status_code == 200
    observations = response.json()["observations"]
    assert observations["available"] is False
    assert "MONGODB_URI" in observations["detail"]


def test_invalid_payload_is_reported_as_422_even_without_a_database():
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/observations/", json={**VALID_OBSERVATION, "humidity_percent": 500}
        )
    assert response.status_code == 422


def test_persistence_endpoints_return_503_without_a_database():
    """Without MONGODB_URI the module must fail loudly rather than fake readings."""
    with TestClient(app) as test_client:
        assert test_client.get("/api/observations/").status_code == 503
        assert test_client.post("/api/observations/", json=VALID_OBSERVATION).status_code == 503
        assert test_client.get("/api/observations/obs-1").status_code == 503


def test_observation_routes_appear_in_openapi(observation_client):
    paths = observation_client.get("/openapi.json").json()["paths"]
    assert set(paths["/api/observations/"]) == {"get", "post"}
    assert "get" in paths["/api/observations/{observation_id}"]
    assert "post" in paths["/api/observations/ingest/sensor"]


def test_note_is_optional_and_surrounding_whitespace_is_trimmed(
    observation_client, registered_pan
):
    response = observation_client.post(
        "/api/observations/",
        json={"pan_id": registered_pan, "salinity_g_l": 280.0, "note": "  After rain.  "},
    )
    assert response.json()["note"] == "After rain."


def test_absent_note_is_returned_as_null(observation_client, registered_pan):
    response = observation_client.post(
        "/api/observations/", json={"pan_id": registered_pan, "salinity_g_l": 280.0}
    )
    assert response.json()["note"] is None
