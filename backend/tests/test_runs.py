# Author: @SaadZarook
"""Runs through the API: checks before a run, the background run, the store and the audit."""

import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from probeowl.api.app import create_app
from probeowl.model import Run
from probeowl.store.audit import list_events
from probeowl.store.runs import save_run
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.local_client import local_client

NO_PAYROLL = "no application named payroll; there are: card_access, wallet"

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
PROBLEM_JSON = "application/problem+json"


def body(value: str = "tp_91f2b9", **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "application": "wallet",
        "environment": "prod",
        "probe": "topup_completed",
        "value": value,
        "investigation": {
            "reason": "incident",
            "reference": "WALLET-482",
            "customer_reference": "CUST-778812",
        },
    }
    return data | changes


def finished(client: TestClient, run_id: str, within_s: float = 10) -> dict[str, Any]:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        run: dict[str, Any] = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} still running after {within_s} s")


def start(client: TestClient, **changes: Any) -> str:
    response = client.post("/api/runs", json=body(**changes))
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


def outcome(run: dict[str, Any], location: str) -> dict[str, Any]:
    [found] = [r for r in run["locations"] if r["location"] == location]
    return found


def no_lookups(setup: Setup) -> bool:
    return all(not fake.calls for fake in setup.fakes.values())


def runs_stored(setup: Setup) -> int:
    with setup.store.connect() as conn:
        return len(conn.exec_driver_sql("SELECT id FROM runs").fetchall())


# --- The planted cases, end to end ------------------------------------------------------------


def test_starting_a_run_answers_202_with_its_location(setup: Setup) -> None:
    response = setup.client().post("/api/runs", json=body())
    assert response.status_code == 202
    run_id = response.json()["id"]
    assert response.json() == {"id": run_id, "status": "running"}
    assert response.headers["Location"] == f"/api/runs/{run_id}"


@pytest.mark.parametrize(
    ("topup", "status", "ledger_outcome", "ledger_reason"),
    [
        ("tp_91f2c7", "failed", "missing", None),
        ("tp_91f2b9", "passed", "found", None),
        ("tp_93b001", "failed", "blocked", "missing link: record.ledger_txn_id is empty"),
    ],
)
def test_the_planted_cases_through_the_api(
    setup: Setup, topup: str, status: str, ledger_outcome: str, ledger_reason: str | None
) -> None:
    client = setup.client()
    run = finished(client, start(client, value=topup))
    assert run["status"] == status
    assert outcome(run, "ledger")["outcome"] == ledger_outcome
    assert outcome(run, "ledger")["reason"] == ledger_reason
    assert run["finished_at"] is not None
    # Masked on the way into the store, so masked on the way out.
    assert outcome(run, "record")["evidence"]["rows"][0]["customer_email"] == "[masked]"
    assert run["investigation"]["customer_reference"] == "[masked]"
    assert "c.customer@example.com" not in str(run)
    assert "CUST-778812" not in str(run)


def test_an_unknown_run_is_a_404_problem(setup: Setup) -> None:
    response = setup.client().get("/api/runs/no-such-run")
    assert response.status_code == 404
    assert response.headers["content-type"] == PROBLEM_JSON


# --- Checked before any lookup ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        ({"application": "payroll"}, NO_PAYROLL),
        ({"environment": "staging"}, "wallet has no environment staging"),
        ({"probe": "topup_refunded"}, "wallet has no probe named topup_refunded"),
    ],
)
def test_unknown_names_are_404_problems(setup: Setup, changes: dict[str, str], detail: str) -> None:
    response = setup.client().post("/api/runs", json=body(**changes))
    assert response.status_code == 404
    assert response.json()["detail"] == detail
    assert no_lookups(setup) and runs_stored(setup) == 0


def test_a_probe_not_enabled_in_the_environment_is_a_422_problem(setup: Setup) -> None:
    probe = setup.applications["wallet"].probes["topup_completed"]
    setup.applications["wallet"].probes["topup_completed"] = probe.model_copy(
        update={"environments": ["uat"]}
    )
    response = setup.client().post("/api/runs", json=body())
    assert response.status_code == 422
    assert response.headers["content-type"] == PROBLEM_JSON
    assert response.json()["detail"] == "probe topup_completed is not enabled in prod"
    assert response.json()["errors"] == [
        {"field": "environment", "message": "topup_completed runs in: uat"}
    ]
    assert no_lookups(setup) and runs_stored(setup) == 0


def test_production_needs_a_reference(setup: Setup) -> None:
    client = setup.client()
    without = body()
    without["investigation"] = {"reason": "incident"}
    response = client.post("/api/runs", json=without)
    assert response.status_code == 422
    assert response.json()["errors"] == [
        {
            "field": "investigation.reference",
            "message": "required when the environment is Production",
        }
    ]
    assert no_lookups(setup) and runs_stored(setup) == 0
    # Outside Production the reference is optional.
    assert client.post("/api/runs", json=without | {"environment": "uat"}).status_code == 202


def test_a_reason_is_always_required(setup: Setup) -> None:
    missing = body(environment="uat")
    missing["investigation"] = {"reference": "WALLET-482"}
    response = setup.client().post("/api/runs", json=missing)
    assert response.status_code == 422
    assert response.json()["errors"][0]["field"] == "investigation.reason"


def test_a_location_without_a_binding_is_a_422_problem(setup: Setup) -> None:
    setup.unbound.add("checkout_api")
    response = setup.client().post("/api/runs", json=body())
    assert response.status_code == 422
    assert "payment (checkout_api) has no binding here" in response.json()["detail"]
    assert no_lookups(setup) and runs_stored(setup) == 0
    # The connectors opened for the check are closed again.
    assert setup.fakes["wallet_db"].closed and setup.fakes["ledger_db"].closed


@pytest.mark.parametrize(
    ("changes", "field", "detail"),
    [
        ({"value": "not-a-topup"}, "identifier", "does not look like any identifier of topup"),
        ({"identifier": "card_id"}, "identifier", "topup has no identifier card_id"),
        (
            {"identifier": "provider_payment_id", "value": "tp_91f2c7"},
            "value",
            "does not look like a provider_payment_id",
        ),
    ],
)
def test_a_value_no_identifier_accepts_is_a_422_problem(
    setup: Setup, changes: dict[str, str], field: str, detail: str
) -> None:
    response = setup.client().post("/api/runs", json=body(**changes))
    assert response.status_code == 422
    assert detail in response.json()["detail"]
    assert response.json()["errors"][0]["field"] == field
    assert no_lookups(setup)


# --- In the background ------------------------------------------------------------------------


def hold(setup: Setup, connector: str) -> threading.Event:
    """Make every lookup on connector wait until the returned event is set."""
    release = threading.Event()
    answer = setup.fakes[connector].answer

    def held(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        release.wait(10)
        return answer(lookup, p)

    setup.fakes[connector].answer = held
    return release


def test_a_run_is_saved_as_each_location_completes(setup: Setup) -> None:
    release = hold(setup, "ledger_db")
    client = setup.client()
    run_id = start(client)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = client.get(f"/api/runs/{run_id}").json()
            if len(run["locations"]) == 3:
                break
            time.sleep(0.02)
        assert run["status"] == "running"
        assert {r["location"] for r in run["locations"]} == {"record", "payment", "webhook"}
        assert run["finished_at"] is None
    finally:
        release.set()
    assert finished(client, run_id)["status"] == "passed"


def test_the_run_time_limit_fails_the_run_and_its_unfinished_locations(setup: Setup) -> None:
    release = hold(setup, "ledger_db")
    client = setup.client(time_limit_s=0.5)
    started = time.monotonic()
    try:
        run = finished(client, start(client))
    finally:
        release.set()
    assert time.monotonic() - started < 5
    assert run["status"] == "failed"
    assert run["stop_reason"] == "run time limit reached"
    assert outcome(run, "ledger") == outcome(run, "ledger") | {
        "outcome": "error",
        "reason": "run time limit reached",
    }
    assert outcome(run, "record")["outcome"] == "found"


def test_more_runs_than_the_cap_are_refused_not_queued(setup: Setup) -> None:
    release = hold(setup, "wallet_db")
    client = setup.client(max_concurrent=2)
    try:
        first, second = start(client), start(client)
        response = client.post("/api/runs", json=body())
        assert response.status_code == 429
        assert response.headers["content-type"] == PROBLEM_JSON
        assert "2 runs are already running" in response.json()["detail"]
        assert runs_stored(setup) == 2  # nothing queued, nothing stored for the refused one
    finally:
        release.set()
    finished(client, first)
    finished(client, second)
    assert client.post("/api/runs", json=body()).status_code == 202


def test_a_fault_in_probeowl_fails_the_run_instead_of_leaving_it_running(setup: Setup) -> None:
    def broken(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        raise RuntimeError("bug")

    setup.fakes["ledger_db"].answer = broken
    client = setup.client()
    run = finished(client, start(client))
    assert run["status"] == "failed"
    assert run["stop_reason"] == "stopped by an unexpected error (RuntimeError)"


def test_connectors_are_closed_when_a_run_ends(setup: Setup) -> None:
    client = setup.client()
    finished(client, start(client))
    wallet = ("wallet_db", "checkout_api", "ledger_db")
    assert all(setup.fakes[name].closed for name in wallet)


# --- Audit and restarts -----------------------------------------------------------------------


def test_a_run_writes_started_and_finished_audit_events(setup: Setup) -> None:
    client = setup.client()
    run_id = start(client, value="tp_91f2c7")
    finished(client, run_id)
    with setup.store.connect() as conn:
        started, ended = list_events(conn, LOCAL_WORKSPACE, run_id)
    assert started.action == "run_started"
    assert started.actor.name == "s.khan"
    assert (started.application, started.environment) == ("wallet", "prod")
    assert started.details == {
        "probe": "topup_completed",
        "entity": "topup",
        "identifiers": {"topup_id": "tp_91f2c7"},
        "from_run": None,
        "healthy_for": None,
        "reason": "incident",
        "reference": "WALLET-482",
    }
    assert ended.action == "run_finished"
    assert ended.details == {
        "status": "failed",
        "stop_reason": None,
        "skip_reason": None,
        "warnings": [],
        "caveat": None,
        "subject": "tp_91f2c7",
        "matches": [],
        "locations": {"found": 3, "missing": 1},
        # The ledger balance is skipped, not passed as 0 == 0: the ledger is Missing.
        "expectations": {"pass": 4, "fail": 1, "skip": 3},
    }


def test_runs_left_running_are_failed_as_interrupted_on_startup(setup: Setup) -> None:
    left = Run.model_validate(
        {
            "id": "left-running",
            "application": "wallet",
            "environment": "prod",
            "probe": "topup_completed",
            "kind": "validate",
            "probe_version": "v1",
            "entity": "topup",
            "identifier": "topup_id",
            "identifier_value": "tp_91f2c7",
            "status": "running",
            "actor": {"kind": "person", "name": "s.khan"},
            "investigation": {"reason": "incident", "reference": "WALLET-482"},
            "started_at": datetime(2026, 11, 1, 9, 0, tzinfo=UTC),
        }
    )
    with setup.store.begin() as conn:
        save_run(conn, LOCAL_WORKSPACE, left)
    service = setup.service()
    assert service.recover_interrupted() == ["left-running"]
    run = service.get("left-running")
    assert (run.status.value, run.stop_reason) == ("failed", "interrupted")
    assert run.finished_at is not None
    with setup.store.connect() as conn:
        [event] = list_events(conn, LOCAL_WORKSPACE, "left-running")
    assert (event.action, event.details) == ("run_interrupted", {"stop_reason": "interrupted"})


def test_without_a_run_service_the_run_routes_answer_501() -> None:
    response = local_client(create_app()).post("/api/runs", json=body())
    assert response.status_code == 501
    assert "owl serve --config" in response.json()["detail"]
