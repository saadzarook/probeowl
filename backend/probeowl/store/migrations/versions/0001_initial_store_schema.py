# Author: @SaadZarook
"""Initial store schema: runs, location results, evidence, expectation results, audit,
catalog and runbook index.

Revision ID: 0001
Revises:
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSON columns are plain text on every database (see tables.JsonText).
NAME = sa.String(64)
ID = sa.String(36)
TIME = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("workspace_id", NAME, nullable=False),
        sa.Column("id", ID, nullable=False),
        sa.Column("application", NAME, nullable=False),
        sa.Column("environment", NAME, nullable=False),
        sa.Column("probe", NAME, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("probe_version", NAME, nullable=False),
        sa.Column("entity", NAME, nullable=False),
        sa.Column("identifier", sa.String(128), nullable=False),
        sa.Column("identifier_value", sa.String(256), nullable=False),
        sa.Column("subject", sa.String(256)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("skip_reason", sa.Text()),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_name", sa.String(128), nullable=False),
        sa.Column("actor_on_behalf_of", sa.String(128)),
        sa.Column("investigation", sa.Text()),
        sa.Column("started_at", TIME, nullable=False),
        sa.Column("finished_at", TIME),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_runs"),
    )
    op.create_index(
        "ix_runs_workspace_id_application_started_at",
        "runs",
        ["workspace_id", "application", "started_at"],
    )
    op.create_index("ix_runs_workspace_id_subject", "runs", ["workspace_id", "subject"])

    op.create_table(
        "location_results",
        sa.Column("workspace_id", NAME, nullable=False),
        sa.Column("run_id", ID, nullable=False),
        sa.Column("seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("location", NAME, nullable=False),
        sa.Column("parent", NAME),
        sa.Column("row_index", sa.Integer()),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["runs.workspace_id", "runs.id"],
            name="fk_location_results_runs",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "run_id", "seq", name="pk_location_results"),
    )

    op.create_table(
        "evidence",
        sa.Column("workspace_id", NAME, nullable=False),
        sa.Column("run_id", ID, nullable=False),
        sa.Column("seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("connector", NAME, nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("parameters", sa.Text(), nullable=False),
        sa.Column("guards", sa.Text(), nullable=False),
        sa.Column("result_rows", sa.Text(), nullable=False),
        sa.Column("rows_total", sa.Integer()),
        sa.Column("started_at", TIME, nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id", "seq"],
            ["location_results.workspace_id", "location_results.run_id", "location_results.seq"],
            name="fk_evidence_location_results",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "run_id", "seq", name="pk_evidence"),
    )

    op.create_table(
        "expectation_results",
        sa.Column("workspace_id", NAME, nullable=False),
        sa.Column("run_id", ID, nullable=False),
        sa.Column("seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("expectation", sa.Text(), nullable=False),
        sa.Column("result", sa.String(8), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.ForeignKeyConstraint(
            ["workspace_id", "run_id"],
            ["runs.workspace_id", "runs.id"],
            name="fk_expectation_results_runs",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "run_id", "seq", name="pk_expectation_results"),
    )

    op.create_table(
        "audit_events",
        sa.Column("workspace_id", NAME, nullable=False),
        sa.Column("id", ID, nullable=False),
        sa.Column("occurred_at", TIME, nullable=False),
        sa.Column("action", NAME, nullable=False),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_name", sa.String(128), nullable=False),
        sa.Column("actor_on_behalf_of", sa.String(128)),
        sa.Column("application", NAME),
        sa.Column("environment", NAME),
        sa.Column("run_id", ID),
        sa.Column("details", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "id", name="pk_audit_events"),
    )
    op.create_index(
        "ix_audit_events_workspace_id_occurred_at", "audit_events", ["workspace_id", "occurred_at"]
    )
    op.create_index(
        "ix_audit_events_workspace_id_run_id", "audit_events", ["workspace_id", "run_id"]
    )

    op.create_table(
        "catalog_tables",
        sa.Column("workspace_id", NAME, nullable=False),
        sa.Column("application", NAME, nullable=False),
        sa.Column("connector", NAME, nullable=False),
        sa.Column("table_name", sa.String(128), nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("marked_columns", sa.Text(), nullable=False),
        sa.Column("retention", sa.String(16)),
        sa.Column("approx_rows", sa.Integer()),
        sa.PrimaryKeyConstraint(
            "workspace_id", "application", "connector", "table_name", name="pk_catalog_tables"
        ),
    )

    op.create_table(
        "runbook_index",
        sa.Column("workspace_id", NAME, nullable=False),
        sa.Column("application", NAME, nullable=False),
        sa.Column("runbook", NAME, nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("entity", NAME, nullable=False),
        sa.Column("applies_when", sa.Text(), nullable=False),
        sa.Column("owner", sa.String(128), nullable=False),
        sa.Column("last_verified", sa.Date(), nullable=False),
        sa.Column("source_path", sa.String(512), nullable=False),
        sa.PrimaryKeyConstraint("workspace_id", "application", "runbook", name="pk_runbook_index"),
    )
    op.create_index(
        "ix_runbook_index_workspace_id_entity", "runbook_index", ["workspace_id", "entity"]
    )


def downgrade() -> None:
    op.drop_table("runbook_index")
    op.drop_table("catalog_tables")
    op.drop_table("audit_events")
    op.drop_table("expectation_results")
    op.drop_table("evidence")
    op.drop_table("location_results")
    op.drop_table("runs")
