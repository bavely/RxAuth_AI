"""Tests for rule-based information extraction (README section 9 / docs/phase-3-extraction.md)."""

from __future__ import annotations

from pathlib import Path

from rxauth_ai.extraction import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    IssueKind,
    combine_confidence,
    extract_evidence,
)
from rxauth_ai.ingestion import IngestedDocument, IngestedPage, ingest_document
from rxauth_ai.matching import evaluate_case
from rxauth_ai.medications import normalize_medication
from rxauth_ai.models import Case, CriterionResult
from rxauth_ai.synthetic_case import build_policy

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _document(text: str, *, filename: str = "note.txt", page_number: int = 1) -> IngestedDocument:
    return IngestedDocument(
        filename=filename,
        media_type="text",
        pages=[
            IngestedPage(
                page_number=page_number, text=text, extraction_method="text", confidence=1.0
            )
        ],
    )


def test_extracts_diagnosis_with_high_confidence():
    result = extract_evidence(_document("Diagnosis: Example Condition."), document_id="D1")

    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.evidence_type == "diagnosis"
    assert item.text_value == "Example Condition"
    assert item.outcome == "documented"
    assert item.confidence >= 0.9
    assert result.issues == []


def test_extracts_assessment_diagnosis_form_from_the_real_corpus():
    document = _document("Assessment: Sample Syndrome, stable on current regimen.")
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.evidence_type == "diagnosis"
    assert item.text_value == "Sample Syndrome"


def test_extracts_prescription_from_rx_form():
    document = _document("Rx: Drug A. Sig: take as directed.")
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.evidence_type == "prescription"
    assert item.medication == "Drug A"


def test_normalizes_brand_and_generic_medication_names_to_one_canonical_value():
    brand = extract_evidence(_document("Prescription: Humira."), document_id="D1")
    generic = extract_evidence(_document("Medication ordered: adalimumab."), document_id="D2")

    assert brand.evidence[0].medication == "adalimumab"
    assert generic.evidence[0].medication == "adalimumab"
    assert normalize_medication("HUMIRA") == "adalimumab"


def test_extracts_named_medication_therapy_without_changing_provenance_text():
    text = "Enbrel used for 12 weeks; no response."
    result = extract_evidence(_document(text), document_id="D1")

    assert len(result.evidence) == 1
    assert result.evidence[0].medication == "etanercept"
    assert result.evidence[0].provenance.source_text == "Enbrel used for 12 weeks; no response"


def test_extracts_previous_therapy_with_duration_and_outcome():
    document = _document("Drug A used for 16 weeks; inadequate response.")
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.evidence_type == "previous_therapy"
    assert item.medication == "Drug A"
    assert item.value == 16.0
    assert item.unit == "weeks"
    assert item.outcome == "inadequate_response"
    assert item.confidence >= 0.85


def test_extracts_previous_therapy_duration_documented_form_from_the_real_corpus():
    document = _document("Drug A — 24 weeks of therapy documented.")
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.evidence_type == "previous_therapy"
    assert item.medication == "Drug A"
    assert item.value == 24.0
    assert item.unit == "weeks"
    assert result.issues == []


def test_extracts_previous_therapy_outcome_only_and_flags_a_review_issue():
    document = _document("Drug D — started 2025-11-05, discontinued due to inadequate response.")
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.evidence_type == "previous_therapy"
    assert item.medication == "Drug D"
    assert item.value is None
    assert item.outcome == "inadequate_response"
    assert item.confidence < DEFAULT_CONFIDENCE_THRESHOLD

    assert len(result.issues) == 1
    assert result.issues[0].evidence_id == item.id
    assert result.requires_human_review is True


def test_extracts_lab_value_with_and_without_percent_sign():
    with_percent = extract_evidence(_document("A1c: 7.4%"), document_id="D1")
    without_percent = extract_evidence(
        _document("A1c: 7.4 — collected 2025-12-22"), document_id="D1"
    )

    assert with_percent.evidence[0].value == 7.4
    assert with_percent.evidence[0].unit == "percent"
    assert with_percent.evidence[0].confidence > without_percent.evidence[0].confidence


def test_extracts_administrative_identifiers_and_payer():
    text = "Patient ID: SYNTH-0101. Member ID: MEMBER-22. Health plan: Example Health Plan."
    evidence = extract_evidence(_document(text), document_id="D1").evidence
    values = {item.evidence_type: item.text_value for item in evidence}

    assert values == {
        "patient_id": "SYNTH-0101",
        "member_id": "MEMBER-22",
        "payer": "Example Health Plan",
    }


def test_extracts_payer_from_member_identification_card_heading():
    result = extract_evidence(
        _document("Example Health Plan Member Identification Card."), document_id="D1"
    )

    assert len(result.evidence) == 1
    assert result.evidence[0].evidence_type == "payer"
    assert result.evidence[0].text_value == "Example Health Plan"


def test_extracts_quantities_and_document_dates():
    text = (
        "Quantity requested: 30-day supply. Quantity: 30. "
        "Date written: 2025-09-14. Date of request: 2025-10-02. "
        "Visit date: 2025-11-06."
    )
    evidence = extract_evidence(_document(text), document_id="D1").evidence

    assert [(item.evidence_type, item.value, item.unit) for item in evidence[:2]] == [
        ("days_supply", 30.0, "days"),
        ("prescription_quantity", 30.0, "count"),
    ]
    assert [item.text_value for item in evidence[2:]] == [
        "2025-09-14",
        "2025-10-02",
        "2025-11-06",
    ]


def test_extracts_additional_labs_and_screening_documentation():
    text = (
        "LDL cholesterol: 112.5. ALT: 42. eGFR: 73.5. CRP: 8.1. Screening documentation attached."
    )
    evidence = extract_evidence(_document(text), document_id="D1").evidence
    values = {item.evidence_type: item.value for item in evidence}

    assert values == {
        "lab_ldl_cholesterol": 112.5,
        "lab_alt": 42.0,
        "lab_egfr": 73.5,
        "lab_crp": 8.1,
        "screening_doc": None,
    }
    screening = next(item for item in evidence if item.evidence_type == "screening_doc")
    assert screening.outcome == "documented"


def test_extracts_vague_therapy_duration_and_flags_a_review_issue():
    document = _document("Patient on therapy for several months.")
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.evidence_type == "therapy_duration"
    assert item.value is None
    assert item.outcome == "several months"
    assert item.confidence < DEFAULT_CONFIDENCE_THRESHOLD
    assert len(result.issues) == 1


def test_no_match_yields_no_evidence_and_no_issues():
    result = extract_evidence(_document("No acute distress noted on exam today."), document_id="D1")
    assert result.evidence == []
    assert result.issues == []
    assert result.requires_human_review is False


def test_negated_diagnosis_does_not_create_supported_evidence():
    result = extract_evidence(
        _document("No Diagnosis: pending specialist review."), document_id="D1"
    )

    assert result.evidence == []


def test_confidence_threshold_is_configurable_for_a_complete_field():
    document = _document("A1c: 7.4 — collected 2025-12-22")
    lenient = extract_evidence(document, document_id="D1", confidence_threshold=0.0)
    strict = extract_evidence(document, document_id="D1", confidence_threshold=0.8)

    assert lenient.issues == []
    assert [issue.kind for issue in strict.issues] == [IssueKind.LOW_CONFIDENCE]


def test_an_incomplete_value_is_not_silenced_by_a_lenient_threshold():
    """An incomplete value is not a confidence problem, so the confidence knob
    must not clear it — the span was read correctly and still says too little
    for a deterministic check."""
    document = _document("Drug D — started 2025-11-05, discontinued due to inadequate response.")
    lenient = extract_evidence(document, document_id="D1", confidence_threshold=0.0)

    assert [issue.kind for issue in lenient.issues] == [IssueKind.INCOMPLETE_VALUE]
    assert lenient.requires_human_review is True


def test_provenance_captures_page_and_character_span():
    text = "Intake note.\nDiagnosis: Example Condition.\nEnd of note."
    document = _document(text, filename="clinical_note.pdf", page_number=3)
    item = extract_evidence(document, document_id="D7").evidence[0]

    assert item.provenance.document_id == "D7"
    assert item.provenance.filename == "clinical_note.pdf"
    assert item.provenance.page == 3
    start, end = item.provenance.start_char, item.provenance.end_char
    assert text[start:end] == item.provenance.source_text


def test_multi_page_document_assigns_matches_to_their_own_page():
    document = IngestedDocument(
        filename="note.pdf",
        media_type="pdf",
        pages=[
            IngestedPage(
                page_number=1,
                text="Diagnosis: Example Condition.",
                extraction_method="pypdf",
                confidence=1.0,
            ),
            IngestedPage(
                page_number=2,
                text="A1c: 7.4%",
                extraction_method="pypdf",
                confidence=1.0,
            ),
        ],
    )
    evidence = extract_evidence(document, document_id="D9").evidence

    pages_by_type = {item.evidence_type: item.provenance.page for item in evidence}
    assert pages_by_type["diagnosis"] == 1
    assert pages_by_type["lab_a1c"] == 2


def test_evidence_ids_are_unique_and_ordered():
    text = "Diagnosis: Example Condition.\nA1c: 7.4%"
    evidence = extract_evidence(_document(text), document_id="D1").evidence
    ids = [item.id for item in evidence]
    assert len(ids) == len(set(ids))


def test_extracted_evidence_reproduces_milestone_zero_criterion_outcomes():
    """The real extractor, run over document text, must match the hand-authored
    Milestone 0 fixture's criterion outcomes (README section 23) exactly —
    proving extraction is a drop-in replacement for the pre-supplied evidence."""
    policy = build_policy()
    clinical_note = _document(
        "Diagnosis: Example Condition.\nPatient on therapy for several months.",
        filename="clinical_note.pdf",
    )
    medication_history = _document(
        "Drug A used for 16 weeks; inadequate response.",
        filename="medication_history.pdf",
    )
    lab_report = _document("A1c: 7.4%", filename="lab_report.pdf")

    evidence = (
        extract_evidence(clinical_note, document_id="D3").evidence
        + extract_evidence(medication_history, document_id="D4").evidence
        + extract_evidence(lab_report, document_id="D5").evidence
    )

    case = Case(
        id="PA-DEMO-001",
        patient_synthetic_id="SYNTH-0001",
        payer="Example Health Plan",
        medication="Drug A",
        indication="Example Condition",
        pa_required=True,
        evidence=evidence,
    )

    evaluations = evaluate_case(case, policy.criteria)
    results = {evaluation.criterion_id: evaluation.result for evaluation in evaluations}

    assert results["C1"] == CriterionResult.SATISFIED
    assert results["C2"] == CriterionResult.SATISFIED
    assert results["C3"] == CriterionResult.SATISFIED
    assert results["C4"] == CriterionResult.SATISFIED
    assert results["C5"] == CriterionResult.MISSING
    assert results["C6"] == CriterionResult.AMBIGUOUS


def test_extracts_evidence_from_the_documented_cli_example_file():
    """docs/phase-3-extraction.md's own usage example must actually produce evidence."""
    path = _DATA_DIR / "documents" / "clinical_note" / "doc_0002.txt"
    document = ingest_document(path)
    result = extract_evidence(document, document_id="SYN-EXAMPLE")

    assert result.evidence, "the documented CLI example should extract at least one field"


# --- Phase 3.5: span resolution, multi-span facts, and OCR-aware confidence ---


def test_links_a_therapy_duration_and_its_outcome_into_one_fact_citing_both_spans():
    document = _document(
        "Drug A — 16 weeks of therapy documented. "
        "Drug A — started 2025-06-05, discontinued due to inadequate response."
    )
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert (item.medication, item.value, item.unit, item.outcome) == (
        "Drug A",
        16.0,
        "weeks",
        "inadequate_response",
    )
    # The linked fact is complete, so the missing-duration penalty is gone.
    assert item.confidence >= DEFAULT_CONFIDENCE_THRESHOLD
    assert result.issues == []

    # Both spans stay citable, and the duration span anchors the record.
    assert len(item.sources) == 2
    assert item.provenance.source_text.endswith("of therapy documented")
    assert "inadequate response" in item.supporting_provenance[0].source_text
    for provenance in item.sources:
        assert provenance.document_id == "D1"


def test_refuses_to_link_when_several_pairings_are_equally_plausible():
    document = _document(
        "Drug B — 12 weeks of therapy documented. "
        "Drug B — 20 weeks of therapy documented. "
        "Drug B — started 2025-03-05, discontinued due to inadequate response."
    )
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 3
    assert {item.value for item in result.evidence} == {12.0, 20.0, None}
    assert [issue.kind for issue in result.issues] == [IssueKind.AMBIGUOUS_LINKAGE]
    assert "equally plausible" in result.issues[0].reason


def test_does_not_link_a_duration_and_an_outcome_for_different_medications():
    document = _document(
        "Drug A — 18 weeks of therapy documented. "
        "Drug C — started 2025-02-14, discontinued due to inadequate response."
    )
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 2
    by_medication = {item.medication: item for item in result.evidence}
    assert by_medication["Drug A"].outcome is None
    assert by_medication["Drug C"].value is None
    assert [issue.kind for issue in result.issues] == [IssueKind.INCOMPLETE_VALUE]


def test_merges_a_repeated_mention_into_one_fact_with_several_citations():
    document = _document(
        "Example Health Plan Member Identification Card. Health plan: Example Health Plan."
    )
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.text_value == "Example Health Plan"
    assert len(item.sources) == 2
    assert [provenance.start_char for provenance in item.sources] == sorted(
        provenance.start_char for provenance in item.sources
    )


def test_does_not_merge_facts_that_differ_in_any_normalized_field():
    document = _document("Health plan: Example Health Plan. Health plan: Sample Care Network.")
    values = {item.text_value for item in extract_evidence(document, document_id="D1").evidence}

    assert values == {"Example Health Plan", "Sample Care Network"}


def test_overlapping_same_type_spans_resolve_to_one_and_record_the_suppression():
    document = _document("Health plan: Example Health Plan Member Identification Card")
    result = extract_evidence(document, document_id="D1")

    assert len(result.evidence) == 1
    assert result.evidence[0].extraction_rule == "payer_labeled"
    assert len(result.suppressed) == 1
    suppressed = result.suppressed[0]
    assert suppressed.rule == "payer_card_heading"
    assert suppressed.superseded_by == "payer_labeled"
    assert suppressed.reason == "contained in a longer span"


def test_a_date_inside_a_therapy_line_stays_a_separate_fact():
    """Overlap resolution is per evidence type — different types may share text."""
    document = _document("Drug A used for 16 weeks; inadequate response. Visit date: 2026-01-09.")
    types = {item.evidence_type for item in extract_evidence(document, document_id="D1").evidence}

    assert types == {"previous_therapy", "document_date"}


def _ocr_document(text: str, confidence: float) -> IngestedDocument:
    return IngestedDocument(
        filename="scan.png",
        media_type="image",
        pages=[
            IngestedPage(page_number=1, text=text, extraction_method="ocr", confidence=confidence)
        ],
    )


def test_ocr_page_confidence_is_folded_into_extraction_confidence():
    clean = extract_evidence(_document("Diagnosis: Example Condition."), document_id="D1")
    scanned = extract_evidence(
        _ocr_document("Diagnosis: Example Condition.", 0.7), document_id="D2"
    )

    assert clean.evidence[0].confidence == 0.95
    assert clean.evidence[0].source_confidence == 1.0
    assert scanned.evidence[0].confidence == combine_confidence(0.95, 0.7)
    assert scanned.evidence[0].source_confidence == 0.7
    assert scanned.evidence[0].confidence < clean.evidence[0].confidence


def test_a_poor_scan_routes_an_otherwise_confident_field_to_review():
    result = extract_evidence(_ocr_document("Diagnosis: Example Condition.", 0.5), document_id="D1")

    assert [issue.kind for issue in result.issues] == [IssueKind.LOW_CONFIDENCE]
    assert "page ingestion confidence" in result.issues[0].reason
    assert result.requires_human_review is True


def test_combine_confidence_leaves_digital_text_untouched():
    assert combine_confidence(0.85, 1.0) == 0.85
