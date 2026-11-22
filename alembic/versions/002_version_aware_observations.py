"""Add version column and version-aware constraints for observation revisions.

Revision ID: 002_version_aware_observations
Revises: 001_initial_schema
Create Date: 2026-11-20

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "002_version_aware_observations"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("observations") as batch_op:
        batch_op.add_column(sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
        batch_op.drop_constraint("uq_sensor_observed_at", type_="unique")
        batch_op.create_unique_constraint(
            "uq_sensor_observed_version", ["sensor_id", "observed_at", "version"]
        )
        batch_op.create_index(
            "ix_obs_sensor_observed_version", ["sensor_id", "observed_at", "version"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("observations") as batch_op:
        batch_op.drop_index("ix_obs_sensor_observed_version")
        batch_op.drop_constraint("uq_sensor_observed_version", type_="unique")
        batch_op.create_unique_constraint("uq_sensor_observed_at", ["sensor_id", "observed_at"])
        batch_op.drop_column("version")
