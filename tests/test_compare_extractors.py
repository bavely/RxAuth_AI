"""Tests for the Phase 3 deterministic-versus-learned span comparison."""

from __future__ import annotations

from pathlib import Path

from rxauth_ai.compare_extractors import compare_extractors, render_report

_GOLD_PATH = Path(__file__).resolve().parents[1] / "data" / "extraction_gold.jsonl"


def test_learned_candidate_trains_only_on_validation_and_rules_remain_selected():
    results = compare_extractors(_GOLD_PATH)

    assert results["training_document_ids"]
    assert all(document_id.startswith("GOLD-") for document_id in results["training_document_ids"])
    assert "GOLD-038" not in results["training_document_ids"]  # held-out test record
    assert "GOLD-050" not in results["training_document_ids"]  # held-out challenge record
    assert set(results["training_document_ids"]).isdisjoint(results["selection_document_ids"])
    assert "GOLD-038" not in results["selection_document_ids"]
    assert "GOLD-050" not in results["selection_document_ids"]
    assert results["selected_extractor"] == "regex-v3"
    assert results["evaluations"]["test"]["rules"]["f1"] >= results["evaluations"]["test"]["learned"]["f1"]


def test_comparison_report_discloses_scope_and_selection():
    results = compare_extractors(_GOLD_PATH)
    report = render_report(results, _GOLD_PATH)

    assert "not an externally independent clinical benchmark" in report
    assert "Selected extractor: `regex-v3`" in report
    assert "exact evidence-type + page + start/end span" in report
