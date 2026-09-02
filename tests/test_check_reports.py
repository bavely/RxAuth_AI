"""Tests for the report-drift gate."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from rxauth_ai.check_reports import (
    DEFAULT_REPORTS,
    check_report,
    normalize,
    normalize_markdown,
)

WIDE_TABLE = """# Report

| Split | F1 | Latency (ms) |
|---|---:|---:|
| validation | 1.000 | 0.051 |
| test | 1.000 | 0.062 |
"""

NARROW_TABLE = """# Report

| Metric | Value |
|---|---:|
| Correct-policy rate | 1.000 |
| Latency (ms/policy) | 0.103 |
"""


def test_latency_column_is_blanked_and_quality_metrics_are_not():
    slower = WIDE_TABLE.replace("0.051", "0.140").replace("0.062", "0.201")

    assert normalize_markdown(WIDE_TABLE) == normalize_markdown(slower)
    assert "1.000" in normalize_markdown(WIDE_TABLE)


def test_a_changed_quality_metric_still_shows_as_drift():
    regressed = WIDE_TABLE.replace("| validation | 1.000 |", "| validation | 0.875 |")

    assert normalize_markdown(WIDE_TABLE) != normalize_markdown(regressed)


def test_latency_row_label_blanks_its_own_value():
    slower = NARROW_TABLE.replace("0.103", "0.288")

    assert normalize_markdown(NARROW_TABLE) == normalize_markdown(slower)
    # The neighbouring quality row is untouched.
    assert "| Correct-policy rate | 1.000 |" in normalize_markdown(NARROW_TABLE)


def test_two_tables_in_one_document_do_not_share_column_positions():
    """A latency column in the first table must not blank column 3 of the second."""
    document = (
        WIDE_TABLE + "\n" + "| Split | Failures | Notes |\n|---|---|---|\n| test | 0 | 1.000 |\n"
    )

    normalized = normalize_markdown(document)

    assert "| test | 0 | 1.000 |" in normalized


def test_json_reports_are_compared_by_content_not_by_formatting():
    path = Path("reports/case_PA-CASE-001.json")
    compact = '{"readiness":{"criteria_satisfied":4,"case_id":"X"}}'
    reordered = json.dumps({"readiness": {"case_id": "X", "criteria_satisfied": 4}}, indent=1)

    assert normalize(path, compact) == normalize(path, reordered)


def test_a_changed_value_in_a_json_report_is_still_drift():
    path = Path("reports/case_PA-CASE-001.json")
    before = '{"readiness": {"criteria_satisfied": 4}}'
    after = '{"readiness": {"criteria_satisfied": 3}}'

    assert normalize(path, before) != normalize(path, after)


def test_timing_keys_anywhere_in_a_json_report_are_ignored():
    """The workflow record has none today; this keeps adding one from breaking the gate."""
    path = Path("reports/case_PA-CASE-001.json")
    fast = '{"workflow": {"nodes": [{"name": "a", "elapsed_ms": 3, "status": "ok"}]}}'
    slow = '{"workflow": {"nodes": [{"name": "a", "elapsed_ms": 91, "status": "ok"}]}}'
    broken = '{"workflow": {"nodes": [{"name": "a", "elapsed_ms": 3, "status": "failed"}]}}'

    assert normalize(path, fast) == normalize(path, slow)
    assert normalize(path, fast) != normalize(path, broken)


def test_a_json_report_that_will_not_parse_is_compared_verbatim():
    """Malformed output must read as drift, never silently pass."""
    path = Path("reports/case_PA-CASE-001.json")
    text = "{not json at all"

    assert normalize(path, text) == text


def test_drift_is_reported_against_the_committed_blob(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")

    report = repo / "report.md"
    report.write_text(WIDE_TABLE, encoding="utf-8", newline="\n")
    git("add", "report.md")
    git("commit", "-qm", "add report")

    original = Path.cwd()
    try:
        import os

        os.chdir(repo)
        # Only the timing moved: reproduces.
        report.write_text(WIDE_TABLE.replace("0.051", "0.400"), encoding="utf-8", newline="\n")
        assert check_report(Path("report.md"), "HEAD") == []

        # A quality metric moved: drift, reported as a readable diff.
        report.write_text(
            WIDE_TABLE.replace("| test | 1.000 |", "| test | 0.500 |"),
            encoding="utf-8",
            newline="\n",
        )
        diff = check_report(Path("report.md"), "HEAD")
        assert diff
        assert any("0.500" in line for line in diff)
    finally:
        os.chdir(original)


def test_an_absent_report_is_drift_not_a_silent_pass(tmp_path: Path):
    """A benchmark that failed to write its report must not read as 'unchanged'."""
    diff = check_report(tmp_path / "never_written.md", "HEAD")

    assert diff and "not regenerated" in diff[0]


def test_default_report_list_matches_what_is_checked_in():
    for name in DEFAULT_REPORTS:
        assert Path(name).is_file(), f"{name} is listed as checked in but is absent"
