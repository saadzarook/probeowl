# Author: @SaadZarook
"""Reload configuration: owl serve loads its folder again, or keeps what it had and says why."""

import shutil
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

import probeowl.runs
from probeowl.api.app import CONFIGURATION_HEADER, create_app
from probeowl.cli import main as cli
from probeowl.cli import server
from probeowl.config_folder import ConfigFolder
from probeowl.connector_status import ConnectorStatuses
from probeowl.connectors.lookup import LookupResult
from probeowl.guards.breaker import CircuitBreaker
from probeowl.guards.kill_switch import KillSwitches
from probeowl.model.check import load_applications
from probeowl.model.connector import SqlBinding
from probeowl.model.kill_switch import KillScope, KillTarget
from probeowl.model.run import Actor, ActorKind
from probeowl.reload import ConfigReloader
from probeowl.runs import RunService
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.audit_fails import audit_fails
from tests.conftest import EXAMPLES, Setup
from tests.local_client import local_client
from tests.test_runs import finished, start
from tests.wallet_fakes import Fake

REPORTS_DB = """connector: reports_db
type: mariadb
owner: payments-team
environments:
  prod:
    host: 10.0.0.5
    database: reports
    user: probeowl_ro
    secret: env:PROBEOWL_REPORTS_DB_PROD
    replica: true
"""
REPORTS_DB_SWITCH = KillTarget(
    scope=KillScope.CONNECTOR, application="wallet", connector="reports_db"
)
WALLET_DB_SWITCH = KillTarget(
    scope=KillScope.CONNECTOR, application="wallet", connector="wallet_db"
)
ACTOR = Actor(kind=ActorKind.PERSON, name="s.khan")


@dataclass
class Served:
    """owl serve over a copy of the examples, as the reload sees it."""

    folder: Path
    runs: RunService
    api: TestClient

    @property
    def connectors(self) -> Path:
        return self.folder / "wallet" / "connectors"

    def reload(self) -> Any:
        return self.api.post("/api/config/reload")


@pytest.fixture
def served(setup: Setup, tmp_path: Path) -> Served:
    folder = tmp_path / "config"
    shutil.copytree(EXAMPLES, folder)
    setup.applications = load_applications(folder)
    runs = setup.service()
    statuses = ConnectorStatuses(runs.applications, CircuitBreaker(), runs.kill.reason)
    reloader = ConfigReloader(folder, ConfigFolder(folder), runs)
    app = create_app(runs, connectors=statuses, reloader=reloader, plans=setup.plans)
    return Served(folder, runs, local_client(app))


def reloads(setup: Setup) -> list[Any]:
    with setup.store.connect() as conn:
        return [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "config_reloaded"]


def listed(served: Served, environment: str = "prod") -> list[str]:
    response = served.api.get(f"/api/applications/wallet/connectors?environment={environment}")
    assert response.status_code == 200, response.text
    return [c["connector"] for c in response.json()]


def test_a_new_connector_is_loaded_and_shown_without_a_restart(
    served: Served, setup: Setup
) -> None:
    assert "reports_db" not in listed(served)
    (served.connectors / "reports_db.yaml").write_text(REPORTS_DB, encoding="utf-8")
    response = served.reload()
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["connectors_added"] == ["wallet/reports_db"]
    assert (report["applications"], report["connectors"], report["probes"]) == (2, 6, 2)
    assert report["connectors_removed"] == report["connectors_changed"] == []
    assert "restarted" in report["note"]
    # Another service sees it too: they share the one configuration.
    assert "reports_db" in listed(served)
    [event] = reloads(setup)
    assert event.details["connectors_added"] == ["wallet/reports_db"]
    assert "10.0.0.5" not in str(event.details)  # what changed, never where it points
    assert event.actor.name == "s.khan"


def test_every_response_says_how_many_times_the_configuration_was_reloaded(
    served: Served,
) -> None:
    # An open screen compares it with what it last saw, so owl reload in a terminal reaches it.
    assert served.api.get("/api/applications").headers[CONFIGURATION_HEADER] == "0"
    assert served.reload().headers[CONFIGURATION_HEADER] == "1"
    assert served.api.get("/api/applications").headers[CONFIGURATION_HEADER] == "1"
    # A refused reload loaded nothing new.
    (served.folder / "wallet" / "connectors" / "broken.yaml").write_text("connector: [", "utf-8")
    assert served.reload().status_code == 422
    assert served.api.get("/api/applications").headers[CONFIGURATION_HEADER] == "1"


def test_a_changed_binding_is_named(served: Served) -> None:
    path = served.connectors / "ledger_db.yaml"
    path.write_text(path.read_text("utf-8").replace("port: 1521", "port: 1522", 1), "utf-8")
    report = served.reload().json()
    assert report["connectors_changed"] == ["wallet/ledger_db"]
    binding = served.runs.applications["wallet"].connectors["ledger_db"].environments["prod"]
    assert isinstance(binding, SqlBinding)
    assert binding.port == 1522


def test_a_folder_owl_serve_would_refuse_keeps_the_configuration_it_had(
    served: Served, setup: Setup
) -> None:
    (served.connectors / "reports_db.yaml").write_text(REPORTS_DB, encoding="utf-8")
    path = served.connectors / "wallet_db.yaml"
    path.write_text(path.read_text("utf-8").replace("type: sqlserver", "type: db2"), "utf-8")
    response = served.reload()
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert "run: owl check" in response.json()["detail"]
    # Nothing changed: not even the file that was right.
    assert "reports_db" not in served.runs.applications["wallet"].connectors
    assert served.runs.applications["wallet"].connectors["wallet_db"].type == "sqlserver"
    assert reloads(setup) == []
    assert finished(served.api, start(served.api, value="tp_91f2b9"))["status"] == "passed"


class Held(Fake):
    """A lookup that waits until the test lets it go: a run in progress."""

    def __init__(self, inner: Fake):
        super().__init__(inner.answer)
        self.started = threading.Event()
        self.release = threading.Event()

    def lookup(
        self, lookup: str, parameters: Mapping[str, object], max_rows: int, /
    ) -> LookupResult:
        self.started.set()
        assert self.release.wait(10)
        return super().lookup(lookup, parameters, max_rows)


def test_it_is_refused_while_a_run_is_running(served: Served, setup: Setup) -> None:
    held = Held(setup.fakes["wallet_db"])
    setup.fakes["wallet_db"] = held
    run = start(served.api, value="tp_91f2b9")
    assert held.started.wait(10)
    response = served.reload()
    assert response.status_code == 409
    assert response.json()["detail"] == "1 run is running; reload when they have finished"
    assert reloads(setup) == []
    held.release.set()
    assert finished(served.api, run)["status"] == "passed"
    assert served.reload().status_code == 200


def test_a_run_started_while_reloading_is_refused_rather_than_run_on_the_old_one(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepare = served.runs._prepare

    def prepared_then_reloaded(*args: Any, **kwargs: Any) -> Any:
        found = prepare(*args, **kwargs)
        assert served.reload().status_code == 200
        return found

    monkeypatch.setattr(served.runs, "_prepare", prepared_then_reloaded)
    response = served.api.post("/api/runs", json=_run())
    assert response.status_code == 409
    assert "reloaded while this run was starting; start it again" in response.json()["detail"]
    monkeypatch.setattr(served.runs, "_prepare", prepare)
    assert finished(served.api, start(served.api, value="tp_91f2b9"))["status"] == "passed"


def _run() -> dict[str, Any]:
    return {
        "application": "wallet",
        "environment": "prod",
        "probe": "topup_completed",
        "value": "tp_91f2b9",
        "investigation": {"reason": "incident", "reference": "WALLET-482"},
    }


def test_kill_switches_survive_a_reload(served: Served, setup: Setup) -> None:
    switches = KillSwitches(setup.store)
    switches.engage(WALLET_DB_SWITCH, "failover", "INC-9", ACTOR)
    assert served.reload().status_code == 200
    run = finished(served.api, start(served.api, value="tp_91f2b9"))
    assert run["status"] == "skipped"
    assert "kill switch on connector wallet_db" in run["skip_reason"]


def test_a_switch_on_a_connector_removed_then_added_back_is_still_on(
    served: Served, setup: Setup
) -> None:
    path = served.connectors / "reports_db.yaml"
    path.write_text(REPORTS_DB, encoding="utf-8")
    assert served.reload().status_code == 200
    KillSwitches(setup.store).engage(REPORTS_DB_SWITCH, "decommissioning", None, ACTOR)
    path.unlink()
    assert served.reload().json()["connectors_removed"] == ["wallet/reports_db"]
    assert [s["connector"] for s in served.api.get("/api/kill-switches").json()] == ["reports_db"]
    path.write_text(REPORTS_DB, encoding="utf-8")
    assert served.reload().json()["connectors_added"] == ["wallet/reports_db"]
    assert "decommissioning" in (KillSwitches(setup.store).reason("wallet", "reports_db") or "")


def test_without_a_config_folder_it_is_not_available(setup: Setup) -> None:
    response = setup.client().post("/api/config/reload")
    assert response.status_code == 501
    assert "owl serve --config" in response.json()["detail"]


# --- owl reload ----------------------------------------------------------------------------------


def owl_reload(served: Served, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(server, "client", lambda url: served.api)
    return CliRunner().invoke(cli.app, ["reload"])


def test_owl_reload_says_what_changed(served: Served, monkeypatch: pytest.MonkeyPatch) -> None:
    (served.connectors / "reports_db.yaml").write_text(REPORTS_DB, encoding="utf-8")
    result = owl_reload(served, monkeypatch)
    assert result.exit_code == 0, result.output
    assert "Reloaded: 2 applications, 6 connectors, 2 probes." in result.output
    assert "Connectors added: wallet/reports_db" in result.output
    assert "change only when owl serve is restarted" in result.output


def test_owl_reload_with_nothing_changed_says_so(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = owl_reload(served, monkeypatch)
    assert result.exit_code == 0, result.output
    assert "No application or connector changed." in result.output


def test_owl_reload_refused_shows_why_and_fails(
    served: Served, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = served.connectors / "wallet_db.yaml"
    path.write_text(path.read_text("utf-8").replace("type: sqlserver", "type: db2"), "utf-8")
    result = owl_reload(served, monkeypatch)
    assert result.exit_code == server.NOT_RUN
    assert "run: owl check" in result.output


def test_owl_reload_takes_no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    result = CliRunner().invoke(cli.app, ["reload", "--config", "probeowl"])
    assert result.exit_code != 0
    assert "takes no --config" in result.output


def test_a_reload_whose_audit_cannot_be_written_loads_nothing_new(
    served: Served, setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    (served.connectors / "reports_db.yaml").write_text(REPORTS_DB, encoding="utf-8")
    with monkeypatch.context() as patch:
        audit_fails(patch, probeowl.runs, "config_reloaded")
        assert served.reload().status_code == 500
    assert "reports_db" not in listed(served)
    assert served.api.get("/api/applications").headers[CONFIGURATION_HEADER] == "0"
    assert reloads(setup) == []
    assert served.reload().status_code == 200  # and it loads once the store can be written
    assert "reports_db" in listed(served)
