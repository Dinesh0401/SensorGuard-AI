"""Add commit_proof_status and single-use Stage 1 pre-flight authorization ledger.

Revision ID: 010_single_use_stage1_proof
Revises: 009_m87_commit_timing
Create Date: 2026-11-27 11:35:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "010_single_use_stage1_proof"
down_revision: str | None = "009_m87_commit_timing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add mandatory commit proof status columns and single-use Stage 1 authorization table."""
    op.add_column(
        "forecast_runs",
        sa.Column("commit_proof_status", sa.String(length=40), nullable=True),
    )
    op.create_index(
        "ix_forecast_runs_commit_proof_status",
        "forecast_runs",
        ["commit_proof_status"],
        unique=False,
    )

    op.add_column(
        "forecasts",
        sa.Column("commit_proof_status", sa.String(length=40), nullable=True),
    )
    op.create_index(
        "ix_forecasts_commit_proof_status",
        "forecasts",
        ["commit_proof_status"],
        unique=False,
    )

    op.create_table(
        "stage1_preflight_authorizations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("approval_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="AUTHORIZED"),
        sa.Column("execution_id", sa.String(length=64), nullable=True),
        sa.Column("authorized_by", sa.String(length=128), nullable=False),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("attempts_consumed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recovered_from_approval_id", sa.String(length=64), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_by_worker", sa.String(length=128), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("approval_id"),
        sa.UniqueConstraint("execution_id"),
    )
    op.create_index(
        "ix_stage1_preflight_authorizations_approval_id",
        "stage1_preflight_authorizations",
        ["approval_id"],
        unique=True,
    )
    op.create_index(
        "ix_stage1_preflight_authorizations_status",
        "stage1_preflight_authorizations",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_stage1_preflight_authorizations_execution_id",
        "stage1_preflight_authorizations",
        ["execution_id"],
        unique=True,
    )


def downgrade() -> None:
    """Revert single-use Stage 1 authorization table and commit_proof_status columns."""
    op.drop_index(
        "ix_stage1_preflight_authorizations_execution_id",
        table_name="stage1_preflight_authorizations",
    )
    op.drop_index(
        "ix_stage1_preflight_authorizations_status",
        table_name="stage1_preflight_authorizations",
    )
    op.drop_index(
        "ix_stage1_preflight_authorizations_approval_id",
        table_name="stage1_preflight_authorizations",
    )
    op.drop_table("stage1_preflight_authorizations")

    op.drop_index("ix_forecasts_commit_proof_status", table_name="forecasts")
    op.drop_column("forecasts", "commit_proof_status")

    op.drop_index("ix_forecast_runs_commit_proof_status", table_name="forecast_runs")
    op.drop_column("forecast_runs", "commit_proof_status")
