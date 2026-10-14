"""Decision records cite tests as evidence; a renamed or deleted test must not leave a dead citation.

ADR 002 and the threat model justify their claims with `test_file.py::test_name` references. A
reference that no longer resolves is a claim with nothing behind it, which is the failure this
project refuses everywhere else (README section 3, "no fabricated metrics"). This reads each
document, resolves every reference against the test modules' syntax trees, and fails on any
that point nowhere.

The release checklist (backlog task 103) carries two more obligations: it must have a row for
every AGENTS.md invariant and every backlog epic, and its **Open tasks** cells must name tasks
that exist and are not yet Done. A task marked Done while the checklist still lists it as open
fails here, so the checklist cannot silently fall behind the backlog.
"""

from __future__ import annotations

import ast
import csv
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TESTS = _ROOT / "tests"

#: Documents whose evidence is a test reference.
EVIDENCE_DOCUMENTS = (
    "docs/adr-002-production-v1-scope.md",
    "docs/threat-model.md",
    "docs/release-checklist.md",
)

_CHECKLIST = _ROOT / "docs" / "release-checklist.md"
_BACKLOG = _ROOT / "docs" / "jira-production-readiness.csv"

_REFERENCE = re.compile(r"\b(test_\w+\.py)::(test_\w+)")
_INVARIANT = re.compile(r"^(\d+)\. \*\*(.+?)\.?\*\*", re.MULTILINE)


def _test_names(module: str) -> set[str]:
    path = _TESTS / module
    if not path.exists():
        return set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def _references(document: str) -> list[tuple[str, str]]:
    return _REFERENCE.findall((_ROOT / document).read_text(encoding="utf-8"))


def _checklist_rows() -> dict[str, dict[str, str]]:
    """Rows of every checklist table that has an Open tasks column, keyed by their first cell."""
    rows: dict[str, dict[str, str]] = {}
    header: list[str] | None = None
    for line in _CHECKLIST.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            header = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if header is None:
            header = cells if "Open tasks" in cells else []
        elif header and not set(cells[0]) <= {"-"}:
            rows[cells[0]] = dict(zip(header, cells, strict=True))
    return rows


def _backlog() -> dict[str, dict[str, str]]:
    with _BACKLOG.open(encoding="utf-8", newline="") as handle:
        return {row["Issue ID"]: row for row in csv.DictReader(handle)}


def test_every_evidence_document_cites_at_least_one_test():
    """Guard against a vacuous pass if the reference format ever changes."""
    for document in EVIDENCE_DOCUMENTS:
        assert _references(document), f"{document} cites no test_file.py::test_name reference"


def test_every_cited_test_exists():
    dead = [
        f"{document}: {module}::{name}"
        for document in EVIDENCE_DOCUMENTS
        for module, name in _references(document)
        if name not in _test_names(module)
    ]
    assert not dead, "cited tests that do not exist:\n" + "\n".join(dead)


def test_the_release_checklist_has_a_row_for_every_repository_invariant():
    agents = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    section = agents.split("## Non-negotiable invariants", 1)[1].split("\n## ", 1)[0]
    invariants = _INVARIANT.findall(section)
    assert invariants, "no numbered invariants found in AGENTS.md"
    rows = _checklist_rows()
    missing = [
        f"I{number} {title}"
        for number, title in invariants
        if f"**{title}**" not in rows.get(f"I{number}", {}).get("Invariant", "")
    ]
    assert not missing, "AGENTS.md invariants without a checklist row:\n" + "\n".join(missing)


def test_the_release_checklist_has_a_row_for_every_backlog_epic():
    epics = {key for key, row in _backlog().items() if row["Issue Type"] == "Epic"}
    missing = sorted(epics - _checklist_rows().keys(), key=int)
    assert not missing, f"backlog epics without a checklist row: {missing}"


def test_every_open_task_in_the_release_checklist_exists_and_is_not_done():
    backlog = _backlog()
    problems = []
    for key, row in _checklist_rows().items():
        for task in re.findall(r"\d+", row["Open tasks"]):
            if task not in backlog:
                problems.append(f"{key}: task {task} is not in the backlog")
            elif backlog[task]["Status"] == "Done":
                problems.append(f"{key}: task {task} is Done; move its evidence out of Open tasks")
    assert not problems, "\n".join(problems)
