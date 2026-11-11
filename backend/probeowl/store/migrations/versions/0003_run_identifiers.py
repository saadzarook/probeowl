# Author: @SaadZarook
"""Runs record how their identifier was resolved: via, the lookup, the matches, the run picked from.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("via", sa.String(256)))
    op.add_column("runs", sa.Column("resolution", sa.Text()))
    op.add_column("runs", sa.Column("matches", sa.Text()))
    op.add_column("runs", sa.Column("from_run", sa.String(36)))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        for column in ("from_run", "matches", "resolution", "via"):
            batch.drop_column(column)
