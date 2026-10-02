import "server-only";

/**
 * How the server obtains an access token for the API.
 *
 * The browser never gets one. Every call to the API happens in the Next.js
 * server, so a token lives in one process's memory instead of in
 * `localStorage`, where any script on the page can read it and where it
 * survives the tab that fetched it. For an API that serves patient documents
 * that is not a preference.
 *
 * `import "server-only"` makes that structural: importing this module from a
 * client component is a build error, not a review comment somebody has to
 * catch.
 *
 * **The OIDC implementation is deliberately absent.** The API validates
 * asymmetric JWTs against a provider's JWKS endpoint, and which provider — Entra,
 * Cognito, Auth0, Keycloak — is deployment configuration. Writing an
 * authorization-code flow against a provider this repository cannot test would
 * be exactly the unmeasured infrastructure it refuses elsewhere. What belongs
 * here is the seam and the one place to fill in; see `OidcSessionTokenProvider`.
 */
export interface TokenProvider {
  /**
   * A bearer token, or `null` when the API resolves the caller itself.
   *
   * `null` is a real answer, not a failure: a local API with
   * `RXAUTH_AUTH_ENABLED=false` resolves its own synthetic principal and
   * rejects nothing for the absence of a header.
   */
  token(): Promise<string | null>;
}

/**
 * No token at all, for a local API that resolves the synthetic principal.
 *
 * This mirrors `auth.LocalDevelopmentAuthenticator`, which the API selects only
 * when authentication is off. Against a deployed API every request from this
 * provider is a 401 — which is the correct and loud failure.
 */
export class LocalPrincipalTokenProvider implements TokenProvider {
  async token(): Promise<string | null> {
    return null;
  }
}

/**
 * One token from the environment.
 *
 * Suitable for a machine identity or an end-to-end test against a real API. It
 * is *not* a reviewer session: every request carries the same subject, so the
 * reviewer id recorded against a decision would name the service rather than
 * the person who made it, and §16 feedback exists to attribute decisions to
 * people.
 */
export class StaticTokenProvider implements TokenProvider {
  constructor(private readonly value: string) {}

  async token(): Promise<string | null> {
    return this.value;
  }
}

/**
 * Where a per-reviewer token comes from once an identity provider is wired up.
 *
 * The contract: read the signed-in user's session, return their current access
 * token, refresh it if expired, and throw if there is no session so the caller
 * surfaces a sign-in rather than an anonymous read. The API takes the reviewer
 * id from the verified token subject and ignores anything the client asserts,
 * so this function is the only thing that decides who a decision is attributed
 * to.
 */
export class OidcSessionTokenProvider implements TokenProvider {
  async token(): Promise<string | null> {
    throw new Error(
      "No OIDC session provider is configured. Implement OidcSessionTokenProvider " +
        "against the deployment's identity provider, or set RXAUTH_API_TOKEN for a " +
        "machine identity.",
    );
  }
}

/**
 * Pick a provider from the environment.
 *
 * An explicit token wins. Otherwise this is the local synthetic principal,
 * which a deployed API will reject.
 */
export function resolveTokenProvider(env: NodeJS.ProcessEnv = process.env): TokenProvider {
  const configured = env.RXAUTH_API_TOKEN?.trim();
  if (configured) {
    return new StaticTokenProvider(configured);
  }
  return new LocalPrincipalTokenProvider();
}
