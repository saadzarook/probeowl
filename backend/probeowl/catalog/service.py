# Author: @SaadZarook
"""The catalog in owl serve: schema imports (stored and audited), and the review of tables."""

import getpass
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol

from pydantic import JsonValue
from sqlalchemy import Engine

from probeowl.catalog.edit import checked, with_table
from probeowl.catalog.review import build_review
from probeowl.config_folder import Change, ConfigFolder
from probeowl.connectors.factory import open_sql
from probeowl.connectors.sql import SqlLimits
from probeowl.errors import NotFoundError, RequestError, unknown_application
from probeowl.guards.kill_switch import KillSwitches
from probeowl.model.catalog import CatalogTable
from probeowl.model.check import LoadedApplication
from probeowl.model.connector import SqlConnector
from probeowl.model.review import CatalogReview
from probeowl.model.run import Actor, ActorKind
from probeowl.model.schema import SchemaSnapshot, TableSchema
from probeowl.store.audit import AuditEvent, append_event
from probeowl.store.schemas import latest_snapshot, latest_snapshots, save_snapshot
from probeowl.store.tables import LOCAL_WORKSPACE

# Catalog views on a large database answer slower than a keyed lookup.
IMPORT_LIMITS = SqlLimits(statement_timeout_s=30)
# Where a new catalog goes, in the application's folder.
CATALOG_FILE = "catalog.yaml"


class SchemaSource(Protocol):
    """What an import needs from a SQL connector."""

    def import_schema(self) -> tuple[list[TableSchema], str | None]:
        """The tables, and why some were left out, if any were."""
        ...

    def close(self) -> None:
        """Release the connection."""
        ...


Opener = Callable[[SqlConnector, str], SchemaSource | None]


def _open(connector: SqlConnector, environment: str) -> SchemaSource | None:
    return open_sql(connector, environment, limits=IMPORT_LIMITS)


class CatalogService:
    """The catalog of the applications owl serve loaded: imports, review and confirmations."""

    def __init__(
        self,
        applications: dict[str, LoadedApplication],
        store: Engine,
        opener: Opener = _open,
        actor_name: str | None = None,
        workspace_id: str = LOCAL_WORKSPACE,
        folder: ConfigFolder | None = None,
    ):
        self._applications = applications
        self._store = store
        self._opener = opener
        self._folder = folder
        self._actor = Actor(kind=ActorKind.PERSON, name=actor_name or getpass.getuser())
        self._workspace = workspace_id

    @property
    def applications(self) -> dict[str, LoadedApplication]:
        """The applications owl serve loaded, by name; confirmations update them in place."""
        return self._applications

    def run(self, application: str, connector: str, environment: str) -> SchemaSnapshot:
        """Import, store and audit one connector's schema; ConnectorError if it cannot be read."""
        definition = self._connector(application, connector, environment)
        KillSwitches(self._store, self._workspace).check(application, connector)
        source = self._opener(definition, environment)
        if source is None:
            raise RequestError(
                f"{connector} has no binding in {environment}",
                {"environment": f"bind {connector} in {environment} first"},
            )
        try:
            tables, truncated = source.import_schema()
        finally:
            source.close()
        snapshot = SchemaSnapshot(
            application=application,
            connector=connector,
            environment=environment,
            imported_at=datetime.now(UTC),
            tables=tables,
            truncated=truncated,
        )
        event = AuditEvent(
            action="schema_imported",
            actor=self._actor,
            application=application,
            environment=environment,
            details={"connector": connector, "tables": len(tables), "truncated": truncated},
        )
        with self._store.begin() as conn:
            save_snapshot(conn, self._workspace, snapshot)
            append_event(conn, self._workspace, event)
        return snapshot

    def latest(self, application: str, connector: str, environment: str) -> SchemaSnapshot:
        """The most recent import; NotFoundError if there has been none."""
        self._connector(application, connector, environment)
        with self._store.connect() as conn:
            return latest_snapshot(conn, self._workspace, application, connector, environment)

    def review(self, application: str) -> CatalogReview:
        """Suggested labels, drift per environment and probe usage for every known table."""
        app = self._applications.get(application)
        if app is None:
            raise unknown_application(application, self._applications)
        with self._store.connect() as conn:
            snapshots = latest_snapshots(conn, self._workspace, application)
        return build_review(app, snapshots)

    def confirm(self, application: str, entry: CatalogTable, base: str | None) -> Change:
        """Confirm a table's entry into catalog.yaml: without base, the change it would make;
        with the base its preview gave, written, then audited."""
        app = self._applications.get(application)
        if app is None:
            raise unknown_application(application, self._applications)
        if self._folder is None or app.folder is None:
            raise RequestError("start owl serve with --config <folder> to write the catalog")
        if entry.connector not in app.connectors:
            raise RequestError(
                f"{application} has no connector {entry.connector}",
                {"entry.connector": f"one of: {', '.join(sorted(app.connectors))}"},
            )
        path = app.catalog_path or app.folder / CATALOG_FILE
        relative = self._folder.relative(path)
        with self._folder.lock:
            target = self._folder.resolve(relative)
            current = target.read_text(encoding="utf-8") if target.exists() else None
            text = with_table(current, application, entry)
            catalog = checked(text, application, set(app.connectors))
            preview = self._folder.preview(relative, text)
            if base is None:
                return preview
            details: dict[str, JsonValue] = {
                "path": preview.path,
                "connector": entry.connector,
                "table": entry.table,
                "label": entry.label.value,
                "base": base,
                "after": preview.after,
            }
            # The audit is the write's last step: if it cannot be written, the file is put back.
            [change] = self._folder.write_all(
                [(relative, text, base)],
                lambda: self._audit("catalog_table_confirmed", application, details),
            )
            # What runs and reviews see from now on; the folder is not reloaded as a whole.
            self._applications[application] = replace(
                self._applications[application], catalog=catalog, catalog_path=target
            )
        return change

    def _audit(self, action: str, application: str, details: dict[str, JsonValue]) -> None:
        event = AuditEvent(
            action=action, actor=self._actor, application=application, details=details
        )
        with self._store.begin() as conn:
            append_event(conn, self._workspace, event)

    def _connector(self, application: str, connector: str, environment: str) -> SqlConnector:
        app = self._applications.get(application)
        if app is None:
            raise unknown_application(application, self._applications)
        if environment not in app.application.environments:
            raise NotFoundError(f"{application} has no environment {environment}")
        definition = app.connectors.get(connector)
        if definition is None:
            raise NotFoundError(f"{application} has no connector named {connector}")
        if not isinstance(definition, SqlConnector):
            raise RequestError(
                f"{connector} is a REST connector: only SQL connectors have a schema to import",
                {"connector": "choose a SQL Server, Oracle or MariaDB connector"},
            )
        return definition
