"""Tests for structured logging and its PHI guarantee (README section 18/19)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from rxauth_ai.config import settings_from_env
from rxauth_ai.models import Document, DocumentType, Provenance
from rxauth_ai.observability import (
    LOG_SCHEMA_VERSION,
    LOGGABLE_FIELDS,
    LOGGER_NAME,
    RunContext,
    configure_logging,
    log_event,
    loggable_provenance,
    timed,
)
from rxauth_ai.policy_retrieval import build_index
from rxauth_ai.workflow import run_case_workflow

_ROOT = Path(__file__).resolve().parents[1]
_CASE_DIR = _ROOT / "data" / "cases" / "PA-CASE-001"
_POLICY_DIR = _ROOT / "data" / "policies"


class _Capture(logging.Handler):
    """Collects records so a test can inspect exactly what would be emitted."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    @property
    def fields(self) -> list[dict]:
        return [getattr(record, "rxauth_fields", {}) for record in self.records]


@pytest.fixture
def captured():
    logger = logging.getLogger(LOGGER_NAME)
    handler = _Capture()
    previous = list(logger.handlers)
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    yield handler
    logger.handlers = previous


class _FilenameClassifier:
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


def test_an_event_carries_its_structured_fields(captured):
    log_event("workflow.node", workflow_node="retrieve_policy", case_id="PA-1", latency_ms=1.5)

    assert captured.records[0].getMessage() == "workflow.node"
    assert captured.fields[0]["workflow_node"] == "retrieve_policy"
    assert captured.fields[0]["latency_ms"] == 1.5


def test_a_field_outside_the_allow_list_is_dropped_and_the_drop_is_recorded(captured):
    """Fail closed: an unknown field is more likely PHI than not."""
    log_event("test", case_id="PA-1", source_text="Patient has Example Condition")

    fields = captured.fields[0]
    assert "source_text" not in fields
    assert "source_text" in fields["error"]
    assert fields["case_id"] == "PA-1"


def test_the_json_formatter_emits_one_object_per_line():
    settings = settings_from_env(log_format="json")
    logger = configure_logging(settings)
    formatter = logger.handlers[0].formatter
    record = logging.LogRecord(LOGGER_NAME, logging.INFO, __file__, 1, "workflow.node", (), None)
    record.rxauth_fields = {"case_id": "PA-1", "latency_ms": 2.0}

    payload = json.loads(formatter.format(record))

    assert payload["event"] == "workflow.node"
    assert payload["schema_version"] == LOG_SCHEMA_VERSION
    assert payload["case_id"] == "PA-1"


def test_a_run_context_correlates_every_line_of_one_run():
    context = RunContext(case_id="PA-CASE-001")

    assert len(context.request_id) == 32
    assert context.fields()["case_id"] == "PA-CASE-001"
    assert RunContext().request_id != context.request_id


def test_a_citation_is_reduced_to_the_parts_that_locate_it():
    """Enough to open the span; never the span itself."""
    provenance = Provenance(
        document_id="D1",
        filename="03_clinical_note.txt",
        page=1,
        start_char=74,
        end_char=103,
        source_text="Assessment: Example Condition",
    )

    loggable = loggable_provenance(provenance)

    assert loggable == {
        "document_id": "D1",
        "filename": "03_clinical_note.txt",
        "page": 1,
        "start_char": 74,
        "end_char": 103,
    }
    assert "source_text" not in loggable


def test_timed_logs_a_latency_even_when_the_block_raises(captured):
    with pytest.raises(ValueError):
        with timed("workflow.node", workflow_node="retrieve_policy"):
            raise ValueError("boom")

    fields = captured.fields[0]
    assert fields["node_status"] == "failed"
    assert fields["error_type"] == "ValueError"
    assert fields["latency_ms"] >= 0


def test_timed_lets_the_block_add_fields_it_only_learns_while_running(captured):
    with timed("workflow.node", workflow_node="evaluate_criteria") as extra:
        extra["evaluation_result"] = "SATISFIED"

    assert captured.fields[0]["evaluation_result"] == "SATISFIED"


# --- The guarantee -----------------------------------------------------------


def test_a_whole_case_run_emits_no_patient_text(captured):
    """The PHI-safe convention, enforced against a real run rather than asserted.

    Every quoted span in the case packet is checked against every field of
    every log line. An allow-list guards structured fields; this is what
    catches text smuggled in some other way.
    """
    result = run_case_workflow(
        _CASE_DIR, classifier=_FilenameClassifier(), index=build_index(_POLICY_DIR)
    )
    assert not result.failed
    assert captured.records, "the run should have logged something"

    quoted = {
        source.source_text
        for item in result.state.evidence
        for source in item.sources
        if source.source_text
    }
    assert quoted, "the case packet should contain quoted spans"

    emitted = json.dumps(captured.fields, default=str) + " ".join(
        record.getMessage() for record in captured.records
    )
    leaked = sorted(span for span in quoted if span in emitted)
    assert not leaked, f"patient text reached the log: {leaked}"


def test_every_node_logs_its_stage_latency_and_correlation_ids(captured):
    result = run_case_workflow(
        _CASE_DIR, classifier=_FilenameClassifier(), index=build_index(_POLICY_DIR)
    )

    node_lines = [
        fields
        for record, fields in zip(captured.records, captured.fields, strict=True)
        if record.getMessage() == "workflow.node"
    ]

    assert len(node_lines) == len(result.records)
    assert {line["workflow_node"] for line in node_lines} == {
        record.name for record in result.records
    }
    for line in node_lines:
        assert line["request_id"]
        assert "latency_ms" in line


def test_component_versions_reach_the_log_under_their_section_18_names(captured):
    run_case_workflow(_CASE_DIR, classifier=_FilenameClassifier(), index=build_index(_POLICY_DIR))

    merged = {key: value for fields in captured.fields for key, value in fields.items()}

    assert merged["extractor_version"] == "regex-v3"
    assert merged["matcher_version"] == "evidence-match-v2"
    assert merged["embedding_model"] == "tfidf-v1"


def test_the_allow_list_covers_the_section_18_schema():
    """If a field named in README §18 is not loggable, it is silently missing."""
    required = {
        "request_id",
        "case_id",
        "workflow_node",
        "prompt_version",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "estimated_cost_usd",
        "retrieved_document_ids",
        "evaluation_result",
        "error_type",
    }

    assert required <= LOGGABLE_FIELDS
