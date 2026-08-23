"""Rule-based information extraction with provenance and confidence (README section 9,
Phase 3 — see docs/phase-3-extraction.md).

Converts ingested document text (README section 7) into typed Evidence records
(README section 17). Every extracted value carries the document, page, character
span, and source text it was read from, plus a confidence that reflects how
explicit the match was in the text — a value is never stored without a trace
back to where it came from, and a low-confidence extraction is never silently
dropped: it is retained and flagged with an ExtractionIssue instead.

Design principle (README section 6): match deterministically where a value is
numerically explicit in the text; assign a confidence below the review
threshold when a requirement is mentioned without an explicit number, so a
reviewer sees it flagged instead of a guessed value.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from .ingestion import IngestedDocument, ingest_document
from .models import Evidence, Provenance

EXTRACTOR_VERSION = "regex-v1"
DEFAULT_CONFIDENCE_THRESHOLD = 0.65


class ExtractionIssue(BaseModel):
    """Flags one retained Evidence item whose confidence fell below threshold."""

    evidence_id: str
    evidence_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


@dataclass
class ExtractionResult:
    evidence: list[Evidence]
    issues: list[ExtractionIssue]

    @property
    def requires_human_review(self) -> bool:
        return bool(self.issues)


@dataclass(frozen=True)
class _ExtractedFields:
    evidence_type: str
    medication: Optional[str] = None
    text_value: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    outcome: Optional[str] = None
    confidence: float = 0.9


def _normalize_duration_unit(raw: str) -> str:
    lowered = raw.lower()
    if lowered.startswith("week"):
        return "weeks"
    if lowered.startswith("month"):
        return "months"
    if lowered.startswith("day"):
        return "days"
    return lowered


def _normalize_outcome(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    return raw.strip().lower().replace(" ", "_")


def _diagnosis_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="diagnosis",
        text_value=match.group("condition").strip(),
        outcome="documented",
        confidence=0.95,
    )


def _prescription_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="prescription",
        medication=match.group("medication"),
        outcome="prescribed",
        confidence=0.9,
    )


def _previous_therapy_used_for_fields(match: re.Match[str]) -> _ExtractedFields:
    outcome = match.group("outcome")
    return _ExtractedFields(
        evidence_type="previous_therapy",
        medication=match.group("medication"),
        value=float(match.group("value")),
        unit=_normalize_duration_unit(match.group("unit")),
        outcome=_normalize_outcome(outcome),
        confidence=0.9 if outcome else 0.85,
    )


def _previous_therapy_documented_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="previous_therapy",
        medication=match.group("medication"),
        value=float(match.group("value")),
        unit=_normalize_duration_unit(match.group("unit")),
        confidence=0.85,
    )


def _previous_therapy_outcome_only_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="previous_therapy",
        medication=match.group("medication"),
        outcome=_normalize_outcome(match.group("outcome")),
        # No explicit duration in the source text: the phrase itself was read
        # cleanly, but the missing number is an extraction-confidence issue
        # (flagged below DEFAULT_CONFIDENCE_THRESHOLD) as well as an AMBIGUOUS
        # downstream match for any criterion that needs a numeric comparison.
        confidence=0.60,
    )


def _lab_value_fields(match: re.Match[str]) -> _ExtractedFields:
    has_percent = match.group("percent") is not None
    return _ExtractedFields(
        evidence_type="lab_a1c",
        value=float(match.group("value")),
        unit="percent",
        confidence=0.95 if has_percent else 0.75,
    )


def _patient_id_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="patient_id",
        text_value=match.group("identifier"),
        confidence=0.98,
    )


def _member_id_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="member_id",
        text_value=match.group("identifier"),
        confidence=0.98,
    )


def _payer_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="payer",
        text_value=match.group("payer").strip(),
        confidence=0.95,
    )


def _days_supply_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="days_supply",
        value=float(match.group("value")),
        unit="days",
        confidence=0.95,
    )


def _prescription_quantity_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="prescription_quantity",
        value=float(match.group("value")),
        unit="count",
        confidence=0.95,
    )


def _document_date_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="document_date",
        text_value=match.group("date"),
        confidence=0.98,
    )


def _additional_lab_fields(match: re.Match[str]) -> _ExtractedFields:
    lab_types = {
        "alt": "lab_alt",
        "crp": "lab_crp",
        "egfr": "lab_egfr",
        "ldl cholesterol": "lab_ldl_cholesterol",
    }
    return _ExtractedFields(
        evidence_type=lab_types[match.group("lab").lower()],
        value=float(match.group("value")),
        confidence=0.9,
    )


def _screening_documentation_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="screening_doc",
        outcome="documented",
        confidence=0.9,
    )


def _therapy_duration_vague_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="therapy_duration",
        outcome=match.group("phrase").strip().lower(),
        confidence=0.60,
    )


_FieldBuilder = Callable[[re.Match[str]], _ExtractedFields]


@dataclass(frozen=True)
class _ExtractionRule:
    pattern: re.Pattern[str]
    build: _FieldBuilder


_RULES: list[_ExtractionRule] = [
    _ExtractionRule(
        pattern=re.compile(r"Patient ID:\s*(?P<identifier>[A-Z0-9][A-Z0-9_-]*)", re.IGNORECASE),
        build=_patient_id_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(r"Member ID:\s*(?P<identifier>[A-Z0-9][A-Z0-9_-]*)", re.IGNORECASE),
        build=_member_id_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(r"Health plan:\s*(?P<payer>[^.\r\n]+)", re.IGNORECASE),
        build=_payer_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(
            r"(?P<payer>[A-Z][A-Za-z ]*? Health Plan)\s+Member Identification Card",
            re.IGNORECASE,
        ),
        build=_payer_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(
            r"Quantity requested:\s*(?P<value>\d+(?:\.\d+)?)-day supply",
            re.IGNORECASE,
        ),
        build=_days_supply_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(r"Quantity:\s*(?P<value>\d+(?:\.\d+)?)", re.IGNORECASE),
        build=_prescription_quantity_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(
            r"(?:Date written|Date of request|Visit date):\s*"
            r"(?P<date>\d{4}-\d{2}-\d{2})",
            re.IGNORECASE,
        ),
        build=_document_date_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(
            r"(?P<lab>LDL cholesterol|ALT|eGFR|CRP):\s*"
            r"(?P<value>\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        build=_additional_lab_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(r"Screening documentation attached", re.IGNORECASE),
        build=_screening_documentation_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(
            r"(?<!No )(?:Diagnosis|Assessment):\s*(?P<condition>[^,.\n]+)", re.IGNORECASE
        ),
        build=_diagnosis_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(r"Rx:\s*(?P<medication>Drug [A-Z])\.", re.IGNORECASE),
        build=_prescription_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(
            r"(?P<medication>Drug [A-Z])\s+used for\s+(?P<value>\d+(?:\.\d+)?)\s*"
            r"(?P<unit>weeks?|months?|days?)"
            r"(?:[;,]?\s*(?P<outcome>inadequate response|adequate response|"
            r"no response|good response))?",
            re.IGNORECASE,
        ),
        build=_previous_therapy_used_for_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(
            r"(?P<medication>Drug [A-Z])\s*[—\-–]\s*(?P<value>\d+(?:\.\d+)?)\s*"
            r"(?P<unit>weeks?|months?|days?)\s+of therapy documented",
            re.IGNORECASE,
        ),
        build=_previous_therapy_documented_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(
            r"(?P<medication>Drug [A-Z])\s*[—\-–]\s*started[^.\n]*?discontinued due to\s+"
            r"(?P<outcome>inadequate response|adequate response|no response|good response)",
            re.IGNORECASE,
        ),
        build=_previous_therapy_outcome_only_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(r"A1c:?\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<percent>%)?", re.IGNORECASE),
        build=_lab_value_fields,
    ),
    _ExtractionRule(
        pattern=re.compile(
            r"(?:on therapy|therapy)\s+for\s+"
            r"(?P<phrase>several months|a few weeks|some time|an extended period)",
            re.IGNORECASE,
        ),
        build=_therapy_duration_vague_fields,
    ),
]


def extract_evidence(
    document: IngestedDocument,
    *,
    document_id: str,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> ExtractionResult:
    """Extract typed, provenance-carrying Evidence from an ingested document's text.

    Every match is retained as Evidence regardless of confidence — nothing is
    silently discarded. A match below ``confidence_threshold`` additionally
    produces an ExtractionIssue so a caller can route it to human review. A
    page with no matching text simply yields no Evidence for that type — an
    absent requirement stays MISSING downstream rather than being fabricated.
    """
    evidence: list[Evidence] = []
    issues: list[ExtractionIssue] = []
    for page in document.pages:
        for rule in _RULES:
            for match in rule.pattern.finditer(page.text):
                fields = rule.build(match)
                item = Evidence(
                    id=f"{document_id}-EV{len(evidence) + 1}",
                    evidence_type=fields.evidence_type,
                    medication=fields.medication,
                    text_value=fields.text_value,
                    value=fields.value,
                    unit=fields.unit,
                    outcome=fields.outcome,
                    confidence=fields.confidence,
                    provenance=Provenance(
                        document_id=document_id,
                        filename=document.filename,
                        page=page.page_number,
                        start_char=match.start(),
                        end_char=match.end(),
                        source_text=match.group(0),
                    ),
                    extraction_method=EXTRACTOR_VERSION,
                )
                evidence.append(item)
                if fields.confidence < confidence_threshold:
                    issues.append(
                        ExtractionIssue(
                            evidence_id=item.id,
                            evidence_type=item.evidence_type,
                            confidence=item.confidence,
                            reason=(
                                f"Confidence {item.confidence:.2f} is below the "
                                f"{confidence_threshold:.2f} review threshold; value is "
                                "retained but requires human review."
                            ),
                        )
                    )
    return ExtractionResult(evidence=evidence, issues=issues)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract typed, provenance-carrying evidence from one document."
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--confidence-threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD)
    args = parser.parse_args()

    if not 0.0 <= args.confidence_threshold <= 1.0:
        parser.error("--confidence-threshold must be between 0 and 1.")

    document = ingest_document(args.path)
    result = extract_evidence(
        document, document_id=args.document_id, confidence_threshold=args.confidence_threshold
    )
    output = {
        "evidence": [item.model_dump() for item in result.evidence],
        "issues": [issue.model_dump() for issue in result.issues],
        "requires_human_review": result.requires_human_review,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
