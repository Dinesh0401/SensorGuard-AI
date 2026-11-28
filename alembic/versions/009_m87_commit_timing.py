"""Add pre_commit_at, commit_completed_at, and commit_safety_margin_seconds to forecasts and runs.

Revision ID: 009_m87_commit_timing
Revises: 008_m87_artifact_timing_gate
Create Date: 2026-11-26 22:15:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "009_m87_commit_timing"
down_revision: str | None = "008_m87_artifact_timing_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add post-commit completion proof timestamps and conservative safety margin columns."""
    op.add_column(
        "forecast_runs",
        sa.Column("commit_safety_margin_seconds", sa.Float(), nullable=True),
    )
    op.add_column(
        "forecast_runs",
        sa.Column("pre_commit_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "forecast_runs",
        sa.Column("commit_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_forecast_runs_commit_completed_at",
        "forecast_runs",
        ["commit_completed_at"],
        unique=False,
    )

    op.add_column(
        "forecasts",
        sa.Column("commit_safety_margin_seconds", sa.Float(), nullable=True),
    )
    op.add_column(
        "forecasts",
        sa.Column("pre_commit_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "forecasts",
        sa.Column("commit_completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Revert commit-completion proof columns."""
    op.drop_column("forecasts", "commit_completed_at")
    op.drop_column("forecasts", "pre_commit_at")
    op.drop_column("forecasts", "commit_safety_margin_seconds")

    op.drop_index("ix_forecast_runs_commit_completed_at", table_name="forecast_runs")
    op.drop_column("forecast_runs", "commit_completed_at")
    op.drop_column("forecast_runs", "pre_commit_at")
    op.drop_column("forecast_runs", "commit_safety_margin_seconds")
