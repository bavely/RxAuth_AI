"""Assemble a case from real documents and run it through the Milestone 0 spine.

Milestone 0 (README section 23) proved the spine with hand-authored evidence:
the classification and extraction stages were fixtures, and the point was the
*shape* of the flow. Phase 1.5 and Phase 3 then built the real classifier and
the real extractor. This module removes the fixtures — it walks a directory of
synthetic documents and runs

    ingest -> classify -> extract -> resolve -> Case -> match -> groundedness

so the criterion results are produced by the components the project actually
ships, and every value in the report traces back to a span in a file on disk.

Two things stay fixtures on purpose, because they belong to later phases:

- the policy, which README section 10 replaces with retrieval over real public
  payer documents;
- `pa_required`, which README section 3 requires to come from a synthetic
  trigger or explicit user input and never from policy text. It is read from
  the case manifest as declared input, not inferred from anything.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from .extraction import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    EXTRACTOR_VERSION,
    ExtractionIssue,
    SuppressedSpan,
    extract_evidence,
)
from .ingestion import ingest_document
from .models import Case, CaseReadinessReport, Document, Evidence, EvidenceLink, Policy
from .pipeline import run_pipeline
from .synthetic_case import build_policy

DEFAULT_CLASSIFIER_PATH = Path("artifacts/classifier_baseline.pkl")
MANIFEST_FILENAME = "case.json"
_DOCUMENT_SUFFIXES = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


class CaseManifest(BaseModel):
    """Declared, non-inferrable facts about a synthetic case packet."""

    case_id: str
    patient_synthetic_id: str
    payer: str
    medication: str
    indication: str
    pa_required: bool = Field(
        description=(
            "Synthetic benefit trigger or explicit user input (README section 3). Never "
            "inferred from policy text — a public policy cannot establish a live benefit."
        )
    )
    policy_id: str
    plan: str | None = None
    note: str | None = None


class DocumentClassifierLike(Protocol):
    """The slice of `classifier.DocumentClassifier` this module depends on.

    Declared as a protocol so a caller — a test, or a future service — can
    supply its own classifier without loading a pickled artifact.
    """

    def classify_path(self, path: Path, *, document_id: str) -> tuple[Document, bool]: ...


@dataclass
class AssembledCase:
    case: Case
    manifest: CaseManifest
    documents_requiring_review: list[str] = field(default_factory=list)
    extraction_issues: list[ExtractionIssue] = field(default_factory=list)
    suppressed_spans: list[SuppressedSpan] = field(default_factory=list)
    evidence_links: list[EvidenceLink] = field(default_factory=list)

    @property
    def evidence_requiring_review(self) -> int:
        return len(self.extraction_issues)


def load_manifest(case_dir: Path) -> CaseManifest:
    manifest_path = Path(case_dir) / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"{manifest_path} not found. A case packet needs a {MANIFEST_FILENAME} declaring "
            "the payer, medication, indication, applicable policy, and the pa_required trigger."
        )
    return CaseManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))


def case_document_paths(case_dir: Path) -> list[Path]:
    """Every ingestable document in the packet, in a stable order."""
    paths = sorted(
        path
        for path in Path(case_dir).iterdir()
        if path.is_file()
        and path.name != MANIFEST_FILENAME
        and path.suffix.casefold() in _DOCUMENT_SUFFIXES
    )
    if not paths:
        raise FileNotFoundError(f"No ingestable documents found in {case_dir}.")
    return paths


def load_classifier(path: Path = DEFAULT_CLASSIFIER_PATH) -> DocumentClassifierLike:
    """Load the trained baseline classifier, or explain how to produce it.

    The artifact is a build output and is not committed, so a fresh clone will
    not have one. That is a setup step, not a failure of the case packet.
    """
    from .classifier import DocumentClassifier

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Classifier artifact not found at {path}. Build it first:\n"
            "    uv run rxauth-build-dataset\n"
            "    uv run rxauth-train-classifier"
        )
    return DocumentClassifier.load(path)


def _evidence_signature(item: Evidence) -> tuple[object, ...]:
    return (
        item.evidence_type,
        item.medication,
        item.text_value,
        item.value,
        item.unit,
        item.outcome,
    )


def link_cross_document_evidence(evidence: list[Evidence]) -> list[EvidenceLink]:
    """Link exact normalized facts repeated across distinct documents.

    This is intentionally more conservative than within-document therapy
    linking: a duration in one document is not combined with an outcome in
    another. Cross-document text corroborates a fact only when every normalized
    field agrees, preventing assembly from manufacturing one complete clinical
    statement out of two incomplete records.
    """
    grouped: dict[tuple[object, ...], list[Evidence]] = defaultdict(list)
    for item in evidence:
        grouped[_evidence_signature(item)].append(item)

    links: list[EvidenceLink] = []
    for group in grouped.values():
        document_ids = {
            item.provenance.document_id
            for item in group
            if item.provenance.document_id is not None
        }
        if len(document_ids) < 2:
            continue
        ranked = sorted(group, key=lambda item: (-item.confidence, item.id))
        links.append(
            EvidenceLink(
                id=f"XLINK-{len(links) + 1}",
                evidence_type=ranked[0].evidence_type,
                canonical_evidence_id=ranked[0].id,
                evidence_ids=[item.id for item in ranked],
                document_ids=sorted(document_ids),
                provenance=[source for item in ranked for source in item.sources],
            )
        )
    return links


def assemble_case(
    case_dir: Path,
    *,
    classifier: DocumentClassifierLike,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> AssembledCase:
    """Classify and extract every document in a packet into one typed Case.

    Document IDs are assigned by filename order, and extraction scopes evidence
    IDs to their document, so every evidence ID in the assembled case is unique
    and stable across runs.
    """
    manifest = load_manifest(case_dir)
    documents: list[Document] = []
    evidence: list[Evidence] = []
    needs_classification_review: list[str] = []
    issues: list[ExtractionIssue] = []
    suppressed: list[SuppressedSpan] = []

    for index, path in enumerate(case_document_paths(case_dir), start=1):
        document_id = f"D{index}"
        document, requires_review = classifier.classify_path(path, document_id=document_id)
        documents.append(document)
        if requires_review:
            needs_classification_review.append(document_id)

        result = extract_evidence(
            ingest_document(path),
            document_id=document_id,
            confidence_threshold=confidence_threshold,
        )
        evidence.extend(result.evidence)
        issues.extend(result.issues)
        suppressed.extend(result.suppressed)

    case = Case(
        id=manifest.case_id,
        patient_synthetic_id=manifest.patient_synthetic_id,
        payer=manifest.payer,
        plan=manifest.plan,
        medication=manifest.medication,
        indication=manifest.indication,
        pa_required=manifest.pa_required,
        documents=documents,
        evidence=evidence,
    )
    return AssembledCase(
        case=case,
        manifest=manifest,
        documents_requiring_review=needs_classification_review,
        extraction_issues=issues,
        suppressed_spans=suppressed,
        evidence_links=link_cross_document_evidence(evidence),
    )


def resolve_policy(policy_id: str) -> Policy:
    """Look up the policy a case packet names.

    Until README section 10 lands, there is exactly one policy fixture. Raising
    on an unknown ID keeps a packet from silently being evaluated against the
    wrong requirements.
    """
    policy = build_policy()
    if policy.id != policy_id:
        raise ValueError(
            f"Unknown policy_id {policy_id!r}. The only policy available before payer-policy "
            f"retrieval (README section 10) is {policy.id!r}."
        )
    return policy


def run_case(
    case_dir: Path,
    *,
    classifier: DocumentClassifierLike,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> tuple[CaseReadinessReport, AssembledCase]:
    assembled = assemble_case(
        case_dir, classifier=classifier, confidence_threshold=confidence_threshold
    )
    policy = resolve_policy(assembled.manifest.policy_id)
    report = run_pipeline(
        assembled.case,
        policy,
        evidence_requiring_review=assembled.evidence_requiring_review,
        documents_requiring_classification_review=len(assembled.documents_requiring_review),
    )
    return report, assembled


def build_output(
    report: CaseReadinessReport, assembled: AssembledCase, case_dir: Path
) -> dict[str, object]:
    return {
        "readiness": report.model_dump(mode="json"),
        "assembly": {
            "case_directory": Path(case_dir).as_posix(),
            "extractor_version": EXTRACTOR_VERSION,
            "documents": [
                document.model_dump(mode="json") for document in assembled.case.documents
            ],
            "documents_requiring_classification_review": assembled.documents_requiring_review,
            "evidence": [item.model_dump(mode="json") for item in assembled.case.evidence],
            "extraction_issues": [
                issue.model_dump(mode="json") for issue in assembled.extraction_issues
            ],
            "suppressed_spans": [
                span.model_dump(mode="json") for span in assembled.suppressed_spans
            ],
            "cross_document_evidence_links": [
                link.model_dump(mode="json") for link in assembled.evidence_links
            ],
        },
    }


def print_extraction_summary(assembled: AssembledCase) -> None:
    line = "-" * 56
    print("Extracted evidence")
    print(line)
    for item in assembled.case.evidence:
        detail = " ".join(
            part
            for part in (
                item.medication,
                None if item.value is None else f"{item.value:g}",
                item.unit,
                item.outcome,
                item.text_value,
            )
            if part
        )
        print(f"{item.id:14} {item.evidence_type:22} conf {item.confidence:.2f}  {detail}")
        for provenance in item.sources:
            print(
                f'               ↳ {provenance.filename} p.{provenance.page} "{provenance.source_text}"'
            )
    print()

    if assembled.extraction_issues:
        print("Fields routed to human review")
        print(line)
        for issue in assembled.extraction_issues:
            print(f"{issue.evidence_id:14} {issue.kind.value:20} {issue.reason}")
        print()

    if assembled.suppressed_spans:
        print("Spans suppressed during overlap resolution")
        print(line)
        for span in assembled.suppressed_spans:
            print(f'{span.rule:30} {span.reason} (kept: {span.superseded_by}) "{span.source_text}"')
        print()

    if assembled.evidence_links:
        print("Cross-document corroboration")
        print(line)
        for link in assembled.evidence_links:
            print(
                f"{link.id:14} {link.evidence_type:22} "
                f"{', '.join(link.evidence_ids)} across {', '.join(link.document_ids)}"
            )
        print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one document packet end to end: ingest, classify, extract, evaluate."
    )
    parser.add_argument("case_dir", type=Path, help="Directory holding case.json and documents.")
    parser.add_argument("--classifier-path", type=Path, default=DEFAULT_CLASSIFIER_PATH)
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    parser.add_argument("--confidence-threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD)
    parser.add_argument("--json-only", action="store_true", help="Print only the JSON output.")
    args = parser.parse_args()

    if not 0.0 <= args.confidence_threshold <= 1.0:
        parser.error("--confidence-threshold must be between 0 and 1.")

    classifier = load_classifier(args.classifier_path)
    report, assembled = run_case(
        args.case_dir,
        classifier=classifier,
        confidence_threshold=args.confidence_threshold,
    )
    output = build_output(report, assembled, args.case_dir)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / f"case_{report.case_id}.json"
    out_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8", newline="\n")

    if args.json_only:
        print(json.dumps(output, indent=2))
        return

    from .cli import print_report

    print_report(report, report.evaluations)
    print_extraction_summary(assembled)
    print(f"Structured output written to: {out_path}")


if __name__ == "__main__":
    main()
