# Author: @SaadZarook
"""The SQL Server adapter against a real server: locks, timeouts, plans and the guards.

Runs in CI against a container, or locally against PROBEOWL_TEST_MSSQL_URL; skips otherwise.
"""

import os
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import mssql_python
import pytest
from sqlalchemy.engine import make_url

from probeowl.connectors.plan import Access
from probeowl.connectors.sql import SqlLimits
from probeowl.connectors.sqlserver import SqlServerAdapter, connection_string
from probeowl.engine.masking import MASKED
from probeowl.errors import ConnectorError, GuardViolation
from probeowl.healthy import SAMPLE_ROWS
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

TEST_DATABASE = "probeowl_test"
EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
ROWS = 2000


# Natively, the tests' own setup, checks and cleanup connect as a login of their own, and ProbeOwl
# as one with only what a Production read login would have. CI's container has only sa, for both.
ADMIN_URL = "PROBEOWL_TEST_MSSQL_ADMIN_URL"


@dataclass(frozen=True)
class Server:
    binding: SqlBinding
    password: str
    table: str
    admin_binding: SqlBinding
    admin_password: str

    def admin(self) -> Any:
        """The tests' own connection, for setup, checks, cleanup and holding locks."""
        return mssql_python.connect(
            connection_string(self.admin_binding, self.admin_password), autocommit=True
        )

    def adapter(self, **binding_changes: Any) -> SqlServerAdapter:
        binding = self.binding.model_copy(update=binding_changes)
        return SqlServerAdapter(binding, self.password, SqlLimits(statement_timeout_s=2))


@pytest.fixture(scope="module")
def server() -> Iterator[Server]:
    with store_database_url("mssql") as url:
        parsed = make_url(url)
        on_master = SqlBinding(
            host=parsed.host or "127.0.0.1",
            port=parsed.port,
            database=parsed.database or "master",
            user=parsed.username or "sa",
            secret="env:PROBEOWL_TEST_MSSQL_URL",
            trust_server_certificate=parsed.query.get("TrustServerCertificate") == "yes",
        )
        password = parsed.password or ""
        # A container starts with only master; a given URL already names a test database.
        database = TEST_DATABASE if on_master.database == "master" else on_master.database
        binding = on_master.model_copy(update={"database": database})
        admin_binding, admin_password = binding, password
        if os.environ.get(ADMIN_URL):
            check_test_database(os.environ[ADMIN_URL])
            admin_url = make_url(os.environ[ADMIN_URL])
            admin_binding = binding.model_copy(update={"user": admin_url.username})
            admin_password = admin_url.password or ""
        admin = mssql_python.connect(
            connection_string(
                admin_binding.model_copy(update={"database": "master"}), admin_password
            ),
            autocommit=True,
        )
        admin.cursor().execute(f"IF DB_ID('{database}') IS NULL CREATE DATABASE [{database}]")
        admin.close()

        table = f"probe_topups_{uuid.uuid4().hex[:8]}"
        server = Server(binding, password, table, admin_binding, admin_password)
        writer = server.admin()
        cursor = writer.cursor()
        cursor.execute(
            f"CREATE TABLE {table} (id varchar(20) NOT NULL PRIMARY KEY, "
            "user_id varchar(20) NOT NULL, status varchar(16) NOT NULL, amount decimal(18, 2))"
        )
        cursor.execute(
            f"WITH n AS (SELECT TOP ({ROWS}) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS i "
            "FROM sys.all_objects AS a CROSS JOIN sys.all_objects AS b) "
            f"INSERT INTO {table} (id, user_id, status, amount) "
            "SELECT 'tp_' + RIGHT('000000' + CAST(i AS varchar(6)), 6), "
            "'u_' + CAST(i % 50 AS varchar(6)), "
            "CASE WHEN i <= 10 THEN 'pending' ELSE 'succeeded' END, 500.00 FROM n"
        )
        writer.close()
        try:
            yield server
        finally:
            writer = server.admin()
            writer.cursor().execute(f"DROP TABLE {table}")
            writer.close()


def test_mssql_lookup_returns_rows_and_its_evidence(server: Server) -> None:
    adapter = server.adapter()
    result = adapter.lookup(
        f"SELECT id, status, amount FROM {server.table} WHERE id = :topup_id",
        {"topup_id": "tp_000042"},
        max_rows=1,
    )
    adapter.close()
    assert result.rows == [{"id": "tp_000042", "status": "succeeded", "amount": pytest.approx(500)}]
    assert result.truncated is None
    assert result.parameters == {"topup_id": "tp_000042"}
    assert result.query.startswith("SELECT TOP (2) id, status, amount")
    assert result.query.endswith("OPTION (MAXDOP 1)")
    assert "SET LOCK_TIMEOUT 0" in result.guards
    assert "statement timeout 2 s" in result.guards


def test_mssql_more_rows_than_allowed_are_reported(server: Server) -> None:
    adapter = server.adapter()
    result = adapter.lookup(
        f"SELECT id FROM {server.table} WHERE status = :status ORDER BY id",
        {"status": "pending"},
        5,
    )
    adapter.close()
    assert len(result.rows) == 5
    assert result.truncated == "more than 5 rows"
    # Counted without the ORDER BY, which SQL Server refuses in a derived table.
    assert result.rows_total == 10
    # The guard caps the count query too, with TOP.
    assert any(g.startswith("rows counted with: SELECT TOP (1) COUNT(*)") for g in result.guards)


def test_mssql_session_guards_are_in_force(server: Server) -> None:
    adapter = server.adapter()
    adapter.lookup(f"SELECT id FROM {server.table} WHERE id = :id", {"id": "tp_000001"}, 1)
    # Read through the adapter's own session, which the guard would not let a probe do.
    cursor = adapter._connection.cursor()
    cursor.execute(
        "SELECT @@LOCK_TIMEOUT, deadlock_priority, transaction_isolation_level "
        "FROM sys.dm_exec_sessions WHERE session_id = @@SPID"
    )
    lock_timeout, deadlock_priority, isolation = cursor.fetchone()
    adapter.close()
    assert lock_timeout == 0
    assert deadlock_priority == -5  # LOW
    assert isolation == 2  # READ COMMITTED


def test_mssql_a_locked_row_fails_fast_and_never_waits(server: Server) -> None:
    # The tests' own login holds the lock: ProbeOwl's has no business writing.
    writer = mssql_python.connect(
        connection_string(server.admin_binding, server.admin_password), autocommit=False
    )
    holder = writer.cursor()
    # The writer's open transaction keeps an exclusive lock on tp_000007 until it rolls back.
    holder.execute(f"UPDATE {server.table} SET status = 'failed' WHERE id = 'tp_000007'")
    adapter = server.adapter()
    try:
        start = time.monotonic()
        with pytest.raises(ConnectorError, match="a lock was not granted in time"):
            adapter.lookup(
                f"SELECT id, status FROM {server.table} WHERE id = :id", {"id": "tp_000007"}, 1
            )
        assert time.monotonic() - start < 1.5
        # The adapter recovers on a fresh connection.
        other = adapter.lookup(
            f"SELECT id FROM {server.table} WHERE id = :id", {"id": "tp_000008"}, 1
        )
        assert other.rows == [{"id": "tp_000008"}]
    finally:
        writer.rollback()
        writer.close()
        adapter.close()


def test_mssql_a_runaway_recursive_cte_is_stopped_by_the_statement_timeout(server: Server) -> None:
    # 100 levels, cross-joined five times: 10^10 rows to count, which never finishes in time.
    runaway = (
        "WITH r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r WHERE n < 100) "
        "SELECT COUNT_BIG(*) AS c FROM r AS a CROSS JOIN r AS b CROSS JOIN r AS c "
        "CROSS JOIN r AS d CROSS JOIN r AS e"
    )
    adapter = server.adapter()
    start = time.monotonic()
    with pytest.raises(ConnectorError, match="timed out after 2 s"):
        adapter.lookup(runaway, {}, 1)
    assert time.monotonic() - start < 10
    adapter.close()


# CI's tests run as sa. Natively the test login has these through dev/native/create_test_users.sh.
SERVER_STATE = "VIEW SERVER PERFORMANCE STATE"


def _server_view(cursor: Any, sql: str, parameters: list[Any] | None = None) -> None:
    """Read one of the server's own views: a missing grant says so, rather than a driver error."""
    try:
        cursor.execute(sql, parameters or [])
    except mssql_python.ProgrammingError as err:
        if SERVER_STATE in str(err):
            pytest.fail(
                f"the test login needs {SERVER_STATE} to read the server's views; "
                "natively, run dev/native/create_test_users.sh mssql"
            )
        raise


def _cached_plans(admin: Any, statement: str) -> list[str]:
    """The cached plans of exactly this statement, as the driver sent it.

    Matched on the whole text, never a part of it: other tests' statements on the same table
    are in the cache too, such as the lock test's UPDATE, which SQL Server parameterizes itself.
    """
    cursor = admin.cursor()
    _server_view(
        cursor,
        "SELECT CAST(qp.query_plan AS nvarchar(max)) FROM sys.dm_exec_cached_plans AS cp "
        "CROSS APPLY sys.dm_exec_sql_text(cp.plan_handle) AS st "
        "CROSS APPLY sys.dm_exec_query_plan(cp.plan_handle) AS qp WHERE st.text = ?",
        [statement],
    )
    plans = [str(row[0]) for row in cursor.fetchall()]
    assert plans, f"no cached plan for {statement}"
    return plans


SHOWPLAN = "{http://schemas.microsoft.com/sqlserver/2004/07/showplan}"


def _operators_on(plan: str, table: str) -> list[str]:
    """Each plan operator that reads table itself, by its physical name."""
    found = []
    for operator in ET.fromstring(plan).iter(f"{SHOWPLAN}RelOp"):
        # RelOp > IndexScan (a seek is an IndexScan with a seek predicate) > Object.
        for access in operator:
            target = access.find(f"{SHOWPLAN}Object")
            if target is not None and target.get("Table") == f"[{table}]":
                found.append(str(operator.get("PhysicalOp")))
    return found


def test_mssql_a_lookup_runs_exactly_as_written(server: Server) -> None:
    sql = f"select  id /* why */\nfrom {server.table}\twhere id = :id -- note"
    adapter = server.adapter()
    result = adapter.lookup(sql, {"id": "tp_000042"}, 1)
    adapter.close()
    # TOP right after SELECT keeps the server's row goal, and MAXDOP 1 goes right after the last
    # token, before the trailing comment; the rest is the author's text.
    assert result.query == (
        f"select TOP (2)  id /* why */\nfrom {server.table}\twhere id = ? OPTION (MAXDOP 1) -- note"
    )
    assert result.rows == [{"id": "tp_000042"}]


def test_mssql_a_varchar_id_lookup_is_an_index_seek(server: Server) -> None:
    lookup = f"SELECT id, status FROM {server.table} WHERE id = :topup_id"
    adapter = server.adapter()
    sent = adapter.lookup(lookup, {"topup_id": "tp_000042"}, 1).query
    # The control: the same lookup sent as nvarchar makes SQL Server convert the column.
    adapter.lookup(
        lookup, {"topup_id": "tp_000042"}, 1, parameter_types={"topup_id": "nvarchar(20)"}
    )
    adapter.close()
    # The driver sends the one ? as @P1, declared in front of the statement.
    assert sent.count("?") == 1, sent
    statement = sent.replace("?", "@P1")
    admin = server.admin()
    varchar_plans = _cached_plans(admin, f"(@P1 varchar(8000)){statement}")
    nvarchar_plans = _cached_plans(admin, f"(@P1 nvarchar(4000)){statement}")
    admin.close()
    for plan in varchar_plans:
        operators = _operators_on(plan, server.table)
        # No scan of the lookup table, of any kind; how it seeks is the optimizer's choice.
        assert operators, plan
        assert not [op for op in operators if "Scan" in op], operators
        assert [op for op in operators if "Seek" in op], operators
        assert "CONVERT_IMPLICIT" not in plan
    assert all("CONVERT_IMPLICIT" in plan for plan in nvarchar_plans)


def test_operators_on_names_only_the_tables_own_operators() -> None:
    # Offline, from the shape of a real plan: a seek on the table under a Top, which is not.
    plan = (
        f'<ShowPlanXML xmlns="{SHOWPLAN[1:-1]}"><RelOp PhysicalOp="Top"><Top>'
        '<RelOp PhysicalOp="Clustered Index Seek"><IndexScan><Object Table="[t1]"/></IndexScan>'
        '</RelOp></Top></RelOp><RelOp PhysicalOp="Index Scan"><IndexScan>'
        '<Object Table="[other]"/></IndexScan></RelOp></ShowPlanXML>'
    )
    assert _operators_on(plan, "t1") == ["Clustered Index Seek"]
    assert _operators_on(plan, "other") == ["Index Scan"]


def test_mssql_another_database_needs_allowed_databases(server: Server) -> None:
    sql = "SELECT TOP 1 name FROM master.sys.objects"
    with pytest.raises(GuardViolation, match="names database master"):
        server.adapter().lookup(sql, {}, 1)
    adapter = server.adapter(allowed_databases=["master"])
    assert len(adapter.lookup(sql, {}, 1).rows) == 1
    adapter.close()


def test_mssql_a_wrong_password_is_a_connector_error_without_the_password(server: Server) -> None:
    adapter = SqlServerAdapter(server.binding, "Wrong-s3cret-pw")
    with pytest.raises(ConnectorError) as failure:
        adapter.lookup(f"SELECT id FROM {server.table} WHERE id = :id", {"id": "tp_000001"}, 1)
    assert "Wrong-s3cret-pw" not in str(failure.value)
    # What the server's "Login failed" becomes, word for word.
    assert str(failure.value) == "authentication failed; check the binding's login and secret"


def _base_type(expression: str) -> str:
    return f"CAST(SQL_VARIANT_PROPERTY({expression}, 'BaseType') AS varchar(20))"


def test_mssql_dateadd_on_a_time_parameter_stays_datetime2(server: Server) -> None:
    # Sent as text, DATEADD would make it a datetime, rounded to 1/300 s.
    at = datetime(2026, 11, 7, 10, 0, 0, 123456)
    adapter = server.adapter()
    result = adapter.lookup(
        "SELECT DATEADD(minute, -5, :at) AS earlier, "
        f"{_base_type('DATEADD(minute, -5, :at)')} AS base_type",
        {"at": at},
        1,
    )
    adapter.close()
    assert result.rows == [
        {"earlier": datetime(2026, 11, 7, 9, 55, 0, 123456), "base_type": "datetime2"}
    ]


PLUS_FOUR = timezone(timedelta(hours=4))


def test_mssql_a_plus_four_time_arrives_as_datetimeoffset_unshifted(server: Server) -> None:
    at = datetime(2026, 11, 7, 14, 0, tzinfo=PLUS_FOUR)
    adapter = server.adapter()
    result = adapter.lookup(
        f"SELECT {_base_type(':at')} AS base_type, DATEPART(hour, :at) AS hour, "
        "DATEPART(tzoffset, :at) AS offset_minutes, "
        "CASE WHEN :at = CAST('2026-11-07 10:00 +00:00' AS datetimeoffset) "
        "THEN 'same instant' ELSE 'different' END AS as_utc",
        {"at": at},
        1,
    )
    adapter.close()
    assert result.rows == [
        {"base_type": "datetimeoffset", "hour": 14, "offset_minutes": 240, "as_utc": "same instant"}
    ]


def test_mssql_a_plus_four_time_against_a_datetime2_column(server: Server) -> None:
    # SQL Server's own rule, on the real server: to compare, it turns the datetime2 column into a
    # datetimeoffset at +00:00. So a +04:00 value matches the row stored as its UTC wall clock,
    # not the row stored as its own wall clock.
    table = f"probe_times_{uuid.uuid4().hex[:8]}"
    admin = server.admin()
    cursor = admin.cursor()
    cursor.execute(f"CREATE TABLE {table} (id varchar(20) NOT NULL PRIMARY KEY, at datetime2)")
    cursor.execute(
        f"INSERT INTO {table} VALUES ('own_clock', '2026-11-07 14:00'), "
        "('utc_clock', '2026-11-07 10:00')"
    )
    adapter = server.adapter()
    try:
        result = adapter.lookup(
            f"SELECT id FROM {table} WHERE at = :at",
            {"at": datetime(2026, 11, 7, 14, 0, tzinfo=PLUS_FOUR)},
            2,
            {"at": "datetime2"},
        )
    finally:
        adapter.close()
        cursor.execute(f"DROP TABLE {table}")
        admin.close()
    assert result.rows == [{"id": "utc_clock"}]


# What the time offset save check accepts as converting a +04:00 value explicitly, and what it
# refuses, each against the same datetime2 column: the check's rules rest on these.
OFFSET_FORMS = {
    "at = :at": "utc_clock",
    "at = :at AT TIME ZONE 'Arabian Standard Time'": "utc_clock",  # still a datetimeoffset
    "at = SWITCHOFFSET(:at, '+04:00')": "utc_clock",
    "at >= DATEADD(minute, -1, :at) AND at < DATEADD(minute, 1, :at)": "utc_clock",
    "at = CAST(:at AS datetime2)": "own_clock",
    "at = CONVERT(datetime2, :at)": "own_clock",
    "at = CAST(:at AT TIME ZONE 'Arabian Standard Time' AS datetime2)": "own_clock",
    # Converting the column decides the comparison too; the check leaves these alone.
    "TODATETIMEOFFSET(at, '+04:00') = :at": "own_clock",
    "at AT TIME ZONE 'Arabian Standard Time' = :at": "own_clock",
}
# A value typed with an offset arrives as text, and SQL Server drops the offset of text.
TYPED_FORMS = {
    "2026-11-07T14:00:00+04:00": "own_clock",
    "2026-11-07 14:00 +04:00": "own_clock",
    "2026-11-07T10:00:00Z": "utc_clock",
}


def test_mssql_what_each_conversion_of_a_plus_four_time_matches(server: Server) -> None:
    table = f"probe_times_{uuid.uuid4().hex[:8]}"
    admin = server.admin()
    cursor = admin.cursor()
    cursor.execute(f"CREATE TABLE {table} (id varchar(20) NOT NULL PRIMARY KEY, at datetime2)")
    cursor.execute(
        f"INSERT INTO {table} VALUES ('own_clock', '2026-11-07 14:00'), "
        "('utc_clock', '2026-11-07 10:00')"
    )
    adapter = server.adapter()
    at = datetime(2026, 11, 7, 14, 0, tzinfo=PLUS_FOUR)
    matched: dict[str, str] = {}
    try:
        for where in OFFSET_FORMS:
            rows = adapter.lookup(f"SELECT id FROM {table} WHERE {where}", {"at": at}, 2).rows
            matched[where] = ",".join(str(row["id"]) for row in rows)
        for text in TYPED_FORMS:
            rows = adapter.lookup(f"SELECT id FROM {table} WHERE at = :at", {"at": text}, 2).rows
            matched[text] = ",".join(str(row["id"]) for row in rows)
    finally:
        adapter.close()
        cursor.execute(f"DROP TABLE {table}")
        admin.close()
    assert matched == OFFSET_FORMS | TYPED_FORMS


# The other way round: a 14:00 value without an offset against a datetimeoffset column, holding
# 14:00 +04:00 and 14:00 +00:00. What the check accepts as giving the value its offset.
REVERSE_FORMS = {
    "at = :at": "utc",  # read as UTC
    "at = CAST(:at AS datetimeoffset)": "utc",
    "at = CONVERT(datetimeoffset, :at)": "utc",
    "at = SWITCHOFFSET(:at, '+04:00')": "utc",
    "at >= DATEADD(minute, -1, :at) AND at < DATEADD(minute, 1, :at)": "utc",
    "at = TODATETIMEOFFSET(:at, '+04:00')": "plus_four",
    "at = TODATETIMEOFFSET(:at, 240)": "plus_four",
    "at = :at AT TIME ZONE 'Arabian Standard Time'": "plus_four",
}
TYPED_REVERSE_FORMS = {
    "2026-11-07T14:00:00": "utc",  # text without an offset is read as UTC too
    "2026-11-07 14:00": "utc",
    "2026-11-07T14:00:00+04:00": "plus_four",
}


def test_mssql_what_each_conversion_of_a_time_without_an_offset_matches(server: Server) -> None:
    table = f"probe_times_{uuid.uuid4().hex[:8]}"
    admin = server.admin()
    cursor = admin.cursor()
    cursor.execute(f"CREATE TABLE {table} (id varchar(20) NOT NULL PRIMARY KEY, at datetimeoffset)")
    cursor.execute(
        f"INSERT INTO {table} VALUES ('plus_four', '2026-11-07 14:00 +04:00'), "
        "('utc', '2026-11-07 14:00 +00:00')"
    )
    adapter = server.adapter()
    at = datetime(2026, 11, 7, 14, 0)
    matched: dict[str, str] = {}
    try:
        for where in REVERSE_FORMS:
            rows = adapter.lookup(f"SELECT id FROM {table} WHERE {where}", {"at": at}, 2).rows
            matched[where] = ",".join(str(row["id"]) for row in rows)
        for text in TYPED_REVERSE_FORMS:
            rows = adapter.lookup(f"SELECT id FROM {table} WHERE at = :at", {"at": text}, 2).rows
            matched[text] = ",".join(str(row["id"]) for row in rows)
    finally:
        adapter.close()
        cursor.execute(f"DROP TABLE {table}")
        admin.close()
    assert matched == REVERSE_FORMS | TYPED_REVERSE_FORMS


def test_mssql_no_leftover_test_login_is_on_the_server(server: Server) -> None:
    # #143's probe_noplan_ logins, and any a test interrupted before its cleanup left behind.
    admin = server.admin()
    cursor = admin.cursor()
    cursor.execute(
        "SELECT name FROM sys.server_principals WHERE name LIKE 'probe[_]noplan[_]%' "
        "OR name LIKE 'probeowl[_]test[_]noplan[_]%' OR name LIKE 'probeowl[_]test[_]lag[_]%'"
    )
    left = [str(row[0]) for row in cursor.fetchall()]
    admin.close()
    assert left == [], (
        f"{left} are left on the server; natively, dev/native/create_test_users.sh mssql drops them"
    )


def test_mssql_the_connection_is_encrypted(server: Server) -> None:
    adapter = server.adapter()
    adapter.lookup(f"SELECT id FROM {server.table} WHERE id = :id", {"id": "tp_000001"}, 1)
    # Its own session ID needs no grant; reading another session's connection does.
    own = adapter._connection.cursor()
    own.execute("SELECT @@SPID")
    (session_id,) = own.fetchone()
    admin = server.admin()
    check = admin.cursor()
    _server_view(
        check,
        "SELECT encrypt_option FROM sys.dm_exec_connections WHERE session_id = ?",
        [session_id],
    )
    (encrypt_option,) = check.fetchone()
    admin.close()
    adapter.close()
    assert encrypt_option == "TRUE"


SERVER_GRANTS = ("VIEW SERVER PERFORMANCE STATE", "VIEW SERVER STATE", "ALTER ANY CONNECTION")


def test_mssql_probeowl_connects_with_no_server_grant(server: Server) -> None:
    if server.admin_binding.user == server.binding.user:
        pytest.skip(
            f"ProbeOwl and the checks connect as one login ({ADMIN_URL} is not set), as in CI's "
            "container, which has only sa"
        )
    adapter = server.adapter()
    adapter.lookup(f"SELECT id FROM {server.table} WHERE id = :id", {"id": "tp_000001"}, 1)
    cursor = adapter._connection.cursor()
    held = {}
    for grant in SERVER_GRANTS:
        cursor.execute("SELECT HAS_PERMS_BY_NAME(NULL, NULL, ?)", [grant])
        (held[grant],) = cursor.fetchone()
    cursor.execute("SELECT IS_SRVROLEMEMBER('sysadmin'), IS_MEMBER('db_owner')")
    sysadmin, owner = cursor.fetchone()
    adapter.close()
    assert held == dict.fromkeys(SERVER_GRANTS, 0)
    assert (sysadmin, owner) == (0, 0)


def test_mssql_an_untrusted_certificate_is_refused_by_default(server: Server) -> None:
    if not server.binding.trust_server_certificate:
        pytest.skip("this server's certificate is trusted, so there is nothing to refuse")
    adapter = server.adapter(trust_server_certificate=False)
    with pytest.raises(ConnectorError, match="certificate could not be verified"):
        adapter.lookup("SELECT 1 AS one", {}, 1)


def test_mssql_a_server_that_is_not_a_replica_reports_its_lag_unknown(server: Server) -> None:
    adapter = server.adapter()
    lag = adapter.replica_lag()
    assert lag.seconds is None
    assert lag.note.startswith("replica lag unknown: ")
    assert adapter.lookup("SELECT 1 AS one", {}, 1).rows == [{"one": 1}]
    adapter.close()


def test_mssql_import_reads_the_schema_never_data(server: Server) -> None:
    adapter = server.adapter()
    tables, _ = adapter.import_schema()
    adapter.close()
    [topups] = [t for t in tables if t.table == server.table]
    assert topups.db_schema == "dbo"
    assert [(c.name, c.type, c.nullable) for c in topups.columns] == [
        ("id", "varchar(20)", False),
        ("user_id", "varchar(20)", False),
        ("status", "varchar(16)", False),
        ("amount", "decimal(18,2)", True),
    ]
    assert topups.primary_key == ["id"]
    assert topups.approx_rows == ROWS
    assert "tp_000001" not in str(tables)


def test_mssql_showplan_shows_a_seek_and_a_scan_without_running(server: Server) -> None:
    adapter = server.adapter()
    by_id = adapter.explain(f"SELECT id FROM {server.table} WHERE id = :id", {"id": "tp_000001"})
    by_user = adapter.explain(f"SELECT id FROM {server.table} WHERE user_id = :u", {"u": "u_1"})
    assert [s.operation for s in by_id if s.access is Access.INDEX] == ["Clustered Index Seek"]
    assert by_id[0].table == server.table
    [scan] = [s for s in by_user if s.access is Access.SCAN]
    assert scan.table == server.table and scan.table_rows == ROWS
    # SHOWPLAN is off again: the same session runs lookups as usual.
    result = adapter.lookup(f"SELECT id FROM {server.table} WHERE id = :id", {"id": "tp_000001"}, 1)
    assert result.rows == [{"id": "tp_000001"}]
    adapter.close()


def test_mssql_a_login_that_can_read_plans_is_told_so(server: Server) -> None:
    adapter = server.adapter()
    assert adapter.unreachable() is None
    assert adapter.plan_access() == (PlanAccess.READABLE, None)
    adapter.close()


def test_mssql_a_login_without_showplan_is_told_the_grant(server: Server) -> None:
    # "test" in its name, as every login the tests make: dev/native drops leftovers by it.
    login = f"probeowl_test_noplan_{uuid.uuid4().hex[:8]}"
    password = f"NoPlan-{uuid.uuid4().hex}-1a"
    admin = server.admin()
    cursor = admin.cursor()
    cursor.execute(f"CREATE LOGIN {login} WITH PASSWORD = '{password}'")
    cursor.execute(f"CREATE USER {login} FOR LOGIN {login}")
    try:
        adapter = SqlServerAdapter(
            server.binding.model_copy(update={"user": login}),
            password,
            SqlLimits(statement_timeout_s=2),
        )
        access, message = adapter.plan_access()
        adapter.close()
    finally:
        cursor.execute(f"DROP USER {login}")
        _drop_login(cursor, login)
        admin.close()
    assert access is PlanAccess.DENIED
    assert message is not None and "GRANT SHOWPLAN TO <login>" in message
    assert login not in message


def _drop_login(cursor: Any, login: str) -> None:
    """End the session the pool keeps for login, then drop it; say which grant is missing."""
    # Without VIEW SERVER PERFORMANCE STATE the session cannot be seen, so nothing is ended.
    _server_view(
        cursor,
        "DECLARE @kill nvarchar(max) = ''; "
        "SELECT @kill += 'KILL ' + CAST(session_id AS varchar(10)) + ';' "
        f"FROM sys.dm_exec_sessions WHERE login_name = '{login}'; "
        "EXEC (@kill);",
    )
    try:
        cursor.execute(f"DROP LOGIN {login}")
    except mssql_python.ProgrammingError as err:
        if "currently logged in" in str(err):
            pytest.fail(
                f"{login} is still signed in, so it cannot be dropped: the test login needs "
                "ALTER ANY CONNECTION to end its session; natively, run "
                "dev/native/create_test_users.sh mssql"
            )
        raise


def test_mssql_an_unreachable_server_says_why_but_not_where(server: Server) -> None:
    reason = server.adapter(port=1).unreachable()
    assert reason is not None
    assert server.binding.host not in reason and server.binding.user not in reason


def test_mssql_the_lag_grant_named_is_the_one_that_is_enough(server: Server) -> None:
    # A login with only what ProbeOwl needs to read, and not the lag grant.
    login = f"probeowl_test_lag_{uuid.uuid4().hex[:8]}"
    password = f"Lag-{uuid.uuid4().hex}-1a"
    admin = server.admin()
    cursor = admin.cursor()
    cursor.execute(f"CREATE LOGIN {login} WITH PASSWORD = '{password}'")
    cursor.execute(f"CREATE USER {login} FOR LOGIN {login}")
    try:
        adapter = SqlServerAdapter(
            server.binding.model_copy(update={"user": login}), password, SqlLimits()
        )
        refused = adapter.replica_lag()
        adapter.close()
        assert refused.cause is not None and refused.cause.startswith("missing VIEW "), refused
        assert refused.note == f"replica lag unknown: {refused.cause}"
        grant = refused.cause.removeprefix("missing ")
        # Database-scoped permissions are granted in the database, server ones on the login.
        if "DATABASE" in grant:
            cursor.execute(f"GRANT {grant} TO {login}")
        else:
            master = mssql_python.connect(
                connection_string(
                    server.admin_binding.model_copy(update={"database": "master"}),
                    server.admin_password,
                ),
                autocommit=True,
            )
            master.cursor().execute(f"GRANT {grant} TO {login}")
            master.close()
        adapter = SqlServerAdapter(
            server.binding.model_copy(update={"user": login}), password, SqlLimits()
        )
        allowed = adapter.replica_lag()
        adapter.close()
        # Not a replica, so the lag stays unknown, but no longer for want of a grant.
        assert allowed.cause is None or not allowed.cause.startswith("missing "), allowed
    finally:
        cursor.execute(f"DROP USER {login}")
        _drop_login(cursor, login)
        admin.close()


# --- Safety on a real server: runbook reads, drift, Test connection, temp tables --------------
# The rest of each rule is tested with fakes; these are the parts a real SQL Server decides.

PEOPLE = 5000  # past SMALL_TABLE_ROWS, so a whole-table read is a large one


def _people(server: Server) -> str:
    table = f"probe_people_{uuid.uuid4().hex[:8]}"
    admin = server.admin()
    cursor = admin.cursor()
    cursor.execute(
        f"CREATE TABLE {table} (id varchar(20) NOT NULL PRIMARY KEY, status varchar(16) NOT NULL, "
        "email varchar(100) NOT NULL, created_at datetime2 NOT NULL)"
    )
    cursor.execute(
        f"WITH n AS (SELECT TOP ({PEOPLE}) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS i "
        "FROM sys.all_objects AS a CROSS JOIN sys.all_objects AS b) "
        f"INSERT INTO {table} (id, status, email, created_at) SELECT "
        "CONCAT('p_', FORMAT(i, '000000')), "
        "CASE WHEN i % 10 = 0 THEN 'inactive' ELSE 'active' END, "
        "CONCAT('person', i, '@example.com'), DATEADD(minute, i, '2026-11-01') FROM n"
    )
    admin.close()
    return table


def _drop(server: Server, table: str) -> None:
    admin = server.admin()
    admin.cursor().execute(f"DROP TABLE {table}")
    admin.close()


def test_mssql_a_runbook_read_on_a_full_scan_is_refused_and_a_keyed_one_masked(
    server: Server, tmp_path: Path
) -> None:
    table = _people(server)
    entry = safety.catalog_table(
        table, {"id": safety.roles(ColumnRole.PRIMARY_KEY), "email": safety.roles(ColumnRole.PII)}
    )
    app = safety.application("sqlserver", server.binding, [entry])
    try:
        scan = safety.plan_check(
            app, f"SELECT id FROM {table} WHERE status = 'inactive'", server.adapter()
        )
        keyed_sql = f"SELECT id, email FROM {table} WHERE id = :id"
        keyed = safety.plan_check(app, keyed_sql, server.adapter())
        result = safety.read_masked(app, keyed_sql, {"id": "p_000042"}, server.adapter(), tmp_path)
    finally:
        _drop(server, table)
    # The real plan reads the whole table, so the step is refused before it runs.
    assert scan.status is CheckStatus.FAIL, scan.message
    assert f"the lookup would read all of {table}" in scan.message
    assert keyed.status is CheckStatus.PASS, keyed.message
    # And a keyed read runs, with the catalog's PII masked in its evidence.
    assert result.outcome is Outcome.FOUND
    assert result.evidence is not None
    assert result.evidence.rows == [{"id": "p_000042", "email": MASKED}]


def test_mssql_the_drift_flag_reads_a_real_import(server: Server) -> None:
    table = _people(server)
    try:
        drifted = safety.review(
            safety.application(
                "sqlserver",
                server.binding,
                [
                    safety.catalog_table(
                        table,
                        {
                            "email": safety.roles(ColumnRole.PRIMARY_KEY),
                            "created_at": safety.roles(ColumnRole.TIME, indexed=True),
                            "gone": safety.roles(ColumnRole.PII),
                        },
                    )
                ],
            ),
            server.adapter(),
            table,
        )
        right = safety.review(
            safety.application(
                "sqlserver",
                server.binding,
                [safety.catalog_table(table, {"id": safety.roles(ColumnRole.PRIMARY_KEY)})],
            ),
            server.adapter(),
            table,
        )
    finally:
        _drop(server, table)
    assert sorted(d.message for d in drifted.drift) == [
        "column gone is not in the live schema",
        "created_at is marked indexed, but no index starts with it",
        "primary key is (email) in the catalog but (id) in the live schema",
    ]
    assert right.drift == []


def test_mssql_test_connection_says_a_refused_login_and_never_where(server: Server) -> None:
    app = safety.application("sqlserver", server.binding, [])
    reached = safety.connection_test(app, server.adapter())
    refused = safety.connection_test(app, SqlServerAdapter(server.binding, "Wrong-s3cret-pw"))
    assert (reached.reachable, reached.reason) == (True, None)
    assert not refused.reachable
    assert refused.reason == "authentication failed; check the binding's login and secret"


def test_mssql_a_temp_table_is_refused_before_anything_reaches_the_server(server: Server) -> None:
    # Real temp tables, so the refusal is not because there is nothing to read.
    admin = server.admin()
    cursor = admin.cursor()
    cursor.execute("CREATE TABLE #probe_temp (id int)")
    cursor.execute("CREATE TABLE ##probe_temp_shared (id int)")
    adapter = server.adapter()
    try:
        for name in ("#probe_temp", "##probe_temp_shared"):
            with pytest.raises(GuardViolation, match="temp tables are not allowed"):
                adapter.lookup(f"SELECT id FROM {name}", {}, 1)
        connected = adapter._connection is not None
    finally:
        cursor.execute("DROP TABLE ##probe_temp_shared")
        admin.close()
    assert not connected  # refused by the guard, so no connection was ever opened


def test_mssql_the_example_outbox_read_step_runs_on_a_real_server(
    server: Server, tmp_path: Path
) -> None:
    # The example runbook's check-outbox step as written, on a copy of its table.
    [step] = [
        s
        for s in load_applications(EXAMPLES)["wallet"].runbooks["topup_not_credited"].steps
        if s.id == "check-outbox"
    ]
    assert isinstance(step, ReadQueryStep)
    table = f"probe_outbox_{uuid.uuid4().hex[:8]}"
    admin = server.admin()
    cursor = admin.cursor()
    cursor.execute(
        f"CREATE TABLE {table} (id varchar(20) NOT NULL PRIMARY KEY, aggregate_id varchar(20) "
        "NOT NULL, status varchar(16) NOT NULL, attempts int NOT NULL, last_error varchar(200), "
        "created_at datetime2 NOT NULL)"
    )
    cursor.execute(f"CREATE INDEX ix_{table}_aggregate ON {table} (aggregate_id, created_at)")
    cursor.execute(
        f"INSERT INTO {table} VALUES ('ob_1', 'tp_91f2c7', 'failed', 3, 'timeout', '2026-11-07')"
    )
    sql = step.sql.replace("FROM outbox_events", f"FROM {table}")
    entry = safety.catalog_table(table, {"id": safety.roles(ColumnRole.PRIMARY_KEY)})
    app = safety.application("sqlserver", server.binding, [entry])
    try:
        checked = safety.plan_check(app, sql, server.adapter())
        result = safety.read_masked(app, sql, {"topup_id": "tp_91f2c7"}, server.adapter(), tmp_path)
    finally:
        cursor.execute(f"DROP TABLE {table}")
        admin.close()
    assert checked.status is not CheckStatus.FAIL, checked.message
    assert result.outcome is Outcome.FOUND
    assert result.evidence is not None
    assert [row["id"] for row in result.evidence.rows] == ["ob_1"]
    # What ran is the step as written, its own TOP (20) kept, the parameter bound.
    assert result.evidence.query.startswith("SELECT TOP (20) id, status, attempts")


def test_mssql_the_example_healthy_sample_runs_on_the_seeds_index(server: Server) -> None:
    # The wallet healthy sample as written, DATEADD on a bound parameter included, on a copy of
    # topups with the dev seed's ix_topups_created_at.
    sample = load_applications(EXAMPLES)["wallet"].entities["topup"].healthy_sample
    assert sample is not None
    table = f"probe_topups_{uuid.uuid4().hex[:8]}"
    admin = server.admin()
    cursor = admin.cursor()
    cursor.execute(
        f"CREATE TABLE {table} (id varchar(20) NOT NULL PRIMARY KEY, status varchar(16) NOT NULL, "
        "payment_method varchar(20) NOT NULL, created_at datetime2 NOT NULL)"
    )
    cursor.execute(f"CREATE INDEX ix_{table}_created_at ON {table} (created_at)")
    cursor.execute(
        f"WITH n AS (SELECT TOP ({PEOPLE}) ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS i "
        "FROM sys.all_objects AS a CROSS JOIN sys.all_objects AS b) "
        f"INSERT INTO {table} SELECT CONCAT('tp_', FORMAT(i, '000000')), "
        "CASE WHEN i % 5 = 0 THEN 'failed' ELSE 'succeeded' END, 'card', "
        "DATEADD(hour, i, '2026-01-01') FROM n"
    )
    sql = sample.lookup.replace("FROM topups", f"FROM {table}")
    app = safety.application(
        "sqlserver",
        server.binding,
        [safety.catalog_table(table, {"id": safety.roles(ColumnRole.PRIMARY_KEY)})],
    )
    # The failing top-up is tp_004000; its week before runs from hour 3832 to 3999.
    failing = {"topup_id": "tp_004000", "created_at": datetime(2026, 1, 1) + timedelta(hours=4000)}
    try:
        checked = safety.plan_check(app, sql, server.adapter())
        adapter = server.adapter()
        result = adapter.lookup(sql, failing, SAMPLE_ROWS)
        adapter.close()
    finally:
        cursor.execute(f"DROP TABLE {table}")
        admin.close()
    assert checked.status is CheckStatus.PASS, checked.message
    assert f"ix_{table}_created_at" in checked.message
    # Succeeded, within the week before, newest first, never the failing top-up itself.
    assert [row["topup_id"] for row in result.rows] == [
        "tp_003999",
        "tp_003998",
        "tp_003997",
        "tp_003996",
    ]
