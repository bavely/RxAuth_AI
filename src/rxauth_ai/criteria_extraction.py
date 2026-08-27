"""Policy prose to structured requirements (README section 11).

README section 6 states the hybrid principle this module exists to serve: a
policy line like "at least 12 weeks of Drug A" is converted here into a
structured rule (`operator: >=, expected_value: 12, unit: weeks`), and
`matching` then evaluates it in plain Python (`16 >= 12 -> SATISFIED`). The
language never reaches the comparison, and the comparison never re-reads the
language.

Three failure modes shape the design, and each is handled explicitly rather
than assumed away:

- **A requirement that is silently dropped is worse than one that fails.** A
  policy with six criteria evaluated as five produces a case that looks readier
  than it is. Every enumerated item therefore becomes a `Criterion`; one no
  rule can structure becomes a `criterion_type="unstructured"` criterion that
  `matching` routes to a human, plus a `CriteriaIssue` explaining why.
- **An exclusion is not a criterion.** "Not covered when ANY of the following
  applies" reads almost identically to a coverage list. Exclusions are parsed
  from their own section, marked `polarity="exclusion"`, and kept out of the
  conjunctive criteria list — the deterministic matcher has no NOT semantics,
  so pretending otherwise would invert a result.
- **ALL is not ANY.** The connective is read from the section's lead-in and
  carried on the policy. A disjunctive policy is not evaluated as a conjunction
  of its items; `pipeline` refuses it by name instead.

Every criterion records the payer, policy version, effective date, page,
character span, exact source text, the rule that produced it, and this module's
version (README section 11 and section 18).
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from .medications import MEDICATION_PATTERN, normalize_medication
from .models import Criterion, Policy, Provenance
from .policy_corpus import DEFAULT_POLICY_DIR, PolicyChunk, PolicyDocument, load_corpus

CRITERIA_EXTRACTOR_VERSION = "policy-rules-v1"
DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD = 0.65

# Payer prose states a comparison in words; the matcher needs an operator. This
# mapping is the whole translation, kept in one auditable place. Longest first,
# so "no greater than" is never read as the "greater than" it contains.
_COMPARATORS: dict[str, str] = {
    "no fewer than": ">=",
    "no less than": ">=",
    "a minimum of": ">=",
    "at least": ">=",
    "no greater than": "<=",
    "no more than": "<=",
    "at most": "<=",
    "up to": "<=",
    "less than": "<",
    "below": "<",
    "under": "<",
    "greater than": ">",
    "above": ">",
    "over": ">",
    "exactly": "==",
    "equal to": "==",
}
_COMPARATOR_PATTERN = "|".join(
    re.escape(phrase) for phrase in sorted(_COMPARATORS, key=lambda item: (-len(item), item))
)

_LAB_TYPES: dict[str, str] = {
    "a1c": "lab_a1c",
    "hemoglobin a1c": "lab_a1c",
    "ldl cholesterol": "lab_ldl_cholesterol",
    "alt": "lab_alt",
    "egfr": "lab_egfr",
    "crp": "lab_crp",
}
_LAB_PATTERN = "|".join(
    re.escape(name) for name in sorted(_LAB_TYPES, key=lambda item: (-len(item), item))
)

_ITEM_NUMBER = re.compile(r"^\s*\d+\.\s*")


class CriteriaIssueKind(str, Enum):
    """Why one extracted requirement needs a human before it can be trusted.

    `UNSTRUCTURED_REQUIREMENT` is the important one: the sentence was read, it
    is a real requirement, and no deterministic rule could turn it into a
    comparison. It is kept and routed, never dropped.
    """

    UNSTRUCTURED_REQUIREMENT = "unstructured_requirement"
    LOW_CONFIDENCE = "low_confidence"
    DISJUNCTIVE_SECTION = "disjunctive_section"


class CriteriaIssue(BaseModel):
    criterion_id: str
    policy_id: str
    kind: CriteriaIssueKind
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    source_text: str


@dataclass(frozen=True)
class _CriterionFields:
    criterion_type: str
    medication: Optional[str] = None
    operator: Optional[str] = None
    expected_value: Optional[float] = None
    unit: Optional[str] = None
    required_outcome: Optional[str] = None
    confidence: float = 0.9


_FieldBuilder = Callable[[re.Match[str]], _CriterionFields]


@dataclass(frozen=True)
class _CriteriaRule:
    name: str
    pattern: re.Pattern[str]
    build: _FieldBuilder


def _operator_for(raw: Optional[str]) -> Optional[str]:
    return None if raw is None else _COMPARATORS[raw.strip().casefold()]


def _normalize_unit(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    lowered = raw.strip().casefold()
    for stem, unit in (("week", "weeks"), ("month", "months"), ("day", "days")):
        if lowered.startswith(stem):
            return unit
    return lowered


def _normalize_outcome(raw: str) -> str:
    return raw.strip().casefold().replace(" ", "_")


def _prior_therapy_response_fields(match: re.Match[str]) -> _CriterionFields:
    has_duration = match.group("value") is not None
    return _CriterionFields(
        criterion_type="previous_therapy",
        medication=normalize_medication(match.group("medication")),
        operator=_operator_for(match.group("comparator")),
        expected_value=float(match.group("value")) if has_duration else None,
        unit=_normalize_unit(match.group("unit")),
        required_outcome=_normalize_outcome(match.group("outcome")),
        # A response requirement with no stated duration is a weaker rule: it
        # constrains the outcome but leaves "after how long" to the reader.
        confidence=0.95 if has_duration else 0.80,
    )


def _prior_therapy_duration_fields(match: re.Match[str]) -> _CriterionFields:
    return _CriterionFields(
        criterion_type="previous_therapy",
        medication=normalize_medication(match.group("medication")),
        operator=_operator_for(match.group("comparator")),
        expected_value=float(match.group("value")),
        unit=_normalize_unit(match.group("unit")),
        confidence=0.95,
    )


def _diagnosis_fields(match: re.Match[str]) -> _CriterionFields:
    return _CriterionFields(criterion_type="diagnosis", operator="exists", confidence=0.95)


def _lab_threshold_fields(match: re.Match[str]) -> _CriterionFields:
    lab = match.group("lab").casefold()
    return _CriterionFields(
        criterion_type=_LAB_TYPES[lab],
        operator=_operator_for(match.group("comparator")),
        expected_value=float(match.group("value")),
        # Only A1c carries a unit in the evidence contract; the other labs are
        # reported as bare numbers, and inventing a unit here would make every
        # comparison fail the matcher's unit check.
        unit="percent" if lab.endswith("a1c") and match.group("percent") else None,
        confidence=0.95,
    )


def _screening_documentation_fields(match: re.Match[str]) -> _CriterionFields:
    return _CriterionFields(criterion_type="screening_doc", operator="exists", confidence=0.92)


def _therapy_duration_fields(match: re.Match[str]) -> _CriterionFields:
    return _CriterionFields(
        criterion_type="therapy_duration",
        operator=_operator_for(match.group("comparator")),
        expected_value=float(match.group("value")),
        unit=_normalize_unit(match.group("unit")),
        confidence=0.92,
    )


# Declaration order is precedence: the first rule that matches wins, so the
# rules that read *more* of the sentence are declared first. "inadequate
# response ... after at least 12 weeks" must not be reduced to a bare duration.
_RULES: list[_CriteriaRule] = [
    _CriteriaRule(
        name="prior_therapy_response",
        pattern=re.compile(
            r"(?P<outcome>inadequate response|adequate response|no response|good response)\s+to\s+"
            r"(?:prior\s+)?therapy\s+with\s+"
            rf"(?P<medication>{MEDICATION_PATTERN})"
            rf"(?:\s+after\s+(?P<comparator>{_COMPARATOR_PATTERN})\s+"
            r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>weeks?|months?|days?))?",
            re.IGNORECASE,
        ),
        build=_prior_therapy_response_fields,
    ),
    _CriteriaRule(
        name="prior_therapy_duration",
        pattern=re.compile(
            rf"completed\s+(?P<comparator>{_COMPARATOR_PATTERN})\s+"
            r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>weeks?|months?|days?)\s+of\s+therapy\s+with\s+"
            rf"(?P<medication>{MEDICATION_PATTERN})",
            re.IGNORECASE,
        ),
        build=_prior_therapy_duration_fields,
    ),
    _CriteriaRule(
        name="documented_diagnosis",
        pattern=re.compile(
            r"documented\s+diagnosis\s+of\s+(?P<condition>[^.\r\n]+)", re.IGNORECASE
        ),
        build=_diagnosis_fields,
    ),
    _CriteriaRule(
        name="therapy_duration_documented",
        pattern=re.compile(
            r"duration\s+of\s+the\s+most\s+recent\s+therapy\s+course\s+is\s+documented\s+and\s+is\s+"
            rf"(?P<comparator>{_COMPARATOR_PATTERN})\s+"
            r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>weeks?|months?|days?)",
            re.IGNORECASE,
        ),
        build=_therapy_duration_fields,
    ),
    _CriteriaRule(
        name="lab_threshold",
        pattern=re.compile(
            rf"(?:most recent|current|latest)\s+(?P<lab>{_LAB_PATTERN})\s+is\s+"
            rf"(?P<comparator>{_COMPARATOR_PATTERN})\s+"
            r"(?P<value>\d+(?:\.\d+)?)\s*(?P<percent>percent|%)?",
            re.IGNORECASE,
        ),
        build=_lab_threshold_fields,
    ),
    _CriteriaRule(
        name="screening_documentation",
        pattern=re.compile(
            r"(?:required\s+)?screening\s+documentation\s+is\s+on\s+file", re.IGNORECASE
        ),
        build=_screening_documentation_fields,
    ),
]


@dataclass
class CriteriaExtractionResult:
    """Structured requirements for one policy version, plus what needs a human."""

    policy_id: str
    policy_version: str
    criteria: list[Criterion] = field(default_factory=list)
    exclusions: list[Criterion] = field(default_factory=list)
    issues: list[CriteriaIssue] = field(default_factory=list)
    connective: str = "all"
    extractor_version: str = CRITERIA_EXTRACTOR_VERSION

    @property
    def requires_human_review(self) -> bool:
        return bool(self.issues)

    @property
    def unstructured_count(self) -> int:
        return sum(
            1 for issue in self.issues if issue.kind is CriteriaIssueKind.UNSTRUCTURED_REQUIREMENT
        )


def _requirement_text(chunk: PolicyChunk) -> str:
    """The requirement sentence with its list numbering removed."""
    return _ITEM_NUMBER.sub("", chunk.text).strip()


def _provenance(chunk: PolicyChunk) -> Provenance:
    """Cite the policy the same way evidence cites a patient document."""
    return Provenance(
        document_id=chunk.policy_id,
        filename=chunk.filename,
        page=chunk.page,
        start_char=chunk.start_char,
        end_char=chunk.end_char,
        source_text=chunk.text,
    )


def _structure(chunk: PolicyChunk) -> tuple[_CriterionFields, Optional[str]]:
    """Apply the first matching rule; fall back to an unstructured requirement."""
    text = _requirement_text(chunk)
    for rule in _RULES:
        match = rule.pattern.search(text)
        if match is not None:
            return rule.build(match), rule.name
    return (
        _CriterionFields(
            criterion_type="unstructured",
            # Deliberately below the review threshold. The sentence was read
            # correctly; what is missing is a machine-checkable form of it, and
            # that is exactly what a reviewer has to supply.
            confidence=0.30,
        ),
        None,
    )


def _build_criterion(
    chunk: PolicyChunk,
    *,
    identifier: str,
    polarity: str,
    fields: _CriterionFields,
    rule_name: Optional[str],
) -> Criterion:
    return Criterion(
        id=identifier,
        policy_id=chunk.policy_id,
        description=_requirement_text(chunk),
        criterion_type=fields.criterion_type,
        medication=fields.medication,
        operator=fields.operator,
        expected_value=fields.expected_value,
        unit=fields.unit,
        required_outcome=fields.required_outcome,
        polarity=polarity,
        source_section=chunk.section_title,
        confidence=fields.confidence,
        extraction_rule=rule_name,
        extractor_version=CRITERIA_EXTRACTOR_VERSION,
        provenance=_provenance(chunk),
    )


def extract_criteria(
    document: PolicyDocument,
    *,
    confidence_threshold: float = DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD,
) -> CriteriaExtractionResult:
    """Convert one policy's enumerated requirements into structured criteria."""
    criteria_items = [
        chunk for chunk in document.chunks_of_kind("criteria") if chunk.item_number is not None
    ]
    exclusion_items = [
        chunk for chunk in document.chunks_of_kind("exclusions") if chunk.item_number is not None
    ]
    connectives = {
        chunk.connective for chunk in document.chunks_of_kind("criteria") if chunk.connective
    }
    # A section with no stated connective is not assumed conjunctive; "all" is
    # only claimed when the policy says so, and a mixed section stays unknown.
    connective = connectives.pop() if len(connectives) == 1 else "unknown"

    result = CriteriaExtractionResult(
        policy_id=document.policy_id,
        policy_version=document.version,
        connective=connective,
    )

    for index, chunk in enumerate(criteria_items, start=1):
        fields, rule_name = _structure(chunk)
        criterion = _build_criterion(
            chunk,
            identifier=f"C{index}",
            polarity="inclusion",
            fields=fields,
            rule_name=rule_name,
        )
        result.criteria.append(criterion)

        if criterion.criterion_type == "unstructured":
            result.issues.append(
                CriteriaIssue(
                    criterion_id=criterion.id,
                    policy_id=document.policy_id,
                    kind=CriteriaIssueKind.UNSTRUCTURED_REQUIREMENT,
                    confidence=fields.confidence,
                    reason=(
                        "No deterministic rule converts this requirement into a comparison. "
                        "It is retained with its citation and routed to a reviewer rather than "
                        "dropped, because a policy evaluated against fewer criteria than it "
                        "states reads as readier than it is."
                    ),
                    source_text=criterion.description,
                )
            )
        elif fields.confidence < confidence_threshold:
            result.issues.append(
                CriteriaIssue(
                    criterion_id=criterion.id,
                    policy_id=document.policy_id,
                    kind=CriteriaIssueKind.LOW_CONFIDENCE,
                    confidence=fields.confidence,
                    reason=(
                        f"Structured with confidence {fields.confidence:.2f}, below the "
                        f"{confidence_threshold:.2f} review threshold."
                    ),
                    source_text=criterion.description,
                )
            )

    for index, chunk in enumerate(exclusion_items, start=1):
        fields, rule_name = _structure(chunk)
        result.exclusions.append(
            _build_criterion(
                chunk,
                identifier=f"X{index}",
                polarity="exclusion",
                fields=fields,
                rule_name=rule_name,
            )
        )

    if connective != "all" and result.criteria:
        result.issues.append(
            CriteriaIssue(
                criterion_id="*",
                policy_id=document.policy_id,
                kind=CriteriaIssueKind.DISJUNCTIVE_SECTION,
                confidence=0.0,
                reason=(
                    f"The coverage-criteria section joins its items with {connective!r}. The "
                    "deterministic matcher evaluates a conjunction, so this policy is not "
                    "evaluated automatically; treating an ANY list as an ALL list would fail a "
                    "case the policy actually covers."
                ),
                source_text=result.criteria[0].description,
            )
        )

    return result


def build_policy(
    document: PolicyDocument,
    *,
    confidence_threshold: float = DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD,
) -> tuple[Policy, CriteriaExtractionResult]:
    """Assemble the `Policy` the matcher consumes, from a retrieved document."""
    extraction = extract_criteria(document, confidence_threshold=confidence_threshold)
    policy = Policy(
        id=document.policy_id,
        payer=document.payer,
        medication=document.normalized_medication,
        indication=document.indication,
        effective_date=document.effective_date,
        superseded_date=document.superseded_date,
        source_url=document.source_url,
        filename=document.filename,
        version=document.version,
        criteria_connective=extraction.connective,
        criteria=extraction.criteria,
        exclusions=extraction.exclusions,
        extractor_version=CRITERIA_EXTRACTOR_VERSION,
    )
    return policy, extraction


def load_policy_documents(policy_dir: Path = DEFAULT_POLICY_DIR) -> dict[str, PolicyDocument]:
    """Every parsed policy version, keyed by `policy_id:version`."""
    return {document.key: document for document in load_corpus(policy_dir)}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a payer policy's prose requirements into structured criteria."
    )
    parser.add_argument("policy_id", help="Policy ID, optionally as POLICY:VERSION.")
    parser.add_argument("--policy-dir", type=Path, default=DEFAULT_POLICY_DIR)
    parser.add_argument(
        "--confidence-threshold", type=float, default=DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD
    )
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    if not 0.0 <= args.confidence_threshold <= 1.0:
        parser.error("--confidence-threshold must be between 0 and 1.")

    documents = load_policy_documents(args.policy_dir)
    matches = [
        document
        for key, document in documents.items()
        if key == args.policy_id or document.policy_id == args.policy_id
    ]
    if not matches:
        parser.error(
            f"Unknown policy {args.policy_id!r}. Available: {', '.join(sorted(documents))}."
        )
    if len(matches) > 1:
        parser.error(
            f"{args.policy_id!r} names several versions: "
            f"{', '.join(sorted(document.key for document in matches))}. Pass POLICY:VERSION."
        )

    policy, extraction = build_policy(matches[0], confidence_threshold=args.confidence_threshold)
    output = {
        "policy": policy.model_dump(mode="json"),
        "connective": extraction.connective,
        "issues": [issue.model_dump(mode="json") for issue in extraction.issues],
        "requires_human_review": extraction.requires_human_review,
    }

    if args.json_only:
        print(json.dumps(output, indent=2))
        return

    line = "-" * 72
    print(f"{policy.id} v{policy.version} — {policy.payer}")
    print(f"{policy.medication} / {policy.indication}, effective {policy.effective_date}")
    print(f"Criteria connective: {extraction.connective}   Extractor: {policy.extractor_version}")
    print(line)
    for criterion in policy.criteria:
        rule = criterion.extraction_rule or "unstructured"
        comparison = " ".join(
            str(part)
            for part in (criterion.operator, criterion.expected_value, criterion.unit)
            if part is not None
        )
        print(f"{criterion.id}  {criterion.criterion_type:18} {comparison or '(no comparison)'}")
        if criterion.medication:
            print(f"      medication: {criterion.medication}")
        if criterion.required_outcome:
            print(f"      outcome:    {criterion.required_outcome}")
        print(f"      rule:       {rule} (confidence {criterion.confidence:.2f})")
        print(
            f"      source:     {criterion.provenance.filename} p.{criterion.provenance.page} "
            f"chars {criterion.provenance.start_char}-{criterion.provenance.end_char}"
        )
        print(f'      text:       "{criterion.description}"')
    if policy.exclusions:
        print(line)
        print("Exclusions (parsed, not evaluated — the matcher has no NOT semantics)")
        for exclusion in policy.exclusions:
            print(f'{exclusion.id}  "{exclusion.description}"')
    if extraction.issues:
        print(line)
        print("Routed to human review")
        for issue in extraction.issues:
            print(f"{issue.criterion_id:4} {issue.kind.value:24} {issue.reason}")


if __name__ == "__main__":
    main()
