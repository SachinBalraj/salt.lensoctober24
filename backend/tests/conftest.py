"""Shared fixtures for the whole backend suite.

Application settings are built once (``lru_cache``) the first time anything
imports ``app.config``, so environment overrides must exist before that first
import. This module is imported by pytest before any test module, which is why
the overrides live here and not in a per-test fixture.

Two overrides are set before ``app.main`` is imported at all.

``RATE_LIMIT_ENABLED`` is turned off for the suite. The middleware is attached
to the single shared ``app`` object and counts per client IP; several thousand
requests in a few seconds all come from the ``testclient`` address, so a
600-per-minute budget would make late test modules fail with 429 rather than
testing anything. The middleware itself is covered by a dedicated test that
builds its own app.

``SALTLENS_TEST_NO_DATABASE`` makes the shared database manager unconfigured.
The suite must never reach a real cluster: a developer's ``backend/.env``
usually holds a live Atlas URI, and a single stray query would then read or
write real records. It is an environment variable rather than a fixture because
the manager is built at import time, long before a fixture could run.
"""

import os

os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("SALTLENS_TEST_NO_DATABASE", "1")

from typing import Any

import pytest
from starlette.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> Any:
    """A throwaway TestClient over the shared app, with no database attached.

    Used by the meta tests (health, module registration, OpenAPI), which only
    read endpoints that need nothing persisted. Because it is used across test
    modules, its overrides are deliberately left untouched so no module's setup
    leaks into another module's meta checks.
    """
    with TestClient(app) as test_client:
        yield test_client