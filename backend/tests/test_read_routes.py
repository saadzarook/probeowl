# Author: @SaadZarook
"""The read routes: applications, environments, entities and probes from the probe files."""

import platform
import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from probeowl.api.app import create_app
from probeowl.errors import unknown_application
from probeowl.model.check import LoadedApplication, load_applications
from probeowl.model.connector import RestConnector
from probeowl.runs import RunService
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade
from tests.local_client import local_client

NO_PAYROLL = "no application named payroll; there are: card_access, wallet"

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture
def applications() -> dict[str, LoadedApplication]:
    return load_applications(EXAMPLES)


@pytest.fixture
def client(tmp_path: Path, applications: dict[str, LoadedApplication]) -> Iterator[TestClient]:
    store = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    upgrade(store)
    yield local_client(create_app(RunService(applications, store, actor_name="s.khan")))
    store.dispose()


def test_applications_are_listed_by_name(client: TestClient) -> None:
    response = client.get("/api/applications")
    assert response.status_code == 200
    assert response.json() == [
        {
            "name": "card_access",
            "title": "Card access",
            "owner": "access-team",
            "environments": [
                {"name": "prod", "title": "Production", "production": True},
                {"name": "uat", "title": "UAT", "production": False},
            ],
        },
        {
            "name": "wallet",
            "title": "Wallet",
            "owner": "payments-team",
            "environments": [
                {"name": "prod", "title": "Production", "production": True},
                {"name": "uat", "title": "UAT", "production": False},
            ],
        },
    ]


def test_one_application_and_its_environments(client: TestClient) -> None:
    assert client.get("/api/applications/wallet").json()["title"] == "Wallet"
    assert client.get("/api/applications/wallet/environments").json() == [
        {"name": "prod", "title": "Production", "production": True},
        {"name": "uat", "title": "UAT", "production": False},
    ]


def test_entities_as_the_contract_defines_them(client: TestClient) -> None:
    [topup] = client.get("/api/applications/wallet/entities").json()
    assert topup["entity"] == "topup"
    assert topup["root_key"] == "topup_id"
    assert set(topup["locations"]) == {"record", "payment", "webhook", "ledger"}
    assert topup["locations"]["payment"]["mask"] == ["customer.email", "customer.phone"]
    assert client.get("/api/applications/wallet/entities/topup").json() == topup


def test_probes_for_an_entity(client: TestClient) -> None:
    assert client.get("/api/applications/wallet/probes", params={"entity": "topup"}).json() == [
        {
            "name": "topup_completed",
            "title": "Top-up completed",
            "kind": "validate",
            "entity": "topup",
            "owner": "payments-team",
            "description": (
                "A top-up was captured, confirmed by webhook and posted to the ledger once."
            ),
        }
    ]
    assert client.get("/api/applications/wallet/probes", params={"entity": "refund"}).json() == []


def test_probes_are_filtered_to_those_enabled_in_the_environment(
    client: TestClient, applications: dict[str, LoadedApplication]
) -> None:
    wallet = applications["wallet"]
    probe = wallet.probes["topup_completed"]
    wallet.probes["topup_completed"] = probe.model_copy(update={"environments": ["uat"]})
    wallet.probes["topup_all"] = probe.model_copy(update={"probe": "topup_all"})

    def names(environment: str) -> list[str]:
        response = client.get(
            "/api/applications/wallet/probes", params={"environment": environment}
        )
        return [p["name"] for p in response.json()]

    assert names("uat") == ["topup_all", "topup_completed"]
    assert names("prod") == ["topup_all"]


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        ("/api/applications/payroll", NO_PAYROLL),
        ("/api/applications/payroll/environments", NO_PAYROLL),
        ("/api/applications/payroll/entities", NO_PAYROLL),
        ("/api/applications/wallet/entities/refund", "wallet has no entity named refund"),
        ("/api/applications/payroll/probes", NO_PAYROLL),
        (
            "/api/applications/wallet/probes?environment=staging",
            "wallet has no environment staging",
        ),
    ],
)
def test_unknown_names_are_404_problems(client: TestClient, path: str, detail: str) -> None:
    response = client.get(path)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["detail"] == detail


def binding_values(applications: dict[str, LoadedApplication]) -> set[str]:
    """Where each connector points and how it logs in: hosts, users, URLs, secret references."""
    values: set[str] = set()
    for app in applications.values():
        for connector in app.connectors.values():
            for binding in connector.environments.values():
                if isinstance(connector, RestConnector):
                    url = binding.base_url  # type: ignore[union-attr]
                    values |= {str(url), str(url.host)}
                else:
                    sql = binding
                    values |= {sql.host, sql.user, f"{sql.host}:{sql.port}"}  # type: ignore[union-attr]
                if binding.secret:
                    values.add(binding.secret)
    return values


def test_no_response_carries_a_host_username_or_secret_reference(
    client: TestClient, applications: dict[str, LoadedApplication]
) -> None:
    secrets = binding_values(applications)
    assert {"127.0.0.1", "probeowl_ro", "keyring:probeowl/wallet_db.prod"} <= secrets
    paths = ["/api/applications", "/api/me"]
    for name, app in applications.items():
        paths += [
            f"/api/applications/{name}",
            f"/api/applications/{name}/environments",
            f"/api/applications/{name}/entities",
            f"/api/applications/{name}/probes",
        ]
        paths += [f"/api/applications/{name}/entities/{e}" for e in app.entities]
        paths += [
            f"/api/applications/{name}/probes?environment={e}" for e in app.application.environments
        ]
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, path
        leaked = {value for value in secrets if value in response.text}
        assert not leaked, f"{path} returned {sorted(leaked)}"


def test_me_is_only_what_runs_record(client: TestClient) -> None:
    """GET /api/me: the OS username runs are recorded under; no email, machine or host (#255)."""
    response = client.get("/api/me")
    assert response.status_code == 200
    assert response.json() == {"kind": "person", "name": "s.khan", "on_behalf_of": None}
    machine = {socket.gethostname(), socket.getfqdn(), platform.node()} - {""}
    leaked = {value for value in machine if value.lower() in response.text.lower()}
    assert not leaked, f"/api/me returned {sorted(leaked)}"
    assert "@" not in response.text


def test_with_no_applications_loaded_it_says_how_to_get_one() -> None:
    assert str(unknown_application("card_access", {})) == (
        "no application named card_access: owl serve loaded none; run owl init, "
        "or restart owl serve --config <folder>"
    )
