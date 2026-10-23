"""The Cognito trigger that puts the tenant into access tokens (task 201) fails closed.

The trigger ships as a standalone Lambda without the application package, so it is loaded from
its file. Its organization pattern is a copy of `auth.SAFE_ID_PATTERN`; a drift between the two
would let the provider sign a tenant the API then refuses, or the reverse, so it is pinned here.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from rxauth_ai.auth import SAFE_ID_PATTERN

_TRIGGER = (
    Path(__file__).resolve().parents[1]
    / "infra"
    / "modules"
    / "cognito-identity"
    / "lambda"
    / "pre_token_generation.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("pre_token_generation", _TRIGGER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(attributes):
    return {
        "triggerSource": "TokenGeneration_HostedAuth",
        "request": {"userAttributes": attributes, "groupConfiguration": {}},
        "response": {"claimsAndScopeOverrideDetails": None},
    }


def test_the_organization_becomes_a_claim_on_the_access_token_only():
    trigger = _load()

    result = trigger.handler(_event({"sub": "u-1", "custom:org_id": "synthetic-org-a"}), None)

    details = result["response"]["claimsAndScopeOverrideDetails"]
    assert details["accessTokenGeneration"] == {
        "claimsToAddOrOverride": {"org_id": "synthetic-org-a"}
    }
    # Without a tenant, an ID token can never pass the API's required-claim check.
    assert "idTokenGeneration" not in details


def test_the_claim_name_follows_the_configured_organization_claim(monkeypatch):
    trigger = _load()
    monkeypatch.setenv("ORGANIZATION_CLAIM", "tenant")

    result = trigger.handler(_event({"custom:org_id": "synthetic-org-a"}), None)

    claims = result["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"]
    assert claims["claimsToAddOrOverride"] == {"tenant": "synthetic-org-a"}


@pytest.mark.parametrize("organization", [None, "", "../org-b", "org a", "x" * 129])
def test_a_user_without_a_safe_organization_gets_no_token(organization):
    trigger = _load()
    attributes = {"sub": "u-1"}
    if organization is not None:
        attributes["custom:org_id"] = organization

    with pytest.raises(trigger.MissingOrganizationError) as raised:
        trigger.handler(_event(attributes), None)

    # The refusal names the attribute, never the value it rejected.
    if organization:
        assert organization not in str(raised.value)


def test_the_trigger_pattern_matches_the_api_pattern():
    assert _load().SAFE_ID_PATTERN == SAFE_ID_PATTERN
