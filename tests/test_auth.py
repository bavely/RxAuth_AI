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


def _cognito_authenticator(monkeypatch, claims):
    settings = settings_from_env(
        auth_enabled=True,
        auth_issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example",
        auth_audience="reviewer-ui-client",
        auth_audience_claim="client_id",
        auth_required_token_use="access",
        auth_jwks_url="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example/jwks",
        auth_roles_claim="cognito:groups",
    )
    authenticator = JWTAuthenticator(settings)
    calls = {}
    monkeypatch.setattr(authenticator._jwks, "get_signing_key_from_jwt", lambda token: object())

    # Enforces `require` like PyJWT does; lifetime claims are present in every token here.
    issued = {"exp": 2_000_000_000, "iat": 1_900_000_000, **claims}

    def decode(token, key, **kwargs):
        calls.update(kwargs)
        missing = [name for name in kwargs["options"]["require"] if name not in issued]
        if missing:
            raise authenticator._jwt.MissingRequiredClaimError(missing[0])
        return issued

    monkeypatch.setattr(authenticator._jwt, "decode", decode)
    return authenticator, calls


def test_a_cognito_access_token_is_matched_on_its_client_id(monkeypatch):
    authenticator, calls = _cognito_authenticator(
        monkeypatch,
        {
            "sub": "reviewer-a1-sub",
            "client_id": "reviewer-ui-client",
            "token_use": "access",
            "org_id": "synthetic-org-a",
            "cognito:groups": ["case:read", "case:review"],
        },
    )

    principal = authenticator.authenticate("signed-token")

    assert principal.subject == "reviewer-a1-sub"
    assert principal.organization_id == "synthetic-org-a"
    assert principal.roles == {"case:read", "case:review"}
    # The audience check moves from PyJWT to an exact comparison; it is never dropped.
    assert calls["audience"] is None
    assert calls["options"]["verify_aud"] is False
    assert "client_id" in calls["options"]["require"]
    assert "token_use" in calls["options"]["require"]


def test_a_token_for_another_client_is_rejected(monkeypatch):
    authenticator, _ = _cognito_authenticator(
        monkeypatch,
        {
            "sub": "user-1",
            "client_id": "some-other-client",
            "token_use": "access",
            "org_id": "synthetic-org-a",
        },
    )

    with pytest.raises(AuthenticationError, match="Invalid or missing bearer token"):
        authenticator.authenticate("signed-token")


def test_an_id_token_cannot_stand_in_for_an_access_token(monkeypatch):
    # A Cognito ID token names its client in `aud` and carries no `client_id`.
    authenticator, _ = _cognito_authenticator(
        monkeypatch,
        {"sub": "user-1", "aud": "reviewer-ui-client", "org_id": "synthetic-org-a"},
    )

    with pytest.raises(AuthenticationError):
        authenticator.authenticate("signed-id-token")


@pytest.mark.parametrize("token_use", ["id", None])
def test_only_an_access_token_is_accepted_when_token_use_is_required(monkeypatch, token_use):
    # Defence in depth: even a token carrying the right client_id is refused unless it says
    # it is an access token, so the audience setting alone never decides token type.
    claims = {"sub": "user-1", "client_id": "reviewer-ui-client", "org_id": "synthetic-org-a"}
    if token_use is not None:
        claims["token_use"] = token_use
    authenticator, _ = _cognito_authenticator(monkeypatch, claims)

    with pytest.raises(AuthenticationError):
        authenticator.authenticate("signed-token")


def test_verify_token_prints_the_principal_and_never_the_token(monkeypatch, capsys):
    import io

    from rxauth_ai import auth

    settings = settings_from_env(
        auth_enabled=True,
        auth_issuer="https://identity.example.test/",
        auth_audience="rxauth-api",
        auth_jwks_url="https://identity.example.test/jwks.json",
    )
    seen = {}

    class FakeAuthenticator:
        def __init__(self, configured):
            seen["settings"] = configured

        def authenticate(self, token):
            seen["token"] = token
            return Principal(subject="reviewer-7", organization_id="org-a", roles={"case:read"})

    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "JWTAuthenticator", FakeAuthenticator)
    monkeypatch.setattr("sys.stdin", io.StringIO("secret-token-value\n"))

    auth.main([])

    output = capsys.readouterr().out
    assert seen == {"settings": settings, "token": "secret-token-value"}
    assert '"subject": "reviewer-7"' in output
    assert '"organization_id": "org-a"' in output
    assert "secret-token-value" not in output


def test_verify_token_reports_a_rejection_without_detail(monkeypatch):
    import io

    from rxauth_ai import auth

    settings = settings_from_env(
        auth_enabled=True,
        auth_issuer="https://identity.example.test/",
        auth_audience="rxauth-api",
        auth_jwks_url="https://identity.example.test/jwks.json",
    )

    class RejectingAuthenticator:
        def __init__(self, configured):
            pass

        def authenticate(self, token):
            raise AuthenticationError()

    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    monkeypatch.setattr(auth, "JWTAuthenticator", RejectingAuthenticator)
    monkeypatch.setattr("sys.stdin", io.StringIO("bad-token"))

    with pytest.raises(SystemExit, match="Rejected: Invalid or missing bearer token"):
        auth.main([])


def test_verify_token_refuses_to_run_without_a_provider(monkeypatch):
    from rxauth_ai import auth

    monkeypatch.setattr(auth, "get_settings", lambda: settings_from_env(auth_enabled=False))

    with pytest.raises(SystemExit, match="RXAUTH_AUTH_ENABLED is false"):
        auth.main([])


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
