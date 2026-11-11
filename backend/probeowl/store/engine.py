# Author: @SaadZarook
"""Create the store engine with the store's own safety rules attached."""

import re
from typing import Any

from sqlalchemy import Delete, Engine, Update, create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import TextClause

from probeowl.errors import GuardViolation
from probeowl.store.tables import APPEND_ONLY_TABLES

_TEXT_CHANGE = re.compile(
    r"\b(update|delete\s+from|truncate\s+table|drop\s+table)\s+[\"`\[]?(\w+)", re.IGNORECASE
)


def create_store_engine(url: str) -> Engine:
    """Create an engine for ProbeOwl's store at url."""
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _sqlite_foreign_keys)
    event.listen(engine, "before_execute", _refuse_append_only_changes)
    return engine


def _sqlite_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
    # SQLite ignores foreign keys unless each connection turns them on.
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def _refuse_append_only_changes(
    conn: Connection, clauseelement: Any, multiparams: Any, params: Any, execution_options: Any
) -> None:
    # The audit is append-only. Database rights enforce this in team mode; this catches
    # ProbeOwl's own code first.
    tables: set[str] = set()
    if isinstance(clauseelement, Update | Delete):
        tables.add(clauseelement.table.name)  # type: ignore[union-attr]
    elif isinstance(clauseelement, TextClause):
        tables.update(m.group(2).lower() for m in _TEXT_CHANGE.finditer(clauseelement.text))
    refused = tables & APPEND_ONLY_TABLES
    if refused:
        raise GuardViolation(f"{', '.join(sorted(refused))} is append-only; rows cannot change")
