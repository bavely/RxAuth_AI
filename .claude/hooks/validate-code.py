"""PostToolUse check: format and lint the file an agent just edited.

CI fails on `ruff format --check` and `ruff check`, and on `eslint` in web/. Finding
that out after a full test run, or after a push, wastes a cycle. This runs the
same tools on the one edited file and, when something remains that cannot be
auto-fixed, exits 2 so the message goes straight back to the agent.

Python: `ruff format`, then `ruff check --fix`, using the project's venv when it
exists. TypeScript in web/: `eslint`, only if web/node_modules is installed.
Standard library only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 60


def _ruff(root: Path) -> list[str]:
    for candidate in (root / ".venv" / "Scripts" / "ruff.exe", root / ".venv" / "bin" / "ruff"):
        if candidate.exists():
            return [str(candidate)]
    return ["uv", "run", "--quiet", "ruff"]


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_SECONDS,
    )


def _python(path: Path, root: Path) -> str | None:
    ruff = _ruff(root)
    _run([*ruff, "format", "--quiet", str(path)], root)
    result = _run([*ruff, "check", "--fix", "--quiet", str(path)], root)
    if result.returncode != 0:
        return f"ruff check failed for {path.name}:\n{result.stdout}{result.stderr}"
    return None


def _typescript(path: Path, root: Path) -> str | None:
    web = root / "web"
    eslint = web / "node_modules" / "eslint" / "bin" / "eslint.js"
    if not eslint.exists():
        return None
    result = _run(["node", str(eslint), str(path)], web)
    if result.returncode != 0:
        return f"eslint failed for {path.name}:\n{result.stdout}{result.stderr}"
    return None


def main() -> int:
    payload = json.load(sys.stdin)
    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path:
        return 0
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".").resolve()
    path = Path(file_path).resolve()
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return 0
    if rel.startswith((".venv/", "web/node_modules/")) or not path.exists():
        return 0

    try:
        if path.suffix == ".py":
            problem = _python(path, root)
        elif rel.startswith("web/") and path.suffix in {".ts", ".tsx", ".mjs"}:
            problem = _typescript(path, root)
        else:
            return 0
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"validate-code hook skipped: {error}", file=sys.stderr)
        return 0

    if problem:
        print(problem, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
