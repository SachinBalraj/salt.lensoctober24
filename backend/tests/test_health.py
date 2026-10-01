import pytest
from starlette.testclient import TestClient


def test_root_metadata(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "SaltLens DSS API"
    assert body["version"]
    assert body["health"] == "/api/health"


def test_health_endpoint_reports_database_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] in {
        "not_configured",
        "connected",
        "error",
        "disconnected",
        "unknown",
    }
    assert "database_health" in body
    assert "indexes_ready" in body
    assert body["python"]


def test_database_health_endpoint(client):
    response = client.get("/api/health/database")
    assert response.status_code == 200
    body = response.json()
    assert "status" in body
    assert "connected" in body
    assert "indexes_ready" in body


def test_persistence_dependency_is_explicit_without_a_database():
    from fastapi import HTTPException

    from app.db.mongo import MongoDatabase

    database = MongoDatabase(uri=None)
    with pytest.raises(HTTPException) as error:
        database.get_db()
    assert error.value.status_code == 503
    assert "MONGODB_URI" in error.value.detail


def test_no_read_endpoint_faults_when_the_database_is_absent(client):
    """Every read route must survive an absent database without a 500.

    Routers hand their services ``get_db`` -- the provider -- so the service is
    the layer that has to resolve it. A route that forgets answers with
    ``TypeError: 'function' object is not subscriptable``, which surfaces as an
    internal error for what is only a missing configuration. Enumerating the
    routes from the schema means a router added later is covered without this
    test being updated, and no response body is asserted here: the individual
    modules own what their own 503 says.
    """
    from app.main import app

    paths = app.openapi()["paths"]
    assert paths, "the schema should describe at least one route"

    checked = 0
    with TestClient(app) as test_client:
        for path, operations in sorted(paths.items()):
            # Path parameters would need a value, and a read route behind one is
            # covered by the module that owns it.
            if "get" not in operations or "{" in path:
                continue
            checked += 1
            response = test_client.get(path)
            # Either the route answered without needing storage, or it reported
            # the database as unavailable. Anything else -- 500 in particular --
            # means the route mishandled the missing database rather than
            # describing it.
            assert response.status_code < 500 or response.status_code == 503, (
                f"GET {path} answered {response.status_code} without a database"
            )
    assert checked > 20


def test_all_module_routers_registered(client):
    response = client.get("/api/modules")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 20
    assert "/api/auth" in body["prefixes"]
    assert "/api/twin" in body["prefixes"]
    assert "/api/explain" in body["prefixes"]
    assert "/api/experiments" in body["prefixes"]
    assert "/api/field-validation" in body["prefixes"]


def test_openapi_schema_available(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "SaltLens DSS API"
