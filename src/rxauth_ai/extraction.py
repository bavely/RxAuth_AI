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

Matching a pattern is only the first stage. Raw matches are then *resolved*
(Phase 3.5) before they become Evidence:

    match rules per page
      -> resolve_overlaps        one span wins when two rules claim the same text
      -> merge_repeated_mentions one fact, several citations
      -> link_previous_therapy   duration + outcome combined only when unambiguous
      -> apply source confidence a poor scan cannot produce a confident field

Each stage is deterministic and either keeps a cited span or records why it was
dropped. Nothing is invented, and nothing is combined that the system cannot
still point at.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from .ingestion import IngestedDocument, IngestedPage, ingest_document
from .medications import MEDICATION_PATTERN, normalize_medication
from .models import Evidence, Provenance

EXTRACTOR_VERSION = "regex-v3"
DEFAULT_CONFIDENCE_THRESHOLD = 0.65


class IssueKind(str, Enum):
    """Why one retained field needs a human.

    The three reasons are genuinely different, and collapsing them into one
    number hid that: `LOW_CONFIDENCE` means the span may have been misread,
    `INCOMPLETE_VALUE` means the span was read correctly but does not state
    enough for a deterministic check, and `AMBIGUOUS_LINKAGE` means the
    document contains several equally plausible readings and the extractor
    refused to pick one.
    """

    LOW_CONFIDENCE = "low_confidence"
    INCOMPLETE_VALUE = "incomplete_value"
    AMBIGUOUS_LINKAGE = "ambiguous_linkage"


class ExtractionIssue(BaseModel):
    """Flags one retained Evidence item that a reviewer must look at."""

    evidence_id: str
    evidence_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    kind: IssueKind = IssueKind.LOW_CONFIDENCE


class SuppressedSpan(BaseModel):
    """One raw match that lost to another during overlap resolution.

    Retained so the report can show what the extractor chose *not* to emit;
    a silently dropped match is indistinguishable from a rule that never fired.
    """

    evidence_type: str
    rule: str
    page: int
    start_char: int
    end_char: int
    source_text: str
    reason: str
    superseded_by: str


@dataclass
class ExtractionResult:
    evidence: list[Evidence]
    issues: list[ExtractionIssue]
    suppressed: list[SuppressedSpan] = field(default_factory=list)

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
    incomplete_reason: Optional[str] = None
    linked_confidence: Optional[float] = None
    """Confidence to use once a linked span supplies the part this span lacks.

    `confidence` on an incomplete field is deliberately depressed so it routes
    to review on its own. That penalty is about the *missing part*, not about
    how well the span was read, so it must not survive into a record where
    another cited span supplies the missing part.
    """


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
        medication=normalize_medication(match.group("medication")),
        outcome="prescribed",
        confidence=0.9,
    )


def _previous_therapy_used_for_fields(match: re.Match[str]) -> _ExtractedFields:
    outcome = match.group("outcome")
    return _ExtractedFields(
        evidence_type="previous_therapy",
        medication=normalize_medication(match.group("medication")),
        value=float(match.group("value")),
        unit=_normalize_duration_unit(match.group("unit")),
        outcome=_normalize_outcome(outcome),
        confidence=0.9 if outcome else 0.85,
    )


def _previous_therapy_documented_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="previous_therapy",
        medication=normalize_medication(match.group("medication")),
        value=float(match.group("value")),
        unit=_normalize_duration_unit(match.group("unit")),
        confidence=0.85,
    )


def _previous_therapy_outcome_only_fields(match: re.Match[str]) -> _ExtractedFields:
    return _ExtractedFields(
        evidence_type="previous_therapy",
        medication=normalize_medication(match.group("medication")),
        outcome=_normalize_outcome(match.group("outcome")),
        # No explicit duration in this span: the phrase itself was read cleanly,
        # but the missing number is an extraction-confidence issue (flagged
        # below DEFAULT_CONFIDENCE_THRESHOLD) as well as an AMBIGUOUS downstream
        # match for any criterion that needs a numeric comparison. If the same
        # document states the duration for the same medication in another span,
        # link_previous_therapy combines the two and restores linked_confidence.
        confidence=0.60,
        incomplete_reason="the cited span states an outcome but no therapy duration",
        linked_confidence=0.88,
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
        # Unlike an outcome-only therapy span, this one cannot be repaired by
        # linking: the document itself never states a number, so there is no
        # other span to cite. It stays incomplete however many rules fire.
        incomplete_reason="duration is described in words, not as a number",
    )


_FieldBuilder = Callable[[re.Match[str]], _ExtractedFields]


@dataclass(frozen=True)
class _ExtractionRule:
    name: str
    pattern: re.Pattern[str]
    build: _FieldBuilder


# Declaration order is the precedence order: when two rules claim overlapping
# text for the same evidence type, the longer span wins, and the rule declared
# first breaks a tie. More specific rules are therefore declared before the
# general ones they refine.
_RULES: list[_ExtractionRule] = [
    _ExtractionRule(
        name="patient_id",
        pattern=re.compile(r"Patient ID:\s*(?P<identifier>[A-Z0-9][A-Z0-9_-]*)", re.IGNORECASE),
        build=_patient_id_fields,
    ),
    _ExtractionRule(
        name="member_id",
        pattern=re.compile(
            r"Member\s+[I1l]D:\s*(?P<identifier>[A-Z0-9][A-Z0-9_-]*)", re.IGNORECASE
        ),
        build=_member_id_fields,
    ),
    _ExtractionRule(
        name="payer_labeled",
        pattern=re.compile(r"(?:Health plan|Plan name):\s*(?P<payer>[^.\r\n]+)", re.IGNORECASE),
        build=_payer_fields,
    ),
    _ExtractionRule(
        name="payer_card_heading",
        pattern=re.compile(
            r"(?P<payer>[A-Z][A-Za-z ]*? Health Plan)\s+Member Identification Card",
            re.IGNORECASE,
        ),
        build=_payer_fields,
    ),
    _ExtractionRule(
        name="days_supply",
        pattern=re.compile(
            r"Quantity requested:\s*(?P<value>\d+(?:\.\d+)?)-day supply",
            re.IGNORECASE,
        ),
        build=_days_supply_fields,
    ),
    _ExtractionRule(
        name="prescription_quantity",
        pattern=re.compile(r"(?:Quantity|Qty):\s*(?P<value>\d+(?:\.\d+)?)", re.IGNORECASE),
        build=_prescription_quantity_fields,
    ),
    _ExtractionRule(
        name="days_supply_labeled",
        pattern=re.compile(r"Days supply:\s*(?P<value>\d+(?:\.\d+)?)", re.IGNORECASE),
        build=_days_supply_fields,
    ),
    _ExtractionRule(
        name="document_date",
        pattern=re.compile(
            r"(?:Date written|Date of request|Visit date|Encounter date):\s*"
            r"(?P<date>\d{4}-\d{2}-\d{2})",
            re.IGNORECASE,
        ),
        build=_document_date_fields,
    ),
    _ExtractionRule(
        name="additional_lab",
        pattern=re.compile(
            r"(?P<lab>LDL cholesterol|ALT|eGFR|CRP):\s*"
            r"(?P<value>\d+(?:\.\d+)?)",
            re.IGNORECASE,
        ),
        build=_additional_lab_fields,
    ),
    _ExtractionRule(
        name="screening_documentation",
        pattern=re.compile(r"Screening documentation attached", re.IGNORECASE),
        build=_screening_documentation_fields,
    ),
    _ExtractionRule(
        name="diagnosis",
        pattern=re.compile(
            r"(?<!No )(?:Diagnosis|Diagn0sis|Assessment)\s*[:\-]"
            r"(?![ \t]*(?:rule out|possible|suspected|no)\b)\s*"
            r"(?P<condition>[^,.\n]+)",
            re.IGNORECASE,
        ),
        build=_diagnosis_fields,
    ),
    _ExtractionRule(
        name="prescription",
        pattern=re.compile(
            rf"(?:Rx|Prescription|Medication ordered):\s*"
            rf"(?P<medication>{MEDICATION_PATTERN})\s*\.",
            re.IGNORECASE,
        ),
        build=_prescription_fields,
    ),
    _ExtractionRule(
        name="previous_therapy_used_for",
        pattern=re.compile(
            rf"(?P<medication>{MEDICATION_PATTERN})\s+used for\s+"
            r"(?P<value>\d+(?:\.\d+)?)\s*"
            r"(?P<unit>weeks?|months?|days?)"
            r"(?:[;,]?\s*(?P<outcome>inadequate response|adequate response|"
            r"no response|good response))?",
            re.IGNORECASE,
        ),
        build=_previous_therapy_used_for_fields,
    ),
    _ExtractionRule(
        name="previous_therapy_trial",
        pattern=re.compile(
            rf"Trial of\s+(?P<medication>{MEDICATION_PATTERN})\s+lasted\s+"
            r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>weeks?|months?|days?)"
            r"(?:\s+with\s+(?P<outcome>inadequate response|adequate response|"
            r"no response|good response))?",
            re.IGNORECASE,
        ),
        build=_previous_therapy_used_for_fields,
    ),
    _ExtractionRule(
        name="previous_therapy_documented",
        pattern=re.compile(
            rf"(?P<medication>{MEDICATION_PATTERN})\s*[—\-–]\s*"
            r"(?P<value>\d+(?:\.\d+)?)\s*"
            r"(?P<unit>weeks?|months?|days?)\s+of therapy documented",
            re.IGNORECASE,
        ),
        build=_previous_therapy_documented_fields,
    ),
    _ExtractionRule(
        name="previous_therapy_outcome_only",
        pattern=re.compile(
            rf"(?P<medication>{MEDICATION_PATTERN})\s*[—\-–]\s*"
            r"started[^.\n]*?discontinued due to\s+"
            r"(?P<outcome>inadequate response|adequate response|no response|good response)",
            re.IGNORECASE,
        ),
        build=_previous_therapy_outcome_only_fields,
    ),
    _ExtractionRule(
        name="lab_a1c",
        pattern=re.compile(
            r"(?:Hemoglobin\s+)?A1c\s*[:=]?\s*(?P<value>\d+(?:\.\d+)?)\s*"
            r"(?P<percent>%)?",
            re.IGNORECASE,
        ),
        build=_lab_value_fields,
    ),
    _ExtractionRule(
        name="therapy_duration_vague",
        pattern=re.compile(
            r"(?:on therapy|therapy)\s+for\s+"
            r"(?P<phrase>several months|a few weeks|some time|an extended period)",
            re.IGNORECASE,
        ),
        build=_therapy_duration_vague_fields,
    ),
]


@dataclass(eq=False)
class _Candidate:
    """One resolved-or-not fact in flight, before it becomes Evidence."""

    rule_name: str
    rule_index: int
    page_number: int
    page_confidence: float
    start: int
    end: int
    source_text: str
    filename: str
    fields: _ExtractedFields
    supporting: list[Provenance] = field(default_factory=list)
    forced_issue: Optional[tuple[IssueKind, str]] = None

    @property
    def span_length(self) -> int:
        return self.end - self.start

    def provenance(self, document_id: str) -> Provenance:
        return Provenance(
            document_id=document_id,
            filename=self.filename,
            page=self.page_number,
            start_char=self.start,
            end_char=self.end,
            source_text=self.source_text,
        )


def _signature(fields: _ExtractedFields) -> tuple[object, ...]:
    return (
        fields.evidence_type,
        fields.medication,
        fields.text_value,
        fields.value,
        fields.unit,
        fields.outcome,
    )


def combine_confidence(rule_confidence: float, source_confidence: float) -> float:
    """Fold the page's ingestion confidence into the rule's confidence.

    Digital text and text PDFs ingest at 1.0, so this is a no-op for them. An
    OCR'd scan ingests at the engine's own score, and a field read off a poor
    scan must not be presented as confidently as the same field read off clean
    text — README section 9 asks for the confidence a *reviewer* should act on,
    not the rule's confidence in isolation.

    The product assumes the two error sources are independent. That is an
    explicit engineering prior, not a measured joint distribution: the gold set
    is digital text only, so there is nothing yet to fit it against.
    """
    return round(rule_confidence * source_confidence, 4)


def _match_page(page: IngestedPage, filename: str) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for rule_index, rule in enumerate(_RULES):
        for match in rule.pattern.finditer(page.text):
            candidates.append(
                _Candidate(
                    rule_name=rule.name,
                    rule_index=rule_index,
                    page_number=page.page_number,
                    page_confidence=page.confidence,
                    start=match.start(),
                    end=match.end(),
                    source_text=match.group(0),
                    filename=filename,
                    fields=rule.build(match),
                )
            )
    return candidates


def resolve_overlaps(
    candidates: list[_Candidate],
) -> tuple[list[_Candidate], list[SuppressedSpan]]:
    """Keep one span per overlapping group of same-type matches.

    Two rules claiming the same text for the same evidence type is a precedence
    question, not two facts: the longer span is the more specific reading, and
    the rule declared first in `_RULES` breaks a tie. Overlaps *between*
    evidence types are left alone — a date inside a therapy line is genuinely
    two facts about the same words.
    """
    kept: list[_Candidate] = []
    suppressed: list[SuppressedSpan] = []
    grouped: dict[tuple[int, str], list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[(candidate.page_number, candidate.fields.evidence_type)].append(candidate)

    for group in grouped.values():
        ranked = sorted(group, key=lambda c: (-c.span_length, c.rule_index, c.start))
        winners: list[_Candidate] = []
        for candidate in ranked:
            overlapping = next(
                (
                    winner
                    for winner in winners
                    if candidate.start < winner.end and winner.start < candidate.end
                ),
                None,
            )
            if overlapping is None:
                winners.append(candidate)
                continue
            contained = overlapping.start <= candidate.start and candidate.end <= overlapping.end
            suppressed.append(
                SuppressedSpan(
                    evidence_type=candidate.fields.evidence_type,
                    rule=candidate.rule_name,
                    page=candidate.page_number,
                    start_char=candidate.start,
                    end_char=candidate.end,
                    source_text=candidate.source_text,
                    reason="contained in a longer span" if contained else "overlapping span",
                    superseded_by=overlapping.rule_name,
                )
            )
        kept.extend(winners)

    kept.sort(key=lambda c: (c.page_number, c.start, c.rule_index))
    suppressed.sort(key=lambda s: (s.page, s.start_char, s.rule))
    return kept, suppressed


def merge_repeated_mentions(candidates: list[_Candidate]) -> list[_Candidate]:
    """Collapse identical normalized facts stated more than once in one document.

    A payer named on the card heading and again on a coverage line is one fact
    with two citations, not two facts. The strongest span (then the earliest)
    anchors the record and the rest become supporting provenance, so every
    mention stays citable. Facts that differ in *any* normalized field are
    never merged — two therapy durations for the same drug stay two facts.
    """
    grouped: dict[tuple[object, ...], list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[_signature(candidate.fields)].append(candidate)

    merged: list[_Candidate] = []
    for group in grouped.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        ranked = sorted(
            group, key=lambda c: (-c.fields.confidence, c.page_number, c.start, c.rule_index)
        )
        anchor, *rest = ranked
        anchor.supporting.extend(
            provenance
            for other in rest
            for provenance in [other.provenance(document_id=""), *other.supporting]
        )
        anchor.page_confidence = min(candidate.page_confidence for candidate in group)
        merged.append(anchor)

    merged.sort(key=lambda c: (c.page_number, c.start, c.rule_index))
    return merged


def link_previous_therapy(candidates: list[_Candidate]) -> list[_Candidate]:
    """Combine a therapy duration span with its outcome span, when unambiguous.

    The `Evidence` contract now cites several spans for one fact, so a document
    that states "Drug A — 16 weeks of therapy documented" on one line and
    "Drug A — started ..., discontinued due to inadequate response" on the next
    can yield the single complete fact a duration+outcome criterion needs.

    The link is only made when the document leaves no choice: exactly one
    duration-only span and exactly one outcome-only span for that medication.
    Any other shape (two durations, two outcomes, a different medication) is
    left unlinked, and the outcome spans are flagged AMBIGUOUS_LINKAGE — the
    system will not pick one pairing over another on the reviewer's behalf.
    """
    by_medication: dict[str, list[_Candidate]] = defaultdict(list)
    unlinkable: list[_Candidate] = []
    for candidate in candidates:
        if candidate.fields.evidence_type != "previous_therapy" or not candidate.fields.medication:
            unlinkable.append(candidate)
        else:
            by_medication[candidate.fields.medication.casefold()].append(candidate)

    linked: list[_Candidate] = []
    for group in by_medication.values():
        durations = [c for c in group if c.fields.value is not None and c.fields.outcome is None]
        outcomes = [c for c in group if c.fields.value is None and c.fields.outcome is not None]

        if len(durations) == 1 and len(outcomes) == 1:
            linked.append(_link_pair(durations[0], outcomes[0]))
            linked.extend(c for c in group if c is not durations[0] and c is not outcomes[0])
            continue

        if durations and outcomes:
            for candidate in outcomes:
                candidate.forced_issue = (
                    IssueKind.AMBIGUOUS_LINKAGE,
                    (
                        f"{len(durations)} therapy duration span(s) and {len(outcomes)} outcome "
                        f"span(s) for {candidate.fields.medication} are equally plausible "
                        "pairings; the spans are kept separate for human review."
                    ),
                )
        linked.extend(group)

    combined = unlinkable + linked
    combined.sort(key=lambda c: (c.page_number, c.start, c.rule_index))
    return combined


def _link_pair(duration: _Candidate, outcome: _Candidate) -> _Candidate:
    """Anchor the linked fact on the duration span — it carries the number."""
    outcome_confidence = outcome.fields.linked_confidence or outcome.fields.confidence
    duration.fields = replace(
        duration.fields,
        outcome=outcome.fields.outcome,
        confidence=min(duration.fields.confidence, outcome_confidence),
        incomplete_reason=None,
        linked_confidence=None,
    )
    duration.supporting.append(outcome.provenance(document_id=""))
    duration.supporting.extend(outcome.supporting)
    duration.page_confidence = min(duration.page_confidence, outcome.page_confidence)
    return duration


def _issue_for(
    candidate: _Candidate, confidence: float, threshold: float
) -> Optional[tuple[IssueKind, str]]:
    """Decide the single most specific reason this field needs a human."""
    if candidate.forced_issue is not None:
        return candidate.forced_issue
    if candidate.fields.incomplete_reason is not None:
        return (
            IssueKind.INCOMPLETE_VALUE,
            (
                f"{candidate.fields.incomplete_reason.capitalize()}; the value is retained with "
                "its source but is not explicit enough for a deterministic check."
            ),
        )
    if confidence < threshold:
        source_note = (
            ""
            if candidate.page_confidence >= 1.0
            else f" after folding in a page ingestion confidence of {candidate.page_confidence:.2f}"
        )
        return (
            IssueKind.LOW_CONFIDENCE,
            (
                f"Confidence {confidence:.2f} is below the {threshold:.2f} review "
                f"threshold{source_note}; value is retained but requires human review."
            ),
        )
    return None


def extract_evidence(
    document: IngestedDocument,
    *,
    document_id: str,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> ExtractionResult:
    """Extract typed, provenance-carrying Evidence from an ingested document's text.

    Every fact the rules find is retained regardless of confidence — nothing is
    silently discarded. A field that needs a human (misread risk, an incomplete
    value, or an ambiguous linkage) additionally produces an ExtractionIssue so
    a caller can route it to review. A page with no matching text simply yields
    no Evidence — an absent requirement stays MISSING downstream rather than
    being fabricated.
    """
    raw: list[_Candidate] = []
    for page in document.pages:
        raw.extend(_match_page(page, document.filename))

    resolved, suppressed = resolve_overlaps(raw)
    resolved = merge_repeated_mentions(resolved)
    resolved = link_previous_therapy(resolved)

    evidence: list[Evidence] = []
    issues: list[ExtractionIssue] = []
    for index, candidate in enumerate(resolved, start=1):
        confidence = combine_confidence(candidate.fields.confidence, candidate.page_confidence)
        item = Evidence(
            id=f"{document_id}-EV{index}",
            evidence_type=candidate.fields.evidence_type,
            medication=candidate.fields.medication,
            text_value=candidate.fields.text_value,
            value=candidate.fields.value,
            unit=candidate.fields.unit,
            outcome=candidate.fields.outcome,
            confidence=confidence,
            provenance=candidate.provenance(document_id),
            supporting_provenance=[
                replace_document_id(provenance, document_id) for provenance in candidate.supporting
            ],
            source_confidence=candidate.page_confidence,
            extraction_rule=candidate.rule_name,
            extraction_method=EXTRACTOR_VERSION,
        )
        evidence.append(item)
        issue = _issue_for(candidate, confidence, confidence_threshold)
        if issue is not None:
            kind, reason = issue
            issues.append(
                ExtractionIssue(
                    evidence_id=item.id,
                    evidence_type=item.evidence_type,
                    confidence=item.confidence,
                    reason=reason,
                    kind=kind,
                )
            )
    return ExtractionResult(evidence=evidence, issues=issues, suppressed=suppressed)


def replace_document_id(provenance: Provenance, document_id: str) -> Provenance:
    """Stamp the document ID onto a span captured before the ID was known."""
    return provenance.model_copy(update={"document_id": document_id})


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
        "extractor_version": EXTRACTOR_VERSION,
        "evidence": [item.model_dump() for item in result.evidence],
        "issues": [issue.model_dump() for issue in result.issues],
        "suppressed_spans": [span.model_dump() for span in result.suppressed],
        "requires_human_review": result.requires_human_review,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
