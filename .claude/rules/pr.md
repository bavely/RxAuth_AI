# Commits and pull requests

## Before you call a change done
Run what CI runs for the area you touched:

| Touched | Run |
|---|---|
| Any Python | `uv run ruff check . && uv run ruff format --check . && uv run pytest` |
| Spine (`models`, `pipeline`, `matching`, `extraction`, `ingestion`, `policy_*`, `criteria_extraction`, `groundedness`, `generation`, `workflow`, `case_assembly`) | the above + `/regenerate-reports`, then `uv run rxauth-evaluate` and `uv run rxauth-check-reports` |
| `persistence/`, `alembic/` | the above + `uv run alembic upgrade head && uv run alembic check` against a DB if available |
| `web/` | `cd web && npm run lint && npm run typecheck && npm test` (and `npm run build` for routing/config changes) |

If you could not run a step (no Postgres, no Tesseract), say so explicitly. Don't
imply it passed.

## Commits
- Imperative subject, ≤ 72 chars, describing the behaviour change (`Link duration to
  outcome across sentence boundaries`), not the file list.
- Body explains **why** and names any version bump, regenerated report, or threshold
  move. A threshold *lowered* must say why in the body.
- Regenerated reports go in the same commit as the code that changed them.
- Never commit `artifacts/`, `.env*` (except `.env.example`), `data/reviewer_feedback.jsonl`,
  or `CLAUDE.local.md`.
- Commit or push only when the user asks. Branch off `main` for new work.

## PR description
1. What changed and why (one paragraph).
2. Blast radius: which layers and reports move.
3. Metric deltas from `reports/evaluation_suite.md` (before → after), if any.
4. Verification: the commands run and their result.
5. Guardrail check: confirm no change to scope, citation gate, gold data, or tenant isolation,
   or call out the one that changed.
