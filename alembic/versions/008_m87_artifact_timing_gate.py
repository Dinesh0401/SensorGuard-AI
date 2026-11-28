"""Add Milestone 8.7 artifact lineage, training cutoffs, and forecast lead-time columns.

Revision ID: 008_m87_artifact_timing_gate
Revises: 007_m86_safety_fencing
Create Date: 2026-11-26 21:15:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "008_m87_artifact_timing_gate"
down_revision: str | None = "007_m86_safety_fencing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add per-model artifact lineage, training/calibration cutoffs, and forecast lead timing."""
    op.add_column(
        "forecast_runs",
        sa.Column("training_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "forecast_runs",
        sa.Column("training_data_cutoff", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "forecast_runs",
        sa.Column("calibration_cutoff", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("forecast_runs", sa.Column("code_version", sa.String(length=64), nullable=True))
    op.add_column("forecast_runs", sa.Column("feature_schema_version", sa.String(length=64), nullable=True))
    op.add_column("forecast_runs", sa.Column("min_lead_seconds", sa.Float(), nullable=True))
    op.add_column("forecast_runs", sa.Column("effective_lead_seconds", sa.Float(), nullable=True))

    op.add_column("forecasts", sa.Column("artifact_immutable_ref", sa.String(length=128), nullable=True))
    op.add_column("forecasts", sa.Column("artifact_sha256", sa.String(length=64), nullable=True))
    op.add_column(
        "forecasts",
        sa.Column("training_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("forecasts", sa.Column("effective_lead_seconds", sa.Float(), nullable=True))
    op.add_column("forecasts", sa.Column("min_lead_seconds", sa.Float(), nullable=True))
    op.add_column("forecasts", sa.Column("available_at_decision_cutoff", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Revert Milestone 8.7 schema additions."""
    op.drop_column("forecasts", "available_at_decision_cutoff")
    op.drop_column("forecasts", "min_lead_seconds")
    op.drop_column("forecasts", "effective_lead_seconds")
    op.drop_column("forecasts", "training_completed_at")
    op.drop_column("forecasts", "artifact_sha256")
    op.drop_column("forecasts", "artifact_immutable_ref")

    op.drop_column("forecast_runs", "effective_lead_seconds")
    op.drop_column("forecast_runs", "min_lead_seconds")
    op.drop_column("forecast_runs", "feature_schema_version")
    op.drop_column("forecast_runs", "code_version")
    op.drop_column("forecast_runs", "calibration_cutoff")
    op.drop_column("forecast_runs", "training_data_cutoff")
    op.drop_column("forecast_runs", "training_completed_at")
