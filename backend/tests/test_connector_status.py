# Author: @SaadZarook
"""What the Connectors screen and owl check report about each binding."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from probeowl.api.app import create_app
from probeowl.cli import main as cli
from probeowl.connector_status import CACHE_S, ConnectorStatuses, unreadable_plans
from probeowl.connectors.lookup import ReplicaLag
from probeowl.errors import ConfigError, KillSwitchEngaged, NotFoundError
from probeowl.guards.breaker import CircuitBreaker
from probeowl.model.check import LoadedApplication, load_applications
from probeowl.model.connector import SqlConnector
from probeowl.model.connector_status import BindingHealth, ConnectorStatus, PlanAccess
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade
from tests.local_client import local_client
from tests.test_read_routes import binding_values

EXAMPLES = Path(__file__).parents[2] / "examples"
READABLE = (PlanAccess.READABLE, None)


def NOT_STOPPED(application: str, connector: str) -> str | None:
    return None


GRANT = "this login cannot read plans, so the plan save checks cannot run: it needs GRANT SHOWPLAN"


class Source:
    """A SQL binding that answers as a test says."""

    def __init__(
        self,
        unreachable: str | None = None,
        lag: ReplicaLag | None = None,
        plans: tuple[PlanAccess, str | None] = READABLE,
    ):
        self._unreachable = unreachable
        self._lag = lag or ReplicaLag(3.0, "replica lag 3 s")
        self._plans = plans
        self.closed = 0

    def unreachable(self) -> str | None:
        return self._unreachable

    def replica_lag(self) -> ReplicaLag:
        return self._lag

    def plan_access(self) -> tuple[PlanAccess, str | None]:
        return self._plans

    def close(self) -> None:
        self.closed += 1


class Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def applications() -> dict[str, LoadedApplication]:
    return load_applications(EXAMPLES)


def statuses(
    applications: dict[str, LoadedApplication],
    source: Source,
    breaker: CircuitBreaker | None = None,
    clock: Callable[[], float] | None = None,
) -> ConnectorStatuses:
    """Statuses whose wallet_db is source; the other SQL connectors are simply healthy."""

    def opener(connector: SqlConnector, environment: str) -> Source:
        return source if connector.connector == "wallet_db" else Source()

    return ConnectorStatuses(
        applications, breaker or CircuitBreaker(), NOT_STOPPED, opener, clock=clock or Clock()
    )


def wallet_db(found: ConnectorStatuses, environment: str = "prod") -> ConnectorStatus:
    return next(
        s for s in found.for_environment("wallet", environment) if s.connector == "wallet_db"
    )


def test_a_healthy_replica(applications: dict[str, LoadedApplication]) -> None:
    source = Source()
    status = wallet_db(statuses(applications, source))
    assert status.health is BindingHealth.HEALTHY
    assert (status.production, status.replica) == (True, True)
    assert (status.lag_seconds, status.lag_note) == (3.0, "replica lag 3 s")
    assert status.max_replica_lag_seconds == 60
    assert status.plan_access is PlanAccess.READABLE
    assert "row ceiling 1000" in status.guards
    assert source.closed == 1  # nothing stays open between readings


def test_a_replica_behind_its_limit_is_lagging(
    applications: dict[str, LoadedApplication],
) -> None:
    source = Source(lag=ReplicaLag(90.0, "replica lag 90 s"))
    status = wallet_db(statuses(applications, source))
    assert status.health is BindingHealth.LAGGING
    assert status.reason == "replica lag 90 s (max 60 s)"


def test_replication_that_stopped_is_lagging_and_stays_valid_json(
    applications: dict[str, LoadedApplication],
) -> None:
    note = "replica lag unknown: replication is not running"
    status = wallet_db(statuses(applications, Source(lag=ReplicaLag(float("inf"), note))))
    assert status.health is BindingHealth.LAGGING
    assert status.lag_seconds is None and status.lag_note == note
    assert '"lag_seconds":null' in status.model_dump_json()


def test_a_lag_it_cannot_read_is_not_called_healthy(
    applications: dict[str, LoadedApplication],
) -> None:
    lag = ReplicaLag(None, "replica lag unknown: denied", "missing REPLICA MONITOR")
    status = wallet_db(statuses(applications, Source(lag=lag)))
    assert status.health is BindingHealth.NOT_CHECKED
    assert status.reason == "missing REPLICA MONITOR"
    assert status.plan_access is PlanAccess.READABLE  # the connection still answered


def test_an_unreachable_database_says_why_and_the_plan_check_was_not_made(
    applications: dict[str, LoadedApplication],
) -> None:
    status = wallet_db(statuses(applications, Source(unreachable="SQL Server: no route to [host]")))
    assert status.health is BindingHealth.UNREACHABLE
    assert status.reason == "SQL Server: no route to [host]"
    assert status.plan_access is PlanAccess.NOT_CHECKED
    assert status.plan_message == "plan access not checked: SQL Server: no route to [host]"


def test_an_unreadable_secret_is_not_checked(applications: dict[str, LoadedApplication]) -> None:
    def no_secret(connector: SqlConnector, environment: str) -> Source:
        raise ConfigError("the secret of connector wallet_db in prod cannot be read")

    found = ConnectorStatuses(applications, CircuitBreaker(), NOT_STOPPED, no_secret)
    status = wallet_db(found)
    assert status.health is BindingHealth.NOT_CHECKED
    assert status.reason == "the secret of connector wallet_db in prod cannot be read"
    assert status.plan_access is PlanAccess.NOT_CHECKED


def test_a_paused_connector_is_shown_and_not_read(
    applications: dict[str, LoadedApplication],
) -> None:
    breaker = CircuitBreaker()
    breaker.replica_lag(("wallet", "wallet_db", "prod"), 90.0, 60.0)
    source = Source()
    status = wallet_db(statuses(applications, source, breaker))
    assert status.health is BindingHealth.PAUSED
    assert status.reason is not None and "replica lag 90 s" in status.reason
    assert source.closed == 0


def test_reading_a_status_never_pauses_a_connector(
    applications: dict[str, LoadedApplication],
) -> None:
    breaker = CircuitBreaker()
    wallet_db(statuses(applications, Source(lag=ReplicaLag(90.0, "replica lag 90 s")), breaker))
    assert breaker.paused(("wallet", "wallet_db", "prod")) is None


def test_a_rest_connector_is_not_called(applications: dict[str, LoadedApplication]) -> None:
    source = Source()
    found = statuses(applications, source).for_environment("wallet", "prod")
    rest = next(s for s in found if s.type == "rest")
    assert rest.health is BindingHealth.NOT_CHECKED
    assert rest.plan_access is None
    assert "GET only" in rest.guards


def test_a_connector_without_a_binding_is_not_configured(
    applications: dict[str, LoadedApplication],
) -> None:
    app = applications["wallet"]
    connector = app.connectors["wallet_db"]
    connector.environments.pop("uat")
    assert wallet_db(statuses(applications, Source()), "uat").health is (
        BindingHealth.NOT_CONFIGURED
    )


def test_a_reading_is_kept_for_a_minute(applications: dict[str, LoadedApplication]) -> None:
    source, clock = Source(), Clock()
    found = statuses(applications, source, clock=clock)
    wallet_db(found)
    clock.now += CACHE_S - 1
    wallet_db(found)
    assert source.closed == 1
    clock.now += 1
    wallet_db(found)
    assert source.closed == 2


@pytest.mark.parametrize(
    ("application", "environment"),
    [("nope", "prod"), ("wallet", "nope")],
)
def test_an_unknown_application_or_environment_is_not_found(
    applications: dict[str, LoadedApplication], application: str, environment: str
) -> None:
    with pytest.raises(NotFoundError):
        statuses(applications, Source()).for_environment(application, environment)


# --- Who is told -------------------------------------------------------------------------------


def test_production_bindings_that_cannot_read_plans_name_the_grant(
    applications: dict[str, LoadedApplication],
) -> None:
    found = statuses(applications, Source(plans=(PlanAccess.DENIED, GRANT)))
    warnings = unreadable_plans(found, "wallet", applications["wallet"])
    assert f"wallet: wallet_db in prod: {GRANT}" in warnings
    assert all(" in prod: " in w for w in warnings)  # UAT's logins are not asked


def test_owl_check_without_a_store_says_so_and_creates_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "nothing" / "store.db"
    monkeypatch.setenv("PROBEOWL_STORE_URL", f"sqlite:///{path}")
    result = CliRunner().invoke(cli.app, ["check", str(EXAMPLES)])
    assert result.exit_code == 0
    assert "Plan access not checked: no store yet; run: owl store init" in result.output
    assert not path.exists()


def test_owl_check_warns_and_still_passes(
    applications: dict[str, LoadedApplication], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    upgrade(create_store_engine(url))
    monkeypatch.setenv("PROBEOWL_STORE_URL", url)
    denied = Source(plans=(PlanAccess.DENIED, GRANT))
    real = ConnectorStatuses.__init__

    def with_source(
        self: ConnectorStatuses,
        applications: dict[str, LoadedApplication],
        breaker: CircuitBreaker,
        stopped: Callable[[str, str], str | None],
    ) -> None:
        real(self, applications, breaker, stopped, opener=lambda c, e: denied)

    monkeypatch.setattr(ConnectorStatuses, "__init__", with_source)
    result = CliRunner().invoke(cli.app, ["check", str(EXAMPLES)])
    assert result.exit_code == 0
    assert f"warning: wallet: wallet_db in prod: {GRANT}" in result.output


def test_owl_check_no_connect_asks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: Any) -> None:
        raise AssertionError("no connector is opened")

    monkeypatch.setattr(ConnectorStatuses, "for_environment", refuse)
    result = CliRunner().invoke(cli.app, ["check", str(EXAMPLES), "--no-connect"])
    assert result.exit_code == 0
    # The only warning is the time offset check, which reads no connector either.
    warnings = [line for line in result.output.splitlines() if line.startswith("warning:")]
    assert warnings == [
        "warning: wallet: time-offset check not run: no schema import for wallet_db"
    ]


# --- The route ---------------------------------------------------------------------------------


def test_the_route_never_says_where_or_who(applications: dict[str, LoadedApplication]) -> None:
    source = Source(unreachable="SQL Server: no route to [host]")
    api = local_client(create_app(connectors=statuses(applications, source)))
    secrets = binding_values(applications)
    for name, app in applications.items():
        for environment in app.application.environments:
            response = api.get(f"/api/applications/{name}/connectors?environment={environment}")
            assert response.status_code == 200
            leaked = {value for value in secrets if value in response.text}
            assert not leaked, f"{name} in {environment} returned {sorted(leaked)}"


def test_the_route_needs_an_environment_it_knows(
    applications: dict[str, LoadedApplication],
) -> None:
    api = local_client(create_app(connectors=statuses(applications, Source())))
    assert api.get("/api/applications/wallet/connectors").status_code == 422
    missing = api.get("/api/applications/wallet/connectors?environment=nope")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/problem+json"


# --- Role, limits and what is forbidden ----------------------------------------------------------


def by_name(found: ConnectorStatuses, environment: str = "prod") -> dict[str, ConnectorStatus]:
    return {s.connector: s for s in found.for_environment("wallet", environment)}


def test_each_binding_says_its_role_and_limits(applications: dict[str, LoadedApplication]) -> None:
    found = by_name(statuses(applications, Source()))
    assert found["wallet_db"].role == "Readable secondary"
    assert found["ledger_db"].role == "Active Data Guard standby"
    assert found["checkout_api"].role is None
    assert found["wallet_db"].limits == ["2 s", "100 rows"]
    assert found["checkout_api"].limits == ["5 s", "100 req/min", "GET only"]


def test_an_oracle_binding_says_its_session_time_zone(
    applications: dict[str, LoadedApplication],
) -> None:
    found = by_name(statuses(applications, Source()))
    assert found["ledger_db"].time_zone == "the database's own (DBTIMEZONE)"
    assert "TIME_ZONE DBTIMEZONE (the database's own)" in found["ledger_db"].guards
    # SQL Server has no session time zone to set, and a REST API none at all.
    assert found["wallet_db"].time_zone is None
    assert found["checkout_api"].time_zone is None


def test_mariadb_says_read_replica(applications: dict[str, LoadedApplication]) -> None:
    found = ConnectorStatuses(applications, CircuitBreaker(), NOT_STOPPED, lambda c, e: Source())
    access = next(s for s in found.for_environment("card_access", "prod") if s.type == "mariadb")
    assert access.role == "Read replica"
    assert "GET_LOCK()" in access.forbidden


def test_forbidden_comes_from_the_dialect_and_a_rest_api_has_none(
    applications: dict[str, LoadedApplication],
) -> None:
    found = by_name(statuses(applications, Source()))
    assert "NOLOCK" in found["wallet_db"].forbidden
    assert "temp tables" in found["wallet_db"].forbidden
    assert "FOR UPDATE" in found["ledger_db"].forbidden
    assert found["checkout_api"].forbidden == []


def test_a_stopped_connector_still_says_what_its_config_says(
    applications: dict[str, LoadedApplication],
) -> None:
    def stopped(application: str, connector: str) -> str | None:
        return "stopped by the kill switch"

    def never(connector: Any, environment: str) -> Any:
        raise AssertionError("nothing is read while the switch is on")

    found = by_name(ConnectorStatuses(applications, CircuitBreaker(), stopped, never))
    assert found["wallet_db"].health is BindingHealth.STOPPED
    assert found["wallet_db"].role == "Readable secondary"
    assert found["wallet_db"].limits == ["2 s", "100 rows"]
    assert found["checkout_api"].health is BindingHealth.STOPPED
    assert found["checkout_api"].transport is not None


# --- Test connection ------------------------------------------------------------------------


def statuses_for_test(
    applications: dict[str, LoadedApplication],
    source: Source,
    stopped: Callable[[str, str], str | None] = NOT_STOPPED,
    breaker: CircuitBreaker | None = None,
    clock: Callable[[], float] | None = None,
) -> ConnectorStatuses:
    return ConnectorStatuses(
        applications,
        breaker or CircuitBreaker(),
        stopped,
        lambda c, e: source,
        clock=clock or Clock(),
        connection_opener=lambda c, e: source,
    )


def test_a_connection_that_works_says_so_and_closes(
    applications: dict[str, LoadedApplication],
) -> None:
    source = Source()
    result = statuses_for_test(applications, source).test_connection("wallet", "wallet_db", "prod")
    assert result.reachable is True
    assert result.reason is None
    assert source.closed == 1


def test_a_connection_that_fails_says_why_and_closes(
    applications: dict[str, LoadedApplication],
) -> None:
    source = Source(unreachable="the connection was refused")
    result = statuses_for_test(applications, source).test_connection("wallet", "wallet_db", "prod")
    assert result.reachable is False
    assert result.reason == "the connection was refused"
    assert source.closed == 1


def test_a_secret_that_cannot_be_read_is_a_failed_test(
    applications: dict[str, LoadedApplication],
) -> None:
    def no_secret(connector: Any, environment: str) -> Any:
        raise ConfigError("the secret cannot be read; the owl serve terminal says why")

    found = ConnectorStatuses(
        applications, CircuitBreaker(), NOT_STOPPED, connection_opener=no_secret
    ).test_connection("wallet", "wallet_db", "prod")
    assert found.reachable is False
    assert "the secret cannot be read" in (found.reason or "")


def test_a_kill_switch_refuses_it_before_the_secret_is_read(
    applications: dict[str, LoadedApplication],
) -> None:
    def stopped(application: str, connector: str) -> str | None:
        return "stopped by the kill switch on connector wallet_db"

    def never(connector: Any, environment: str) -> Any:
        raise AssertionError("the secret is not read while the switch is on")

    found = ConnectorStatuses(applications, CircuitBreaker(), stopped, connection_opener=never)
    with pytest.raises(KillSwitchEngaged, match="stopped by the kill switch"):
        found.test_connection("wallet", "wallet_db", "prod")


def test_a_paused_connector_is_left_alone(applications: dict[str, LoadedApplication]) -> None:
    def never(connector: Any, environment: str) -> Any:
        raise AssertionError("a paused connector is not connected to")

    breaker = CircuitBreaker()
    key = ("wallet", "wallet_db", "prod")
    breaker.replica_lag(key, 1000.0, 60.0)
    found = ConnectorStatuses(
        applications, breaker, NOT_STOPPED, connection_opener=never
    ).test_connection("wallet", "wallet_db", "prod")
    assert found.reachable is False
    assert "wallet_db is paused" in (found.reason or "")


@pytest.mark.parametrize(
    ("application", "connector", "environment", "match"),
    [
        ("nope", "wallet_db", "prod", "no application"),
        ("wallet", "wallet_db", "nope", "no environment"),
        ("wallet", "nope", "prod", "no connector"),
    ],
)
def test_testing_what_does_not_exist_is_not_found(
    applications: dict[str, LoadedApplication],
    application: str,
    connector: str,
    environment: str,
    match: str,
) -> None:
    found = statuses_for_test(applications, Source())
    with pytest.raises(NotFoundError, match=match):
        found.test_connection(application, connector, environment)


def test_a_test_replaces_the_kept_reading(applications: dict[str, LoadedApplication]) -> None:
    source = Source(unreachable="the connection was refused")
    found = statuses_for_test(applications, source)
    assert wallet_db(found).health is BindingHealth.UNREACHABLE
    source._unreachable = None
    assert wallet_db(found).health is BindingHealth.UNREACHABLE  # kept for a minute
    assert found.test_connection("wallet", "wallet_db", "prod").reachable is True
    assert wallet_db(found).health is BindingHealth.HEALTHY


# --- Guards from config, and the row ceiling -------------------------------------------------


@pytest.mark.parametrize("environment", ["prod", "uat"])
def test_a_stopped_connector_shows_its_guards_without_reading_anything(
    applications: dict[str, LoadedApplication], environment: str
) -> None:
    def stopped(application: str, connector: str) -> str | None:
        return "stopped by the kill switch"

    def never(connector: Any, environment: str) -> Any:
        raise AssertionError("no secret is read and nothing is opened while the switch is on")

    found = by_name(ConnectorStatuses(applications, CircuitBreaker(), stopped, never), environment)
    assert "SET LOCK_TIMEOUT 0" in found["wallet_db"].guards
    assert "row ceiling 1000" in found["wallet_db"].guards
    assert "GET only" in found["checkout_api"].guards


def test_an_unreachable_connector_shows_its_guards(
    applications: dict[str, LoadedApplication],
) -> None:
    found = by_name(statuses(applications, Source(unreachable="refused")))
    assert found["wallet_db"].health is BindingHealth.UNREACHABLE
    assert "statement timeout 2 s" in found["wallet_db"].guards


def test_the_guards_shown_are_a_lookups_not_the_longer_limit_a_status_reads_with(
    applications: dict[str, LoadedApplication],
) -> None:
    guards = by_name(statuses(applications, Source()))["wallet_db"].guards
    assert "statement timeout 2 s" in guards
    assert "statement timeout 10 s" not in guards


def test_a_connector_with_no_binding_has_no_guards(
    applications: dict[str, LoadedApplication],
) -> None:
    connector = applications["wallet"].connectors["wallet_db"]
    del connector.environments["prod"]
    status = wallet_db(statuses(applications, Source()))
    assert status.health is BindingHealth.NOT_CONFIGURED
    assert status.guards == []
    assert status.row_ceiling is None


def test_a_status_says_the_ceiling_and_the_default_cap_never_shows_above_it(
    applications: dict[str, LoadedApplication],
) -> None:
    found = by_name(statuses(applications, Source()))
    assert found["wallet_db"].row_ceiling == 1000
    assert found["checkout_api"].row_ceiling == 1000
    applications["wallet"].connectors["wallet_db"].guards.row_ceiling = 50
    low = wallet_db(statuses(applications, Source()))
    assert low.limits == ["2 s", "50 rows"]
    assert "row ceiling 50" in low.guards
