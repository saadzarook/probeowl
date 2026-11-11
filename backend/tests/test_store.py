# Author: @SaadZarook
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, delete, inspect, text, update
from sqlalchemy.exc import StatementError

from probeowl.errors import ConfigError, GuardViolation, NotFoundError
from probeowl.model import Actor, Run, RunStatus
from probeowl.model.schema import ColumnSchema, SchemaSnapshot, TableSchema
from probeowl.store.audit import AuditEvent, append_event, list_events
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import DIALECTS, VERSION_TABLE, check_current, ddl, upgrade
from probeowl.store.runs import get_run, list_runs, save_run
from probeowl.store.schemas import latest_snapshot, latest_snapshots, save_snapshot
from probeowl.store.tables import LOCAL_WORKSPACE, audit_events, metadata
from tests.store_databases import STORE_DATABASES, store_database_url

T0 = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)


@pytest.fixture(
    scope="session",
    params=["sqlite", *(pytest.param(n, marks=pytest.mark.database) for n in STORE_DATABASES)],
)
def store_url(request: pytest.FixtureRequest) -> Iterator[str | None]:
    # One container per database for the whole session; SQLite gets a file per test.
    if request.param == "sqlite":
        yield None
        return
    with store_database_url(request.param) as url:
        yield url


@pytest.fixture
def engine(store_url: str | None, tmp_path: Path) -> Iterator[Engine]:
    store = create_store_engine(store_url or f"sqlite:///{tmp_path / 'store.db'}")
    _drop_everything(store)
    upgrade(store)
    yield store
    store.dispose()


def _drop_everything(store: Engine) -> None:
    # Containers live for the session; each test starts from an empty store.
    with store.begin() as conn:
        metadata.drop_all(conn)
        if inspect(conn).has_table(VERSION_TABLE):
            conn.execute(text(f"DROP TABLE {VERSION_TABLE}"))


def run_data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "6f1c2a9e-0000-4000-8000-000000008812",
        "application": "wallet",
        "environment": "prod",
        "probe": "topup_completed",
        "kind": "validate",
        "probe_version": "v3",
        "entity": "topup",
        "identifier": "topup_id",
        "identifier_value": "tp_91f2c7",
        "subject": "tp_91f2c7",
        "status": "failed",
        "caveat": "replica lag unknown: 1 Missing result may not have arrived yet",
        "actor": {"kind": "person", "name": "s.khan"},
        "investigation": {"reason": "incident", "reference": "WALLET-482"},
        "started_at": T0,
        "finished_at": datetime(2026, 10, 31, 9, 0, 3, tzinfo=UTC),
        "locations": [
            {
                "location": "record",
                "outcome": "found",
                "evidence": {
                    "connector": "wallet_db",
                    "query": "SELECT TOP (2) id FROM topups WHERE id = @topup_id",
                    "parameters": {"topup_id": "tp_91f2c7"},
                    "guards": ["SET LOCK_TIMEOUT 0"],
                    "rows": [{"id": "tp_91f2c7", "customer_email": "c***@example.com"}],
                    "started_at": T0,
                    "duration_ms": 12,
                },
            },
            {
                "location": "ledger",
                "outcome": "missing",
                "reason": "0 rows",
                "caveat": "replica lag unknown, the record may not have arrived yet",
            },
            {"location": "controller", "parent": "cards", "row": 1, "outcome": "found"},
        ],
        "expectations": [
            {
                "step": 3,
                "expectation": "count(ledger) == 2",
                "result": "fail",
                "detail": "0",
                "caveat": "reads a Missing record that may not have arrived yet",
            }
        ],
    }
    return data | changes


def test_migration_matches_the_tables(engine: Engine) -> None:
    with engine.connect() as conn:
        # Only ProbeOwl's tables: SQL Server's master database has system tables of its own.
        ours = {*metadata.tables, VERSION_TABLE}
        context = MigrationContext.configure(
            conn,
            opts={
                "version_table": VERSION_TABLE,
                "include_name": lambda name, type_, parent: type_ != "table" or name in ours,
            },
        )
        assert compare_metadata(context, metadata) == []


def test_every_table_starts_its_primary_key_with_workspace_id(engine: Engine) -> None:
    inspector = inspect(engine)
    for table in metadata.tables:
        columns = inspector.get_pk_constraint(table)["constrained_columns"]
        assert columns[0] == "workspace_id", table


def test_check_current_before_and_after_init(tmp_path: Path) -> None:
    store = create_store_engine(f"sqlite:///{tmp_path / 'new.db'}")
    with pytest.raises(ConfigError, match="run: owl store init"):
        check_current(store)
    upgrade(store)
    check_current(store)
    store.dispose()


@pytest.mark.parametrize("dialect", DIALECTS)
def test_ddl_for_every_store_database(dialect: str) -> None:
    sql = ddl(dialect)
    for table in metadata.tables:
        assert f"CREATE TABLE {table}" in sql
    assert "probeowl_schema_version" in sql


def test_ddl_for_an_unknown_database_is_a_config_error() -> None:
    with pytest.raises(ConfigError, match="unknown store dialect 'db2'"):
        ddl("db2")


def test_run_round_trips(engine: Engine) -> None:
    run = Run.model_validate(run_data())
    with engine.begin() as conn:
        save_run(conn, LOCAL_WORKSPACE, run)
    with engine.connect() as conn:
        assert get_run(conn, LOCAL_WORKSPACE, run.id) == run


def test_running_run_is_saved_as_outcomes_arrive(engine: Engine) -> None:
    running = Run.model_validate(
        run_data(status="running", finished_at=None, locations=[], expectations=[])
    )
    finished = Run.model_validate(run_data())
    with engine.begin() as conn:
        save_run(conn, LOCAL_WORKSPACE, running)
    with engine.connect() as conn:
        assert get_run(conn, LOCAL_WORKSPACE, running.id).status is RunStatus.RUNNING
    with engine.begin() as conn:
        save_run(conn, LOCAL_WORKSPACE, finished)
    with engine.connect() as conn:
        assert get_run(conn, LOCAL_WORKSPACE, running.id) == finished


def test_unknown_run_is_not_found(engine: Engine) -> None:
    with engine.connect() as conn, pytest.raises(NotFoundError, match="no run with id x"):
        get_run(conn, LOCAL_WORKSPACE, "x")


def test_runs_are_separate_per_workspace(engine: Engine) -> None:
    run = Run.model_validate(run_data())
    with engine.begin() as conn:
        save_run(conn, "team-payments", run)
    with engine.connect() as conn, pytest.raises(NotFoundError):
        get_run(conn, LOCAL_WORKSPACE, run.id)


def test_runs_are_listed_newest_first_page_by_page(engine: Engine) -> None:
    def stored(run_id: str, minutes: int, workspace: str = LOCAL_WORKSPACE, **changes: Any) -> None:
        at = T0 + timedelta(minutes=minutes)
        run = Run.model_validate(
            run_data(id=run_id, started_at=at, finished_at=at + timedelta(seconds=3), **changes)
        )
        with engine.begin() as conn:
            save_run(conn, workspace, run)

    for minute in range(3):
        stored(f"run-{minute}", minute)
    stored("run-1b", 1)  # started with run-1: the id orders them
    stored("in-uat", 5, environment="uat")
    stored("candidate", 6, healthy_for="run-2")
    stored("draft", 7, probe_version="draft:sha256:abc")
    stored("other-team", 8, workspace="team-payments")

    def page(before: tuple[datetime, str] | None) -> list[str]:
        with engine.connect() as conn:
            return [r.id for r in list_runs(conn, LOCAL_WORKSPACE, "wallet", "prod", before, 3)]

    first = page(None)
    assert first == ["draft", "run-2", "run-1b"]
    with engine.connect() as conn:
        [draft] = list_runs(conn, LOCAL_WORKSPACE, "wallet", "prod", None, 1)
    assert (draft.draft, draft.reason, draft.reference, draft.actor) == (
        True,
        "incident",
        "WALLET-482",
        "s.khan",
    )
    assert draft.started_at == T0 + timedelta(minutes=7)
    assert page((T0 + timedelta(minutes=1), "run-1b")) == ["run-1", "run-0"]


def test_naive_timestamps_are_refused(engine: Engine) -> None:
    event = AuditEvent(
        action="run_started",
        actor=Actor(kind="person", name="s.khan"),
        occurred_at=datetime(2026, 10, 31, 9, 0),
    )
    with engine.begin() as conn, pytest.raises(StatementError, match="timezone-aware"):
        append_event(conn, LOCAL_WORKSPACE, event)


def audit_event(**changes: Any) -> AuditEvent:
    data: dict[str, Any] = {
        "action": "run_started",
        "actor": Actor(kind="person", name="s.khan"),
        "application": "wallet",
        "environment": "prod",
        "run_id": "8812",
        "details": {"reason": "incident", "reference": "WALLET-482"},
        "occurred_at": T0,
    }
    return AuditEvent(**(data | changes))


def test_audit_events_are_appended_and_listed(engine: Engine) -> None:
    first = audit_event()
    second = audit_event(
        action="write_query_copied", occurred_at=datetime(2026, 10, 31, 9, 5, tzinfo=UTC)
    )
    other_run = audit_event(run_id="9000")
    with engine.begin() as conn:
        for event in (second, first, other_run):
            append_event(conn, LOCAL_WORKSPACE, event)
    with engine.connect() as conn:
        assert list_events(conn, LOCAL_WORKSPACE, run_id="8812") == [first, second]
        assert list_events(conn, "team-payments") == []


@pytest.mark.parametrize(
    "statement",
    [
        update(audit_events).values(action="changed"),
        delete(audit_events),
        text("UPDATE audit_events SET action = 'changed'"),
        text("delete from AUDIT_EVENTS"),
        text('DELETE FROM "audit_events" WHERE 1 = 1'),
    ],
    ids=["core update", "core delete", "text update", "text delete", "quoted delete"],
)
def test_audit_is_append_only(engine: Engine, statement: Any) -> None:
    with engine.begin() as conn:
        append_event(conn, LOCAL_WORKSPACE, audit_event())
    with engine.begin() as conn, pytest.raises(GuardViolation, match="append-only"):
        conn.execute(statement)
    with engine.connect() as conn:
        assert [e.action for e in list_events(conn, LOCAL_WORKSPACE)] == ["run_started"]


def test_the_latest_schema_snapshot_per_connector_and_environment(engine: Engine) -> None:
    def snapshot(connector: str, environment: str, minutes: int, table: str) -> SchemaSnapshot:
        return SchemaSnapshot(
            application="wallet",
            connector=connector,
            environment=environment,
            imported_at=T0 + timedelta(minutes=minutes),
            tables=[
                TableSchema(
                    schema="dbo",
                    table=table,
                    columns=[ColumnSchema(name="id", type="varchar(20)", nullable=False)],
                    primary_key=["id"],
                )
            ],
        )

    with engine.begin() as conn:
        for found in (
            snapshot("wallet_db", "prod", 0, "old"),
            snapshot("wallet_db", "prod", 5, "new"),
            snapshot("wallet_db", "uat", 1, "uat_only"),
            snapshot("ledger_db", "prod", 2, "ledger_entries"),
        ):
            save_snapshot(conn, LOCAL_WORKSPACE, found)
        save_snapshot(conn, "other-workspace", snapshot("wallet_db", "prod", 9, "other"))
    with engine.connect() as conn:
        latest = latest_snapshots(conn, LOCAL_WORKSPACE, "wallet")
        one = latest_snapshot(conn, LOCAL_WORKSPACE, "wallet", "wallet_db", "prod")
    assert sorted((s.connector, s.environment, s.tables[0].table) for s in latest) == [
        ("ledger_db", "prod", "ledger_entries"),
        ("wallet_db", "prod", "new"),
        ("wallet_db", "uat", "uat_only"),
    ]
    assert one.tables[0].table == "new"
    assert one.imported_at == T0 + timedelta(minutes=5)
