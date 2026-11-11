# Author: @SaadZarook
"""Reading a replica's lag in each dialect, without a server."""

import math
from typing import Any

import mssql_python
import oracledb
import pymysql
import pytest

from probeowl.connectors.mariadb import MariaDbAdapter
from probeowl.connectors.oracle import OracleAdapter, apply_lag_seconds
from probeowl.connectors.sql import LagUnknown, SqlAdapter
from probeowl.connectors.sqlserver import SqlServerAdapter
from probeowl.model import SqlBinding

BINDING = SqlBinding(host="db-ro", database="access", user="ro", secret="env:PW", replica=True)


class Connection:
    """Answers the lag query with one row (or none), or raises."""

    def __init__(self, row: Any, columns: list[str], error: Exception | None = None):
        self.row, self.columns, self.error = row, columns, error
        self.sent: list[str] = []

    def cursor(self) -> Any:
        connection = self

        class Cursor:
            def __init__(self) -> None:
                self.description = [(c,) for c in connection.columns]

            def execute(self, sql: str) -> None:
                connection.sent.append(sql)
                if connection.error is not None:
                    raise connection.error

            def fetchone(self) -> Any:
                return connection.row

            def close(self) -> None:
                pass

        return Cursor()


def with_connection(adapter: SqlAdapter, connection: Connection) -> SqlAdapter:
    adapter._connection = connection
    return adapter


MARIADB_COLUMNS = ["Slave_IO_Running", "Seconds_Behind_Master"]


def test_mariadb_reads_seconds_behind_master() -> None:
    connection = Connection(("Yes", 7), MARIADB_COLUMNS)
    adapter = with_connection(MariaDbAdapter(BINDING, "pw"), connection)
    lag = adapter.replica_lag()
    assert (lag.seconds, lag.note) == (7.0, "replica lag 7 s")
    assert connection.sent == ["SHOW REPLICA STATUS"]


def test_mariadb_stopped_replication_is_infinitely_behind() -> None:
    adapter = with_connection(
        MariaDbAdapter(BINDING, "pw"), Connection(("No", None), MARIADB_COLUMNS)
    )
    lag = adapter.replica_lag()
    assert lag.seconds == math.inf
    assert lag.note == "replica lag unknown: replication is not running"


def test_mariadb_without_replication_the_lag_is_unknown() -> None:
    adapter = with_connection(MariaDbAdapter(BINDING, "pw"), Connection(None, MARIADB_COLUMNS))
    lag = adapter.replica_lag()
    assert lag.seconds is None
    assert lag.note == (
        "replica lag unknown: the server reports no replication (SHOW REPLICA STATUS is empty)"
    )


def test_a_missing_privilege_leaves_the_lag_unknown_with_the_reason() -> None:
    denied = pymysql.err.OperationalError(1227, "Access denied; you need REPLICA MONITOR")
    adapter = with_connection(MariaDbAdapter(BINDING, "pw"), Connection(None, [], denied))
    lag = adapter.replica_lag()
    assert lag.seconds is None
    assert lag.note == "replica lag unknown: missing REPLICA MONITOR"
    assert lag.cause == "missing REPLICA MONITOR"


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ((3,), "replica lag 3 s"),
        (None, "replica lag unknown: the database is not in an availability group"),
        (
            (None,),
            "replica lag unknown: no secondary lag is reported here (this may be the primary)",
        ),
    ],
)
def test_sql_server_reads_secondary_lag_seconds(row: Any, expected: str) -> None:
    connection = Connection(row, ["secondary_lag_seconds"])
    adapter = with_connection(SqlServerAdapter(BINDING, "pw"), connection)
    assert adapter.replica_lag().note == expected
    assert "sys.dm_hadr_database_replica_states" in connection.sent[0]


@pytest.mark.parametrize(
    ("value", "seconds"),
    [("+00 00:00:05", 5.0), ("+01 02:03:04", 93784.0), ("00 00:01:00", 60.0)],
)
def test_oracle_apply_lag_is_an_interval(value: str, seconds: float) -> None:
    assert apply_lag_seconds(value) == seconds


@pytest.mark.parametrize("value", [None, "", "unknown"])
def test_oracle_without_an_apply_lag_it_is_unknown(value: str | None) -> None:
    with pytest.raises(LagUnknown, match="Data Guard reports no apply lag"):
        apply_lag_seconds(value)


def test_oracle_on_a_database_that_is_not_a_standby() -> None:
    adapter = with_connection(OracleAdapter(BINDING, "pw"), Connection(None, ["value"]))
    assert adapter.replica_lag().note == (
        "replica lag unknown: this database is not a Data Guard standby"
    )


def test_oracle_without_access_to_the_view_it_is_unknown() -> None:
    error = oracledb.DatabaseError("ORA-00942: table or view does not exist")
    adapter = with_connection(OracleAdapter(BINDING, "pw"), Connection(None, [], error))
    assert (
        adapter.replica_lag().note
        == "replica lag unknown: missing SELECT on SYS.V_$DATAGUARD_STATS"
    )


@pytest.mark.parametrize(
    ("adapter", "error", "cause"),
    [
        (
            MariaDbAdapter(BINDING, "pw"),
            pymysql.err.OperationalError(1227, "Access denied; you need REPLICA MONITOR"),
            "missing REPLICA MONITOR",
        ),
        (
            MariaDbAdapter(BINDING, "pw"),
            pymysql.err.OperationalError(
                1227, "Access denied; you need (at least one of) the SLAVE MONITOR privilege(s)"
            ),
            "missing SLAVE MONITOR",
        ),
        (
            MariaDbAdapter(BINDING, "pw"),
            pymysql.err.OperationalError(1227, "Access denied"),
            "missing REPLICA MONITOR",
        ),
        (
            OracleAdapter(BINDING, "pw"),
            oracledb.DatabaseError("ORA-00942: table or view does not exist"),
            "missing SELECT on SYS.V_$DATAGUARD_STATS",
        ),
        (
            OracleAdapter(BINDING, "pw"),
            oracledb.DatabaseError("ORA-01031: insufficient privileges"),
            "missing SELECT on SYS.V_$DATAGUARD_STATS",
        ),
    ],
)
def test_a_missing_privilege_is_named_as_the_cause(
    adapter: SqlAdapter, error: Exception, cause: str
) -> None:
    lag = with_connection(adapter, Connection(None, [], error)).replica_lag()
    assert lag.cause == cause


def test_a_server_that_is_not_a_replica_says_so_as_the_cause() -> None:
    lag = with_connection(SqlServerAdapter(BINDING, "pw"), Connection(None, ["x"])).replica_lag()
    assert lag.cause == "the database is not in an availability group"


def test_sql_server_names_the_privilege_it_said_was_missing() -> None:
    # The server's words on the dev stack, from CI.
    denied = mssql_python.Error(
        "Statement execution failed",
        "[SQL Server]VIEW DATABASE PERFORMANCE STATE permission denied in database 'wallet'.",
    )
    adapter = with_connection(SqlServerAdapter(BINDING, "pw"), Connection(None, [], denied))
    assert adapter.replica_lag().cause == "missing VIEW DATABASE PERFORMANCE STATE"
    assert (
        adapter.replica_lag().note == "replica lag unknown: missing VIEW DATABASE PERFORMANCE STATE"
    )


@pytest.mark.parametrize(
    ("refusal", "grant"),
    [
        (
            "The user does not have the external policy action '...' or permission "
            "'VIEW SERVER PERFORMANCE STATE' to perform this action.",
            "VIEW SERVER PERFORMANCE STATE",
        ),
        ("VIEW SERVER STATE permission was denied on object 'server'", "VIEW SERVER STATE"),
        # Names none: what SQL Server 2022 asked for on the dev stack.
        ("permission was denied", "VIEW DATABASE PERFORMANCE STATE"),
    ],
)
def test_sql_server_names_the_grant_the_server_asked_for(refusal: str, grant: str) -> None:
    denied = mssql_python.Error("Statement execution failed", f"[SQL Server]{refusal}")
    adapter = with_connection(SqlServerAdapter(BINDING, "pw"), Connection(None, [], denied))
    lag = adapter.replica_lag()
    assert lag.note == f"replica lag unknown: missing {grant}"


def test_only_the_grants_name_is_taken_from_the_refusal() -> None:
    denied = mssql_python.Error(
        "Statement execution failed",
        "[SQL Server]VIEW SERVER STATE permission denied for login 'probeowl_ro' on db-ro,1433",
    )
    adapter = with_connection(SqlServerAdapter(BINDING, "pw"), Connection(None, [], denied))
    lag = adapter.replica_lag()
    assert "probeowl_ro" not in lag.note + (lag.cause or "")
    assert "db-ro" not in lag.note + (lag.cause or "")
