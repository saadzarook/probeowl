# Author: @SaadZarook
"""The entity graph's lookup of its sample (#163): a draft test run, with every guard.

The body is what web/src/pages/NewEntityPage.tsx sends: the draft entity, and the Inspect probe
web/src/sampleRun.ts generates.
"""

from typing import Any

import pytest

from probeowl.guards.breaker import PAUSE_S, CircuitBreaker
from probeowl.model.kill_switch import KillTarget
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import EXAMPLES, Setup
from tests.test_kill_switch import GLOBAL, WALLET, WALLET_DB, engage
from tests.test_run_breaker import Clock, on_replica
from tests.test_runs import finished, hold, runs_stored

ENTITY = (EXAMPLES / "wallet" / "entities" / "topup.yaml").read_text(encoding="utf-8")
PROBE = """probe: topup_sample
title: Sample of Top-up
kind: inspect
entity: topup
owner: payments-team
expect:
  - count(record) >= 1
"""
EMAIL = "c.customer@example.com"
REFERENCED = {"reason": "incident", "reference": "WALLET-482"}


def lookup(setup: Setup, client: Any = None, **changes: Any) -> Any:
    body = {
        "probe": PROBE,
        "entity": ENTITY,
        "environment": "uat",
        "value": "tp_91f2c7",
        "investigation": {
            "reason": "proactive_check",
            "notes": "Entity graph: looking up the sample",
        },
    } | changes
    api = client or setup.client()
    return api.post("/api/applications/wallet/probes/test-runs", json=body)


def read_nothing(setup: Setup) -> bool:
    return all(not fake.calls for fake in setup.fakes.values())


def events(setup: Setup) -> list[Any]:
    with setup.store.connect() as conn:
        return list(list_events(conn, LOCAL_WORKSPACE))


def test_a_lookup_is_a_stored_draft_run_with_an_outcome_per_location(setup: Setup) -> None:
    run = finished(setup.client(), lookup(setup).json()["id"])
    assert run["probe_version"].startswith("draft:sha256:")
    assert {r["location"]: r["outcome"] for r in run["locations"]} == {
        "record": "found",
        "payment": "found",
        "webhook": "found",
        "ledger": "missing",
    }


def test_what_a_lookup_returns_and_stores_is_masked(setup: Setup) -> None:
    api = setup.client()
    run_id = lookup(setup, api).json()["id"]
    run = finished(api, run_id)
    [record] = [r for r in run["locations"] if r["location"] == "record"]
    assert record["evidence"]["rows"][0]["customer_email"] == "[masked]"
    # The fake's row holds the address: nowhere in the answer, the stored run or the audit.
    assert EMAIL not in api.get(f"/api/runs/{run_id}").text
    assert all(EMAIL not in event.model_dump_json() for event in events(setup))


def test_a_lookup_is_audited_with_its_actor_and_the_draft_it_ran(setup: Setup) -> None:
    run = finished(setup.client(), lookup(setup).json()["id"])
    audited = [e for e in events(setup) if e.run_id == run["id"]]
    assert sorted(e.action for e in audited) == ["run_finished", "run_started"]
    assert {(e.actor.name, e.application, e.environment) for e in audited} == {
        ("s.khan", "wallet", "uat")
    }
    assert run["investigation"]["notes"] == "Entity graph: looking up the sample"


@pytest.mark.parametrize("target", [GLOBAL, WALLET, WALLET_DB], ids=lambda t: t.scope.value)
def test_a_kill_switch_stops_a_lookup_before_it_reads(setup: Setup, target: KillTarget) -> None:
    engage(setup, target)
    run = finished(setup.client(), lookup(setup).json()["id"])
    assert run["status"] == "skipped" and "kill switch" in run["skip_reason"]
    assert read_nothing(setup)


def test_the_circuit_breaker_stops_a_lookup_and_the_next_ones_until_its_pause_ends(
    setup: Setup,
) -> None:
    clock = Clock()
    setup.breaker = CircuitBreaker(clock)
    production = {"environment": "prod", "investigation": REFERENCED}
    on_replica(setup, 120)
    api = setup.client()
    run = finished(api, lookup(setup, api, **production).json()["id"])
    assert run["status"] == "skipped"
    assert run["skip_reason"] == "ledger_db is paused: replica lag 120 s (max 60 s)"
    assert run["locations"] == [] and read_nothing(setup)
    assert sorted(e.action for e in events(setup) if e.run_id == run["id"]) == [
        "run_finished",
        "run_started",
    ]
    # The graph asks again as the draft changes: still paused, and the replica isn't measured again.
    caught_up = on_replica(setup, 0)
    again = finished(api, lookup(setup, api, **production).json()["id"])
    assert again["status"] == "skipped" and "paused for another" in again["skip_reason"]
    assert caught_up.measured == 0 and read_nothing(setup)
    clock.now += PAUSE_S
    resumed = finished(api, lookup(setup, api, **production).json()["id"])
    assert resumed["status"] != "skipped" and resumed["locations"]


def test_a_lookup_in_production_without_a_reference_is_refused(setup: Setup) -> None:
    response = lookup(setup, environment="prod", investigation={"reason": "incident"})
    assert response.status_code == 422
    assert "reference" in response.json()["detail"]
    assert read_nothing(setup)


def test_a_lookup_over_the_run_limit_is_a_429_and_reads_nothing(setup: Setup) -> None:
    release = hold(setup, "wallet_db")
    api = setup.client(max_concurrent=1)
    try:
        first = lookup(setup, api).json()["id"]
        refused = lookup(setup, api)
        assert refused.status_code == 429
        assert "already running" in refused.json()["detail"]
    finally:
        release.set()
    finished(api, first)
    assert runs_stored(setup) == 1  # nothing queued, stored or read for the refused one


def test_a_lookup_whose_draft_would_write_is_refused(setup: Setup) -> None:
    writes = ENTITY.replace(
        "FROM provider_webhooks WHERE", "INTO copied FROM provider_webhooks WHERE"
    )
    assert writes != ENTITY
    response = lookup(setup, entity=writes)
    assert response.status_code == 422
    assert read_nothing(setup)


def test_a_draft_run_reads_no_plan_so_the_screen_waits_for_the_save_checks(setup: Setup) -> None:
    # Why NewEntityPage holds its lookup until the save checks, index check included, have passed.
    finished(setup.client(), lookup(setup).json()["id"])
    assert setup.plans.asked == []
