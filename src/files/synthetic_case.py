"""Synthetic case + policy fixtures for Milestone 0.

GUARDRAIL (README section 3): synthetic / de-identified data only. Every patient,
document, and value below is fabricated. No real PHI, no real payer, no real drug.
Drug, payer, indication, and criteria are placeholders ("Drug A", "Example Health
Plan") on purpose.

This builds the exact Milestone 0 scope: 1 synthetic patient, 1 medication,
1 payer, 1 public-style policy, several documents, and 4-8 criteria — including
deliberately satisfied, missing, and ambiguous cases so the pipeline exercises
every one of the five result states.
"""
from __future__ import annotations

from .models import (
    Case,
    Criterion,
    Document,
    DocumentType,
    Evidence,
    Policy,
    Provenance,
)

POLICY_ID = "PA-104"


def build_policy() -> Policy:
    criteria = [
        Criterion(
            id="C1",
            policy_id=POLICY_ID,
            description="Documented diagnosis of the covered indication.",
            criterion_type="diagnosis",
            provenance=Provenance(page=3, source_text="Patient must have a documented diagnosis."),
        ),
        Criterion(
            id="C2",
            policy_id=POLICY_ID,
            description="Prior Drug A therapy for at least 12 weeks.",
            criterion_type="previous_therapy",
            medication="Drug A",
            operator=">=",
            expected_value=12,
            unit="weeks",
            provenance=Provenance(page=4, source_text="At least 12 weeks of Drug A."),
        ),
        Criterion(
            id="C3",
            policy_id=POLICY_ID,
            description="Inadequate response to prior Drug A therapy.",
            criterion_type="previous_therapy",
            medication="Drug A",
            operator=">=",
            expected_value=12,
            unit="weeks",
            required_outcome="inadequate_response",
            provenance=Provenance(page=4, source_text="...with inadequate response."),
        ),
        Criterion(
            id="C4",
            policy_id=POLICY_ID,
            description="Recent A1c below 8.0.",
            criterion_type="lab_a1c",
            operator="<",
            expected_value=8.0,
            unit="percent",
            provenance=Provenance(page=5, source_text="A1c must be below 8.0."),
        ),
        Criterion(
            id="C5",
            policy_id=POLICY_ID,
            description="Required screening documentation on file.",
            criterion_type="screening_doc",
            provenance=Provenance(page=6, source_text="Screening documentation required."),
        ),
        Criterion(
            id="C6",
            policy_id=POLICY_ID,
            description="Duration of most recent therapy course documented.",
            criterion_type="therapy_duration",
            operator=">=",
            expected_value=8,
            unit="weeks",
            provenance=Provenance(page=6, source_text="Therapy duration must be documented."),
        ),
    ]
    return Policy(
        id=POLICY_ID,
        payer="Example Health Plan",
        medication="Drug A",
        indication="Example Condition",
        effective_date="2026-01-01",
        source_url="https://example.com/policies/PA-104.pdf",
        criteria=criteria,
    )


def build_case() -> Case:
    documents = [
        Document(id="D1", filename="pa_request.pdf", document_type=DocumentType.PA_REQUEST,
                 classification_confidence=0.98),
        Document(id="D2", filename="insurance_card.png", document_type=DocumentType.INSURANCE_CARD,
                 classification_confidence=0.97),
        Document(id="D3", filename="clinical_note.pdf", document_type=DocumentType.CLINICAL_NOTE,
                 classification_confidence=0.95, page_count=4),
        Document(id="D4", filename="medication_history.pdf",
                 document_type=DocumentType.MEDICATION_HISTORY,
                 classification_confidence=0.96, page_count=2),
        Document(id="D5", filename="lab_report.pdf", document_type=DocumentType.LAB_REPORT,
                 classification_confidence=0.94),
    ]

    evidence = [
        # Satisfies C1 (diagnosis present).
        Evidence(
            id="E1", evidence_type="diagnosis", outcome="documented", confidence=0.97,
            provenance=Provenance(document_id="D3", filename="clinical_note.pdf", page=1,
                                  source_text="Diagnosis: Example Condition."),
        ),
        # Satisfies C2 and C3 (16 weeks >= 12, inadequate response).
        Evidence(
            id="E2", evidence_type="previous_therapy", medication="Drug A",
            value=16, unit="weeks", outcome="inadequate_response", confidence=0.93,
            provenance=Provenance(document_id="D4", filename="medication_history.pdf", page=2,
                                  source_text="Drug A used for 16 weeks; inadequate response."),
        ),
        # Satisfies C4 (A1c 7.4 < 8.0).
        Evidence(
            id="E3", evidence_type="lab_a1c", value=7.4, unit="percent", confidence=0.95,
            provenance=Provenance(document_id="D5", filename="lab_report.pdf", page=1,
                                  source_text="A1c: 7.4%"),
        ),
        # C6: therapy duration present but not numerically explicit -> AMBIGUOUS.
        Evidence(
            id="E4", evidence_type="therapy_duration", value=None, unit="weeks",
            outcome="several months", confidence=0.88,
            provenance=Provenance(document_id="D3", filename="clinical_note.pdf", page=3,
                                  source_text="Patient on therapy for several months."),
        ),
        # C5 (screening_doc) has NO matching evidence on purpose -> MISSING.
    ]

    return Case(
        id="PA-DEMO-001",
        patient_synthetic_id="SYNTH-0001",
        payer="Example Health Plan",
        plan="Example PPO",
        medication="Drug A",
        indication="Example Condition",
        pa_required=True,  # synthetic trigger, not inferred from policy text
        documents=documents,
        evidence=evidence,
    )
