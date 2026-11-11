# Author: @SaadZarook
"""Oracle through python-oracledb in thin mode: no client libraries, no waiting, no rounding."""

import re
import uuid
from collections.abc import Generator, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import oracledb

from probeowl.connectors.failures import Failure
from probeowl.connectors.plan import Access, PlanStep
from probeowl.connectors.schema import ColumnRow, IndexColumnRow, TableKey, assemble
from probeowl.connectors.sql import (
    LagUnknown,
    SqlAdapter,
    SqlLimits,
    trust_any_certificate,
)
from probeowl.errors import ConfigError, ConnectorError
from probeowl.guards.dialects import ORACLE
from probeowl.guards.statement import GuardedQuery, ParameterValue
from probeowl.model.connector import SqlBinding
from probeowl.model.schema import TableSchema

DEFAULT_PORT = 1521
CONNECT_TIMEOUT_S = 5

# An unquoted Oracle identifier, as a schema name in a binding must be.
_SCHEMA = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")
_TIMED_OUT = ("DPY-4024", "DPI-1067", "ORA-03156")
_CANNOT_CONNECT = "DPY-6005"
_AUTHENTICATION = ("ORA-01017", "ORA-28000", "ORA-28001")  # bad login, locked, expired
_CONNECTION = (
    _CANNOT_CONNECT,
    "DPY-6001",
    "DPY-6003",
    "DPY-4011",
    "ORA-12170",
    "ORA-12514",
    "ORA-12541",
    "ORA-12543",
    "ORA-12545",
)
_PLAN_ROWS = (
    "SELECT operation, options, object_name FROM plan_table "
    "WHERE statement_id = :statement ORDER BY id"
)
# Thin mode reads a TIMESTAMP WITH TIME ZONE as its clock time with no offset, and refuses a named
# zone (DPY-3022), so such a column is never returned as if it were exact.
WITH_TIME_ZONE = (
    "{column} is TIMESTAMP WITH TIME ZONE, which ProbeOwl cannot read with its offset: select "
    "SYS_EXTRACT_UTC({column}) for its UTC instant, or "
    "TO_CHAR({column}, 'YYYY-MM-DD HH24:MI:SS TZH:TZM')"
)
# Oracle 23ai and later name the view a login could not read. SYS's views always exist, so a
# refusal naming one, such as v$session's SYS.V_$SESSION, means the login lacks SELECT on it.
_SYS_VIEW_REFUSED = re.compile(r'ORA-00942: table or view "SYS"\."(G?V_\$\w+)" does not exist')
_INDEX_ACCESS = {"UNIQUE SCAN", "RANGE SCAN", "RANGE SCAN DESCENDING", "SKIP SCAN"}
_COLUMNS = (
    "SELECT c.table_name, c.column_name, c.data_type, c.char_length, c.data_precision, "
    "c.data_scale, c.nullable FROM all_tab_columns c JOIN all_tables t "
    "ON t.owner = c.owner AND t.table_name = c.table_name "
    "WHERE c.owner = :owner ORDER BY c.table_name, c.column_id"
)
_PRIMARY_KEYS = (
    "SELECT cc.table_name, cc.column_name FROM all_constraints k JOIN all_cons_columns cc "
    "ON cc.owner = k.owner AND cc.constraint_name = k.constraint_name "
    "WHERE k.owner = :owner AND k.constraint_type = 'P' ORDER BY cc.table_name, cc.position"
)
_INDEX_COLUMNS = (
    "SELECT i.table_name, i.index_name, i.uniqueness, ic.column_name FROM all_indexes i "
    "JOIN all_ind_columns ic ON ic.index_owner = i.owner AND ic.index_name = i.index_name "
    "WHERE i.table_owner = :owner ORDER BY i.table_name, i.index_name, ic.column_position"
)
_ROW_ESTIMATES = "SELECT table_name, num_rows FROM all_tables WHERE owner = :owner"
_SIZED_BY_LENGTH = {"CHAR", "VARCHAR2", "NCHAR", "NVARCHAR2", "RAW"}
# Data Guard's apply lag, as an interval: +DD HH:MI:SS.
_APPLY_LAG = re.compile(r"^\+?(\d+) (\d{2}):(\d{2}):(\d{2})$")


class OracleAdapter(SqlAdapter):
    """A read-only Oracle connection.

    Readers never wait for writers in Oracle (multi-version reads from undo), so a probe
    sees the last committed row while a writer holds its lock. FOR UPDATE, the one way a
    query could wait, is refused by the statement guard.
    """

    rules = ORACLE
    replica_role = "Active Data Guard standby"
    # ORA-00942 names no grant. V$DATAGUARD_STATS is a public synonym: the grant is on the view.
    lag_privilege = "SELECT on SYS.V_$DATAGUARD_STATS"
    lag_denied = ("ORA-00942", "ORA-01031")
    # EXPLAIN PLAN writes to PLAN_TABLE and is rolled back: a read-only standby refuses it.
    plan_privilege = "write access to PLAN_TABLE (public by default; a read-only standby has none)"
    plan_denied = ("ORA-00942", "ORA-01031", "ORA-16000")
    plan_trial = "SELECT 1 AS one FROM dual"

    def __init__(self, binding: SqlBinding, password: str, limits: SqlLimits | None = None):
        super().__init__(binding, password, limits)
        self._time_zone: str | None = None  # the session's, once connected

    def _connect(self) -> Any:
        schema = _schema(self._binding)
        connection = oracledb.connect(
            user=self._binding.user,
            password=self._password,
            host=self._binding.host,
            port=self._binding.port or DEFAULT_PORT,
            service_name=self._binding.database,
            tcp_connect_timeout=CONNECT_TIMEOUT_S,
            **_tls(self._binding),
        )
        # Bounds every round trip, so a long-running statement is cut off by the client
        # even where Resource Manager is not set up.
        connection.call_timeout = self._limits.statement_timeout_s * 1000
        connection.outputtypehandler = _exact_types
        if schema is not None:
            connection.current_schema = schema
        # A table with a parallel degree would otherwise be read by many server processes.
        # This overrides the table's degree and any hint; the guard refuses hints as well.
        cursor = connection.cursor()
        cursor.execute("ALTER SESSION DISABLE PARALLEL QUERY")
        # Thin mode would set the session's time zone to this machine's. The binding's offset or
        # the database's own decide instead: CURRENT_DATE, LOCALTIMESTAMP and how a TIMESTAMP
        # compares with a TIMESTAMP WITH TIME ZONE follow it. The offset was checked by the model.
        cursor.execute(f"ALTER SESSION SET TIME_ZONE = {time_zone_setting(self._binding)[0]}")
        cursor.execute("SELECT SESSIONTIMEZONE FROM dual")
        row = cursor.fetchone()
        self._time_zone = str(row[0]) if row else None
        cursor.close()
        return connection

    def _run(
        self, query: GuardedQuery, parameter_types: Mapping[str, str]
    ) -> Generator[dict[str, Any]]:
        cursor = self._connection.cursor()
        # With rows prefetched, thin mode decodes the first rows during execute, and a named time
        # zone fails there (DPY-3022) before the column types below can be checked.
        cursor.prefetchrows = 0
        try:
            _declare_types(cursor, query)
            cursor.execute(query.sql, _bound(query))
            # Oracle returns unquoted names in upper case; probes and the catalog use lower.
            columns = [column[0].lower() for column in cursor.description]
            for name, column in zip(columns, cursor.description, strict=True):
                if column[1] is oracledb.DB_TYPE_TIMESTAMP_TZ:
                    raise ConnectorError(WITH_TIME_ZONE.format(column=name))
            while batch := cursor.fetchmany(100):
                for row in batch:
                    yield dict(zip(columns, row, strict=True))
        finally:
            cursor.close()

    def _sql_sent(self, query: GuardedQuery) -> str:
        return query.sql

    def _session_guards(self) -> list[str]:
        guards = [
            "readers never wait for writers (multi-version reads)",
            f"call timeout {self._limits.statement_timeout_s} s",
            "ALTER SESSION DISABLE PARALLEL QUERY",
        ]
        schema = _schema(self._binding)
        if schema is not None:
            guards.append(f"CURRENT_SCHEMA {schema}")
        setting, source = time_zone_setting(self._binding)
        # Once connected, the zone the session got; before, what it will be set to.
        guards.append(f"TIME_ZONE {self._time_zone or setting} ({source})")
        return guards

    def _explain(self, query: GuardedQuery, parameter_types: Mapping[str, str]) -> list[PlanStep]:
        # EXPLAIN PLAN writes the plan into the session's own PLAN_TABLE (a global temporary
        # table) and does not run the query; the rollback removes it at once. The one write
        # ProbeOwl makes, decided 2026-11-03 ("EXPLAIN on Oracle" in docs/decisions.md).
        statement = f"probeowl-{uuid.uuid4().hex[:20]}"
        cursor = self._connection.cursor()
        try:
            _declare_types(cursor, query)
            cursor.execute(
                f"EXPLAIN PLAN SET STATEMENT_ID = '{statement}' FOR {query.sql}", _bound(query)
            )
            cursor.execute(_PLAN_ROWS, {"statement": statement})
            rows = cursor.fetchall()
        finally:
            self._connection.rollback()
            cursor.close()
        return [plan_table_step(*row) for row in rows]

    def _read_schema(self) -> tuple[list[TableSchema], str | None]:
        # The binding's schema, else the login's own.
        owner = _schema(self._binding) or self._binding.user.upper()
        bind = {"owner": owner}
        key = _name(owner)
        columns = [
            ColumnRow(
                (key, _name(table)),
                _name(name),
                _type(data_type, length, precision, scale),
                nullable == "Y",
            )
            for table, name, data_type, length, precision, scale, nullable in self._query(
                _COLUMNS, bind
            )
        ]
        primary: dict[TableKey, list[str]] = {}
        for table, column in self._query(_PRIMARY_KEYS, bind):
            primary.setdefault((key, _name(table)), []).append(_name(column))
        index_columns = [
            IndexColumnRow(
                (key, _name(table)), _name(index), uniqueness == "UNIQUE", False, _name(column)
            )
            for table, index, uniqueness, column in self._query(_INDEX_COLUMNS, bind)
        ]
        rows: dict[TableKey, int | None] = {
            (key, _name(table)): None if estimate is None else int(estimate)
            for table, estimate in self._query(_ROW_ESTIMATES, bind)
        }
        return assemble(columns, index_columns, rows, primary)

    def _read_lag(self) -> float:
        # Needs SELECT on SYS.V_$DATAGUARD_STATS for the probe login.
        cursor = self._connection.cursor()
        try:
            cursor.execute("SELECT value FROM v$dataguard_stats WHERE name = 'apply lag'")
            row = cursor.fetchone()
        finally:
            cursor.close()
        if row is None:
            raise LagUnknown("this database is not a Data Guard standby")
        return apply_lag_seconds(row[0])

    def _driver_error(self) -> type[Exception]:
        return oracledb.Error

    def _missing_grant(self, err: Exception) -> str | None:
        found = _SYS_VIEW_REFUSED.search(str(err))
        return f"SELECT on SYS.{found.group(1)}" if found else None

    def _classify(self, err: Exception) -> Failure:
        message = str(err)
        if any(code in message for code in _TIMED_OUT):
            return Failure.TIMED_OUT
        if "CERTIFICATE_VERIFY_FAILED" in message:
            return Failure.TLS_NOT_TRUSTED
        if _CANNOT_CONNECT in message and self._binding.encrypt:
            # Thin mode encrypts only over tcps; a listener on plain tcp fails the handshake.
            return Failure.TLS_NOT_OFFERED
        if any(code in message for code in _AUTHENTICATION):
            return Failure.AUTHENTICATION
        if any(code in message for code in _CONNECTION):
            return Failure.CONNECTION
        return Failure.REFUSED if "ORA-" in message else Failure.UNKNOWN


def plan_table_step(operation: str, options: str | None, name: str | None) -> PlanStep:
    """One row of PLAN_TABLE as a plan step."""
    described = f"{operation} {options}".strip() if options else operation
    target = _name(name) if name else None
    if operation == "TABLE ACCESS" and options == "FULL":
        return PlanStep(described, Access.SCAN, target)
    if operation == "INDEX" and options in _INDEX_ACCESS:
        return PlanStep(described, Access.INDEX, None, target)
    if operation == "INDEX":  # FULL SCAN, FAST FULL SCAN: every entry of the index
        return PlanStep(described, Access.SCAN, None, target)
    return PlanStep(described, Access.OTHER, target if operation == "TABLE ACCESS" else None)


def _name(name: str) -> str:
    # Unquoted names are stored in upper case; ProbeOwl shows them lower case, like the rows.
    return name.lower() if name == name.upper() else name


def _type(name: str, length: int | None, precision: int | None, scale: int | None) -> str:
    if name in _SIZED_BY_LENGTH and length:
        return f"{name.lower()}({length})"
    if name == "NUMBER" and precision is not None:
        return f"number({precision},{scale or 0})"
    return name.lower()


def apply_lag_seconds(value: str | None) -> float:
    """Data Guard's apply lag ('+00 00:01:05') in seconds; LagUnknown if it reports none."""
    match = _APPLY_LAG.match(value.strip()) if value else None
    if match is None:
        raise LagUnknown(f"Data Guard reports no apply lag ({value!r})")
    days, hours, minutes, secs = (int(part) for part in match.groups())
    return float(((days * 24 + hours) * 60 + minutes) * 60 + secs)


def _schema(binding: SqlBinding) -> str | None:
    if binding.db_schema is None:
        return None
    if not _SCHEMA.match(binding.db_schema):
        raise ConfigError(
            f"schema {binding.db_schema!r} is not a plain Oracle name "
            "(a letter, then letters, digits, _, $ or #)"
        )
    return binding.db_schema.upper()


def time_zone_setting(binding: SqlBinding) -> tuple[str, str]:
    """What ALTER SESSION SET TIME_ZONE is given, and where it comes from."""
    if binding.time_zone is None:
        return "DBTIMEZONE", "the database's own"
    return f"'{binding.time_zone}'", "from the binding"


def time_zone_shown(binding: SqlBinding) -> str:
    """The session time zone as the Connectors screen shows it."""
    if binding.time_zone is None:
        return "the database's own (DBTIMEZONE)"
    return f"{binding.time_zone} (from the binding)"


def _tls(binding: SqlBinding) -> dict[str, Any]:
    """python-oracledb's transport arguments. Thin mode encrypts only over tcps."""
    if not binding.encrypt:
        return {"protocol": "tcp"}
    if binding.trust_server_certificate:
        return {
            "protocol": "tcps",
            "ssl_context": trust_any_certificate(),
            "ssl_server_dn_match": False,
        }
    # Verified against the system's trusted authorities, and the host name must match.
    return {"protocol": "tcps", "ssl_server_dn_match": True}


def _exact_types(cursor: Any, metadata: Any) -> Any:
    # NUMBER as Decimal: a float would round money. CLOBs as text, so a row is plain data.
    if metadata.type_code is oracledb.DB_TYPE_NUMBER:
        return cursor.var(Decimal, arraysize=cursor.arraysize)
    if metadata.type_code is oracledb.DB_TYPE_CLOB:
        return cursor.var(oracledb.DB_TYPE_LONG, arraysize=cursor.arraysize)
    if metadata.type_code is oracledb.DB_TYPE_NCLOB:
        return cursor.var(oracledb.DB_TYPE_LONG_NVARCHAR, arraysize=cursor.arraysize)
    return None


def input_type(value: ParameterValue) -> Any:
    """The Oracle type a parameter is sent as, or None for the driver's own choice, text's."""
    if isinstance(value, str | bool | float) or value is None:
        return None
    if isinstance(value, int | Decimal):
        return oracledb.DB_TYPE_NUMBER
    if isinstance(value, datetime):
        aware = value.tzinfo is not None
        return oracledb.DB_TYPE_TIMESTAMP_TZ if aware else oracledb.DB_TYPE_TIMESTAMP
    if isinstance(value, date):
        return oracledb.DB_TYPE_DATE
    raise TypeError(f"no Oracle type for {type(value).__name__}")  # Oracle has no time of day


def bound_value(value: ParameterValue) -> ParameterValue:
    """The value as sent. Thin mode sends a time with an offset as its clock time at +00:00,
    so 14:00 +04:00 would arrive as 14:00 UTC: it is sent as its UTC instant instead."""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(UTC)
    return value


def _bound(query: GuardedQuery) -> Any:
    if isinstance(query.parameters, dict):
        return {name: bound_value(value) for name, value in query.parameters.items()}
    return [bound_value(value) for value in query.parameters]


def _declare_types(cursor: Any, query: GuardedQuery) -> None:
    if not isinstance(query.parameters, dict):
        return
    types = {name: input_type(value) for name, value in query.parameters.items()}
    declared = {name: kind for name, kind in types.items() if kind is not None}
    if declared:
        cursor.setinputsizes(**declared)
