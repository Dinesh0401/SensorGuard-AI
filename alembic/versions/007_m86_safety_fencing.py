"""Add Milestone 8.6 prospective timestamp lineage, idempotency constraints, lease fencing, and server rate limits.

Revision ID: 007_m86_safety_fencing
Revises: 006_durable_pilot_control
Create Date: 2026-11-26 20:30:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "007_m86_safety_fencing"
down_revision: str | None = "006_durable_pilot_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add immutable prospective timestamps, unique forecast constraints, fencing tokens, and API rate limits."""
    # 1. forecast_runs lineage & idempotency
    op.add_column(
        "forecast_runs",
        sa.Column("evaluation_mode", sa.String(length=32), nullable=False, server_default="serving"),
    )
    op.add_column("forecast_runs", sa.Column("model_created_at", sa.String(length=64), nullable=True))
    op.add_column("forecast_runs", sa.Column("artifact_immutable_ref", sa.String(length=128), nullable=True))
    op.add_column("forecast_runs", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.add_column(
        "forecast_runs",
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "forecast_runs",
        sa.Column(
            "persisted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_forecast_runs_generated_at", "forecast_runs", ["generated_at"], unique=False)
    op.create_index("ix_forecast_runs_persisted_at", "forecast_runs", ["persisted_at"], unique=False)
    op.create_index("ix_forecast_runs_idempotency_key", "forecast_runs", ["idempotency_key"], unique=True)

    # 2. forecasts immutable generation/persistence timestamps & run-station-target uniqueness
    op.add_column(
        "forecasts",
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.add_column(
        "forecasts",
        sa.Column(
            "persisted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "uq_forecasts_run_station_target",
        "forecasts",
        ["run_id", "station_id", "target_time"],
        unique=True,
    )

    # 3. Lease fencing tokens, operator authorization timestamps & HTTP attempt audit
    op.add_column(
        "pilot_poll_leases",
        sa.Column("fencing_token", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "pilot_control_states",
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pilot_ingest_audit_runs",
        sa.Column("fencing_token", sa.Integer(), nullable=True),
    )
    op.add_column(
        "pilot_ingest_audit_runs",
        sa.Column("http_attempts", sa.Integer(), nullable=False, server_default="0"),
    )

    # 4. Server-side durable rate-limit buckets independent of inference workers
    op.create_table(
        "pilot_api_rate_limits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False),
        sa.Column("window_minute_utc", sa.String(length=32), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_requests", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope", "window_minute_utc", name="uq_pilot_api_rate_scope_window"),
    )
    op.create_index("ix_pilot_api_rate_limits_scope", "pilot_api_rate_limits", ["scope"], unique=False)
    op.create_index(
        "ix_pilot_api_rate_limits_window_minute_utc",
        "pilot_api_rate_limits",
        ["window_minute_utc"],
        unique=False,
    )


def downgrade() -> None:
    """Revert Milestone 8.6 schema additions."""
    op.drop_index("ix_pilot_api_rate_limits_window_minute_utc", table_name="pilot_api_rate_limits")
    op.drop_index("ix_pilot_api_rate_limits_scope", table_name="pilot_api_rate_limits")
    op.drop_table("pilot_api_rate_limits")

    op.drop_column("pilot_ingest_audit_runs", "http_attempts")
    op.drop_column("pilot_ingest_audit_runs", "fencing_token")
    op.drop_column("pilot_control_states", "authorized_at")
    op.drop_column("pilot_poll_leases", "fencing_token")

    op.drop_index("uq_forecasts_run_station_target", table_name="forecasts")
    op.drop_column("forecasts", "persisted_at")
    op.drop_column("forecasts", "generated_at")

    op.drop_index("ix_forecast_runs_idempotency_key", table_name="forecast_runs")
    op.drop_index("ix_forecast_runs_persisted_at", table_name="forecast_runs")
    op.drop_index("ix_forecast_runs_generated_at", table_name="forecast_runs")
    op.drop_column("forecast_runs", "persisted_at")
    op.drop_column("forecast_runs", "generated_at")
    op.drop_column("forecast_runs", "idempotency_key")
    op.drop_column("forecast_runs", "artifact_immutable_ref")
    op.drop_column("forecast_runs", "model_created_at")
    op.drop_column("forecast_runs", "evaluation_mode")
