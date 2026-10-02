@AGENTS.md

## Claude Code specifics

- Topic rules load from `.claude/rules/`. `guardrails.md` and `evaluation.md` apply everywhere;
  `api-conventions.md` applies to the service layer and `web/`.
- Skills: `/testing-patterns`, `/regenerate-reports`, `/add-migration`.
- Subagents: `@code-reviewer` before a commit, `@security-auditor` for anything touching
  `api.py`, `auth.py`, `persistence/`, `storage.py`, `uploads.py`, `observability.py`, or `web/src/lib/`.
- Hooks: `.claude/hooks/protect-paths.py` refuses edits to generated or gold files, and
  `.claude/hooks/validate-code.py` runs ruff on each Python file you edit. If a hook
  blocks you, follow its message. Don't work around it.
- Personal, uncommitted notes go in `CLAUDE.local.md` (gitignored).
