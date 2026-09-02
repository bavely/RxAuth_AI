"""Tests for checklist drafting and the claim-level groundedness gate (README section 14)."""

from __future__ import annotations

from rxauth_ai.generation import (
    GENERATOR_VERSION,
    DeterministicChecklistGenerator,
    generate_checklist,
)
from rxauth_ai.groundedness import check_draft_groundedness
from rxauth_ai.models import (
    Case,
    ClaimStatus,
    ClaimType,
    DraftClaim,
    Evidence,
    Provenance,
)
from rxauth_ai.pipeline import run_pipeline
from rxauth_ai.synthetic_case import build_case, build_policy


def _drafted():
    case, policy = build_case(), build_policy()
    report = run_pipeline(case, policy)
    checklist = generate_checklist(report, case, policy)
    return case, policy, report, checklist


def test_every_evaluated_requirement_gets_exactly_one_claim():
    case, _, report, checklist = _drafted()

    assert [claim.criterion_id for claim in checklist.claims] == [
        evaluation.criterion_id for evaluation in report.evaluations
    ]
    assert checklist.generator_version == GENERATOR_VERSION
    # No prompt produced this text, so there is no prompt version to record.
    assert checklist.prompt_version is None


def test_a_checklist_is_never_marked_submittable():
    """README section 20 puts autonomous submission permanently out of scope."""
    _, _, _, checklist = _drafted()

    assert checklist.human_review_required is True
    assert not hasattr(checklist, "submission_ready")
    assert not hasattr(checklist, "approved")


def test_missing_evidence_is_drafted_as_absent_never_guessed():
    case, _, report, checklist = _drafted()
    missing = [
        claim for claim in checklist.claims if claim.claim_type is ClaimType.EVIDENCE_MISSING
    ]

    assert missing, "the synthetic case is built to contain a missing requirement"
    for claim in missing:
        assert "does not document" in claim.text
        assert claim.evidence_ids == []


def test_the_deterministic_draft_passes_its_own_gate():
    case, _, report, checklist = _drafted()

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert gate.passed, gate.issues
    assert gate.status == "PASS"
    assert gate.count(ClaimStatus.GROUNDED) >= 1


# --- What the gate exists to catch -----------------------------------------


def _tamper(checklist, index: int, **updates) -> None:
    checklist.claims[index] = checklist.claims[index].model_copy(update=updates)


def test_gate_catches_a_value_that_appears_in_no_cited_source():
    """The fabrication a model would actually commit: a plausible number."""
    case, _, report, checklist = _drafted()
    supported = next(
        index
        for index, claim in enumerate(checklist.claims)
        if claim.claim_type is ClaimType.REQUIREMENT_MET
    )
    _tamper(checklist, supported, text="The patient completed 47 weeks of therapy with Drug A.")

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert not gate.passed
    assert any("47" in issue for issue in gate.issues)


def test_gate_catches_a_medication_that_appears_in_no_cited_source():
    case, _, report, checklist = _drafted()
    supported = next(
        index
        for index, claim in enumerate(checklist.claims)
        if claim.claim_type is ClaimType.REQUIREMENT_MET
    )
    _tamper(checklist, supported, text="The patient had an inadequate response to adalimumab.")

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert not gate.passed
    assert any("adalimumab" in issue for issue in gate.issues)


def test_gate_catches_a_claim_citing_evidence_the_case_does_not_contain():
    """A generator that invents a fact *and* a citation for it still fails."""
    case, _, report, checklist = _drafted()
    _tamper(checklist, 0, evidence_ids=["D9-EV9"])

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert not gate.passed
    assert any("D9-EV9" in issue for issue in gate.issues)


def test_gate_catches_support_asserted_with_no_evidence_at_all():
    case, _, report, checklist = _drafted()
    supported = next(
        index
        for index, claim in enumerate(checklist.claims)
        if claim.claim_type is ClaimType.REQUIREMENT_MET
    )
    _tamper(checklist, supported, evidence_ids=[])

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert not gate.passed
    assert any("cites no evidence" in issue for issue in gate.issues)


def test_gate_catches_a_claim_that_cites_different_evidence_than_its_evaluation():
    case, _, report, checklist = _drafted()
    other = next(
        evaluation.supporting_evidence_ids
        for evaluation in report.evaluations
        if evaluation.supporting_evidence_ids
        and evaluation.criterion_id != checklist.claims[0].criterion_id
    )
    _tamper(checklist, 0, evidence_ids=list(other))

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert not gate.passed
    assert any(item.status is ClaimStatus.CONFLICTING for item in gate.assessments)


def test_gate_catches_a_claim_with_no_policy_source():
    case, _, report, checklist = _drafted()
    _tamper(checklist, 0, policy_source=None)

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert not gate.passed
    assert any("no policy requirement" in issue for issue in gate.issues)


def test_gate_catches_a_requirement_silently_dropped_from_the_draft():
    """A shorter checklist reads as a readier case, so omission is a failure."""
    case, _, report, checklist = _drafted()
    dropped = checklist.claims.pop()

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert not gate.passed
    assert any(dropped.criterion_id in issue for issue in gate.issues)


def test_gate_rejects_a_claim_about_a_criterion_that_was_never_evaluated():
    case, _, report, checklist = _drafted()
    checklist.claims.append(
        DraftClaim(
            criterion_id="C-INVENTED",
            claim_type=ClaimType.REQUIREMENT_MET,
            text="An unrelated requirement is met.",
            evidence_ids=[],
            policy_source=Provenance(document_id="PA-104", page=3, source_text="invented"),
        )
    )

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert not gate.passed
    assert any("never evaluated" in issue for issue in gate.issues)


def test_restating_the_requirement_is_not_treated_as_a_patient_claim():
    """A threshold from the policy is a legitimate thing for a sentence to contain."""
    case, _, report, checklist = _drafted()
    evaluation = report.evaluations[0]
    _tamper(
        checklist,
        0,
        text=f"Policy requirement: {evaluation.criterion_description}",
    )

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert gate.passed, gate.issues


def test_an_injected_generator_is_held_to_the_same_gate():
    """The seam a model drops into does not come with a wider contract."""

    class FabricatingGenerator:
        version = "fabricator-v0"

        def generate(self, *, report, case, policy):
            base = DeterministicChecklistGenerator().generate(
                report=report, case=case, policy=policy
            )
            return base.model_copy(
                update={
                    "generator_version": self.version,
                    "prompt_version": "draft-v7",
                    "claims": [
                        base.claims[0].model_copy(
                            update={"text": "A1c was 5.1 percent, well within range."}
                        ),
                        *base.claims[1:],
                    ],
                }
            )

    case, policy = build_case(), build_policy()
    report = run_pipeline(case, policy)
    checklist = generate_checklist(report, case, policy, generator=FabricatingGenerator())

    assert checklist.prompt_version == "draft-v7"
    gate = check_draft_groundedness(checklist, report.evaluations, case)
    assert not gate.passed
    assert any("5.1" in issue for issue in gate.issues)


def test_equivalent_number_formatting_is_not_reported_as_invention():
    """`12.0` from a span reading `12` is a copy, not a fabrication."""
    case = Case(
        id="CASE-1",
        patient_synthetic_id="SYN-1",
        payer="Example Health Plan",
        medication="Drug A",
        indication="Example Condition",
        pa_required=True,
        evidence=[
            Evidence(
                id="D1-EV1",
                evidence_type="previous_therapy",
                value=12,
                unit="weeks",
                confidence=0.9,
                provenance=Provenance(
                    document_id="D1", filename="d1.txt", page=1, source_text="12 weeks of therapy"
                ),
            )
        ],
    )
    _, _, report, checklist = _drafted()
    claim = DraftClaim(
        criterion_id=report.evaluations[0].criterion_id,
        claim_type=ClaimType.REQUIREMENT_MET,
        text="Therapy ran 12.0 weeks.",
        evidence_ids=list(report.evaluations[0].supporting_evidence_ids),
        policy_source=report.evaluations[0].policy_source,
        patient_evidence_sources=[
            Provenance(document_id="D1", filename="d1.txt", page=1, source_text="12 weeks")
        ],
    )
    checklist.claims[0] = claim

    gate = check_draft_groundedness(checklist, report.evaluations, case)

    assert not any("12" in issue for issue in gate.issues)
