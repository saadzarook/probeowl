# Author: @SaadZarook
"""First run and Add connection from the browser: a new application, a connector or binding, and
Test connection on a binding not saved yet (#36)."""

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

import probeowl.setup_writes
from probeowl.api.app import create_app
from probeowl.config_folder import ConfigFolder
from probeowl.connector_status import ConnectorStatuses
from probeowl.errors import ConfigError
from probeowl.guards.breaker import CircuitBreaker
from probeowl.model.check import load_applications
from probeowl.model.connector import RestConnector, SqlConnector
from probeowl.reload import ConfigReloader
from probeowl.runs import RunService
from probeowl.setup_writes import SetupWrites
from probeowl.store.audit import list_events
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.audit_fails import audit_fails
from tests.local_client import local_client

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
TEST_PASSWORD = "setup-test-password-7f3a"  # not a real password: a value to look for
NEW_APP = {
    "title": "Billing",
    "owner": "billing-team",
    "environments": [
        {"name": "dev", "title": "Dev"},
        {"name": "uat", "title": "UAT"},
        {"name": "prod", "title": "Production", "production": True},
    ],
}
SQL_BINDING = {
    "host": "db.internal.example",
    "port": 3306,
    "database": "billing_uat",
    "user": "probeowl_ro",
    "trust_server_certificate": True,
    "trust_server_certificate_reason": "internal CA, not in this machine's store",
}


class Source:
    """A connection that answers as it is told."""

    def __init__(self, reason: str | None) -> None:
        self.reason = reason

    def unreachable(self) -> str | None:
        return self.reason

    def close(self) -> None:
        pass


class Setup:
    """The examples behind the API, with the setup routes and a fake draft connection."""

    def __init__(self, tmp_path: Path, keychain: Callable[[], None] = lambda: None):
        self.root = tmp_path / "config"
        shutil.copytree(EXAMPLES, self.root)
        self.store = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
        upgrade(self.store)
        self.applications = load_applications(self.root)
        self.runs = RunService(self.applications, self.store, actor_name="s.khan")
        self.secrets_seen: list[str] = []
        self.reason: str | None = None

        def draft_opener(
            connector: SqlConnector | RestConnector, env: str, secrets: Callable[[str], str]
        ) -> Source:
            reference = connector.environments[env].secret
            if reference is not None:
                self.secrets_seen.append(secrets(reference))
            return Source(self.reason)

        statuses = ConnectorStatuses(
            self.applications, CircuitBreaker(), self.runs.kill.reason, draft_opener=draft_opener
        )
        folder = ConfigFolder(self.root)
        reloader = ConfigReloader(self.root, folder, self.runs)
        setup = SetupWrites(
            self.applications,
            self.store,
            folder,
            reloader,
            statuses,
            actor_name="s.khan",
            keychain=keychain,
        )
        self.api = local_client(
            create_app(self.runs, connectors=statuses, reloader=reloader, setup=setup)
        )

    def events(self, action: str) -> list[Any]:
        with self.store.connect() as conn:
            return [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == action]

    def create_application(self, name: str = "billing", **change: Any) -> Any:
        body = NEW_APP | change
        preview = self.api.put(f"/api/applications/{name}", json=body)
        if preview.status_code != 200:
            return preview
        return self.api.put(
            f"/api/applications/{name}", json=body | {"base": preview.json()["base"]}
        )

    def add_connector(
        self, connector: str = "billing_db", application: str = "billing", **change: Any
    ) -> Any:
        body = {"type": "mariadb", "environment": "uat", "binding": SQL_BINDING} | change
        url = f"/api/applications/{application}/connectors/{connector}"
        preview = self.api.put(url, json=body)
        if preview.status_code != 200:
            return preview
        return self.api.put(url, json=body | {"base": preview.json()["base"]})


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    return Setup(tmp_path)


# --- GET /api/setup ---------------------------------------------------------------------------


def test_setup_state_names_the_folder_store_and_keychain_but_no_url(setup: Setup) -> None:
    state = setup.api.get("/api/setup").json()
    assert state["applications"] == 2
    assert Path(state["config_folder"]) == setup.root.resolve()
    assert state["store"] == {"dialect": "sqlite", "created_now": False}
    assert state["keychain"] == {"available": True, "reason": None}
    assert "store.db" not in str(state)


def test_setup_state_says_why_there_is_no_keychain(tmp_path: Path) -> None:
    def none() -> None:
        raise ConfigError("no system keychain was found")

    state = Setup(tmp_path, keychain=none).api.get("/api/setup").json()
    assert state["keychain"] == {"available": False, "reason": "no system keychain was found"}


# --- A new application ------------------------------------------------------------------------


def test_a_new_application_is_previewed_first_and_nothing_is_written(setup: Setup) -> None:
    response = setup.api.put("/api/applications/billing", json=NEW_APP)
    assert response.status_code == 200, response.text
    change = response.json()
    assert change["path"] == "billing/application.yaml"
    assert change["written"] is False and change["base"] == "absent"
    assert "+application: billing" in change["diff"]
    assert not (setup.root / "billing").exists()
    assert setup.events("application_created") == []


def test_written_with_its_base_it_is_loaded_at_once_and_audited(setup: Setup) -> None:
    response = setup.create_application()
    assert response.status_code == 200, response.text
    assert response.json()["written"] is True
    written = yaml.safe_load((setup.root / "billing" / "application.yaml").read_text())
    assert written == {
        "application": "billing",
        "title": "Billing",
        "owner": "billing-team",
        "environments": {
            "dev": {"title": "Dev", "production": False},
            "uat": {"title": "UAT", "production": False},
            "prod": {"title": "Production", "production": True},
        },
    }
    names = [a["name"] for a in setup.api.get("/api/applications").json()]
    assert "billing" in names
    [event] = setup.events("application_created")
    assert event.application == "billing"
    assert event.actor.name == "s.khan"
    assert event.details["environments"] == ["dev", "uat", "prod"]


def test_an_application_already_loaded_is_a_409(setup: Setup) -> None:
    response = setup.api.put("/api/applications/card_access", json=NEW_APP)
    assert response.status_code == 409
    assert "already loaded" in response.json()["detail"]


def test_a_stale_base_is_a_409_and_writes_nothing(setup: Setup) -> None:
    response = setup.api.put("/api/applications/billing", json=NEW_APP | {"base": "0" * 64})
    assert response.status_code == 409
    assert not (setup.root / "billing").exists()


@pytest.mark.parametrize(
    ("name", "change"),
    [
        ("Billing", {}),
        ("billing", {"environments": [{"name": "uat", "title": "UAT"}] * 2}),
        ("billing", {"environments": []}),
        ("billing", {"title": ""}),
    ],
)
def test_a_bad_id_or_application_is_a_422(setup: Setup, name: str, change: dict[str, Any]) -> None:
    response = setup.api.put(f"/api/applications/{name}", json=NEW_APP | change)
    assert response.status_code == 422, response.text


def test_when_the_audit_fails_the_application_is_not_kept(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_fails(monkeypatch, probeowl.setup_writes, "application_created")
    response = setup.create_application()
    assert response.status_code == 500
    assert not (setup.root / "billing" / "application.yaml").exists()
    assert "billing" not in [a["name"] for a in setup.api.get("/api/applications").json()]


def test_while_a_run_is_running_nothing_is_written(setup: Setup) -> None:
    setup.runs._slots.acquire()  # a run holds a slot
    try:
        response = setup.create_application()
    finally:
        setup.runs._slots.release()
    assert response.status_code == 409
    assert "running" in response.json()["detail"]
    assert not (setup.root / "billing" / "application.yaml").exists()


# --- A connector, or a binding ----------------------------------------------------------------


def test_a_new_connector_names_its_keychain_entry_and_holds_no_password(setup: Setup) -> None:
    assert setup.create_application().status_code == 200
    response = setup.add_connector()
    assert response.status_code == 200, response.text
    assert response.json()["path"] == "billing/connectors/billing_db.yaml"
    written = yaml.safe_load(
        (setup.root / "billing" / "connectors" / "billing_db.yaml").read_text()
    )
    assert written["owner"] == "billing-team"
    assert written["guards"] == {"row_ceiling": 1000}
    assert written["environments"]["uat"]["secret"] == "keyring:probeowl/billing_db.uat"
    assert "replica" not in written["environments"]["uat"]
    statuses = setup.api.get("/api/applications/billing/connectors?environment=uat").json()
    assert [s["connector"] for s in statuses] == ["billing_db"]
    [event] = setup.events("connector_added")
    assert event.details["environments"] == ["uat"] and event.details["new"] is True
    assert "db.internal.example" not in str(event.details)
    assert "probeowl_ro" not in str(event.details)


def test_a_binding_for_an_environment_not_bound_keeps_the_file_as_it_was(setup: Setup) -> None:
    assert setup.create_application().status_code == 200
    assert setup.add_connector().status_code == 200
    path = setup.root / "billing" / "connectors" / "billing_db.yaml"
    path.write_text("# kept: a comment of its own\n" + path.read_text(), encoding="utf-8")
    setup.api.post("/api/config/reload")
    dev = SQL_BINDING | {"database": "billing_dev"}
    response = setup.add_connector(environment="dev", binding=dev)
    assert response.status_code == 200, response.text
    text = path.read_text()
    assert text.startswith("# kept: a comment of its own\n")
    assert "keyring:probeowl/billing_db.dev" in text
    assert "keyring:probeowl/billing_db.uat" in text


def test_a_binding_that_exists_is_a_409(setup: Setup) -> None:
    response = setup.add_connector("access_db", "card_access")
    assert response.status_code == 409
    assert "already bound in uat" in response.json()["detail"]


def test_a_type_that_differs_from_the_connector_is_a_422(setup: Setup) -> None:
    assert setup.create_application().status_code == 200
    assert setup.add_connector().status_code == 200
    response = setup.add_connector(type="oracle", environment="dev")
    assert response.status_code == 422
    assert "is a mariadb connector" in response.json()["detail"]


def test_production_needs_a_replica(setup: Setup) -> None:
    assert setup.create_application().status_code == 200
    response = setup.add_connector(environment="prod")
    assert response.status_code == 422
    assert "read replica" in response.json()["detail"]
    replica = setup.add_connector(environment="prod", binding=SQL_BINDING | {"replica": True})
    assert replica.status_code == 200, replica.text


@pytest.mark.parametrize(
    "binding",
    [
        SQL_BINDING | {"trust_server_certificate_reason": None},
        SQL_BINDING | {"encrypt": False, "trust_server_certificate": False,
                       "trust_server_certificate_reason": None},
    ],
)  # fmt: skip
def test_trusting_or_not_encrypting_needs_a_reason(setup: Setup, binding: dict[str, Any]) -> None:
    assert setup.create_application().status_code == 200
    response = setup.add_connector(binding=binding)
    assert response.status_code == 422
    assert "say why" in response.json()["detail"]


def test_a_binding_never_takes_a_secret_or_its_reference(setup: Setup) -> None:
    assert setup.create_application().status_code == 200
    for extra in ({"secret": "env:BILLING"}, {"password": TEST_PASSWORD}):
        response = setup.add_connector(binding=SQL_BINDING | extra)
        assert response.status_code == 422
        assert TEST_PASSWORD not in response.text


def test_a_rest_connector_names_a_token_only_when_it_needs_one(setup: Setup) -> None:
    assert setup.create_application().status_code == 200
    binding = {"base_url": "https://api.example.test", "needs_token": False}
    assert setup.add_connector("billing_api", type="rest", binding=binding).status_code == 200
    path = setup.root / "billing" / "connectors" / "billing_api.yaml"
    written = yaml.safe_load(path.read_text())
    assert written["environments"]["uat"] == {"base_url": "https://api.example.test"}
    assert "guards" not in written


def test_when_the_audit_fails_the_connector_is_not_kept(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert setup.create_application().status_code == 200
    audit_fails(monkeypatch, probeowl.setup_writes, "connector_added")
    assert setup.add_connector().status_code == 500
    assert not (setup.root / "billing" / "connectors" / "billing_db.yaml").exists()


# --- Test connection on a draft ---------------------------------------------------------------


def draft(**change: Any) -> dict[str, Any]:
    body = {
        "connector": "access_reports",
        "type": "mariadb",
        "environment": "uat",
        "binding": SQL_BINDING,
        "password": TEST_PASSWORD,
    }
    return body | change


def test_a_draft_is_tested_with_the_password_given_and_never_echoes_it(setup: Setup) -> None:
    response = setup.api.post("/api/applications/card_access/connectors/test-draft", json=draft())
    assert response.status_code == 200, response.text
    assert response.json()["reachable"] is True
    assert setup.secrets_seen == [TEST_PASSWORD]
    assert TEST_PASSWORD not in response.text
    [event] = setup.events("connection_tested")
    assert event.details == {
        "application": "card_access",
        "connector": "access_reports",
        "environment": "uat",
        "reachable": True,
        "draft": True,
    }
    assert TEST_PASSWORD not in str(event.details)


def test_a_draft_that_fails_says_the_fixed_reason(setup: Setup) -> None:
    setup.reason = "authentication failed; check the binding's login and secret"
    response = setup.api.post("/api/applications/card_access/connectors/test-draft", json=draft())
    assert response.json() | {"duration_ms": 0} == {
        "connector": "access_reports",
        "environment": "uat",
        "reachable": False,
        "reason": "authentication failed; check the binding's login and secret",
        "duration_ms": 0,
    }


def test_a_draft_uses_the_saved_password_only_when_asked(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(probeowl.setup_writes, "resolve_secret", lambda ref: f"saved for {ref}")
    url = "/api/applications/card_access/connectors/test-draft"
    response = setup.api.post(url, json=draft(password=None, use_saved_password=True))
    assert response.status_code == 200, response.text
    assert setup.secrets_seen == ["saved for keyring:probeowl/access_reports.uat"]
    neither = setup.api.post(url, json=draft(password=None))
    assert neither.status_code == 422
    assert "give the password" in neither.json()["detail"]


def test_a_kill_switch_refuses_a_draft_before_its_password_is_used(setup: Setup) -> None:
    engaged = setup.api.post(
        "/api/kill-switches",
        json={"scope": "application", "application": "card_access", "reason": "incident"},
    )
    assert engaged.status_code == 200, engaged.text
    response = setup.api.post("/api/applications/card_access/connectors/test-draft", json=draft())
    assert response.status_code == 403
    assert setup.secrets_seen == []


def test_a_production_draft_must_be_a_replica_too(setup: Setup) -> None:
    response = setup.api.post(
        "/api/applications/card_access/connectors/test-draft", json=draft(environment="prod")
    )
    assert response.status_code == 422
    assert setup.secrets_seen == []


def test_an_unknown_application_or_environment_is_a_404(setup: Setup) -> None:
    url = "/api/applications/nowhere/connectors/test-draft"
    assert setup.api.post(url, json=draft()).status_code == 404
    url = "/api/applications/card_access/connectors/test-draft"
    assert setup.api.post(url, json=draft(environment="staging")).status_code == 404
