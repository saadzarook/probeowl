# Author: @SaadZarook
"""The kill switches that are on.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kill_switches",
        sa.Column("workspace_id", sa.String(64), nullable=False),
        # "global", "global/<application>" or "global/<application>/<connector>": never empty,
        # since Oracle stores an empty string as NULL and a key column cannot be NULL.
        sa.Column("target", sa.String(140), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("application", sa.String(64)),
        sa.Column("connector", sa.String(64)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reference", sa.String(64)),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_name", sa.String(255), nullable=False),
        # Timezone-aware, as in 0001: stored in UTC on every store database.
        sa.Column("engaged_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "target", name="pk_kill_switches"),
    )


def downgrade() -> None:
    op.drop_table("kill_switches")
