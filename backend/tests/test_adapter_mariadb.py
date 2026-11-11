# Author: @SaadZarook
"""The MariaDB adapter against a real server: locks, the statement timeout, read-only sessions.

Runs in CI against a container, or locally against PROBEOWL_TEST_MARIADB_URL; skips otherwise.
"""

import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymysql
import pytest
from sqlalchemy.engine import make_url

from probeowl.connectors.mariadb import MariaDbAdapter
from probeowl.connectors.plan import Access
from probeowl.connectors.sql import SqlLimits
from probeowl.engine.masking import MASKED
from probeowl.errors import ConnectorError, GuardViolation
from probeowl.healthy import SAMPLE_ROWS
from probeowl.model import SqlBinding
from probeowl.model.catalog import ColumnRole
from probeowl.model.check import load_applications
from probeowl.model.connector_status import PlanAccess
from probeowl.model.run import Outcome
from probeowl.model.save_check import CheckStatus
from tests import native_safety as safety
from tests.store_databases import check_test_database, store_database_url

pytestmark = pytest.mark.database

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

CARDS_FOR_ONE_VISITOR = 30


# Natively, the tests' own setup, checks and cleanup connect as a login of their own, and ProbeOwl
# as one with only what a Production read login would have, plus the store tests' DDL in its own
# database. CI's container has one test user, for both.
ADMIN_URL = "PROBEOWL_TEST_MARIADB_ADMIN_URL"


@dataclass(frozen=True)
class Database:
    binding: SqlBinding
    password: str
    table: str
    admin_user: str
    admin_password: str

    def writer(self) -> Any:
        """The tests' own session, for setup, cleanup and holding locks."""
        return pymysql.connect(
            host=self.binding.host,
            port=self.binding.port or 3306,
            user=self.admin_user,
            password=self.admin_password,
            database=self.binding.database,
        )

    def adapter(self, **binding_changes: Any) -> MariaDbAdapter:
        binding = self.binding.model_copy(update=binding_changes)
        return MariaDbAdapter(binding, self.password, SqlLimits(statement_timeout_s=2))


@pytest.fixture(scope="module")
def database() -> Iterator[Database]:
    with store_database_url("mariadb") as url:
        parsed = make_url(url)
        binding = SqlBinding(
            host=parsed.host or "127.0.0.1",
            port=parsed.port,
            database=parsed.database,
            user=parsed.username or "test",
            secret="env:PROBEOWL_TEST_MARIADB_URL",
            # The container's certificate is self-signed.
            trust_server_certificate=True,
        )
        password = parsed.password or ""
        admin_user, admin_password = binding.user, password
        if os.environ.get(ADMIN_URL):
            check_test_database(os.environ[ADMIN_URL])
            admin_url = make_url(os.environ[ADMIN_URL])
            admin_user, admin_password = admin_url.username or "", admin_url.password or ""
        db = Database(
            binding, password, f"cards_{uuid.uuid4().hex[:8]}", admin_user, admin_password
        )
        writer = db.writer()
        cursor = writer.cursor()
        cursor.execute(
            f"CREATE TABLE {db.table} (card_id VARCHAR(10) NOT NULL PRIMARY KEY, "
            "visitor_profile_id VARCHAR(12) NOT NULL, status VARCHAR(10) NOT NULL, "
            "valid_to DATE NOT NULL, deposit DECIMAL(10, 2) NOT NULL, KEY (visitor_profile_id)) "
            "ENGINE=InnoDB"
        )
        cursor.executemany(
            f"INSERT INTO {db.table} VALUES (%s, %s, %s, %s, %s)",
            [
                ("C204518", "VP00012931", "active", date(2026, 12, 31), Decimal("25.50")),
                ("C198220", "VP00012931", "active", date(2026, 9, 30), Decimal("25.50")),
            ]
            + [
                (f"C9{i:05d}", "VP00000001", "active", date(2026, 12, 31), Decimal("0"))
                for i in range(CARDS_FOR_ONE_VISITOR)
            ],
        )
        writer.commit()
        writer.close()
        try:
            yield db
        finally:
            writer = db.writer()
            writer.cursor().execute(f"DROP TABLE {db.table}")
            writer.close()


def test_mariadb_a_lookup_runs_exactly_as_written(database: Database) -> None:
    # Spacing, case and comments are the author's; only the parameter and the cap are filled in.
    sql = (
        f"select card_id /* why: 100% sure */\nfrom {database.table}\t"
        "where visitor_profile_id = :id and status like 'act%' order by card_id -- note"
    )
    adapter = database.adapter()
    result = adapter.lookup(sql, {"id": "VP00012931"}, 5)
    adapter.close()
    assert result.query == (
        f"select card_id /* why: 100%% sure */\nfrom {database.table}\t"
        "where visitor_profile_id = %(id)s and status like 'act%%' order by card_id LIMIT 6 -- note"
    )
    assert result.rows == [{"card_id": "C198220"}, {"card_id": "C204518"}]


def test_mariadb_an_executable_comment_never_reaches_the_server(database: Database) -> None:
    adapter = database.adapter()
    with pytest.raises(GuardViolation, match="comment the database would run"):
        adapter.lookup(f"SELECT card_id /*!, SLEEP(5) */ FROM {database.table}", {}, 1)
    adapter.close()


PLUS_FOUR = timezone(timedelta(hours=4))


def test_mariadb_a_time_with_an_offset_is_refused_unless_the_lookup_converts_it(
    database: Database,
) -> None:
    # PyMySQL would send 14:00 +04:00 as '2026-11-07 14:00:00', its offset dropped.
    adapter = database.adapter()
    at = {"at": datetime(2026, 11, 7, 14, 0, tzinfo=PLUS_FOUR)}
    with pytest.raises(ConnectorError) as refused:
        adapter.lookup("SELECT :at AS sent", at, 1)
    assert str(refused.value) == (
        ":at is a time with an offset, and MariaDB has no type with one: it would be sent as its "
        "clock time, the offset dropped. Convert it in the lookup, for example "
        "CAST(:at AS DATETIME), so the save check can pass it"
    )
    # Converted, it is the value's own clock time, which is what the save check says it means.
    result = adapter.lookup("SELECT CAST(:at AS DATETIME) AS sent", at, 1)
    adapter.close()
    assert result.rows == [{"sent": datetime(2026, 11, 7, 14, 0)}]


def test_mariadb_lookup_returns_rows_and_its_evidence(database: Database) -> None:
    adapter = database.adapter()
    result = adapter.lookup(
        f"SELECT card_id, status, valid_to, deposit FROM {database.table} "
        "WHERE visitor_profile_id = :visitor_profile_id ORDER BY card_id",
        {"visitor_profile_id": "VP00012931"},
        max_rows=10,
    )
    adapter.close()
    assert result.rows == [
        {
            "card_id": "C198220",
            "status": "active",
            "valid_to": date(2026, 9, 30),
            "deposit": Decimal("25.50"),
        },
        {
            "card_id": "C204518",
            "status": "active",
            "valid_to": date(2026, 12, 31),
            "deposit": Decimal("25.50"),
        },
    ]
    assert result.query.endswith("LIMIT 11")
    assert result.parameters == {"visitor_profile_id": "VP00012931"}
    assert "max_statement_time 2 s" in result.guards


def test_mariadb_more_rows_than_allowed_are_reported(database: Database) -> None:
    adapter = database.adapter()
    result = adapter.lookup(
        f"SELECT card_id FROM {database.table} WHERE visitor_profile_id = :v",
        {"v": "VP00000001"},
        5,
    )
    adapter.close()
    assert len(result.rows) == 5
    assert result.truncated == "more than 5 rows"
    assert result.rows_total == CARDS_FOR_ONE_VISITOR  # counted, for "showing 5 of N"
    assert any(g.startswith("rows counted with: SELECT COUNT(*)") for g in result.guards)


def test_mariadb_session_settings_are_in_force(database: Database) -> None:
    adapter = database.adapter()
    adapter.lookup(
        f"SELECT card_id FROM {database.table} WHERE card_id = :id", {"id": "C204518"}, 1
    )
    # Read through the adapter's own session, which the guard would not let a probe do.
    cursor = adapter._connection.cursor()
    cursor.execute(
        "SELECT @@SESSION.max_statement_time, @@SESSION.lock_wait_timeout, "
        "@@SESSION.innodb_lock_wait_timeout, @@SESSION.tx_isolation, @@SESSION.tx_read_only"
    )
    settings = cursor.fetchone()
    adapter.close()
    assert float(settings[0]) == 2
    assert int(settings[1]) == 1 and int(settings[2]) == 1
    assert settings[3] == "READ-COMMITTED"
    assert int(settings[4]) == 1


def test_mariadb_the_session_cannot_write_even_past_the_guard(database: Database) -> None:
    adapter = database.adapter()
    adapter.lookup(
        f"SELECT card_id FROM {database.table} WHERE card_id = :id", {"id": "C204518"}, 1
    )
    cursor = adapter._connection.cursor()
    with pytest.raises(pymysql.err.MySQLError) as failure:
        cursor.execute(f"UPDATE {database.table} SET status = 'revoked' WHERE card_id = 'C204518'")
    adapter.close()
    assert failure.value.args[0] == 1792  # cannot execute in a READ ONLY transaction


def test_mariadb_a_locked_row_never_makes_the_probe_wait(database: Database) -> None:
    writer = database.writer()
    writer.begin()
    writer.cursor().execute(
        f"UPDATE {database.table} SET status = 'revoked' WHERE card_id = 'C204518'"
    )
    adapter = database.adapter()
    try:
        start = time.monotonic()
        result = adapter.lookup(
            f"SELECT status FROM {database.table} WHERE card_id = :id", {"id": "C204518"}, 1
        )
        assert time.monotonic() - start < 1.5
        # A consistent read: the last committed value, never the writer's uncommitted one.
        assert result.rows == [{"status": "active"}]
    finally:
        writer.rollback()
        writer.close()
        adapter.close()


def test_mariadb_a_locked_table_fails_fast_never_waits(database: Database) -> None:
    writer = database.writer()
    # LOCK TABLES ... WRITE holds a metadata lock every reader must wait for.
    writer.cursor().execute(f"LOCK TABLES {database.table} WRITE")
    adapter = database.adapter()
    try:
        start = time.monotonic()
        with pytest.raises(ConnectorError, match="a lock was not granted in time"):
            adapter.lookup(
                f"SELECT status FROM {database.table} WHERE card_id = :id", {"id": "C204518"}, 1
            )
        assert time.monotonic() - start < 4
    finally:
        writer.cursor().execute("UNLOCK TABLES")
        writer.close()
    # The adapter recovers on a fresh connection.
    result = adapter.lookup(
        f"SELECT status FROM {database.table} WHERE card_id = :id", {"id": "C204518"}, 1
    )
    assert result.rows == [{"status": "active"}]
    adapter.close()


def test_mariadb_max_statement_time_stops_a_runaway_query(database: Database) -> None:
    # 100 levels, cross-joined five times: 10^10 rows to count, which never finishes in time.
    runaway = (
        "WITH RECURSIVE r (n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 100) "
        "SELECT COUNT(*) AS c FROM r AS a CROSS JOIN r AS b CROSS JOIN r AS c "
        "CROSS JOIN r AS d CROSS JOIN r AS e"
    )
    adapter = database.adapter()
    start = time.monotonic()
    with pytest.raises(ConnectorError, match="timed out after 2 s"):
        adapter.lookup(runaway, {}, 1)
    assert time.monotonic() - start < 10
    assert adapter.lookup("SELECT 1 AS one", {}, 1).rows == [{"one": 1}]
    adapter.close()


def test_mariadb_another_database_needs_allowed_databases(database: Database) -> None:
    sql = "SELECT table_name FROM information_schema.tables WHERE table_name = :t"
    with pytest.raises(GuardViolation, match="names database information_schema"):
        database.adapter().lookup(sql, {"t": database.table}, 1)
    adapter = database.adapter(allowed_databases=["information_schema"])
    assert len(adapter.lookup(sql, {"t": database.table}, 1).rows) == 1
    adapter.close()


def test_mariadb_probeowl_connects_with_no_global_privilege(database: Database) -> None:
    if database.admin_user == database.binding.user:
        pytest.skip(
            f"ProbeOwl and the checks connect as one login ({ADMIN_URL} is not set), as in CI's "
            "container, which has one test user"
        )
    adapter = database.adapter()
    adapter.lookup("SELECT 1 AS one", {}, 1)
    cursor = adapter._connection.cursor()
    cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
    grants = [row[0] for row in cursor.fetchall()]
    adapter.close()
    # On every database (*.*), only USAGE, which is the right to sign in and nothing more.
    server_wide = [g for g in grants if " ON *.* " in g]
    assert server_wide, grants
    assert all(g.startswith("GRANT USAGE ON *.* ") for g in server_wide), server_wide
    assert not [g for g in grants if "ALL PRIVILEGES" in g or "WITH GRANT OPTION" in g], grants


def test_mariadb_a_wrong_password_is_a_connector_error_without_the_password(
    database: Database,
) -> None:
    adapter = MariaDbAdapter(database.binding, "Wrong-s3cret-pw")
    with pytest.raises(ConnectorError) as failure:
        adapter.lookup("SELECT 1 AS one", {}, 1)
    assert "Wrong-s3cret-pw" not in str(failure.value)


def test_mariadb_the_connection_is_encrypted(database: Database) -> None:
    adapter = database.adapter()
    result = adapter.lookup("SELECT 1 AS one", {}, 1)
    cursor = adapter._connection.cursor()
    cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
    cipher = cursor.fetchone()[1]
    adapter.close()
    assert cipher, "the session is not encrypted"
    assert "TLS, any server certificate trusted" in result.guards


def test_mariadb_a_self_signed_certificate_is_refused_by_default(database: Database) -> None:
    adapter = database.adapter(trust_server_certificate=False)
    with pytest.raises(ConnectorError, match="certificate could not be verified"):
        adapter.lookup("SELECT 1 AS one", {}, 1)


def test_mariadb_encrypt_false_sends_in_the_clear(database: Database) -> None:
    adapter = database.adapter(trust_server_certificate=False, encrypt=False)
    result = adapter.lookup("SELECT 1 AS one", {}, 1)
    cursor = adapter._connection.cursor()
    cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
    cipher = cursor.fetchone()[1]
    adapter.close()
    assert not cipher
    assert "not encrypted" in result.guards


def test_mariadb_a_server_that_is_not_a_replica_reports_its_lag_unknown(
    database: Database,
) -> None:
    adapter = database.adapter()
    lag = adapter.replica_lag()
    assert lag.seconds is None
    assert lag.note.startswith("replica lag unknown: ")
    # The lag query leaves the session usable.
    assert adapter.lookup("SELECT 1 AS one", {}, 1).rows == [{"one": 1}]
    adapter.close()


def test_mariadb_import_reads_the_schema_never_data(database: Database) -> None:
    adapter = database.adapter()
    tables, truncated = adapter.import_schema()
    adapter.close()
    [cards] = [t for t in tables if t.table == database.table]
    assert cards.db_schema is None
    assert [(c.name, c.type, c.nullable) for c in cards.columns] == [
        ("card_id", "varchar(10)", False),
        ("visitor_profile_id", "varchar(12)", False),
        ("status", "varchar(10)", False),
        ("valid_to", "date", False),
        ("deposit", "decimal(10,2)", False),
    ]
    assert cards.primary_key == ["card_id"]
    assert ["visitor_profile_id"] in [i.columns for i in cards.indexes]
    assert cards.approx_rows is not None
    assert truncated is None
    assert "C204518" not in str(tables)  # no row data, only definitions


def test_mariadb_explain_shows_index_use_and_scans_without_running(database: Database) -> None:
    adapter = database.adapter()
    by_key = adapter.explain(
        f"SELECT card_id FROM {database.table} WHERE visitor_profile_id = :v", {"v": "VP00000001"}
    )
    by_status = adapter.explain(
        f"SELECT card_id FROM {database.table} WHERE status = :s", {"s": "active"}
    )
    adapter.close()
    assert [s.access for s in by_key] == [Access.INDEX]
    assert by_key[0].table == database.table
    [scan] = by_status
    assert (scan.access, scan.table) == (Access.SCAN, database.table)
    assert scan.table_rows is not None and scan.table_rows >= CARDS_FOR_ONE_VISITOR


def test_mariadb_explain_goes_through_the_guard(database: Database) -> None:
    with pytest.raises(GuardViolation):
        database.adapter().explain(f"DELETE FROM {database.table}", {})


def test_mariadb_a_login_that_can_read_plans_is_told_so(database: Database) -> None:
    adapter = database.adapter()
    assert adapter.unreachable() is None
    assert adapter.plan_access() == (PlanAccess.READABLE, None)
    adapter.close()


def test_mariadb_an_unreachable_server_says_why_but_not_where(database: Database) -> None:
    reason = database.adapter(port=1).unreachable()
    assert reason is not None
    assert database.binding.host not in reason and database.binding.user not in reason


# --- Safety on a real server: runbook reads, drift, Test connection, the healthy sample -------
# The rest of each rule is tested with fakes; these are the parts a real MariaDB decides.

PEOPLE = 5000  # past SMALL_TABLE_ROWS, so a whole-table read is a large one


def _people(database: Database) -> str:
    table = f"probe_people_{uuid.uuid4().hex[:8]}"
    writer = database.writer()
    cursor = writer.cursor()
    cursor.execute(
        f"CREATE TABLE {table} (id VARCHAR(20) NOT NULL PRIMARY KEY, status VARCHAR(16) NOT NULL, "
        "email VARCHAR(100) NOT NULL, created_at DATETIME NOT NULL)"
    )
    # MariaDB's sequence engine: seq_1_to_N is a table of the numbers 1 to N.
    cursor.execute(
        f"INSERT INTO {table} SELECT CONCAT('p_', LPAD(seq, 6, '0')), "
        "IF(seq % 10 = 0, 'inactive', 'active'), CONCAT('person', seq, '@example.com'), "
        f"'2026-11-01' + INTERVAL seq MINUTE FROM seq_1_to_{PEOPLE}"
    )
    cursor.execute(f"ANALYZE TABLE {table}")
    cursor.fetchall()
    writer.commit()
    writer.close()
    return table


def _drop(database: Database, table: str) -> None:
    writer = database.writer()
    writer.cursor().execute(f"DROP TABLE {table}")
    writer.close()


def test_mariadb_a_runbook_read_on_a_full_scan_is_refused_and_a_keyed_one_masked(
    database: Database, tmp_path: Path
) -> None:
    table = _people(database)
    entry = safety.catalog_table(
        table, {"id": safety.roles(ColumnRole.PRIMARY_KEY), "email": safety.roles(ColumnRole.PII)}
    )
    app = safety.application("mariadb", database.binding, [entry])
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
    assert result.outcome is Outcome.FOUND
    assert result.evidence is not None
    assert result.evidence.rows == [{"id": "p_000042", "email": MASKED}]


def test_mariadb_the_drift_flag_reads_a_real_import(database: Database) -> None:
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
            safety.application("mariadb", database.binding, [wrong]), database.adapter(), table
        )
        matched = safety.review(
            safety.application("mariadb", database.binding, [right]), database.adapter(), table
        )
    finally:
        _drop(database, table)
    assert sorted(d.message for d in drifted.drift) == [
        "column gone is not in the live schema",
        "created_at is marked indexed, but no index starts with it",
        "primary key is (email) in the catalog but (id) in the live schema",
    ]
    assert matched.drift == []


def test_mariadb_test_connection_says_a_refused_login_and_never_where(database: Database) -> None:
    app = safety.application("mariadb", database.binding, [])
    reached = safety.connection_test(app, database.adapter())
    refused = safety.connection_test(app, MariaDbAdapter(database.binding, "Wrong-s3cret-pw"))
    assert (reached.reachable, reached.reason) == (True, None)
    assert not refused.reachable
    assert refused.reason == "authentication failed; check the binding's login and secret"


def test_mariadb_the_example_healthy_sample_runs_on_the_seeds_index(database: Database) -> None:
    # The card access healthy sample as written, on a copy of visitor_profile with the dev
    # seed's ix_visitor_profile_healthy (status, type, created_at).
    sample = load_applications(EXAMPLES)["card_access"].entities["visitor"].healthy_sample
    assert sample is not None
    table = f"probe_visitors_{uuid.uuid4().hex[:8]}"
    writer = database.writer()
    cursor = writer.cursor()
    cursor.execute(
        f"CREATE TABLE {table} (id VARCHAR(12) NOT NULL PRIMARY KEY, type VARCHAR(20) NOT NULL, "
        "status VARCHAR(20) NOT NULL, created_at DATETIME NOT NULL)"
    )
    cursor.execute(f"CREATE INDEX ix_{table}_healthy ON {table} (status, type, created_at)")
    cursor.execute(
        f"INSERT INTO {table} SELECT CONCAT('VP', LPAD(seq, 8, '0')), "
        "IF(seq % 3 = 0, 'contractor', 'employee'), IF(seq % 7 = 0, 'inactive', 'active'), "
        f"'2026-01-01' + INTERVAL seq MINUTE FROM seq_1_to_{PEOPLE}"
    )
    cursor.execute(f"ANALYZE TABLE {table}")
    cursor.fetchall()
    writer.commit()
    sql = sample.lookup.replace("FROM visitor_profile", f"FROM {table}")
    app = safety.application(
        "mariadb",
        database.binding,
        [safety.catalog_table(table, {"id": safety.roles(ColumnRole.PRIMARY_KEY)})],
    )
    failing = {"visitor_profile_id": "VP00004998", "type": "contractor"}
    try:
        checked = safety.plan_check(app, sql, database.adapter())
        adapter = database.adapter()
        result = adapter.lookup(sql, failing, SAMPLE_ROWS)
        adapter.close()
    finally:
        cursor.execute(f"DROP TABLE {table}")
        writer.close()
    # Its plan uses the index, so the save check passes it.
    assert checked.status is CheckStatus.PASS, checked.message
    assert f"ix_{table}_healthy" in checked.message
    # Active contractors, newest first, never the failing visitor itself; parameters bound.
    assert [row["visitor_profile_id"] for row in result.rows] == [
        "VP00004995",
        "VP00004992",
        "VP00004989",
        "VP00004986",
    ]
    assert "%(visitor_profile_id)s" in result.query and "%(type)s" in result.query
