"""Reading and writing case runs.

Every write goes through here rather than through the ORM directly, so the
columns that index a run and the payload that *is* the run cannot disagree —
they are derived from the same object in one place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..feedback import ReviewerDecision
from ..models import CaseReadinessReport, CriterionEvaluation
from .tables import (
    CaseRow,
    CaseRunRow,
    CriterionEvaluationRow,
    DocumentRow,
    ReviewerDecisionRow,
    UploadedDocumentRow,
)


@dataclass(frozen=True)
class CaseRecord:
    id: str
    organization_id: str
    case_id: str
    manifest: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class UploadedDocumentRecord:
    id: str
    organization_id: str
    case_id: str
    filename: str
    media_type: str
    size_bytes: int
    sha256: str
    storage_key: str
    created_at: datetime
    retain_until: datetime


def _case_record(row: CaseRow) -> CaseRecord:
    return CaseRecord(
        id=row.id,
        organization_id=row.organization_id,
        case_id=row.case_id,
        manifest=row.manifest,
        created_at=row.created_at,
    )


def _uploaded_document_record(row: UploadedDocumentRow) -> UploadedDocumentRecord:
    return UploadedDocumentRecord(
        id=row.id,
        organization_id=row.organization_id,
        case_id=row.case_id,
        filename=row.filename,
        media_type=row.media_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        storage_key=row.storage_key,
        created_at=row.created_at,
        retain_until=row.retain_until,
    )


def create_case_record(
    session: Session, *, organization_id: str, case_id: str, manifest: dict[str, Any]
) -> CaseRecord:
    row = CaseRow(
        id=uuid.uuid4().hex,
        organization_id=organization_id,
        case_id=case_id,
        manifest=manifest,
    )
    session.add(row)
    session.flush()
    return _case_record(row)


def load_case_record(
    session: Session,
    *,
    organization_id: str,
    case_id: str,
    for_update: bool = False,
) -> Optional[CaseRecord]:
    statement = select(CaseRow).where(
        CaseRow.organization_id == organization_id,
        CaseRow.case_id == case_id,
    )
    if for_update:
        statement = statement.with_for_update()
    row = session.execute(statement).scalar_one_or_none()
    return _case_record(row) if row is not None else None


@dataclass(frozen=True)
class CaseRunSummary:
    """The last run of a case, reduced to what a worklist row shows."""

    run_id: str
    created_at: datetime
    matcher_version: str
    groundedness_gate: str
    draft_gate: Optional[str]
    criteria_total: int
    criteria_satisfied: int
    criteria_not_satisfied: int
    criteria_missing: int
    criteria_needs_review: int


@dataclass(frozen=True)
class CaseSummaryRecord:
    """One row of a reviewer worklist."""

    case_id: str
    created_at: datetime
    manifest: dict[str, Any]
    document_count: int
    latest_run: Optional[CaseRunSummary]


def _latest_runs_by_case(
    session: Session, *, organization_id: str, case_ids: list[str]
) -> dict[str, CaseRunSummary]:
    """The newest run for each of `case_ids`, in one query.

    A window function rather than a query per case: a worklist page that issues
    one round trip per row is the classic reason a list view gets slow only
    after it has real data in it.
    """
    if not case_ids:
        return {}
    ranked = (
        select(
            CaseRunRow.id.label("run_id"),
            CaseRunRow.case_id.label("case_id"),
            CaseRunRow.created_at.label("created_at"),
            CaseRunRow.matcher_version.label("matcher_version"),
            CaseRunRow.groundedness_gate.label("groundedness_gate"),
            CaseRunRow.draft_gate.label("draft_gate"),
            CaseRunRow.criteria_total.label("criteria_total"),
            CaseRunRow.criteria_satisfied.label("criteria_satisfied"),
            CaseRunRow.criteria_not_satisfied.label("criteria_not_satisfied"),
            CaseRunRow.criteria_missing.label("criteria_missing"),
            CaseRunRow.criteria_needs_review.label("criteria_needs_review"),
            func.row_number()
            .over(
                partition_by=CaseRunRow.case_id,
                # `id` breaks the tie so two runs saved in the same clock tick
                # still order deterministically.
                order_by=(CaseRunRow.created_at.desc(), CaseRunRow.id.desc()),
            )
            .label("recency"),
        )
        .where(
            CaseRunRow.organization_id == organization_id,
            CaseRunRow.case_id.in_(case_ids),
        )
        .subquery()
    )
    rows = session.execute(select(ranked).where(ranked.c.recency == 1)).mappings()
    return {
        row["case_id"]: CaseRunSummary(
            run_id=row["run_id"],
            created_at=row["created_at"],
            matcher_version=row["matcher_version"],
            groundedness_gate=row["groundedness_gate"],
            draft_gate=row["draft_gate"],
            criteria_total=row["criteria_total"],
            criteria_satisfied=row["criteria_satisfied"],
            criteria_not_satisfied=row["criteria_not_satisfied"],
            criteria_missing=row["criteria_missing"],
            criteria_needs_review=row["criteria_needs_review"],
        )
        for row in rows
    }


def list_cases(
    session: Session, *, organization_id: str, limit: int = 25, offset: int = 0
) -> tuple[list[CaseSummaryRecord], int]:
    """One page of an organization's cases, newest first, plus the total.

    The total is returned alongside the page rather than left to the caller,
    because a worklist that cannot say "41 cases" can only say "here are 25",
    and a reviewer has no way to tell a full page from the end of the list.
    """
    total = int(
        session.execute(
            select(func.count(CaseRow.id)).where(CaseRow.organization_id == organization_id)
        ).scalar_one()
    )
    rows = list(
        session.execute(
            select(CaseRow)
            .where(CaseRow.organization_id == organization_id)
            .order_by(CaseRow.created_at.desc(), CaseRow.case_id)
            .limit(limit)
            .offset(offset)
        ).scalars()
    )
    case_ids = [row.case_id for row in rows]

    counts: dict[str, int] = {}
    if case_ids:
        counted = session.execute(
            select(UploadedDocumentRow.case_id, func.count(UploadedDocumentRow.id))
            .where(
                UploadedDocumentRow.organization_id == organization_id,
                UploadedDocumentRow.case_id.in_(case_ids),
            )
            .group_by(UploadedDocumentRow.case_id)
        )
        counts = {case_id: int(count) for case_id, count in counted}

    latest = _latest_runs_by_case(session, organization_id=organization_id, case_ids=case_ids)
    summaries = [
        CaseSummaryRecord(
            case_id=row.case_id,
            created_at=row.created_at,
            manifest=row.manifest,
            document_count=counts.get(row.case_id, 0),
            latest_run=latest.get(row.case_id),
        )
        for row in rows
    ]
    return summaries, total


def case_upload_usage(session: Session, *, organization_id: str, case_id: str) -> tuple[int, int]:
    count, size = session.execute(
        select(
            func.count(UploadedDocumentRow.id),
            func.coalesce(func.sum(UploadedDocumentRow.size_bytes), 0),
        ).where(
            UploadedDocumentRow.organization_id == organization_id,
            UploadedDocumentRow.case_id == case_id,
        )
    ).one()
    return int(count), int(size)


def save_uploaded_document(
    session: Session,
    *,
    case_record_id: str,
    organization_id: str,
    case_id: str,
    filename: str,
    media_type: str,
    size_bytes: int,
    sha256: str,
    storage_key: str,
    retain_until: datetime,
    document_id: Optional[str] = None,
) -> UploadedDocumentRecord:
    row = UploadedDocumentRow(
        id=document_id or uuid.uuid4().hex,
        case_row_id=case_record_id,
        organization_id=organization_id,
        case_id=case_id,
        filename=filename,
        media_type=media_type,
        size_bytes=size_bytes,
        sha256=sha256,
        storage_key=storage_key,
        retain_until=retain_until,
    )
    session.add(row)
    session.flush()
    return _uploaded_document_record(row)


def list_uploaded_documents(
    session: Session, *, organization_id: str, case_id: str
) -> list[UploadedDocumentRecord]:
    rows = session.execute(
        select(UploadedDocumentRow)
        .where(
            UploadedDocumentRow.organization_id == organization_id,
            UploadedDocumentRow.case_id == case_id,
        )
        .order_by(UploadedDocumentRow.created_at, UploadedDocumentRow.id)
    ).scalars()
    return [_uploaded_document_record(row) for row in rows]


@dataclass(frozen=True)
class CaseRunRecord:
    """A stored run, read back as the objects the pipeline produced."""

    run_id: str
    organization_id: str
    case_id: str
    request_id: str
    created_at: str
    report: CaseReadinessReport
    payload: dict[str, Any]

    @property
    def evaluations(self) -> list[CriterionEvaluation]:
        return list(self.report.evaluations)


def _version_from(payload: dict[str, Any], node: str, key: str) -> Optional[str]:
    """Pull one component version out of the workflow trace.

    Read from the recorded nodes rather than from an import, so a stored run
    reports the version that actually produced it even after the code moves on.
    """
    for record in payload.get("workflow", {}).get("nodes", []):
        if record.get("name") == node:
            return record.get("versions", {}).get(key)
    return None


def save_case_run(
    session: Session,
    *,
    payload: dict[str, Any],
    request_id: str,
    organization_id: str,
    storage_keys: Optional[dict[str, str]] = None,
    run_id: Optional[str] = None,
) -> str:
    """Persist one run and return its id.

    `payload` is the `build_output` document — the same one written to
    `reports/case_<id>.json`, so what a reviewer reads from the API and what a
    maintainer diffs on disk are the same bytes.
    """
    readiness = payload["readiness"]
    workflow = payload.get("workflow", {})
    keys = storage_keys or {}

    row = CaseRunRow(
        id=run_id or uuid.uuid4().hex,
        organization_id=organization_id,
        case_id=readiness["case_id"],
        request_id=request_id,
        payer=readiness["payer"],
        medication=readiness["medication"],
        indication=readiness["indication"],
        policy_id=readiness["policy_id"],
        policy_version=readiness["policy_version"],
        workflow_version=workflow.get("version", "unknown"),
        matcher_version=readiness.get("matcher_version", "unknown"),
        extractor_version=_version_from(payload, "extract_case_evidence", "extractor"),
        generator_version=_version_from(payload, "generate_requirement_checklist", "generator"),
        criteria_total=readiness["criteria_total"],
        criteria_satisfied=readiness["criteria_satisfied"],
        criteria_not_satisfied=readiness["criteria_not_satisfied"],
        criteria_missing=readiness["criteria_missing"],
        criteria_needs_review=readiness["criteria_needs_review"],
        groundedness_gate=readiness["groundedness_gate"],
        draft_gate=(
            "PASS"
            if payload.get("draft_groundedness", {}).get("passed")
            else "FAIL"
            if "draft_groundedness" in payload
            else None
        ),
        payload=payload,
    )

    needing_review = set(
        payload.get("assembly", {}).get("documents_requiring_classification_review", [])
    )
    for document in payload.get("assembly", {}).get("documents", []):
        row.documents.append(
            DocumentRow(
                document_id=document["id"],
                filename=document["filename"],
                document_type=document["document_type"],
                classification_confidence=document["classification_confidence"],
                requires_review=document["id"] in needing_review,
                storage_key=keys.get(document["id"]),
            )
        )

    for evaluation in readiness.get("evaluations", []):
        row.evaluations.append(
            CriterionEvaluationRow(
                criterion_id=evaluation["criterion_id"],
                result=evaluation["result"],
                confidence=evaluation["confidence"],
                evaluation_method=evaluation["evaluation_method"],
                matcher_version=evaluation.get("matcher_version", "unknown"),
                payload=evaluation,
            )
        )

    session.add(row)
    session.flush()
    return row.id


def _to_record(row: CaseRunRow) -> CaseRunRecord:
    return CaseRunRecord(
        run_id=row.id,
        organization_id=row.organization_id,
        case_id=row.case_id,
        request_id=row.request_id,
        created_at=row.created_at.isoformat() if row.created_at else "",
        report=CaseReadinessReport.model_validate(row.payload["readiness"]),
        payload=row.payload,
    )


def load_case_run(
    session: Session, *, run_id: str, organization_id: str
) -> Optional[CaseRunRecord]:
    statement = select(CaseRunRow).where(
        CaseRunRow.id == run_id,
        CaseRunRow.organization_id == organization_id,
    )
    row = session.execute(statement).scalar_one_or_none()
    return _to_record(row) if row is not None else None


def recent_case_runs(
    session: Session,
    *,
    organization_id: str,
    case_id: Optional[str] = None,
    limit: int = 20,
) -> list[CaseRunRecord]:
    """Newest first, because the question is almost always 'what happened last'."""
    statement = (
        select(CaseRunRow)
        .where(CaseRunRow.organization_id == organization_id)
        .order_by(CaseRunRow.created_at.desc())
        .limit(limit)
    )
    if case_id is not None:
        statement = statement.where(CaseRunRow.case_id == case_id)
    return [_to_record(row) for row in session.execute(statement).scalars()]


@dataclass(frozen=True)
class RunDocumentRecord:
    """A document as one run cited it, joined to the object that was uploaded.

    `document_id` here is the run-local citation id (`D1`, `D2`), which is what
    a `Provenance` points at — not the upload's UUID. Resolving one to the other
    is the whole reason this exists: without it a citation names a document the
    API cannot hand back.
    """

    run_id: str
    organization_id: str
    case_id: str
    document_id: str
    filename: str
    document_type: str
    storage_key: Optional[str]
    media_type: Optional[str]
    size_bytes: Optional[int]
    sha256: Optional[str]


def load_run_document(
    session: Session, *, run_id: str, document_id: str, organization_id: str
) -> Optional[RunDocumentRecord]:
    """Resolve one cited document, scoped to the organization that owns the run.

    The organization filter is on `case_runs`, not on the caller's word for it.
    A guessed run id from another tenant finds nothing and is indistinguishable
    from a run that does not exist.
    """
    row = session.execute(
        select(DocumentRow, CaseRunRow, UploadedDocumentRow)
        .join(CaseRunRow, DocumentRow.run_id == CaseRunRow.id)
        .outerjoin(
            UploadedDocumentRow,
            UploadedDocumentRow.storage_key == DocumentRow.storage_key,
        )
        .where(
            DocumentRow.run_id == run_id,
            DocumentRow.document_id == document_id,
            CaseRunRow.organization_id == organization_id,
        )
    ).first()
    if row is None:
        return None
    document, run, uploaded = row
    return RunDocumentRecord(
        run_id=run.id,
        organization_id=run.organization_id,
        case_id=run.case_id,
        document_id=document.document_id,
        filename=document.filename,
        document_type=document.document_type,
        storage_key=document.storage_key,
        media_type=uploaded.media_type if uploaded is not None else None,
        size_bytes=uploaded.size_bytes if uploaded is not None else None,
        sha256=uploaded.sha256 if uploaded is not None else None,
    )


def save_reviewer_decision(
    session: Session,
    decision: ReviewerDecision,
    *,
    organization_id: str,
    run_id: Optional[str] = None,
) -> int:
    """Append one reviewer verdict. There is deliberately no update path."""
    row = ReviewerDecisionRow(
        organization_id=organization_id,
        case_id=decision.case_id,
        criterion_id=decision.criterion_id,
        run_id=run_id,
        reviewer_id=decision.reviewer_id,
        action=decision.action.value,
        recorded_at=decision.recorded_at,
        original_result=decision.original_result.value,
        corrected_result=(
            decision.corrected_result.value if decision.corrected_result is not None else None
        ),
        matcher_version=decision.matcher_version,
        generator_version=decision.generator_version,
        prompt_version=decision.prompt_version,
        note=decision.note,
        payload=decision.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    return row.id


def load_reviewer_decisions(
    session: Session, *, organization_id: str, case_id: Optional[str] = None
) -> list[ReviewerDecision]:
    statement = (
        select(ReviewerDecisionRow)
        .where(ReviewerDecisionRow.organization_id == organization_id)
        .order_by(ReviewerDecisionRow.id)
    )
    if case_id is not None:
        statement = statement.where(ReviewerDecisionRow.case_id == case_id)
    return [
        ReviewerDecision.model_validate(row.payload) for row in session.execute(statement).scalars()
    ]
