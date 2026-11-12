# Author: @SaadZarook
"""The Oracle adapter against a real database: locks, the call timeout, types and names.

Runs in CI against Oracle Free in a container, or locally against PROBEOWL_TEST_ORACLE_URL;
skips otherwise.
"""

import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import oracledb
import pytest
from sqlalchemy.engine import make_url

from probeowl.connectors.oracle import OracleAdapter
from probeowl.connectors.plan import Access
from probeowl.connectors.sql import SqlLimits
from probeowl.engine.masking import MASKED
from probeowl.errors import ConnectorError, GuardViolation, MissingGrant
from probeowl.model import SqlBinding
from probeowl.model.catalog import ColumnRole
from probeowl.model.check import load_applications
from probeowl.model.connector_status import PlanAccess
from probeowl.model.run import Outcome
from probeowl.model.runbook import ReadQueryStep
from probeowl.model.save_check import CheckStatus
from tests import native_safety as safety
from tests.store_databases import check_test_database, store_database_url

pytestmark = pytest.mark.database

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

ROWS_FOR_ONE_ACCOUNT = 30
# Natively, the tests' own setup, checks and cleanup connect as a login of their own, and ProbeOwl
# as one with only what a Production read login would have, plus the store tests' DDL in its own
# schema. CI's container has one test user, for both.
ADMIN_URL = "PROBEOWL_TEST_ORACLE_ADMIN_URL"


@dataclass(frozen=True)
class Database:
    binding: SqlBinding
    password: str
    table: str
    admin_user: str
    admin_password: str

    def writer(self) -> Any:
        """The tests' own session, for setup, cleanup, checks and holding locks."""
        session = oracledb.connect(
            user=self.admin_user,
            password=self.admin_password,
            host=self.binding.host,
            port=self.binding.port,
            service_name=self.binding.database,
        )
        # Unqualified names mean the tables in ProbeOwl's schema, as they do for ProbeOwl.
        session.cursor().execute(f"ALTER SESSION SET CURRENT_SCHEMA = {self.binding.user}")
        return session

    def adapter(self, timeout_s: int = 2) -> OracleAdapter:
        return OracleAdapter(self.binding, self.password, SqlLimits(statement_timeout_s=timeout_s))


@pytest.fixture(scope="module")
def database() -> Iterator[Database]:
    with store_database_url("oracle") as url:
        parsed = make_url(url)
        user = parsed.username or "probeowl"
        binding = SqlBinding.model_validate(
            {
                "host": parsed.host or "127.0.0.1",
                "port": parsed.port or 1521,
                "database": str(parsed.query.get("service_name", "FREEPDB1")),
                # The user's own schema, set explicitly so CURRENT_SCHEMA is exercised.
                "schema": user,
                "user": user,
                "secret": "env:PROBEOWL_TEST_ORACLE_URL",
                # Oracle Free has no tcps listener.
                "encrypt": False,
            }
        )
        password = parsed.password or ""
        admin_user, admin_password = user, password
        if os.environ.get(ADMIN_URL):
            check_test_database(os.environ[ADMIN_URL])
            admin_url = make_url(os.environ[ADMIN_URL])
            admin_user, admin_password = admin_url.username or "", admin_url.password or ""
        db = Database(
            binding,
            password,
            f"ledger_entries_{uuid.uuid4().hex[:8]}",
            admin_user,
            admin_password,
        )
        writer = db.writer()
        cursor = writer.cursor()
        cursor.execute(
            f"CREATE TABLE {db.table} (txn_id VARCHAR2(20) NOT NULL, "
            "direction VARCHAR2(6) NOT NULL, account VARCHAR2(30) NOT NULL, "
            "amount NUMBER(18, 2) NOT NULL, note CLOB, "
            "CONSTRAINT pk_" + db.table[-8:] + " PRIMARY KEY (txn_id, direction))"
        )
        cursor.executemany(
            f"INSERT INTO {db.table} (txn_id, direction, account, amount, note) "
            "VALUES (:1, :2, :3, :4, :5)",
            [
                ("lt_55e1a0", "credit", "wallet:u_1", Decimal("500.10"), "first"),
                ("lt_55e1a0", "debit", "provider:clearing", Decimal("500.10"), None),
            ]
            + [
                (f"lt_{i:06d}", "credit", "wallet:u_2", Decimal("1.00"), None)
                for i in range(ROWS_FOR_ONE_ACCOUNT)
            ],
        )
        writer.commit()
        writer.close()
        try:
            yield db
        finally:
            writer = db.writer()
            writer.cursor().execute(f"DROP TABLE {db.table} PURGE")
            writer.close()


def test_oracle_lookup_returns_lowercase_names_and_exact_numbers(database: Database) -> None:
    adapter = database.adapter()
    result = adapter.lookup(
        f"SELECT account, direction, amount, note FROM {database.table} "
        "WHERE txn_id = :ledger_txn_id ORDER BY direction",
        {"ledger_txn_id": "lt_55e1a0"},
        max_rows=20,
    )
    adapter.close()
    assert result.rows == [
        {
            "account": "wallet:u_1",
            "direction": "credit",
            "amount": Decimal("500.1"),
            "note": "first",
        },
        {
            "account": "provider:clearing",
            "direction": "debit",
            "amount": Decimal("500.1"),
            "note": None,
        },
    ]
    assert result.query.endswith("FETCH FIRST 21 ROWS ONLY")
    assert result.parameters == {"ledger_txn_id": "lt_55e1a0"}
    assert "call timeout 2 s" in result.guards


def test_oracle_more_rows_than_allowed_are_reported(database: Database) -> None:
    adapter = database.adapter()
    result = adapter.lookup(
        f"SELECT txn_id FROM {database.table} WHERE account = :account ORDER BY txn_id",
        {"account": "wallet:u_2"},
        max_rows=5,
    )
    adapter.close()
    assert len(result.rows) == 5
    assert result.truncated == "more than 5 rows"
    assert result.rows_total == ROWS_FOR_ONE_ACCOUNT  # counted, for "showing 5 of N"
    assert any(g.startswith("rows counted with: SELECT COUNT(*)") for g in result.guards)


# What the time offset save check accepts as converting a +04:00 value explicitly, against a
# plain TIMESTAMP: the check's Oracle rules rest on these.
ORACLE_OFFSET_FORMS = {
    # The driver cannot send the +04:00 itself, so the value arrives as its UTC instant: a plain
    # CAST gives its UTC time, and AT TIME ZONE the clock time in the zone named.
    "at = CAST(:at AS TIMESTAMP)": "utc_clock",
    "at = SYS_EXTRACT_UTC(:at)": "utc_clock",
    "at = CAST(:at AT TIME ZONE '+04:00' AS TIMESTAMP)": "own_clock",
}


def test_oracle_what_each_conversion_of_a_plus_four_time_matches(database: Database) -> None:
    table = f"probe_times_{uuid.uuid4().hex[:8]}"
    writer = database.writer()
    cursor = writer.cursor()
    cursor.execute(f"CREATE TABLE {table} (id varchar2(20) PRIMARY KEY, at TIMESTAMP)")
    cursor.execute(
        f"INSERT ALL INTO {table} VALUES ('own_clock', TIMESTAMP '2026-11-07 14:00:00') "
        f"INTO {table} VALUES ('utc_clock', TIMESTAMP '2026-11-07 10:00:00') SELECT 1 FROM dual"
    )
    writer.commit()
    adapter = database.adapter()
    at = datetime(2026, 11, 7, 14, 0, tzinfo=timezone(timedelta(hours=4)))
    matched: dict[str, str] = {}
    try:
        for where in ORACLE_OFFSET_FORMS:
            rows = adapter.lookup(f"SELECT id FROM {table} WHERE {where}", {"at": at}, 2).rows
            matched[where] = ",".join(str(row["id"]) for row in rows)
    finally:
        adapter.close()
        cursor.execute(f"DROP TABLE {table} PURGE")
        writer.close()
    assert matched == ORACLE_OFFSET_FORMS


# The other way round: 14:00 without an offset against a TIMESTAMP WITH TIME ZONE column, holding
# 14:00 +04:00 and 14:00 +00:00. FROM_TZ gives the value its offset; the plain comparison and
# AT TIME ZONE depend on the session's time zone, so the check accepts neither.
ORACLE_REVERSE_FORMS = {
    "at = FROM_TZ(:at, '+04:00')": "plus_four",
    "at = FROM_TZ(CAST(:at AS TIMESTAMP), '+00:00')": "utc",
}


def test_oracle_what_each_conversion_of_a_time_without_an_offset_matches(
    database: Database,
) -> None:
    table = f"probe_times_{uuid.uuid4().hex[:8]}"
    writer = database.writer()
    cursor = writer.cursor()
    cursor.execute(
        f"CREATE TABLE {table} (id varchar2(20) PRIMARY KEY, at TIMESTAMP WITH TIME ZONE)"
    )
    cursor.execute(
        f"INSERT ALL INTO {table} VALUES ('plus_four', TIMESTAMP '2026-11-07 14:00:00 +04:00') "
        f"INTO {table} VALUES ('utc', TIMESTAMP '2026-11-07 14:00:00 +00:00') SELECT 1 FROM dual"
    )
    writer.commit()
    adapter = database.adapter()
    matched: dict[str, str] = {}
    try:
        for where in ORACLE_REVERSE_FORMS:
            rows = adapter.lookup(
                f"SELECT id FROM {table} WHERE {where}", {"at": datetime(2026, 11, 7, 14, 0)}, 2
            ).rows
            matched[where] = ",".join(str(row["id"]) for row in rows)
    finally:
        adapter.close()
        cursor.execute(f"DROP TABLE {table} PURGE")
        writer.close()
    assert matched == ORACLE_REVERSE_FORMS


def test_oracle_a_lookup_runs_exactly_as_written(database: Database) -> None:
    sql = (
        f"select txn_id /* why */\nfrom {database.table}\twhere txn_id = :id "
        "and direction = 'credit' -- note"
    )
    adapter = database.adapter()
    result = adapter.lookup(sql, {"id": "lt_55e1a0"}, 1)
    adapter.close()
    # The cap goes before the trailing comment, so the comment cannot swallow it.
    assert result.query == (
        f"select txn_id /* why */\nfrom {database.table}\twhere txn_id = :id "
        "and direction = 'credit' FETCH FIRST 2 ROWS ONLY -- note"
    )
    assert result.rows == [{"txn_id": "lt_55e1a0"}]


PLUS_FOUR = timezone(timedelta(hours=4))


def test_oracle_a_time_with_an_offset_arrives_as_its_exact_instant(database: Database) -> None:
    # Thin mode would send 14:00 +04:00 as 14:00 +00:00, four hours off.
    adapter = database.adapter()
    result = adapter.lookup(
        "SELECT TO_CHAR(SYS_EXTRACT_UTC(:at), 'YYYY-MM-DD HH24:MI') AS utc FROM dual",
        {"at": datetime(2026, 11, 7, 14, 0, tzinfo=PLUS_FOUR)},
        1,
    )
    adapter.close()
    assert result.rows == [{"utc": "2026-11-07 10:00"}]


def test_oracle_the_session_time_zone_is_the_databases_own_by_default(database: Database) -> None:
    # Never this machine's: thin mode would set it from the process's local time zone.
    adapter = database.adapter()
    result = adapter.lookup(
        "SELECT SESSIONTIMEZONE AS session_tz, DBTIMEZONE AS db_tz FROM dual", {}, 1
    )
    adapter.close()
    [row] = result.rows
    assert row["session_tz"] == row["db_tz"]
    assert f"TIME_ZONE {row['db_tz']} (the database's own)" in result.guards


def test_oracle_the_bindings_time_zone_is_the_sessions(database: Database) -> None:
    binding = database.binding.model_copy(update={"time_zone": "+04:00"})
    adapter = OracleAdapter(binding, database.password, SqlLimits(statement_timeout_s=2))
    result = adapter.lookup(
        "SELECT SESSIONTIMEZONE AS session_tz, "
        "TO_CHAR(CAST(TIMESTAMP '2026-11-07 14:00:00' AS TIMESTAMP WITH TIME ZONE), 'TZH:TZM') "
        "AS assumed FROM dual",
        {},
        1,
    )
    adapter.close()
    # A TIMESTAMP compared with a time with an offset is read in this zone.
    assert result.rows == [{"session_tz": "+04:00", "assumed": "+04:00"}]
    assert "TIME_ZONE +04:00 (from the binding)" in result.guards


@pytest.mark.parametrize(
    "value",
    [
        "FROM_TZ(TIMESTAMP '2026-11-07 14:00:00', '+04:00')",
        # A named zone: thin mode would fail reading it (DPY-3022); it never gets that far.
        "FROM_TZ(TIMESTAMP '2026-11-07 14:00:00', 'Asia/Dubai')",
    ],
)
def test_oracle_a_time_with_time_zone_column_is_an_error_not_a_clock_time(
    database: Database, value: str
) -> None:
    adapter = database.adapter()
    with pytest.raises(ConnectorError) as refused:
        adapter.lookup(f"SELECT {value} AS posted_at FROM dual", {}, 1)
    adapter.close()
    assert str(refused.value) == (
        "posted_at is TIMESTAMP WITH TIME ZONE, which ProbeOwl cannot read with its offset: "
        "select SYS_EXTRACT_UTC(posted_at) for its UTC instant, or "
        "TO_CHAR(posted_at, 'YYYY-MM-DD HH24:MI:SS TZH:TZM')"
    )


def test_oracle_sys_extract_utc_reads_a_time_with_time_zone_exactly(database: Database) -> None:
    adapter = database.adapter()
    result = adapter.lookup(
        "SELECT SYS_EXTRACT_UTC(FROM_TZ(TIMESTAMP '2026-11-07 14:00:00', '+04:00')) AS at "
        "FROM dual",
        {},
        1,
    )
    adapter.close()
    assert result.rows == [{"at": datetime(2026, 11, 7, 10, 0)}]


def test_oracle_the_binding_schema_is_the_current_schema(database: Database) -> None:
    adapter = database.adapter()
    result = adapter.lookup(
        "SELECT SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA') AS current_schema FROM dual", {}, 1
    )
    adapter.close()
    assert result.rows == [{"current_schema": database.binding.user.upper()}]
    assert f"CURRENT_SCHEMA {database.binding.user.upper()}" in result.guards


def test_oracle_a_locked_row_never_makes_the_probe_wait(database: Database) -> None:
    writer = database.writer()
    # The writer's open transaction holds the row lock and an uncommitted change.
    writer.cursor().execute(
        f"UPDATE {database.table} SET amount = 999 "
        "WHERE txn_id = 'lt_55e1a0' AND direction = 'credit'"
    )
    adapter = database.adapter()
    try:
        start = time.monotonic()
        result = adapter.lookup(
            f"SELECT amount FROM {database.table} WHERE txn_id = :id AND direction = 'credit'",
            {"id": "lt_55e1a0"},
            1,
        )
        assert time.monotonic() - start < 1.5
        # The probe reads the last committed value, never the writer's uncommitted one.
        assert result.rows == [{"amount": Decimal("500.1")}]
    finally:
        writer.rollback()
        writer.close()
        adapter.close()


def test_oracle_the_call_timeout_stops_a_running_statement(database: Database) -> None:
    # 100 levels, cross-joined five times: 10^10 rows to count, which never finishes in time.
    runaway = (
        "WITH r (n) AS (SELECT 1 FROM dual UNION ALL SELECT n + 1 FROM r WHERE n < 100) "
        "SELECT COUNT(*) AS c FROM r a CROSS JOIN r b CROSS JOIN r c CROSS JOIN r d CROSS JOIN r e"
    )
    adapter = database.adapter(timeout_s=2)
    start = time.monotonic()
    with pytest.raises(ConnectorError, match="timed out after 2 s"):
        adapter.lookup(runaway, {}, 1)
    assert time.monotonic() - start < 10
    # The adapter recovers on a fresh connection.
    assert adapter.lookup("SELECT 1 AS one FROM dual", {}, 1).rows == [{"one": Decimal(1)}]
    adapter.close()


def _plan_operations(connection: Any, sql: str) -> list[str]:
    statement_id = uuid.uuid4().hex[:20]
    cursor = connection.cursor()
    cursor.execute(f"EXPLAIN PLAN SET STATEMENT_ID = '{statement_id}' FOR {sql}")
    cursor.execute(
        "SELECT operation FROM plan_table WHERE statement_id = :id ORDER BY id",
        {"id": statement_id},
    )
    return [str(row[0]) for row in cursor.fetchall()]


def test_oracle_a_table_with_a_parallel_degree_is_read_serially(database: Database) -> None:
    table = f"probe_parallel_{uuid.uuid4().hex[:8]}"
    writer = database.writer()
    cursor = writer.cursor()
    cursor.execute(
        f"CREATE TABLE {table} PARALLEL 4 AS SELECT LEVEL AS n FROM dual CONNECT BY LEVEL <= 5000"
    )
    sql = f"SELECT COUNT(*) AS c FROM {table}"
    adapter = database.adapter()
    try:
        # The control: an ordinary session must plan this table in parallel, or the comparison
        # proves nothing. Oracle Free never does, not even with a PARALLEL hint, so on Free
        # this test skips; on Enterprise Edition (PROBEOWL_TEST_ORACLE_URL) it runs.
        ordinary = _plan_operations(writer, sql)
        if not any(op.startswith("PX") for op in ordinary):
            pytest.skip(
                f"this database plans no parallel query even for a PARALLEL 4 table ({ordinary}),"
                " so it cannot show the probe reads serially; run against Enterprise Edition"
            )
        result = adapter.lookup(sql, {}, 1)
        assert result.rows == [{"c": Decimal(5000)}]
        assert "ALTER SESSION DISABLE PARALLEL QUERY" in result.guards
        # The probe's own session plans the same query with no parallel step at all.
        assert not any(op.startswith("PX") for op in _plan_operations(adapter._connection, sql))
    finally:
        adapter.close()
        cursor.execute(f"DROP TABLE {table} PURGE")
        writer.close()


def _pq_status(control: Any, session: Any) -> str:
    """session's parallel query status, read by control: ProbeOwl's login cannot read v$session."""
    cursor = session.cursor()
    cursor.execute("SELECT SYS_CONTEXT('USERENV', 'SID') FROM dual")
    sid = int(cursor.fetchone()[0])
    cursor = control.cursor()
    cursor.execute("SELECT pq_status FROM v$session WHERE sid = :sid", {"sid": sid})
    return str(cursor.fetchone()[0])


def test_oracle_the_probe_session_has_parallel_query_disabled(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The proof that works on Oracle Free. Free disables parallel query in every session by
    # default, so each session here starts with it enabled, as on a database whose sessions
    # default to ENABLED; only the adapter's own ALTER SESSION can then disable it.
    connect = oracledb.connect

    def connect_enabled(*args: Any, **kwargs: Any) -> Any:
        session = connect(*args, **kwargs)
        session.cursor().execute("ALTER SESSION ENABLE PARALLEL QUERY")
        return session

    monkeypatch.setattr(oracledb, "connect", connect_enabled)
    control = database.writer()
    adapter = database.adapter()
    try:
        try:
            status = _pq_status(control, control)
        except oracledb.DatabaseError as err:
            if "ORA-00942" not in str(err):
                raise
            pytest.skip(
                "the tests' login cannot read v$session (CI grants it to its one test login; "
                "create_test_users.sh oracle to probeowl_test_admin), so the probe session's "
                "parallel query status cannot be read"
            )
        # The control: the status must follow the session setting, or DISABLED proves nothing.
        # Oracle Free keeps parallel query disabled in every session, even after ENABLE.
        if status != "ENABLED":
            pytest.skip(
                f"this database reports pq_status {status} even after ALTER SESSION ENABLE "
                "PARALLEL QUERY, so it cannot show the probe disabled it; run against "
                "Enterprise Edition"
            )
        adapter.lookup("SELECT 1 AS one FROM dual", {}, 1)
        assert _pq_status(control, adapter._connection) == "DISABLED"
    finally:
        adapter.close()
        control.close()


def test_oracle_an_authors_parallel_hint_never_reaches_the_database(database: Database) -> None:
    adapter = database.adapter()
    with pytest.raises(GuardViolation, match="an optimizer hint"):
        adapter.lookup(f"SELECT /*+ PARALLEL(8) */ COUNT(*) AS c FROM {database.table}", {}, 1)
    # Anywhere else it is refused too: the SQL runs as written, so a hint is never sent.
    with pytest.raises(GuardViolation, match="comment the database would run"):
        adapter.lookup(f"SELECT COUNT(*) AS c /*+ PARALLEL(8) */ FROM {database.table}", {}, 1)
    adapter.close()


def test_oracle_probeowl_connects_with_only_its_own_schemas_rights(database: Database) -> None:
    if database.admin_user == database.binding.user:
        pytest.skip(
            f"ProbeOwl and the checks connect as one login ({ADMIN_URL} is not set), as in CI's "
            "container, which has one test user"
        )
    adapter = database.adapter()
    adapter.lookup("SELECT 1 AS one FROM dual", {}, 1)
    cursor = adapter._connection.cursor()
    cursor.execute("SELECT privilege FROM session_privs")
    privileges = {str(row[0]) for row in cursor.fetchall()}
    # Every right on another schema's objects, such as v$session, which the parallel query test
    # reads as the tests' own login.
    cursor.execute("SELECT owner, table_name, privilege FROM user_tab_privs WHERE grantee = USER")
    grants = cursor.fetchall()
    adapter.close()
    # Signing in, and the store tests' tables and sequences in its own schema: no ANY privilege.
    assert privileges <= {"CREATE SESSION", "CREATE TABLE", "CREATE SEQUENCE"}, privileges
    assert grants == [], grants


def test_oracle_the_tests_own_login_has_its_any_privileges_on_one_schema(
    database: Database,
) -> None:
    if database.admin_user == database.binding.user:
        pytest.skip(
            f"ProbeOwl and the checks connect as one login ({ADMIN_URL} is not set), as in CI's "
            "container, which has one test user"
        )
    writer = database.writer()
    cursor = writer.cursor()
    cursor.execute("SELECT privilege FROM session_privs WHERE privilege LIKE '% ANY %'")
    everywhere = [str(row[0]) for row in cursor.fetchall()]
    # 23ai's schema privileges: what it holds on ProbeOwl's schema, and on no other.
    cursor.execute("SELECT DISTINCT schema FROM user_schema_privs")
    schemas = [str(row[0]) for row in cursor.fetchall()]
    writer.close()
    assert everywhere == [], everywhere
    assert schemas == [database.binding.user.upper()], schemas


def test_oracle_a_wrong_password_is_a_connector_error_without_the_password(
    database: Database,
) -> None:
    adapter = OracleAdapter(database.binding, "Wrong-s3cret-pw")
    with pytest.raises(ConnectorError) as failure:
        adapter.lookup("SELECT 1 AS one FROM dual", {}, 1)
    assert "Wrong-s3cret-pw" not in str(failure.value)


def test_oracle_by_default_connects_only_over_tcps(database: Database) -> None:
    # The container listens on plain tcp only: a default binding refuses to fall back.
    binding = database.binding.model_copy(update={"encrypt": True})
    adapter = OracleAdapter(binding, database.password)
    start = time.monotonic()
    with pytest.raises(ConnectorError, match="could not be made over TLS"):
        adapter.lookup("SELECT 1 AS one FROM dual", {}, 1)
    assert time.monotonic() - start < 15


def test_oracle_evidence_says_the_connection_is_not_encrypted(database: Database) -> None:
    adapter = database.adapter()
    result = adapter.lookup("SELECT 1 AS one FROM dual", {}, 1)
    adapter.close()
    assert "not encrypted" in result.guards


def test_oracle_a_database_that_is_not_a_standby_reports_its_lag_unknown(
    database: Database,
) -> None:
    adapter = database.adapter()
    lag = adapter.replica_lag()
    assert lag.seconds is None
    assert lag.note.startswith("replica lag unknown: ")
    assert adapter.lookup("SELECT 1 AS one FROM dual", {}, 1).rows == [{"one": 1}]
    adapter.close()


def test_oracle_import_reads_the_schema_never_data(database: Database) -> None:
    # Oracle's ALL_* views answer slower than a keyed lookup: imports allow 30 s.
    adapter = database.adapter(timeout_s=30)
    tables, _ = adapter.import_schema()
    adapter.close()
    [entries] = [t for t in tables if t.table == database.table]
    assert entries.db_schema == database.binding.user.lower()
    assert [(c.name, c.type, c.nullable) for c in entries.columns] == [
        ("txn_id", "varchar2(20)", False),
        ("direction", "varchar2(6)", False),
        ("account", "varchar2(30)", False),
        ("amount", "number(18,2)", False),
        ("note", "clob", True),
    ]
    assert entries.primary_key == ["txn_id", "direction"]
    assert "lt_55e1a0" not in str(tables)


def test_oracle_plan_explain_plan_shows_index_use_and_leaves_nothing(database: Database) -> None:
    adapter = database.adapter(timeout_s=10)
    by_key = adapter.explain(
        f"SELECT amount FROM {database.table} WHERE txn_id = :t AND direction = :d",
        {"t": "lt_55e1a0", "d": "credit"},
    )
    by_account = adapter.explain(
        f"SELECT txn_id FROM {database.table} WHERE account = :a", {"a": "wallet:u_2"}
    )
    assert [s.access for s in by_key if s.access is not Access.OTHER] == [Access.INDEX]
    [scan] = [s for s in by_account if s.access is Access.SCAN]
    assert scan.table == database.table
    # Rolled back: the session's plan table keeps nothing of either plan.
    cursor = adapter._connection.cursor()
    cursor.execute("SELECT COUNT(*) FROM plan_table WHERE statement_id LIKE 'probeowl-%'")
    assert cursor.fetchone()[0] == 0
    adapter.close()


def test_oracle_a_login_that_can_read_plans_is_told_so(database: Database) -> None:
    adapter = database.adapter(timeout_s=10)
    assert adapter.unreachable() is None
    assert adapter.plan_access() == (PlanAccess.READABLE, None)
    adapter.close()


def test_oracle_an_unreachable_server_says_why_but_not_where(database: Database) -> None:
    binding = database.binding.model_copy(update={"port": 1})
    reason = OracleAdapter(binding, database.password).unreachable()
    assert reason is not None
    assert database.binding.host not in reason and database.binding.user not in reason


# --- Safety on a real server: runbook reads, drift, Test connection, v$session ----------------
# The rest of each rule is tested with fakes; these are the parts a real Oracle decides.

PEOPLE = 5000  # past SMALL_TABLE_ROWS, so a whole-table read is a large one


def _people(database: Database) -> str:
    table = f"probe_people_{uuid.uuid4().hex[:8]}"
    writer = database.writer()
    cursor = writer.cursor()
    cursor.execute(
        f"CREATE TABLE {table} (id varchar2(20) NOT NULL, status varchar2(16) NOT NULL, "
        "email varchar2(100) NOT NULL, created_at TIMESTAMP NOT NULL, "
        f"CONSTRAINT pk_{table[-8:]} PRIMARY KEY (id))"
    )
    cursor.execute(
        f"INSERT INTO {table} SELECT 'p_' || LPAD(LEVEL, 6, '0'), "
        "CASE WHEN MOD(LEVEL, 10) = 0 THEN 'inactive' ELSE 'active' END, "
        "'person' || LEVEL || '@example.com', "
        "TIMESTAMP '2026-11-01 00:00:00' + NUMTODSINTERVAL(LEVEL, 'MINUTE') "
        f"FROM dual CONNECT BY LEVEL <= {PEOPLE}"
    )
    writer.commit()
    writer.close()
    return table


def _drop(database: Database, table: str) -> None:
    writer = database.writer()
    writer.cursor().execute(f"DROP TABLE {table} PURGE")
    writer.close()


def test_oracle_a_runbook_read_on_a_full_scan_is_refused_and_a_keyed_one_masked(
    database: Database, tmp_path: Path
) -> None:
    table = _people(database)
    entry = safety.catalog_table(
        table, {"id": safety.roles(ColumnRole.PRIMARY_KEY), "email": safety.roles(ColumnRole.PII)}
    )
    app = safety.application("oracle", database.binding, [entry])
    try:
        scan_sql = f"SELECT id FROM {table} WHERE status = 'inactive'"
        scan = safety.plan_check(app, scan_sql, database.adapter())
        keyed_sql = f"SELECT id, email FROM {table} WHERE id = :id"
        keyed = safety.plan_check(app, keyed_sql, database.adapter())
        result = safety.read_masked(
            app, keyed_sql, {"id": "p_000042"}, database.adapter(), tmp_path
        )
    finally:
        _drop(database, table)
    assert scan.status is CheckStatus.FAIL, scan.message
    assert f"the lookup would read all of {table}" in scan.message
    assert keyed.status is CheckStatus.PASS, keyed.message
    # Oracle returns EMAIL; the catalog says email: masked all the same.
    assert result.outcome is Outcome.FOUND
    assert result.evidence is not None
    assert result.evidence.rows == [{"id": "p_000042", "email": MASKED}]


def test_oracle_the_drift_flag_reads_a_real_import(database: Database) -> None:
    table = _people(database)
    wrong = safety.catalog_table(
        table,
        {
            "email": safety.roles(ColumnRole.PRIMARY_KEY),
            "created_at": safety.roles(ColumnRole.TIME, indexed=True),
            "gone": safety.roles(ColumnRole.PII),
        },
    )
    right = safety.catalog_table(table, {"id": safety.roles(ColumnRole.PRIMARY_KEY)})
    try:
        drifted = safety.review(
            safety.application("oracle", database.binding, [wrong]), database.adapter(), table
        )
        matched = safety.review(
            safety.application("oracle", database.binding, [right]), database.adapter(), table
        )
    finally:
        _drop(database, table)
    assert sorted(d.message for d in drifted.drift) == [
        "column gone is not in the live schema",
        "created_at is marked indexed, but no index starts with it",
        "primary key is (email) in the catalog but (id) in the live schema",
    ]
    assert matched.drift == []


def test_oracle_test_connection_says_a_refused_login_and_never_where(database: Database) -> None:
    app = safety.application("oracle", database.binding, [])
    reached = safety.connection_test(app, database.adapter())
    refused = safety.connection_test(app, OracleAdapter(database.binding, "Wrong-s3cret-pw"))
    assert (reached.reachable, reached.reason) == (True, None)
    assert not refused.reachable
    assert refused.reason == "authentication failed; check the binding's login and secret"


def test_oracle_the_example_v_session_read_step_runs_as_written(database: Database) -> None:
    # The example runbook's check-locks step, exactly as written. It needs SELECT on v$session,
    # which the tests' own login has and ProbeOwl's does not.
    [step] = [
        s
        for s in load_applications(EXAMPLES)["wallet"].runbooks["topup_not_credited"].steps
        if s.id == "check-locks"
    ]
    assert isinstance(step, ReadQueryStep)
    admin = database.binding.model_copy(update={"user": database.admin_user})
    app = safety.application("oracle", admin, [])
    entitled = OracleAdapter(admin, database.admin_password, SqlLimits(statement_timeout_s=5))
    checked = safety.plan_check(app, step.sql, entitled)
    result = entitled.lookup(step.sql, {}, 20)
    entitled.close()
    # v$session is a fixed view: no table is read, so the plan check passes.
    assert checked.status is CheckStatus.PASS, checked.message
    assert result.query.startswith("SELECT sid, blocking_session, event, seconds_in_wait")
    assert "FROM v$session WHERE blocking_session IS NOT NULL" in result.query
    if database.admin_user == database.binding.user:
        pytest.skip(
            f"ProbeOwl and the checks connect as one login ({ADMIN_URL} is not set), as in CI's "
            "container, whose one test user can read v$session, so the refusal cannot be shown"
        )
    probe = database.adapter()
    with pytest.raises(MissingGrant) as refused:
        probe.lookup(step.sql, {}, 20)
    with pytest.raises(MissingGrant) as unplanned:
        probe.explain(step.sql, {})
    probe.close()
    # Oracle names the view it refused, so ProbeOwl names the grant, as for the lag grants.
    assert refused.value.grant == unplanned.value.grant == "SELECT on SYS.V_$SESSION"
    assert str(refused.value) == (
        "missing SELECT on SYS.V_$SESSION: a DBA grants it to the binding's login"
    )
