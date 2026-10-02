---
name: testing-patterns
description: How tests are written and run in RxAuth AI. Use when adding or changing tests, choosing which tests cover a module, debugging a failing pytest/vitest run, or testing persistence/API code against SQLite or Postgres.
---

# Testing patterns in RxAuth AI

## Layout
- One `tests/test_<module>.py` per `src/rxauth_ai/<module>.py`. No `conftest.py`: fixtures
  live in the file that uses them.
- Each test file opens with a docstring naming what it covers and the README/doc section.
- Repo data is reached through a module constant, not the CWD:
  `_ROOT = Path(__file__).resolve().parents[1]`, then `_ROOT / "data" / ...`.
- Small private builders (`_document(text, ...)`, `_case(...)`) construct inputs inline.
  Prefer them over fixture files for unit tests.
- `test_*` names state the behaviour: `test_extracts_diagnosis_with_high_confidence`.

## Which tests to run for a change

| Changed | Run first | Then |
|---|---|---|
| `extraction.py`, `medications.py` | `test_extraction.py` | `test_case_assembly.py`, `test_matching.py`, `test_benchmark_extraction.py`, `test_calibration.py` |
| `matching.py` | `test_matching.py` | `test_pipeline.py`, `test_case_assembly.py`, `test_benchmark_matching.py` |
| `ingestion.py` | `test_ingestion.py` | `test_case_assembly.py` |
| `policy_corpus.py`, `policy_retrieval.py`, `criteria_extraction.py` | their own test files | `test_benchmark_policy.py`, `test_case_assembly.py` |
| `workflow.py`, `case_assembly.py`, `pipeline.py` | `test_workflow.py`, `test_case_assembly.py`, `test_pipeline.py` | full suite |
| `groundedness.py`, `generation.py` | `test_generation.py`, `test_pipeline.py` | full suite |
| `api.py`, `auth.py`, `jobs.py`, `storage.py`, `uploads.py` | `test_api.py`, `test_auth.py`, `test_jobs.py`, `test_uploads.py` | `test_persistence.py` |
| `persistence/`, `alembic/` | `test_persistence.py`, `test_api.py` | Postgres run (below) |
| `models.py` | **full suite** | `/regenerate-reports` |

`test_case_assembly.py` and `test_extraction.py` catch the most real regressions. Run
them for any spine change.

```bash
uv run pytest tests/test_matching.py -q
uv run pytest -q -k "duration and not deep"
uv run pytest --cov=rxauth_ai --cov-report=term-missing
```

## Persistence and API tests
- A `settings` fixture builds `Settings` with
  `os.environ.get("RXAUTH_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'x.db'}"`.
  Keep that pattern: SQLite by default, Postgres when CI sets the variable.
- API tests use `fastapi.testclient.TestClient(create_app(...))`. Authentication is
  swapped for a small `TokenAuthenticator` mapping tokens to `Principal`s. Cover each
  role and a **cross-organization** case (expect 404).
- To run against Postgres locally:
  ```bash
  docker compose up -d postgres
  RXAUTH_DATABASE_URL=postgresql+psycopg://rxauth:rxauth@localhost:5432/rxauth \
  RXAUTH_TEST_DATABASE_URL=postgresql+psycopg://rxauth:rxauth@localhost:5432/rxauth \
    uv run pytest tests/test_persistence.py tests/test_api.py -q
  ```

## Optional dependencies
- `deep` extra (torch/transformers) and Tesseract are optional. Tests that need them
  use `pytest.importorskip` / skip markers. Keep new ones the same way so a plain
  `uv sync --group dev` passes.
- `rxauth-run-case` tests need the classifier artifact. If missing, run
  `uv run rxauth-train-classifier` (it writes gitignored `artifacts/`).

## What a good test here asserts
- Provenance, not just values: document id, page, span, quoted text, method.
- Uncertain inputs produce `MISSING` / `AMBIGUOUS` / `HUMAN_REVIEW_REQUIRED`. Add a
  negative or ambiguous case next to every positive one.
- Exact numbers from reports belong in benchmark tests only. Unit tests use small inline inputs.
- Never "fix" a failing test by editing gold data or loosening `groundedness`.

## Web (`web/`)
- Vitest, colocated `*.test.ts` (e.g. `src/lib/citations.test.ts`,
  `src/app/runs/[runId]/actions.test.ts`). Test pure lib functions and server-action
  input handling. Mock `@/lib/api`, never the network.
- `cd web && npm test` (single file: `npx vitest run src/lib/citations.test.ts`).
