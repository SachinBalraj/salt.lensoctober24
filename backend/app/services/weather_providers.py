"""Module 4 - the seam where a real weather API will be plugged in.

Manual entry is the only way a weather record enters SaltLens today. This
module exists so that adding a real forecast API later is an additive change:
implement :class:`WeatherProvider`, register it at start-up, and the ingest
route, the storage path, the API contract and the operator UI already work.

The registry deliberately ships **empty**. An empty registry is the honest
description of this deployment, and it is what makes
``POST /api/weather/ingest/{provider}`` answer 404 instead of inventing a
forecast. Registering a provider is a deliberate act that should accompany real
credentials, not something a default can do on the operator's behalf.

Provenance is enforced structurally: a provider may only stamp
``weather_api``, and it is the ingest route, not the caller, that sets the
source. No request body can make a record look like it came from an API.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.models.enums import WeatherSource
from app.models.salt_pan import SaltPan

#: Provider names are used in a URL path, so they are restricted to a safe set.
PROVIDER_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,62}$"


class WeatherProviderError(RuntimeError):
    """A provider could not answer.

    Raised by a provider implementation. The ingest route reports it as a bad
    gateway so an upstream fault is never mistaken for a SaltLens bug or, worse,
    for an absence of weather data.
    """


@dataclass(frozen=True)
class ForecastWindow:
    """The period a caller wants covered."""

    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError("forecast window end must not precede its start")


@dataclass(frozen=True)
class ProviderForecastPoint:
    """One forecast as a provider states it, before SaltLens stores it.

    Every quantity is optional because providers differ in what they publish.
    A provider that returns only a probability still yields a usable record;
    SaltLens stores what was supplied and leaves the rest absent rather than
    filling the gaps.
    """

    forecast_for: datetime
    rainfall_mm: float | None = None
    rainfall_probability_percent: float | None = None
    ambient_temperature_c: float | None = None
    humidity_percent: float | None = None


@runtime_checkable
class WeatherProvider(Protocol):
    """What a weather data source must be able to do.

    Implementations are expected to be stateless with respect to SaltLens: they
    translate an upstream API into :class:`ProviderForecastPoint` values and
    raise :class:`WeatherProviderError` when they cannot answer. They must not
    write to the database; the ingest route owns persistence so that provenance
    is applied in exactly one place.
    """

    @property
    def name(self) -> str:
        """Stable identifier used in the ingest URL, e.g. ``openweather``."""

    @property
    def source(self) -> WeatherSource:
        """The provenance this provider's readings are stored under.

        Must be :attr:`WeatherSource.WEATHER_API`. A provider that reports
        anything else would file instrument or operator data under an API
        label, so registration refuses it.
        """

    async def available(self) -> bool:
        """Whether this provider can answer right now.

        Checked before every ingest, so a provider whose credentials were
        revoked reports its own unavailability instead of failing mid-request.
        """

    async def fetch_forecast(
        self,
        pan: SaltPan,
        window: ForecastWindow,
    ) -> Sequence[ProviderForecastPoint]:
        """Return the forecast for one pan, or raise :class:`WeatherProviderError`."""


class WeatherProviderRegistry:
    """The set of weather providers this deployment can reach."""

    def __init__(self) -> None:
        self._providers: dict[str, WeatherProvider] = {}

    def register(self, provider: WeatherProvider) -> None:
        name = provider.name
        if not isinstance(name, str) or not name:
            raise ValueError("a weather provider must have a non-empty name")
        if provider.source is not WeatherSource.WEATHER_API:
            raise ValueError(
                f"provider {name!r} reports source {provider.source!r}; a provider "
                "must report weather_api so its readings are labelled with the "
                "system that produced them"
            )
        if name in self._providers:
            raise ValueError(f"a weather provider named {name!r} is already registered")
        self._providers[name] = provider

    def get(self, name: str) -> WeatherProvider | None:
        return self._providers.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def all(self) -> tuple[WeatherProvider, ...]:
        return tuple(self._providers[name] for name in self.names())

    def clear(self) -> None:
        """Drop every registration. Used by tests to isolate the registry."""
        self._providers.clear()

    def __len__(self) -> int:
        return len(self._providers)

    def __contains__(self, name: object) -> bool:
        return name in self._providers


#: The deployment-wide registry. Intentionally empty on purpose: no weather API
#: is connected to this research prototype, and the empty state is what the
#: status endpoint and the ingest route report.
provider_registry = WeatherProviderRegistry()


__all__ = [
    "PROVIDER_NAME_PATTERN",
    "ForecastWindow",
    "ProviderForecastPoint",
    "WeatherProvider",
    "WeatherProviderError",
    "WeatherProviderRegistry",
    "provider_registry",
]
