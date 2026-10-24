"""Register Open-Meteo provider on import."""

from app.services.open_meteo import open_meteo_provider
from app.services.weather_providers import provider_registry


def register_open_meteo() -> None:
    try:
        if open_meteo_provider.name not in provider_registry:
            provider_registry.register(open_meteo_provider)
    except Exception:
        pass


register_open_meteo()
