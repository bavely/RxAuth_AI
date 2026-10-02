"""Authentication identities and role-based authorization.

Deployed environments validate asymmetric JWT access tokens against an OIDC
provider's JWKS endpoint.  Local development uses one explicit synthetic
principal so the offline workflow remains easy to run without weakening the
staging/production boundary.

The organization claim is part of the principal, not request data.  Callers
must carry it into every filesystem, object-storage, job, and database lookup;
an authenticated user is not automatically authorized to another tenant's
resource merely because they know its identifier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .config import Settings

ROLE_CASE_READ = "case:read"
ROLE_CASE_WRITE = "case:write"
ROLE_REVIEW = "case:review"
ROLE_ADMIN = "admin"

ALL_APPLICATION_ROLES = frozenset({ROLE_CASE_READ, ROLE_CASE_WRITE, ROLE_REVIEW, ROLE_ADMIN})

SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"


class Principal(BaseModel):
    """A verified caller and the tenant boundary they are allowed to enter."""

    subject: str = Field(min_length=1, max_length=255)
    organization_id: str = Field(pattern=SAFE_ID_PATTERN)
    roles: frozenset[str] = Field(default_factory=frozenset)

    def permits_any(self, required: frozenset[str]) -> bool:
        return ROLE_ADMIN in self.roles or bool(self.roles.intersection(required))


@dataclass(frozen=True)
class AuthenticationError(Exception):
    """A bearer token is absent, invalid, expired, or missing identity claims."""

    detail: str = "Invalid or missing bearer token."

    def __str__(self) -> str:
        return self.detail


class Authenticator(Protocol):
    def authenticate(self, token: str | None) -> Principal: ...


class LocalDevelopmentAuthenticator:
    """One non-production identity, selected only when authentication is off."""

    def __init__(self, settings: Settings) -> None:
        self._principal = Principal(
            subject=settings.local_auth_subject,
            organization_id=settings.local_auth_organization,
            roles=ALL_APPLICATION_ROLES,
        )

    def authenticate(self, token: str | None) -> Principal:
        return self._principal


class JWTAuthenticator:
    """Validate OIDC access tokens with a cached JWKS client."""

    def __init__(self, settings: Settings) -> None:
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - deployment packaging failure
            raise RuntimeError(
                "JWT authentication requires the service dependencies. Install `pyjwt[crypto]`."
            ) from exc

        if not (settings.auth_issuer and settings.auth_audience and settings.auth_jwks_url):
            raise RuntimeError("JWT authentication settings are incomplete.")

        self._jwt = jwt
        self._issuer = settings.auth_issuer
        self._audience = settings.auth_audience
        self._organization_claim = settings.auth_organization_claim
        self._roles_claim = settings.auth_roles_claim
        self._algorithms = settings.auth_algorithm_list
        self._leeway = settings.auth_clock_skew_seconds
        self._jwks = jwt.PyJWKClient(
            settings.auth_jwks_url,
            cache_keys=True,
            lifespan=settings.auth_jwks_cache_seconds,
            timeout=settings.auth_jwks_timeout_seconds,
        )

    @staticmethod
    def _roles(value: Any) -> frozenset[str]:
        if isinstance(value, str):
            return frozenset(part for part in re.split(r"[\s,]+", value.strip()) if part)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return frozenset(value)
        return frozenset()

    def authenticate(self, token: str | None) -> Principal:
        if not token:
            raise AuthenticationError()

        try:
            signing_key = self._jwks.get_signing_key_from_jwt(token)
            claims = self._jwt.decode(
                token,
                signing_key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "sub", self._organization_claim]},
            )
            return Principal(
                subject=claims["sub"],
                organization_id=claims[self._organization_claim],
                roles=self._roles(claims.get(self._roles_claim)),
            )
        except (self._jwt.InvalidTokenError, self._jwt.PyJWKClientError, ValueError, TypeError):
            # Token/parser details can reveal provider configuration. The API returns
            # one stable response and records no token or claims in logs.
            raise AuthenticationError() from None


def build_authenticator(settings: Settings) -> Authenticator:
    if settings.auth_enabled:
        return JWTAuthenticator(settings)
    return LocalDevelopmentAuthenticator(settings)


__all__ = [
    "ALL_APPLICATION_ROLES",
    "AuthenticationError",
    "Authenticator",
    "JWTAuthenticator",
    "LocalDevelopmentAuthenticator",
    "Principal",
    "ROLE_ADMIN",
    "ROLE_CASE_READ",
    "ROLE_CASE_WRITE",
    "ROLE_REVIEW",
    "SAFE_ID_PATTERN",
    "build_authenticator",
]
