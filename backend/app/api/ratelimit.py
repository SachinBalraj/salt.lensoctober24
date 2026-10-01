"""Per-client request throttling for the open-access API.

There is no account layer yet (Module 1 authentication is a placeholder), so the
only cheap client identity is the source IP address. This middleware keeps a
fixed-window counter per ``(client_ip, bucket)`` in memory:

* every request counts against a general per-minute budget;
* requests to compute-heavy prefixes count against their own, tighter budget
  too (training, evaluation, SHAP explanation, simulation sweeps, dataset
  building, prediction).

A fixed window is deliberately simple: it bounds abuse without pretending to
per-client fairness, and it needs no external store, which keeps the prototype
single-process. The window is pruned as it ages so the table cannot grow without
bound.

Violations return HTTP 429 with a body that says when the window resets, in the
same ``detail``/``error_code`` shape the rest of the API uses.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.config import Settings


def _buckets_for_path(length: int, heavy: int, prefixes: tuple[str, ...], path: str) -> list[int]:
    """Which per-minute budgets a request on ``path`` draws from."""
    buckets = [length]
    if any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes):
        buckets.append(heavy)
    return buckets


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Throttle requests by source IP against per-minute budgets.

    Constructed from :class:`app.config.Settings` so the limits and the list of
    heavy prefixes stay configuration rather than code. Disabled entirely when
    ``settings.rate_limit_enabled`` is false.
    """

    def __init__(self, app: Any, settings: Settings) -> None:
        super().__init__(app)
        self._enabled = settings.rate_limit_enabled
        self._general = settings.rate_limit_per_minute
        self._heavy = settings.rate_limit_heavy_per_minute
        prefixes = settings.rate_limit_heavy_prefixes
        self._heavy_prefixes = tuple(
            item.strip()
            for item in prefixes.split(",")
            if item.strip()
        )
        self._window_seconds = 60.0
        # key -> (window_start, assigned_counts)
        self._windows: dict[str, tuple[float, list[int]]] = defaultdict(
            lambda: (0.0, [])
        )
        self._counts: dict[str, list[int]] = {}

    def _key(self, client_ip: str | None, freshness: float) -> str:
        return f"{client_ip or 'unknown'}:{int(freshness)}"

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if not self._enabled or request.method == "OPTIONS":
            return await call_next(request)

        scope = request.scope
        client = scope.get("client")
        client_ip = client[0] if isinstance(client, (tuple, list)) else None

        now = time.monotonic()
        window_start = now - (now % self._window_seconds)
        # Drop windows older than two windows, so a hammered table cannot grow.
        stale = [k for k, (start, _) in self._windows.items() if start < window_start - self._window_seconds]
        for key in stale:
            self._windows.pop(key, None)
            self._counts.pop(key, None)

        key = self._key(client_ip, window_start)
        if key not in self._windows:
            self._windows[key] = (window_start, [0, 0])
            self._counts[key] = [0, 0]

        limits = _buckets_for_path(
            self._general, self._heavy, self._heavy_prefixes, scope.get("path", "")
        )
        counts = self._counts[key]
        while len(counts) < len(limits):
            counts.append(0)

        for bucket, limit in enumerate(limits):
            if counts[bucket] >= limit:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": (
                            "Too many requests from this client. The limit "
                            "resets on a rolling per-minute window."
                        ),
                        "error_code": "rate_limited",
                    },
                )
        for bucket, _ in enumerate(limits):
            counts[bucket] += 1
        return await call_next(request)


__all__ = ["RateLimitMiddleware"]