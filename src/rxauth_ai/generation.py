"""Requirement-checklist drafting (README section 14).

The generator turns criterion evaluations into the sentences a reviewer reads.
It is the first component in the project whose output is prose, which makes it
the first that could invent something — a duration nobody documented, a lab
value that reads plausibly, a requirement the policy never stated.

Two rules keep that from being possible here:

1. **Nothing is written that is not already structured.** Every sentence is
   assembled from a criterion the policy states and evidence the case
   contains, and quotes the source span verbatim rather than paraphrasing it.
   Missing evidence produces "not documented", never a guess.
2. **Every sentence carries its sources.** A claim without a policy span, or a
   support claim without patient evidence, cannot be constructed — and
   `groundedness.check_draft_groundedness` re-derives that independently
   rather than trusting the generator that produced it.

`DraftGenerator` is the seam a prompted model drops into. When one does, the
gate does not change and does not become more trusting: it already assumes the
text may be wrong and checks it against the structured record.
"""

from __future__ import annotations

from typing import Optional, Protocol

from .models import (
    Case,
    CaseReadinessReport,
    ClaimType,
    CriterionEvaluation,
    CriterionResult,
    DraftClaim,
    Policy,
    RequirementChecklist,
)

GENERATOR_VERSION = "checklist-v1"

#: Which claim each of the five results produces. Kept as a table rather than a
#: chain of conditionals so that adding a result to `CriterionResult` without
#: deciding how it should read fails loudly here instead of drafting silence.
_CLAIM_TYPES: dict[CriterionResult, ClaimType] = {
    CriterionResult.SATISFIED: ClaimType.REQUIREMENT_MET,
    CriterionResult.NOT_SATISFIED: ClaimType.REQUIREMENT_NOT_MET,
    CriterionResult.MISSING: ClaimType.EVIDENCE_MISSING,
    CriterionResult.AMBIGUOUS: ClaimType.NEEDS_REVIEW,
    CriterionResult.HUMAN_REVIEW_REQUIRED: ClaimType.NEEDS_REVIEW,
}

_OPENING: dict[CriterionResult, str] = {
    CriterionResult.SATISFIED: "The record supports this requirement.",
    CriterionResult.NOT_SATISFIED: "The record does not meet this requirement.",
    CriterionResult.MISSING: "The record does not document this requirement.",
    CriterionResult.AMBIGUOUS: "The record addresses this requirement without stating it "
    "precisely enough to check.",
    CriterionResult.HUMAN_REVIEW_REQUIRED: "This requirement needs a reviewer.",
}


class DraftGenerator(Protocol):
    """Anything that can draft a checklist from an evaluated case.

    A model-backed implementation returns the same typed object and is held to
    the same gate; it does not get a wider contract for being a model.
    """

    def generate(
        self, *, report: CaseReadinessReport, case: Case, policy: Policy
    ) -> RequirementChecklist: ...


def _quote_sources(evaluation: CriterionEvaluation) -> str:
    """Quote the cited spans verbatim, without citation decoration.

    Verbatim is the point. Every number and medication name in a drafted
    sentence then provably came from a span the reviewer can open, which is
    exactly what the groundedness gate checks for.

    The filename and page are deliberately *not* interpolated into the
    sentence. They are already structured on the claim, the CLI renders them
    beside it, and folding them into prose put the digits of
    `03_clinical_note.txt` into the text where the gate correctly — and
    uselessly — read them as values with no source.
    """
    quotes = [
        f'"{source.source_text}"'
        for source in evaluation.patient_evidence_sources
        if source.source_text
    ]
    if not quotes:
        return ""
    return " Documented: " + "; ".join(quotes) + "."


class DeterministicChecklistGenerator:
    """Assembles the checklist from structured results without a model.

    This is not a placeholder for a model that would do it better. A
    deterministic drafter cannot hallucinate, needs no BAA to see a patient
    record, and produces the same sentence for the same evidence every time —
    which is what makes the output diffable across runs and reviewable as
    evidence. A model earns its place here only by being measurably clearer,
    and `reports/generation_faithfulness.md` is where that would be shown.
    """

    version = GENERATOR_VERSION

    def generate(
        self, *, report: CaseReadinessReport, case: Case, policy: Policy
    ) -> RequirementChecklist:
        claims = [self._claim(evaluation) for evaluation in report.evaluations]
        return RequirementChecklist(
            case_id=report.case_id,
            policy_id=report.policy_id,
            policy_version=report.policy_version,
            generator_version=self.version,
            prompt_version=None,
            claims=claims,
            # A checklist is a draft for a person, always. The only case that
            # would not need review is one with nothing outstanding and no
            # exclusion left unevaluated — and this system never evaluates
            # exclusions, so that case does not exist.
            human_review_required=True,
        )

    def _claim(self, evaluation: CriterionEvaluation) -> DraftClaim:
        opening = _OPENING[evaluation.result]
        description = evaluation.criterion_description.strip()
        text = f"{description} {opening}{_quote_sources(evaluation)}"
        return DraftClaim(
            criterion_id=evaluation.criterion_id,
            claim_type=_CLAIM_TYPES[evaluation.result],
            text=" ".join(text.split()),
            evidence_ids=list(evaluation.supporting_evidence_ids),
            policy_source=evaluation.policy_source,
            patient_evidence_sources=list(evaluation.patient_evidence_sources),
        )


def generate_checklist(
    report: CaseReadinessReport,
    case: Case,
    policy: Policy,
    *,
    generator: Optional[DraftGenerator] = None,
) -> RequirementChecklist:
    """Draft the reviewer-facing checklist for one evaluated case."""
    active = generator or DeterministicChecklistGenerator()
    return active.generate(report=report, case=case, policy=policy)
