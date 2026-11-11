# Author: @SaadZarook
"""ProbeOwl's own tables, portable across SQLite, PostgreSQL, SQL Server, Oracle and MariaDB.

Portability rules: no vendor types (JSON is stored as text), every string has a length,
no column named after a reserved word, and every table carries workspace_id.
"""

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

# Explicit constraint names, so migrations can refer to them on every database.
metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "fk": "fk_%(table_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

NAME = 64
ID = 36  # a UUID in text form


class JsonText(TypeDecorator[Any]):
    """JSON stored as text. Oracle has no portable JSON type, so every database gets text."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> str | None:
        return None if value is None else json.dumps(value, separators=(",", ":"))

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        return None if value is None else json.loads(value)


class UtcDateTime(TypeDecorator[datetime]):
    """A UTC timestamp. SQLite and MariaDB drop the zone, so it is restored on read."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("store timestamps must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)


LOCAL_WORKSPACE = "local"


def _workspace() -> Column[str]:
    # First column of every primary key, so hosted workspaces never share a row.
    return Column("workspace_id", String(NAME), nullable=False, primary_key=True)


runs = Table(
    "runs",
    metadata,
    _workspace(),
    Column("id", String(ID), nullable=False, primary_key=True),
    Column("application", String(NAME), nullable=False),
    Column("environment", String(NAME), nullable=False),
    Column("probe", String(NAME), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("probe_version", String(NAME), nullable=False),
    Column("entity", String(NAME), nullable=False),
    Column("identifier", String(128), nullable=False),
    Column("identifier_value", String(256), nullable=False),  # masked when PII
    Column("subject", String(256)),
    Column("via", String(256)),
    Column("resolution", JsonText),  # the identifier's lookup result, masked
    Column("matches", JsonText),  # masked
    Column("from_run", String(ID)),
    Column("healthy_for", String(ID)),
    Column("healthy_attempt", JsonText),  # masked: its sample holds root keys only
    Column("warnings", JsonText),
    Column("status", String(16), nullable=False),
    Column("skip_reason", Text),
    Column("stop_reason", Text),
    Column("caveat", Text),
    Column("actor_kind", String(16), nullable=False),
    Column("actor_name", String(128), nullable=False),
    Column("actor_on_behalf_of", String(128)),
    Column("investigation", JsonText),  # the investigation record, as entered
    Column("started_at", UtcDateTime(), nullable=False),
    Column("finished_at", UtcDateTime()),
    Index(None, "workspace_id", "application", "started_at"),
    Index(None, "workspace_id", "subject"),
)
location_results = Table(
    "location_results",
    metadata,
    _workspace(),
    Column("run_id", String(ID), nullable=False, primary_key=True),
    Column("seq", Integer, nullable=False, primary_key=True, autoincrement=False),
    Column("location", String(NAME), nullable=False),
    Column("parent", String(NAME)),
    Column("row_index", Integer),
    Column("outcome", String(16), nullable=False),
    Column("reason", Text),
    Column("caveat", Text),
    ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"]),
)

evidence = Table(
    "evidence",
    metadata,
    _workspace(),
    Column("run_id", String(ID), nullable=False, primary_key=True),
    Column("seq", Integer, nullable=False, primary_key=True, autoincrement=False),
    Column("connector", String(NAME), nullable=False),
    Column("query_text", Text, nullable=False),
    Column("parameters", JsonText, nullable=False),
    Column("guards", JsonText, nullable=False),
    Column("result_rows", JsonText, nullable=False),  # masked before it is stored
    Column("column_types", JsonText),  # each column's type, never its value
    Column("rows_total", Integer),
    Column("started_at", UtcDateTime(), nullable=False),
    Column("duration_ms", Integer, nullable=False),
    ForeignKeyConstraint(
        ["workspace_id", "run_id", "seq"],
        [location_results.c.workspace_id, location_results.c.run_id, location_results.c.seq],
    ),
)

expectation_results = Table(
    "expectation_results",
    metadata,
    _workspace(),
    Column("run_id", String(ID), nullable=False, primary_key=True),
    Column("seq", Integer, nullable=False, primary_key=True, autoincrement=False),
    Column("step", Integer, nullable=False),
    Column("expectation", Text, nullable=False),
    Column("result", String(8), nullable=False),
    Column("detail", Text),
    Column("caveat", Text),
    ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"]),
)

# Append-only: the store refuses UPDATE and DELETE on this table (see engine.py), and in
# team mode members get insert-only rights on it.
audit_events = Table(
    "audit_events",
    metadata,
    _workspace(),
    Column("id", String(ID), nullable=False, primary_key=True),
    Column("occurred_at", UtcDateTime(), nullable=False),
    Column("action", String(NAME), nullable=False),
    Column("actor_kind", String(16), nullable=False),
    Column("actor_name", String(128), nullable=False),
    Column("actor_on_behalf_of", String(128)),
    Column("application", String(NAME)),
    Column("environment", String(NAME)),
    Column("run_id", String(ID)),
    Column("details", JsonText, nullable=False),
    Index(None, "workspace_id", "occurred_at"),
    Index(None, "workspace_id", "run_id"),
)

catalog_tables = Table(
    "catalog_tables",
    metadata,
    _workspace(),
    Column("application", String(NAME), nullable=False, primary_key=True),
    Column("connector", String(NAME), nullable=False, primary_key=True),
    Column("table_name", String(128), nullable=False, primary_key=True),
    Column("label", String(32), nullable=False),
    Column("owner", String(128), nullable=False),
    Column("description", Text),
    Column("marked_columns", JsonText, nullable=False),
    Column("retention", String(16)),
    Column("approx_rows", Integer),
)

runbook_steps = Table(
    "runbook_steps",
    metadata,
    _workspace(),
    Column("run_id", String(ID), nullable=False, primary_key=True),
    Column("runbook", String(NAME), nullable=False, primary_key=True),
    Column("step", String(NAME), nullable=False, primary_key=True),
    Column("done", Boolean, nullable=False),
    Column("result", JsonText),  # masked before it is stored
    ForeignKeyConstraint(["workspace_id", "run_id"], ["runs.workspace_id", "runs.id"]),
)

kill_switches = Table(
    "kill_switches",
    metadata,
    _workspace(),
    Column("target", String(140), nullable=False, primary_key=True),
    Column("scope", String(16), nullable=False),
    Column("application", String(NAME)),
    Column("connector", String(NAME)),
    Column("reason", Text, nullable=False),
    Column("reference", String(NAME)),
    Column("actor_kind", String(16), nullable=False),
    Column("actor_name", String(255), nullable=False),
    Column("engaged_at", UtcDateTime(), nullable=False),
)

schema_snapshots = Table(
    "schema_snapshots",
    metadata,
    _workspace(),
    Column("id", String(ID), nullable=False, primary_key=True),
    Column("application", String(NAME), nullable=False),
    Column("connector", String(NAME), nullable=False),
    Column("environment", String(NAME), nullable=False),
    Column("imported_at", UtcDateTime(), nullable=False),
    Column("tables", JsonText, nullable=False),  # schema only: never a row of data
    Column("truncated", Text),
    Index(
        "ix_schema_snapshots_latest",
        "workspace_id",
        "application",
        "connector",
        "environment",
        "imported_at",
    ),
)

runbook_index = Table(
    "runbook_index",
    metadata,
    _workspace(),
    Column("application", String(NAME), nullable=False, primary_key=True),
    Column("runbook", String(NAME), nullable=False, primary_key=True),
    Column("title", String(256), nullable=False),
    Column("entity", String(NAME), nullable=False),
    Column("applies_when", JsonText, nullable=False),
    Column("owner", String(128), nullable=False),
    Column("last_verified", Date, nullable=False),
    Column("source_path", String(512), nullable=False),
    Index(None, "workspace_id", "entity"),
)

APPEND_ONLY_TABLES = frozenset({audit_events.name})
