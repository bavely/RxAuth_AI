# The reviewer UI

A Next.js application in [`web/`](../web). Three pages — a worklist, a case, and a run — plus the
thing the rest of the system exists to make possible: every cited span shown inside the document it
came from.

It adds no endpoint and decides nothing. Everything it displays is read from the API, and the one
thing it writes is a reviewer decision, which the API validates and attributes to a verified
identity.

## The browser never talks to the API

Every API call happens in the Next.js server. The browser receives rendered HTML and, for a
non-text document, a same-origin URL served by one proxy route.

That is the whole security posture in one sentence, and it follows from what this API serves. A
browser-side client needs an access token in JavaScript's reach — `localStorage`, a cookie a script
can read, or a variable in a bundle — where any script on the page can take it and where it
outlives the tab. Patient documents would then be fetched by client code, landing in the browser
cache and the network log. Keeping the token and the bytes on one server removes that whole class
of problem rather than mitigating it.

`src/lib/auth.ts` and `src/lib/api.ts` both start with `import "server-only"`, so importing either
from a client component is a build error rather than a review comment somebody has to catch.

**A consequence worth stating plainly: this design does not exercise the API's CORS policy.** That
policy is still correct and still needed — any direct browser or programmatic client depends on it,
and it is what `docker-compose.yml` configures — but the UI does not rely on it, and no weaker
architecture was chosen to make it look used.

| | Server-side (this) | Browser SPA |
|---|---|---|
| Access token | One server process | `localStorage` or a readable cookie |
| Patient bytes | Server → HTML | Client fetch, browser cache, network log |
| CORS | Not needed | Required |
| Works without client JS | Yes, except the decision form's conditional field | No |

## Pages

| Route | What it answers |
|---|---|
| `/` | What is waiting for me? Cases newest first, document counts, and the latest run's five-state counts with its citation gate |
| `/cases/[caseId]` | What was declared, what was uploaded, what has been run, what has already been decided — and a control to queue a run |
| `/runs/[runId]` | Every requirement, its result and confidence, the policy text behind it, the evidence found for it, how it was decided, and a decision form |
| `/api/documents/[runId]/[documentId]` | A same-origin URL for a PDF or scan, proxying one API response |

The worklist deliberately shows **counts, not a score**. The five results are not points on one
axis, and averaging `MISSING` against `SATISFIED` would invent a number the pipeline never
computed. A case that has never run appears with "Not run" rather than being filtered out — it is
usually the one most in need of attention.

## Showing a citation in its document

`src/lib/citations.ts` is the only real logic in the application and the most tested part of it.
Given the served document and one `Provenance`, it returns the span with the surrounding lines and
one of six statuses:

| Status | Meaning |
|---|---|
| `verified` | The range lands on exactly the text the citation quoted |
| `mismatch` | The range resolved, to *different* text than the citation claims |
| `out_of_range` | The range runs past the end of the document |
| `no_span` | The citation carries no character range |
| `not_text` | Not a text document; the span cannot be highlighted in place |
| `page_offsets_unavailable` | Offsets index one ingested page, and this document has several |

`mismatch` is the one that matters. The alternative to detecting it is highlighting whatever sits
at those offsets with complete confidence, which is worse than declining — a reviewer trusts a
highlight. The same reasoning drives `page_offsets_unavailable`: character offsets are positions
within an *ingested page*, and a multi-page document's raw bytes are not that page, so the excerpt
is refused and the reviewer is told to find the quote on the cited page.

On the demo packet all 38 citations across six criteria resolve as `verified`.

Two bugs in this file were caught by its own tests before it ever ran: context was computed by
snapping to the nearest line boundary, which returns *nothing* when a span starts a line — and
almost every span in this corpus starts a line. The forward side had the mirror-image bug. The
tests that pin both are named after the regression.

## Recording a decision

The decision form is the only client component, and it is a client component for one reason: the
corrected-result field is meaningless unless the action is `corrected`, and showing it always
invites a reviewer to fill in a correction that will then be ignored.

Submission goes to a server action. The reviewer id is deliberately not a field — the API takes it
from the verified token subject and ignores anything a client asserts, which is what makes an
append-only decision log worth keeping. A "correction" that changes nothing is refused by the API
with a 422, and that reason is shown rather than swallowed.

## Authentication

`TokenProvider` in `src/lib/auth.ts` is the seam:

- `LocalPrincipalTokenProvider` sends no token, mirroring `auth.LocalDevelopmentAuthenticator`,
  which the API selects only when authentication is off. Against a deployed API every request from
  it is a 401 — the correct and loud failure.
- `StaticTokenProvider` reads `RXAUTH_API_TOKEN`. Suitable for a machine identity or an end-to-end
  test, *not* for reviewers: every request would carry the same subject, so decisions would be
  attributed to the service rather than the person.
- `OidcSessionTokenProvider` is the one place to implement, and it throws until someone does.

**No authorization-code flow is written here.** The API validates asymmetric JWTs against a
provider's JWKS endpoint, and which provider — Entra, Cognito, Auth0, Keycloak — is deployment
configuration. Writing a flow against a provider this repository cannot test would be exactly the
unmeasured infrastructure it refuses elsewhere. What belongs here is the boundary and one honest
error message.

## Running it

```bash
docker compose up --build          # Postgres, migrations, API, worker, and the UI on :3000
```

Or against an API you are already running:

```bash
cd web
npm ci
cp .env.example .env.local         # RXAUTH_API_URL, default http://localhost:8000
npm run dev
```

CI runs `npm ci`, `npm run lint`, `npm run typecheck`, `npm test`, and `npm run build` in a
separate job. It needs no database and no API.

ESLint is pinned to 9.x: `eslint-config-next` 16 bundles an `eslint-plugin-react` that calls a
context API removed in ESLint 10, so 10 crashes before it lints anything.

## What this does not do

- **Policy documents are not shown in context.** The corpus lives on disk, not in object storage,
  so the API serves no bytes for `PA-104` and a policy citation shows its quote and exact location
  but not its surroundings. Closing this means an endpoint for the policy corpus, not a workaround
  here.
- **No page text for multi-page documents.** Fixing it properly means persisting ingested page
  text at run time — a schema change with a measurement behind it, not a default. Re-ingesting
  inside a request handler would put OCR in a synchronous endpoint, which is why runs go to a
  queue.
- **No upload screen.** Documents are uploaded through the API. The streaming validation, the size
  and page limits, and the per-object retention all live there, and a second implementation in a
  browser form would be the one place they drift.
- **No OIDC flow**, as above.
- **No component or end-to-end browser tests.** The tested units are the citation resolver and the
  decision action — the parts with logic. Rendering was verified by running the stack against a
  seeded database and reading the responses, not by an automated browser.
