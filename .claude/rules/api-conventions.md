---
paths:
  - "src/rxauth_ai/api.py"
  - "src/rxauth_ai/auth.py"
  - "src/rxauth_ai/jobs.py"
  - "src/rxauth_ai/case_jobs.py"
  - "src/rxauth_ai/worker.py"
  - "src/rxauth_ai/storage.py"
  - "src/rxauth_ai/uploads.py"
  - "src/rxauth_ai/persistence/**"
  - "alembic/**"
  - "web/**"
  - "tests/test_api.py"
  - "tests/test_persistence.py"
---

# Service-layer and API conventions

## FastAPI (`api.py`)
- Handlers are plain `def`, not `async def`. Everything underneath is sync and CPU-bound,
  and FastAPI runs `def` handlers on a threadpool.
- The API reaches the workflow. It never re-implements it. Don't re-derive citations or
  results server-side that the run payload already carries.
- Long work (a case run) goes through `JobQueue`. Requests stay short and clients poll.
- Every endpoint resolves a `Principal` via `Security(...)`, checks a role
  (`ROLE_CASE_READ`, `ROLE_CASE_WRITE`, `ROLE_REVIEW`, `ROLE_ADMIN` from `auth.py`), and
  passes `principal.organization_id` into every persistence and storage call.
- Another organization's resource returns **404**, the same as absent. A 403 is only for
  "authenticated but lacking the role".
- Path ids are validated against `SAFE_ID_PATTERN`.
- Reviewer identity comes from the verified token subject, never a request field.
- Auth fails closed: staging/production require OIDC issuer, audience, and JWKS. Local
  uses one explicit synthetic principal. Never add a bypass flag.
- `API_VERSION` stays `v1` unless a breaking change is intended.

## Persistence
- Every repository function takes `organization_id` as keyword-only and filters on it.
- The schema is dialect-neutral: tests run on SQLite and CI re-runs persistence/API tests on
  Postgres 16. Avoid Postgres-only types or SQLite-only behaviour.
- Schema changes go through Alembic (`/add-migration`). Never use `create_all` as the
  deployed path. CI runs `alembic upgrade head`, `alembic check`, and `downgrade base`.

## Storage
- Object keys are built via `storage.document_key(...)`. Local disk is permitted only in
  `environment=local`. S3 credentials come from the environment or an instance role. Never
  commit them.

## Reviewer UI (`web/`)
- The browser never calls the API. All calls run server-side through `web/src/lib/api.ts`,
  so tokens and document bytes never reach client JS.
- No caching of API responses (stale gate results; PHI copies).
- Surface `ApiError` states explicitly (404 / 503 / 401-403) through `ErrorPanel`.
  Don't swallow them.
