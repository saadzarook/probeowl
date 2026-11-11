# Author: @SaadZarook
"""The API strips hosts, ports, logins, URLs and secret references, from stored runs too."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from probeowl.api.app import create_app
from probeowl.api.redaction import Redactor
from probeowl.errors import ConnectorError
from probeowl.model.check import LoadedApplication, load_applications
from probeowl.model.run import (
    Actor,
    ActorKind,
    Evidence,
    InvestigationRecord,
    LocationResult,
    Outcome,
    ProbeKind,
    Reason,
    Run,
    RunStatus,
)
from probeowl.runs import RunService
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade
from tests.local_client import local_client
from tests.test_read_routes import binding_values

EXAMPLES = Path(__file__).parents[2] / "examples"
NOW = datetime(2026, 11, 4, tzinfo=UTC)


@pytest.fixture
def applications() -> dict[str, LoadedApplication]:
    return load_applications(EXAMPLES)


@pytest.fixture
def redactor(applications: dict[str, LoadedApplication]) -> Redactor:
    return Redactor(applications)


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("SQL Server: cannot reach 127.0.0.1,1433", "SQL Server: cannot reach [host]"),
        ("Login failed for user 'probeowl_ro'.", "Login failed for user '[login]'."),
        ("no route to wallet-sql.corp.example:1433", "no route to [host]"),
        ("refused by 10.20.30.40:3306", "refused by [host]"),
        ("the API at https://api.corp.example answered", "the API at [host] answered"),
        ("GET https://api.corp.example:8443/v1/x failed", "GET /v1/x failed"),
        ("no secret keyring:probeowl/wallet_db.prod", "no secret [secret]"),
        ("env:PROBEOWL_WALLET_DB_UAT is not set", "[secret] is not set"),
        ("Server=db1,1433;UID=ro;PWD=x;Encrypt=yes", "[secret];[secret];[secret];Encrypt=yes"),
        ("(ADDRESS=(HOST=db1)(PORT=1521))", "(ADDRESS=[secret][secret])"),
        ("the statement timed out after 2 s", "the statement timed out after 2 s"),
    ],
)
def test_messages_lose_where_and_who(redactor: Redactor, text: str, shown: str) -> None:
    assert redactor.text(text) == shown


def test_a_login_is_only_replaced_as_a_whole_name() -> None:
    # Built from a binding whose login is short enough to appear inside other words.
    apps = load_applications(EXAMPLES)
    connector = apps["wallet"].connectors["wallet_db"]
    connector.environments["uat"].user = "sa"  # type: ignore[union-attr]
    assert Redactor(apps).text("unsafe login sa") == "unsafe login [login]"


def stored_run(reason: str, query: str, row: str) -> Run:
    """A run as the store holds it from before responses were fixed."""
    evidence = Evidence(
        connector="checkout_api",
        query=query,
        parameters={"id": "pay_7hq2m9"},
        guards=["TLS certificate verified", "connected to 127.0.0.1:8081"],
        rows=[{"status": "captured", "self": row, "role": "probeowl_ro"}],
        started_at=NOW,
        duration_ms=4,
    )
    return Run(
        id="run-1",
        application="wallet",
        environment="uat",
        probe="topup_completed",
        kind=ProbeKind.VALIDATE,
        probe_version="1",
        entity="topup",
        identifier="topup_id",
        identifier_value="tp_91f2c7",
        subject="tp_91f2c7",
        status=RunStatus.FAILED,
        actor=Actor(kind=ActorKind.PERSON, name="s.khan"),
        investigation=InvestigationRecord(reason=Reason.INCIDENT, reference="W-1"),
        started_at=NOW,
        finished_at=NOW,
        locations=[
            LocationResult(location="payment", outcome=Outcome.FOUND, evidence=evidence),
            LocationResult(location="ledger", outcome=Outcome.ERROR, reason=reason),
        ],
        warnings=[f"Replica lag unknown ({reason})"],
    )


def test_evidence_stays_exact_except_where_and_who(redactor: Redactor) -> None:
    run = stored_run(
        "Oracle: DPY-6005: cannot connect to 127.0.0.1:1521 as probeowl_ro",
        "GET http://127.0.0.1:8081/v1/payments/pay_7hq2m9",
        "http://127.0.0.1:8081/v1/payments/pay_7hq2m9",
    )
    found = redactor.run(run)
    [payment, ledger] = found.locations
    assert ledger.reason == "Oracle: DPY-6005: cannot connect to [host] as [login]"
    assert found.warnings == [f"Replica lag unknown ({ledger.reason})"]
    assert payment.evidence is not None
    assert payment.evidence.query == "GET /v1/payments/pay_7hq2m9"
    assert payment.evidence.guards == ["TLS certificate verified", "connected to [host]"]
    assert payment.evidence.parameters == {"id": "pay_7hq2m9"}
    # A row is the subject's data: its URL loses the host, and a value equal to a login stays.
    assert payment.evidence.rows == [
        {"status": "captured", "self": "/v1/payments/pay_7hq2m9", "role": "probeowl_ro"}
    ]
    # What was stored is untouched: redaction happens on read.
    assert run.locations[1].reason is not None and "127.0.0.1" in run.locations[1].reason


def _client(applications: dict[str, LoadedApplication], store: Engine) -> TestClient:
    return local_client(create_app(RunService(applications, store, actor_name="s.khan")))


@pytest.fixture
def store(tmp_path: Path) -> Engine:
    engine = create_store_engine(f"sqlite:///{tmp_path}/store.db")
    upgrade(engine)
    return engine


def test_a_stored_run_is_redacted_by_the_route(
    applications: dict[str, LoadedApplication],
    store: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = stored_run(
        "SQL Server: Login failed for user 'probeowl_ro' at 127.0.0.1,1433",
        "GET http://127.0.0.1:8081/v1/payments/x",
        "ok",
    )
    monkeypatch.setattr(RunService, "get", lambda self, run_id: old)
    response = _client(applications, store).get("/api/runs/run-1")
    assert response.status_code == 200
    for value in binding_values(applications) - {"probeowl_ro"}:
        assert value not in response.text
    assert "Login failed for user '[login]' at [host]" in response.text
    assert "GET /v1/payments/x" in response.text


def test_a_problem_is_redacted(
    applications: dict[str, LoadedApplication],
    store: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fails(self: RunService, run_id: str) -> Run:
        raise ConnectorError("cannot reach 127.0.0.1:1433 with keyring:probeowl/wallet_db.prod")

    monkeypatch.setattr(RunService, "get", fails)
    response = _client(applications, store).get("/api/runs/run-1")
    assert response.status_code == 502
    assert response.json()["detail"] == "cannot reach [host] with [secret]"
