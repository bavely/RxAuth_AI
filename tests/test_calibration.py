"""Tests for extraction confidence calibration (docs/phase-3-extraction.md, §9 item 5)."""

from __future__ import annotations

import json
from pathlib import Path

from rxauth_ai.calibration import CALIBRATION_SPLIT, calibrate, render_report

_GOLD_PATH = Path(__file__).resolve().parents[1] / "data" / "extraction_gold.jsonl"


def _write_gold(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def test_calibration_reads_the_validation_split_only():
    """The test split exists to be surprised by; reading it here would spend
    that exactly once."""
    from rxauth_ai.benchmark_extraction import load_gold

    results = calibrate(_GOLD_PATH)
    validation_documents = sum(
        1 for record in load_gold(_GOLD_PATH) if record.split == CALIBRATION_SPLIT
    )

    assert results["split"] == CALIBRATION_SPLIT
    assert results["documents"] == validation_documents


def test_every_scored_field_lands_in_exactly_one_reliability_row():
    results = calibrate(_GOLD_PATH)

    assert sum(row["count"] for row in results["reliability"]) == results["fields_scored"]
    assert sum(row["count"] for row in results["by_rule"]) == results["fields_scored"]


def test_reliability_gap_is_observed_accuracy_minus_claimed_confidence():
    for row in calibrate(_GOLD_PATH)["reliability"]:
        assert row["accuracy"] == row["correct"] / row["count"]
        assert row["gap"] == row["accuracy"] - row["confidence"]


def test_calibration_error_and_brier_score_are_in_range():
    results = calibrate(_GOLD_PATH)

    assert 0.0 <= results["expected_calibration_error"] <= 1.0
    assert 0.0 <= results["brier_score"] <= 1.0


def test_a_prediction_with_no_gold_counterpart_counts_as_incorrect(tmp_path):
    """A confident claim about something that is not in the gold annotation is
    a calibration failure, not an unscored field."""
    gold = _write_gold(
        tmp_path / "gold.jsonl",
        [
            {
                "document_id": "T-1",
                "split": "validation",
                "filename": "note.txt",
                "text": "Diagnosis: Example Condition.",
                "expected": [],
            },
            {
                "document_id": "T-2",
                "split": "test",
                "filename": "note.txt",
                "text": "Nothing to extract here.",
                "expected": [],
            },
        ],
    )
    results = calibrate(gold)

    assert results["fields_scored"] == 1
    assert results["fields_correct"] == 0
    assert results["reliability"][0]["accuracy"] == 0.0
    assert results["reliability"][0]["gap"] < 0


def test_sweep_reports_a_band_of_thresholds_that_reproduce_gold_routing():
    results = calibrate(_GOLD_PATH)
    low, high = results["safe_threshold_band"]

    assert low <= results["current_threshold"] <= high
    assert results["best_review_f1"] > 0.0


def test_report_discloses_the_split_and_the_synthetic_caveat():
    results = calibrate(_GOLD_PATH)
    report = render_report(results, _GOLD_PATH)

    assert "validation only" in report
    assert "synthetic and in-distribution" in report
    assert "incomplete_value" in report
