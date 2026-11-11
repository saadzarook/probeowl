# Author: @SaadZarook
"""Imported schema snapshots, per application, connector and environment.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schema_snapshots",
        sa.Column("workspace_id", sa.String(64), nullable=False),
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("application", sa.String(64), nullable=False),
        sa.Column("connector", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        # Timezone-aware, as in 0001: stored in UTC on every store database.
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tables", sa.Text(), nullable=False),
        sa.Column("truncated", sa.Text()),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_schema_snapshots"),
    )
    op.create_index(
        "ix_schema_snapshots_latest",
        "schema_snapshots",
        ["workspace_id", "application", "connector", "environment", "imported_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_schema_snapshots_latest", table_name="schema_snapshots")
    op.drop_table("schema_snapshots")
