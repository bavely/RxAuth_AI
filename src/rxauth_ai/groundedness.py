"""Groundedness / citation gate (README section 14).

Before any result is shown to a reviewer, this gate checks that every evaluation
is traceable to a source. In Milestone 0 there is no generated prose to fact-check,
so the gate enforces the structural version of groundedness:

  - every SATISFIED / NOT_SATISFIED result must cite at least one piece of
    patient evidence AND a policy source,
  - MISSING results must cite a policy source (we must know what was required),
  - nothing may claim support it does not have.

A later phase adds LLM-drafted content and a semantic faithfulness check
(e.g. Ragas). The interface stays the same: the gate returns PASS or FAIL plus
the list of any ungrounded claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import CriterionEvaluation, CriterionResult


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
