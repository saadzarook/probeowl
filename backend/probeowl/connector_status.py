# Author: @SaadZarook
"""Each connector's state in one environment, for the Connectors screen and owl check.

SQL bindings are read live: a connection, the replica's lag, whether the login can read plans.
All of it is fixed statements that read no data, kept for a minute so an open screen does not
add load. REST bindings are not called, since a call spends the API's rate budget.

Reading a status never trips the circuit breaker: only runs do.
"""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from probeowl.connectors.factory import configured_guards, open_connector, open_sql, sql_adapter
from probeowl.connectors.lookup import ReplicaLag
from probeowl.connectors.oracle import time_zone_shown
from probeowl.connectors.rest import RestLimits, rest_guards
from probeowl.connectors.sql import SqlLimits, transport
from probeowl.engine.locations import DEFAULT_MAX_ROWS
from probeowl.errors import (
    KillSwitchEngaged,
    KillSwitchUnknown,
    NotFoundError,
    ProbeOwlError,
    unknown_application,
)
from probeowl.guards.breaker import CircuitBreaker
from probeowl.model.check import LoadedApplication
from probeowl.model.common import seconds
from probeowl.model.connector import RestConnector, SqlBinding, SqlConnector
from probeowl.model.connector_status import (
    BindingHealth,
    ConnectionTest,
    ConnectorStatus,
    PlanAccess,
)

CACHE_S = 60.0
# As for plan checks: Oracle's EXPLAIN PLAN takes longer than a keyed lookup.
STATUS_LIMITS = SqlLimits(statement_timeout_s=10)


class StatusSource(Protocol):
    """What a status needs from a SQL connector."""

    def unreachable(self) -> str | None:
        """Why the database cannot be reached, or None once connected."""
        ...

    def replica_lag(self) -> ReplicaLag:
        """How far the replica is behind."""
        ...

    def plan_access(self) -> tuple[PlanAccess, str | None]:
        """Whether this login can read plans, and what to grant if not."""
        ...

    def close(self) -> None:
        """Release the connection."""
        ...


class ConnectionSource(Protocol):
    """What Test connection needs from a connector."""

    def unreachable(self) -> str | None:
        """Why it cannot be reached, or None if it can."""
        ...

    def close(self) -> None:
        """Release the connection."""
        ...


StatusOpener = Callable[[SqlConnector, str], StatusSource | None]
ConnectionOpener = Callable[[SqlConnector | RestConnector, str], ConnectionSource | None]


def open_for_status(connector: SqlConnector, environment: str) -> StatusSource | None:
    """The connector's adapter in environment, with the status time limit."""
    return open_sql(connector, environment, limits=STATUS_LIMITS)


@dataclass(frozen=True)
class _Reading:
    """What a live read found: the parts of a status kept for a minute."""

    health: BindingHealth
    reason: str | None = None
    lag_seconds: float | None = None
    lag_note: str | None = None
    plan_access: PlanAccess = PlanAccess.NOT_CHECKED
    plan_message: str | None = None


class ConnectorStatuses:
    """Connector statuses for the applications owl serve loaded."""

    def __init__(
        self,
        applications: dict[str, LoadedApplication],
        breaker: CircuitBreaker,
        stopped: Callable[[str, str], str | None],
        opener: StatusOpener = open_for_status,
        clock: Callable[[], float] = time.monotonic,
        connection_opener: ConnectionOpener = open_connector,
    ):
        self._applications = applications
        self._breaker = breaker
        self._stopped = stopped
        self._opener = opener
        self._connection_opener = connection_opener
        self._clock = clock
        self._lock = threading.Lock()
        self._readings: dict[tuple[str, str, str], tuple[float, _Reading]] = {}

    @property
    def applications(self) -> dict[str, LoadedApplication]:
        """The applications whose connectors are reported, by name."""
        return self._applications

    def for_environment(self, application: str, environment: str) -> list[ConnectorStatus]:
        """Every connector of the application in environment."""
        app = self._application(application, environment)
        production = app.application.environments[environment].production
        return [
            self._status(application, connector, environment, production)
            for _, connector in sorted(app.connectors.items())
        ]

    def test_connection(self, application: str, connector: str, environment: str) -> ConnectionTest:
        """Connect once to a binding, now, and say how it went. A kill switch refuses it."""
        app = self._application(application, environment)
        definition = app.connectors.get(connector)
        if definition is None:
            raise NotFoundError(f"{application} has no connector named {connector}")
        if environment not in definition.environments:
            raise NotFoundError(f"{connector} has no binding in {environment}")
        # Before the secret is read: stopped always says stopped, and the keyring is left alone.
        stopped = self._stopped(application, connector)
        if stopped is not None:
            raise KillSwitchEngaged(stopped)
        key = (application, connector, environment)
        start = self._clock()
        reason = self._breaker.paused(key) or self._try_connecting(definition, environment)
        # What the screen shows next should agree with what this just found.
        with self._lock:
            self._readings.pop(key, None)
        return ConnectionTest(
            connector=connector,
            environment=environment,
            reachable=reason is None,
            reason=reason,
            duration_ms=round((self._clock() - start) * 1000),
        )

    def _try_connecting(
        self, connector: SqlConnector | RestConnector, environment: str
    ) -> str | None:
        try:
            source = self._connection_opener(connector, environment)
        except ProbeOwlError as err:  # its secret cannot be read
            return str(err)
        assert source is not None  # the caller found the binding
        try:
            return source.unreachable()
        finally:
            source.close()

    def _application(self, application: str, environment: str) -> LoadedApplication:
        app = self._applications.get(application)
        if app is None:
            raise unknown_application(application, self._applications)
        if environment not in app.application.environments:
            raise NotFoundError(f"{application} has no environment {environment}")
        return app

    def _status(
        self,
        application: str,
        connector: SqlConnector | RestConnector,
        environment: str,
        production: bool,
    ) -> ConnectorStatus:
        status = ConnectorStatus(
            connector=connector.connector,
            type=connector.type,
            owner=connector.owner,
            environment=environment,
            production=production,
            health=BindingHealth.NOT_CONFIGURED,
        )
        if environment not in connector.environments:
            reason = f"{connector.connector} has no binding in {environment}"
            return status.model_copy(update={"reason": reason})
        status = status.model_copy(update=_from_config(connector, environment))
        # Asked before anything reads: not even a kept reading is shown as current.
        try:
            stopped = self._stopped(application, connector.connector)
        except KillSwitchUnknown as err:
            return status.model_copy(
                update={"health": BindingHealth.NOT_CHECKED, "reason": str(err)}
            )
        if stopped is not None:
            return status.model_copy(update={"health": BindingHealth.STOPPED, "reason": stopped})
        if isinstance(connector, RestConnector):
            return status.model_copy(
                update={
                    "health": BindingHealth.NOT_CHECKED,
                    "reason": "not called for a status: a call spends the API's rate budget",
                }
            )
        binding = connector.environments[environment]
        # A paused connector is left alone, and a pause is always current.
        paused = self._breaker.paused((application, connector.connector, environment))
        if paused is not None:
            return status.model_copy(update={"health": BindingHealth.PAUSED, "reason": paused})
        reading = self._reading(application, connector, binding, environment)
        return status.model_copy(
            update={
                "health": reading.health,
                "reason": reading.reason,
                "lag_seconds": reading.lag_seconds,
                "lag_note": reading.lag_note,
                "plan_access": reading.plan_access,
                "plan_message": reading.plan_message,
            }
        )

    def _reading(
        self, application: str, connector: SqlConnector, binding: SqlBinding, environment: str
    ) -> _Reading:
        key = (application, connector.connector, environment)
        with self._lock:
            kept = self._readings.get(key)
        if kept is not None and self._clock() - kept[0] < CACHE_S:
            return kept[1]
        # Outside the lock: a slow database must not hold up the other connectors' statuses.
        reading = self._read(connector, binding, environment)
        with self._lock:
            self._readings[key] = (self._clock(), reading)
        return reading

    def _read(self, connector: SqlConnector, binding: SqlBinding, environment: str) -> _Reading:
        try:
            source = self._opener(connector, environment)
        except ProbeOwlError as err:  # its secret cannot be read
            return _Reading(
                BindingHealth.NOT_CHECKED, str(err), plan_message=f"plan access not checked: {err}"
            )
        assert source is not None  # the caller found the binding
        try:
            cannot = source.unreachable()
            if cannot is not None:
                return _Reading(
                    BindingHealth.UNREACHABLE,
                    cannot,
                    plan_message=f"plan access not checked: {cannot}",
                )
            lag = source.replica_lag() if binding.replica else None
            access, message = source.plan_access()
            found = _Reading(
                BindingHealth.HEALTHY,
                plan_access=access,
                plan_message=message,
            )
            return _with_lag(found, lag, seconds(binding.max_replica_lag)) if lag else found
        finally:
            source.close()


def unreadable_plans(
    statuses: ConnectorStatuses, application: str, app: LoadedApplication
) -> list[str]:
    """Each Production SQL binding whose login cannot read plans, or could not be asked."""
    found: list[str] = []
    for name, environment in app.application.environments.items():
        if not environment.production:
            continue
        for status in statuses.for_environment(application, name):
            if status.plan_access in {PlanAccess.DENIED, PlanAccess.NOT_CHECKED}:
                found.append(f"{application}: {status.connector} in {name}: {status.plan_message}")
    return found


def _from_config(connector: SqlConnector | RestConnector, environment: str) -> dict[str, object]:
    """What a status can say without reading anything, so a stopped connector says it too."""
    if isinstance(connector, RestConnector):
        limits = RestLimits(row_ceiling=connector.guards.row_ceiling)
        secure = connector.environments[environment].base_url.scheme == "https"
        return {
            "limits": [
                f"{limits.timeout_s:g} s",
                f"{limits.requests_per_minute} req/min",
                "GET only",
            ],
            "row_ceiling": limits.row_ceiling,
            "guards": rest_guards(limits),
            "transport": "TLS, server certificate verified" if secure else "not encrypted",
        }
    binding = connector.environments[environment]
    adapter = sql_adapter(connector.type)
    ceiling = connector.guards.row_ceiling
    return {
        "replica": binding.replica,
        "role": adapter.replica_role if binding.replica else None,
        "limits": [
            f"{SqlLimits().statement_timeout_s} s",
            f"{min(DEFAULT_MAX_ROWS, ceiling)} rows",
        ],
        "row_ceiling": ceiling,
        "guards": configured_guards(connector, environment),
        "forbidden": list(adapter.rules.forbidden_shown),
        "transport": transport(binding),
        "time_zone": time_zone_shown(binding) if connector.type == "oracle" else None,
        "max_replica_lag_seconds": seconds(binding.max_replica_lag) if binding.replica else None,
    }


def _with_lag(reading: _Reading, lag: ReplicaLag, max_seconds: float) -> _Reading:
    # JSON has no infinity: replication that is not running is said in the note.
    known = lag.seconds if lag.seconds is not None and math.isfinite(lag.seconds) else None
    reading = replace(reading, lag_seconds=known, lag_note=lag.note)
    if lag.seconds is None:
        return replace(reading, health=BindingHealth.NOT_CHECKED, reason=lag.cause or lag.note)
    if lag.seconds > max_seconds:
        reason = f"{lag.note} (max {max_seconds:g} s)"
        return replace(reading, health=BindingHealth.LAGGING, reason=reason)
    return reading
