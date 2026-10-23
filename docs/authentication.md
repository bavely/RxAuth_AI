# Authentication, authorization, and tenant isolation

The API accepts OIDC access tokens signed with an explicitly configured
asymmetric algorithm. It resolves the signing key from the provider's JWKS,
then validates signature, expiration, issued-at time, issuer, and audience.
The token algorithm never selects the server's algorithm configuration.

## Deployment configuration

Staging and production require all of the following:

```text
RXAUTH_ENVIRONMENT=staging
RXAUTH_AUTH_ENABLED=true
RXAUTH_AUTH_ISSUER=https://identity.example.com/
RXAUTH_AUTH_AUDIENCE=rxauth-api
RXAUTH_AUTH_AUDIENCE_CLAIM=aud
# Optional: RXAUTH_AUTH_REQUIRED_TOKEN_USE=access (set for Cognito)
RXAUTH_AUTH_JWKS_URL=https://identity.example.com/.well-known/jwks.json
RXAUTH_AUTH_ALGORITHMS=RS256
RXAUTH_AUTH_ORGANIZATION_CLAIM=org_id
RXAUTH_AUTH_ROLES_CLAIM=roles
RXAUTH_S3_BUCKET=rxauth-staging-documents
RXAUTH_DATABASE_URL=postgresql+psycopg://...
```

`RXAUTH_AUTH_ALGORITHMS` is a server-side allow-list. Only asymmetric
algorithms are accepted. It must not be populated from the JWT header.

## Required claims

Every access token must contain:

- `sub`: stable pseudonymous user identifier; recorded as the reviewer ID.
- `exp` and `iat`: token lifetime.
- `iss`: matched exactly against `RXAUTH_AUTH_ISSUER`.
- The audience: `aud` by default, or the claim named by `RXAUTH_AUTH_AUDIENCE_CLAIM`
  (`client_id` for Cognito), matched exactly against `RXAUTH_AUTH_AUDIENCE`. A non-standard
  audience claim is required, not optional; the check moves, it is never skipped.
- `org_id` by default: organization/tenant identifier. The claim name is configurable.
- `roles` by default: an array or whitespace-delimited string of application roles.

Organization identifiers use `A-Z`, `a-z`, `0-9`, `_`, and `-`, begin with an
alphanumeric character, and are at most 128 characters. This contract makes
the verified claim safe to carry into filesystem and object-storage namespaces.

## Roles

| Role | Capability |
|---|---|
| `case:write` | Create cases, upload documents, and start runs; also read their results. |
| `case:read` | Read jobs, runs, and reviewer decisions. |
| `case:review` | Read results and append reviewer decisions. |
| `admin` | Explicit override for all application roles within the token's organization. |

Roles never bypass the organization boundary. An administrator for `org-a`
cannot access `org-b` unless the identity provider issues a token whose verified
organization claim is `org-b`.

## Local development

When `RXAUTH_ENVIRONMENT=local` and authentication is disabled, the service
uses `local-developer` in organization `local` with all roles. This is only for
synthetic local work. The settings validator refuses that mode in staging or
production.

## Provider setup checklist

1. Create an API audience for `rxauth-api` (or configure another audience).
2. Add organization and application-role claims to access tokens.
3. Grant roles through provider-managed groups or application assignments.
4. Keep access-token lifetimes short and use the frontend's authorization-code
   flow with PKCE.
5. Exercise 401, 403, expired-token, key-rotation, and cross-organization tests
   against the real staging identity provider before promotion.

## Staging identity provider: Amazon Cognito (task 201)

ADR 003 puts staging in its own AWS account in us-east-1; the identity provider is a Cognito
user pool defined in [`infra/modules/cognito-identity`](../infra/modules/cognito-identity) and
applied from [`infra/staging/identity`](../infra/staging/identity).

### How Cognito meets the token contract

| API requirement | Cognito source |
|---|---|
| Asymmetric signature, JWKS | RS256; `<issuer>/.well-known/jwks.json` |
| `iss` | `https://cognito-idp.us-east-1.amazonaws.com/<pool id>`, no trailing slash |
| Audience | Access tokens name the app client in `client_id`, so `RXAUTH_AUTH_AUDIENCE_CLAIM=client_id`. The claim must be one of `aud`, `client_id` or `azp`; nothing a user can write is accepted |
| Token type | `RXAUTH_AUTH_REQUIRED_TOKEN_USE=access` requires `token_use: access`, so an ID token is refused even if the audience setting is wrong |
| `sub` | Cognito's stable user identifier, recorded as the reviewer ID |
| Organization | Copied from the immutable `custom:org_id` attribute into the **access token only** as `org_id`, by the pre-token-generation trigger (Essentials tier, V2 event). ID tokens carry no organization, which is a second reason they can never call the API. A user without a valid organization gets no token |
| Roles | `cognito:groups`; the pool has one group per application role (`case:read`, `case:write`, `case:review`, `admin`) |

Controls built into the pool: administrator-created accounts only (no self-sign-up), TOTP MFA
required, a 14-character password policy, `custom:org_id` immutable after creation and not
writable by the app client, 15-minute access tokens with revocation enabled, and a confidential
authorization-code client for the reviewer UI with no password or implicit grant. That client
accepts HTTPS callbacks only. Local UI development (task 202) uses a separate development
client whose `client_id` is a different audience, so a secret or code on a laptop yields tokens
the staging API refuses; a local API sets `RXAUTH_AUTH_AUDIENCE` to `development_client_id`.

Terraform state holds the app client secrets but never a user password. State access is limited
to the operator role, and task 703 moves the UI secret into Secrets Manager.

### API settings

`terraform output api_settings` prints exactly these values:

```text
RXAUTH_AUTH_ENABLED=true
RXAUTH_AUTH_ISSUER=https://cognito-idp.us-east-1.amazonaws.com/<pool id>
RXAUTH_AUTH_JWKS_URL=https://cognito-idp.us-east-1.amazonaws.com/<pool id>/.well-known/jwks.json
RXAUTH_AUTH_AUDIENCE=<reviewer UI client id>
RXAUTH_AUTH_AUDIENCE_CLAIM=client_id
RXAUTH_AUTH_REQUIRED_TOKEN_USE=access
RXAUTH_AUTH_ALGORITHMS=RS256
RXAUTH_AUTH_ORGANIZATION_CLAIM=org_id
RXAUTH_AUTH_ROLES_CLAIM=cognito:groups
```

### Synthetic accounts

| Username | Organization | Groups | Proves |
|---|---|---|---|
| `reviewer-a1`, `reviewer-a2` | `synthetic-org-a` | `case:read`, `case:review` | Distinct sign-ins get distinct subjects (task 202) |
| `intake-a1` | `synthetic-org-a` | `case:write` | Case creation and upload |
| `admin-a1` | `synthetic-org-a` | `admin` | Admin stays inside its organization |
| `reviewer-b1` | `synthetic-org-b` | `case:read`, `case:review` | Cross-tenant requests are 404 (task 205) |

Addresses use `synthetic.example.test`; no invitation email is sent. Terraform creates each
account with an unsent, unknown temporary password. Whoever holds a temporary password at
first sign-in also enrols the TOTP device, so the operator sets one per person, out of band:

```text
aws cognito-idp admin-set-user-password --user-pool-id <pool id> --username reviewer-a1 \
  --password "$(read -rs p; echo "$p")" --no-permanent
```

Hand that password to the account's intended user over a separate channel, and check within
the 3-day temporary-password window that every account has left `FORCE_CHANGE_PASSWORD`
(`aws cognito-idp list-users --user-pool-id <pool id>`). An account still waiting is reset,
not left open.

### Verification runbook

Run from a workstation with credentials for the staging account. Task 201 is accepted when
steps 5–7 succeed and their output is recorded (no tokens) in the backlog.

1. `cd infra/staging/identity`, copy `backend.hcl.example` and `terraform.tfvars.example` to
   their real names, then `terraform init -backend-config=backend.hcl` and `terraform plan`.
   Before applying, confirm in the plan the points this repository could not validate:
   `user_pool_tier` exists at the pinned provider version (raise the floor if not), the
   `aws_cognito_user` attributes map to `custom:org_id`, and the pre-token trigger is
   `V2_0`. Then `terraform apply`.
2. Set `reviewer-a1`'s and `reviewer-b1`'s temporary passwords as above.
3. Read `terraform output api_settings`, `hosted_ui_domain` and `reviewer_ui_client_id`. Write
   the client id and secret into a curl config file that never reaches shell history:
   a file `token.curlrc` (mode 600, outside the repository) containing
   `user = "<client id>:<client secret>"`, with the secret from
   `terraform output -raw reviewer_ui_client_secret`.
4. In a browser, open
   `<hosted_ui_domain>/oauth2/authorize?response_type=code&client_id=<client id>&redirect_uri=<staging callback URL>&scope=openid+email`,
   sign in as `reviewer-a1`, set a new password, and enrol TOTP. If no UI is deployed yet the
   redirect fails to load; copy the `code` parameter from the address bar.
5. Exchange the code within five minutes:
   `curl -s -K token.curlrc -d grant_type=authorization_code -d client_id=<client id> -d code=<code> -d redirect_uri=<staging callback URL> <hosted_ui_domain>/oauth2/token`.
   Keep the response in memory or a mode-600 file; never paste a token into a ticket or log.
6. Export the step 3 settings, leaving `RXAUTH_ENVIRONMENT` unset: a workstation is not a staging
   deployment, and staging settings would also demand its bucket and database. Token
   verification is identical either way. Pipe the `access_token` to `uv run rxauth-verify-token`
   and expect `{"subject": "<sub>", "organization_id": "synthetic-org-a", "roles": ["case:read",
   "case:review"]}`. Pipe the `id_token` from the same response and expect `Rejected`.
7. Repeat steps 4–6 for `reviewer-b1` and expect `synthetic-org-b` and a different subject.
   Then delete `token.curlrc`.

The first sign-in in step 4 is also the check that the classic hosted UI handles the forced
password change and TOTP enrolment together on this pool. If it does not, record that and
switch the pool to managed login before task 202.

Signing-key rotation, outage behaviour and wrong-issuer tests belong to task 206; MFA, approval
and offboarding procedure to task 207.
