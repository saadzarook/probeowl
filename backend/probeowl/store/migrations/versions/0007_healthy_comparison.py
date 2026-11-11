# Author: @SaadZarook
"""A run's link to the run it is healthy for, its latest attempt, and evidence's column types.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("healthy_for", sa.String(36)))
    op.add_column("runs", sa.Column("healthy_attempt", sa.Text()))
    op.add_column("evidence", sa.Column("column_types", sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table("evidence") as batch:
        batch.drop_column("column_types")
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("healthy_attempt")
        batch.drop_column("healthy_for")
