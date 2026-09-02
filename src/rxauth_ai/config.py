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

    #: SQLAlchemy URL. Unset means "no database": the CLI keeps working exactly
    #: as it always has, and only the service layer requires one.
    database_url: Optional[str] = None
    database_echo: bool = False

    #: OIDC access-token validation. The issuer, audience, and JWKS URL are
    #: deployment configuration rather than provider-specific code, so Entra,
    #: Cognito, Auth0, Keycloak, and other compliant providers use the same
    #: boundary. Local development has an explicit synthetic principal.
    auth_enabled: bool = False
    auth_issuer: Optional[str] = None
    auth_audience: Optional[str] = None
    auth_jwks_url: Optional[str] = None
    auth_algorithms: str = "RS256"
    auth_organization_claim: str = "org_id"
    auth_roles_claim: str = "roles"
    auth_clock_skew_seconds: int = Field(default=30, ge=0, le=300)
    auth_jwks_cache_seconds: int = Field(default=300, ge=30, le=86400)
    auth_jwks_timeout_seconds: float = Field(default=5.0, ge=0.1, le=30.0)
    local_auth_subject: str = "local-developer"
    local_auth_organization: str = "local"

    #: Object storage. Credentials are read by boto3 from the standard AWS
    #: chain (environment, shared config, instance role) and are deliberately
    #: not fields here — a secret in a settings object is a secret one
    #: `repr()` away from a log line.
    s3_bucket: Optional[str] = None
    s3_region: Optional[str] = None
    s3_endpoint_url: Optional[str] = Field(
        default=None, description="Set for S3-compatible storage; unset for AWS itself."
    )
    s3_prefix: str = "cases"
    s3_object_lock_mode: Literal["GOVERNANCE", "COMPLIANCE"] = "COMPLIANCE"

    #: Local directory used when no bucket is configured. This is a developer
    #: and test convenience, not a deployment target.
    local_storage_dir: Path = Path("artifacts/object-store")

    #: Upload resource limits. These defaults are intentionally conservative
    #: for an OCR-heavy service and remain environment-overridable after load
    #: testing with representative pharmacy packets.
    upload_max_file_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    upload_max_case_bytes: int = Field(default=250 * 1024 * 1024, ge=1024)
    upload_max_documents_per_case: int = Field(default=20, ge=1, le=500)
    upload_max_pdf_pages: int = Field(default=100, ge=1, le=5000)
    upload_max_image_pixels: int = Field(default=50_000_000, ge=1_000_000)
    upload_chunk_bytes: int = Field(default=1024 * 1024, ge=64 * 1024, le=8 * 1024 * 1024)
    upload_multipart_overhead_bytes: int = Field(
        default=1024 * 1024, ge=64 * 1024, le=8 * 1024 * 1024
    )

    #: Retention contracts. Calendar years are kept as years rather than
    #: approximated day counts so leap years do not shorten a legal hold.
    original_document_retention_years: int = Field(default=10, ge=1, le=100)
    temporary_copy_retention_hours: int = Field(default=72, ge=1, le=24 * 30)
    completed_job_retention_years: int = Field(default=6, ge=1, le=100)
    failed_job_retention_days: int = Field(default=90, ge=1, le=3650)

    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_retry_initial_seconds: float = Field(default=30 * 60, ge=0.0, le=86400.0)
    job_retry_max_seconds: float = Field(default=60 * 60, ge=0.0, le=604800.0)
    job_lease_seconds: float = Field(default=15 * 60, ge=60.0, le=86400.0)
    job_heartbeat_seconds: float = Field(default=5 * 60, ge=1.0, le=28800.0)
    job_poll_seconds: float = Field(default=1.0, ge=0.05, le=60.0)

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

    @model_validator(mode="after")
    def refuse_local_storage_in_a_deployed_environment(self) -> Settings:
        if self.s3_bucket is None and self.environment in {"staging", "production"}:
            raise ValueError(
                "No RXAUTH_S3_BUCKET is configured. Falling back to local disk in "
                f"environment={self.environment} would put uploaded documents on an "
                "ephemeral filesystem with no encryption, retention, or audit trail "
                "(README section 19)."
            )
        return self

    @model_validator(mode="after")
    def require_complete_authentication_outside_local(self) -> Settings:
        deployed = self.environment in {"staging", "production"}
        if deployed and not self.auth_enabled:
            raise ValueError(f"environment={self.environment} requires RXAUTH_AUTH_ENABLED=true.")
        required = {
            "RXAUTH_AUTH_ISSUER": self.auth_issuer,
            "RXAUTH_AUTH_AUDIENCE": self.auth_audience,
            "RXAUTH_AUTH_JWKS_URL": self.auth_jwks_url,
        }
        missing = [name for name, value in required.items() if not value]
        if self.auth_enabled and missing:
            raise ValueError(
                "Authentication is enabled but required settings are missing: " + ", ".join(missing)
            )
        allowed_algorithms = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "EdDSA"}
        configured = self.auth_algorithm_list
        if not configured or not set(configured).issubset(allowed_algorithms):
            raise ValueError(
                "RXAUTH_AUTH_ALGORITHMS must contain only asymmetric algorithms: "
                + ", ".join(sorted(allowed_algorithms))
            )
        return self

    @model_validator(mode="after")
    def require_compliance_object_lock_in_production(self) -> Settings:
        if self.environment == "production" and self.s3_object_lock_mode != "COMPLIANCE":
            raise ValueError(
                "Production document retention requires RXAUTH_S3_OBJECT_LOCK_MODE=COMPLIANCE."
            )
        return self

    @model_validator(mode="after")
    def validate_job_timing_policy(self) -> Settings:
        if self.job_retry_initial_seconds > self.job_retry_max_seconds:
            raise ValueError(
                "RXAUTH_JOB_RETRY_INITIAL_SECONDS cannot exceed RXAUTH_JOB_RETRY_MAX_SECONDS."
            )
        if self.job_heartbeat_seconds >= self.job_lease_seconds:
            raise ValueError(
                "RXAUTH_JOB_HEARTBEAT_SECONDS must be shorter than RXAUTH_JOB_LEASE_SECONDS."
            )
        return self

    @model_validator(mode="after")
    def require_postgresql_outside_local(self) -> Settings:
        if self.environment not in {"staging", "production"}:
            return self
        if not self.database_url:
            raise ValueError(f"environment={self.environment} requires RXAUTH_DATABASE_URL.")
        if not self.database_url.casefold().startswith("postgresql"):
            raise ValueError("Staging and production require a PostgreSQL RXAUTH_DATABASE_URL.")
        return self

    @property
    def effective_job_retry_initial_seconds(self) -> float:
        return self.job_retry_initial_seconds

    @property
    def effective_job_retry_max_seconds(self) -> float:
        return self.job_retry_max_seconds

    @property
    def auth_algorithm_list(self) -> tuple[str, ...]:
        return tuple(part.strip() for part in self.auth_algorithms.split(",") if part.strip())

    @property
    def storage_is_local(self) -> bool:
        """True when uploads go to disk rather than to a bucket."""
        return self.s3_bucket is None

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
        if Settings.model_fields[name].annotation is bool:
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
