# Author: @SaadZarook
"""Passwords on this machine (#36): the write-only secret endpoint, whether a binding has its
password here, and a run that needs one this machine lacks."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

import probeowl.secret_writes
from probeowl import secrets
from probeowl.api.app import create_app
from probeowl.errors import KeychainUnavailable
from probeowl.model.check import LoadedApplication, load_applications
from probeowl.model.connector_status import BindingHealth, SecretState
from probeowl.secret_writes import SecretWrites
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.audit_fails import audit_fails
from tests.conftest import EXAMPLES, MemoryKeychain, Setup
from tests.local_client import local_client
from tests.test_connector_status import Source, statuses, wallet_db
from tests.test_runs import body, no_lookups, runs_stored

TEST_PASSWORD = "secret-test-password-91c4"  # not a real password: a value to look for
URL = "/api/applications/wallet/connectors/ledger_db/environments/prod/secret"
ENTRY = ("probeowl", "ledger_db.prod")


def client(setup: Setup) -> TestClient:
    app = create_app(setup.service(), secrets=SecretWrites(setup.applications, setup.store))
    return local_client(app)


def events(setup: Setup) -> list[Any]:
    with setup.store.connect() as conn:
        return [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "secret_saved"]


@pytest.fixture
def missing(machine_keychain: MemoryKeychain) -> MemoryKeychain:
    """This machine has no password for ledger_db in Production, as a teammate's would."""
    machine_keychain.absent.add(ENTRY)
    return machine_keychain


# --- The secret endpoint ----------------------------------------------------------------------


def test_a_new_password_is_saved_with_no_body_and_audited_without_it(
    setup: Setup, missing: MemoryKeychain
) -> None:
    response = client(setup).put(URL, json={"password": TEST_PASSWORD})
    assert response.status_code == 204
    assert response.content == b""
    assert missing.entries[ENTRY] == TEST_PASSWORD
    [event] = events(setup)
    assert event.application == "wallet" and event.environment == "prod"
    assert event.details == {"connector": "ledger_db", "replaced": False}
    assert TEST_PASSWORD not in str(event)


def test_one_already_saved_is_a_409_naming_the_binding_and_is_left_as_it_was(
    setup: Setup, machine_keychain: MemoryKeychain
) -> None:
    machine_keychain.entries[ENTRY] = "the-one-already-there"
    response = client(setup).put(URL, json={"password": TEST_PASSWORD})
    assert response.status_code == 409
    problem = response.json()
    assert problem["type"] == "urn:probeowl:problem:secret-exists"
    assert problem["connector"] == "ledger_db" and problem["environment"] == "prod"
    assert "probeowl/ledger_db.prod" in problem["detail"]
    assert TEST_PASSWORD not in response.text
    assert machine_keychain.entries[ENTRY] == "the-one-already-there"
    assert events(setup) == []


def test_replace_overwrites_it_and_the_audit_says_so(
    setup: Setup, machine_keychain: MemoryKeychain
) -> None:
    machine_keychain.entries[ENTRY] = "the-one-already-there"
    response = client(setup).put(URL, json={"password": TEST_PASSWORD, "replace": True})
    assert response.status_code == 204
    assert machine_keychain.entries[ENTRY] == TEST_PASSWORD
    assert events(setup)[0].details == {"connector": "ledger_db", "replaced": True}


@pytest.mark.parametrize(
    ("url", "status", "detail"),
    [
        (URL.replace("prod/secret", "uat/secret"), 422, "from [secret]; set it there"),
        (
            "/api/applications/card_access/connectors/door_controller_api/environments/uat/secret",
            422,
            "needs no password",
        ),
        (URL.replace("ledger_db", "nothing_db"), 404, "no connector named nothing_db"),
        (URL.replace("prod/secret", "staging/secret"), 404, "no binding in staging"),
    ],
)
def test_a_binding_not_in_the_keychain_is_refused(
    setup: Setup, machine_keychain: MemoryKeychain, url: str, status: int, detail: str
) -> None:
    response = client(setup).put(url, json={"password": TEST_PASSWORD})
    assert response.status_code == status
    assert detail in response.json()["detail"]
    assert TEST_PASSWORD not in response.text
    assert machine_keychain.entries == {}


def test_when_the_audit_fails_a_new_entry_is_removed(
    setup: Setup, missing: MemoryKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    audit_fails(monkeypatch, probeowl.secret_writes, "secret_saved")
    response = client(setup).put(URL, json={"password": TEST_PASSWORD})
    assert response.status_code == 500
    assert ENTRY not in missing.entries
    assert TEST_PASSWORD not in response.text


def test_when_the_audit_fails_a_replaced_entry_is_put_back(
    setup: Setup, machine_keychain: MemoryKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine_keychain.entries[ENTRY] = "the-one-already-there"
    audit_fails(monkeypatch, probeowl.secret_writes, "secret_saved")
    response = client(setup).put(URL, json={"password": TEST_PASSWORD, "replace": True})
    assert response.status_code == 500
    assert machine_keychain.entries[ENTRY] == "the-one-already-there"


def test_no_keychain_is_a_503_and_nothing_is_recorded(
    setup: Setup, missing: MemoryKeychain, monkeypatch: pytest.MonkeyPatch
) -> None:
    def none(service: str, username: str, value: str) -> None:
        raise KeychainUnavailable("the system keychain could not be written: locked")

    monkeypatch.setattr(secrets, "save_secret", none)
    response = client(setup).put(URL, json={"password": TEST_PASSWORD})
    assert response.status_code == 503
    assert "keychain" in response.json()["detail"]
    assert events(setup) == []


def test_no_route_reads_a_password_back(setup: Setup) -> None:
    api = client(setup)
    assert api.get(URL).status_code == 405
    route = (
        "/api/applications/{application}/connectors/{connector}/environments/{environment}/secret"
    )
    assert list(api.get("/openapi.json").json()["paths"][route]) == ["put"]


# --- Whether this machine has the password ----------------------------------------------------


@pytest.fixture
def applications() -> dict[str, LoadedApplication]:
    return load_applications(EXAMPLES)


def test_a_binding_whose_password_is_missing_asks_for_it_and_is_not_tried(
    applications: dict[str, LoadedApplication], machine_keychain: MemoryKeychain
) -> None:
    machine_keychain.absent.add(("probeowl", "wallet_db.prod"))
    source = Source()
    status = wallet_db(statuses(applications, source))
    assert status.secret is SecretState.MISSING
    assert status.health is BindingHealth.NOT_CONFIGURED
    assert status.reason == "no password saved on this machine for prod"
    assert source.closed == 0  # never opened


@pytest.mark.parametrize(
    ("application", "connector", "environment", "state"),
    [
        ("wallet", "wallet_db", "prod", SecretState.SAVED),
        ("wallet", "wallet_db", "uat", SecretState.FROM_ENVIRONMENT),
        ("card_access", "door_controller_api", "uat", SecretState.NOT_NEEDED),
    ],
)
def test_the_status_says_where_its_secret_comes_from(
    applications: dict[str, LoadedApplication],
    application: str,
    connector: str,
    environment: str,
    state: SecretState,
) -> None:
    found = statuses(applications, Source()).for_environment(application, environment)
    [status] = [s for s in found if s.connector == connector]
    assert status.secret is state


# --- A run that needs a password this machine lacks -------------------------------------------


def test_a_run_needing_a_missing_password_is_refused_before_it_exists(
    setup: Setup, missing: MemoryKeychain
) -> None:
    response = setup.client().post("/api/runs", json=body())
    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == "urn:probeowl:problem:password-missing"
    assert problem["connector"] == "ledger_db" and problem["environment"] == "prod"
    assert "needs your password for Production" in problem["detail"]
    assert no_lookups(setup) and runs_stored(setup) == 0


def test_a_run_on_a_binding_read_from_the_environment_is_not_refused(
    setup: Setup, missing: MemoryKeychain
) -> None:
    response = setup.client().post("/api/runs", json=body(environment="uat"))
    assert response.status_code == 202, response.text


def test_a_run_a_kill_switch_stops_is_skipped_as_before_not_refused(
    setup: Setup, missing: MemoryKeychain
) -> None:
    api = setup.client()
    engaged = api.post("/api/kill-switches", json={"scope": "global", "reason": "incident"})
    assert engaged.status_code == 200, engaged.text
    assert api.post("/api/runs", json=body()).status_code == 202
