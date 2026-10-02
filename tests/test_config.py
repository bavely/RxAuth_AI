"""Tests for typed settings (roadmap Stage 2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from rxauth_ai.config import (
    ENV_PREFIX,
    ConfigurationError,
    Settings,
    get_settings,
    reset_settings_cache,
    settings_from_env,
)


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch):
    """No RXAUTH_* variable leaks between tests, or from the developer's shell."""
    for name in Settings.model_fields:
        monkeypatch.delenv(f"{ENV_PREFIX}{name.upper()}", raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


def test_an_empty_environment_reproduces_the_historical_cli_defaults():
    """This is what lets the settings layer land without moving a single report."""
    settings = settings_from_env()

    assert settings.data_dir == Path("data")
    assert settings.reports_dir == Path("reports")
    assert settings.policy_dir == Path("data/policies")
    assert settings.extraction_confidence_threshold == 0.65
    assert settings.environment == "local"


def test_environment_variables_override_defaults(monkeypatch):
    monkeypatch.setenv(f"{ENV_PREFIX}DATA_DIR", "/srv/corpus")
    monkeypatch.setenv(f"{ENV_PREFIX}LOG_FORMAT", "json")
    monkeypatch.setenv(f"{ENV_PREFIX}EXTRACTION_CONFIDENCE_THRESHOLD", "0.8")

    settings = settings_from_env()

    assert settings.data_dir == Path("/srv/corpus")
    assert settings.log_format == "json"
    assert settings.extraction_confidence_threshold == 0.8


def test_an_explicit_override_beats_the_environment(monkeypatch):
    """A CLI flag is more specific than a variable, so it wins."""
    monkeypatch.setenv(f"{ENV_PREFIX}REPORTS_DIR", "/from/env")

    settings = settings_from_env(reports_dir=Path("/from/flag"))

    assert settings.reports_dir == Path("/from/flag")


def test_an_unset_override_falls_through_rather_than_clobbering(monkeypatch):
    """argparse passes None for a flag nobody typed; that must not erase the env."""
    monkeypatch.setenv(f"{ENV_PREFIX}REPORTS_DIR", "/from/env")

    settings = settings_from_env(reports_dir=None)

    assert settings.reports_dir == Path("/from/env")


def test_an_out_of_range_threshold_is_refused_with_the_variable_named(monkeypatch):
    monkeypatch.setenv(f"{ENV_PREFIX}EXTRACTION_CONFIDENCE_THRESHOLD", "1.5")

    with pytest.raises(ConfigurationError) as caught:
        settings_from_env()

    assert "EXTRACTION_CONFIDENCE_THRESHOLD" in str(caught.value)


def test_an_unparseable_boolean_says_what_it_wanted(monkeypatch):
    monkeypatch.setenv(f"{ENV_PREFIX}LOG_SOURCE_TEXT", "maybe")

    with pytest.raises(ConfigurationError, match="not a boolean"):
        settings_from_env()


def test_an_unknown_log_level_is_refused(monkeypatch):
    monkeypatch.setenv(f"{ENV_PREFIX}LOG_LEVEL", "CHATTY")

    with pytest.raises(ConfigurationError):
        settings_from_env()


def test_logging_patient_text_is_refused_outside_a_developer_machine(monkeypatch):
    """A quoted span is PHI; a log sink has no retention policy (README §19)."""
    monkeypatch.setenv(f"{ENV_PREFIX}LOG_SOURCE_TEXT", "true")
    monkeypatch.setenv(f"{ENV_PREFIX}ENVIRONMENT", "production")

    with pytest.raises(ConfigurationError, match="only permitted when environment=local"):
        settings_from_env()


def test_logging_patient_text_is_allowed_locally(monkeypatch):
    monkeypatch.setenv(f"{ENV_PREFIX}LOG_SOURCE_TEXT", "true")

    assert settings_from_env().log_source_text is True


def test_settings_are_frozen_so_they_cannot_drift_mid_process():
    settings = settings_from_env()

    with pytest.raises(ValidationError):
        settings.data_dir = Path("/somewhere/else")


def test_settings_are_read_once_and_the_cache_can_be_cleared(monkeypatch):
    monkeypatch.setenv(f"{ENV_PREFIX}DATA_DIR", "/first")
    assert get_settings().data_dir == Path("/first")

    monkeypatch.setenv(f"{ENV_PREFIX}DATA_DIR", "/second")
    assert get_settings().data_dir == Path("/first"), "cached value should not move"

    reset_settings_cache()
    assert get_settings().data_dir == Path("/second")


def test_blank_variables_are_treated_as_unset(monkeypatch):
    """An empty variable in a compose file should not mean an empty path."""
    monkeypatch.setenv(f"{ENV_PREFIX}DATA_DIR", "   ")

    assert settings_from_env().data_dir == Path("data")


def test_deployed_environments_require_complete_oidc_configuration():
    with pytest.raises(ConfigurationError, match="AUTH_ENABLED"):
        settings_from_env(environment="production", s3_bucket="rxauth-docs")

    with pytest.raises(ConfigurationError, match="AUTH_JWKS_URL"):
        settings_from_env(
            environment="production",
            s3_bucket="rxauth-docs",
            auth_enabled=True,
            auth_issuer="https://identity.example.test/",
            auth_audience="rxauth-api",
        )


def test_authentication_accepts_only_configured_asymmetric_algorithms():
    with pytest.raises(ConfigurationError, match="asymmetric algorithms"):
        settings_from_env(
            auth_enabled=True,
            auth_issuer="https://identity.example.test/",
            auth_audience="rxauth-api",
            auth_jwks_url="https://identity.example.test/jwks.json",
            auth_algorithms="HS256",
        )


def test_auth_algorithm_list_is_fixed_by_configuration():
    settings = settings_from_env(
        auth_enabled=True,
        auth_issuer="https://identity.example.test/",
        auth_audience="rxauth-api",
        auth_jwks_url="https://identity.example.test/jwks.json",
        auth_algorithms="RS256,ES256",
    )

    assert settings.auth_algorithm_list == ("RS256", "ES256")


def test_production_requires_compliance_object_lock_mode():
    with pytest.raises(ConfigurationError, match="S3_OBJECT_LOCK_MODE=COMPLIANCE"):
        settings_from_env(
            environment="production",
            s3_bucket="rxauth-docs",
            s3_object_lock_mode="GOVERNANCE",
            auth_enabled=True,
            auth_issuer="https://identity.example.test/",
            auth_audience="rxauth-api",
            auth_jwks_url="https://identity.example.test/jwks.json",
            job_retry_initial_seconds=1800,
            job_retry_max_seconds=3600,
            job_lease_seconds=3600,
        )


def test_confirmed_retry_and_object_lock_defaults_are_encoded():
    settings = settings_from_env()

    assert settings.job_max_attempts == 3
    assert settings.job_retry_initial_seconds == 30 * 60
    assert settings.job_retry_max_seconds == 60 * 60
    assert settings.job_lease_seconds == 15 * 60
    assert settings.job_heartbeat_seconds == 5 * 60
    assert settings.s3_object_lock_mode == "COMPLIANCE"


def test_job_heartbeat_must_be_shorter_than_the_lease():
    with pytest.raises(ConfigurationError, match="JOB_HEARTBEAT_SECONDS"):
        settings_from_env(job_lease_seconds=900, job_heartbeat_seconds=900)


def test_deployed_environments_require_postgresql():
    common = {
        "environment": "staging",
        "s3_bucket": "rxauth-docs",
        "auth_enabled": True,
        "auth_issuer": "https://identity.example.test/",
        "auth_audience": "rxauth-api",
        "auth_jwks_url": "https://identity.example.test/jwks.json",
        "job_retry_initial_seconds": 1800,
        "job_retry_max_seconds": 3600,
        "job_lease_seconds": 3600,
    }
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        settings_from_env(**common)
    with pytest.raises(ConfigurationError, match="PostgreSQL"):
        settings_from_env(database_url="sqlite:///rxauth.db", **common)

    configured = settings_from_env(
        database_url="postgresql+psycopg://rxauth:secret@db.example.test/rxauth",
        **common,
    )
    assert configured.database_url.startswith("postgresql")


# --- Browser origins -------------------------------------------------------


_DEPLOYED = {
    "environment": "staging",
    "database_url": "postgresql+psycopg://rxauth:secret@db.example.test/rxauth",
    "s3_bucket": "rxauth-docs",
    "auth_enabled": True,
    "auth_issuer": "https://identity.example.test/",
    "auth_audience": "rxauth-api",
    "auth_jwks_url": "https://identity.example.test/jwks.json",
    "job_retry_initial_seconds": 1800,
    "job_retry_max_seconds": 3600,
    "job_lease_seconds": 3600,
}


def test_no_browser_origin_is_configured_by_default():
    """The CLI and the worker are not browsers. Silence is the right default."""
    assert settings_from_env().cors_origin_list == ()


def test_origins_parse_in_order_with_blanks_dropped():
    settings = settings_from_env(
        cors_allowed_origins="https://reviewer.example.test, ,https://admin.example.test"
    )

    assert settings.cors_origin_list == (
        "https://reviewer.example.test",
        "https://admin.example.test",
    )


def test_a_wildcard_origin_is_refused_in_a_deployed_environment():
    """A stolen token plus `*` is any website reading patient documents."""
    with pytest.raises(ConfigurationError, match="wildcard"):
        settings_from_env(cors_allowed_origins="*", **_DEPLOYED)


def test_a_wildcard_origin_remains_available_locally():
    settings = settings_from_env(cors_allowed_origins="*")

    assert settings.cors_origin_list == ("*",)


def test_a_plain_http_origin_is_refused_in_a_deployed_environment():
    with pytest.raises(ConfigurationError, match="https"):
        settings_from_env(cors_allowed_origins="http://reviewer.example.test", **_DEPLOYED)

    configured = settings_from_env(
        cors_allowed_origins="https://reviewer.example.test", **_DEPLOYED
    )
    assert configured.cors_origin_list == ("https://reviewer.example.test",)


def test_localhost_over_http_still_works_for_development():
    settings = settings_from_env(cors_allowed_origins="http://localhost:3000")

    assert settings.cors_origin_list == ("http://localhost:3000",)


def test_something_that_is_not_an_origin_is_refused_everywhere():
    """A browser matches scheme://host[:port] and nothing else.

    A trailing slash or a path never matches any request, so accepting one
    would produce a policy that silently blocks every call it was meant to
    allow.
    """
    with pytest.raises(ConfigurationError, match="not an origin"):
        settings_from_env(cors_allowed_origins="reviewer.example.test")
    with pytest.raises(ConfigurationError, match="path"):
        settings_from_env(cors_allowed_origins="https://reviewer.example.test/app")
    with pytest.raises(ConfigurationError, match="path"):
        settings_from_env(cors_allowed_origins="https://reviewer.example.test/")
    with pytest.raises(ConfigurationError, match="http or https"):
        settings_from_env(cors_allowed_origins="ftp://reviewer.example.test")
