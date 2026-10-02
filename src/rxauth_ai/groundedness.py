"""Groundedness / citation gate (README section 14).

Before any result is shown to a reviewer, this gate checks that every evaluation
is traceable to a source. In Milestone 0 there is no generated prose to fact-check,
so the gate enforces the structural version of groundedness:

  - every SATISFIED / NOT_SATISFIED result must cite at least one piece of
    patient evidence AND a policy source,
  - MISSING results must cite a policy source (we must know what was required),
  - nothing may claim support it does not have.

README section 14 adds drafted prose, and `check_draft_groundedness` below
holds it to the same standard: every sentence must attach to a policy span,
every sentence that asserts support must cite patient evidence that exists,
and — the part that matters once a model writes the text — every number and
every medication name in the sentence must appear in a span the sentence
cites. A drafted duration that is not in the record is caught as a fabrication
rather than read as a claim, which is the failure mode a semantic scorer such
as Ragas would only ever assign a low score to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .medications import MEDICATION_ALIASES
from .models import (
    Case,
    ClaimAssessment,
    ClaimStatus,
    ClaimType,
    CriterionEvaluation,
    CriterionResult,
    DraftClaim,
    DraftGroundedness,
    RequirementChecklist,
)


@dataclass
class GroundednessResult:
    passed: bool
    issues: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "PASS" if self.passed else "FAIL"


_NEEDS_PATIENT_EVIDENCE = {
    CriterionResult.SATISFIED,
    CriterionResult.NOT_SATISFIED,
}


def check_groundedness(evaluations: list[CriterionEvaluation]) -> GroundednessResult:
    issues: list[str] = []

    for ev in evaluations:
        if ev.supporting_evidence_ids and ev.patient_evidence_source is None:
            issues.append(f"{ev.criterion_id}: cited patient evidence has no provenance.")

        # Any concrete satisfied/not-satisfied claim must cite patient evidence.
        if ev.result in _NEEDS_PATIENT_EVIDENCE:
            if not ev.supporting_evidence_ids:
                issues.append(
                    f"{ev.criterion_id}: result {ev.result.value} claims support "
                    f"but cites no patient evidence."
                )
        # Every evaluation must know which policy requirement it came from.
        if ev.policy_source is None:
            issues.append(f"{ev.criterion_id}: evaluation has no policy source provenance.")

    return GroundednessResult(passed=not issues, issues=issues)


# --- Claim-level gate for drafted prose (README section 14) ----------------

#: Numbers as a reader would say them. `12` and `12.0` are the same claim, so
#: trailing zeros are normalized away before comparison; otherwise a generator
#: that wrote "12.0 weeks" from a span reading "12 weeks" would be accused of
#: inventing a value it copied correctly.
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

_CLAIMS_ASSERTING_SUPPORT = {ClaimType.REQUIREMENT_MET}


def _normalize_number(raw: str) -> str:
    value = float(raw)
    return f"{value:g}"


def _numbers_in(text: str) -> set[str]:
    return {_normalize_number(match.group()) for match in _NUMBER.finditer(text or "")}


def _medications_in(text: str) -> set[str]:
    """Medication names the shared lexicon recognizes, matched whole-word."""
    lowered = (text or "").casefold()
    found = set()
    for alias in MEDICATION_ALIASES:
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", lowered):
            found.add(alias)
    return found


def _supporting_text(claim: DraftClaim, evaluation: CriterionEvaluation | None) -> str:
    """Everything the claim is allowed to have drawn its wording from."""
    parts: list[str] = []
    for source in claim.patient_evidence_sources:
        # The filename and page are part of the citation, so a generator that
        # writes them into the sentence is quoting the record, not inventing.
        parts += [source.source_text or "", source.filename or "", str(source.page or "")]
    if claim.policy_source is not None:
        parts += [
            claim.policy_source.source_text or "",
            claim.policy_source.filename or "",
            str(claim.policy_source.page or ""),
        ]
    if evaluation is not None:
        # The requirement's own wording is a legitimate source: restating what
        # the policy asks for is not a claim about the patient.
        parts.append(evaluation.criterion_description)
        if evaluation.policy_source is not None:
            parts.append(evaluation.policy_source.source_text or "")
    return " ".join(parts)


def _assess_claim(
    claim: DraftClaim,
    evaluation: CriterionEvaluation | None,
    known_evidence_ids: set[str],
) -> ClaimAssessment:
    def verdict(status: ClaimStatus, reason: str) -> ClaimAssessment:
        return ClaimAssessment(
            criterion_id=claim.criterion_id,
            claim_type=claim.claim_type,
            status=status,
            reason=reason,
        )

    if evaluation is None:
        return verdict(
            ClaimStatus.UNSUPPORTED,
            "Claim refers to a criterion that was never evaluated for this case.",
        )
    if claim.policy_source is None:
        return verdict(ClaimStatus.UNSUPPORTED, "Claim cites no policy requirement.")

    invented_ids = sorted(set(claim.evidence_ids) - known_evidence_ids)
    if invented_ids:
        return verdict(
            ClaimStatus.UNSUPPORTED,
            f"Claim cites evidence absent from the case: {', '.join(invented_ids)}.",
        )

    if claim.claim_type in _CLAIMS_ASSERTING_SUPPORT and not claim.evidence_ids:
        return verdict(
            ClaimStatus.UNSUPPORTED, "Claim asserts the requirement is met but cites no evidence."
        )

    if set(claim.evidence_ids) != set(evaluation.supporting_evidence_ids):
        return verdict(
            ClaimStatus.CONFLICTING,
            "Claim cites different evidence than the evaluation it describes.",
        )

    supporting = _supporting_text(claim, evaluation)
    unsupported_numbers = sorted(_numbers_in(claim.text) - _numbers_in(supporting))
    if unsupported_numbers:
        return verdict(
            ClaimStatus.UNSUPPORTED,
            f"Claim states values that appear in no cited source: {', '.join(unsupported_numbers)}.",
        )

    unsupported_medications = sorted(_medications_in(claim.text) - _medications_in(supporting))
    if unsupported_medications:
        return verdict(
            ClaimStatus.UNSUPPORTED,
            "Claim names medications that appear in no cited source: "
            f"{', '.join(unsupported_medications)}.",
        )

    if claim.claim_type is ClaimType.NEEDS_REVIEW:
        return verdict(ClaimStatus.REQUIRES_REVIEW, "Routed to a reviewer by the evaluation.")
    if claim.claim_type is ClaimType.EVIDENCE_MISSING:
        return verdict(
            ClaimStatus.PARTIALLY_GROUNDED,
            "Grounded in the policy requirement; the record documents nothing to cite.",
        )
    return verdict(ClaimStatus.GROUNDED, "Every value in the claim appears in a cited source.")


def check_draft_groundedness(
    checklist: RequirementChecklist,
    evaluations: list[CriterionEvaluation],
    case: Case,
) -> DraftGroundedness:
    """Check drafted prose against the structured record it claims to describe.

    The gate re-derives support from the case rather than trusting the
    generator's own citations, so a generator that both invents a fact and
    invents a citation for it still fails.
    """
    by_criterion = {evaluation.criterion_id: evaluation for evaluation in evaluations}
    known_evidence_ids = {item.id for item in case.evidence}

    assessments = [
        _assess_claim(claim, by_criterion.get(claim.criterion_id), known_evidence_ids)
        for claim in checklist.claims
    ]

    issues = [
        f"{item.criterion_id}: {item.reason}"
        for item in assessments
        if item.status in (ClaimStatus.UNSUPPORTED, ClaimStatus.CONFLICTING)
    ]

    missing = sorted(by_criterion.keys() - {claim.criterion_id for claim in checklist.claims})
    issues.extend(
        f"{criterion_id}: evaluated requirement is absent from the checklist."
        for criterion_id in missing
    )

    return DraftGroundedness(passed=not issues, assessments=assessments, issues=issues)
