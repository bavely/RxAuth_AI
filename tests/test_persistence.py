"""Tests for relational persistence of case runs and reviewer decisions.

Runs against SQLite by default and against whatever `RXAUTH_TEST_DATABASE_URL`
names when it is set — which is how CI runs the same assertions on Postgres.
The schema is deliberately dialect-neutral so that is possible.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from rxauth_ai.feedback import ReviewerAction, decision_from_evaluation
from rxauth_ai.models import CriterionResult
from rxauth_ai.persistence import (
    CaseRunRow,
    create_all,
    load_case_run,
    load_reviewer_decisions,
    recent_case_runs,
    save_case_run,
    save_reviewer_decision,
    session_scope,
)
from rxauth_ai.persistence.tables import Base, DocumentRow

_ROOT = Path(__file__).resolve().parents[1]
_PAYLOAD = _ROOT / "reports" / "case_PA-CASE-001.json"


@pytest.fixture
def engine(tmp_path):
    url = os.environ.get("RXAUTH_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    engine = create_engine(url, future=True)
    Base.metadata.drop_all(engine)
    create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def payload():
    return json.loads(_PAYLOAD.read_text(encoding="utf-8"))


def test_a_run_round_trips_to_the_objects_the_pipeline_produced(engine, payload):
    with session_scope(engine) as session:
        run_id = save_case_run(session, payload=payload, request_id="req-1")

    with session_scope(engine) as session:
        record = load_case_run(session, run_id)

    assert record is not None
    assert record.case_id == payload["readiness"]["case_id"]
    assert record.request_id == "req-1"
    # The report is a real CaseReadinessReport, not a dict that looks like one.
    assert record.report.criteria_total == payload["readiness"]["criteria_total"]
    assert record.report.summary_line()


def test_the_stored_payload_is_byte_identical_to_what_was_written(engine, payload):
    """What the API returns and what `reports/` holds must be the same bytes."""
    with session_scope(engine) as session:
        run_id = save_case_run(session, payload=payload, request_id="req-1")

    with session_scope(engine) as session:
        record = load_case_run(session, run_id)

    assert record.payload == payload


def test_every_cited_span_survives_the_round_trip(engine, payload):
    """A stored evaluation without its citations would defeat the whole gate."""
    with session_scope(engine) as session:
        run_id = save_case_run(session, payload=payload, request_id="req-1")

    with session_scope(engine) as session:
        record = load_case_run(session, run_id)

    supported = [
        evaluation
        for evaluation in record.evaluations
        if evaluation.result is CriterionResult.SATISFIED
    ]
    assert supported
    for evaluation in supported:
        assert evaluation.patient_evidence_sources
        assert all(source.source_text for source in evaluation.patient_evidence_sources)
        assert evaluation.policy_source is not None


def test_versions_are_columns_so_a_bump_can_be_compared(engine, payload):
    with session_scope(engine) as session:
        run_id = save_case_run(session, payload=payload, request_id="req-1")
        row = session.get(CaseRunRow, run_id)

        assert row.matcher_version == payload["readiness"]["matcher_version"]
        assert row.workflow_version == payload["workflow"]["version"]
        assert row.extractor_version == "regex-v3"
        assert row.generator_version == "checklist-v1"


def test_each_criterion_is_queryable_without_opening_the_payload(engine, payload):
    with session_scope(engine) as session:
        run_id = save_case_run(session, payload=payload, request_id="req-1")
        row = session.get(CaseRunRow, run_id)

        assert len(row.evaluations) == payload["readiness"]["criteria_total"]
        assert {item.result for item in row.evaluations} <= {
            result.value for result in CriterionResult
        }


def test_running_the_same_case_twice_keeps_both_runs(engine, payload):
    """Overwriting would destroy the comparison a version bump depends on."""
    with session_scope(engine) as session:
        first = save_case_run(session, payload=payload, request_id="req-1")
        second = save_case_run(session, payload=payload, request_id="req-2")

    assert first != second
    with session_scope(engine) as session:
        runs = recent_case_runs(session, case_id=payload["readiness"]["case_id"])

    assert {run.run_id for run in runs} == {first, second}


def test_document_rows_record_where_the_bytes_are_never_the_bytes(engine, payload):
    keys = {"D1": "cases/PA-CASE-001/D1/01_pa_request.txt"}

    with session_scope(engine) as session:
        run_id = save_case_run(session, payload=payload, request_id="req-1", storage_keys=keys)
        row = session.get(CaseRunRow, run_id)
        documents = {item.document_id: item for item in row.documents}

        assert documents["D1"].storage_key == keys["D1"]
        assert not hasattr(DocumentRow, "content")
        assert not hasattr(DocumentRow, "text")


def test_a_failed_transaction_leaves_nothing_behind(engine, payload):
    with pytest.raises(RuntimeError):
        with session_scope(engine) as session:
            save_case_run(session, payload=payload, request_id="req-1")
            raise RuntimeError("something went wrong after the write")

    with session_scope(engine) as session:
        assert recent_case_runs(session) == []


def test_reviewer_decisions_append_and_read_back(engine, payload):
    with session_scope(engine) as session:
        run_id = save_case_run(session, payload=payload, request_id="req-1")
        record = load_case_run(session, run_id)
        evaluation = record.evaluations[0]

        accepted = decision_from_evaluation(
            evaluation, reviewer_id="reviewer-01", action=ReviewerAction.ACCEPTED
        )
        corrected = decision_from_evaluation(
            evaluation,
            reviewer_id="reviewer-02",
            action=ReviewerAction.CORRECTED,
            corrected_result=CriterionResult.HUMAN_REVIEW_REQUIRED,
            note="The two documents disagree.",
        )
        save_reviewer_decision(session, accepted, run_id=run_id)
        save_reviewer_decision(session, corrected, run_id=run_id)

    with session_scope(engine) as session:
        decisions = load_reviewer_decisions(session, case_id=record.case_id)

    assert [item.action for item in decisions] == [
        ReviewerAction.ACCEPTED,
        ReviewerAction.CORRECTED,
    ]
    assert decisions[1].corrected_result is CriterionResult.HUMAN_REVIEW_REQUIRED
    assert decisions[1].matcher_version == evaluation.matcher_version


def test_a_superseding_decision_is_another_row_not_an_edit(engine, payload):
    """Append-only: a correction that can be edited is not a record of the moment."""
    with session_scope(engine) as session:
        run_id = save_case_run(session, payload=payload, request_id="req-1")
        evaluation = load_case_run(session, run_id).evaluations[0]
        for action in (ReviewerAction.ACCEPTED, ReviewerAction.REJECTED):
            save_reviewer_decision(
                session,
                decision_from_evaluation(evaluation, reviewer_id="reviewer-01", action=action),
                run_id=run_id,
            )

    with session_scope(engine) as session:
        assert len(load_reviewer_decisions(session)) == 2
