"""Assemble a case from real documents and run it through the Milestone 0 spine.

Milestone 0 (README section 23) proved the spine with hand-authored evidence:
the classification and extraction stages were fixtures, and the point was the
*shape* of the flow. Phase 1.5 and Phase 3 then built the real classifier and
the real extractor. This module removes the fixtures — it walks a directory of
synthetic documents and runs

    ingest -> classify -> extract -> resolve -> Case -> match -> groundedness

so the criterion results are produced by the components the project actually
ships, and every value in the report traces back to a span in a file on disk.

README section 10 and section 11 then removed the last fixture on the policy
side. The flow now reads:

    ingest -> classify -> extract -> resolve -> retrieve policy
        -> extract criteria -> Case -> match -> groundedness

The policy is no longer handed to the pipeline. It is retrieved from the
corpus by the case's own payer, medication, indication, and request date, and
its requirements are read out of its prose.

One thing stays declared input on purpose: `pa_required`. README section 3
requires it to come from a synthetic benefit trigger or explicit user input
and never from policy text, because a public policy cannot establish a live
member's benefit status. It is read from the case manifest, not inferred.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, Field

from .config import get_settings
from .criteria_extraction import (
    DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD,
    CriteriaExtractionResult,
    build_policy,
)
from .extraction import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    EXTRACTOR_VERSION,
    ExtractionIssue,
    SuppressedSpan,
    extract_evidence,
)
from .ingestion import IngestedDocument, ingest_document
from .models import (
    Case,
    CaseReadinessReport,
    Document,
    DocumentType,
    DraftGroundedness,
    Evidence,
    EvidenceLink,
    Policy,
    RequirementChecklist,
)
from .policy_corpus import DEFAULT_POLICY_DIR, PolicyDocument
from .policy_retrieval import (
    PolicyIndex,
    RetrievalResult,
    resolve_policy_document,
)

DEFAULT_CLASSIFIER_PATH = Path(get_settings().classifier_path)
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
    policy_id: str | None = Field(
        default=None,
        description=(
            "Optional assertion, not a lookup key. Retrieval (README section 10) selects the "
            "policy from the case metadata; when a packet names one, a disagreement is raised "
            "rather than resolved silently in either direction."
        ),
    )
    request_date: str | None = Field(
        default=None,
        description=(
            "ISO date the policy version must be in force on. Left unset, it is read from the "
            "PA request's own extracted date, so the version window is driven by a cited fact."
        ),
    )
    plan: str | None = None
    note: str | None = None


class DocumentClassifierLike(Protocol):
    """The slice of `classifier.DocumentClassifier` this module depends on.

    Declared as a protocol so a caller — a test, or a future service — can
    supply its own classifier without loading a pickled artifact.

    It takes an already-ingested document rather than a path. Classification
    and extraction need the same page text, and taking the path here meant
    every document was read — and every scan OCR'd — twice per run.
    """

    def classify_ingested(
        self, ingested: IngestedDocument, *, document_id: str
    ) -> tuple[Document, bool]: ...


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
    if not path.is_dir():
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
            item.provenance.document_id for item in group if item.provenance.document_id is not None
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


def ingest_documents(case_dir: Path) -> dict[str, IngestedDocument]:
    """Read every document in the packet exactly once, keyed by document ID.

    Document IDs are assigned by filename order, so they are stable across
    runs, and extraction scopes evidence IDs to their document — which is what
    makes every evidence ID in the assembled case unique.
    """
    return {
        f"D{index}": ingest_document(path)
        for index, path in enumerate(case_document_paths(case_dir), start=1)
    }


def classify_documents(
    ingested: dict[str, IngestedDocument], *, classifier: DocumentClassifierLike
) -> tuple[list[Document], list[str]]:
    """Type every ingested document, returning the ones a reviewer must confirm."""
    documents: list[Document] = []
    needs_review: list[str] = []
    for document_id, source in ingested.items():
        document, requires_review = classifier.classify_ingested(source, document_id=document_id)
        documents.append(document)
        if requires_review:
            needs_review.append(document_id)
    return documents, needs_review


def extract_documents(
    ingested: dict[str, IngestedDocument],
    *,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> tuple[list[Evidence], list[ExtractionIssue], list[SuppressedSpan]]:
    """Pull typed, cited evidence out of every ingested document."""
    evidence: list[Evidence] = []
    issues: list[ExtractionIssue] = []
    suppressed: list[SuppressedSpan] = []
    for document_id, source in ingested.items():
        result = extract_evidence(
            source, document_id=document_id, confidence_threshold=confidence_threshold
        )
        evidence.extend(result.evidence)
        issues.extend(result.issues)
        suppressed.extend(result.suppressed)
    return evidence, issues, suppressed


def assemble_case(
    case_dir: Path,
    *,
    classifier: DocumentClassifierLike,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> AssembledCase:
    """Classify and extract every document in a packet into one typed Case.

    Composed from the same three stages the workflow graph runs as separate
    nodes, so there is one implementation rather than two that can drift.
    """
    manifest = load_manifest(case_dir)
    ingested = ingest_documents(case_dir)
    documents, needs_classification_review = classify_documents(ingested, classifier=classifier)
    evidence, issues, suppressed = extract_documents(
        ingested, confidence_threshold=confidence_threshold
    )

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


@dataclass
class ResolvedPolicy:
    """The policy retrieval selected, and the requirements read out of it."""

    policy: Policy
    document: PolicyDocument
    retrieval: RetrievalResult
    extraction: CriteriaExtractionResult
    request_date: str | None
    request_date_source: str


def request_date_for(assembled: AssembledCase) -> tuple[str | None, str]:
    """Decide which date the policy version window is evaluated against.

    A payer policy is only the applicable policy for requests inside its
    version window, so this date is part of the lookup, not a formality. It is
    resolved in the order a reviewer would defend:

    1. the manifest, when the packet declares it explicitly;
    2. the date extracted from the PA request itself — a cited span in a real
       document, which is what makes the version choice auditable;
    3. nothing, in which case retrieval considers every version and refuses to
       choose if more than one is in force. An undated question must not
       silently resolve to the newest file.
    """
    if assembled.manifest.request_date:
        return assembled.manifest.request_date, "case manifest"

    request_ids = {
        document.id
        for document in assembled.case.documents
        if document.document_type is DocumentType.PA_REQUEST
    }
    dates = [
        item
        for item in assembled.case.evidence
        if item.evidence_type == "document_date"
        and item.text_value
        and item.provenance.document_id in request_ids
    ]
    if not dates:
        return None, "undeclared"
    best = max(dates, key=lambda item: (item.confidence, item.text_value or ""))
    return best.text_value, f"extracted from {best.provenance.filename} ({best.id})"


def resolve_policy(
    assembled: AssembledCase,
    *,
    index: PolicyIndex,
    criteria_confidence_threshold: float = DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD,
) -> ResolvedPolicy:
    """Retrieve the applicable policy version and structure its requirements.

    This is where README section 10 and section 11 replace the Milestone 0
    fixture. Nothing about the requirements is supplied by the case packet: the
    payer, drug, indication, and request date select a policy *version* from
    the corpus, and the criteria come out of that version's prose.

    A packet may still name a `policy_id`. That is treated as an assertion to
    check, never as the lookup key — if the packet and retrieval disagree, one
    of them is wrong about the case, and silently trusting either would hide it.
    """
    manifest = assembled.manifest
    request_date, source = request_date_for(assembled)
    document, retrieval = resolve_policy_document(
        index,
        payer=manifest.payer,
        medication=manifest.medication,
        indication=manifest.indication,
        as_of_date=request_date,
    )

    if manifest.policy_id is not None and manifest.policy_id != document.policy_id:
        raise ValueError(
            f"Case packet asserts policy {manifest.policy_id!r} but retrieval selected "
            f"{document.policy_id!r} (v{document.version}) for {manifest.payer} / "
            f"{manifest.medication} / {manifest.indication} as of {request_date or 'any date'}. "
            "Resolve the disagreement rather than evaluating the case against either."
        )

    policy, extraction = build_policy(document, confidence_threshold=criteria_confidence_threshold)
    return ResolvedPolicy(
        policy=policy,
        document=document,
        retrieval=retrieval,
        extraction=extraction,
        request_date=request_date,
        request_date_source=source,
    )


def run_case(
    case_dir: Path,
    *,
    classifier: DocumentClassifierLike,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    index: PolicyIndex | None = None,
    policy_dir: Path = DEFAULT_POLICY_DIR,
) -> tuple[CaseReadinessReport, AssembledCase, ResolvedPolicy]:
    """Run one packet end to end and return the three Milestone 0 artifacts.

    The work happens in `workflow.run_workflow` (README section 13); this stays
    as the narrow, long-standing entry point that raises on failure. Callers
    that want the per-node record — which stage failed, what each stage
    produced, which versions it used — should call `run_case_workflow`
    directly and read `WorkflowResult`.

    Imported inside the function because `workflow` imports this module for its
    node implementations, and there is no cycle as long as the dependency runs
    one way at import time.
    """
    from .workflow import run_case_workflow

    result = run_case_workflow(
        case_dir,
        classifier=classifier,
        confidence_threshold=confidence_threshold,
        index=index,
        policy_dir=policy_dir,
    )
    if result.error is not None:
        raise result.error
    state = result.state
    assert state.report is not None and state.assembled is not None and state.resolved is not None
    return state.report, state.assembled, state.resolved


def build_output(
    report: CaseReadinessReport,
    assembled: AssembledCase,
    resolved: ResolvedPolicy,
    case_dir: Path,
    *,
    workflow_records: list[dict[str, object]] | None = None,
    checklist: RequirementChecklist | None = None,
    draft_groundedness: DraftGroundedness | None = None,
) -> dict[str, object]:
    """Assemble the committed JSON record of one run.

    The workflow section carries no timings on purpose: this file is committed
    as evidence and gated by `rxauth-check-reports`, so a duration would make
    it differ on every run for reasons that say nothing about correctness.
    """
    output: dict[str, object] = {
        "readiness": report.model_dump(mode="json"),
        "policy": {
            "selected": resolved.document.key,
            "request_date": resolved.request_date,
            "request_date_source": resolved.request_date_source,
            "retrieval": resolved.retrieval.as_dict(),
            "criteria_extractor_version": resolved.extraction.extractor_version,
            "criteria_connective": resolved.extraction.connective,
            "criteria": [
                criterion.model_dump(mode="json") for criterion in resolved.policy.criteria
            ],
            "exclusions_not_evaluated": [
                exclusion.model_dump(mode="json") for exclusion in resolved.policy.exclusions
            ],
            "criteria_issues": [
                issue.model_dump(mode="json") for issue in resolved.extraction.issues
            ],
        },
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
    if workflow_records is not None:
        # Lazy: `workflow` imports this module for its node implementations.
        from .workflow import WORKFLOW_VERSION

        output["workflow"] = {"version": WORKFLOW_VERSION, "nodes": workflow_records}
    if checklist is not None:
        output["checklist"] = checklist.model_dump(mode="json")
    if draft_groundedness is not None:
        output["draft_groundedness"] = draft_groundedness.model_dump(mode="json")
    return output


def print_policy_summary(resolved: ResolvedPolicy) -> None:
    line = "-" * 56
    document = resolved.document
    print("Policy retrieval")
    print(line)
    print(f"Selected:        {document.policy_id} v{document.version} ({document.filename})")
    print(f"Effective:       {document.effective_date} -> {document.superseded_date or 'current'}")
    print(
        f"Request date:    {resolved.request_date or 'undeclared'} [{resolved.request_date_source}]"
    )
    print(f"Metadata filter: {resolved.retrieval.query.describe_filter()}")
    print(f"Embedding model: {resolved.retrieval.embedding_model}")
    print(f"Candidates kept: {', '.join(resolved.retrieval.candidate_policies)}")
    print(f"Criteria:        {len(resolved.policy.criteria)} ({resolved.extraction.connective})")
    print()

    print("Top-ranked policy passages")
    print(line)
    for retrieved in resolved.retrieval.chunks:
        print(f"[{retrieved.rank}] {retrieved.score:.3f} {retrieved.chunk.citation}")
        print(f'      "{retrieved.chunk.text}"')
    print()

    if resolved.policy.exclusions:
        print("Policy exclusions parsed but NOT evaluated")
        print(line)
        for exclusion in resolved.policy.exclusions:
            print(f'{exclusion.id:4} "{exclusion.description}"')
        print()

    if resolved.extraction.issues:
        print("Policy requirements routed to human review")
        print(line)
        for issue in resolved.extraction.issues:
            print(f"{issue.criterion_id:4} {issue.kind.value:26} {issue.reason}")
        print()


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
                f"               -> {provenance.filename} p.{provenance.page} "
                f'"{provenance.source_text}"'
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


_CLAIM_ICON = {
    "grounded": "[ok ]",
    "partially_grounded": "[par]",
    "requires_review": "[hum]",
    "unsupported": "[!! ]",
    "conflicting": "[!! ]",
}


def print_checklist(checklist: RequirementChecklist, gate: DraftGroundedness) -> None:
    """Print the drafted checklist beside the gate's verdict on each sentence.

    The verdict is printed next to the sentence rather than summarized at the
    end, because "which of these sentences can I trust" is the question a
    reviewer actually has.
    """
    line = "-" * 56
    print("Drafted requirement checklist")
    print(line)
    print(f"Generator:       {checklist.generator_version}")
    print(f"Prompt version:  {checklist.prompt_version or 'n/a (deterministic)'}")
    print(f"Groundedness:    {gate.status}")
    print()

    verdicts = {item.criterion_id: item for item in gate.assessments}
    for claim in checklist.claims:
        verdict = verdicts.get(claim.criterion_id)
        status = verdict.status.value if verdict else "unassessed"
        print(f"{_CLAIM_ICON.get(status, '[?  ]')} {claim.criterion_id}  {claim.text}")
        print(f"       support:   {', '.join(claim.evidence_ids) or 'none cited'}")
        if verdict is not None:
            print(f"       gate:      {status} — {verdict.reason}")
        print()

    if gate.issues:
        print("Claims the gate refused")
        print(line)
        for issue in gate.issues:
            print(f"  {issue}")
        print()

    print("This checklist is a draft for a reviewer. Nothing has been submitted.")
    print()


def print_workflow_trace(records) -> None:
    line = "-" * 56
    print("Workflow trace")
    print(line)
    for record in records:
        versions = " ".join(f"{key}={value}" for key, value in record.versions.items())
        print(f"{record.status.value:8} {record.name:32} {record.summary}")
        if versions:
            print(f"{'':8} {'':32} [{versions}]")
        if record.error:
            print(f"{'':8} {'':32} {record.error_type}: {record.error}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one document packet end to end: ingest, classify, extract, evaluate."
    )
    parser.add_argument("case_dir", type=Path, help="Directory holding case.json and documents.")
    parser.add_argument("--classifier-path", type=Path, default=DEFAULT_CLASSIFIER_PATH)
    parser.add_argument("--policy-dir", type=Path, default=DEFAULT_POLICY_DIR)
    parser.add_argument("--output-dir", type=Path, default=get_settings().reports_dir)
    parser.add_argument("--confidence-threshold", type=float, default=DEFAULT_CONFIDENCE_THRESHOLD)
    parser.add_argument("--json-only", action="store_true", help="Print only the JSON output.")
    args = parser.parse_args()

    if not 0.0 <= args.confidence_threshold <= 1.0:
        parser.error("--confidence-threshold must be between 0 and 1.")

    from .workflow import run_case_workflow

    classifier = load_classifier(args.classifier_path)
    result = run_case_workflow(
        args.case_dir,
        classifier=classifier,
        confidence_threshold=args.confidence_threshold,
        policy_dir=args.policy_dir,
    )
    if result.error is not None:
        raise result.error

    state = result.state
    report, assembled, resolved = state.report, state.assembled, state.resolved
    assert report is not None and assembled is not None and resolved is not None
    output = build_output(
        report,
        assembled,
        resolved,
        args.case_dir,
        workflow_records=result.record_dicts(),
        checklist=state.checklist,
        draft_groundedness=state.draft_groundedness,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / f"case_{report.case_id}.json"
    out_path.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8", newline="\n")

    if args.json_only:
        print(json.dumps(output, indent=2))
        return

    from .cli import print_report

    print_report(report, report.evaluations)
    print_policy_summary(resolved)
    print_extraction_summary(assembled)
    if state.checklist is not None and state.draft_groundedness is not None:
        print_checklist(state.checklist, state.draft_groundedness)
    print_workflow_trace(result.records)
    print(f"Structured output written to: {out_path}")


if __name__ == "__main__":
    main()
