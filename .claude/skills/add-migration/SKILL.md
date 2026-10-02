---
name: add-migration
description: Add or change a database table/column in RxAuth AI safely — SQLAlchemy model, Alembic revision, repository function, tests on SQLite and Postgres. Use whenever src/rxauth_ai/persistence/tables.py changes or a new persisted field is needed.
---

# Add a schema change

The deployed schema path is Alembic. `create_all` is test-only. CI applies
`upgrade head`, runs `alembic check` (models must match migrations), runs the
persistence/API tests on Postgres 16, then `downgrade base`. All four must pass.

## 1. Model
Edit `src/rxauth_ai/persistence/tables.py`.
- Dialect-neutral types only (no `JSONB`, `ARRAY`, server-side Postgres functions).
  The same tests run on SQLite.
- Tenant-owned tables carry a non-null, indexed `organization_id`, and their unique
  constraints include it.
- A new non-null column on an existing table needs a `server_default` or a backfill
  in the migration.

## 2. Revision
Needs a reachable Postgres (`RXAUTH_DATABASE_URL` is read by `alembic/env.py` through `Settings`):
```bash
docker compose up -d postgres
export RXAUTH_DATABASE_URL=postgresql+psycopg://rxauth:rxauth@localhost:5432/rxauth
uv run alembic upgrade head
uv run alembic revision --autogenerate -m "short lowercase description"
```
Then review the generated file in `alembic/versions/`:
- Docstring header kept as generated (`Revision ID`, `Revises`, `Create Date`), and
  `from __future__ import annotations` present (match existing revisions).
- `downgrade()` truly reverses `upgrade()`. Autogenerate often omits index/constraint drops.
- Named constraints and indexes, so a downgrade can drop them by name.
- Never edit a revision that has already been merged. Add a new one.

If no Postgres is available, write the revision by hand with
`uv run alembic revision -m "..."` and tell the user `alembic check` was not run.

## 3. Verify
```bash
uv run alembic upgrade head && uv run alembic check && uv run alembic downgrade base && uv run alembic upgrade head
```

## 4. Repository and API
- Add functions in `persistence/repository.py` and export them from `persistence/__init__.py`.
- Every function takes keyword-only `organization_id` and filters on it.
- API payload change → mirror it in `web/src/lib/types.ts`.

## 5. Tests
- `tests/test_persistence.py`: round-trip plus a cross-organization read that returns nothing.
- `tests/test_api.py` if exposed: the role check and a cross-org 404.
- Run on SQLite (`uv run pytest tests/test_persistence.py tests/test_api.py -q`), and on
  Postgres with `RXAUTH_TEST_DATABASE_URL` set (see `/testing-patterns`).
