"""Criteria-to-evidence retrieval, normalization, and evaluation (README §12).

The matcher is hybrid by design. Structured facts are retrieved and compared in
plain Python. Genuinely incomplete prose is offered to an injectable ambiguity
interpreter; the offline default abstains, leaving ``AMBIGUOUS`` as a first-class
and citable outcome instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from pydantic import BaseModel, Field, model_validator

from .medications import normalize_medication
from .models import (
    Case,
    Criterion,
    CriterionEvaluation,
    CriterionResult,
    EvaluationMethod,
    Evidence,
    Provenance,
)

MATCHER_VERSION = "evidence-match-v2"
NORMALIZATION_VERSION = "clinical-units-v1"
MATCH_CONFIDENCE_THRESHOLD = 0.65
MODEL_CONFIDENCE_THRESHOLD = 0.75

_OPERATORS = {
    ">=": lambda actual, expected: actual >= expected,
    "<=": lambda actual, expected: actual <= expected,
    ">": lambda actual, expected: actual > expected,
    "<": lambda actual, expected: actual < expected,
    "==": lambda actual, expected: actual == expected,
}


class ModelInterpretation(BaseModel):
    """Typed response accepted from an optional ambiguity interpreter."""

    result: CriterionResult
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = Field(min_length=1)
    model_version: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_interpretable_result(self) -> ModelInterpretation:
        allowed = {
            CriterionResult.SATISFIED,
            CriterionResult.NOT_SATISFIED,
            CriterionResult.AMBIGUOUS,
            CriterionResult.HUMAN_REVIEW_REQUIRED,
        }
        if self.result not in allowed:
            raise ValueError(f"An ambiguity interpreter cannot return {self.result.value}.")
        return self


class AmbiguityInterpreter(Protocol):
    """Model seam; production adapters must return a typed decision or abstain."""

    def interpret(
        self, *, criterion: Criterion, evidence: Evidence, case: Case, reason: str
    ) -> Optional[ModelInterpretation]: ...


class AbstainingAmbiguityInterpreter:
    """Safe offline default: the model-assisted stage runs but never invents a value."""

    def interpret(
        self, *, criterion: Criterion, evidence: Evidence, case: Case, reason: str
    ) -> Optional[ModelInterpretation]:
        return None


@dataclass(frozen=True)
class EvidenceCandidate:
    evidence: Evidence
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class NormalizedValue:
    value: float
    unit: Optional[str]
    explanation: str


@dataclass(frozen=True)
class _CandidateDecision:
    evidence: Evidence
    result: CriterionResult
    confidence: float
    method: EvaluationMethod
    explanation: str
    trace: tuple[str, ...]


def _canonical_medication(value: str) -> str:
    try:
        return normalize_medication(value).casefold()
    except ValueError:
        return " ".join(value.split()).casefold()


def medications_match(left: str, right: str) -> bool:
    """Compare medication aliases through the shared Phase 3 lexicon."""
    return _canonical_medication(left) == _canonical_medication(right)


def _completeness(criterion: Criterion, evidence: Evidence) -> float:
    required: list[bool] = []
    if criterion.operator in _OPERATORS and criterion.expected_value is not None:
        required.append(evidence.value is not None)
    if criterion.required_outcome is not None:
        required.append(evidence.outcome is not None)
    return sum(required) / len(required) if required else 1.0


def retrieve_evidence(criterion: Criterion, case: Case) -> list[EvidenceCandidate]:
    """Retrieve every structurally relevant fact, then rank without discarding alternatives.

    Evidence type and named medication are hard constraints. Diagnosis also has
    to equal the case indication; a diagnosis of a different condition does not
    satisfy a policy merely because both facts have type ``diagnosis``.
    """
    candidates: list[EvidenceCandidate] = []
    for evidence in case.evidence:
        if evidence.evidence_type != criterion.criterion_type:
            continue
        reasons = ["evidence type matched"]
        context_score = 0.2
        if criterion.medication is not None:
            if evidence.medication is None or not medications_match(
                evidence.medication, criterion.medication
            ):
                continue
            reasons.append("medication normalized and matched")
        elif criterion.criterion_type == "diagnosis":
            if (
                evidence.text_value is None
                or evidence.text_value.casefold() != case.indication.casefold()
            ):
                continue
            reasons.append("diagnosis matched the case indication")
        else:
            reasons.append("criterion has no additional context filter")

        completeness = _completeness(criterion, evidence)
        score = round(0.4 + context_score + 0.2 * completeness + 0.2 * evidence.confidence, 6)
        reasons.append(f"required-field completeness={completeness:.2f}")
        reasons.append(f"source confidence={evidence.confidence:.2f}")
        candidates.append(EvidenceCandidate(evidence=evidence, score=score, reasons=tuple(reasons)))
    return sorted(candidates, key=lambda item: (-item.score, item.evidence.id))


def _canonical_unit(unit: Optional[str]) -> Optional[str]:
    if unit is None:
        return None
    aliases = {
        "%": "percent",
        "percent": "percent",
        "day": "days",
        "days": "days",
        "week": "weeks",
        "weeks": "weeks",
        "month": "months",
        "months": "months",
    }
    return aliases.get(unit.strip().casefold(), unit.strip().casefold())


def normalize_for_comparison(evidence: Evidence, criterion: Criterion) -> NormalizedValue:
    """Normalize exact unit aliases and the exact seven-day week conversion.

    Months are intentionally not converted to weeks or days: calendar months do
    not have one exact duration, and silently choosing 30 days could flip a PA
    result at a boundary.
    """
    if evidence.value is None:
        raise ValueError("evidence has no numeric value")
    source_unit = _canonical_unit(evidence.unit)
    target_unit = _canonical_unit(criterion.unit)
    if target_unit is None:
        return NormalizedValue(evidence.value, source_unit, "criterion has no unit constraint")
    if source_unit is None:
        raise ValueError(f"criterion requires {target_unit!r}, but evidence has no unit")
    if source_unit == target_unit:
        return NormalizedValue(evidence.value, target_unit, f"units already agree as {target_unit}")
    duration_days = {"days": 1.0, "weeks": 7.0}
    if source_unit in duration_days and target_unit in duration_days:
        normalized = evidence.value * duration_days[source_unit] / duration_days[target_unit]
        return NormalizedValue(
            normalized,
            target_unit,
            f"converted {evidence.value:g} {source_unit} to {normalized:g} {target_unit}",
        )
    raise ValueError(
        f"cannot safely compare evidence in {source_unit!r} with criterion unit {target_unit!r}"
    )


def _model_assisted_decision(
    criterion: Criterion,
    evidence: Evidence,
    case: Case,
    reason: str,
    interpreter: AmbiguityInterpreter,
) -> _CandidateDecision:
    interpretation = interpreter.interpret(
        criterion=criterion, evidence=evidence, case=case, reason=reason
    )
    if interpretation is None:
        return _CandidateDecision(
            evidence=evidence,
            result=CriterionResult.AMBIGUOUS,
            confidence=min(evidence.confidence, 0.5),
            method=EvaluationMethod.MODEL_ASSISTED,
            explanation=f"{reason}; the ambiguity interpreter abstained, so no value was guessed.",
            trace=("model-assisted interpretation requested", "interpreter abstained"),
        )
    if interpretation.confidence < MODEL_CONFIDENCE_THRESHOLD:
        return _CandidateDecision(
            evidence=evidence,
            result=CriterionResult.HUMAN_REVIEW_REQUIRED,
            confidence=interpretation.confidence,
            method=EvaluationMethod.MODEL_ASSISTED,
            explanation=(
                f"{interpretation.model_version} returned {interpretation.result.value} at "
                f"{interpretation.confidence:.2f}, below the {MODEL_CONFIDENCE_THRESHOLD:.2f} "
                f"model threshold. {interpretation.explanation}"
            ),
            trace=("model-assisted interpretation requested", "model confidence too low"),
        )
    return _CandidateDecision(
        evidence=evidence,
        result=interpretation.result,
        confidence=min(evidence.confidence, interpretation.confidence),
        method=EvaluationMethod.MODEL_ASSISTED,
        explanation=f"{interpretation.model_version}: {interpretation.explanation}",
        trace=("model-assisted interpretation requested", "typed model result accepted"),
    )


def _evaluate_candidate(
    criterion: Criterion,
    evidence: Evidence,
    case: Case,
    interpreter: AmbiguityInterpreter,
) -> _CandidateDecision:
    trace: list[str] = []
    normalized: Optional[NormalizedValue] = None
    if criterion.operator in _OPERATORS and criterion.expected_value is not None:
        if evidence.value is None:
            return _model_assisted_decision(
                criterion,
                evidence,
                case,
                "criterion needs a number but the cited evidence has no explicit numeric value",
                interpreter,
            )
        try:
            normalized = normalize_for_comparison(evidence, criterion)
            trace.append(normalized.explanation)
        except ValueError as exc:
            return _CandidateDecision(
                evidence=evidence,
                result=CriterionResult.HUMAN_REVIEW_REQUIRED,
                confidence=min(evidence.confidence, 0.5),
                method=EvaluationMethod.NONE,
                explanation=f"Unit normalization refused: {exc}.",
                trace=("unit normalization refused",),
            )

    if criterion.required_outcome is not None and evidence.outcome is None:
        return _model_assisted_decision(
            criterion,
            evidence,
            case,
            "criterion needs an outcome but the cited evidence does not state one",
            interpreter,
        )

    if evidence.confidence < MATCH_CONFIDENCE_THRESHOLD:
        return _CandidateDecision(
            evidence=evidence,
            result=CriterionResult.HUMAN_REVIEW_REQUIRED,
            confidence=evidence.confidence,
            method=EvaluationMethod.NONE,
            explanation=(
                f"Extraction confidence {evidence.confidence:.2f} is below the "
                f"{MATCH_CONFIDENCE_THRESHOLD:.2f} matching threshold."
            ),
            trace=(*trace, "source confidence below matching threshold"),
        )

    numeric_ok = True
    comparison_note = ""
    if normalized is not None and criterion.expected_value is not None:
        numeric_ok = _OPERATORS[criterion.operator](normalized.value, criterion.expected_value)
        comparison_note = (
            f"{normalized.value:g} {criterion.operator} {criterion.expected_value:g} is "
            f"{'true' if numeric_ok else 'false'}"
        )
        trace.append("deterministic numeric comparison")

    outcome_ok = True
    outcome_note = ""
    if criterion.required_outcome is not None:
        outcome_ok = (evidence.outcome or "").casefold() == criterion.required_outcome.casefold()
        outcome_note = (
            f"required outcome {criterion.required_outcome!r} "
            f"{'matches' if outcome_ok else 'does not match'} {evidence.outcome!r}"
        )
        trace.append("deterministic outcome comparison")

    passed = numeric_ok and outcome_ok
    detail = "; ".join(note for note in (comparison_note, outcome_note) if note)
    if not detail:
        detail = "required documentation is present"
        trace.append("deterministic existence check")
    return _CandidateDecision(
        evidence=evidence,
        result=CriterionResult.SATISFIED if passed else CriterionResult.NOT_SATISFIED,
        confidence=min(evidence.confidence, criterion.confidence, 0.98),
        method=EvaluationMethod.DETERMINISTIC,
        explanation=detail.capitalize() + ".",
        trace=tuple(trace),
    )


def _unique_sources(decisions: list[_CandidateDecision]) -> list[Provenance]:
    sources: list[Provenance] = []
    seen: set[tuple[object, ...]] = set()
    for decision in decisions:
        for source in decision.evidence.sources:
            key = (
                source.document_id,
                source.filename,
                source.page,
                source.start_char,
                source.end_char,
                source.source_text,
            )
            if key not in seen:
                sources.append(source)
                seen.add(key)
    return sources


def _aggregate(
    criterion: Criterion,
    case: Case,
    candidates: list[EvidenceCandidate],
    decisions: list[_CandidateDecision],
) -> CriterionEvaluation:
    by_result = {
        result: [decision for decision in decisions if decision.result is result]
        for result in CriterionResult
    }
    trace = [
        f"retrieved {len(candidates)} candidate(s): "
        + ", ".join(f"{item.evidence.id}@{item.score:.3f}" for item in candidates)
    ]

    satisfied = by_result[CriterionResult.SATISFIED]
    not_satisfied = by_result[CriterionResult.NOT_SATISFIED]
    if satisfied and not_satisfied:
        selected = [*satisfied, *not_satisfied]
        result = CriterionResult.HUMAN_REVIEW_REQUIRED
        confidence = min(decision.confidence for decision in selected)
        method = EvaluationMethod.DETERMINISTIC
        explanation = (
            "Relevant evidence conflicts: at least one cited fact satisfies the requirement and "
            "at least one cited fact does not. Recency or document context needs human review."
        )
        trace.append("conflicting deterministic results routed to review")
    elif satisfied:
        selected = satisfied
        result = CriterionResult.SATISFIED
        confidence = max(decision.confidence for decision in selected)
        method = (
            EvaluationMethod.MODEL_ASSISTED
            if any(decision.method is EvaluationMethod.MODEL_ASSISTED for decision in selected)
            else EvaluationMethod.DETERMINISTIC
        )
        explanation = "Supporting evidence found. " + " ".join(
            decision.explanation for decision in selected
        )
        trace.append("one or more candidates satisfied the requirement")
    elif by_result[CriterionResult.HUMAN_REVIEW_REQUIRED]:
        selected = by_result[CriterionResult.HUMAN_REVIEW_REQUIRED]
        result = CriterionResult.HUMAN_REVIEW_REQUIRED
        confidence = max(decision.confidence for decision in selected)
        method = (
            EvaluationMethod.MODEL_ASSISTED
            if any(decision.method is EvaluationMethod.MODEL_ASSISTED for decision in selected)
            else EvaluationMethod.NONE
        )
        explanation = " ".join(decision.explanation for decision in selected)
        trace.append("no support; review-required candidate took precedence")
    elif by_result[CriterionResult.AMBIGUOUS]:
        selected = by_result[CriterionResult.AMBIGUOUS]
        result = CriterionResult.AMBIGUOUS
        confidence = max(decision.confidence for decision in selected)
        method = EvaluationMethod.MODEL_ASSISTED
        explanation = " ".join(decision.explanation for decision in selected)
        trace.append("no deterministic answer; ambiguity preserved")
    else:
        selected = not_satisfied
        result = CriterionResult.NOT_SATISFIED
        confidence = max(decision.confidence for decision in selected)
        method = EvaluationMethod.DETERMINISTIC
        explanation = "All relevant evidence failed the requirement. " + " ".join(
            decision.explanation for decision in selected
        )
        trace.append("all relevant candidates failed the requirement")

    sources = _unique_sources(selected)
    trace.extend(item for decision in selected for item in decision.trace)
    return CriterionEvaluation(
        criterion_id=criterion.id,
        case_id=case.id,
        result=result,
        supporting_evidence_ids=[decision.evidence.id for decision in selected],
        candidate_evidence_ids=[candidate.evidence.id for candidate in candidates],
        confidence=confidence,
        evaluation_method=method,
        matcher_version=MATCHER_VERSION,
        normalization_version=NORMALIZATION_VERSION,
        decision_trace=trace,
        explanation=explanation,
        criterion_description=criterion.description,
        policy_source=criterion.provenance,
        patient_evidence_source=sources[0] if sources else None,
        patient_evidence_sources=sources,
    )


def evaluate_criterion(
    criterion: Criterion,
    case: Case,
    *,
    interpreter: Optional[AmbiguityInterpreter] = None,
) -> CriterionEvaluation:
    """Retrieve, normalize, evaluate, and aggregate one policy requirement."""
    base = dict(
        criterion_id=criterion.id,
        case_id=case.id,
        criterion_description=criterion.description,
        policy_source=criterion.provenance,
        matcher_version=MATCHER_VERSION,
        normalization_version=NORMALIZATION_VERSION,
    )
    if criterion.criterion_type == "unstructured":
        return CriterionEvaluation(
            result=CriterionResult.HUMAN_REVIEW_REQUIRED,
            confidence=criterion.confidence,
            evaluation_method=EvaluationMethod.NONE,
            decision_trace=["criterion extractor retained an unstructured requirement"],
            explanation=(
                "This cited policy requirement could not be converted into a deterministic "
                "check. A reviewer must read the policy text and decide."
            ),
            **base,
        )
    if criterion.confidence < MATCH_CONFIDENCE_THRESHOLD:
        return CriterionEvaluation(
            result=CriterionResult.HUMAN_REVIEW_REQUIRED,
            confidence=criterion.confidence,
            evaluation_method=EvaluationMethod.NONE,
            decision_trace=["criterion confidence below matching threshold"],
            explanation=(
                f"Criterion extraction confidence {criterion.confidence:.2f} is below the "
                f"{MATCH_CONFIDENCE_THRESHOLD:.2f} matching threshold."
            ),
            **base,
        )

    candidates = retrieve_evidence(criterion, case)
    if not candidates:
        return CriterionEvaluation(
            result=CriterionResult.MISSING,
            confidence=min(criterion.confidence, 0.99),
            evaluation_method=EvaluationMethod.DETERMINISTIC,
            decision_trace=["structured retrieval returned no relevant evidence"],
            explanation=(
                f"No relevant evidence of type {criterion.criterion_type!r}"
                + (f" for {criterion.medication}" if criterion.medication else "")
                + " was found in the case."
            ),
            **base,
        )

    active_interpreter = interpreter or AbstainingAmbiguityInterpreter()
    decisions = [
        _evaluate_candidate(criterion, candidate.evidence, case, active_interpreter)
        for candidate in candidates
    ]
    return _aggregate(criterion, case, candidates, decisions)


def evaluate_case(
    case: Case,
    criteria: list[Criterion],
    *,
    interpreter: Optional[AmbiguityInterpreter] = None,
) -> list[CriterionEvaluation]:
    """Evaluate every inclusion criterion; exclusions remain visible but unscored."""
    return [
        evaluate_criterion(criterion, case, interpreter=interpreter)
        for criterion in criteria
        if criterion.polarity == "inclusion"
    ]
