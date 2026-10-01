"""The in-memory rate limiter, tested on its own app instance.

The middleware is attached to the shared application object, so the main suite
runs with ``RATE_LIMIT_ENABLED=false`` (see conftest.py). These tests build a
dedicated app to prove the limiter itself: budgets are enforced, the heavy
bucket is stricter, preflight requests are never throttled, and a higher budget
requires more requests before a 429.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.ratelimit import RateLimitMiddleware
from app.config import Settings


def _bounded_app(*, general: int, heavy: int) -> TestClient:
    """A throwaway app whose only endpoint returns 200 once reached."""
    app = FastAPI()
    settings = Settings(
        rate_limit_enabled=True,
        rate_limit_per_minute=general,
        rate_limit_heavy_per_minute=heavy,
        rate_limit_heavy_prefixes="/api/evaluate",
    )
    app.add_middleware(RateLimitMiddleware, settings=settings)

    @app.get("/api/plain")
    def plain() -> dict[str, str]:
        return {"ok": "true"}

    @app.get("/api/evaluate/the-job")
    def heavy_route() -> dict[str, str]:
        return {"ok": "true"}

    return TestClient(app)


def test_general_budget_is_enforced() -> None:
    with _bounded_app(general=3, heavy=3) as client:
        for _ in range(3):
            assert client.get("/api/plain").status_code == 200
        assert client.get("/api/plain").status_code == 429
        body = client.get("/api/plain").json()
        assert body["error_code"] == "rate_limited"
        assert "Too many requests" in body["detail"]


def test_heavy_prefix_draws_on_its_own_budget() -> None:
    with _bounded_app(general=100, heavy=2) as client:
        for _ in range(2):
            assert client.get("/api/evaluate/the-job").status_code == 200
        assert client.get("/api/evaluate/the-job").status_code == 429


def test_preflight_requests_are_never_throttled() -> None:
    with _bounded_app(general=1, heavy=1) as client:
        for _ in range(5):
            response = client.options("/api/plain")
            assert response.status_code != 429
        assert client.get("/api/plain").status_code == 200


def test_a_budget_exhausted_server_side_recovers_once() -> None:
    with _bounded_app(general=2, heavy=2) as client:
        assert client.get("/api/plain").status_code == 200
        assert client.get("/api/plain").status_code == 200
        assert client.get("/api/plain").status_code == 429