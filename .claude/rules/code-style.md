# Code style

## Python (`src/`, `tests/`, `alembic/`)
- Python 3.12, `from __future__ import annotations` at the top of every module.
- Ruff is the authority: line length 100, rules `E4 E7 E9 F I B`, `ruff format`. The
  `validate-code` hook runs it after each edit. CI runs `ruff check` and
  `ruff format --check`.
- Every module opens with a docstring that explains **why** it exists and the
  decisions it embodies, often citing the README section (`(README section 9)`). Keep
  that voice when adding a module. Don't restate what the code does.
- Comments are rare and explain a decision or a trap. No narration comments.
- Data shapes are pydantic models in `models.py` (or local `BaseModel`s in the service
  layer). Settings come from `config.get_settings()`, never `os.environ` reads scattered
  through modules and never literal paths like `Path("data")`.
- Paths: `pathlib.Path`; open text with `encoding="utf-8"`. Code must pass on Windows and Linux.
- Public constants use `#:` doc comments; module-private helpers are `_prefixed`.
- Errors: raise a specific exception (`ConfigurationError`, `StorageError`,
  `AuthenticationError`, ...). Don't catch broad `Exception` to keep a pipeline going.
  A failed node records its failure and later nodes are marked not-run.
- No new runtime dependency without saying so. The core package is deliberately light,
  and service-only deps belong in the `service` extra.
- New CLI → `main()` in the module + an entry in `[project.scripts]`, defaults sourced
  from `Settings`.

## TypeScript (`web/`)
- Next.js App Router, React 19, strict TS. `npm run lint`, `typecheck`, `test` (vitest).
- Anything that talks to the API imports `"server-only"`. Mutations are server actions
  (`"use server"` modules export only async functions).
- Shared types live in `web/src/lib/types.ts` and mirror the API payloads. Change both
  sides together.
- JSDoc blocks explain intent and the risk avoided, matching `lib/api.ts`.
