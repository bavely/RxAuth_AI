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
