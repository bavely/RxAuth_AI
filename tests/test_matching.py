"""Focused tests for the Phase 5 criteria-to-evidence matching engine."""

from __future__ import annotations

from rxauth_ai.matching import (
    MATCHER_VERSION,
    ModelInterpretation,
    evaluate_criterion,
    retrieve_evidence,
)
from rxauth_ai.models import (
    Case,
    Criterion,
    CriterionResult,
    EvaluationMethod,
    Evidence,
    Provenance,
)


def _criterion(**updates) -> Criterion:
    values = {
        "id": "C1",
        "policy_id": "PA-1",
        "description": "At least 12 weeks of adalimumab.",
        "criterion_type": "previous_therapy",
        "medication": "adalimumab",
        "operator": ">=",
        "expected_value": 12,
        "unit": "weeks",
        "provenance": Provenance(document_id="PA-1", page=3, source_text="12 weeks"),
    }
    values.update(updates)
    return Criterion(**values)


def _evidence(evidence_id: str, **updates) -> Evidence:
    values = {
        "id": evidence_id,
        "evidence_type": "previous_therapy",
        "medication": "adalimumab",
        "value": 12,
        "unit": "weeks",
        "confidence": 0.9,
        "provenance": Provenance(
            document_id=evidence_id.split("-")[0],
            filename=f"{evidence_id}.txt",
            page=1,
            source_text=evidence_id,
        ),
    }
    values.update(updates)
    return Evidence(**values)


def _case(*evidence: Evidence, indication: str = "Example Condition") -> Case:
    return Case(
        id="CASE-1",
        patient_synthetic_id="SYNTH-1",
        payer="Example Health Plan",
        medication="adalimumab",
        indication=indication,
        pa_required=True,
        evidence=list(evidence),
    )


def test_retrieval_uses_alias_normalization_and_ranks_complete_evidence_first():
    incomplete = _evidence("D1-E1", medication="Humira", value=None, confidence=0.99)
    complete = _evidence("D2-E1", medication="adalimumab", value=12, confidence=0.8)

    candidates = retrieve_evidence(_criterion(), _case(incomplete, complete))

    assert [candidate.evidence.id for candidate in candidates] == ["D2-E1", "D1-E1"]
    assert "medication normalized and matched" in candidates[0].reasons


def test_all_supporting_facts_are_cited_instead_of_only_the_highest_confidence_one():
    result = evaluate_criterion(
        _criterion(),
        _case(_evidence("D1-E1", value=12), _evidence("D2-E1", value=16)),
    )

    assert result.result is CriterionResult.SATISFIED
    assert result.supporting_evidence_ids == ["D1-E1", "D2-E1"]
    assert {source.document_id for source in result.patient_evidence_sources} == {"D1", "D2"}
    assert result.matcher_version == MATCHER_VERSION


def test_conflicting_deterministic_facts_route_to_review_with_both_citations():
    result = evaluate_criterion(
        _criterion(),
        _case(_evidence("D1-E1", value=16), _evidence("D2-E1", value=6)),
    )

    assert result.result is CriterionResult.HUMAN_REVIEW_REQUIRED
    assert set(result.supporting_evidence_ids) == {"D1-E1", "D2-E1"}
    assert "conflicts" in result.explanation


def test_exact_days_to_weeks_conversion_is_deterministic_and_traced():
    result = evaluate_criterion(_criterion(), _case(_evidence("D1-E1", value=84, unit="days")))

    assert result.result is CriterionResult.SATISFIED
    assert "converted 84 days to 12 weeks" in result.decision_trace


def test_calendar_months_are_not_approximated_into_weeks():
    result = evaluate_criterion(_criterion(), _case(_evidence("D1-E1", value=3, unit="months")))

    assert result.result is CriterionResult.HUMAN_REVIEW_REQUIRED
    assert "Unit normalization refused" in result.explanation


def test_wrong_diagnosis_does_not_satisfy_a_diagnosis_exists_rule():
    criterion = _criterion(
        criterion_type="diagnosis",
        medication=None,
        operator="exists",
        expected_value=None,
        unit=None,
    )
    evidence = _evidence(
        "D1-E1",
        evidence_type="diagnosis",
        medication=None,
        value=None,
        unit=None,
        text_value="Different Condition",
    )

    assert evaluate_criterion(criterion, _case(evidence)).result is CriterionResult.MISSING


class _Interpreter:
    def __init__(self, confidence: float) -> None:
        self.confidence = confidence

    def interpret(self, **_kwargs) -> ModelInterpretation:
        return ModelInterpretation(
            result=CriterionResult.SATISFIED,
            confidence=self.confidence,
            explanation="The phrase explicitly states a duration above the threshold.",
            model_version="test-interpreter-v1",
        )


def test_default_ambiguity_interpreter_abstains_instead_of_guessing():
    result = evaluate_criterion(_criterion(), _case(_evidence("D1-E1", value=None)))

    assert result.result is CriterionResult.AMBIGUOUS
    assert result.evaluation_method is EvaluationMethod.MODEL_ASSISTED
    assert "interpreter abstained" in result.explanation


def test_typed_high_confidence_model_interpretation_can_be_accepted():
    result = evaluate_criterion(
        _criterion(),
        _case(_evidence("D1-E1", value=None)),
        interpreter=_Interpreter(0.85),
    )

    assert result.result is CriterionResult.SATISFIED
    assert result.evaluation_method is EvaluationMethod.MODEL_ASSISTED


def test_low_confidence_model_interpretation_routes_to_review():
    result = evaluate_criterion(
        _criterion(),
        _case(_evidence("D1-E1", value=None)),
        interpreter=_Interpreter(0.6),
    )

    assert result.result is CriterionResult.HUMAN_REVIEW_REQUIRED
    assert "below the 0.75 model threshold" in result.explanation


def test_low_confidence_criterion_routes_before_patient_retrieval():
    result = evaluate_criterion(_criterion(confidence=0.4), _case(_evidence("D1-E1")))

    assert result.result is CriterionResult.HUMAN_REVIEW_REQUIRED
    assert result.candidate_evidence_ids == []
