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
- `iss` and `aud`: matched against deployment configuration.
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
