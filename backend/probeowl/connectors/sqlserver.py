# Author: @SaadZarook
"""SQL Server through mssql-python: never wait for a lock, never run long, never go parallel."""

import re
from collections.abc import Generator, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree

import mssql_python
import sqlglot
from mssql_python.constants import ConstantsDDBC
from mssql_python.exceptions import ConnectionStringParseError
from sqlglot import exp

from probeowl.connectors.failures import Failure
from probeowl.connectors.plan import Access, PlanStep
from probeowl.connectors.schema import ColumnRow, IndexColumnRow, TableKey, assemble
from probeowl.connectors.sql import LagUnknown, SqlAdapter
from probeowl.errors import ConfigError, ConnectorError
from probeowl.guards.dialects import SQLSERVER
from probeowl.guards.statement import GuardedQuery, ParameterValue
from probeowl.model.connector import SqlBinding
from probeowl.model.schema import TableSchema

DEFAULT_PORT = 1433
LOGIN_TIMEOUT_S = 5

# Applied once per connection. LOCK_TIMEOUT 0: a locked row fails the lookup at once instead
# of waiting behind a writer. DEADLOCK_PRIORITY LOW: if a deadlock happens anyway, the probe
# is the one SQL Server chooses to end. READ COMMITTED: no dirty reads, which would turn a
# half-written transaction into a false finding.
_SESSION = (
    "SET LOCK_TIMEOUT 0; SET DEADLOCK_PRIORITY LOW; SET TRANSACTION ISOLATION LEVEL READ COMMITTED;"
)

# Varchar unless the catalog says otherwise: an nvarchar parameter against a varchar column
# makes SQL Server convert the column, which turns an index seek into a scan.
_VARCHAR = (mssql_python.SQL_VARCHAR, 8000, 0)
_NVARCHAR = (mssql_python.SQL_WVARCHAR, 4000, 0)
_WIDE_TYPES = ("nvarchar", "nchar", "ntext")
# Not among the package's exported names, only in its constants.
_DATETIMEOFFSET = ConstantsDDBC.SQL_DATETIMEOFFSET.value

_COLUMNS = (
    "SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, "
    "c.CHARACTER_MAXIMUM_LENGTH, c.NUMERIC_PRECISION, c.NUMERIC_SCALE, c.IS_NULLABLE "
    "FROM INFORMATION_SCHEMA.COLUMNS c JOIN INFORMATION_SCHEMA.TABLES t "
    "ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME "
    "WHERE t.TABLE_TYPE = 'BASE TABLE' ORDER BY c.TABLE_SCHEMA, c.TABLE_NAME, c.ORDINAL_POSITION"
)
# Key columns only (not INCLUDE columns), in key order; heaps (type 0) have no index.
_INDEX_COLUMNS = (
    "SELECT s.name, t.name, i.name, i.is_unique, i.is_primary_key, c.name "
    "FROM sys.indexes i JOIN sys.tables t ON t.object_id = i.object_id "
    "JOIN sys.schemas s ON s.schema_id = t.schema_id "
    "JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
    "JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id "
    "WHERE i.type > 0 AND ic.is_included_column = 0 "
    "ORDER BY s.name, t.name, i.name, ic.key_ordinal"
)
_ROW_ESTIMATES = (
    "SELECT s.name, t.name, SUM(p.rows) FROM sys.tables t "
    "JOIN sys.schemas s ON s.schema_id = t.schema_id "
    "JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0, 1) "
    "GROUP BY s.name, t.name"
)
# Types whose size is written after them, and how.
_SIZED_BY_LENGTH = {"char", "varchar", "nchar", "nvarchar", "binary", "varbinary"}
_SIZED_BY_PRECISION = {"decimal", "numeric"}
_LAG_QUERY = (
    "SELECT secondary_lag_seconds FROM sys.dm_hadr_database_replica_states "
    "WHERE is_local = 1 AND database_id = DB_ID()"
)
_HAS_SHOWPLAN = "SELECT HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'SHOWPLAN')"
_LOCK_REFUSED = "Lock request time out period exceeded"
_TIMED_OUT = ("Query timeout expired", "timeout expired", "HYT00")
_UNTRUSTED = ("certificate verify failed", "certificate chain was issued by an authority")
_CONNECTION = (
    "Login timeout expired",
    "TCP Provider",
    "network-related",
    "actively refused",
    "unable to establish connection",
    "Communication link failure",
    "08001",
)


class SqlServerAdapter(SqlAdapter):
    """A read-only SQL Server connection."""

    rules = SQLSERVER
    replica_role = "Readable secondary"
    # SQL Server names the permission it refused; 2022 asked for this one on the dev stack.
    lag_privilege = "VIEW DATABASE PERFORMANCE STATE"
    lag_grant_named = re.compile(r"\b(VIEW (?:SERVER|DATABASE) (?:PERFORMANCE |SECURITY )?STATE)\b")
    lag_denied = ("permission was denied", "permission denied", "VIEW SERVER", "VIEW DATABASE")
    plan_privilege = "GRANT SHOWPLAN TO <login> in the database"
    plan_denied = ("SHOWPLAN permission denied",)

    def _connect(self) -> Any:
        try:
            # connect(timeout=) is the login timeout, as in pyodbc; the statement timeout is
            # the connection's `timeout` property, which defaults to none at all.
            connection = mssql_python.connect(
                connection_string(self._binding, self._password),
                autocommit=True,
                timeout=LOGIN_TIMEOUT_S,
            )
        except ConnectionStringParseError as err:
            # Not a driver Error: the binding's values do not form a connection string. The
            # parser's text may echo values, the password among them, so it is not repeated.
            raise ConfigError(
                "the binding does not form a valid SQL Server connection string; "
                "check its host, port, database and user"
            ) from err
        connection.timeout = self._limits.statement_timeout_s
        cursor = connection.cursor()
        cursor.execute(_SESSION)
        cursor.close()
        return connection

    def _run(
        self, query: GuardedQuery, parameter_types: Mapping[str, str]
    ) -> Generator[dict[str, Any]]:
        cursor = self._connection.cursor()
        try:
            values = list(query.parameters) if isinstance(query.parameters, list) else []
            if values:
                cursor.setinputsizes(
                    [
                        input_size(v, parameter_types.get(n))
                        for n, v in zip(query.names, values, strict=True)
                    ]
                )
            cursor.execute(self._sql_sent(query), values)
            columns = [column[0] for column in cursor.description]
            while batch := cursor.fetchmany(100):
                for row in batch:
                    yield dict(zip(columns, row, strict=True))
        finally:
            cursor.close()

    def _sql_sent(self, query: GuardedQuery) -> str:
        # The guard has added OPTION (MAXDOP 1), after the last token (rules.query_options).
        return query.sql

    def _session_guards(self) -> list[str]:
        guards = [
            "SET LOCK_TIMEOUT 0",
            "SET DEADLOCK_PRIORITY LOW",
            "READ COMMITTED",
            f"statement timeout {self._limits.statement_timeout_s} s",
            "OPTION (MAXDOP 1)",
        ]
        if self._binding.replica:
            guards.append("ApplicationIntent=ReadOnly")
        return guards

    def _can_read_plans(self) -> bool:
        # SHOWPLAN is only checked for a statement that reads a table, so a trial on one that
        # reads none succeeds for any login. The server is asked instead.
        [(granted,)] = self._query(_HAS_SHOWPLAN)
        return bool(granted)

    def _explain(self, query: GuardedQuery, parameter_types: Mapping[str, str]) -> list[PlanStep]:
        # With SHOWPLAN_XML on, the server returns the plan instead of running the statement.
        # mssql-python returns that plan only for a statement executed directly, which cannot
        # carry bound parameters: the placeholders become fixed literals of each one's type.
        sql = plan_sql(query, parameter_types)
        cursor = self._connection.cursor()
        try:
            cursor.execute("SET SHOWPLAN_XML ON")
            try:
                cursor.execute(sql, use_prepare=False)
                row = cursor.fetchone()
            finally:
                cursor.execute("SET SHOWPLAN_XML OFF")
        finally:
            cursor.close()
        if row is None:
            raise ConnectorError("SQL Server returned no plan for the lookup")
        return showplan_steps(str(row[0]))

    def _read_schema(self) -> tuple[list[TableSchema], str | None]:
        # Every schema of the binding's database (the connection's own database).
        columns = [
            ColumnRow(
                (schema, table), name, _type(data_type, length, precision, scale), nullable == "YES"
            )
            for schema, table, name, data_type, length, precision, scale, nullable in self._query(
                _COLUMNS
            )
        ]
        index_columns = [
            IndexColumnRow((schema, table), index, bool(unique), bool(primary), column)
            for schema, table, index, unique, primary, column in self._query(_INDEX_COLUMNS)
        ]
        rows: dict[TableKey, int | None] = {
            (schema, table): None if estimate is None else int(estimate)
            for schema, table, estimate in self._query(_ROW_ESTIMATES)
        }
        return assemble(columns, index_columns, rows)

    def _read_lag(self) -> float:
        # Needs VIEW DATABASE PERFORMANCE STATE (VIEW DATABASE STATE before SQL Server 2022).
        cursor = self._connection.cursor()
        try:
            cursor.execute(_LAG_QUERY)
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            raise LagUnknown("the database is not in an availability group")
        if row[0] is None:
            raise LagUnknown("no secondary lag is reported here (this may be the primary)")
        return float(row[0])

    def _driver_error(self) -> type[Exception]:
        return mssql_python.Error

    def _classify(self, err: Exception) -> Failure:
        message = str(err)
        if any(marker in message for marker in _UNTRUSTED):
            return Failure.TLS_NOT_TRUSTED
        if "Login failed" in message:
            return Failure.AUTHENTICATION
        # Before the timeouts: "Login timeout expired" is about reaching the server.
        if any(marker in message for marker in _CONNECTION):
            return Failure.CONNECTION
        if _LOCK_REFUSED in message:
            return Failure.LOCK_NOT_GRANTED
        if any(marker in message for marker in _TIMED_OUT):
            return Failure.TIMED_OUT
        return Failure.REFUSED if "[SQL Server]" in message else Failure.UNKNOWN


_SHOWPLAN = "{http://schemas.microsoft.com/sqlserver/2004/07/showplan}"
_SEEKS = {"Index Seek", "Clustered Index Seek"}
_SCANS = {"Table Scan", "Clustered Index Scan", "Index Scan"}


def showplan_steps(plan: str) -> list[PlanStep]:
    """The operators of a SHOWPLAN_XML plan that read a table or index, in plan order."""
    steps = []
    # The plan comes from the server just asked, not from a person: plain parsing is enough.
    for relop in ElementTree.fromstring(plan).iter(f"{_SHOWPLAN}RelOp"):
        operation = relop.get("PhysicalOp", "")
        target = relop.find(f"./*/{_SHOWPLAN}Object")
        if target is None:
            continue
        table = (target.get("Table") or "").strip("[]").lower() or None
        index = (target.get("Index") or "").strip("[]") or None
        cardinality = relop.get("TableCardinality")
        rows = int(float(cardinality)) if cardinality else None
        if operation in _SEEKS:
            steps.append(PlanStep(operation, Access.INDEX, table, index, rows))
        elif operation in _SCANS:
            steps.append(PlanStep(operation, Access.SCAN, table, index, rows))
        else:
            steps.append(PlanStep(operation, Access.OTHER, table, index, rows))
    return steps


def _type(name: str, length: int | None, precision: int | None, scale: int | None) -> str:
    if name in _SIZED_BY_LENGTH and length is not None:
        return f"{name}({'max' if length == -1 else length})"
    if name in _SIZED_BY_PRECISION and precision is not None:
        return f"{name}({precision},{scale or 0})"
    return name


def connection_string(binding: SqlBinding, password: str) -> str:
    """Build an mssql-python connection string for a binding."""
    parts = {
        "Server": f"{binding.host},{binding.port or DEFAULT_PORT}",
        "Database": binding.database,
        "UID": binding.user,
        "PWD": password,
        # Always encrypted: the model refuses encrypt: false for SQL Server.
        "Encrypt": "yes",
        "TrustServerCertificate": "yes" if binding.trust_server_certificate else "no",
        # On an availability group, read-only intent routes the session to a readable secondary.
        "ApplicationIntent": "ReadOnly" if binding.replica else None,
    }
    return ";".join(f"{key}={_quote(value)}" for key, value in parts.items() if value)


def _quote(value: str) -> str:
    # ODBC: a value containing ; { or } must be braced, with } doubled.
    if any(c in value for c in ";{}") or value != value.strip():
        return "{" + value.replace("}", "}}") + "}"
    return value


def plan_sql(query: GuardedQuery, parameter_types: Mapping[str, str]) -> str:
    """The guarded query with each ? as a fixed literal of its parameter's type, for a plan.

    Only the type is kept, never the value, so nothing a person typed reaches the SQL text;
    varchar or nvarchar as a run binds it, since a mismatch changes the plan.
    """
    values = list(query.parameters) if isinstance(query.parameters, list) else []
    types = iter(
        input_size(v, parameter_types.get(n)) for n, v in zip(query.names, values, strict=True)
    )

    def literal(node: exp.Expr) -> exp.Expr:
        if not isinstance(node, exp.Placeholder):
            return node
        sql_type = next(types)[0]
        if sql_type == mssql_python.SQL_WVARCHAR:
            return exp.National(this="0")
        if sql_type == mssql_python.SQL_VARCHAR:
            return exp.Literal.string("0")
        if sql_type in (mssql_python.SQL_TYPE_TIMESTAMP, mssql_python.SQL_TYPE_DATE):
            return exp.Literal.string("2000-01-01")
        if sql_type == mssql_python.SQL_TYPE_TIME:
            return exp.Literal.string("00:00:00")
        if sql_type == _DATETIMEOFFSET:
            return exp.Literal.string("2000-01-01 00:00:00 +00:00")
        return exp.Literal.number(0)

    tree = sqlglot.parse_one(query.sql, read="tsql")
    return tree.transform(literal).sql("tsql")


def input_size(value: ParameterValue, declared: str | None) -> tuple[int, int, int]:
    """The SQL type a parameter is sent as."""
    if isinstance(value, str) or value is None:
        wide = declared is not None and declared.lower().startswith(_WIDE_TYPES)
        return _NVARCHAR if wide else _VARCHAR
    if isinstance(value, bool):
        return (mssql_python.SQL_BIT, 1, 0)
    if isinstance(value, int):
        return (mssql_python.SQL_BIGINT, 19, 0)
    if isinstance(value, float):
        return (mssql_python.SQL_DOUBLE, 53, 0)
    if isinstance(value, Decimal):
        exponent = value.as_tuple().exponent
        scale = -exponent if isinstance(exponent, int) and exponent < 0 else 0
        return (mssql_python.SQL_DECIMAL, 38, scale)
    if isinstance(value, datetime):
        # With an offset, the server compares it and the value is never shifted. Many tables keep
        # local time in a plain datetime2, where a conversion to UTC would move a window by hours.
        if value.tzinfo is not None:
            return (_DATETIMEOFFSET, 34, 7)
        return (mssql_python.SQL_TYPE_TIMESTAMP, 27, 7)
    if isinstance(value, date):
        return (mssql_python.SQL_TYPE_DATE, 10, 0)
    if isinstance(value, time):
        return (mssql_python.SQL_TYPE_TIME, 16, 7)
    raise TypeError(f"no SQL Server type for {type(value).__name__}")
