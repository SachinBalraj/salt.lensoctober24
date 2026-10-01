"""The production configuration guard and its development warnings."""

from __future__ import annotations

from app.config import (
    DEV_ONLY_JWT_SECRET,
    Settings,
    _secret_is_insecure,
    configuration_warnings,
    production_config_problems,
)


def test_the_old_default_and_empty_secrets_are_both_insecure() -> None:
    assert _secret_is_insecure("")
    assert _secret_is_insecure(DEV_ONLY_JWT_SECRET)
    assert not _secret_is_insecure("a-real-random-secret-9f37")


def test_production_with_no_secrets_reports_every_unaddressed_problem() -> None:
    settings = Settings(
        app_env="production",
        jwt_secret_key="",
        frontend_origin="http://localhost:3000",
        mongodb_uri=None,
        rate_limit_enabled=False,
    )
    problems = production_config_problems(settings)
    assert "JWT_SECRET_KEY" in "\n".join(problems)
    assert "https" in "\n".join(problems)
    assert "MONGODB_URI" in "\n".join(problems)
    assert "RATE_LIMIT_ENABLED" in "\n".join(problems)


def test_a_hardened_production_configuration_raises_no_problems() -> None:
    settings = Settings(
        app_env="production",
        jwt_secret_key="a-strong-random-production-secret-91b2",
        frontend_origin="https://saltlens.example.org",
        mongodb_uri="mongodb+srv://user:pass@cluster.example.net/?retryWrites=true",
        rate_limit_enabled=True,
    )
    assert production_config_problems(settings) == []


def test_the_insecure_secret_is_a_warning_outside_production_not_a_failure() -> None:
    development = Settings(
        app_env="development",
        jwt_secret_key="",
        mongodb_uri=None,
    )
    assert production_config_problems(development) == []
    assert configuration_warnings(development)
    assert not configuration_warnings(
        Settings(app_env="development", jwt_secret_key="a-real-secret-cc44")
    )