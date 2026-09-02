"""SQLAlchemy tables for case runs and reviewer feedback.

Column choices follow one rule: a column exists when something filters, joins,
or orders on it. Everything else lives in the row's `payload`, which holds the
validated Pydantic object exactly as the pipeline produced it. That keeps
`models.py` the single definition of every shape.

Types stay dialect-neutral — `JSON`, not `JSONB`; no vector columns — so the
same schema runs on SQLite in a developer's test run and Postgres in CI and
deployment. When a query genuinely needs `JSONB` indexing, that is a migration
with a reason attached rather than a default nobody chose.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CaseRow(Base):
    """Durable case manifest, unique within one organization."""

    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)

    documents: Mapped[list[UploadedDocumentRow]] = relationship(
        back_populates="case", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "case_id", name="uq_cases_organization_case"),
    )


class UploadedDocumentRow(Base):
    """Original uploaded object metadata; bytes remain in object storage."""

    __tablename__ = "uploaded_documents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_row_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    retain_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    case: Mapped[CaseRow] = relationship(back_populates="documents")

    __table_args__ = (
        UniqueConstraint("case_row_id", "filename", name="uq_uploaded_document_filename"),
        Index(
            "ix_uploaded_documents_organization_case",
            "organization_id",
            "case_id",
        ),
    )


class JobRow(Base):
    """Durable queue entry claimed by PostgreSQL workers."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    case_id: Mapped[str | None] = mapped_column(String(128), index=True)
    request_id: Mapped[str | None] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON)
    error_type: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        Index("ix_jobs_claim", "status", "next_attempt_at", "created_at"),
        Index("ix_jobs_organization_created", "organization_id", "created_at"),
    )


class CaseRunRow(Base):
    """One end-to-end run of one case packet.

    A case can be run more than once — after a document is added, after the
    matcher changes — and each run is its own row. Overwriting would destroy
    the comparison that makes a version bump answerable.
    """

    __tablename__ = "case_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    payer: Mapped[str] = mapped_column(String(255), nullable=False)
    medication: Mapped[str] = mapped_column(String(255), nullable=False)
    indication: Mapped[str] = mapped_column(String(255), nullable=False)

    policy_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)

    # Versions live in columns because "did evidence-match-v3 change anything?"
    # is the question this table exists to answer.
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    matcher_version: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str | None] = mapped_column(String(64))
    generator_version: Mapped[str | None] = mapped_column(String(64))

    criteria_total: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria_satisfied: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria_not_satisfied: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria_missing: Mapped[int] = mapped_column(Integer, nullable=False)
    criteria_needs_review: Mapped[int] = mapped_column(Integer, nullable=False)

    groundedness_gate: Mapped[str] = mapped_column(String(16), nullable=False)
    draft_gate: Mapped[str | None] = mapped_column(String(16))

    #: The whole `build_output` document: readiness, policy, assembly,
    #: workflow, checklist, draft groundedness. The row above is an index into
    #: this, never a replacement for it.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    documents: Mapped[list[DocumentRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )
    evaluations: Mapped[list[CriterionEvaluationRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        Index(
            "ix_case_runs_organization_case_created",
            "organization_id",
            "case_id",
            "created_at",
        ),
    )


class DocumentRow(Base):
    """One document in one run, and where its bytes live."""

    __tablename__ = "case_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("case_runs.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    classification_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    requires_review: Mapped[bool] = mapped_column(default=False)

    #: Where the uploaded bytes are, never the bytes themselves. A document is
    #: PHI in a real deployment and belongs in encrypted object storage with a
    #: retention policy, not in a row somebody will `SELECT *` by accident.
    storage_key: Mapped[str | None] = mapped_column(String(1024))

    run: Mapped[CaseRunRow] = relationship(back_populates="documents")

    __table_args__ = (UniqueConstraint("run_id", "document_id", name="uq_run_document"),)


class CriterionEvaluationRow(Base):
    """One criterion result, queryable without opening the run payload."""

    __tablename__ = "criterion_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("case_runs.id", ondelete="CASCADE"), index=True
    )
    criterion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evaluation_method: Mapped[str] = mapped_column(String(32), nullable=False)
    matcher_version: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The full `CriterionEvaluation`, including every cited span. The gate that
    #: refuses an uncited claim is worth nothing if the citation is not stored.
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    run: Mapped[CaseRunRow] = relationship(back_populates="evaluations")

    __table_args__ = (UniqueConstraint("run_id", "criterion_id", name="uq_run_criterion"),)


class ReviewerDecisionRow(Base):
    """One reviewer verdict (README section 16).

    Append-only by construction: there is no update path, and a superseding
    decision is another row. A correction that can be edited later is not a
    record of what a reviewer thought at the time.
    """

    __tablename__ = "reviewer_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    criterion_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)

    reviewer_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    recorded_at: Mapped[str] = mapped_column(String(64), nullable=False)

    original_result: Mapped[str] = mapped_column(String(32), nullable=False)
    corrected_result: Mapped[str | None] = mapped_column(String(32))

    #: Which components produced the answer being corrected. Without these,
    #: "did the new matcher reduce corrections?" cannot be asked.
    matcher_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    generator_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))

    note: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index(
            "ix_reviewer_decisions_organization_case_criterion",
            "organization_id",
            "case_id",
            "criterion_id",
        ),
    )
