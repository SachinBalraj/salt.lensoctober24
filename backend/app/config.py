from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_ONLY_JWT_SECRET = "dev-only-unsafe-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "SaltLens DSS API"
    app_version: str = "0.1.0"
    app_env: str = "development"

    backend_host: str = "0.0.0.0"
    backend_port: int = Field(default=8000, ge=1, le=65535)

    frontend_origin: str = "http://localhost:3000"

    #: The JWT signing secret. Empty (``""``) means "no token can ever be
    #: trusted", which is the safe default until Module 1 authentication is
    #: actually wired. A process running in ``APP_ENV=production`` refuses to
    #: start without a strong, non-default value.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = Field(default=60, ge=1)

    mongodb_uri: str | None = None
    database_name: str = Field(default="saltlens", min_length=1, max_length=63)
    mongodb_connect_timeout_ms: int = Field(default=2500, ge=100, le=120_000)
    mongodb_app_name: str = "saltlens-dss"

    ml_model_dir: str = "ml/models"
    enable_mock_simulations: bool = False

    #: Per-client request ceilings (defence in depth; there is no account layer
    #: to lean on). ``rate_limit_heavy_prefixes`` are the compute endpoints that
    #: train, explain or sweep, kept on a tighter budget than the read surface.
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = Field(default=600, ge=1, le=10_000)
    rate_limit_heavy_per_minute: int = Field(default=120, ge=0, le=10_000)
    rate_limit_heavy_prefixes: str = (
        "/api/ml,/api/evaluate,/api/explain,/api/simulator,/api/datasets,/api/risk/predict,/api/decisions/predict"
    )

    #: Master switch for sensor-sourced observations.
    #:
    #: No sensor gateway ships with this prototype, so this stays ``False`` and
    #: the API refuses ``data_source="sensor"`` rather than accepting readings
    #: that no instrument produced. Turning it on is a deliberate act that should
    #: only accompany a real, connected gateway.
    sensor_ingest_enabled: bool = False

    @field_validator("mongodb_uri", mode="before")
    @classmethod
    def _empty_uri_is_none(cls, value):
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
            if not value.lower().startswith(("mongodb://", "mongodb+srv://")):
                raise ValueError("MONGODB_URI must use mongodb:// or mongodb+srv://")
            return value
        return value

    @field_validator("database_name")
    @classmethod
    def _valid_database_name(cls, value: str) -> str:
        if any(character in value for character in ("/", "\\", ".", " ", '"')):
            raise ValueError("database_name must be a simple MongoDB database name")
        return value


def _secret_is_insecure(secret: str | None) -> bool:
    """A secret no deployment should trust: empty, or the old dev placeholder."""
    if not secret:
        return True
    return secret == DEV_ONLY_JWT_SECRET


def production_config_problems(settings: Settings) -> list[str]:
    """What stops this configuration from running as a production service.

    Returns an empty list when the configuration is acceptable for
    ``APP_ENV=production``, and a human-readable list otherwise. Called at
    startup so a production process fails fast instead of silently serving on a
    known secret, an HTTP origin or no database.
    """
    if settings.app_env != "production":
        return []
    problems: list[str] = []
    if _secret_is_insecure(settings.jwt_secret_key):
        problems.append(
            "JWT_SECRET_KEY must be set to a strong random value; the default "
            "and empty values are not acceptable in production."
        )
    if not settings.frontend_origin.startswith("https://"):
        problems.append(
            "FRONTEND_ORIGIN must be an https:// origin in production."
        )
    if not settings.mongodb_uri:
        problems.append("MONGODB_URI must be configured in production.")
    if not settings.rate_limit_enabled:
        problems.append("RATE_LIMIT_ENABLED is false in production.")
    return problems


def configuration_warnings(settings: Settings) -> list[str]:
    """Advisories that matter in development but are not fatal there.

    Mirrors :func:`production_config_problems` for the insecure secret outside
    production, so a developer is told the token layer (Module 1) cannot be
    trusted while it is still harmless to run without it.
    """
    if _secret_is_insecure(settings.jwt_secret_key):
        return [
            (
                "JWT_SECRET_KEY is missing or is the development placeholder; no "
                "access token can be issued or verified until a real secret is set. "
                "This is fine for local development and fatal in APP_ENV=production."
            )
        ]
    return []


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
