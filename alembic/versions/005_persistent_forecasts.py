"""Add forecast_runs and forecasts persistence tables.

Revision ID: 005_persistent_forecasts
Revises: 004_snapshots_and_raw_bytes
Create Date: 2026-11-25 17:05:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "005_persistent_forecasts"
down_revision: str | None = "004_snapshots_and_raw_bytes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create forecast_runs and forecasts tables for persistent forecast auditability."""
    op.create_table(
        "forecast_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("model_sha256", sa.String(length=64), nullable=False),
        sa.Column("dataset_hash", sa.String(length=64), nullable=True),
        sa.Column("algorithm", sa.String(length=64), nullable=False),
        sa.Column("nominal_coverage", sa.Float(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_forecast_runs_model_id", "forecast_runs", ["model_id"], unique=False)
    op.create_index("ix_forecast_runs_issued_at", "forecast_runs", ["issued_at"], unique=False)
    op.create_index("ix_forecast_runs_decision_cutoff", "forecast_runs", ["decision_cutoff"], unique=False)

    op.create_table(
        "forecasts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("sensor_id", sa.Integer(), nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("point_forecast", sa.Float(), nullable=False),
        sa.Column("lower_bound", sa.Float(), nullable=True),
        sa.Column("upper_bound", sa.Float(), nullable=True),
        sa.Column("nominal_coverage", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("abstain_reason", sa.String(length=64), nullable=False),
        sa.Column("d_sensitivity", sa.Float(), nullable=True),
        sa.Column("newest_source_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("feature_hash", sa.String(length=64), nullable=True),
        sa.Column("scenario_bounds_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["forecast_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_forecasts_run_id", "forecasts", ["run_id"], unique=False)
    op.create_index("ix_forecasts_station_id", "forecasts", ["station_id"], unique=False)
    op.create_index("ix_forecasts_sensor_id", "forecasts", ["sensor_id"], unique=False)
    op.create_index("ix_forecasts_decision_cutoff", "forecasts", ["decision_cutoff"], unique=False)
    op.create_index("ix_forecasts_target_time", "forecasts", ["target_time"], unique=False)
    op.create_index("ix_forecasts_status", "forecasts", ["status"], unique=False)
    op.create_index("ix_forecasts_feature_hash", "forecasts", ["feature_hash"], unique=False)


def downgrade() -> None:
    """Drop forecasts and forecast_runs tables."""
    op.drop_index("ix_forecasts_feature_hash", table_name="forecasts")
    op.drop_index("ix_forecasts_status", table_name="forecasts")
    op.drop_index("ix_forecasts_target_time", table_name="forecasts")
    op.drop_index("ix_forecasts_decision_cutoff", table_name="forecasts")
    op.drop_index("ix_forecasts_sensor_id", table_name="forecasts")
    op.drop_index("ix_forecasts_station_id", table_name="forecasts")
    op.drop_index("ix_forecasts_run_id", table_name="forecasts")
    op.drop_table("forecasts")

    op.drop_index("ix_forecast_runs_decision_cutoff", table_name="forecast_runs")
    op.drop_index("ix_forecast_runs_issued_at", table_name="forecast_runs")
    op.drop_index("ix_forecast_runs_model_id", table_name="forecast_runs")
    op.drop_table("forecast_runs")
