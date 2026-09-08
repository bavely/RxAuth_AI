"""Tests for relational persistence of case runs and reviewer decisions.

Runs against SQLite by default and against whatever `RXAUTH_TEST_DATABASE_URL`
names when it is set — which is how CI runs the same assertions on Postgres.
The schema is deliberately dialect-neutral so that is possible.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select

from rxauth_ai.feedback import ReviewerAction, decision_from_evaluation
from rxauth_ai.models import CriterionResult
from rxauth_ai.persistence import (
    CaseRow,
    CaseRunRow,
    create_all,
    create_case_record,
    list_cases,
    load_case_run,
    load_reviewer_decisions,
    load_run_document,
    recent_case_runs,
    save_case_run,
    save_reviewer_decision,
    save_uploaded_document,
    session_scope,
)
from rxauth_ai.persistence.tables import Base, DocumentRow

_ROOT = Path(__file__).resolve().parents[1]
_PAYLOAD = _ROOT / "reports" / "case_PA-CASE-001.json"
_ORGANIZATION = "org-a"


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
        run_id = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )

    with session_scope(engine) as session:
        record = load_case_run(session, run_id=run_id, organization_id=_ORGANIZATION)

    assert record is not None
    assert record.organization_id == _ORGANIZATION
    assert record.case_id == payload["readiness"]["case_id"]
    assert record.request_id == "req-1"
    # The report is a real CaseReadinessReport, not a dict that looks like one.
    assert record.report.criteria_total == payload["readiness"]["criteria_total"]
    assert record.report.summary_line()


def test_the_stored_payload_is_byte_identical_to_what_was_written(engine, payload):
    """What the API returns and what `reports/` holds must be the same bytes."""
    with session_scope(engine) as session:
        run_id = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )

    with session_scope(engine) as session:
        record = load_case_run(session, run_id=run_id, organization_id=_ORGANIZATION)

    assert record.payload == payload


def test_every_cited_span_survives_the_round_trip(engine, payload):
    """A stored evaluation without its citations would defeat the whole gate."""
    with session_scope(engine) as session:
        run_id = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )

    with session_scope(engine) as session:
        record = load_case_run(session, run_id=run_id, organization_id=_ORGANIZATION)

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
        run_id = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )
        row = session.get(CaseRunRow, run_id)

        assert row.matcher_version == payload["readiness"]["matcher_version"]
        assert row.workflow_version == payload["workflow"]["version"]
        assert row.extractor_version == "regex-v3"
        assert row.generator_version == "checklist-v1"


def test_each_criterion_is_queryable_without_opening_the_payload(engine, payload):
    with session_scope(engine) as session:
        run_id = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )
        row = session.get(CaseRunRow, run_id)

        assert len(row.evaluations) == payload["readiness"]["criteria_total"]
        assert {item.result for item in row.evaluations} <= {
            result.value for result in CriterionResult
        }


def test_running_the_same_case_twice_keeps_both_runs(engine, payload):
    """Overwriting would destroy the comparison a version bump depends on."""
    with session_scope(engine) as session:
        first = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )
        second = save_case_run(
            session, payload=payload, request_id="req-2", organization_id=_ORGANIZATION
        )

    assert first != second
    with session_scope(engine) as session:
        runs = recent_case_runs(
            session,
            organization_id=_ORGANIZATION,
            case_id=payload["readiness"]["case_id"],
        )

    assert {run.run_id for run in runs} == {first, second}


def test_document_rows_record_where_the_bytes_are_never_the_bytes(engine, payload):
    keys = {"D1": "cases/PA-CASE-001/D1/01_pa_request.txt"}

    with session_scope(engine) as session:
        run_id = save_case_run(
            session,
            payload=payload,
            request_id="req-1",
            organization_id=_ORGANIZATION,
            storage_keys=keys,
        )
        row = session.get(CaseRunRow, run_id)
        documents = {item.document_id: item for item in row.documents}

        assert documents["D1"].storage_key == keys["D1"]
        assert not hasattr(DocumentRow, "content")
        assert not hasattr(DocumentRow, "text")


def test_a_failed_transaction_leaves_nothing_behind(engine, payload):
    with pytest.raises(RuntimeError):
        with session_scope(engine) as session:
            save_case_run(
                session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
            )
            raise RuntimeError("something went wrong after the write")

    with session_scope(engine) as session:
        assert recent_case_runs(session, organization_id=_ORGANIZATION) == []


def test_reviewer_decisions_append_and_read_back(engine, payload):
    with session_scope(engine) as session:
        run_id = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )
        record = load_case_run(session, run_id=run_id, organization_id=_ORGANIZATION)
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
        save_reviewer_decision(session, accepted, run_id=run_id, organization_id=_ORGANIZATION)
        save_reviewer_decision(session, corrected, run_id=run_id, organization_id=_ORGANIZATION)

    with session_scope(engine) as session:
        decisions = load_reviewer_decisions(
            session, organization_id=_ORGANIZATION, case_id=record.case_id
        )

    assert [item.action for item in decisions] == [
        ReviewerAction.ACCEPTED,
        ReviewerAction.CORRECTED,
    ]
    assert decisions[1].corrected_result is CriterionResult.HUMAN_REVIEW_REQUIRED
    assert decisions[1].matcher_version == evaluation.matcher_version


def test_a_superseding_decision_is_another_row_not_an_edit(engine, payload):
    """Append-only: a correction that can be edited is not a record of the moment."""
    with session_scope(engine) as session:
        run_id = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )
        evaluation = load_case_run(
            session, run_id=run_id, organization_id=_ORGANIZATION
        ).evaluations[0]
        for action in (ReviewerAction.ACCEPTED, ReviewerAction.REJECTED):
            save_reviewer_decision(
                session,
                decision_from_evaluation(evaluation, reviewer_id="reviewer-01", action=action),
                run_id=run_id,
                organization_id=_ORGANIZATION,
            )

    with session_scope(engine) as session:
        assert len(load_reviewer_decisions(session, organization_id=_ORGANIZATION)) == 2


def test_runs_and_decisions_are_invisible_across_organizations(engine, payload):
    with session_scope(engine) as session:
        run_id = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )
        record = load_case_run(session, run_id=run_id, organization_id=_ORGANIZATION)
        decision = decision_from_evaluation(
            record.evaluations[0], reviewer_id="reviewer-01", action=ReviewerAction.ACCEPTED
        )
        save_reviewer_decision(session, decision, run_id=run_id, organization_id=_ORGANIZATION)

    with session_scope(engine) as session:
        assert load_case_run(session, run_id=run_id, organization_id="org-b") is None
        assert recent_case_runs(session, organization_id="org-b") == []
        assert load_reviewer_decisions(session, organization_id="org-b") == []


# --- The reviewer worklist -------------------------------------------------


def _case_manifest(case_id: str) -> dict:
    return {
        "case_id": case_id,
        "patient_synthetic_id": "SYNTH-0001",
        "payer": "Example Health Plan",
        "medication": "Drug A",
        "indication": "Example Condition",
        "pa_required": True,
    }


def test_the_worklist_shows_the_newest_run_not_the_last_one_written(engine, payload):
    """`created_at` decides, so a backfilled run does not become the headline.

    The timestamps are set explicitly rather than left to the clock: two runs
    written in one transaction can land in the same microsecond, and a test that
    passed because of insertion order would not be testing the ordering at all.
    """
    case_id = payload["readiness"]["case_id"]
    with session_scope(engine) as session:
        create_case_record(
            session,
            organization_id=_ORGANIZATION,
            case_id=case_id,
            manifest=_case_manifest(case_id),
        )
        older = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )
        newer = save_case_run(
            session, payload=payload, request_id="req-2", organization_id=_ORGANIZATION
        )
        session.get(CaseRunRow, older).created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        session.get(CaseRunRow, newer).created_at = datetime(2026, 6, 1, tzinfo=timezone.utc)

    with session_scope(engine) as session:
        summaries, total = list_cases(session, organization_id=_ORGANIZATION)

    assert total == 1
    assert summaries[0].latest_run is not None
    assert summaries[0].latest_run.run_id == newer

    # Move the other run ahead of it: the headline must follow the timestamp.
    with session_scope(engine) as session:
        session.get(CaseRunRow, older).created_at = datetime(2026, 12, 1, tzinfo=timezone.utc)

    with session_scope(engine) as session:
        summaries, _ = list_cases(session, organization_id=_ORGANIZATION)

    assert summaries[0].latest_run.run_id == older


def test_the_worklist_carries_the_counts_a_reviewer_triages_on(engine, payload):
    case_id = payload["readiness"]["case_id"]
    with session_scope(engine) as session:
        create_case_record(
            session,
            organization_id=_ORGANIZATION,
            case_id=case_id,
            manifest=_case_manifest(case_id),
        )
        save_case_run(session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION)

    with session_scope(engine) as session:
        summaries, _ = list_cases(session, organization_id=_ORGANIZATION)

    latest = summaries[0].latest_run
    assert latest.criteria_total == payload["readiness"]["criteria_total"]
    assert latest.groundedness_gate == payload["readiness"]["groundedness_gate"]
    assert latest.matcher_version


def test_a_case_that_has_never_run_still_appears_on_the_worklist(engine):
    """A case with no run is the one a reviewer most needs to be shown."""
    with session_scope(engine) as session:
        create_case_record(
            session,
            organization_id=_ORGANIZATION,
            case_id="PA-CASE-NEW",
            manifest=_case_manifest("PA-CASE-NEW"),
        )

    with session_scope(engine) as session:
        summaries, total = list_cases(session, organization_id=_ORGANIZATION)

    assert total == 1
    assert summaries[0].case_id == "PA-CASE-NEW"
    assert summaries[0].latest_run is None
    assert summaries[0].document_count == 0


def test_the_worklist_pages_newest_first_and_reports_the_whole_total(engine):
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    with session_scope(engine) as session:
        for index in range(5):
            create_case_record(
                session,
                organization_id=_ORGANIZATION,
                case_id=f"PA-CASE-{index}",
                manifest=_case_manifest(f"PA-CASE-{index}"),
            )
        rows = session.execute(
            select(CaseRow).where(CaseRow.organization_id == _ORGANIZATION)
        ).scalars()
        for row in rows:
            row.created_at = base + timedelta(days=int(row.case_id.rsplit("-", 1)[1]))

    with session_scope(engine) as session:
        first_page, total = list_cases(session, organization_id=_ORGANIZATION, limit=2)
        second_page, _ = list_cases(session, organization_id=_ORGANIZATION, limit=2, offset=2)

    assert total == 5
    assert [summary.case_id for summary in first_page] == ["PA-CASE-4", "PA-CASE-3"]
    assert [summary.case_id for summary in second_page] == ["PA-CASE-2", "PA-CASE-1"]


def test_the_worklist_stops_at_the_organization_boundary(engine):
    with session_scope(engine) as session:
        create_case_record(
            session,
            organization_id=_ORGANIZATION,
            case_id="PA-CASE-A",
            manifest=_case_manifest("PA-CASE-A"),
        )
        create_case_record(
            session,
            organization_id="org-b",
            case_id="PA-CASE-B",
            manifest=_case_manifest("PA-CASE-B"),
        )

    with session_scope(engine) as session:
        summaries, total = list_cases(session, organization_id=_ORGANIZATION)

    assert total == 1
    assert [summary.case_id for summary in summaries] == ["PA-CASE-A"]


# --- Resolving a citation to the object that was uploaded ------------------


def test_a_cited_document_resolves_to_the_object_it_was_uploaded_as(engine, payload):
    """`D1` in a citation and a UUID in object storage are the same document."""
    case_id = payload["readiness"]["case_id"]
    key = "cases/org-a/PA-CASE-001/abc123/01_pa_request.txt"
    with session_scope(engine) as session:
        case_record = create_case_record(
            session,
            organization_id=_ORGANIZATION,
            case_id=case_id,
            manifest=_case_manifest(case_id),
        )
        save_uploaded_document(
            session,
            case_record_id=case_record.id,
            organization_id=_ORGANIZATION,
            case_id=case_id,
            filename="01_pa_request.txt",
            media_type="text/plain",
            size_bytes=412,
            sha256="a" * 64,
            storage_key=key,
            retain_until=datetime(2036, 1, 1, tzinfo=timezone.utc),
        )
        run_id = save_case_run(
            session,
            payload=payload,
            request_id="req-1",
            organization_id=_ORGANIZATION,
            storage_keys={"D1": key},
        )

    with session_scope(engine) as session:
        record = load_run_document(
            session, run_id=run_id, document_id="D1", organization_id=_ORGANIZATION
        )

    assert record is not None
    assert record.filename == "01_pa_request.txt"
    assert record.storage_key == key
    # Digest and media type come from the upload, the only record of what was
    # actually accepted at the boundary.
    assert record.sha256 == "a" * 64
    assert record.media_type == "text/plain"


def test_a_cited_document_is_invisible_from_another_organization(engine, payload):
    with session_scope(engine) as session:
        run_id = save_case_run(
            session,
            payload=payload,
            request_id="req-1",
            organization_id=_ORGANIZATION,
            storage_keys={"D1": "cases/org-a/PA-CASE-001/abc123/01_pa_request.txt"},
        )

    with session_scope(engine) as session:
        assert (
            load_run_document(session, run_id=run_id, document_id="D1", organization_id="org-b")
            is None
        )
        assert (
            load_run_document(
                session, run_id=run_id, document_id="D9", organization_id=_ORGANIZATION
            )
            is None
        )


def test_a_run_whose_documents_were_never_uploaded_resolves_without_a_key(engine, payload):
    """A CLI run stores no object. The citation resolves; the bytes do not exist."""
    with session_scope(engine) as session:
        run_id = save_case_run(
            session, payload=payload, request_id="req-1", organization_id=_ORGANIZATION
        )

    with session_scope(engine) as session:
        record = load_run_document(
            session, run_id=run_id, document_id="D1", organization_id=_ORGANIZATION
        )

    assert record is not None
    assert record.storage_key is None
    assert record.sha256 is None
