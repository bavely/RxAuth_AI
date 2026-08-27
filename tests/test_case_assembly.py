"""Tests for real-document case assembly (README section 23 / docs/case-assembly.md).

Milestone 0 proved the spine with hand-authored evidence. These tests hold the
line that the spine still produces the same criterion outcomes when the
evidence comes from the real extractor running over real files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rxauth_ai.case_assembly import (
    CaseManifest,
    assemble_case,
    case_document_paths,
    link_cross_document_evidence,
    load_classifier,
    load_manifest,
    request_date_for,
    resolve_policy,
    run_case,
)
from rxauth_ai.models import CriterionResult, Document, DocumentType, Evidence, Provenance
from rxauth_ai.policy_retrieval import PolicyNotFoundError, build_index

_ROOT = Path(__file__).resolve().parents[1]
_CASE_DIR = _ROOT / "data" / "cases" / "PA-CASE-001"
_POLICY_DIR = _ROOT / "data" / "policies"


@pytest.fixture(scope="module")
def policy_index():
    """One index for the module: parsing and embedding the corpus is pure setup."""
    return build_index(_POLICY_DIR)


class _FilenameClassifier:
    """Stand-in for the trained baseline, so tests need no build artifact.

    `assemble_case` takes any object satisfying `DocumentClassifierLike`, which
    is what lets the classification stage be swapped without touching the rest
    of the assembly.
    """

    def __init__(self, confidence: float = 0.95) -> None:
        self.confidence = confidence

    def classify_path(self, path: Path, *, document_id: str) -> tuple[Document, bool]:
        label = next(
            (document_type for document_type in DocumentType if document_type.value in path.stem),
            DocumentType.OTHER,
        )
        document = Document(
            id=document_id,
            filename=path.name,
            document_type=label,
            classification_confidence=self.confidence,
        )
        return document, self.confidence < 0.65


def test_case_packet_declares_every_non_inferrable_fact():
    manifest = load_manifest(_CASE_DIR)

    assert isinstance(manifest, CaseManifest)
    assert manifest.case_id == "PA-CASE-001"
    assert manifest.policy_id == "PA-104"
    # README section 3: the PA trigger is declared input, never read out of a policy.
    assert manifest.pa_required is True


def test_assembles_every_document_in_the_packet():
    assembled = assemble_case(_CASE_DIR, classifier=_FilenameClassifier())

    assert len(assembled.case.documents) == len(case_document_paths(_CASE_DIR))
    assert [document.document_type for document in assembled.case.documents] == [
        DocumentType.PA_REQUEST,
        DocumentType.INSURANCE_CARD,
        DocumentType.CLINICAL_NOTE,
        DocumentType.MEDICATION_HISTORY,
        DocumentType.LAB_REPORT,
    ]
    assert assembled.case.evidence, "the packet should yield extracted evidence"


def test_evidence_ids_are_unique_across_the_whole_assembled_case():
    assembled = assemble_case(_CASE_DIR, classifier=_FilenameClassifier())
    ids = [item.id for item in assembled.case.evidence]

    assert len(ids) == len(set(ids))


def test_every_extracted_value_traces_back_to_a_document_in_the_packet():
    assembled = assemble_case(_CASE_DIR, classifier=_FilenameClassifier())
    filenames = {path.name for path in case_document_paths(_CASE_DIR)}

    for item in assembled.case.evidence:
        for provenance in item.sources:
            assert provenance.document_id is not None
            assert provenance.filename in filenames
            assert provenance.source_text
            assert provenance.start_char is not None


def test_exact_facts_are_linked_across_documents_without_merging_their_sources():
    assembled = assemble_case(_CASE_DIR, classifier=_FilenameClassifier())
    links_by_type = {link.evidence_type: link for link in assembled.evidence_links}

    assert {"diagnosis", "payer"}.issubset(links_by_type)
    diagnosis = links_by_type["diagnosis"]
    assert len(diagnosis.evidence_ids) == 2
    assert set(diagnosis.document_ids) == {"D1", "D3"}
    assert {source.document_id for source in diagnosis.provenance} == {"D1", "D3"}

    # Linking is a relationship; the original document-scoped facts stay intact.
    linked_items = [item for item in assembled.case.evidence if item.id in diagnosis.evidence_ids]
    assert len(linked_items) == 2
    assert all(len(item.sources) == 1 for item in linked_items)


def test_cross_document_linking_does_not_complete_two_partial_therapy_facts():
    duration = Evidence(
        id="D1-EV1",
        evidence_type="previous_therapy",
        medication="Drug A",
        value=12,
        unit="weeks",
        confidence=0.9,
        provenance=Provenance(document_id="D1", source_text="12 weeks"),
    )
    outcome = Evidence(
        id="D2-EV1",
        evidence_type="previous_therapy",
        medication="Drug A",
        outcome="inadequate_response",
        confidence=0.9,
        provenance=Provenance(document_id="D2", source_text="inadequate response"),
    )

    assert link_cross_document_evidence([duration, outcome]) == []


def test_real_extraction_reproduces_the_milestone_zero_criterion_profile(policy_index):
    """The whole point of the phase: swapping fixtures for real components must
    not change what the reviewer is told about the case."""
    report, _, _ = run_case(_CASE_DIR, classifier=_FilenameClassifier(), index=policy_index)
    results = {evaluation.criterion_id: evaluation.result for evaluation in report.evaluations}

    assert results == {
        "C1": CriterionResult.SATISFIED,
        "C2": CriterionResult.SATISFIED,
        "C3": CriterionResult.SATISFIED,
        "C4": CriterionResult.SATISFIED,
        "C5": CriterionResult.MISSING,
        "C6": CriterionResult.AMBIGUOUS,
    }
    assert report.groundedness_gate == "PASS"


def test_a_linked_fact_cites_both_of_its_spans_in_the_criterion_evaluation(policy_index):
    """C2 and C3 are only satisfiable because the duration and the outcome were
    linked across two lines — so the evaluation must cite both."""
    report, _, _ = run_case(_CASE_DIR, classifier=_FilenameClassifier(), index=policy_index)
    evaluation = next(e for e in report.evaluations if e.criterion_id == "C3")

    assert evaluation.result is CriterionResult.SATISFIED
    assert len(evaluation.patient_evidence_sources) == 2
    cited = " ".join(
        provenance.source_text or "" for provenance in evaluation.patient_evidence_sources
    )
    assert "16 weeks" in cited
    assert "inadequate response" in cited
    assert evaluation.patient_evidence_source == evaluation.patient_evidence_sources[0]


def test_the_report_counts_what_a_reviewer_still_has_to_look_at(policy_index):
    report, assembled, _ = run_case(_CASE_DIR, classifier=_FilenameClassifier(), index=policy_index)

    assert report.evidence_total == len(assembled.case.evidence)
    assert report.evidence_requiring_review == len(assembled.extraction_issues)
    assert report.evidence_requiring_review >= 1  # the vague therapy duration
    assert report.documents_requiring_classification_review == 0


def test_low_confidence_classification_is_reported_not_swallowed(policy_index):
    report, assembled, _ = run_case(
        _CASE_DIR, classifier=_FilenameClassifier(confidence=0.4), index=policy_index
    )

    assert len(assembled.documents_requiring_review) == len(assembled.case.documents)
    assert report.documents_requiring_classification_review == len(assembled.case.documents)


def test_missing_manifest_says_what_a_case_packet_needs(tmp_path):
    (tmp_path / "note.txt").write_text("Diagnosis: Example Condition.", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="pa_required"):
        assemble_case(tmp_path, classifier=_FilenameClassifier())


def test_packet_with_no_ingestable_documents_is_rejected(tmp_path):
    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "case_id": "T",
                "patient_synthetic_id": "S",
                "payer": "Example Health Plan",
                "medication": "Drug A",
                "indication": "Example Condition",
                "pa_required": True,
                "policy_id": "PA-104",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="No ingestable documents"):
        assemble_case(tmp_path, classifier=_FilenameClassifier())


def test_packet_asserting_the_wrong_policy_is_rejected(policy_index):
    """A packet may name a policy, but retrieval decides. When the two disagree
    one of them is wrong about the case, and neither may be trusted silently."""
    assembled = assemble_case(_CASE_DIR, classifier=_FilenameClassifier())
    assembled.manifest.policy_id = "PA-207"

    with pytest.raises(ValueError, match="asserts policy 'PA-207'"):
        resolve_policy(assembled, index=policy_index)


def test_a_case_the_corpus_does_not_cover_is_refused_not_approximated(policy_index):
    assembled = assemble_case(_CASE_DIR, classifier=_FilenameClassifier())
    assembled.manifest.medication = "Drug Z"
    assembled.manifest.policy_id = None

    with pytest.raises(PolicyNotFoundError, match="No payer policy found"):
        resolve_policy(assembled, index=policy_index)


def test_missing_classifier_artifact_names_the_commands_that_build_it(tmp_path):
    with pytest.raises(FileNotFoundError, match="rxauth-train-classifier"):
        load_classifier(tmp_path / "absent.pkl")


def test_the_policy_is_retrieved_from_the_corpus_not_supplied_by_the_packet(policy_index):
    """README section 10: the last fixture on the policy side is gone. The packet
    names a payer, a drug, and an indication; the requirements come from a
    document in `data/policies/` that retrieval selected."""
    _, _, resolved = run_case(_CASE_DIR, classifier=_FilenameClassifier(), index=policy_index)

    assert resolved.document.policy_id == "PA-104"
    assert resolved.document.filename == "PA-104_2026-01.txt"
    assert resolved.policy.criteria, "the policy's requirements are read out of its prose"
    for criterion in resolved.policy.criteria:
        assert criterion.provenance.document_id == "PA-104"
        assert criterion.provenance.source_text
        assert criterion.provenance.start_char is not None
        assert criterion.extractor_version == "policy-rules-v1"


def test_retrieved_criteria_match_the_milestone_zero_fixture_exactly(policy_index):
    """The equivalence is the acceptance test for this phase, the same way real
    extraction had to reproduce the hand-authored evidence: reading the
    requirements out of policy prose must not change what the policy requires."""
    from rxauth_ai.synthetic_case import build_policy as fixture_policy

    _, _, resolved = run_case(_CASE_DIR, classifier=_FilenameClassifier(), index=policy_index)

    def structure(criterion):
        return (
            criterion.criterion_type,
            criterion.medication,
            criterion.operator,
            criterion.expected_value,
            criterion.unit,
            criterion.required_outcome,
        )

    assert [structure(c) for c in resolved.policy.criteria] == [
        structure(c) for c in fixture_policy().criteria
    ]


def test_the_policy_version_is_chosen_by_the_request_date_read_off_the_request(policy_index):
    """The version window is not decoration: PA-104 v2024-06 requires 8 weeks
    where v2026-01 requires 12, so picking the wrong version silently changes
    the answer. The date driving that choice is an extracted, cited fact."""
    assembled = assemble_case(_CASE_DIR, classifier=_FilenameClassifier())
    date, source = request_date_for(assembled)

    assert date == "2026-01-14"
    assert "01_pa_request.txt" in source

    resolved = resolve_policy(assembled, index=policy_index)
    assert resolved.document.version == "2026-01"

    assembled.manifest.request_date = "2025-06-01"
    earlier = resolve_policy(assembled, index=policy_index)
    assert earlier.document.version == "2024-06"
    assert earlier.request_date_source == "case manifest"


def test_exclusions_are_counted_but_never_evaluated_as_criteria(policy_index):
    """PA-104 states two exclusions. Scoring them as criteria would report a
    reason to deny coverage as a requirement the case satisfies."""
    report, _, resolved = run_case(_CASE_DIR, classifier=_FilenameClassifier(), index=policy_index)

    assert len(resolved.policy.exclusions) == 2
    assert report.policy_exclusions_not_evaluated == 2
    assert report.criteria_total == len(resolved.policy.criteria)
    evaluated = {evaluation.criterion_id for evaluation in report.evaluations}
    assert evaluated.isdisjoint({exclusion.id for exclusion in resolved.policy.exclusions})
