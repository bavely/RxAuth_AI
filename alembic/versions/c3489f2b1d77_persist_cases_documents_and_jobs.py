"""persist cases documents and jobs

Revision ID: c3489f2b1d77
Revises: 7b64f0cd96f2
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3489f2b1d77"
down_revision: str | None = "7b64f0cd96f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", "case_id", name="uq_cases_organization_case"),
    )
    op.create_index(op.f("ix_cases_organization_id"), "cases", ["organization_id"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_type", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_jobs_case_id"), "jobs", ["case_id"], unique=False)
    op.create_index(
        "ix_jobs_claim", "jobs", ["status", "next_attempt_at", "created_at"], unique=False
    )
    op.create_index(op.f("ix_jobs_expires_at"), "jobs", ["expires_at"], unique=False)
    op.create_index(op.f("ix_jobs_kind"), "jobs", ["kind"], unique=False)
    op.create_index(op.f("ix_jobs_lease_expires_at"), "jobs", ["lease_expires_at"], unique=False)
    op.create_index(
        "ix_jobs_organization_created", "jobs", ["organization_id", "created_at"], unique=False
    )
    op.create_index(op.f("ix_jobs_organization_id"), "jobs", ["organization_id"], unique=False)
    op.create_index(op.f("ix_jobs_request_id"), "jobs", ["request_id"], unique=False)
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)

    op.create_table(
        "uploaded_documents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("case_row_id", sa.String(length=64), nullable=False),
        sa.Column("organization_id", sa.String(length=128), nullable=False),
        sa.Column("case_id", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_row_id"], ["cases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_row_id", "filename", name="uq_uploaded_document_filename"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        op.f("ix_uploaded_documents_case_id"), "uploaded_documents", ["case_id"], unique=False
    )
    op.create_index(
        op.f("ix_uploaded_documents_case_row_id"),
        "uploaded_documents",
        ["case_row_id"],
        unique=False,
    )
    op.create_index(
        "ix_uploaded_documents_organization_case",
        "uploaded_documents",
        ["organization_id", "case_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_uploaded_documents_organization_id"),
        "uploaded_documents",
        ["organization_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_uploaded_documents_organization_id"), table_name="uploaded_documents")
    op.drop_index("ix_uploaded_documents_organization_case", table_name="uploaded_documents")
    op.drop_index(op.f("ix_uploaded_documents_case_row_id"), table_name="uploaded_documents")
    op.drop_index(op.f("ix_uploaded_documents_case_id"), table_name="uploaded_documents")
    op.drop_table("uploaded_documents")

    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_request_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_organization_id"), table_name="jobs")
    op.drop_index("ix_jobs_organization_created", table_name="jobs")
    op.drop_index(op.f("ix_jobs_lease_expires_at"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_kind"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_expires_at"), table_name="jobs")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_index(op.f("ix_jobs_case_id"), table_name="jobs")
    op.drop_table("jobs")

    op.drop_index(op.f("ix_cases_organization_id"), table_name="cases")
    op.drop_table("cases")
