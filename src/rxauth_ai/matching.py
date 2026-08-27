"""Criteria-to-evidence matching — the core intelligence layer (README section 12).

Design principle (README section 7): use deterministic Python where a rule can be
represented explicitly, and only fall back to model-assisted interpretation when
the evidence is genuinely ambiguous. Milestone 0 implements the deterministic path
in full and *marks* where model-assisted interpretation would take over, without
calling any model — an ambiguous value is routed to a human instead of guessed.

Every evaluation returns a five-state result:
    SATISFIED / NOT_SATISFIED / MISSING / AMBIGUOUS / HUMAN_REVIEW_REQUIRED
"""

from __future__ import annotations

from typing import Optional

from .models import (
    Case,
    Criterion,
    CriterionEvaluation,
    CriterionResult,
    EvaluationMethod,
    Evidence,
)

# Operators we can evaluate deterministically.
_OPERATORS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
}


def _find_evidence(criterion: Criterion, case: Case) -> Optional[Evidence]:
    """Match a criterion to the most relevant piece of case evidence.

    Milestone 0 uses a simple typed match: same evidence_type as the criterion
    type, and same medication when the criterion names one. A later phase would
    replace this with retrieval + model-assisted linking.
    """
    candidates = [e for e in case.evidence if e.evidence_type == criterion.criterion_type]
    if criterion.medication is not None:
        candidates = [
            e
            for e in candidates
            if e.medication is not None
            and e.medication.casefold() == criterion.medication.casefold()
        ]
    if not candidates:
        return None
    # Prefer the highest-confidence candidate.
    return max(candidates, key=lambda e: e.confidence)


def evaluate_criterion(criterion: Criterion, case: Case) -> CriterionEvaluation:
    evidence = _find_evidence(criterion, case)

    base = dict(
        criterion_id=criterion.id,
        case_id=case.id,
        criterion_description=criterion.description,
        policy_source=criterion.provenance,
    )

    # 1. No evidence at all -> MISSING (deterministic, high confidence).
    if evidence is None:
        return CriterionEvaluation(
            result=CriterionResult.MISSING,
            supporting_evidence_ids=[],
            confidence=0.99,
            evaluation_method=EvaluationMethod.DETERMINISTIC,
            explanation=(
                f"No evidence of type '{criterion.criterion_type}'"
                + (f" for {criterion.medication}" if criterion.medication else "")
                + " found in the case."
            ),
            **base,
        )

    base["patient_evidence_source"] = evidence.provenance
    base["patient_evidence_sources"] = evidence.sources

    # 2. Low-confidence extraction -> route to a human, do not guess.
    if evidence.confidence < 0.60:
        return CriterionEvaluation(
            result=CriterionResult.HUMAN_REVIEW_REQUIRED,
            supporting_evidence_ids=[evidence.id],
            confidence=evidence.confidence,
            evaluation_method=EvaluationMethod.NONE,
            explanation=(
                f"Matching evidence found but extraction confidence "
                f"({evidence.confidence:.2f}) is below threshold. Human review required."
            ),
            **base,
        )

    # 3. Value could not be normalized to a number, but the criterion needs one
    #    -> this is where model-assisted interpretation would take over. In
    #    Milestone 0 we mark it AMBIGUOUS and route to a human rather than call a model.
    if criterion.operator in _OPERATORS and criterion.expected_value is not None:
        if evidence.value is None:
            return CriterionEvaluation(
                result=CriterionResult.AMBIGUOUS,
                supporting_evidence_ids=[evidence.id],
                confidence=0.50,
                evaluation_method=EvaluationMethod.MODEL_ASSISTED,
                explanation=(
                    "Criterion requires a numeric comparison but the evidence value "
                    "is not explicit enough for a deterministic check. In a later "
                    "phase a model would attempt interpretation; here it is routed "
                    "to human review."
                ),
                **base,
            )

        if (
            criterion.unit is not None
            and evidence.unit is not None
            and criterion.unit.casefold() != evidence.unit.casefold()
        ):
            return CriterionEvaluation(
                result=CriterionResult.HUMAN_REVIEW_REQUIRED,
                supporting_evidence_ids=[evidence.id],
                confidence=min(evidence.confidence, 0.50),
                evaluation_method=EvaluationMethod.NONE,
                explanation=(
                    f"Cannot compare evidence in {evidence.unit!r} with a criterion in "
                    f"{criterion.unit!r} without an explicit unit conversion."
                ),
                **base,
            )

        # 4. Deterministic numeric comparison.
        op = _OPERATORS[criterion.operator]
        passed = op(evidence.value, criterion.expected_value)

        # If the criterion also requires an outcome, check it too.
        outcome_ok = True
        outcome_note = ""
        if criterion.required_outcome is not None:
            outcome_ok = (evidence.outcome or "").lower() == criterion.required_outcome.lower()
            outcome_note = (
                f"; required outcome '{criterion.required_outcome}' "
                f"{'met' if outcome_ok else 'not met'} (evidence: '{evidence.outcome}')"
            )

        result = (
            CriterionResult.SATISFIED if (passed and outcome_ok) else CriterionResult.NOT_SATISFIED
        )
        return CriterionEvaluation(
            result=result,
            supporting_evidence_ids=[evidence.id],
            confidence=min(evidence.confidence, 0.98),
            evaluation_method=EvaluationMethod.DETERMINISTIC,
            explanation=(
                f"{evidence.value} {criterion.operator} {criterion.expected_value} "
                f"-> {'true' if passed else 'false'}{outcome_note}."
            ),
            **base,
        )

    # 5. Outcome-only criterion and evidence is present.
    if criterion.required_outcome is not None:
        outcome_ok = (evidence.outcome or "").casefold() == criterion.required_outcome.casefold()
        return CriterionEvaluation(
            result=(CriterionResult.SATISFIED if outcome_ok else CriterionResult.NOT_SATISFIED),
            supporting_evidence_ids=[evidence.id],
            confidence=min(evidence.confidence, 0.98),
            evaluation_method=EvaluationMethod.DETERMINISTIC,
            explanation=(
                f"Required outcome {criterion.required_outcome!r} "
                f"{'matches' if outcome_ok else 'does not match'} evidence outcome "
                f"{evidence.outcome!r}."
            ),
            **base,
        )

    # 6. Existence-only criterion (no numeric/outcome comparison) and evidence is present.
    return CriterionEvaluation(
        result=CriterionResult.SATISFIED,
        supporting_evidence_ids=[evidence.id],
        confidence=min(evidence.confidence, 0.98),
        evaluation_method=EvaluationMethod.DETERMINISTIC,
        explanation="Required documentation is present in the case.",
        **base,
    )


def evaluate_case(case: Case, criteria: list[Criterion]) -> list[CriterionEvaluation]:
    return [evaluate_criterion(c, case) for c in criteria]
