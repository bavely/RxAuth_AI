"""Tests for the Phase 5 gold matching benchmark."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rxauth_ai.benchmark_matching import benchmark_matching, load_gold, render_report
from rxauth_ai.models import CriterionResult

GOLD_PATH = Path("data/matching_gold.jsonl")


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _record(match_id: str, split: str, **updates) -> dict:
    record = {
        "match_id": match_id,
        "split": split,
        "criterion": {
            "criterion_type": "previous_therapy",
            "medication": "adalimumab",
            "operator": ">=",
            "expected_value": 12,
            "unit": "weeks",
            "source_text": "At least 12 weeks of adalimumab.",
        },
        "evidence": [
            {
                "id": "D1-EV1",
                "evidence_type": "previous_therapy",
                "medication": "adalimumab",
                "value": 16,
                "unit": "weeks",
                "confidence": 0.9,
                "source_text": "adalimumab - 16 weeks documented",
            }
        ],
        "expected_result": "SATISFIED",
        "expected_evidence_ids": ["D1-EV1"],
    }
    record.update(updates)
    return record


def _all_splits(**updates) -> list[dict]:
    return [
        _record("V1", "validation", **updates),
        _record("T1", "test", **updates),
        _record("C1", "challenge", **updates),
    ]


def test_perfect_gold_run_reports_result_evidence_and_citation_accuracy(tmp_path: Path):
    gold_path = tmp_path / "gold.jsonl"
    _write_jsonl(gold_path, _all_splits())

    results = benchmark_matching(gold_path)

    assert results["records_total"] == 3
    for split in ("validation", "test", "challenge"):
        metrics = results["evaluations"][split]
        assert metrics["result_accuracy"] == 1.0
        assert metrics["evidence_f1"] == 1.0
        assert metrics["retrieval_recall"] == 1.0
        assert metrics["citation_correctness"] == 1.0
        assert metrics["false_support_rate"] == 0.0
        assert metrics["failures"] == []


def test_wrong_expected_result_is_reported_as_a_failure(tmp_path: Path):
    gold_path = tmp_path / "gold.jsonl"
    _write_jsonl(gold_path, _all_splits(expected_result="NOT_SATISFIED"))

    metrics = benchmark_matching(gold_path)["evaluations"]["test"]

    assert metrics["result_accuracy"] == 0.0
    assert [failure["match_id"] for failure in metrics["failures"]] == ["T1"]
    assert "NOT_SATISFIED" in metrics["failures"][0]["expected"]
    assert "SATISFIED" in metrics["failures"][0]["predicted"]


def test_right_result_with_the_wrong_citation_still_fails(tmp_path: Path):
    """The status alone is not the deliverable; an unciteable claim is a failure."""
    gold_path = tmp_path / "gold.jsonl"
    records = _all_splits()
    for record in records:
        record["evidence"].append(
            {
                "id": "D2-EV1",
                "evidence_type": "previous_therapy",
                "medication": "adalimumab",
                "value": 20,
                "unit": "weeks",
                "confidence": 0.8,
                "source_text": "adalimumab - 20 weeks per pharmacy records",
            }
        )
        # Both facts support the rule, so citing only one is an incomplete answer.
        record["expected_evidence_ids"] = ["D1-EV1"]
    _write_jsonl(gold_path, records)

    metrics = benchmark_matching(gold_path)["evaluations"]["validation"]

    assert metrics["result_accuracy"] == 1.0
    assert metrics["evidence_f1"] < 1.0
    assert [failure["match_id"] for failure in metrics["failures"]] == ["V1"]


def test_false_support_rate_counts_unsupported_satisfied_results(tmp_path: Path):
    """An unsupported SATISFIED is the most dangerous matching error, so it is named."""
    gold_path = tmp_path / "gold.jsonl"
    _write_jsonl(gold_path, _all_splits(expected_result="AMBIGUOUS", expected_evidence_ids=[]))

    metrics = benchmark_matching(gold_path)["evaluations"]["challenge"]

    assert metrics["false_support_rate"] == 1.0


def test_gold_loader_rejects_duplicates_unknown_ids_and_missing_splits(tmp_path: Path):
    duplicate = tmp_path / "duplicate.jsonl"
    _write_jsonl(duplicate, [*_all_splits(), _record("V1", "validation")])
    with pytest.raises(ValueError, match="Duplicate match_id"):
        load_gold(duplicate)

    unknown = tmp_path / "unknown.jsonl"
    _write_jsonl(unknown, _all_splits(expected_evidence_ids=["D9-EV9"]))
    with pytest.raises(ValueError, match="absent from the case"):
        load_gold(unknown)

    incomplete = tmp_path / "incomplete.jsonl"
    _write_jsonl(incomplete, [_record("V1", "validation")])
    with pytest.raises(ValueError, match="validation, test, and challenge"):
        load_gold(incomplete)

    missing = tmp_path / "absent.jsonl"
    with pytest.raises(FileNotFoundError):
        load_gold(missing)


def test_report_renders_every_split_and_names_its_failures(tmp_path: Path):
    gold_path = tmp_path / "gold.jsonl"
    _write_jsonl(gold_path, _all_splits(expected_result="MISSING", expected_evidence_ids=[]))

    report = render_report(benchmark_matching(gold_path), gold_path)

    assert "| validation |" in report
    assert "| test |" in report
    assert "| challenge |" in report
    assert "MISSING []" in report


# --- Contracts the checked-in gold set itself has to keep -------------------


def test_checked_in_gold_covers_every_result_in_every_split():
    """Macro F1 averages over all five results, so a split missing one is penalised
    for a state it never asked about. Full coverage keeps the splits comparable."""
    records = load_gold(GOLD_PATH)
    for split in ("validation", "test", "challenge"):
        covered = {record.expected_result for record in records if record.split == split}
        assert covered == set(CriterionResult), (
            f"{split} is missing {set(CriterionResult) - covered}"
        )


def test_checked_in_gold_annotates_every_case_with_why_it_exists():
    """A gold record without a stated reason cannot be reviewed or defended later."""
    for record in load_gold(GOLD_PATH):
        assert record.note, f"{record.match_id} has no note"


def test_checked_in_gold_passes_on_every_split():
    """The published claim in reports/matching_evaluation.md, enforced as a test."""
    results = benchmark_matching(GOLD_PATH)
    for split, metrics in results["evaluations"].items():
        assert metrics["result_accuracy"] == 1.0, f"{split}: {metrics['failures']}"
        assert metrics["evidence_f1"] == 1.0, f"{split}: {metrics['failures']}"
        assert metrics["false_support_rate"] == 0.0, f"{split}: {metrics['failures']}"
