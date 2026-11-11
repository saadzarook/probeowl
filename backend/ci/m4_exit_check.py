# Author: @SaadZarook
"""The M4 exit check, in CI against the dev stack, with owl serve on 7878.

The failing top-up shows its differences from a healthy one, runbook steps fill in from the run,
and copying the write step appears in the audit with no run button anywhere.

Run from backend/: python ci/m4_exit_check.py <store url>
"""

import sys
import time
from typing import Any

import httpx

from probeowl.store.audit import list_events
from probeowl.store.engine import create_store_engine
from probeowl.store.tables import LOCAL_WORKSPACE

API = "http://127.0.0.1:7878/api"
FAILING = "tp_91f2c7"
HEALTHY = "tp_91f2b9"
RUNBOOK = "topup_not_credited"
WRITE = "requeue-posting"
READ = "check-outbox"


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def wait_for(fetch: Any, done: Any, what: str, within_s: float = 120) -> Any:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        found = fetch()
        if done(found):
            return found
        time.sleep(0.5)
    fail(f"{what} did not finish within {within_s} s")
    return None


def main() -> None:
    api = httpx.Client(headers={"X-ProbeOwl-Request": "1"}, timeout=120)

    print("== the failing top-up in UAT")
    body = {
        "application": "wallet",
        "environment": "uat",
        "probe": "topup_completed",
        "value": FAILING,
        "investigation": {"reason": "incident", "reference": "WALLET-482"},
    }
    run_id = api.post(f"{API}/runs", json=body).raise_for_status().json()["id"]
    run = wait_for(
        lambda: api.get(f"{API}/runs/{run_id}").json(),
        lambda r: r["status"] != "running",
        "the run",
    )
    if run["status"] != "failed":
        fail(f"expected a failed run, got {run['status']}")

    print("== its differences from a healthy one, found by the sample on a real database")
    url = f"{API}/runs/{run_id}/healthy-comparison"
    api.post(url, json={}).raise_for_status()
    found = wait_for(
        lambda: api.get(url).json(),
        lambda c: c["attempt"]["status"] != "running",
        "the comparison",
    )
    if found["attempt"]["status"] != "compared" or found["healthy_subject"] != HEALTHY:
        fail(f"expected a comparison with {HEALTHY}, got {found['attempt']}")
    rows = {(d["location"], d["field"]): d for d in found["differences"]}
    ledger = rows[("ledger", None)]
    print(f"ledger: {ledger['failing']} against {ledger['healthy']}")
    if ledger["failing"] != "Missing, 0 rows" or ledger["healthy"] != "Found, 2 rows":
        fail(f"the ledger entries should differ: {ledger}")
    if rows[("record", "amount")]["result"] != "expected_to_vary":
        fail(f"amount should be expected to vary: {rows[('record', 'amount')]}")

    print("== the failure suggests its runbook")
    suggested = [s["runbook"]["runbook"] for s in api.get(f"{API}/runs/{run_id}/runbooks").json()]
    if RUNBOOK not in suggested:
        fail(f"{RUNBOOK} was not suggested: {suggested}")

    print("== its steps fill in from the run")
    steps_url = f"{API}/runs/{run_id}/runbooks/{RUNBOOK}/steps"
    steps = {step["id"]: step for step in api.get(steps_url).json()}
    if f"'{FAILING}'" not in (steps[WRITE]["filled"] or ""):
        fail(f"the write step did not fill in: {steps[WRITE]}")

    print("== a reviewed read runs on the real database, its plan read first")
    ran = api.post(f"{steps_url}/{READ}/run", json={})
    print(ran.text[:300])
    if ran.status_code != 200 or ran.json()["result"]["outcome"] == "error":
        fail(f"the reviewed read should run: {ran.text}")

    print("== a write step cannot run")
    refused = api.post(f"{steps_url}/{WRITE}/run", json={})
    if refused.status_code != 422 or "never run" not in refused.text:
        fail(f"a write step should be refused: {refused.text}")

    print("== copying it appears in the audit")
    copied = api.post(f"{steps_url}/{WRITE}/copy").raise_for_status().json()["text"]
    print(copied)
    if not copied.startswith("-- WRITE · UAT"):
        fail("the copy has no header")
    with create_store_engine(sys.argv[1]).connect() as conn:
        audit = [
            event
            for event in list_events(conn, LOCAL_WORKSPACE)
            if event.action == "write_query_copied" and event.run_id == run_id
        ]
    if [event.details["step"] for event in audit] != [WRITE]:
        fail(f"the copy is not in the audit: {audit}")
    print("M4 exit check passed")


if __name__ == "__main__":
    main()
