# Author: @SaadZarook
"""Runs keep what a connector with no binding left unchecked, and which checks were not applicable.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # JSON columns are plain text on every database (see tables.JsonText). Both are empty on
    # runs stored before: read as no connector unbound and every check applicable.
    op.add_column("runs", sa.Column("not_applicable", sa.Text()))
    op.add_column("expectation_results", sa.Column("not_applicable", sa.Boolean()))


def downgrade() -> None:
    for table in ("runs", "expectation_results"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("not_applicable")
