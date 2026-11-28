"""Add durable multi-worker pilot control, quota, lease, audit, and forecast mode tables.

Revision ID: 006_durable_pilot_control
Revises: 005_persistent_forecasts
Create Date: 2026-11-25 22:25:00.000000+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "006_durable_pilot_control"
down_revision: str | None = "005_persistent_forecasts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create durable quota, distributed lease, kill-switch, and audit run tables."""
    op.add_column(
        "forecast_runs",
        sa.Column("execution_mode", sa.String(length=32), nullable=False, server_default="serving"),
    )
    op.add_column(
        "forecast_runs",
        sa.Column("data_mode", sa.String(length=32), nullable=False, server_default="archived_historical"),
    )

    op.create_table(
        "pilot_quota_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("budget_date_utc", sa.String(length=16), nullable=False),
        sa.Column("requests_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_requests_per_day", sa.Integer(), nullable=False, server_default="48"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "budget_date_utc", name="uq_pilot_quota_provider_date"),
    )
    op.create_index(
        "ix_pilot_quota_states_budget_date_utc", "pilot_quota_states", ["budget_date_utc"], unique=False
    )

    op.create_table(
        "pilot_poll_leases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("lease_name", sa.String(length=64), nullable=False),
        sa.Column("holder_id", sa.String(length=128), nullable=True),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("lease_name"),
    )
    op.create_index("ix_pilot_poll_leases_lease_name", "pilot_poll_leases", ["lease_name"], unique=True)
    op.create_index("ix_pilot_poll_leases_expires_at", "pilot_poll_leases", ["expires_at"], unique=False)

    op.create_table(
        "pilot_control_states",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("control_name", sa.String(length=64), nullable=False),
        sa.Column("kill_switch_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("opt_in_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("updated_by", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("control_name"),
    )
    op.create_index(
        "ix_pilot_control_states_control_name", "pilot_control_states", ["control_name"], unique=True
    )

    op.create_table(
        "pilot_ingest_audit_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sensors_polled", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("requests_consumed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_revised", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_deduplicated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_invalid", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recovered_from_expired_lease", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_pilot_ingest_audit_runs_started_at", "pilot_ingest_audit_runs", ["started_at"], unique=False
    )
    op.create_index("ix_pilot_ingest_audit_runs_status", "pilot_ingest_audit_runs", ["status"], unique=False)


def downgrade() -> None:
    """Drop durable pilot control tables and forecast_run columns."""
    op.drop_index("ix_pilot_ingest_audit_runs_status", table_name="pilot_ingest_audit_runs")
    op.drop_index("ix_pilot_ingest_audit_runs_started_at", table_name="pilot_ingest_audit_runs")
    op.drop_table("pilot_ingest_audit_runs")

    op.drop_index("ix_pilot_control_states_control_name", table_name="pilot_control_states")
    op.drop_table("pilot_control_states")

    op.drop_index("ix_pilot_poll_leases_expires_at", table_name="pilot_poll_leases")
    op.drop_index("ix_pilot_poll_leases_lease_name", table_name="pilot_poll_leases")
    op.drop_table("pilot_poll_leases")

    op.drop_index("ix_pilot_quota_states_budget_date_utc", table_name="pilot_quota_states")
    op.drop_table("pilot_quota_states")

    op.drop_column("forecast_runs", "data_mode")
    op.drop_column("forecast_runs", "execution_mode")
