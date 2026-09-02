"""Tests for the explicit case workflow graph (README section 13)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rxauth_ai.case_assembly import run_case
from rxauth_ai.models import Document, DocumentType
from rxauth_ai.policy_retrieval import build_index
from rxauth_ai.workflow import (
    NODES,
    Node,
    NodeStatus,
    WorkflowState,
    run_case_workflow,
    run_workflow,
)

_ROOT = Path(__file__).resolve().parents[1]
_CASE_DIR = _ROOT / "data" / "cases" / "PA-CASE-001"
_POLICY_DIR = _ROOT / "data" / "policies"


@pytest.fixture(scope="module")
def policy_index():
    return build_index(_POLICY_DIR)


class _FilenameClassifier:
    """Types a document from its filename, so tests need no build artifact."""

    def classify_ingested(self, ingested, *, document_id: str) -> tuple[Document, bool]:
        stem = Path(ingested.filename).stem
        label = next(
            (document_type for document_type in DocumentType if document_type.value in stem),
            DocumentType.OTHER,
        )
        return (
            Document(
                id=document_id,
                filename=ingested.filename,
                document_type=label,
                classification_confidence=0.95,
            ),
            False,
        )


def _run(policy_index, **updates):
    return run_case_workflow(
        _CASE_DIR, classifier=_FilenameClassifier(), index=policy_index, **updates
    )


def test_a_successful_run_records_every_node_in_order(policy_index):
    result = _run(policy_index)

    assert not result.failed
    assert [record.name for record in result.records] == [node.name for node in NODES]
    assert {record.status for record in result.records} == {NodeStatus.OK}


def test_the_run_produces_a_report_a_checklist_and_a_draft_verdict(policy_index):
    state = _run(policy_index).state

    assert state.report is not None
    assert state.checklist is not None
    assert state.draft_groundedness is not None
    assert state.draft_groundedness.passed, state.draft_groundedness.issues


def test_each_document_is_read_exactly_once(policy_index):
    """Classification and extraction share one ingestion, so a scan is OCR'd once."""
    state = _run(policy_index).state

    assert len(state.ingested) == len(state.documents)
    assert set(state.ingested) == {document.id for document in state.documents}


def test_nodes_record_the_component_versions_their_output_depends_on(policy_index):
    records = {record.name: record for record in _run(policy_index).records}

    assert records["extract_case_evidence"].versions["extractor"]
    assert records["evaluate_criteria"].versions["matcher"]
    assert records["retrieve_policy"].versions["embedding"]
    assert records["generate_requirement_checklist"].versions["generator"]


def test_a_failing_node_stops_the_run_and_marks_the_rest_never_run(policy_index):
    def explode(state: WorkflowState) -> str:
        raise RuntimeError("policy service unreachable")

    nodes = (NODES[0], Node("retrieve_policy", explode), NODES[-1])
    state = WorkflowState(
        case_dir=_CASE_DIR, classifier=_FilenameClassifier(), policy_index=policy_index
    )

    result = run_workflow(state, nodes=nodes)

    assert result.failed
    assert result.failed_node == "retrieve_policy"
    assert result.error is not None and "unreachable" in str(result.error)
    statuses = {record.name: record.status for record in result.records}
    assert statuses["validate_case"] is NodeStatus.OK
    assert statuses["retrieve_policy"] is NodeStatus.FAILED
    assert statuses["await_human_review"] is NodeStatus.NOT_RUN


def test_a_failure_records_the_exception_type_not_just_a_message(policy_index):
    def explode(state: WorkflowState) -> str:
        raise FileNotFoundError("classifier artifact missing")

    state = WorkflowState(
        case_dir=_CASE_DIR, classifier=_FilenameClassifier(), policy_index=policy_index
    )
    result = run_workflow(state, nodes=(Node("boom", explode),))

    record = result.records[0]
    assert record.error_type == "FileNotFoundError"
    assert "classifier artifact missing" in (record.error or "")


def test_run_workflow_does_not_raise_so_a_partial_run_stays_inspectable(policy_index):
    """A failure is an outcome the result describes, not an exception to chase."""

    def explode(state: WorkflowState) -> str:
        raise ValueError("nope")

    state = WorkflowState(
        case_dir=_CASE_DIR, classifier=_FilenameClassifier(), policy_index=policy_index
    )
    result = run_workflow(state, nodes=(Node("boom", explode),))

    assert result.failed
    assert isinstance(result.error, ValueError)


def test_a_retryable_node_is_retried_and_the_attempts_are_recorded(policy_index):
    attempts = {"count": 0}

    def flaky(state: WorkflowState) -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("transient")
        return "recovered"

    state = WorkflowState(
        case_dir=_CASE_DIR, classifier=_FilenameClassifier(), policy_index=policy_index
    )
    result = run_workflow(state, nodes=(Node("flaky", flaky, retries=2),))

    assert not result.failed
    assert result.records[0].attempts == 3


def test_no_shipped_node_is_retryable():
    """Every node is deterministic and offline, so a retry could only repeat itself.

    The mechanism exists for the first node that calls a network. This test is
    the reminder to justify that node when it arrives.
    """
    assert [node.name for node in NODES if node.retries] == []


def test_run_case_still_raises_so_its_long_standing_contract_holds(tmp_path, policy_index):
    """`run_case` predates the graph and callers rely on it raising."""
    (tmp_path / "case.json").write_text(
        json.dumps(
            {
                "case_id": "PA-BAD",
                "patient_synthetic_id": "SYN-1",
                "payer": "Nonexistent Plan",
                "medication": "Drug A",
                "indication": "Example Condition",
                "pa_required": True,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "01_pa_request.txt").write_text("Diagnosis: Example Condition", encoding="utf-8")

    with pytest.raises(Exception) as caught:
        run_case(tmp_path, classifier=_FilenameClassifier(), index=policy_index)

    assert caught.value is not None


def test_the_graph_ends_by_naming_what_a_person_still_has_to_do(policy_index):
    """The terminal node must never read as 'the case was filed'."""
    records = {record.name: record for record in _run(policy_index).records}

    summary = records["await_human_review"].summary
    assert "Nothing is submitted" in summary
    assert "need a person" in summary


def test_the_pa_trigger_node_says_the_value_was_declared_not_inferred(policy_index):
    records = {record.name: record for record in _run(policy_index).records}

    assert "never inferred" in records["resolve_pa_trigger"].summary


def test_node_records_carry_no_wall_clock_timing(policy_index):
    """They are committed to reports/ and gated, so they must reproduce exactly."""
    for record in _run(policy_index).records:
        fields = record.model_dump()
        assert not [key for key in fields if any(word in key for word in ("time", "latency", "ms"))]
