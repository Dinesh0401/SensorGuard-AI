"""Initial schema for SensorGuard stations, sensors, raw batches, and observations.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_location_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("country", sa.String(length=8), nullable=True),
        sa.Column("timezone_name", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_location_id", name="uq_station_provider_location"),
    )
    op.create_index(
        op.f("ix_stations_provider_location_id"), "stations", ["provider_location_id"], unique=False
    )

    op.create_table(
        "sensors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("station_id", sa.Integer(), nullable=False),
        sa.Column("provider_sensor_id", sa.Integer(), nullable=False),
        sa.Column("parameter", sa.String(length=32), nullable=False),
        sa.Column("canonical_unit", sa.String(length=32), nullable=False),
        sa.Column("averaging_period_hours", sa.Float(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["station_id"], ["stations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_sensor_id", name="uq_provider_sensor_id"),
    )
    op.create_index(op.f("ix_sensors_parameter"), "sensors", ["parameter"], unique=False)
    op.create_index(op.f("ix_sensors_provider_sensor_id"), "sensors", ["provider_sensor_id"], unique=True)
    op.create_index(op.f("ix_sensors_station_id"), "sensors", ["station_id"], unique=False)

    op.create_table(
        "raw_ingest_batches",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("response_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_raw_ingest_batches_fetched_at"), "raw_ingest_batches", ["fetched_at"], unique=False
    )
    op.create_index(
        op.f("ix_raw_ingest_batches_response_hash"), "raw_ingest_batches", ["response_hash"], unique=False
    )

    op.create_table(
        "observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("sensor_id", sa.Integer(), nullable=False),
        sa.Column("raw_batch_id", sa.Integer(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("quality_flag", sa.String(length=32), nullable=False),
        sa.Column("source_status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["raw_batch_id"], ["raw_ingest_batches.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["sensor_id"], ["sensors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sensor_id", "observed_at", name="uq_sensor_observed_at"),
    )
    op.create_index("ix_obs_fetched_at", "observations", ["fetched_at"], unique=False)
    op.create_index("ix_obs_sensor_observed", "observations", ["sensor_id", "observed_at"], unique=False)
    op.create_index(op.f("ix_observations_fetched_at"), "observations", ["fetched_at"], unique=False)
    op.create_index(op.f("ix_observations_observed_at"), "observations", ["observed_at"], unique=False)
    op.create_index(op.f("ix_observations_raw_batch_id"), "observations", ["raw_batch_id"], unique=False)
    op.create_index(op.f("ix_observations_sensor_id"), "observations", ["sensor_id"], unique=False)


def downgrade() -> None:
    op.drop_table("observations")
    op.drop_table("raw_ingest_batches")
    op.drop_table("sensors")
    op.drop_table("stations")
