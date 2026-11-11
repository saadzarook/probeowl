# Author: @SaadZarook
"""`owl validate` end to end: the CLI, the real API and store, fake connectors."""

import re
import threading
from collections.abc import Mapping
from typing import Any

import pytest
from typer.testing import CliRunner

from probeowl.cli import main as cli
from probeowl.cli import server
from probeowl.connectors.lookup import ReplicaLag
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup

runner = CliRunner()
PROD = ["--env", "prod", "--reason", "incident", "--reference", "WALLET-482"]


@pytest.fixture
def api(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> Setup:
    """owl validate talks to the real API, in this process, instead of a server."""
    client = setup.client()
    monkeypatch.setattr(server, "client", lambda url: client)
    return setup


def owl(*args: str) -> Any:
    return runner.invoke(cli.app, ["validate", *args])


def run_id(output: str) -> str:
    match = re.search(
        r"Run ([0-9a-f-]{36}): Top-up completed \(wallet/topup_completed\) on", output
    )
    assert match, output
    return match.group(1)


def test_tp_91f2c7_fails_with_ledger_entries_missing(api: Setup) -> None:
    result = owl("topup", "tp_91f2c7", *PROD)
    assert result.exit_code == 1, result.output
    assert re.search(r"^  ledger\s+Missing$", result.output, re.M)
    assert "  fail count(ledger) == 2  (0 == 2)" in result.output
    assert f"Run {run_id(result.output)}: failed" in result.output


def test_tp_91f2b9_passes(api: Setup) -> None:
    result = owl("topup", "tp_91f2b9", *PROD)
    assert result.exit_code == 0, result.output
    for location in ("record", "payment", "webhook", "ledger"):
        assert re.search(rf"^  {location}\s+Found$", result.output, re.M)
    assert f"Run {run_id(result.output)}: passed" in result.output


def test_tp_93b001_shows_the_ledger_blocked_with_the_missing_link(api: Setup) -> None:
    result = owl("topup", "tp_93b001", *PROD)
    assert result.exit_code == 1, result.output
    assert re.search(
        r"^  ledger\s+Blocked  missing link: record\.ledger_txn_id is empty$", result.output, re.M
    )


def test_the_runs_audit_record_has_reason_and_reference(api: Setup) -> None:
    result = owl("topup", "tp_91f2c7", *PROD)
    with api.store.connect() as conn:
        started, finished = list_events(conn, LOCAL_WORKSPACE, run_id(result.output))
    assert (started.details["reason"], started.details["reference"]) == ("incident", "WALLET-482")
    assert finished.details["status"] == "failed"


def test_production_without_a_reference_is_refused_with_the_field(api: Setup) -> None:
    result = owl("topup", "tp_91f2c7", "--env", "prod", "--reason", "incident")
    assert result.exit_code == 2
    assert "error: a Production investigation needs a reason and a reference" in result.output
    assert "investigation.reference: required when the environment is Production" in result.output
    assert all(not fake.calls for fake in api.fakes.values())


def test_uat_needs_no_reference(api: Setup) -> None:
    assert owl("topup", "tp_91f2b9", "--env", "uat", "--reason", "proactive_check").exit_code == 0


def test_an_entity_without_a_validate_probe_in_the_environment(api: Setup) -> None:
    result = owl("refund", "rf_1", *PROD)
    assert result.exit_code == 2
    assert "no validate probe for refund is enabled in prod" in result.output


def test_several_probes_need_a_choice(api: Setup) -> None:
    wallet = api.applications["wallet"]
    wallet.probes["topup_refunded"] = wallet.probes["topup_completed"].model_copy(
        update={"probe": "topup_refunded"}
    )
    result = owl("topup", "tp_91f2b9", *PROD)
    assert result.exit_code == 2
    assert "topup has several validate probes in prod" in result.output
    assert owl("topup", "tp_91f2b9", *PROD, "--probe", "topup_completed").exit_code == 0


def test_a_run_still_running_at_the_timeout_is_reported(api: Setup) -> None:
    release = threading.Event()
    answer = api.fakes["ledger_db"].answer

    def held(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        release.wait(10)
        return answer(lookup, p)

    api.fakes["ledger_db"].answer = held
    try:
        result = owl("topup", "tp_91f2b9", *PROD, "--timeout", "0.5")
    finally:
        release.set()
    assert result.exit_code == 2
    assert "is still running after 0.5 s" in result.output


def test_owl_serve_not_running_is_a_clear_error() -> None:
    result = owl("topup", "tp_91f2c7", *PROD, "--server", "http://127.0.0.1:1")
    assert result.exit_code == 2
    assert "cannot reach owl serve at http://127.0.0.1:1" in result.output
    assert "owl serve --config" in result.output


def test_a_missing_result_that_may_be_late_says_so_on_every_line(
    api: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unknown() -> ReplicaLag:
        return ReplicaLag(None, "replica lag unknown: x", "missing REPLICA MONITOR")

    monkeypatch.setattr(api.fakes["ledger_db"], "replica_lag", unknown, raising=False)
    result = owl("topup", "tp_91f2c7", *PROD)
    assert result.exit_code == 1, result.output  # the exit code is unchanged
    assert re.search(
        r"^  ledger\s+Missing - replica lag unknown, the record may not have arrived yet$",
        result.output,
        re.M,
    )
    assert (
        "  fail count(ledger) == 2  (0 == 2)  - reads a Missing record that may not have arrived "
        "yet: replica lag unknown"
    ) in result.output
    assert (
        f"Run {run_id(result.output)}: failed - replica lag unknown: 1 Missing result may not "
        "have arrived yet"
    ) in result.output
