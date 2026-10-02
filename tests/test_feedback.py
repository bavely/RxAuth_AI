"""Tests for reviewer feedback capture (README section 16)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rxauth_ai.benchmark_matching import load_gold
from rxauth_ai.feedback import (
    FEEDBACK_SCHEMA_VERSION,
    FeedbackStore,
    ReviewerAction,
    correction_rate_by_version,
    decision_from_evaluation,
    export_gold_records,
    to_matching_gold_record,
)
from rxauth_ai.models import CriterionResult
from rxauth_ai.pipeline import run_pipeline
from rxauth_ai.synthetic_case import build_case, build_policy


def _evaluation(index: int = 0):
    case, policy = build_case(), build_policy()
    report = run_pipeline(case, policy)
    return case, policy, report.evaluations[index]


def _decision(action=ReviewerAction.ACCEPTED, **updates):
    _, _, evaluation = _evaluation()
    return decision_from_evaluation(
        evaluation,
        reviewer_id="reviewer-01",
        action=action,
        recorded_at="2026-09-02T10:00:00+00:00",
        **updates,
    )


def test_a_decision_copies_the_answer_and_versions_it_was_filed_against():
    _, _, evaluation = _evaluation()

    decision = _decision()

    assert decision.schema_version == FEEDBACK_SCHEMA_VERSION
    assert decision.original_result is evaluation.result
    assert decision.original_evidence_ids == evaluation.supporting_evidence_ids
    assert decision.matcher_version == evaluation.matcher_version
    assert decision.normalization_version == evaluation.normalization_version


def test_agreement_is_recorded_not_discarded():
    """A component nobody corrects and one nobody reviews must be distinguishable."""
    decision = _decision(action=ReviewerAction.ACCEPTED)

    assert decision.action is ReviewerAction.ACCEPTED
    assert decision.final_result is decision.original_result


def test_a_correction_must_say_what_the_answer_should_have_been():
    with pytest.raises(ValueError, match="must state what the answer should have been"):
        _decision(action=ReviewerAction.CORRECTED)


def test_a_correction_that_changes_nothing_is_rejected_as_a_correction():
    _, _, evaluation = _evaluation()

    with pytest.raises(ValueError, match="should be recorded as ACCEPTED"):
        _decision(
            action=ReviewerAction.CORRECTED,
            corrected_result=evaluation.result,
            corrected_evidence_ids=list(evaluation.supporting_evidence_ids),
        )


def test_the_store_is_append_only_and_round_trips(tmp_path: Path):
    store = FeedbackStore(tmp_path / "feedback.jsonl")
    store.append(_decision())
    store.append(
        _decision(
            action=ReviewerAction.CORRECTED,
            corrected_result=CriterionResult.NOT_SATISFIED,
            note="The lab predates the request window.",
        )
    )

    loaded = store.load()

    assert len(loaded) == 2
    assert loaded[0].action is ReviewerAction.ACCEPTED
    assert loaded[1].corrected_result is CriterionResult.NOT_SATISFIED
    assert loaded[1].note == "The lab predates the request window."


def test_an_empty_store_reads_as_no_decisions(tmp_path: Path):
    assert FeedbackStore(tmp_path / "absent.jsonl").load() == []


def test_a_corrupt_line_names_its_line_number(tmp_path: Path):
    path = tmp_path / "feedback.jsonl"
    path.write_text('{"not": "a decision"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="line 1"):
        FeedbackStore(path).load()


def test_disagreements_exclude_the_decisions_that_agreed(tmp_path: Path):
    store = FeedbackStore(tmp_path / "feedback.jsonl")
    store.append(_decision(action=ReviewerAction.ACCEPTED))
    store.append(
        _decision(action=ReviewerAction.CORRECTED, corrected_result=CriterionResult.MISSING)
    )
    store.append(_decision(action=ReviewerAction.REJECTED))

    assert len(store.disagreements()) == 2


def test_correction_rate_is_measured_per_component_version(tmp_path: Path):
    """The question the version fields exist to answer."""
    store = FeedbackStore(tmp_path / "feedback.jsonl")
    store.append(_decision(action=ReviewerAction.ACCEPTED))
    store.append(_decision(action=ReviewerAction.ACCEPTED))
    store.append(
        _decision(action=ReviewerAction.CORRECTED, corrected_result=CriterionResult.MISSING)
    )

    rates = correction_rate_by_version(store.load())

    assert len(rates) == 1
    assert next(iter(rates.values())) == pytest.approx(1 / 3)


# --- The payoff: a correction becomes a regression case --------------------


def test_a_correction_exports_as_a_valid_matching_gold_record(tmp_path: Path):
    case, policy, evaluation = _evaluation()
    criterion = next(item for item in policy.criteria if item.id == evaluation.criterion_id)
    decision = decision_from_evaluation(
        evaluation,
        reviewer_id="reviewer-01",
        action=ReviewerAction.CORRECTED,
        corrected_result=CriterionResult.HUMAN_REVIEW_REQUIRED,
        note="Two documents disagree about the start date.",
        recorded_at="2026-09-02T10:00:00+00:00",
    )

    record = to_matching_gold_record(decision, case, criterion)

    assert record["expected_result"] == "HUMAN_REVIEW_REQUIRED"
    assert record["split"] == "validation"
    assert "reviewer-01" in record["note"]

    # It must survive the real loader, or it is not a gold record.
    gold_path = tmp_path / "candidate.jsonl"
    export_gold_records(
        gold_path,
        [
            record,
            {**record, "match_id": "T", "split": "test"},
            {**record, "match_id": "C", "split": "challenge"},
        ],
    )
    loaded = load_gold(gold_path)

    assert loaded[0].expected_result is CriterionResult.HUMAN_REVIEW_REQUIRED
    assert loaded[0].note


def test_an_exported_record_only_cites_evidence_the_case_contains():
    """`load_gold` rejects unknown IDs, so the export must not invent any."""
    case, policy, evaluation = _evaluation()
    criterion = next(item for item in policy.criteria if item.id == evaluation.criterion_id)
    decision = decision_from_evaluation(
        evaluation,
        reviewer_id="reviewer-01",
        action=ReviewerAction.CORRECTED,
        corrected_evidence_ids=["D9-EV9-does-not-exist"],
        recorded_at="2026-09-02T10:00:00+00:00",
    )

    record = to_matching_gold_record(decision, case, criterion)

    assert record["expected_evidence_ids"] == []


def test_corrections_default_to_validation_so_the_test_split_stays_frozen():
    """docs/matching-gold.md requires anything driving a change to sit in validation."""
    case, policy, evaluation = _evaluation()
    criterion = next(item for item in policy.criteria if item.id == evaluation.criterion_id)
    decision = _decision(action=ReviewerAction.CORRECTED, corrected_result=CriterionResult.MISSING)

    assert to_matching_gold_record(decision, case, criterion)["split"] == "validation"
