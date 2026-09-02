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
from .matching import MATCHER_VERSION, evaluate_case
from .models import (
    Case,
    CaseReadinessReport,
    CriterionResult,
    Policy,
)


def run_pipeline(
    case: Case,
    policy: Policy,
    *,
    evidence_requiring_review: int = 0,
    documents_requiring_classification_review: int = 0,
) -> CaseReadinessReport:
    """Evaluate one case against one policy and report how ready it is.

    The two review counters are supplied by whatever produced the case. A
    fixture case has none; a case assembled from real documents reports how
    many extracted fields and how many document classifications a reviewer
    must look at before the criterion results mean anything.
    """
    mismatches = [
        field
        for field in ("payer", "medication", "indication")
        if getattr(case, field).casefold() != getattr(policy, field).casefold()
    ]
    if mismatches:
        fields = ", ".join(mismatches)
        raise ValueError(f"Case and policy do not match on: {fields}.")

    # The matcher computes a conjunction of the criteria. A policy that joins
    # its requirements with ANY means something else entirely, and evaluating
    # it as an AND would report a case as failing requirements the payer never
    # asked it to meet all of. Refuse by name rather than approximate.
    if policy.criteria_connective != "all":
        raise ValueError(
            f"Policy {policy.id} v{policy.version} joins its coverage criteria with "
            f"'{policy.criteria_connective}'. The deterministic matcher evaluates a conjunction, "
            "so this policy cannot be scored automatically; it needs a reviewer or a matcher "
            "that represents disjunction."
        )

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
        # Stamped from the matcher that actually ran. The model's default is a
        # literal (models.py cannot import matching.py without a cycle), so an
        # unset field would quietly advertise a matcher that never saw the case.
        matcher_version=MATCHER_VERSION,
        policy_id=policy.id,
        policy_version=policy.version,
        policy_effective_date=policy.effective_date,
        payer=policy.payer,
        medication=policy.medication,
        indication=policy.indication,
        pa_required=case.pa_required,
        documents_detected=len(case.documents),
        mean_classification_confidence=round(mean_conf, 3),
        documents_requiring_classification_review=documents_requiring_classification_review,
        evidence_total=len(case.evidence),
        evidence_requiring_review=evidence_requiring_review,
        criteria_total=len(evaluations),
        criteria_satisfied=counts[CriterionResult.SATISFIED],
        criteria_not_satisfied=counts[CriterionResult.NOT_SATISFIED],
        criteria_missing=counts[CriterionResult.MISSING],
        criteria_needs_review=needs_review,
        criteria_unstructured=sum(
            1 for criterion in policy.criteria if criterion.criterion_type == "unstructured"
        ),
        policy_exclusions_not_evaluated=len(policy.exclusions),
        groundedness_gate=gate.status,
        evaluations=evaluations,
    )
