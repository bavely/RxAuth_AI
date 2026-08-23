"""Tests for the Phase 3 gold extraction benchmark."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rxauth_ai.benchmark_extraction import benchmark_extraction, load_gold, render_report


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _record(document_id: str, split: str, text: str, expected: list[dict]) -> dict:
    return {
        "document_id": document_id,
        "split": split,
        "filename": f"{document_id}.txt",
        "text": text,
        "expected": expected,
    }


def test_perfect_gold_run_reports_exact_fields_spans_and_review_routing(tmp_path: Path):
    gold_path = tmp_path / "gold.jsonl"
    _write_jsonl(
        gold_path,
        [
            _record(
                "V1",
                "validation",
                "Diagnosis: Example Condition.",
                [
                    {
                        "evidence_type": "diagnosis",
                        "text_value": "Example Condition",
                        "outcome": "documented",
                        "source_text": "Diagnosis: Example Condition",
                    }
                ],
            ),
            _record(
                "T1",
                "test",
                "Patient on therapy for several months.",
                [
                    {
                        "evidence_type": "therapy_duration",
                        "outcome": "several months",
                        "source_text": "on therapy for several months",
                        "requires_review": True,
                    }
                ],
            ),
        ],
    )

    results = benchmark_extraction(gold_path)

    for evaluation in results["evaluations"].values():
        assert evaluation["field_f1"] == 1.0
        assert evaluation["normalized_value_accuracy"] == 1.0
        assert evaluation["provenance_span_accuracy"] == 1.0
        assert evaluation["document_review_accuracy"] == 1.0
    assert results["evaluations"]["test"]["review_f1"] == 1.0
    assert "Validation failures\nNone." in render_report(results, gold_path)


def test_benchmark_exposes_false_positive_and_false_negative(tmp_path: Path):
    gold_path = tmp_path / "gold.jsonl"
    _write_jsonl(
        gold_path,
        [
            _record("V1", "validation", "No acute distress.", []),
            _record(
                "T1",
                "test",
                "HbA1c measured at 7.1%.",
                [
                    {
                        "evidence_type": "lab_a1c",
                        "value": 7.1,
                        "unit": "percent",
                        "source_text": "HbA1c measured at 7.1%",
                    }
                ],
            ),
        ],
    )

    evaluation = benchmark_extraction(gold_path)["evaluations"]["test"]

    assert evaluation["false_negative"] == 1
    assert evaluation["field_recall"] == 0.0
    assert evaluation["failures"][0]["kind"] == "false negative"


def test_exact_matching_cannot_cancel_errors_across_documents(tmp_path: Path):
    gold_path = tmp_path / "gold.jsonl"
    _write_jsonl(
        gold_path,
        [
            _record("V1", "validation", "Diagnosis: Example Condition.", []),
            _record(
                "V2",
                "validation",
                "No Diagnosis: Example Condition.",
                [
                    {
                        "evidence_type": "diagnosis",
                        "text_value": "Example Condition",
                        "outcome": "documented",
                        "source_text": "Diagnosis: Example Condition",
                    }
                ],
            ),
            _record("T1", "test", "No result.", []),
        ],
    )

    evaluation = benchmark_extraction(gold_path)["evaluations"]["validation"]

    assert evaluation["true_positive"] == 0
    assert evaluation["false_positive"] == 1
    assert evaluation["false_negative"] == 1


def test_gold_loader_rejects_duplicate_ids_and_ambiguous_spans(tmp_path: Path):
    duplicate_path = tmp_path / "duplicate.jsonl"
    record = _record("D1", "validation", "A1c: 7.4%", [])
    _write_jsonl(duplicate_path, [record, record])
    with pytest.raises(ValueError, match="Duplicate"):
        load_gold(duplicate_path)

    ambiguous_path = tmp_path / "ambiguous.jsonl"
    _write_jsonl(
        ambiguous_path,
        [
            _record(
                "V1",
                "validation",
                "A1c: 7.4% and A1c: 7.4%",
                [
                    {
                        "evidence_type": "lab_a1c",
                        "value": 7.4,
                        "unit": "percent",
                        "source_text": "A1c: 7.4%",
                    }
                ],
            ),
            _record("T1", "test", "No result.", []),
        ],
    )
    with pytest.raises(ValueError, match="exactly once"):
        load_gold(ambiguous_path)
