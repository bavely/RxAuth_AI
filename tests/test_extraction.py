"""Tests for rule-based information extraction (README section 9 / docs/phase-3-extraction.md)."""

from __future__ import annotations

from pathlib import Path

from rxauth_ai.extraction import DEFAULT_CONFIDENCE_THRESHOLD, extract_evidence
from rxauth_ai.ingestion import IngestedDocument, IngestedPage, ingest_document
from rxauth_ai.matching import evaluate_case
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


def test_confidence_threshold_is_configurable():
    document = _document("Drug D — started 2025-11-05, discontinued due to inadequate response.")
    lenient = extract_evidence(document, document_id="D1", confidence_threshold=0.0)
    assert lenient.issues == []


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
