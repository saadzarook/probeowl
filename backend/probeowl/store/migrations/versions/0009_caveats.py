# Author: @SaadZarook
"""Runs, location results and expectation results keep their caveat: a Missing that may be late.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("runs", "location_results", "expectation_results")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("caveat", sa.Text()))


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_column("caveat")
