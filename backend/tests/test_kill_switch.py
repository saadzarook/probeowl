# Author: @SaadZarook
"""The kill switch stops every read through what it covers, and is audited."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from typer.testing import CliRunner

from probeowl.api.app import create_app
from probeowl.cli import main as cli
from probeowl.cli import server
from probeowl.connector_status import ConnectorStatuses
from probeowl.connectors.lookup import LookupResult
from probeowl.errors import KillSwitchEngaged, KillSwitchUnknown
from probeowl.guards.breaker import CircuitBreaker
from probeowl.guards.kill_switch import KillSwitches
from probeowl.model.connector_status import BindingHealth, PlanAccess
from probeowl.model.kill_switch import KillScope, KillTarget
from probeowl.model.run import Actor, ActorKind
from probeowl.runs import RunLimits, RunService
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.local_client import local_client
from tests.test_catalog_import_api import Database, client
from tests.test_catalog_review import WALLET_TABLES
from tests.test_probe_save import Folder
from tests.test_read_routes import binding_values
from tests.test_run_breaker import finished, start
from tests.wallet_fakes import Fake

ACTOR = Actor(kind=ActorKind.PERSON, name="s.khan")
GLOBAL = KillTarget(scope=KillScope.GLOBAL)
WALLET = KillTarget(scope=KillScope.APPLICATION, application="wallet")
WALLET_DB = KillTarget(scope=KillScope.CONNECTOR, application="wallet", connector="wallet_db")
OTHER_APP = KillTarget(scope=KillScope.APPLICATION, application="card_access")
LEDGER_DB = KillTarget(scope=KillScope.CONNECTOR, application="wallet", connector="ledger_db")


def switches(setup: Setup) -> KillSwitches:
    return KillSwitches(setup.store)


def engage(setup: Setup, target: KillTarget, reason: str = "the replica is on fire") -> None:
    switches(setup).engage(target, reason, "INC-1", ACTOR)


def events(setup: Setup) -> list[Any]:
    with setup.store.connect() as conn:
        return [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action.startswith("kill_switch")]


def lost_store(setup: Setup) -> None:
    with setup.store.begin() as conn:
        conn.execute(text("DROP TABLE kill_switches"))


# --- What a switch covers ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (GLOBAL, "stopped by the global kill switch: the replica is on fire"),
        (WALLET, "stopped by the kill switch on application wallet: the replica is on fire"),
        (WALLET_DB, "stopped by the kill switch on connector wallet_db: the replica is on fire"),
    ],
    ids=["global", "application", "connector"],
)
def test_each_scope_stops_what_it_covers(setup: Setup, target: KillTarget, expected: str) -> None:
    engage(setup, target)
    assert switches(setup).reason("wallet", "wallet_db") == expected
    with pytest.raises(KillSwitchEngaged, match="stopped by"):
        switches(setup).check("wallet", "wallet_db")


def test_a_switch_stops_only_what_it_covers(setup: Setup) -> None:
    engage(setup, LEDGER_DB)
    engage(setup, OTHER_APP)
    assert switches(setup).reason("wallet", "wallet_db") is None
    assert switches(setup).reason("wallet", "ledger_db") is not None
    assert switches(setup).reason("card_access", "access_db") is not None


def test_the_broadest_switch_is_the_one_named(setup: Setup) -> None:
    engage(setup, WALLET_DB, "narrow")
    engage(setup, GLOBAL, "broad")
    assert switches(setup).reason("wallet", "wallet_db") == (
        "stopped by the global kill switch: broad"
    )


def test_releasing_leaves_a_broader_switch_on(setup: Setup) -> None:
    engage(setup, WALLET_DB)
    engage(setup, WALLET)
    switches(setup).release(WALLET_DB, "fixed", None, ACTOR)
    assert "application wallet" in (switches(setup).reason("wallet", "wallet_db") or "")


def test_a_switch_survives_a_restart(setup: Setup) -> None:
    engage(setup, WALLET_DB)
    assert KillSwitches(setup.store).reason("wallet", "wallet_db") is not None


@pytest.mark.parametrize(
    "fields",
    [
        {"scope": "global", "application": "wallet"},
        {"scope": "global", "connector": "wallet_db"},
        {"scope": "application"},
        {"scope": "application", "application": "wallet", "connector": "wallet_db"},
        {"scope": "connector", "application": "wallet"},
        {"scope": "connector", "connector": "wallet_db"},
    ],
)
def test_a_target_names_exactly_what_its_scope_needs(fields: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        KillTarget(**fields)


# --- Runs ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", [GLOBAL, WALLET, WALLET_DB], ids=lambda t: t.scope.value)
def test_a_new_run_is_skipped_with_the_reason_and_reads_nothing(
    setup: Setup, target: KillTarget
) -> None:
    engage(setup, target)
    api = setup.client()
    run = finished(api, start(api, value="tp_91f2b9"))
    assert run["status"] == "skipped"
    assert "kill switch" in run["skip_reason"] and "on fire" in run["skip_reason"]
    assert all(not fake.calls for fake in setup.fakes.values())


@pytest.mark.parametrize("environment", ["prod", "uat"])
def test_a_connector_switch_covers_every_environment(setup: Setup, environment: str) -> None:
    engage(setup, WALLET_DB)
    api = setup.client()
    run = finished(api, start(api, value="tp_91f2b9", environment=environment))
    assert run["status"] == "skipped"


def test_another_connectors_switch_leaves_a_run_alone(setup: Setup) -> None:
    engage(setup, OTHER_APP)
    api = setup.client()
    assert finished(api, start(api, value="tp_91f2b9"))["status"] != "skipped"


def test_runs_read_again_once_it_is_off(setup: Setup) -> None:
    engage(setup, WALLET_DB)
    switches(setup).release(WALLET_DB, "fixed", None, ACTOR)
    api = setup.client()
    assert finished(api, start(api, value="tp_91f2b9"))["status"] == "passed"


class EngagesDuringItsLookup(Fake):
    """A lookup in flight when someone turns the switch on: it finishes, nothing follows."""

    def __init__(self, inner: Fake, setup: Setup, then: Any):
        super().__init__(inner.answer)
        self.setup = setup
        self.then = then

    def lookup(
        self, lookup: str, parameters: Mapping[str, object], max_rows: int, /
    ) -> LookupResult:
        result = super().lookup(lookup, parameters, max_rows)
        if len(self.calls) == 1:
            self.then(self.setup)
        return result


def test_a_lookup_in_flight_finishes_then_the_run_stops_with_the_reason(setup: Setup) -> None:
    fake = EngagesDuringItsLookup(setup.fakes["wallet_db"], setup, lambda s: engage(s, WALLET_DB))
    setup.fakes["wallet_db"] = fake
    api = setup.client()
    run = finished(api, start(api, value="tp_91f2b9"))
    assert run["status"] == "failed"
    assert "kill switch on connector wallet_db" in run["stop_reason"]
    # The record's lookup finished and is kept; wallet_db's webhook lookup never went out.
    assert len(fake.calls) == 1
    assert run["locations"][0]["location"] == "record"
    assert not any(r["outcome"] == "error" for r in run["locations"])


def test_a_store_that_cannot_be_read_refuses_a_new_run(setup: Setup) -> None:
    lost_store(setup)
    api = setup.client()
    response = api.post("/api/runs", json=_start_body())
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert "kill switch cannot be checked" in response.json()["detail"]
    assert all(not fake.calls for fake in setup.fakes.values())


def test_losing_the_store_during_a_run_stops_it(setup: Setup) -> None:
    fake = EngagesDuringItsLookup(setup.fakes["wallet_db"], setup, lost_store)
    setup.fakes["wallet_db"] = fake
    api = setup.client()
    run = finished(api, start(api, value="tp_91f2b9"))
    assert run["status"] == "failed"
    assert "kill switch cannot be checked" in run["stop_reason"]
    assert len(fake.calls) == 1


def _start_body() -> dict[str, Any]:
    return {
        "application": "wallet",
        "environment": "prod",
        "probe": "topup_completed",
        "value": "tp_91f2b9",
        "investigation": {"reason": "incident", "reference": "WALLET-482"},
    }


# --- The other reads -----------------------------------------------------------------------------


def _draft() -> str:
    path = Path(__file__).parents[2] / "examples" / "wallet" / "probes" / "topup_completed.yaml"
    return path.read_text(encoding="utf-8")


def test_plan_checks_do_not_read_and_say_why(setup: Setup) -> None:
    engage(setup, WALLET_DB)
    api = setup.client()
    report = api.post(
        "/api/applications/wallet/probes/checks", json={"probe": _draft(), "environment": "prod"}
    ).json()
    stopped = [c for c in report["checks"] if c["plan_not_run"] and "wallet_db" in c["message"]]
    assert stopped and all("stopped by the kill switch" in c["message"] for c in stopped)
    assert ("wallet_db", "prod") not in setup.plans.opened
    assert ("ledger_db", "prod") in setup.plans.opened  # only what it covers


def test_a_save_in_production_then_needs_the_acknowledgement(tmp_path: Path) -> None:
    folder = Folder(tmp_path)
    KillSwitches(folder.store).engage(WALLET_DB, "on fire", None, ACTOR)
    edited = folder.text.replace("count(webhook) >= 1", "count(webhook) >= 2")
    preview = folder.save(edited).json()
    assert preview["report"]["plans_not_run"] > 0
    assert not [a for a in folder.plans.opened if a[0] == "wallet_db"]
    refused = folder.save(edited, preview["base"])
    assert refused.status_code == 422
    assert "stopped by the kill switch" in refused.json()["detail"]


def test_a_schema_import_is_refused_and_reads_nothing(setup: Setup) -> None:
    engage(setup, WALLET_DB)
    database = Database(WALLET_TABLES)
    response = client(setup, database).post(
        "/api/applications/wallet/catalog/imports",
        json={"connector": "wallet_db", "environment": "prod"},
    )
    assert response.status_code == 403
    assert "stopped by the kill switch" in response.json()["detail"]


def test_a_live_status_shows_stopped_and_reads_nothing(setup: Setup) -> None:
    engage(setup, WALLET)

    def never(connector: Any, environment: str) -> Any:
        raise AssertionError("nothing is read while the switch is on")

    found = ConnectorStatuses(
        setup.applications, CircuitBreaker(), switches(setup).reason, never
    ).for_environment("wallet", "prod")
    status = next(s for s in found if s.connector == "wallet_db")
    assert status.health is BindingHealth.STOPPED
    assert "on fire" in (status.reason or "")


def test_a_rest_connector_shows_stopped_too(setup: Setup) -> None:
    engage(setup, WALLET)
    found = ConnectorStatuses(
        setup.applications, CircuitBreaker(), switches(setup).reason
    ).for_environment("wallet", "prod")
    assert {s.health for s in found} == {BindingHealth.STOPPED}


def test_a_kept_reading_is_not_shown_while_it_is_on(setup: Setup) -> None:
    class Source:
        def unreachable(self) -> None:
            return None

        def replica_lag(self) -> Any:
            return None

        def plan_access(self) -> tuple[PlanAccess, None]:
            return PlanAccess.READABLE, None

        def guards(self) -> list[str]:
            return []

        def close(self) -> None:
            return None

    statuses = ConnectorStatuses(
        setup.applications, CircuitBreaker(), switches(setup).reason, lambda c, e: Source()
    )
    statuses.for_environment("wallet", "uat")  # keeps a reading for 60 s
    engage(setup, WALLET_DB)
    found = statuses.for_environment("wallet", "uat")
    assert next(s for s in found if s.connector == "wallet_db").health is BindingHealth.STOPPED


def test_a_status_with_no_store_reads_nothing_and_says_so(setup: Setup) -> None:
    lost_store(setup)

    def never(connector: Any, environment: str) -> Any:
        raise AssertionError("nothing is read if the switch cannot be checked")

    found = ConnectorStatuses(
        setup.applications, CircuitBreaker(), switches(setup).reason, never
    ).for_environment("wallet", "prod")
    status = next(s for s in found if s.connector == "wallet_db")
    assert status.health is BindingHealth.NOT_CHECKED
    assert "kill switch cannot be checked" in (status.reason or "")


def test_listing_with_no_store_is_a_clear_error(setup: Setup) -> None:
    lost_store(setup)
    with pytest.raises(KillSwitchUnknown):
        switches(setup).all()
    assert setup.client().get("/api/kill-switches").status_code == 503


# --- The routes and the audit --------------------------------------------------------------------

BODY = {"scope": "connector", "application": "wallet", "connector": "wallet_db"}


def test_turning_one_on_and_off_is_audited_with_the_actor_and_reason(setup: Setup) -> None:
    api = setup.client()
    on = api.post("/api/kill-switches", json=BODY | {"reason": "on fire", "reference": "INC-1"})
    assert on.status_code == 200
    assert on.json()["actor"]["name"] == "s.khan"
    assert [s["connector"] for s in api.get("/api/kill-switches").json()] == ["wallet_db"]
    off = api.post("/api/kill-switches/release", json=BODY | {"reason": "fixed"})
    assert off.status_code == 200
    assert api.get("/api/kill-switches").json() == []
    engaged, released = events(setup)
    assert (engaged.action, engaged.actor.name, engaged.application) == (
        "kill_switch_engaged",
        "s.khan",
        "wallet",
    )
    assert engaged.details == {
        "scope": "connector",
        "connector": "wallet_db",
        "reason": "on fire",
        "reference": "INC-1",
    }
    assert released.action == "kill_switch_released"
    assert released.details["reason"] == "fixed"


def test_turning_on_twice_keeps_the_first_and_audits_once(setup: Setup) -> None:
    api = setup.client()
    api.post("/api/kill-switches", json=BODY | {"reason": "first"})
    again = api.post("/api/kill-switches", json=BODY | {"reason": "second"})
    assert again.json()["reason"] == "first"
    assert len(events(setup)) == 1


def test_a_reason_is_required(setup: Setup) -> None:
    api = setup.client()
    assert api.post("/api/kill-switches", json=BODY).status_code == 422
    assert api.post("/api/kill-switches", json=BODY | {"reason": ""}).status_code == 422
    assert api.post("/api/kill-switches/release", json=BODY).status_code == 422
    assert events(setup) == []


@pytest.mark.parametrize(
    "body",
    [
        {"scope": "application", "application": "nope"},
        {"scope": "connector", "application": "wallet", "connector": "nope"},
        {"scope": "connector", "application": "nope", "connector": "wallet_db"},
    ],
)
def test_a_misspelt_name_is_refused_rather_than_stopping_nothing(
    setup: Setup, body: dict[str, str]
) -> None:
    response = setup.client().post("/api/kill-switches", json=body | {"reason": "x"})
    assert response.status_code == 404
    assert events(setup) == []


def test_releasing_a_switch_that_is_off_is_a_404(setup: Setup) -> None:
    response = setup.client().post("/api/kill-switches/release", json=BODY | {"reason": "x"})
    assert response.status_code == 404
    assert events(setup) == []


# --- owl kill ------------------------------------------------------------------------------------


@pytest.fixture
def owl(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> Any:
    api = setup.client()
    monkeypatch.setattr(server, "client", lambda url: api)

    def run(*args: str) -> Any:
        return CliRunner().invoke(cli.app, ["kill", *args])

    return run


def test_owl_kill_turns_a_connector_on_lists_it_and_turns_it_off(owl: Any, setup: Setup) -> None:
    on = owl("on", "--application", "wallet", "--connector", "wallet_db", "--reason", "on fire")
    assert on.exit_code == 0, on.output
    assert "On: connector/wallet/wallet_db: on fire (by s.khan" in on.output
    assert "connector/wallet/wallet_db" in owl("list").output
    assert switches(setup).reason("wallet", "wallet_db") is not None
    off = owl("off", "--application", "wallet", "--connector", "wallet_db", "--reason", "fixed")
    assert off.exit_code == 0, off.output
    assert "No kill switch is on." in owl("list").output


def test_owl_kill_all_must_be_asked_for_by_name(owl: Any, setup: Setup) -> None:
    assert owl("on", "--reason", "x").exit_code != 0
    assert switches(setup).all() == []
    assert owl("on", "--all", "--reason", "x").exit_code == 0
    assert switches(setup).reason("wallet", "wallet_db") is not None


@pytest.mark.parametrize(
    "args",
    [
        ("--connector", "wallet_db"),
        ("--all", "--application", "wallet"),
    ],
)
def test_owl_kill_refuses_a_target_that_makes_no_sense(owl: Any, args: tuple[str, ...]) -> None:
    assert owl("on", *args, "--reason", "x").exit_code != 0


def test_owl_validate_says_why_a_run_was_skipped(setup: Setup, owl: Any) -> None:
    engage(setup, WALLET_DB)
    result = CliRunner().invoke(
        cli.app,
        ["validate", "topup", "tp_91f2b9", "--env", "uat", "--reason", "incident"],
    )
    assert result.exit_code == 1
    assert ": skipped (stopped by the kill switch on connector wallet_db" in result.output


# --- The switch is asked before any secret is read ------------------------------------------------


def never_opened(connector: Any, environment: str) -> Any:
    raise AssertionError("no secret is read and nothing is opened while the switch is on")


def test_a_stopped_run_reads_no_secret_and_says_stopped(setup: Setup) -> None:
    engage(setup, WALLET_DB)
    service = RunService(
        setup.applications, setup.store, RunLimits(), never_opened, actor_name="s.khan"
    )
    api = local_client(create_app(service))
    run = finished(api, start(api, value="tp_91f2b9"))
    assert run["status"] == "skipped"
    assert "on fire" in run["skip_reason"]


def test_a_stopped_run_still_refuses_a_connector_with_no_binding(setup: Setup) -> None:
    engage(setup, WALLET)
    del setup.applications["wallet"].connectors["wallet_db"].environments["prod"]
    api = setup.client()
    response = api.post("/api/runs", json=_start_body())
    assert response.status_code == 422
    assert "has no binding here" in response.json()["detail"]


class Reachable:
    """A binding that connects, or fails as a test says."""

    def __init__(self, reason: str | None = None) -> None:
        self.reason = reason

    def unreachable(self) -> str | None:
        return self.reason

    def close(self) -> None:
        return None


def client_with_opener(setup: Setup, opener: Any) -> TestClient:
    connectors = ConnectorStatuses(
        setup.applications, CircuitBreaker(), switches(setup).reason, connection_opener=opener
    )
    return local_client(create_app(setup.service(), connectors=connectors))


TEST_URL = "/api/applications/wallet/connectors/wallet_db/test?environment=prod"


def test_test_connection_answers_and_is_audited(setup: Setup) -> None:
    response = client_with_opener(setup, lambda c, e: Reachable()).post(TEST_URL)
    assert response.status_code == 200
    assert response.json()["reachable"] is True
    with setup.store.connect() as conn:
        tested = [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "connection_tested"]
    assert [e.details for e in tested] == [
        {
            "application": "wallet",
            "connector": "wallet_db",
            "environment": "prod",
            "reachable": True,
        }
    ]
    assert tested[0].actor.name == "s.khan"


def test_a_switch_refuses_test_connection_before_its_secret_is_read(setup: Setup) -> None:
    engage(setup, WALLET_DB)
    response = client_with_opener(setup, never_opened).post(TEST_URL)
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert "stopped by the kill switch" in response.json()["detail"]
    with setup.store.connect() as conn:
        assert not [
            e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "connection_tested"
        ]


def test_a_store_that_cannot_be_read_refuses_test_connection(setup: Setup) -> None:
    api = client_with_opener(setup, never_opened)
    lost_store(setup)
    response = api.post(TEST_URL)
    assert response.status_code == 503
    assert "kill switch cannot be checked" in response.json()["detail"]


def test_test_connection_never_says_where_or_who(setup: Setup) -> None:
    api = client_with_opener(setup, lambda c, e: Reachable("no route to [host]"))
    secrets = binding_values(setup.applications)
    for name, app in setup.applications.items():
        for environment in app.application.environments:
            for connector, definition in app.connectors.items():
                if environment not in definition.environments:
                    continue
                url = f"/api/applications/{name}/connectors/{connector}/test"
                response = api.post(url, params={"environment": environment})
                assert response.status_code == 200
                assert not {value for value in secrets if value in response.text}


def test_test_connection_needs_the_request_header(setup: Setup) -> None:
    api = client_with_opener(setup, never_opened)
    del api.headers["X-ProbeOwl-Request"]
    assert api.post(TEST_URL).status_code == 403


def test_a_binding_that_is_not_there_cannot_be_tested(setup: Setup) -> None:
    del setup.applications["wallet"].connectors["wallet_db"].environments["prod"]
    response = client_with_opener(setup, never_opened).post(TEST_URL)
    assert response.status_code == 404
    assert "has no binding in prod" in response.json()["detail"]
