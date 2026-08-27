"""Tests for the Phase 4 retrieval and criteria-extraction benchmarks.

Two things are held here. First, the gold loaders refuse a dataset that cannot
support the claim being made — an ambiguous snippet, a partial criteria set, a
retrieval set with nothing to abstain on. Second, the headline result of each
report is reproduced, so a regression in retrieval or criteria extraction fails
a test rather than quietly changing a published number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rxauth_ai.benchmark_criteria import benchmark_criteria
from rxauth_ai.benchmark_criteria import load_gold as load_criteria_gold
from rxauth_ai.benchmark_criteria import render_report as render_criteria_report
from rxauth_ai.benchmark_retrieval import benchmark_retrieval
from rxauth_ai.benchmark_retrieval import load_gold as load_retrieval_gold
from rxauth_ai.benchmark_retrieval import render_report as render_retrieval_report
from rxauth_ai.policy_corpus import load_corpus
from rxauth_ai.policy_retrieval import build_index

_ROOT = Path(__file__).resolve().parents[1]
_POLICY_DIR = _ROOT / "data" / "policies"
_RETRIEVAL_GOLD = _ROOT / "data" / "policy_retrieval_gold.jsonl"
_CRITERIA_GOLD = _ROOT / "data" / "policy_criteria_gold.jsonl"


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
        newline="\n",
    )


@pytest.fixture(scope="module")
def retrieval_results():
    return benchmark_retrieval(_RETRIEVAL_GOLD, policy_dir=_POLICY_DIR)


@pytest.fixture(scope="module")
def criteria_results():
    return benchmark_criteria(_CRITERIA_GOLD, policy_dir=_POLICY_DIR)


def _configuration(results, mode, embedding_prefix="tfidf-v1"):
    return next(
        configuration
        for configuration in results["configurations"]
        if configuration["mode"] == mode
        and configuration["embedding_model"].startswith(embedding_prefix)
    )


def test_metadata_filtering_selects_the_right_policy_on_every_gold_query(retrieval_results):
    filtered = _configuration(retrieval_results, "metadata+similarity")

    assert filtered["correct_policy_rate"] == 1.0
    assert filtered["failures"] == []
    assert filtered["abstention_correct"] == filtered["abstention_cases"]


def test_similarity_alone_does_worse_and_never_abstains(retrieval_results):
    """The measured justification for README section 10's design claim. If this
    ever ties, the corpus has stopped containing a real near-miss and the
    ablation has stopped meaning anything."""
    filtered = _configuration(retrieval_results, "metadata+similarity")
    unfiltered = _configuration(retrieval_results, "similarity_only")

    assert unfiltered["correct_policy_rate"] < filtered["correct_policy_rate"]
    assert unfiltered["abstention_correct"] == 0
    assert unfiltered["abstention_cases"] > 0


def test_the_retrieval_report_states_its_limits(retrieval_results):
    report = render_retrieval_report(retrieval_results, _RETRIEVAL_GOLD)

    assert "synthetic public-style policy text" in report
    assert "Neither is a pretrained semantic model" in report
    assert "Precision@5" in report


def test_a_gold_snippet_must_be_uniquely_citable(tmp_path):
    index = build_index(_POLICY_DIR)
    gold = tmp_path / "gold.jsonl"
    _write_jsonl(
        gold,
        [
            {
                "query_id": "Q1",
                "question": "criteria",
                "expected_policy_id": "PA-104",
                "expected_policy_version": "2026-01",
                "expected_snippets": ["The patient"],
            }
        ],
    )

    with pytest.raises(ValueError, match="exactly once"):
        load_retrieval_gold(gold, index)


def test_a_retrieval_gold_set_with_nothing_to_abstain_on_is_rejected(tmp_path):
    index = build_index(_POLICY_DIR)
    gold = tmp_path / "gold.jsonl"
    _write_jsonl(
        gold,
        [
            {
                "query_id": "Q1",
                "question": "criteria",
                "expected_policy_id": "PA-104",
                "expected_policy_version": "2026-01",
                "expected_snippets": ["Required screening documentation is on file."],
            }
        ],
    )

    with pytest.raises(ValueError, match="no abstention cases"):
        load_retrieval_gold(gold, index)


def test_criteria_extraction_reproduces_the_gold_structure(criteria_results):
    assert criteria_results["criterion_f1"] == 1.0
    assert criteria_results["provenance_accuracy"] == 1.0
    assert criteria_results["connective_accuracy"] == 1.0
    assert criteria_results["unstructured_recall"] == 1.0
    assert all(not policy["failures"] for policy in criteria_results["per_policy"])


def test_a_partial_criteria_gold_set_is_rejected(tmp_path):
    documents = {document.key: document for document in load_corpus(_POLICY_DIR)}
    gold = tmp_path / "gold.jsonl"
    _write_jsonl(
        gold,
        [{"policy_key": "PA-104:2026-01", "connective": "all", "expected_criteria": []}],
    )

    with pytest.raises(ValueError, match="Every policy version in the corpus needs"):
        load_criteria_gold(gold, documents)


def test_the_criteria_report_states_its_limits(criteria_results):
    report = render_criteria_report(criteria_results, _CRITERIA_GOLD)

    assert "not generalization to real policy language" in report
    assert "readier than it is" in report
