# Author: @SaadZarook
"""M4's "done when", through the API, with fakes in place of the dev stack's databases.

The failing top-up shows its differences from a healthy one, runbook steps fill in from the run,
and copying the write step appears in the audit with no run button anywhere. ci/m4_exit_check.sh
says the same against real databases.
"""

import time
from collections.abc import Mapping
from typing import Any

from fastapi.testclient import TestClient

from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.test_runs import finished, start

FAILING = "tp_91f2c7"
HEALTHY = "tp_91f2b9"
RUNBOOK = "topup_not_credited"
WRITE = "requeue-posting"


def sample_finds_the_healthy_top_up(setup: Setup) -> None:
    fake = setup.fakes["wallet_db"]
    real = fake.answer

    def answer(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        if "status = 'succeeded'" in lookup:
            return [{"topup_id": HEALTHY}]
        return real(lookup, p)

    fake.answer = answer


def compared(api: TestClient, run_id: str) -> dict[str, Any]:
    url = f"/api/runs/{run_id}/healthy-comparison"
    assert api.post(url, json={}).status_code == 202
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        found: dict[str, Any] = api.get(url).json()
        if found["attempt"]["status"] != "running":
            return found
        time.sleep(0.02)
    raise AssertionError("the comparison is still running")


def test_m4_is_done_when(setup: Setup) -> None:
    sample_finds_the_healthy_top_up(setup)
    api = setup.client()
    run_id = start(api, value=FAILING)
    assert finished(api, run_id)["status"] == "failed"

    # The failing top-up shows its differences from a healthy one.
    comparison = compared(api, run_id)
    assert comparison["attempt"]["status"] == "compared"
    assert comparison["healthy_subject"] == HEALTHY
    [ledger] = [
        d for d in comparison["differences"] if d["location"] == "ledger" and d["field"] is None
    ]
    assert (ledger["failing"], ledger["healthy"]) == ("Missing, 0 rows", "Found, 2 rows")
    assert ledger["result"] == "different"

    # The failure suggests the runbook for it.
    [suggestion] = api.get(f"/api/runs/{run_id}/runbooks").json()
    assert suggestion["runbook"]["runbook"] == RUNBOOK
    assert suggestion["matched"] == ["ledger.missing"]

    # Runbook steps fill in from the run.
    steps = {s["id"]: s for s in api.get(f"/api/runs/{run_id}/runbooks/{RUNBOOK}/steps").json()}
    assert f"aggregate_id = '{FAILING}'" in steps["check-outbox"]["filled"]
    assert f"aggregate_id = '{FAILING}'" in steps[WRITE]["filled"]

    # Copying the write step appears in the audit...
    copy = f"/api/runs/{run_id}/runbooks/{RUNBOOK}/steps/{WRITE}/copy"
    assert api.post(copy).json()["text"].startswith("-- WRITE · Production")
    with setup.store.connect() as conn:
        audit = [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "write_query_copied"]
    assert [(e.run_id, e.details["step"]) for e in audit] == [(run_id, WRITE)]

    # ...and nothing can run it.
    run_it = api.post(f"/api/runs/{run_id}/runbooks/{RUNBOOK}/steps/{WRITE}/run", json={})
    assert run_it.status_code == 422
    assert "never run by ProbeOwl" in run_it.json()["detail"]
    view = api.get(f"/api/applications/wallet/runbooks/{RUNBOOK}").json()
    assert [s["runnable"] for s in view["steps"] if s["kind"] == "write_query"] == [False]
