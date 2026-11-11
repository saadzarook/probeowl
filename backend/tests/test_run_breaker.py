# Author: @SaadZarook
"""The circuit breaker through the API: lagging or slow connectors skip runs, with the reason."""

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

import pytest

from probeowl.connectors.lookup import ReplicaLag
from probeowl.errors import ConnectorError
from probeowl.guards.breaker import PAUSE_S, SLOW_IN_A_ROW, CircuitBreaker
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.test_runs import finished, start
from tests.wallet_fakes import Fake


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Replica(Fake):
    """A SQL connector on a replica that reports its lag."""

    def __init__(
        self, answer: Callable[[str, Mapping[str, object]], list[dict[str, Any]]], lag: float | None
    ):
        super().__init__(answer)
        self.lag = lag
        self.measured = 0

    def replica_lag(self) -> ReplicaLag:
        self.measured += 1
        if self.lag is None:
            return ReplicaLag(None, "replica lag unknown: the server reports no replication")
        return ReplicaLag(self.lag, f"replica lag {self.lag:g} s")


@pytest.fixture
def clock(setup: Setup) -> Clock:
    clock = Clock()
    setup.breaker = CircuitBreaker(clock)
    return clock


def on_replica(setup: Setup, lag: float | None) -> Replica:
    replica = Replica(setup.fakes["ledger_db"].answer, lag)
    setup.fakes["ledger_db"] = replica
    return replica


@pytest.mark.usefixtures("clock")
def test_a_replica_within_its_lag_limit_runs_and_shows_the_lag(setup: Setup) -> None:
    replica = on_replica(setup, 3)
    run = finished(setup.client(), start(setup.client(), value="tp_91f2b9"))
    assert run["status"] == "passed"
    assert replica.measured == 1
    [ledger] = [r for r in run["locations"] if r["location"] == "ledger"]
    assert "replica lag 3 s" in ledger["evidence"]["guards"]


@pytest.mark.usefixtures("clock")
def test_an_unknown_lag_does_not_pause_and_says_why(setup: Setup) -> None:
    on_replica(setup, None)
    run = finished(setup.client(), start(setup.client(), value="tp_91f2b9"))
    assert run["status"] == "passed"
    [ledger] = [r for r in run["locations"] if r["location"] == "ledger"]
    assert "replica lag unknown: the server reports no replication" in ledger["evidence"]["guards"]


def test_a_lagging_replica_skips_the_run_and_pauses_the_connector(
    setup: Setup, clock: Clock
) -> None:
    on_replica(setup, 120)
    client = setup.client()
    run = finished(client, start(client))
    assert run["status"] == "skipped"
    assert run["skip_reason"] == "ledger_db is paused: replica lag 120 s (max 60 s)"
    assert run["locations"] == [] and run["expectations"] == []
    assert not setup.fakes["wallet_db"].calls  # nothing was looked up
    with setup.store.connect() as conn:
        _, ended = list_events(conn, LOCAL_WORKSPACE, run["id"])
    assert ended.details["status"] == "skipped"
    assert ended.details["skip_reason"] == run["skip_reason"]

    # The next run is skipped before it starts, without measuring again.
    setup.fakes["ledger_db"] = Replica(setup.fakes["ledger_db"].answer, 0)
    second = client.get(f"/api/runs/{start(client)}").json()
    assert second["status"] == "skipped"
    assert second["skip_reason"] == (
        f"ledger_db is paused: replica lag 120 s (max 60 s); paused for another {PAUSE_S:g} s"
    )
    assert setup.fakes["ledger_db"].measured == 0  # type: ignore[attr-defined]

    # Once the pause ends, the next run measures again and runs.
    clock.now += PAUSE_S
    assert finished(client, start(client))["status"] == "passed"


@pytest.mark.usefixtures("clock")
def test_a_paused_connector_only_skips_runs_that_need_it(setup: Setup) -> None:
    on_replica(setup, 120)
    client = setup.client()
    finished(client, start(client))
    inspect = start(client, application="card_access", probe="visitor_cards", value="C204518")
    assert finished(client, inspect)["status"] == "failed"  # ran, with its planted issues


@pytest.mark.usefixtures("clock")
def test_a_skipped_run_takes_no_run_slot(setup: Setup) -> None:
    on_replica(setup, 120)
    client = setup.client(max_concurrent=1)
    for _ in range(3):
        assert finished(client, start(client))["status"] == "skipped"


@pytest.mark.usefixtures("clock")
def test_failing_lookups_in_a_row_pause_the_connector(setup: Setup) -> None:
    def down(lookup: str, p: Any) -> list[dict[str, Any]]:
        raise ConnectorError("could not connect: connection refused")

    setup.fakes["checkout_api"].answer = down
    client = setup.client()
    for _ in range(SLOW_IN_A_ROW):
        # Each run reads payment once (no fallback after an error).
        assert finished(client, start(client))["status"] == "failed"
    run = finished(client, start(client))
    assert run["status"] == "skipped"
    assert run["skip_reason"].startswith(
        f"checkout_api is paused: {SLOW_IN_A_ROW} lookups in a row were slower than"
    )


# --- Unknown lag on a Production replica: a warning, not a failure --------------------------


class Unknown(Replica):
    def replica_lag(self) -> ReplicaLag:
        return ReplicaLag(
            None,
            "replica lag unknown: Oracle: ORA-00942: table or view does not exist",
            "missing SELECT on SYS.V_$DATAGUARD_STATS",
        )


@pytest.mark.usefixtures("clock")
def test_unknown_lag_on_a_production_replica_warns_without_failing(setup: Setup) -> None:
    setup.fakes["ledger_db"] = Unknown(setup.fakes["ledger_db"].answer, None)
    client = setup.client()
    run = finished(client, start(client, value="tp_91f2b9"))
    assert run["status"] == "passed"
    assert run["warnings"] == [
        "Replica lag unknown for ledger_db (missing SELECT on SYS.V_$DATAGUARD_STATS): "
        "Missing results can't be confirmed fresh"
    ]
    with setup.store.connect() as conn:
        _, ended = list_events(conn, LOCAL_WORKSPACE, run["id"])
    assert ended.details["warnings"] == run["warnings"]


@pytest.mark.usefixtures("clock")
def test_a_lookup_that_breaks_a_save_rule_runs_and_the_run_says_so(setup: Setup) -> None:
    app = setup.applications["wallet"]
    entity = app.entities["topup"]
    ledger = entity.locations["ledger"].model_copy(
        update={
            "lookup": "SELECT account, direction, amount, posted_at FROM ledger_entries "
            "WHERE account = :ledger_txn_id"
        }
    )
    changed = entity.model_copy(update={"locations": {**entity.locations, "ledger": ledger}})
    setup.applications["wallet"] = replace(app, entities={**app.entities, "topup": changed})
    client = setup.client()
    run = finished(client, start(client, value="tp_91f2b9"))
    assert run["status"] != "skipped"
    [warning] = [w for w in run["warnings"] if "save rule" in w]
    assert warning.startswith(
        "Breaks the Time filter save rule in ledger: ledger_entries is a Transaction table: "
        "filter on its time column posted_at"
    )
    assert run["locations"]  # it still ran


@pytest.mark.usefixtures("clock")
def test_a_lookup_that_keeps_the_rules_has_no_rule_warning(setup: Setup) -> None:
    client = setup.client()
    run = finished(client, start(client, value="tp_91f2b9"))
    assert not [w for w in run["warnings"] if "save rule" in w]


@pytest.mark.usefixtures("clock")
def test_a_known_lag_gives_no_warning(setup: Setup) -> None:
    on_replica(setup, 3)
    run = finished(setup.client(), start(setup.client(), value="tp_91f2b9"))
    assert run["warnings"] == []


@pytest.mark.usefixtures("clock")
def test_outside_production_unknown_lag_gives_no_warning(setup: Setup) -> None:
    # The UAT bindings are not replicas, so no lag is measured there at all.
    setup.fakes["ledger_db"] = Unknown(setup.fakes["ledger_db"].answer, None)
    client = setup.client()
    run = finished(client, start(client, value="tp_91f2b9", environment="uat"))
    assert run["warnings"] == []
    assert setup.fakes["ledger_db"].measured == 0  # type: ignore[attr-defined]


# --- Unknown lag on a Production replica: a Missing result carries a caveat ----------------


@pytest.mark.usefixtures("clock")
def test_a_missing_result_on_an_unknown_lag_replica_carries_the_caveat(setup: Setup) -> None:
    setup.fakes["ledger_db"] = Unknown(setup.fakes["ledger_db"].answer, None)
    client = setup.client()
    run = finished(client, start(client, value="tp_91f2c7"))
    assert run["status"] == "failed"  # unchanged by the caveat
    [ledger] = [r for r in run["locations"] if r["location"] == "ledger"]
    assert ledger["outcome"] == "missing"
    assert ledger["caveat"] == "replica lag unknown, the record may not have arrived yet"
    assert run["caveat"] == "replica lag unknown: 1 Missing result may not have arrived yet"
    [count] = [e for e in run["expectations"] if e["expectation"] == "count(ledger) == 2"]
    assert count["caveat"] == (
        "reads a Missing record that may not have arrived yet: replica lag unknown"
    )
    with setup.store.connect() as conn:
        _, ended = list_events(conn, LOCAL_WORKSPACE, run["id"])
    assert ended.details["caveat"] == run["caveat"]


@pytest.mark.usefixtures("clock")
def test_a_run_with_nothing_missing_has_no_caveat_however_unknown_the_lag(setup: Setup) -> None:
    setup.fakes["ledger_db"] = Unknown(setup.fakes["ledger_db"].answer, None)
    client = setup.client()
    run = finished(client, start(client, value="tp_91f2b9"))
    assert run["status"] == "passed"
    assert run.get("caveat") is None
    assert all(r.get("caveat") is None for r in run["locations"])


@pytest.mark.usefixtures("clock")
def test_a_known_lag_gives_no_caveat(setup: Setup) -> None:
    on_replica(setup, 3)
    run = finished(setup.client(), start(setup.client(), value="tp_91f2c7"))
    assert run["status"] == "failed"
    assert run.get("caveat") is None


@pytest.mark.usefixtures("clock")
def test_outside_production_a_missing_result_has_no_caveat(setup: Setup) -> None:
    setup.fakes["ledger_db"] = Unknown(setup.fakes["ledger_db"].answer, None)
    client = setup.client()
    run = finished(client, start(client, value="tp_91f2c7", environment="uat"))
    assert run.get("caveat") is None
    assert all(r.get("caveat") is None for r in run["locations"])
