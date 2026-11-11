# Author: @SaadZarook
"""MariaDB through PyMySQL: consistent reads, a server-side statement timeout, no lock waits."""

import math
import re
from collections.abc import Generator, Mapping
from datetime import datetime
from typing import Any

import pymysql
from pymysql.constants.CR import CR_SSL_CONNECTION_ERROR

from probeowl.checks.sql_shape import Conversion, placeholder_conversions
from probeowl.connectors.failures import Failure
from probeowl.connectors.plan import Access, PlanStep
from probeowl.connectors.schema import ColumnRow, IndexColumnRow, TableKey, assemble
from probeowl.connectors.sql import (
    LagUnknown,
    SqlAdapter,
    trust_any_certificate,
)
from probeowl.errors import ConnectorError
from probeowl.guards.dialects import MARIADB
from probeowl.guards.statement import GuardedQuery
from probeowl.model.connector import SqlBinding
from probeowl.model.schema import TableSchema

DEFAULT_PORT = 3306
CONNECT_TIMEOUT_S = 5
LOCK_WAIT_S = 1
# The server stops a statement at max_statement_time; the client stops waiting a little
# later, in case the server cannot.
CLIENT_GRACE_S = 5

_COLUMNS = (
    "SELECT c.TABLE_NAME, c.COLUMN_NAME, c.COLUMN_TYPE, c.IS_NULLABLE "
    "FROM information_schema.COLUMNS c JOIN information_schema.TABLES t "
    "ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
    "WHERE c.TABLE_SCHEMA = DATABASE() AND t.TABLE_TYPE = 'BASE TABLE' "
    "ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION"
)
_INDEX_COLUMNS = (
    "SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, COLUMN_NAME FROM information_schema.STATISTICS "
    "WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
)
_ROW_ESTIMATES = (
    "SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES "
    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE'"
)
_LOCK_REFUSED = (1205,)  # ER_LOCK_WAIT_TIMEOUT: a row or metadata lock not granted in time
_TIMED_OUT = (1969, 3024)  # max_statement_time exceeded (MariaDB, MySQL)
_AUTHENTICATION = (1045,)  # ER_ACCESS_DENIED_ERROR
_CONNECTION = (2002, 2003, 2006, 2013)  # no socket, no host, gone away, lost


class MariaDbAdapter(SqlAdapter):
    """A read-only MariaDB connection.

    InnoDB reads are consistent reads: under READ COMMITTED a probe reads the last
    committed row while a writer holds its lock. What can still wait is a metadata lock
    (a table locked for DDL or LOCK TABLES): lock_wait_timeout of 1 s makes that fail
    fast instead of queueing behind it.
    """

    rules = MARIADB
    replica_role = "Read replica"
    lag_privilege = "REPLICA MONITOR"
    # "Access denied; you need (at least one of) the REPLICA MONITOR privilege(s) ..."
    lag_grant_named = re.compile(r"\b(REPLICA MONITOR|SLAVE MONITOR|REPLICATION CLIENT)\b")
    lag_denied = ("(1227,", "(1142,")
    plan_privilege = "SELECT on the tables its lookups read"
    plan_denied = ("(1044,", "(1142,")

    def _connect(self) -> Any:
        timeout = self._limits.statement_timeout_s
        connection = pymysql.connect(
            host=self._binding.host,
            port=self._binding.port or DEFAULT_PORT,
            user=self._binding.user,
            password=self._password,
            database=self._binding.database,
            charset="utf8mb4",
            autocommit=True,
            connect_timeout=CONNECT_TIMEOUT_S,
            read_timeout=timeout + CLIENT_GRACE_S,
            write_timeout=timeout + CLIENT_GRACE_S,
            **_tls(self._binding),
        )
        # One statement at a time: the connection never allows several in one call.
        cursor = connection.cursor()
        for setting in (
            "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY",
            f"SET SESSION max_statement_time = {timeout}",
            f"SET SESSION lock_wait_timeout = {LOCK_WAIT_S}",
            f"SET SESSION innodb_lock_wait_timeout = {LOCK_WAIT_S}",
        ):
            cursor.execute(setting)
        cursor.close()
        return connection

    def _refuse_values(self, sql: str, parameters: Mapping[str, object]) -> None:
        # MariaDB has no type with an offset, and PyMySQL sends a time with one as its clock time,
        # the offset dropped. Only a lookup that converts it itself says which clock it means: the
        # save check passes that and nothing else, and so does this, for a lookup it never saw.
        aware = [
            name
            for name, value in parameters.items()
            if isinstance(value, datetime) and value.tzinfo is not None
        ]
        if not aware:
            return
        uses = placeholder_conversions(sql, self.rules.sqlglot_dialect)
        for name in aware:
            if any(conversion is not Conversion.DROPS for conversion in uses.get(name, [])):
                raise ConnectorError(
                    f":{name} is a time with an offset, and MariaDB has no type with one: it "
                    "would be sent as its clock time, the offset dropped. Convert it in the "
                    f"lookup, for example CAST(:{name} AS DATETIME), so the save check can "
                    "pass it"
                )

    def _run(
        self, query: GuardedQuery, parameter_types: Mapping[str, str]
    ) -> Generator[dict[str, Any]]:
        cursor = self._connection.cursor()
        try:
            cursor.execute(query.sql, query.parameters)
            columns = [column[0] for column in cursor.description]
            while batch := cursor.fetchmany(100):
                for row in batch:
                    yield dict(zip(columns, row, strict=True))
        finally:
            cursor.close()

    def _sql_sent(self, query: GuardedQuery) -> str:
        return query.sql

    def _session_guards(self) -> list[str]:
        return [
            "READ COMMITTED, READ ONLY",
            f"max_statement_time {self._limits.statement_timeout_s} s",
            f"lock_wait_timeout {LOCK_WAIT_S} s",
            f"innodb_lock_wait_timeout {LOCK_WAIT_S} s",
        ]

    def _explain(self, query: GuardedQuery, parameter_types: Mapping[str, str]) -> list[PlanStep]:
        # EXPLAIN reads the plan only: the SELECT is not run and nothing is written.
        cursor = self._connection.cursor()
        try:
            cursor.execute("EXPLAIN " + query.sql, query.parameters)
            columns = [column[0].lower() for column in cursor.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        finally:
            cursor.close()
        return [explain_step(row) for row in rows]

    def _read_schema(self) -> tuple[list[TableSchema], str | None]:
        # The binding's database only: DATABASE() is the schema in MariaDB.
        columns = [
            ColumnRow((None, table), name, type_, nullable == "YES")
            for table, name, type_, nullable in self._query(_COLUMNS)
        ]
        index_columns = [
            IndexColumnRow((None, table), index, non_unique == 0, index == "PRIMARY", column)
            for table, index, non_unique, column in self._query(_INDEX_COLUMNS)
        ]
        rows: dict[TableKey, int | None] = {
            (None, table): None if estimate is None else int(estimate)
            for table, estimate in self._query(_ROW_ESTIMATES)
        }
        return assemble(columns, index_columns, rows)

    def _read_lag(self) -> float:
        # Needs the REPLICA MONITOR privilege on the probe login.
        cursor = self._connection.cursor()
        try:
            cursor.execute("SHOW REPLICA STATUS")
            row = cursor.fetchone()
            columns = [column[0] for column in cursor.description or []]
        finally:
            cursor.close()
        if row is None:
            raise LagUnknown("the server reports no replication (SHOW REPLICA STATUS is empty)")
        seconds = dict(zip(columns, row, strict=True)).get("Seconds_Behind_Master")
        # NULL while the replication threads are stopped: the replica falls further behind.
        return math.inf if seconds is None else float(seconds)

    def _driver_error(self) -> type[Exception]:
        return pymysql.err.MySQLError

    def _driver_text(self, err: Exception) -> str:
        # PyMySQL's str() is a tuple, whose quotes would mask the whole message.
        return " ".join(str(arg) for arg in err.args)

    def _classify(self, err: Exception) -> Failure:
        code = err.args[0] if err.args and isinstance(err.args[0], int) else None
        if code in _LOCK_REFUSED:
            return Failure.LOCK_NOT_GRANTED
        if code in _TIMED_OUT:
            return Failure.TIMED_OUT
        if "CERTIFICATE_VERIFY_FAILED" in str(err):
            return Failure.TLS_NOT_TRUSTED
        if code == CR_SSL_CONNECTION_ERROR:
            return Failure.TLS_NOT_OFFERED
        if code in _AUTHENTICATION:
            return Failure.AUTHENTICATION
        if code in _CONNECTION:
            return Failure.CONNECTION
        return Failure.REFUSED if code is not None else Failure.UNKNOWN


# MariaDB's EXPLAIN `type`: ALL reads every row; index reads every entry of an index.
_FULL = {"ALL": "full table scan (ALL)", "index": "full index scan (index)"}


def explain_step(row: Mapping[str, Any]) -> PlanStep:
    """One row of MariaDB's EXPLAIN as a plan step."""
    kind = str(row.get("type") or "")
    table = str(row["table"]).lower() if row.get("table") else None
    key = str(row["key"]) if row.get("key") else None
    rows = int(row["rows"]) if row.get("rows") is not None else None
    if kind in _FULL:
        return PlanStep(_FULL[kind], Access.SCAN, table, key, rows)
    if key is not None:
        return PlanStep(kind, Access.INDEX, table, key, rows)
    return PlanStep(kind or str(row.get("extra") or "step"), Access.OTHER, table)


def _tls(binding: SqlBinding) -> dict[str, Any]:
    """PyMySQL's TLS arguments. Any of them makes TLS required, never just preferred."""
    if not binding.encrypt:
        return {"ssl_disabled": True}
    if binding.trust_server_certificate:
        return {"ssl": trust_any_certificate()}
    # Verified against the system's trusted authorities, and the host name must match.
    return {"ssl_verify_cert": True, "ssl_verify_identity": True}
