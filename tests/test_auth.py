"""Unit contracts for verified principals and OIDC JWT claim mapping."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rxauth_ai.auth import (
    ROLE_ADMIN,
    ROLE_CASE_READ,
    AuthenticationError,
    JWTAuthenticator,
    Principal,
)
from rxauth_ai.config import settings_from_env


def test_principal_rejects_an_organization_identifier_that_can_traverse_paths():
    with pytest.raises(ValueError):
        Principal(subject="user-1", organization_id="..", roles={ROLE_CASE_READ})


def test_admin_is_authorized_for_every_role():
    principal = Principal(subject="admin-1", organization_id="org-a", roles={ROLE_ADMIN})

    assert principal.permits_any(frozenset({"some:new-role"}))


def test_jwt_authenticator_maps_verified_subject_organization_and_roles(monkeypatch):
    settings = settings_from_env(
        auth_enabled=True,
        auth_issuer="https://identity.example.test/",
        auth_audience="rxauth-api",
        auth_jwks_url="https://identity.example.test/jwks.json",
    )
    authenticator = JWTAuthenticator(settings)
    calls = {}

    monkeypatch.setattr(
        authenticator._jwks,
        "get_signing_key_from_jwt",
        lambda token: calls.setdefault("token", token) or object(),
    )

    def decode(token, key, **kwargs):
        calls.update(token=token, key=key, kwargs=kwargs)
        return {
            "sub": "reviewer-7",
            "org_id": "org-a",
            "roles": ["case:read", "case:review"],
        }

    monkeypatch.setattr(authenticator._jwt, "decode", decode)

    principal = authenticator.authenticate("signed-token")

    assert principal.subject == "reviewer-7"
    assert principal.organization_id == "org-a"
    assert principal.roles == {"case:read", "case:review"}
    assert calls["kwargs"]["issuer"] == settings.auth_issuer
    assert calls["kwargs"]["audience"] == settings.auth_audience
    assert calls["kwargs"]["algorithms"] == ["RS256"]
    assert calls["kwargs"]["options"]["require"] == ["exp", "iat", "sub", "org_id"]


def test_jwt_authenticator_returns_one_stable_error_for_invalid_tokens(monkeypatch):
    settings = settings_from_env(
        auth_enabled=True,
        auth_issuer="https://identity.example.test/",
        auth_audience="rxauth-api",
        auth_jwks_url="https://identity.example.test/jwks.json",
    )
    authenticator = JWTAuthenticator(settings)
    monkeypatch.setattr(
        authenticator,
        "_jwks",
        SimpleNamespace(
            get_signing_key_from_jwt=lambda _token: (_ for _ in ()).throw(
                authenticator._jwt.InvalidTokenError("provider detail")
            )
        ),
    )

    with pytest.raises(AuthenticationError, match="Invalid or missing bearer token"):
        authenticator.authenticate("bad-token")


def test_jwt_authenticator_rejects_a_missing_token_without_contacting_jwks():
    settings = settings_from_env(
        auth_enabled=True,
        auth_issuer="https://identity.example.test/",
        auth_audience="rxauth-api",
        auth_jwks_url="https://identity.example.test/jwks.json",
    )
    authenticator = JWTAuthenticator(settings)

    with pytest.raises(AuthenticationError):
        authenticator.authenticate(None)
