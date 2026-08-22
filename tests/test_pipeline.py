"""Tests for Milestone 0 — verify the pipeline exercises all five result states
and that the groundedness gate holds.

Run: uv run pytest
"""

from __future__ import annotations

from rxauth_ai.matching import evaluate_criterion
from rxauth_ai.models import (
    Case,
    Criterion,
    CriterionResult,
    EvaluationMethod,
    Evidence,
    Provenance,
)
from rxauth_ai.pipeline import run_pipeline
from rxauth_ai.synthetic_case import build_case, build_policy


def _case_with(evidence):
    return Case(
        id="T",
        patient_synthetic_id="S",
        payer="P",
        medication="Drug A",
        indication="I",
        pa_required=True,
        documents=[],
        evidence=evidence,
    )


def _num_criterion():
    return Criterion(
        id="C",
        policy_id="PA-1",
        description="Drug A >= 12 weeks",
        criterion_type="previous_therapy",
        medication="Drug A",
        operator=">=",
        expected_value=12,
        unit="weeks",
        provenance=Provenance(page=4, source_text="At least 12 weeks."),
    )


def test_satisfied():
    ev = Evidence(
        id="E",
        evidence_type="previous_therapy",
        medication="Drug A",
        value=16,
        unit="weeks",
        confidence=0.9,
        provenance=Provenance(document_id="D", filename="f.pdf", page=2),
    )
    out = evaluate_criterion(_num_criterion(), _case_with([ev]))
    assert out.result is CriterionResult.SATISFIED
    assert out.evaluation_method is EvaluationMethod.DETERMINISTIC
    assert out.supporting_evidence_ids == ["E"]


def test_not_satisfied():
    ev = Evidence(
        id="E",
        evidence_type="previous_therapy",
        medication="Drug A",
        value=6,
        unit="weeks",
        confidence=0.9,
        provenance=Provenance(document_id="D", filename="f.pdf", page=2),
    )
    out = evaluate_criterion(_num_criterion(), _case_with([ev]))
    assert out.result is CriterionResult.NOT_SATISFIED


def test_missing():
    out = evaluate_criterion(_num_criterion(), _case_with([]))
    assert out.result is CriterionResult.MISSING


def test_ambiguous_when_value_not_numeric():
    ev = Evidence(
        id="E",
        evidence_type="previous_therapy",
        medication="Drug A",
        value=None,
        outcome="several months",
        confidence=0.9,
        provenance=Provenance(document_id="D", filename="f.pdf", page=2),
    )
    out = evaluate_criterion(_num_criterion(), _case_with([ev]))
    assert out.result is CriterionResult.AMBIGUOUS
    # Ambiguity is routed to a human, not guessed.
    assert out.evaluation_method is EvaluationMethod.MODEL_ASSISTED


def test_low_confidence_routes_to_human():
    ev = Evidence(
        id="E",
        evidence_type="previous_therapy",
        medication="Drug A",
        value=16,
        unit="weeks",
        confidence=0.4,
        provenance=Provenance(document_id="D", filename="f.pdf", page=2),
    )
    out = evaluate_criterion(_num_criterion(), _case_with([ev]))
    assert out.result is CriterionResult.HUMAN_REVIEW_REQUIRED


def test_required_outcome_must_match():
    c = _num_criterion()
    c.required_outcome = "inadequate_response"
    ev = Evidence(
        id="E",
        evidence_type="previous_therapy",
        medication="Drug A",
        value=16,
        unit="weeks",
        outcome="good_response",
        confidence=0.9,
        provenance=Provenance(document_id="D", filename="f.pdf", page=2),
    )
    out = evaluate_criterion(c, _case_with([ev]))
    assert out.result is CriterionResult.NOT_SATISFIED


def test_named_medication_requires_named_evidence():
    ev = Evidence(
        id="E",
        evidence_type="previous_therapy",
        medication=None,
        value=16,
        unit="weeks",
        confidence=0.9,
        provenance=Provenance(document_id="D", filename="f.pdf", page=2),
    )
    out = evaluate_criterion(_num_criterion(), _case_with([ev]))
    assert out.result is CriterionResult.MISSING


def test_incompatible_units_route_to_human():
    ev = Evidence(
        id="E",
        evidence_type="previous_therapy",
        medication="Drug A",
        value=4,
        unit="months",
        confidence=0.9,
        provenance=Provenance(document_id="D", filename="f.pdf", page=2),
    )
    out = evaluate_criterion(_num_criterion(), _case_with([ev]))
    assert out.result is CriterionResult.HUMAN_REVIEW_REQUIRED


def test_outcome_only_criterion_checks_outcome():
    criterion = Criterion(
        id="C",
        policy_id="PA-1",
        description="Inadequate response required",
        criterion_type="previous_therapy",
        medication="Drug A",
        required_outcome="inadequate_response",
        provenance=Provenance(page=4, source_text="Inadequate response required."),
    )
    ev = Evidence(
        id="E",
        evidence_type="previous_therapy",
        medication="Drug A",
        outcome="good_response",
        confidence=0.9,
        provenance=Provenance(document_id="D", filename="f.pdf", page=2),
    )
    out = evaluate_criterion(criterion, _case_with([ev]))
    assert out.result is CriterionResult.NOT_SATISFIED


def test_pipeline_rejects_mismatched_policy():
    policy = build_policy().model_copy(update={"payer": "Different Health Plan"})
    try:
        run_pipeline(build_case(), policy)
    except ValueError as exc:
        assert "payer" in str(exc)
    else:
        raise AssertionError("Expected mismatched case and policy to be rejected.")


def test_full_case_all_states_and_gate_passes():
    report = run_pipeline(build_case(), build_policy())
    results = {ev.result for ev in report.evaluations}
    # The synthetic case is designed to hit satisfied, missing, and ambiguous.
    assert CriterionResult.SATISFIED in results
    assert CriterionResult.MISSING in results
    assert CriterionResult.AMBIGUOUS in results
    assert report.groundedness_gate == "PASS"
    assert report.criteria_total == 6
