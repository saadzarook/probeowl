# Author: @SaadZarook
"""The safety checks that need a real database, shared by each adapter's database tests.

Each builds a one-connector application around the test database, then goes through the same
code a person's request does: a runbook read step's plan check and its masked read, the drift
flag from a real import, and the Connectors screen's Test connection.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from probeowl.catalog.review import build_review
from probeowl.checks.plans import plan_checks
from probeowl.checks.rules import read_step_statement
from probeowl.connector_status import ConnectorStatuses
from probeowl.connectors.sql import SqlAdapter
from probeowl.guards.breaker import CircuitBreaker
from probeowl.model.application import Application, Environment
from probeowl.model.catalog import Catalog, CatalogTable, Column, ColumnRole, TableLabel
from probeowl.model.check import LoadedApplication
from probeowl.model.connector import SqlBinding, SqlConnector
from probeowl.model.connector_status import ConnectionTest
from probeowl.model.review import TableReview
from probeowl.model.run import Run
from probeowl.model.runbook import ReadQueryStep
from probeowl.model.runbook_step import StepResult
from probeowl.model.save_check import SaveCheck
from probeowl.model.schema import SchemaSnapshot
from probeowl.runbook_steps import RunbookSteps
from probeowl.runs import RunService
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade
from tests.test_store import run_data

ENV = "test"
CONNECTOR = "db"
APPLICATION = "native"


def catalog_table(
    table: str, columns: dict[str, Column], label: TableLabel = TableLabel.MASTER
) -> CatalogTable:
    """A confirmed catalog entry for one table."""
    return CatalogTable(
        connector=CONNECTOR, table=table, label=label, owner="test", columns=columns
    )


def roles(*marked: ColumnRole, indexed: bool | None = None) -> Column:
    """A catalog column with these roles."""
    return Column(roles=list(marked), indexed=indexed)


def application(kind: str, binding: SqlBinding, tables: list[CatalogTable]) -> LoadedApplication:
    """One application, one connector bound to the test database, and its catalog."""
    connector = SqlConnector(connector=CONNECTOR, type=kind, environments={ENV: binding})
    return LoadedApplication(
        application=Application(
            application=APPLICATION,
            title="Native",
            owner="test",
            environments={ENV: Environment(title="Test", production=False)},
        ),
        connectors={CONNECTOR: connector},
        entities={},
        probes={},
        catalog=Catalog(catalog=APPLICATION, tables=tables) if tables else None,
    )


def read_step(sql: str) -> ReadQueryStep:
    """A runbook read step on the test connector."""
    return ReadQueryStep(kind="read_query", id="read", title="Read", using=CONNECTOR, sql=sql)


def plan_check(app: LoadedApplication, sql: str, adapter: SqlAdapter) -> SaveCheck:
    """The plan check a runbook read step goes through before it runs, on the real plan."""
    statement = read_step_statement(app, read_step(sql))
    assert statement is not None
    [check] = plan_checks(app, [statement], ENV, lambda connector, environment: adapter)
    return check


@contextmanager
def run_service(app: LoadedApplication, adapter: SqlAdapter, folder: Path) -> Iterator[RunService]:
    """The run service, with a store of its own and this adapter as its only connector."""
    store: Engine = create_store_engine(f"sqlite:///{folder / 'store.db'}")
    upgrade(store)
    runs = RunService({APPLICATION: app}, store, opener=lambda connector, environment: adapter)
    try:
        yield runs
    finally:
        runs.shutdown()
        store.dispose()


def read_masked(
    app: LoadedApplication, sql: str, parameters: dict[str, Any], adapter: SqlAdapter, folder: Path
) -> StepResult:
    """A reviewed read step run as a person runs it: its rows masked by the catalog's PII."""
    run = Run.model_validate(run_data(application=APPLICATION, environment=ENV))
    with run_service(app, adapter, folder) as runs:
        # No entity here, so no PII identifier: the catalog's PII columns mask the rows.
        return RunbookSteps(runs, None)._read(run, app, set(), read_step(sql), parameters, [])


def review(app: LoadedApplication, adapter: SqlAdapter, table: str) -> TableReview:
    """The catalog review of one table, against a real import of the test database's schema."""
    tables, _ = adapter.import_schema()
    snapshot = SchemaSnapshot.model_validate(
        {
            "application": APPLICATION,
            "connector": CONNECTOR,
            "environment": ENV,
            "imported_at": "2026-11-08T10:00:00Z",
            "tables": [t.model_dump(by_alias=True) for t in tables],
        }
    )
    [found] = [t for t in build_review(app, [snapshot]).tables if t.table.lower() == table.lower()]
    return found


def connection_test(app: LoadedApplication, adapter: SqlAdapter) -> ConnectionTest:
    """The Connectors screen's Test connection, through this adapter."""
    statuses = ConnectorStatuses(
        {APPLICATION: app},
        CircuitBreaker(),
        lambda application, connector: None,
        connection_opener=lambda connector, environment: adapter,
    )
    return statuses.test_connection(APPLICATION, CONNECTOR, ENV)
