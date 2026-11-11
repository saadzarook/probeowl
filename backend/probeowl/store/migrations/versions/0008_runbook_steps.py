# Author: @SaadZarook
"""A runbook's steps in one run: a read query's latest result and a manual step's tick.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runbook_steps",
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(36), nullable=False),
        sa.Column("runbook", sa.String(64), nullable=False),
        sa.Column("step", sa.String(64), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("result", sa.Text()),
        sa.PrimaryKeyConstraint("workspace_id", "run_id", "runbook", "step"),
        sa.ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"]),
    )


def downgrade() -> None:
    op.drop_table("runbook_steps")
