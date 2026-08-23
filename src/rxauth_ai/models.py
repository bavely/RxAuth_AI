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
    """A normalized fact extracted from a document, with provenance and confidence."""

    id: str
    evidence_type: str
    medication: Optional[str] = None
    text_value: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    outcome: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)
    provenance: Provenance
    extraction_method: str = "synthetic"


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
