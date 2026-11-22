"""Add raw_bytes to raw_ingest_batches and create forecast_feature_snapshots table.

Revision ID: 004_snapshots_and_raw_bytes
Revises: 003_add_persisted_at
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "004_snapshots_and_raw_bytes"
down_revision: str | None = "003_add_persisted_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("raw_ingest_batches") as batch_op:
        batch_op.add_column(sa.Column("raw_bytes", sa.LargeBinary(), nullable=True))

    op.create_table(
        "forecast_feature_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sensor_id", sa.Integer(), nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("features_json", sa.Text(), nullable=False),
        sa.Column("feature_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_feature_snapshots_sensor_id", "forecast_feature_snapshots", ["sensor_id"], unique=False
    )
    op.create_index(
        "ix_feature_snapshots_cutoff", "forecast_feature_snapshots", ["decision_cutoff"], unique=False
    )
    op.create_index("ix_feature_snapshots_hash", "forecast_feature_snapshots", ["feature_hash"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_feature_snapshots_hash", table_name="forecast_feature_snapshots")
    op.drop_index("ix_feature_snapshots_cutoff", table_name="forecast_feature_snapshots")
    op.drop_index("ix_feature_snapshots_sensor_id", table_name="forecast_feature_snapshots")
    op.drop_table("forecast_feature_snapshots")

    with op.batch_alter_table("raw_ingest_batches") as batch_op:
        batch_op.drop_column("raw_bytes")
