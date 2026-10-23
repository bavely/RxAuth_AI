"""Cognito pre-token-generation trigger: put the tenant into the access token (task 201).

Cognito access tokens carry `sub`, `client_id` and `cognito:groups`, but never a custom user
attribute, and the API takes the organization from the access token alone (`auth.py`). This
trigger copies the immutable `custom:org_id` attribute into an `org_id` claim on the access
token only. The ID token is left without one, so even a misconfigured API cannot accept an ID
token as a tenant-scoped credential.

It fails closed. A user with no organization, or one that is not a safe identifier, gets no
token at all, because Cognito refuses to sign in a user whose trigger raises. A token without a
tenant would be rejected by the API anyway; refusing here keeps the failure at the provider,
where it is visible to whoever provisioned the account.

Nothing from the event is logged or echoed: it holds the user's attributes.
"""

from __future__ import annotations

import os
import re
from typing import Any

#: Mirrors `rxauth_ai.auth.SAFE_ID_PATTERN`; `tests/test_pre_token_generation.py` fails if they
#: drift. Copied rather than imported because the Lambda ships without the application package.
SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"

ORGANIZATION_ATTRIBUTE = "custom:org_id"


class MissingOrganizationError(Exception):
    """The user has no usable organization, so no token may be issued."""


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    claim = os.environ.get("ORGANIZATION_CLAIM", "org_id")
    organization = event.get("request", {}).get("userAttributes", {}).get(ORGANIZATION_ATTRIBUTE)
    if not isinstance(organization, str) or not re.fullmatch(SAFE_ID_PATTERN, organization):
        raise MissingOrganizationError(
            f"User has no valid {ORGANIZATION_ATTRIBUTE}; refusing to issue tokens."
        )

    event.setdefault("response", {})["claimsAndScopeOverrideDetails"] = {
        "accessTokenGeneration": {"claimsToAddOrOverride": {claim: organization}},
    }
    return event
