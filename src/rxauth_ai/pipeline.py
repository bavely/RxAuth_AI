"""End-to-end pipeline for Milestone 0 (README section 23).

Takes a synthetic Case + Policy and produces a CaseReadinessReport by running:
    classification (already on the documents) ->
    evidence (already extracted, with provenance) ->
    criteria matching (deterministic + ambiguity routing) ->
    groundedness gate ->
    readiness report.

In Milestone 0 classification and extraction are pre-supplied by the synthetic
fixture (their real implementations arrive in later phases). What this milestone
proves is the *spine*: structured entities flowing through a deterministic
evaluation core, every result carrying provenance, uncertainty routed to a human,
and a groundedness gate before anything is presented.
"""

from __future__ import annotations

from .groundedness import check_groundedness
from .matching import evaluate_case
from .models import (
    Case,
    CaseReadinessReport,
    CriterionResult,
    Policy,
)


def run_pipeline(case: Case, policy: Policy) -> CaseReadinessReport:
    mismatches = [
        field
        for field in ("payer", "medication", "indication")
        if getattr(case, field).casefold() != getattr(policy, field).casefold()
    ]
    if mismatches:
        fields = ", ".join(mismatches)
        raise ValueError(f"Case and policy do not match on: {fields}.")

    # 1. Evaluate every policy criterion against the case evidence.
    evaluations = evaluate_case(case, policy.criteria)

    # 2. Groundedness gate — nothing is "ready" if a claim lacks a source.
    gate = check_groundedness(evaluations)

    # 3. Tally results.
    counts = {r: 0 for r in CriterionResult}
    for ev in evaluations:
        counts[ev.result] += 1

    needs_review = counts[CriterionResult.AMBIGUOUS] + counts[CriterionResult.HUMAN_REVIEW_REQUIRED]

    mean_conf = (
        sum(d.classification_confidence for d in case.documents) / len(case.documents)
        if case.documents
        else 0.0
    )

    return CaseReadinessReport(
        case_id=case.id,
        policy_id=policy.id,
        payer=policy.payer,
        medication=policy.medication,
        indication=policy.indication,
        pa_required=case.pa_required,
        documents_detected=len(case.documents),
        mean_classification_confidence=round(mean_conf, 3),
        criteria_total=len(evaluations),
        criteria_satisfied=counts[CriterionResult.SATISFIED],
        criteria_not_satisfied=counts[CriterionResult.NOT_SATISFIED],
        criteria_missing=counts[CriterionResult.MISSING],
        criteria_needs_review=needs_review,
        groundedness_gate=gate.status,
        evaluations=evaluations,
    )
