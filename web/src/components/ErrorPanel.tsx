import { ApiError, ApiUnreachableError } from "@/lib/api";

/**
 * Turn a failure into something a reviewer can act on.
 *
 * The three that actually happen are the API not running, an empty database,
 * and a token the API rejects. Each has a different fix, and "Something went
 * wrong" tells a reviewer none of them.
 *
 * This is a server component on purpose: it imports the server-only client to
 * read the error types, so putting it in a client tree is a build error rather
 * than a leak.
 */
export function ErrorPanel({ error }: { error: unknown }) {
  if (error instanceof ApiUnreachableError) {
    return (
      <div className="notice notice-bad">
        <h2>The API is not reachable</h2>
        <p>
          Nothing answered at <code>{error.baseUrl}</code>. Start the stack with{" "}
          <code>docker compose up --build</code>, or point <code>RXAUTH_API_URL</code> at a
          running API.
        </p>
      </div>
    );
  }

  if (error instanceof ApiError && error.isUnavailable) {
    return (
      <div className="notice notice-warn">
        <h2>The API has no database</h2>
        <p>
          It is running, but <code>RXAUTH_DATABASE_URL</code> is unset, so there are no cases to
          read. The CLI does not need one; the service does.
        </p>
        <pre>{error.detail}</pre>
      </div>
    );
  }

  if (error instanceof ApiError && error.isUnauthenticated) {
    return (
      <div className="notice notice-bad">
        <h2>The API rejected this identity</h2>
        <p>
          A deployed API validates a bearer token. Set <code>RXAUTH_API_TOKEN</code>, or implement{" "}
          <code>OidcSessionTokenProvider</code> so each reviewer carries their own — the API takes
          the reviewer id from the verified token and ignores anything a client asserts.
        </p>
        <pre>{error.detail}</pre>
      </div>
    );
  }

  if (error instanceof ApiError) {
    return (
      <div className="notice notice-bad">
        <h2>The API returned {error.status}</h2>
        <pre>{error.detail}</pre>
      </div>
    );
  }

  return (
    <div className="notice notice-bad">
      <h2>Something failed while loading this page</h2>
      <pre>{error instanceof Error ? error.message : String(error)}</pre>
    </div>
  );
}
