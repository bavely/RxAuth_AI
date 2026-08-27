"""Typed domain models for RxAuth AI — Milestone 0.

These mirror the data model in README section 17. Every value that an AI
component would produce carries its provenance (source document, page, span,
confidence, method) so that no normalized value ever exists without a trace
back to where it came from.

Milestone 0 is deterministic and offline: there is no database and no LLM.
The point is to prove the *shape* of the pipeline end to end for one case.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class DocumentType(str, Enum):
    PA_REQUEST = "pa_request"
    INSURANCE_CARD = "insurance_card"
    REFERRAL = "referral"
    PRESCRIPTION = "prescription"
    CLINICAL_NOTE = "clinical_note"
    MEDICATION_HISTORY = "medication_history"
    LAB_REPORT = "lab_report"
    OTHER = "other"


class CriterionResult(str, Enum):
    SATISFIED = "SATISFIED"
    NOT_SATISFIED = "NOT_SATISFIED"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


class EvaluationMethod(str, Enum):
    DETERMINISTIC = "deterministic"
    MODEL_ASSISTED = "model_assisted"
    NONE = "none"


class Provenance(BaseModel):
    """Where a value came from. Attached to every extracted value and criterion."""

    document_id: Optional[str] = None
    filename: Optional[str] = None
    page: Optional[int] = None
    start_char: Optional[int] = Field(
        default=None, ge=0, description="Inclusive character offset within the source page."
    )
    end_char: Optional[int] = Field(
        default=None, ge=0, description="Exclusive character offset within the source page."
    )
    source_text: Optional[str] = Field(
        default=None, description="Raw span the value was read from."
    )

    @model_validator(mode="after")
    def validate_character_span(self) -> Provenance:
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("start_char and end_char must be provided together.")
        if (
            self.start_char is not None
            and self.end_char is not None
            and self.end_char < self.start_char
        ):
            raise ValueError("end_char must not be before start_char.")
        return self


class Document(BaseModel):
    id: str
    filename: str
    document_type: DocumentType
    classification_confidence: float = Field(ge=0.0, le=1.0)
    page_count: int = 1


class Evidence(BaseModel):
    """A normalized fact extracted from a document, with provenance and confidence.

    ``provenance`` is the anchor span — the one the normalized value is read
    from. ``supporting_provenance`` holds any *additional* spans that were
    combined into the same fact (Phase 3.5): a therapy duration cited on one
    line and its outcome on another, or the same payer named twice in one
    document. A fact is only ever assembled from spans the system can still
    point at, so ``sources`` is the complete citation list for the value.
    """

    id: str
    evidence_type: str
    medication: Optional[str] = None
    text_value: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    outcome: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: Provenance
    supporting_provenance: list[Provenance] = Field(
        default_factory=list,
        description="Additional cited spans combined into this one normalized fact.",
    )
    source_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Ingestion confidence of the weakest cited page (1.0 for digital text, the OCR "
            "score for a scan). Folded into `confidence` so a poor scan cannot yield a "
            "confident field."
        ),
    )
    extraction_rule: Optional[str] = Field(
        default=None, description="Name of the rule that produced the anchor span."
    )
    extraction_method: str = "synthetic"

    @property
    def sources(self) -> list[Provenance]:
        """Every span cited for this fact, anchor first."""
        return [self.provenance, *self.supporting_provenance]


class EvidenceLink(BaseModel):
    """Exact normalized facts corroborated by more than one document.

    The source ``Evidence`` records remain separate. This link records their
    relationship without moving a span out of its source document or changing
    the evidence IDs referenced by extraction issues and criterion results.
    """

    id: str
    evidence_type: str
    canonical_evidence_id: str
    evidence_ids: list[str] = Field(min_length=2)
    document_ids: list[str] = Field(min_length=2)
    provenance: list[Provenance] = Field(min_length=2)

    @model_validator(mode="after")
    def validate_cross_document_link(self) -> EvidenceLink:
        if self.canonical_evidence_id not in self.evidence_ids:
            raise ValueError("canonical_evidence_id must be included in evidence_ids.")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids must be unique.")
        if len(set(self.document_ids)) < 2:
            raise ValueError("An evidence link must cite at least two distinct documents.")
        return self


class Criterion(BaseModel):
    """A structured payer requirement extracted from policy prose."""

    id: str
    policy_id: str
    description: str
    criterion_type: str
    medication: Optional[str] = None
    operator: Optional[Literal[">=", "<=", ">", "<", "==", "exists"]] = Field(
        default=None, description="One of >=, <=, >, <, ==, exists."
    )
    expected_value: Optional[float] = None
    unit: Optional[str] = None
    required_outcome: Optional[str] = None
    provenance: Provenance


class Policy(BaseModel):
    id: str
    payer: str
    medication: str
    indication: str
    effective_date: str
    source_url: Optional[str] = None
    version: str = "v1"
    criteria: list[Criterion] = Field(default_factory=list)


class Case(BaseModel):
    id: str
    patient_synthetic_id: str
    payer: str
    plan: Optional[str] = None
    medication: str
    indication: str
    pa_required: bool = Field(
        description="Synthetic trigger or user input — never inferred from policy text."
    )
    documents: list[Document] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class CriterionEvaluation(BaseModel):
    """Result of checking one criterion against the case evidence."""

    criterion_id: str
    case_id: str
    result: CriterionResult
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    evaluation_method: EvaluationMethod
    explanation: str
    criterion_description: str = ""
    policy_source: Optional[Provenance] = None
    patient_evidence_source: Optional[Provenance] = None
    patient_evidence_sources: list[Provenance] = Field(
        default_factory=list,
        description=(
            "Every span cited by the supporting evidence. `patient_evidence_source` is the "
            "anchor span and stays the one-line answer; this is the full citation list for a "
            "fact assembled from more than one span."
        ),
    )


class CaseReadinessReport(BaseModel):
    """The end-to-end output of Milestone 0 for one case."""

    case_id: str
    policy_id: str
    payer: str
    medication: str
    indication: str
    pa_required: bool
    documents_detected: int
    mean_classification_confidence: float
    documents_requiring_classification_review: int = 0
    evidence_total: int = 0
    evidence_requiring_review: int = 0
    criteria_total: int
    criteria_satisfied: int
    criteria_not_satisfied: int
    criteria_missing: int
    criteria_needs_review: int
    groundedness_gate: str
    evaluations: list[CriterionEvaluation] = Field(default_factory=list)

    def summary_line(self) -> str:
        return (
            f"{self.criteria_satisfied} supported, "
            f"{self.criteria_needs_review} need review, "
            f"{self.criteria_missing} missing "
            f"(of {self.criteria_total})"
        )
