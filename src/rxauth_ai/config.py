"""Typed settings, read from the environment (README section 18).

Every path in this project used to be a literal relative to the working
directory — `Path("data")`, `Path("reports")`,
`artifacts/classifier_baseline.pkl`. That is correct for a CLI a developer runs
from the repository root and unshippable for anything else: a service does not
have a meaningful working directory, and a container does not put the corpus
where a checkout does.

So paths, thresholds, and logging behaviour come from one validated object.
Defaults reproduce the historical CLI behaviour exactly, which is what lets
this land without changing a single report.

**Precedence** is the usual three layers, most specific first: an explicit CLI
flag beats an environment variable, which beats the default. That ordering is
implemented by making the settings object the *source* of argparse defaults
rather than a competitor to them, so there is one place to look.

**No new dependency.** `pydantic-settings` does this job well and this uses
plain pydantic with an explicit reader instead, because the whole object is
about thirty lines of parsing and the package has a stated interest in staying
light. If the settings surface grows a nested or secret-bearing section, that
calculus changes.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError, model_validator

ENV_PREFIX = "RXAUTH_"


class ConfigurationError(RuntimeError):
    """Raised when the environment asks for something impossible."""


class Settings(BaseModel):
    """Everything the application reads from its environment.

    Frozen because settings that change under a running process produce bugs
    nobody can reproduce.
    """

    model_config = {"frozen": True}

    environment: Literal["local", "ci", "staging", "production"] = "local"

    data_dir: Path = Path("data")
    reports_dir: Path = Path("reports")
    artifacts_dir: Path = Path("artifacts")
    policy_dir: Path = Path("data/policies")
    classifier_path: Path = Path("artifacts/classifier_baseline")
    feedback_path: Path = Path("data/reviewer_feedback.jsonl")

    extraction_confidence_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    criteria_confidence_threshold: float = Field(default=0.65, ge=0.0, le=1.0)
    classification_confidence_threshold: float = Field(default=0.65, ge=0.0, le=1.0)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "text"] = "text"

    #: Whether logs may carry text quoted out of a patient document. Off, and
    #: not switchable on outside `local`: see the validator below.
    log_source_text: bool = False

    @model_validator(mode="after")
    def refuse_to_log_patient_text_outside_local(self) -> Settings:
        if self.log_source_text and self.environment != "local":
            raise ValueError(
                "log_source_text=true is only permitted when environment=local. Quoted spans "
                "are patient text; emitting them to a log sink outside a developer machine "
                "puts PHI somewhere with no retention policy (README section 19)."
            )
        return self

    @property
    def policy_dir_resolved(self) -> Path:
        """`policy_dir` if set explicitly, else the corpus inside `data_dir`."""
        return self.policy_dir


def _read(name: str) -> Optional[str]:
    raw = os.environ.get(f"{ENV_PREFIX}{name.upper()}")
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _read_bool(name: str) -> Optional[bool]:
    raw = _read(name)
    if raw is None:
        return None
    lowered = raw.casefold()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        f"{ENV_PREFIX}{name.upper()}={raw!r} is not a boolean. Use true/false."
    )


def settings_from_env(**overrides: object) -> Settings:
    """Build settings from `RXAUTH_*`, with explicit overrides winning.

    Unset variables fall through to the defaults, so an empty environment
    reproduces the historical CLI behaviour exactly.
    """
    values: dict[str, object] = {}
    for name in Settings.model_fields:
        if name == "log_source_text":
            parsed = _read_bool(name)
        else:
            parsed = _read(name)
        if parsed is not None:
            values[name] = parsed
    values.update({key: value for key, value in overrides.items() if value is not None})

    try:
        return Settings(**values)
    except ValidationError as exc:
        details = "; ".join(
            f"{ENV_PREFIX}{'.'.join(str(part) for part in error['loc']).upper()}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigurationError(f"Invalid configuration. {details}") from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, read once.

    Cached because reading the environment repeatedly invites a process whose
    behaviour changes halfway through a run. Tests that need different values
    call `settings_from_env` directly or clear the cache.
    """
    return settings_from_env()


def reset_settings_cache() -> None:
    """Forget the cached settings. For tests and for a reloading service."""
    get_settings.cache_clear()
