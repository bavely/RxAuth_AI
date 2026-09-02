"""Reading and writing case runs.

Every write goes through here rather than through the ORM directly, so the
columns that index a run and the payload that *is* the run cannot disagree —
they are derived from the same object in one place.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..feedback import ReviewerDecision
from ..models import CaseReadinessReport, CriterionEvaluation
from .tables import CaseRunRow, CriterionEvaluationRow, DocumentRow, ReviewerDecisionRow


@dataclass(frozen=True)
class CaseRunRecord:
    """A stored run, read back as the objects the pipeline produced."""

    run_id: str
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
        case_id=row.case_id,
        request_id=row.request_id,
        created_at=row.created_at.isoformat() if row.created_at else "",
        report=CaseReadinessReport.model_validate(row.payload["readiness"]),
        payload=row.payload,
    )


def load_case_run(session: Session, run_id: str) -> Optional[CaseRunRecord]:
    row = session.get(CaseRunRow, run_id)
    return _to_record(row) if row is not None else None


def recent_case_runs(
    session: Session, *, case_id: Optional[str] = None, limit: int = 20
) -> list[CaseRunRecord]:
    """Newest first, because the question is almost always 'what happened last'."""
    statement = select(CaseRunRow).order_by(CaseRunRow.created_at.desc()).limit(limit)
    if case_id is not None:
        statement = statement.where(CaseRunRow.case_id == case_id)
    return [_to_record(row) for row in session.execute(statement).scalars()]


def save_reviewer_decision(
    session: Session, decision: ReviewerDecision, *, run_id: Optional[str] = None
) -> int:
    """Append one reviewer verdict. There is deliberately no update path."""
    row = ReviewerDecisionRow(
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
    session: Session, *, case_id: Optional[str] = None
) -> list[ReviewerDecision]:
    statement = select(ReviewerDecisionRow).order_by(ReviewerDecisionRow.id)
    if case_id is not None:
        statement = statement.where(ReviewerDecisionRow.case_id == case_id)
    return [
        ReviewerDecision.model_validate(row.payload) for row in session.execute(statement).scalars()
    ]
