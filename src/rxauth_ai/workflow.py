"""The case workflow as an explicit state graph (README section 13).

Before this module, the end-to-end run was a function that called seven other
functions. That works, and it hides three things a reviewer of a clinical
decision-support system is entitled to see: which stage a run got to, which
stage failed and why, and which model or extractor version produced each part
of the answer. A stack trace answers the second question badly and the other
two not at all.

So the flow is declared as a list of named nodes with typed state, and every
node records how it ended. The graph is deliberately linear and has no
branches, loops, or autonomous decisions — README section 13 chose that over a
service-fragmented design, and nothing here reopens it.

**Why this is not LangGraph.** README section 13 names LangGraph, and this is
a hand-written executor instead. LangGraph earns its weight when a graph
orchestrates model calls with retries, branching, and streamed partial state;
this graph runs offline, deterministic, dependency-light Python and makes zero
model calls. Pulling in a framework to run thirteen functions in order would
add a large dependency tree to a package whose CI lightness is a stated goal,
and would not make any node more auditable. What matters — the decomposition,
the typed state, the per-node record, the explicit failure state — lives in
the node definitions, not in the runtime, so swapping this executor for a
LangGraph `StateGraph` later is an adapter rather than a rewrite. See
`docs/adr-001-workflow-runtime.md`.

**On retries.** `Node.retries` exists and every node sets it to zero. Every
current node is deterministic, offline, and free of transient failure modes,
so a retry could only ever repeat the same failure and slow the run down. The
mechanism is here because the moment a node calls a network — a model API, a
policy fetch, an eligibility check — that node and only that node becomes
retryable, and the decision belongs next to the node rather than in a wrapper
written under time pressure later.

**On timings.** Node records deliberately carry no wall-clock duration. They
are serialized into `reports/case_<id>.json`, which is committed as evidence
and gated by `rxauth-check-reports`; a duration would make that report differ
on every run. Latency belongs in the structured logs README section 18
specifies, which arrive with the service layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Optional

from pydantic import BaseModel, Field

from .case_assembly import (
    AssembledCase,
    CaseManifest,
    DocumentClassifierLike,
    ResolvedPolicy,
    case_document_paths,
    classify_documents,
    extract_documents,
    ingest_documents,
    link_cross_document_evidence,
    load_manifest,
    request_date_for,
)
from .criteria_extraction import DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD, build_policy
from .extraction import DEFAULT_CONFIDENCE_THRESHOLD, ExtractionIssue, SuppressedSpan
from .generation import DraftGenerator, generate_checklist
from .groundedness import check_draft_groundedness
from .ingestion import IngestedDocument
from .matching import AmbiguityInterpreter
from .models import (
    Case,
    CaseReadinessReport,
    Document,
    DraftGroundedness,
    Evidence,
    EvidenceLink,
    RequirementChecklist,
)
from .observability import RunContext, log_event
from .pipeline import run_pipeline
from .policy_corpus import DEFAULT_POLICY_DIR, PolicyDocument
from .policy_retrieval import PolicyIndex, RetrievalResult, build_index, resolve_policy_document

WORKFLOW_VERSION = "case-graph-v1"


class NodeStatus(str, Enum):
    """How one node ended.

    `NOT_RUN` is a real outcome, not a placeholder: when a node fails, the
    nodes after it are recorded as never having run, so a partial result
    cannot be mistaken for a complete one.
    """

    OK = "ok"
    FAILED = "failed"
    NOT_RUN = "not_run"


class NodeRecord(BaseModel):
    """What one node did, kept whether it succeeded or not."""

    name: str
    status: NodeStatus
    attempts: int = 1
    summary: str = ""
    versions: dict[str, str] = Field(
        default_factory=dict,
        description="Component versions this node's output depends on.",
    )
    error_type: Optional[str] = None
    error: Optional[str] = None


@dataclass
class WorkflowState:
    """Everything the graph carries, in the order the nodes fill it in."""

    case_dir: Path
    classifier: DocumentClassifierLike
    policy_index: PolicyIndex
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    criteria_confidence_threshold: float = DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD
    interpreter: Optional[AmbiguityInterpreter] = None
    generator: Optional[DraftGenerator] = None
    context: RunContext = field(default_factory=RunContext)

    manifest: Optional[CaseManifest] = None
    document_paths: list[Path] = field(default_factory=list)
    ingested: dict[str, IngestedDocument] = field(default_factory=dict)
    documents: list[Document] = field(default_factory=list)
    documents_requiring_review: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    extraction_issues: list[ExtractionIssue] = field(default_factory=list)
    suppressed_spans: list[SuppressedSpan] = field(default_factory=list)
    evidence_links: list[EvidenceLink] = field(default_factory=list)
    case: Optional[Case] = None
    assembled: Optional[AssembledCase] = None
    request_date: Optional[str] = None
    request_date_source: str = "undeclared"
    policy_document: Optional[PolicyDocument] = None
    retrieval: Optional[RetrievalResult] = None
    resolved: Optional[ResolvedPolicy] = None
    report: Optional[CaseReadinessReport] = None
    checklist: Optional[RequirementChecklist] = None
    draft_groundedness: Optional[DraftGroundedness] = None


@dataclass(frozen=True)
class Node:
    """One stage: a name, the work, and how often it may safely be retried."""

    name: str
    run: Callable[[WorkflowState], str]
    retries: int = 0


@dataclass
class WorkflowResult:
    """The outcome of a whole run, complete or not."""

    state: WorkflowState
    records: list[NodeRecord]
    error: Optional[BaseException] = None

    @property
    def failed(self) -> bool:
        return self.error is not None

    @property
    def failed_node(self) -> Optional[str]:
        return next(
            (record.name for record in self.records if record.status is NodeStatus.FAILED), None
        )

    def record_dicts(self) -> list[dict[str, object]]:
        return [record.model_dump(mode="json") for record in self.records]


# --- Nodes -----------------------------------------------------------------


def _validate_case(state: WorkflowState) -> str:
    state.manifest = load_manifest(state.case_dir)
    state.document_paths = case_document_paths(state.case_dir)
    # Every line after this one correlates to the case, not just the request.
    state.context.case_id = state.manifest.case_id
    return (
        f"{state.manifest.case_id}: {len(state.document_paths)} document(s) for "
        f"{state.manifest.payer} / {state.manifest.medication}"
    )


def _resolve_pa_trigger(state: WorkflowState) -> str:
    """Read the PA trigger. It is declared input and stays that way.

    README section 3 forbids inferring a live benefit from policy text, so this
    node has nothing to compute. It exists to make the boundary visible: the
    one value the system does not derive gets a node of its own, and anyone
    adding inference here has to delete this docstring first.
    """
    assert state.manifest is not None
    return f"pa_required={state.manifest.pa_required} (declared in case.json, never inferred)"


def _ingest_documents(state: WorkflowState) -> str:
    state.ingested = ingest_documents(state.case_dir)
    methods = sorted(
        {page.extraction_method for doc in state.ingested.values() for page in doc.pages}
    )
    return f"{len(state.ingested)} document(s) read once via {', '.join(methods)}"


def _classify_documents(state: WorkflowState) -> str:
    state.documents, state.documents_requiring_review = classify_documents(
        state.ingested, classifier=state.classifier
    )
    return (
        f"{len(state.documents)} classified, "
        f"{len(state.documents_requiring_review)} below threshold"
    )


def _extract_case_evidence(state: WorkflowState) -> str:
    state.evidence, state.extraction_issues, state.suppressed_spans = extract_documents(
        state.ingested, confidence_threshold=state.confidence_threshold
    )
    return (
        f"{len(state.evidence)} cited fact(s), {len(state.extraction_issues)} routed to review, "
        f"{len(state.suppressed_spans)} span(s) suppressed"
    )


def _link_cross_document_evidence(state: WorkflowState) -> str:
    assert state.manifest is not None
    state.evidence_links = link_cross_document_evidence(state.evidence)
    state.case = Case(
        id=state.manifest.case_id,
        patient_synthetic_id=state.manifest.patient_synthetic_id,
        payer=state.manifest.payer,
        plan=state.manifest.plan,
        medication=state.manifest.medication,
        indication=state.manifest.indication,
        pa_required=state.manifest.pa_required,
        documents=state.documents,
        evidence=state.evidence,
    )
    state.assembled = AssembledCase(
        case=state.case,
        manifest=state.manifest,
        documents_requiring_review=state.documents_requiring_review,
        extraction_issues=state.extraction_issues,
        suppressed_spans=state.suppressed_spans,
        evidence_links=state.evidence_links,
    )
    return f"{len(state.evidence_links)} fact(s) corroborated across documents"


def _resolve_request_date(state: WorkflowState) -> str:
    assert state.assembled is not None
    state.request_date, state.request_date_source = request_date_for(state.assembled)
    return f"{state.request_date or 'undeclared'} [{state.request_date_source}]"


def _retrieve_policy(state: WorkflowState) -> str:
    assert state.manifest is not None
    manifest = state.manifest
    state.policy_document, state.retrieval = resolve_policy_document(
        state.policy_index,
        payer=manifest.payer,
        medication=manifest.medication,
        indication=manifest.indication,
        as_of_date=state.request_date,
    )
    if manifest.policy_id is not None and manifest.policy_id != state.policy_document.policy_id:
        raise ValueError(
            f"Case packet asserts policy {manifest.policy_id!r} but retrieval selected "
            f"{state.policy_document.policy_id!r} (v{state.policy_document.version}) for "
            f"{manifest.payer} / {manifest.medication} / {manifest.indication} as of "
            f"{state.request_date or 'any date'}. Resolve the disagreement rather than "
            "evaluating the case against either."
        )
    return (
        f"{state.policy_document.policy_id} v{state.policy_document.version} "
        f"via {state.retrieval.query.describe_filter()}"
    )


def _extract_policy_criteria(state: WorkflowState) -> str:
    assert state.policy_document is not None and state.retrieval is not None
    policy, extraction = build_policy(
        state.policy_document, confidence_threshold=state.criteria_confidence_threshold
    )
    state.resolved = ResolvedPolicy(
        policy=policy,
        document=state.policy_document,
        retrieval=state.retrieval,
        extraction=extraction,
        request_date=state.request_date,
        request_date_source=state.request_date_source,
    )
    return (
        f"{len(policy.criteria)} criterion/criteria ({extraction.connective}), "
        f"{len(policy.exclusions)} exclusion(s) not evaluated"
    )


def _evaluate_criteria(state: WorkflowState) -> str:
    """Match evidence to requirements and run the structural citation gate.

    README section 13 lists normalization, deterministic evaluation,
    model-assisted interpretation of ambiguity, and missing-evidence detection
    as four nodes. They are one node here because they are one decision per
    criterion: `matching.evaluate_criterion` retrieves, normalizes, compares,
    offers genuinely incomplete prose to the interpreter, and returns `MISSING`
    when nothing relevant was retrieved. Splitting them would mean four nodes
    that pass a half-formed verdict between them, which is less auditable than
    one node with a `decision_trace` on every evaluation — and the trace is
    already recorded per criterion, so nothing is lost.
    """
    assert state.case is not None and state.resolved is not None
    state.report = run_pipeline(
        state.case,
        state.resolved.policy,
        evidence_requiring_review=len(state.extraction_issues),
        documents_requiring_classification_review=len(state.documents_requiring_review),
        interpreter=state.interpreter,
    )
    return f"{state.report.summary_line()}; gate {state.report.groundedness_gate}"


def _generate_requirement_checklist(state: WorkflowState) -> str:
    assert state.report is not None and state.case is not None and state.resolved is not None
    state.checklist = generate_checklist(
        state.report, state.case, state.resolved.policy, generator=state.generator
    )
    return f"{len(state.checklist.claims)} claim(s) drafted by {state.checklist.generator_version}"


def _check_draft_groundedness(state: WorkflowState) -> str:
    assert state.checklist is not None and state.report is not None and state.case is not None
    state.draft_groundedness = check_draft_groundedness(
        state.checklist, state.report.evaluations, state.case
    )
    gate = state.draft_groundedness
    return f"{gate.status}; {len(gate.issues)} ungrounded claim(s)"


def _await_human_review(state: WorkflowState) -> str:
    """The terminal node. It does not submit anything, and never will.

    README section 20 puts autonomous submission permanently out of scope. The
    graph ends by naming what a person still has to do, so that "the workflow
    completed" can never be read as "the case was filed".
    """
    assert state.report is not None
    report = state.report
    outstanding = (
        report.criteria_needs_review
        + report.criteria_missing
        + report.criteria_not_satisfied
        + report.documents_requiring_classification_review
        + report.evidence_requiring_review
        + report.policy_exclusions_not_evaluated
    )
    return f"prepared for review; {outstanding} item(s) need a person. Nothing is submitted."


#: The graph. Order is the contract; there are no branches and no loops.
NODES: tuple[Node, ...] = (
    Node("validate_case", _validate_case),
    Node("resolve_pa_trigger", _resolve_pa_trigger),
    Node("ingest_documents", _ingest_documents),
    Node("classify_documents", _classify_documents),
    Node("extract_case_evidence", _extract_case_evidence),
    Node("link_cross_document_evidence", _link_cross_document_evidence),
    Node("resolve_request_date", _resolve_request_date),
    Node("retrieve_policy", _retrieve_policy),
    Node("extract_policy_criteria", _extract_policy_criteria),
    Node("evaluate_criteria", _evaluate_criteria),
    Node("generate_requirement_checklist", _generate_requirement_checklist),
    Node("check_draft_groundedness", _check_draft_groundedness),
    Node("await_human_review", _await_human_review),
)


def _versions_for(name: str, state: WorkflowState) -> dict[str, str]:
    """Component versions the node's output depends on.

    Recorded per node so that "which extractor produced this evidence" is
    answerable from the report rather than from the commit that produced it.
    """
    from .extraction import EXTRACTOR_VERSION
    from .matching import MATCHER_VERSION, NORMALIZATION_VERSION

    if name == "extract_case_evidence":
        return {"extractor": EXTRACTOR_VERSION}
    if name == "extract_policy_criteria" and state.resolved is not None:
        return {"criteria_extractor": state.resolved.extraction.extractor_version}
    if name == "retrieve_policy" and state.retrieval is not None:
        return {"embedding": state.retrieval.embedding_model}
    if name == "evaluate_criteria":
        return {"matcher": MATCHER_VERSION, "normalization": NORMALIZATION_VERSION}
    if name == "generate_requirement_checklist" and state.checklist is not None:
        versions = {"generator": state.checklist.generator_version}
        if state.checklist.prompt_version:
            versions["prompt"] = state.checklist.prompt_version
        return versions
    return {}


#: `NodeRecord.versions` uses short keys (`matcher`, `generator`); the log
#: allow-list uses the section 18 names. One table, so a new version key that
#: is not safe to log is dropped rather than guessed at.
_VERSION_LOG_KEYS = {
    "extractor": "extractor_version",
    # Its own field, not folded into `extractor_version`: the evidence
    # extractor and the criteria extractor are different components, and one
    # name for both makes a log line unable to say which it is about.
    "criteria_extractor": "criteria_extractor_version",
    "embedding": "embedding_model",
    "matcher": "matcher_version",
    "generator": "generator_version",
    "prompt": "prompt_version",
}


def _loggable_versions(versions: dict[str, str]) -> dict[str, str]:
    return {
        _VERSION_LOG_KEYS[key]: value for key, value in versions.items() if key in _VERSION_LOG_KEYS
    }


def run_workflow(state: WorkflowState, *, nodes: tuple[Node, ...] = NODES) -> WorkflowResult:
    """Run the graph, recording every node.

    This never raises for a node failure. A failure is an outcome the result
    describes — which node, which exception type, what it said, and which nodes
    consequently never ran. Callers that want an exception re-raise
    `result.error` themselves.
    """
    records: list[NodeRecord] = []
    failure: Optional[BaseException] = None

    for node in nodes:
        if failure is not None:
            records.append(NodeRecord(name=node.name, status=NodeStatus.NOT_RUN, attempts=0))
            log_event(
                "workflow.node",
                workflow_node=node.name,
                node_status=NodeStatus.NOT_RUN.value,
                workflow_version=WORKFLOW_VERSION,
                **state.context.fields(),
            )
            continue

        attempts = 0
        while True:
            attempts += 1
            started = perf_counter()
            try:
                summary = node.run(state)
            except Exception as exc:  # noqa: BLE001 - recorded, then re-raised by the caller
                if attempts <= node.retries:
                    continue
                failure = exc
                records.append(
                    NodeRecord(
                        name=node.name,
                        status=NodeStatus.FAILED,
                        attempts=attempts,
                        summary=f"failed after {attempts} attempt(s)",
                        error_type=type(exc).__name__,
                        error=str(exc),
                    )
                )
                # Latency belongs in the log, not in the committed report.
                log_event(
                    "workflow.node",
                    workflow_node=node.name,
                    node_status=NodeStatus.FAILED.value,
                    attempts=attempts,
                    latency_ms=round((perf_counter() - started) * 1000, 3),
                    error_type=type(exc).__name__,
                    error=str(exc),
                    workflow_version=WORKFLOW_VERSION,
                    **state.context.fields(),
                )
                break
            versions = _versions_for(node.name, state)
            records.append(
                NodeRecord(
                    name=node.name,
                    status=NodeStatus.OK,
                    attempts=attempts,
                    summary=summary,
                    versions=versions,
                )
            )
            log_event(
                "workflow.node",
                workflow_node=node.name,
                node_status=NodeStatus.OK.value,
                attempts=attempts,
                latency_ms=round((perf_counter() - started) * 1000, 3),
                workflow_version=WORKFLOW_VERSION,
                **_loggable_versions(versions),
                **state.context.fields(),
            )
            break

    return WorkflowResult(state=state, records=records, error=failure)


def run_case_workflow(
    case_dir: Path,
    *,
    classifier: DocumentClassifierLike,
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    criteria_confidence_threshold: float = DEFAULT_CRITERIA_CONFIDENCE_THRESHOLD,
    index: Optional[PolicyIndex] = None,
    policy_dir: Path = DEFAULT_POLICY_DIR,
    interpreter: Optional[AmbiguityInterpreter] = None,
    generator: Optional[DraftGenerator] = None,
) -> WorkflowResult:
    """Run one case packet through the whole graph."""
    state = WorkflowState(
        case_dir=Path(case_dir),
        classifier=classifier,
        policy_index=index or build_index(policy_dir),
        confidence_threshold=confidence_threshold,
        criteria_confidence_threshold=criteria_confidence_threshold,
        interpreter=interpreter,
        generator=generator,
    )
    return run_workflow(state)
