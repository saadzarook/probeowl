# Author: @SaadZarook
"""What every SQL adapter shares: guards first, then the driver, then the result cap.

An adapter subclass knows its driver and its session settings; the guards, the caps and the
evidence it returns are the same for every database.
"""

import logging
import math
import re
import ssl
import time
from abc import ABC, abstractmethod
from collections.abc import Generator, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from probeowl.connectors.failures import Failure, driver_log_text, failure_message
from probeowl.connectors.lookup import LookupResult, ReplicaLag
from probeowl.connectors.plan import PlanStep
from probeowl.errors import ConnectorError, MissingGrant, ProbeOwlError
from probeowl.guards.dialects import DialectRules
from probeowl.guards.result import cap_result
from probeowl.guards.statement import GuardedQuery, ParameterValue, count_query, prepare
from probeowl.model.connector import DEFAULT_ROW_CEILING, SqlBinding
from probeowl.model.connector_status import PlanAccess
from probeowl.model.schema import TableSchema

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SqlLimits:
    """Runner limits for one connector. The design's defaults: 2 s and a bounded result."""

    statement_timeout_s: int = 2
    max_bytes: int = 1_000_000
    row_ceiling: int = DEFAULT_ROW_CEILING


class SqlAdapter(ABC):
    """A read-only connection to one SQL database, through the guards."""

    rules: ClassVar[DialectRules]
    replica_role: ClassVar[str]  # what this database calls a read-only copy
    # What the probe login needs to read its replica's lag, the errors that say it lacks it, and
    # how to read the grant a refusal names, where it names one.
    lag_privilege: ClassVar[str]
    lag_denied: ClassVar[tuple[str, ...]]
    lag_grant_named: ClassVar[re.Pattern[str] | None] = None
    # What the login needs to read plans, the errors that say it lacks it, and a statement to
    # try it on that reads no table.
    plan_privilege: ClassVar[str]
    plan_denied: ClassVar[tuple[str, ...]]
    plan_trial: ClassVar[str] = "SELECT 1 AS one"

    def __init__(self, binding: SqlBinding, password: str, limits: SqlLimits | None = None):
        self._binding = binding
        self._password = password
        self._limits = limits or SqlLimits()
        self._connection: Any = None

    def lookup(
        self,
        sql: str,
        parameters: Mapping[str, object],
        max_rows: int,
        parameter_types: Mapping[str, str] | None = None,
    ) -> LookupResult:
        """Run one guarded lookup; ConnectorError if the database fails or is too slow.

        parameter_types: a column type per parameter, from the table catalog when known.
        """
        # Whatever a location asks for, including one owl check would have refused.
        max_rows = min(max_rows, self._limits.row_ceiling)
        self._refuse_values(sql, parameters)
        # One row more than allowed shows there are more, without reading them.
        query = prepare(
            sql, parameters, self.rules, max_rows + 1, self._binding.databases_allowed()
        )
        started_at = datetime.now(UTC)
        start = time.monotonic()
        try:
            if self._connection is None:
                self._connection = self._connect()
            rows = self._run(query, parameter_types or {})
            try:
                capped = cap_result(rows, max_rows, self._limits.max_bytes)
            finally:
                rows.close()  # closes the cursor even when the cap stopped reading early
        except self._driver_error() as err:
            # After a timeout or a refused lock the session state is unknown: drop this
            # connection without using it again, and open a new one next time.
            self._connection = None
            raise self._error(err, _values(query)) from None
        guards = query.guards + self._session_guards() + [transport(self._binding)]
        total = None
        if len(capped.rows) == max_rows and capped.truncated is not None:
            total, counted = self._count(sql, parameters)
            guards.append(counted)
        return LookupResult(
            query=self._sql_sent(query),
            parameters=dict(zip(query.names, _values(query), strict=True)),
            guards=guards,
            rows=capped.rows,
            truncated=capped.truncated,
            started_at=started_at,
            duration_ms=round((time.monotonic() - start) * 1000),
            rows_total=total,
        )

    def _count(self, sql: str, parameters: Mapping[str, object]) -> tuple[int | None, str]:
        """How many rows sql matches, and how it was counted, for evidence.

        The rows are already read; a count that fails or times out leaves the total unknown
        and says why, rather than failing the lookup.
        """
        query = prepare(
            count_query(sql, self.rules),
            parameters,
            self.rules,
            1,
            self._binding.databases_allowed(),
        )
        try:
            if self._connection is None:
                self._connection = self._connect()
            rows = self._run(query, {})
            try:
                [row] = list(rows)
            finally:
                rows.close()
        except self._driver_error() as err:
            self._connection = None
            return None, f"rows not counted: {self._safe(err, _values(query))}"
        return int(next(iter(row.values()))), f"rows counted with: {self._sql_sent(query)}"

    def _refuse_values(self, sql: str, parameters: Mapping[str, object]) -> None:  # noqa: B027
        """ConnectorError for a value this database would change on the way; none by default."""

    def replica_lag(self) -> ReplicaLag:
        """How far this binding's replica is behind its primary. Never raises for the lag.

        The lag comes from the database's own replication status, read by a fixed query:
        not probe SQL, so it does not go through the statement guard.
        """
        try:
            if self._connection is None:
                self._connection = self._connect()
            lag = self._read_lag()
        except self._driver_error() as err:
            if any(marker in str(err) for marker in self.lag_denied):
                missing = f"missing {self.lag_grant(str(err))}"
                return ReplicaLag(None, f"replica lag unknown: {missing}", missing)
            return ReplicaLag(None, f"replica lag unknown: {self._safe(err)}", self._safe(err))
        except ProbeOwlError as err:
            return ReplicaLag(None, f"replica lag unknown: {err}", str(err))
        if math.isinf(lag):
            return ReplicaLag(lag, "replica lag unknown: replication is not running")
        return ReplicaLag(lag, f"replica lag {lag:g} s")

    def explain(
        self,
        sql: str,
        parameters: Mapping[str, object],
        parameter_types: Mapping[str, str] | None = None,
    ) -> list[PlanStep]:
        """The database's plan for a lookup, without running it; ConnectorError if refused.

        The lookup passes the statement guard first, exactly as a run would send it.
        """
        query = prepare(sql, parameters, self.rules, 1, self._binding.databases_allowed())
        try:
            if self._connection is None:
                self._connection = self._connect()
            return self._explain(query, parameter_types or {})
        except self._driver_error() as err:
            # A plan session setting (such as SHOWPLAN) may be left on: never reuse it, as a
            # lookup that fails never reuses its connection.
            self._connection = None
            raise self._error(err) from None

    def unreachable(self) -> str | None:
        """Why the database cannot be reached, or None once connected."""
        try:
            if self._connection is None:
                self._connection = self._connect()
        except self._driver_error() as err:
            return self._safe(err)
        except ProbeOwlError as err:
            return str(err)
        return None

    def lag_grant(self, refusal: str) -> str:
        """The grant the lag query needs: the one the database's refusal named, else this dialect's.

        Only a grant's name is taken from the refusal, never the rest of its text.
        """
        found = self.lag_grant_named.search(refusal) if self.lag_grant_named else None
        return found.group(1) if found else self.lag_privilege

    def plan_access(self) -> tuple[PlanAccess, str | None]:
        """Whether this login can read plans, and what to grant if not. Never raises for that."""
        denied = (
            PlanAccess.DENIED,
            "this login cannot read plans, so the plan save checks cannot run: "
            f"it needs {self.plan_privilege}",
        )
        try:
            if self._connection is None:
                self._connection = self._connect()
            if not self._can_read_plans():
                return denied
        except self._driver_error() as err:
            self._connection = None  # as in explain: a plan setting may be left on
            if any(marker in str(err) for marker in self.plan_denied):
                return denied
            return PlanAccess.NOT_CHECKED, f"plan access not checked: {self._safe(err)}"
        except ConnectorError as err:
            return PlanAccess.NOT_CHECKED, f"plan access not checked: {err}"
        return PlanAccess.READABLE, None

    def _can_read_plans(self) -> bool:
        """Try a plan of a statement that reads no table; a driver error says why it failed."""
        query = prepare(self.plan_trial, {}, self.rules, 1, self._binding.databases_allowed())
        self._explain(query, {})
        return True

    def guards(self) -> list[str]:
        """The guards a lookup on this binding runs under, for the Connectors screen."""
        return [
            *self._session_guards(),
            f"row ceiling {self._limits.row_ceiling}",
            transport(self._binding),
        ]

    def import_schema(self) -> tuple[list[TableSchema], str | None]:
        """The binding's tables, columns, keys, indexes and row estimates; never data.

        Read from the database's catalog views by fixed queries, outside the statement guard.
        Returns the tables and why some were left out, if any were.
        """
        try:
            if self._connection is None:
                self._connection = self._connect()
            return self._read_schema()
        except self._driver_error() as err:
            self._connection = None
            raise ConnectorError(self._safe(err)) from None

    def _query(self, sql: str, parameters: Any = ()) -> list[tuple[Any, ...]]:
        """All rows of one fixed catalog query."""
        cursor = self._connection.cursor()
        try:
            if parameters:
                cursor.execute(sql, parameters)
            else:
                cursor.execute(sql)
            return [tuple(row) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def _safe(self, err: Exception, parameters: Iterable[object] = ()) -> str:
        """What a response may say about a driver failure; the log gets a masked form of it."""
        failure = self._classify(err)
        text = driver_log_text(self._driver_text(err), str(err), self._password, parameters)
        log.warning("%s: %s", failure, text)
        return failure_message(failure, self._limits.statement_timeout_s)

    def _error(self, err: Exception, parameters: Iterable[object] = ()) -> ConnectorError:
        """The error a driver failure becomes: a grant the database named, or what _safe says."""
        message = self._safe(err, parameters)
        grant = self._missing_grant(err)
        return MissingGrant(grant) if grant is not None else ConnectorError(message)

    def _missing_grant(self, err: Exception) -> str | None:
        """The grant a refusal names, when the database names one; none by default."""
        return None

    def _driver_text(self, err: Exception) -> str:
        """The driver's error code and message, as plain text."""
        return str(err)

    def close(self) -> None:
        """Close the connection, if one is open."""
        if self._connection is not None:
            connection, self._connection = self._connection, None
            connection.close()

    @abstractmethod
    def _connect(self) -> Any:
        """Open a connection with this database's session guards applied."""

    @abstractmethod
    def _run(
        self, query: GuardedQuery, parameter_types: Mapping[str, str]
    ) -> Generator[dict[str, Any]]:
        """Execute the guarded query and yield rows as dicts, column name to value."""

    @abstractmethod
    def _sql_sent(self, query: GuardedQuery) -> str:
        """The SQL exactly as sent to the database, for evidence."""

    @abstractmethod
    def _session_guards(self) -> list[str]:
        """The guards this adapter applies on its connection, for evidence."""

    @abstractmethod
    def _explain(self, query: GuardedQuery, parameter_types: Mapping[str, str]) -> list[PlanStep]:
        """This dialect's plan for the guarded query, read without running it."""

    @abstractmethod
    def _read_schema(self) -> tuple[list[TableSchema], str | None]:
        """Read this dialect's catalog views for the binding's database or schema."""

    @abstractmethod
    def _read_lag(self) -> float:
        """Seconds this replica is behind; math.inf if replication stopped; LagUnknown if unsure."""

    @abstractmethod
    def _driver_error(self) -> type[Exception]:
        """The driver's base exception class."""

    @abstractmethod
    def _classify(self, err: Exception) -> Failure:
        """Which failure the driver's error is."""


class LagUnknown(ProbeOwlError):
    """The database does not report a replica lag, and why."""


def transport(binding: SqlBinding) -> str:
    """How the connection is protected, for evidence."""
    if not binding.encrypt:
        return "not encrypted"
    if binding.trust_server_certificate:
        return "TLS, any server certificate trusted"
    return "TLS, server certificate verified"


def trust_any_certificate() -> ssl.SSLContext:
    """A TLS context that encrypts but accepts any certificate: trust_server_certificate."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _values(query: GuardedQuery) -> list[ParameterValue]:
    if isinstance(query.parameters, dict):
        return [query.parameters[name] for name in query.names]
    return query.parameters
