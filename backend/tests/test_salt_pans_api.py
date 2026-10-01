"""Module 2 - salt-pan registry API tests.

These run against an in-memory stand-in for the ``salt_pans`` collection, so the
full request/response contract is exercised without a live MongoDB instance.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_optional_db
from app.api.routers.pans import get_salt_pan_service
from app.main import app
from app.services.salt_pans import SaltPanService
from tests.fakes import FakeDatabase

VALID_REGISTRATION = {
    "pan_id": "KZN-2026-A01",
    "pan_name": "Northern Evaporation Pan",
    "location": "Port Shepstone",
    "latitude": -30.7444,
    "longitude": 30.8071,
    "area_m2": 12_500.0,
    "normal_depth_cm": 15.5,
    "target_harvest_salinity_g_l": 285.0,
}


@pytest.fixture()
def pan_client() -> Any:
    """A TestClient whose salt-pan routes are backed by an in-memory collection."""
    database = FakeDatabase()
    app.dependency_overrides[get_db] = lambda: database
    app.dependency_overrides[get_optional_db] = lambda: database
    app.dependency_overrides[get_salt_pan_service] = lambda: SaltPanService(database)
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def register(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload = {**VALID_REGISTRATION, **overrides}
    response = client.post("/api/pans/", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# -- registration ---------------------------------------------------------


def test_register_pan_stores_every_registration_field(pan_client):
    body = register(pan_client)

    assert body["pan_id"] == "kzn-2026-a01"
    assert body["pan_name"] == "Northern Evaporation Pan"
    assert body["location"] == "Port Shepstone"
    assert body["latitude"] == pytest.approx(-30.7444)
    assert body["longitude"] == pytest.approx(30.8071)
    assert body["area_m2"] == pytest.approx(12_500.0)
    assert body["normal_depth_cm"] == pytest.approx(15.5)
    assert body["target_harvest_salinity_g_l"] == pytest.approx(285.0)
    assert body["is_active"] is True
    assert body["deactivated_at"] is None
    assert body["id"] is not None and len(body["id"]) == 24
    assert body["created_at"] and body["updated_at"]


def test_register_pan_allows_omitted_coordinates(pan_client):
    body = register(
        pan_client,
        pan_id="no-coords-1",
        latitude=None,
        longitude=None,
    )
    assert body["latitude"] is None
    assert body["longitude"] is None


def test_register_pan_rejects_duplicate_identifier(pan_client):
    register(pan_client)
    response = pan_client.post("/api/pans/", json=VALID_REGISTRATION)
    assert response.status_code == 409
    assert response.json()["error_code"] == "duplicate_document"


def test_register_pan_rejects_unknown_fields(pan_client):
    response = pan_client.post(
        "/api/pans/", json={**VALID_REGISTRATION, "unexpected": "value"}
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("area_m2", 0),
        ("area_m2", -1),
        ("normal_depth_cm", 0),
        ("normal_depth_cm", -0.5),
        ("target_harvest_salinity_g_l", 0),
        ("target_harvest_salinity_g_l", -285),
    ],
)
def test_register_pan_rejects_non_positive_measurements(pan_client, field, value):
    response = pan_client.post("/api/pans/", json={**VALID_REGISTRATION, field: value})
    assert response.status_code == 422, response.text
    assert field in response.text


def test_register_pan_accepts_small_positive_measurements(pan_client):
    body = register(pan_client, area_m2=0.01, normal_depth_cm=0.1, target_harvest_salinity_g_l=0.5)
    assert body["area_m2"] == pytest.approx(0.01)


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [
        (90.1, 0.0),
        (-90.1, 0.0),
        (0.0, 180.1),
        (0.0, -180.1),
    ],
)
def test_register_pan_rejects_out_of_range_coordinates(pan_client, latitude, longitude):
    response = pan_client.post(
        "/api/pans/",
        json={**VALID_REGISTRATION, "latitude": latitude, "longitude": longitude},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("boundary", [(-90, -180), (90, 180)])
def test_register_pan_accepts_coordinate_boundaries(pan_client, boundary):
    latitude, longitude = boundary
    body = register(
        pan_client,
        pan_id=f"edge-{latitude}",
        latitude=latitude,
        longitude=longitude,
    )
    assert body["latitude"] == pytest.approx(latitude)


def test_register_pan_requires_both_coordinate_components(pan_client):
    response = pan_client.post(
        "/api/pans/", json={**VALID_REGISTRATION, "longitude": None}
    )
    assert response.status_code == 422
    assert "provided together" in response.text


def test_register_pan_requires_every_mandatory_field(pan_client):
    for field in (
        "pan_id",
        "pan_name",
        "location",
        "area_m2",
        "normal_depth_cm",
        "target_harvest_salinity_g_l",
    ):
        payload = {k: v for k, v in VALID_REGISTRATION.items() if k != field}
        response = pan_client.post("/api/pans/", json=payload)
        assert response.status_code == 422, f"{field} should be required"


# -- listing --------------------------------------------------------------


def test_list_pans_is_empty_for_an_unused_registry(pan_client):
    response = pan_client.get("/api/pans/")
    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["total_active"] == 0
    assert body["total_inactive"] == 0


def test_list_pans_returns_registered_pans(pan_client):
    register(pan_client)
    register(pan_client, pan_id="KZN-2026-B02", pan_name="Southern Pan")

    body = pan_client.get("/api/pans/").json()

    assert body["total"] == 2
    assert body["total_active"] == 2
    assert [item["pan_id"] for item in body["items"]] == [
        "kzn-2026-a01",
        "kzn-2026-b02",
    ]


def test_list_pans_search_matches_id_name_and_location(pan_client):
    register(pan_client)
    register(pan_client, pan_id="other-9", pan_name="Zebra Pan", location="Durban")

    assert pan_client.get("/api/pans/?search=zebra").json()["total"] == 1
    assert pan_client.get("/api/pans/?search=ZEBRA").json()["total"] == 1
    assert pan_client.get("/api/pans/?search=OTHER-9").json()["total"] == 1
    assert pan_client.get("/api/pans/?search=durban").json()["total"] == 1
    assert pan_client.get("/api/pans/?search=absent").json()["total"] == 0


def test_list_pans_search_treats_input_as_a_literal_string(pan_client):
    """Operator input must not be interpreted as a regular expression."""
    register(pan_client)
    register(pan_client, pan_id="other-9", pan_name="Zebra Pan")

    assert pan_client.get("/api/pans/?search=.*").json()["total"] == 0
    assert pan_client.get("/api/pans/?search=(").json()["total"] == 0


def test_list_pans_excludes_deactivated_pans_by_default(pan_client):
    register(pan_client)
    register(pan_client, pan_id="KZN-2026-B02")
    assert pan_client.post("/api/pans/kzn-2026-a01/deactivate").status_code == 200

    assert pan_client.get("/api/pans/").json()["total"] == 1
    assert pan_client.get("/api/pans/?pan_status=all").json()["total"] == 2
    inactive = pan_client.get("/api/pans/?pan_status=inactive").json()
    assert [item["pan_id"] for item in inactive["items"]] == ["kzn-2026-a01"]
    assert pan_client.get("/api/pans/").json()["total_inactive"] == 1


def test_list_pans_paging_reports_registry_wide_counts(pan_client):
    for index in range(3):
        register(pan_client, pan_id=f"pan-{index}", pan_name=f"Pan {index}")

    body = pan_client.get("/api/pans/?limit=2&offset=0").json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["items"]) == 2

    tail = pan_client.get("/api/pans/?limit=2&offset=2").json()
    assert len(tail["items"]) == 1


def test_list_pans_rejects_out_of_range_paging(pan_client):
    assert pan_client.get("/api/pans/?limit=0").status_code == 422
    assert pan_client.get("/api/pans/?limit=201").status_code == 422
    assert pan_client.get("/api/pans/?offset=-1").status_code == 422
    assert pan_client.get("/api/pans/?pan_status=unknown").status_code == 422


# -- single pan -----------------------------------------------------------


def test_open_pan_is_case_insensitive(pan_client):
    register(pan_client)
    for identifier in ("KZN-2026-A01", "kzn-2026-a01", " KZN-2026-A01 "):
        response = pan_client.get(f"/api/pans/{identifier}")
        assert response.status_code == 200, response.text
        assert response.json()["pan_id"] == "kzn-2026-a01"


def test_open_unknown_pan_returns_404(pan_client):
    response = pan_client.get("/api/pans/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error_code"] == "document_not_found"


# -- editing --------------------------------------------------------------


def test_edit_pan_updates_only_supplied_fields(pan_client):
    register(pan_client)
    response = pan_client.patch(
        "/api/pans/kzn-2026-a01",
        json={"pan_name": "Renamed Pan", "area_m2": 13_000},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["pan_name"] == "Renamed Pan"
    assert body["area_m2"] == pytest.approx(13_000)
    assert body["location"] == "Port Shepstone"
    assert body["normal_depth_cm"] == pytest.approx(15.5)


def test_edit_pan_records_a_newer_updated_timestamp(pan_client):
    created = register(pan_client)
    patched = pan_client.patch(
        "/api/pans/kzn-2026-a01", json={"pan_name": "Renamed Pan"}
    ).json()
    assert patched["created_at"] == created["created_at"]
    assert patched["updated_at"] >= created["updated_at"]


def test_edit_pan_rejects_empty_payload(pan_client):
    register(pan_client)
    response = pan_client.patch("/api/pans/kzn-2026-a01", json={})
    assert response.status_code == 400
    assert "No editable fields" in response.json()["detail"]


def test_edit_pan_cannot_change_the_identifier(pan_client):
    register(pan_client)
    response = pan_client.patch(
        "/api/pans/kzn-2026-a01", json={"pan_id": "something-else"}
    )
    assert response.status_code == 422
    assert "pan_id" in response.text


def test_edit_pan_rejects_non_positive_measurements(pan_client):
    register(pan_client)
    response = pan_client.patch("/api/pans/kzn-2026-a01", json={"area_m2": -5})
    assert response.status_code == 422


def test_edit_pan_keeps_the_coordinate_pair_intact(pan_client):
    """Supplying one coordinate of a geolocated pan keeps the stored partner."""
    register(pan_client)
    body = pan_client.patch(
        "/api/pans/kzn-2026-a01", json={"latitude": -30.0}
    ).json()
    assert body["latitude"] == pytest.approx(-30.0)
    assert body["longitude"] == pytest.approx(30.8071)


def test_edit_pan_rejects_clearing_only_one_coordinate(pan_client):
    register(pan_client)
    response = pan_client.patch("/api/pans/kzn-2026-a01", json={"latitude": None})
    assert response.status_code == 422
    assert "provided together" in response.text


def test_edit_pan_rejects_adding_a_lone_coordinate_to_ungeolocated_pan(pan_client):
    register(pan_client, pan_id="no-coords-1", latitude=None, longitude=None)
    response = pan_client.patch("/api/pans/no-coords-1", json={"latitude": -30.0})
    assert response.status_code == 422
    assert "provided together" in response.text


def test_edit_pan_can_reposition_both_coordinates(pan_client):
    register(pan_client)
    body = pan_client.patch(
        "/api/pans/kzn-2026-a01",
        json={"latitude": -30.5, "longitude": 30.9},
    ).json()
    assert body["latitude"] == pytest.approx(-30.5)
    assert body["longitude"] == pytest.approx(30.9)


def test_edit_pan_can_clear_coordinates(pan_client):
    register(pan_client)
    body = pan_client.patch(
        "/api/pans/kzn-2026-a01", json={"latitude": None, "longitude": None}
    ).json()
    assert body["latitude"] is None
    assert body["longitude"] is None


def test_edit_unknown_pan_returns_404(pan_client):
    response = pan_client.patch("/api/pans/missing", json={"pan_name": "New"})
    assert response.status_code == 404


# -- deactivation ---------------------------------------------------------


def test_deactivate_pan_marks_it_inactive_and_keeps_the_record(pan_client):
    created = register(pan_client)
    response = pan_client.post("/api/pans/kzn-2026-a01/deactivate")
    assert response.status_code == 200
    body = response.json()
    assert body["is_active"] is False
    assert body["deactivated_at"] is not None
    assert body["created_at"] == created["created_at"]
    assert body["area_m2"] == pytest.approx(12_500.0)

    stored = pan_client.get("/api/pans/kzn-2026-a01").json()
    assert stored["is_active"] is False
    assert pan_client.get("/api/pans/").json()["total"] == 0


def test_deactivate_pan_twice_returns_409(pan_client):
    register(pan_client)
    assert pan_client.post("/api/pans/kzn-2026-a01/deactivate").status_code == 200
    response = pan_client.post("/api/pans/kzn-2026-a01/deactivate")
    assert response.status_code == 409
    assert "already deactivated" in response.json()["detail"]


def test_deactivate_unknown_pan_returns_404(pan_client):
    assert pan_client.post("/api/pans/missing/deactivate").status_code == 404


def test_deactivated_pan_identifier_cannot_be_reused(pan_client):
    register(pan_client)
    pan_client.post("/api/pans/kzn-2026-a01/deactivate")
    response = pan_client.post("/api/pans/", json=VALID_REGISTRATION)
    assert response.status_code == 409


# -- module contract ------------------------------------------------------


def test_module_status_reports_module_two_as_implemented(pan_client):
    body = pan_client.get("/api/pans/status").json()
    assert body["module"] == "salt-pans"
    assert body["module_number"] == 2
    assert body["status"] == "implemented"
    assert "POST   /api/pans/" in body["endpoints"]


def test_module_status_reports_live_counts(pan_client):
    register(pan_client, pan_id="a-1")
    register(pan_client, pan_id="a-2")
    register(pan_client, pan_id="b-1")
    pan_client.post("/api/pans/b-1/deactivate")

    registry = pan_client.get("/api/pans/status").json()["registry"]
    assert registry == {
        "available": True,
        "total_active": 2,
        "total_inactive": 1,
    }


def test_module_status_reports_an_unavailable_database_without_failing():
    """A status endpoint must not 503: reporting unavailability is its job."""
    with TestClient(app) as test_client:
        response = test_client.get("/api/pans/status")
    assert response.status_code == 200
    registry = response.json()["registry"]
    assert registry["available"] is False
    assert "MONGODB_URI" in registry["detail"]


def test_invalid_payload_is_reported_as_422_even_without_a_database():
    """Validation must not be masked by the persistence layer being down."""
    with TestClient(app) as test_client:
        response = test_client.post("/api/pans/", json={**VALID_REGISTRATION, "area_m2": 0})
    assert response.status_code == 422


def test_pan_routes_appear_in_openapi(pan_client):
    paths = pan_client.get("/openapi.json").json()["paths"]
    assert set(paths["/api/pans/"]) == {"get", "post"}
    assert set(paths["/api/pans/{pan_id}"]) == {"get", "patch"}
    assert "post" in paths["/api/pans/{pan_id}/deactivate"]


def test_persistence_endpoints_return_503_without_a_database():
    """Without MONGODB_URI the registry must fail loudly rather than fake rows."""
    with TestClient(app) as test_client:
        assert test_client.get("/api/pans/").status_code == 503
        assert test_client.post("/api/pans/", json=VALID_REGISTRATION).status_code == 503
        assert test_client.get("/api/pans/kzn-2026-a01").status_code == 503


def test_sort_is_stable_on_pan_id(pan_client):
    for identifier in ("c-3", "a-1", "b-2"):
        register(pan_client, pan_id=identifier)
    body = pan_client.get("/api/pans/").json()
    assert [item["pan_id"] for item in body["items"]] == ["a-1", "b-2", "c-3"]
