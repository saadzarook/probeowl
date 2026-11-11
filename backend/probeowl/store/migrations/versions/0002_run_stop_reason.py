# Author: @SaadZarook
"""Runs record why they stopped early: run time limit reached, or interrupted.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("stop_reason", sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("stop_reason")
