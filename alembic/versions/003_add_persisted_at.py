"""Add persisted_at column to raw_ingest_batches and observations.

Revision ID: 003_add_persisted_at
Revises: 002_version_aware_observations
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003_add_persisted_at"
down_revision: str | None = "002_version_aware_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("raw_ingest_batches") as batch_op:
        batch_op.add_column(
            sa.Column(
                "persisted_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            )
        )
        batch_op.create_index("ix_raw_batches_persisted_at", ["persisted_at"], unique=False)

    with op.batch_alter_table("observations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "persisted_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
            )
        )
        batch_op.create_index("ix_obs_persisted_at", ["persisted_at"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("observations") as batch_op:
        batch_op.drop_index("ix_obs_persisted_at")
        batch_op.drop_column("persisted_at")

    with op.batch_alter_table("raw_ingest_batches") as batch_op:
        batch_op.drop_index("ix_raw_batches_persisted_at")
        batch_op.drop_column("persisted_at")
