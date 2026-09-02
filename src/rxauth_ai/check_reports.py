"""Fail when a checked-in report no longer matches what the code produces.

`reports/` is published as evidence: README §3 forbids fabricated metrics, and a
committed report that the current code would not reproduce is exactly that — a
number nobody can regenerate. Nothing enforced it, so
`reports/case_PA-CASE-001.json` sat several commits behind `evidence-match-v2`,
still describing a matcher that had been replaced.

The obvious gate — regenerate and `git diff --exit-code` — does not survive
contact with reality. Every benchmark reports latency, latency depends on the
machine and varies by more than a factor of two between runs, and a gate that
fails on every commit is a gate somebody removes. So timing cells are normalized
away and everything else is compared exactly: quality metrics, counts, failure
tables, and citation IDs all still have to reproduce byte for byte.

A timing regression is a real thing to care about, and this is not the tool that
would catch it; that needs a benchmark harness with a controlled machine, which
belongs to production hardening rather than here.
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

#: Reports a plain `uv sync --group dev` install can regenerate. The deep
#: classifier comparison is deliberately absent: it needs the optional `deep`
#: extra, it reports wall-clock fine-tuning time in prose rather than in a
#: table cell, and CI does not train it.
DEFAULT_REPORTS: tuple[str, ...] = (
    "reports/case_PA-CASE-001.json",
    "reports/criteria_extraction.md",
    "reports/extraction_benchmark.md",
    "reports/extraction_calibration.md",
    "reports/extraction_learned_comparison.md",
    "reports/ingestion_benchmark.md",
    "reports/matching_evaluation.md",
    "reports/policy_retrieval.md",
)

#: Header cells and row labels whose values are wall-clock measurements.
_TIMING = re.compile(r"latency|elapsed|duration|runtime|\btime\b", re.IGNORECASE)

_PLACEHOLDER = " <timing> "


class ReportDrift(Exception):
    """Raised when a report's substantive content no longer reproduces."""


def _split_row(line: str) -> list[str]:
    """Split a markdown table row into cells, keeping the outer pipes implicit."""
    return line.split("|")


def _is_separator(line: str) -> bool:
    return bool(re.fullmatch(r"\|[\s:|-]+\|", line.strip()))


def normalize_markdown(text: str) -> str:
    """Blank every table cell that holds a wall-clock measurement.

    Two shapes appear in this repository: a `Latency (ms)` column in a wide
    metrics table, and a `| Latency (ms/policy) | 0.103 |` label/value row in a
    narrow one. Both are handled, and nothing else in the row is touched.
    """
    lines = text.splitlines()
    output: list[str] = []
    timing_columns: set[int] = set()
    in_table = False

    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            in_table = False
            timing_columns = set()
            output.append(line)
            continue

        cells = _split_row(line)
        if not in_table and not _is_separator(line):
            # First row of a new table block is its header.
            in_table = True
            timing_columns = {index for index, cell in enumerate(cells) if _TIMING.search(cell)}
            output.append(line)
            continue

        if _is_separator(line):
            output.append(line)
            continue

        # A label/value row whose label names a timing blanks its value cells.
        label_is_timing = len(cells) > 1 and bool(_TIMING.search(cells[1]))
        blank = timing_columns | (set(range(2, len(cells) - 1)) if label_is_timing else set())
        if blank:
            cells = [_PLACEHOLDER if index in blank else cell for index, cell in enumerate(cells)]
            output.append("|".join(cells))
        else:
            output.append(line)

    return "\n".join(output)


def normalize(path: Path, text: str) -> str:
    """Strip machine-dependent values so the rest can be compared exactly."""
    if path.suffix == ".md":
        return normalize_markdown(text)
    # Case reports carry no timing fields, so they are compared as written.
    return text


def committed_text(path: Path, ref: str) -> str | None:
    """Return the report as of `ref`, or None when it is not tracked there."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path.as_posix()}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout if result.returncode == 0 else None


def check_report(path: Path, ref: str) -> list[str]:
    """Return a unified diff of substantive drift, empty when the report holds."""
    if not path.is_file():
        return [f"{path.as_posix()}: not regenerated (file is absent)."]

    previous = committed_text(path, ref)
    if previous is None:
        # A newly added report has nothing to drift from.
        return []

    current = path.read_text(encoding="utf-8")
    before = normalize(path, previous).splitlines()
    after = normalize(path, current).splitlines()
    if before == after:
        return []
    return list(
        difflib.unified_diff(
            before, after, fromfile=f"{ref}:{path.as_posix()}", tofile=path.as_posix(), lineterm=""
        )
    )


def check_reports(paths: list[Path], ref: str) -> dict[str, list[str]]:
    """Check every report, returning path -> diff for those that drifted."""
    drifted = {}
    for path in paths:
        diff = check_report(path, ref)
        if diff:
            drifted[path.as_posix()] = diff
    return drifted


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail when a checked-in report no longer matches regenerated output."
    )
    parser.add_argument(
        "reports",
        nargs="*",
        type=Path,
        default=[Path(name) for name in DEFAULT_REPORTS],
        help="Reports to check. Defaults to every report a standard install regenerates.",
    )
    parser.add_argument("--ref", default="HEAD", help="Git ref to compare against.")
    args = parser.parse_args()

    paths = args.reports or [Path(name) for name in DEFAULT_REPORTS]
    drifted = check_reports(paths, args.ref)

    if not drifted:
        print(f"{len(paths)} report(s) reproduce against {args.ref} (timings excluded).")
        return

    for name, diff in drifted.items():
        print(f"\n--- {name} no longer reproduces ---")
        print("\n".join(diff))
    print(
        f"\n{len(drifted)} report(s) drifted. Regenerate them and commit the result, or fix the "
        "change that moved the numbers. Do not edit a report by hand to make this pass."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
