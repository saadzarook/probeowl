# Author: @SaadZarook
"""Runs record warnings that limit how far their results can be trusted.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("warnings", sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("warnings")
