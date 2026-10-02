"""add organization tenant boundaries

Revision ID: 7b64f0cd96f2
Revises: 270b221cb65e
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7b64f0cd96f2"
down_revision: str | None = "270b221cb65e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_backfill_default(table_name: str) -> None:
    """Remove the one-time local backfill without losing SQLite portability."""
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table(table_name) as batch:
            batch.alter_column(
                "organization_id",
                existing_type=sa.String(length=128),
                server_default=None,
            )
        return
    op.alter_column(
        table_name,
        "organization_id",
        existing_type=sa.String(length=128),
        server_default=None,
    )


def upgrade() -> None:
    # Existing prototype rows predate tenants. Assign them to the explicit
    # local-development organization, then remove the defaults so every future
    # write must supply its verified organization.
    op.add_column(
        "case_runs",
        sa.Column("organization_id", sa.String(length=128), server_default="local", nullable=False),
    )
    _drop_backfill_default("case_runs")
    op.create_index(
        op.f("ix_case_runs_organization_id"),
        "case_runs",
        ["organization_id"],
        unique=False,
    )
    op.drop_index("ix_case_runs_case_created", table_name="case_runs")
    op.create_index(
        "ix_case_runs_organization_case_created",
        "case_runs",
        ["organization_id", "case_id", "created_at"],
        unique=False,
    )

    op.add_column(
        "reviewer_decisions",
        sa.Column("organization_id", sa.String(length=128), server_default="local", nullable=False),
    )
    _drop_backfill_default("reviewer_decisions")
    op.create_index(
        op.f("ix_reviewer_decisions_organization_id"),
        "reviewer_decisions",
        ["organization_id"],
        unique=False,
    )
    op.drop_index("ix_reviewer_decisions_case_criterion", table_name="reviewer_decisions")
    op.create_index(
        "ix_reviewer_decisions_organization_case_criterion",
        "reviewer_decisions",
        ["organization_id", "case_id", "criterion_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reviewer_decisions_organization_case_criterion",
        table_name="reviewer_decisions",
    )
    op.create_index(
        "ix_reviewer_decisions_case_criterion",
        "reviewer_decisions",
        ["case_id", "criterion_id"],
        unique=False,
    )
    op.drop_index(op.f("ix_reviewer_decisions_organization_id"), table_name="reviewer_decisions")
    op.drop_column("reviewer_decisions", "organization_id")

    op.drop_index("ix_case_runs_organization_case_created", table_name="case_runs")
    op.create_index(
        "ix_case_runs_case_created",
        "case_runs",
        ["case_id", "created_at"],
        unique=False,
    )
    op.drop_index(op.f("ix_case_runs_organization_id"), table_name="case_runs")
    op.drop_column("case_runs", "organization_id")
