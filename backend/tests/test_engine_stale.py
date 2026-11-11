# Author: @SaadZarook
"""Stale: a Missing record on a replica further behind than the location's window."""

import pytest

from probeowl.connectors.lookup import ReplicaLag
from probeowl.engine.locations import UNKNOWN_LAG_CAVEAT, lag_caveat
from probeowl.engine.runner import EXPECTATION_CAVEAT, ProbeResult, run_probe
from probeowl.model import Entity, Outcome, RunStatus
from probeowl.model.run import ExpectationResult
from tests.wallet_fakes import ENTITY, PROBE, connectors


def with_window(within: str) -> Entity:
    data = ENTITY.model_dump(by_alias=True, exclude_none=True)
    data["locations"]["ledger"]["within"] = within
    return Entity.model_validate(data)


def run(lag: ReplicaLag | None, within: str = "30s", topup: str = "tp_91f2c7") -> ProbeResult:
    lags = {} if lag is None else {"ledger_db": lag}
    return run_probe(PROBE, with_window(within), topup, connectors(), replica_lag=lags)


def ledger(result: ProbeResult) -> tuple[Outcome, str | None, list[str]]:
    [found] = [r for r in result.locations if r.location == "ledger"]
    assert found.evidence is not None
    return found.outcome, found.reason, found.evidence.guards


def test_missing_on_a_replica_behind_the_window_is_stale() -> None:
    result = run(ReplicaLag(45, "replica lag 45 s"))
    outcome, reason, guards = ledger(result)
    assert outcome is Outcome.STALE
    assert reason == "replica lag 45 s is longer than the 30s window: missing cannot be trusted"
    assert guards[-1] == "replica lag 45 s"
    # Expectations that need it cannot be decided, so they are skipped, and the run fails.
    [count] = [e for e in result.expectations if e.expectation == "count(ledger) == 2"]
    assert count.result is ExpectationResult.SKIP
    assert count.detail == f"needs ledger, which is Stale ({reason})"
    assert result.status is RunStatus.FAILED


@pytest.mark.parametrize(
    ("lag", "within"),
    [
        (ReplicaLag(10, "replica lag 10 s"), "30s"),  # within the window
        (ReplicaLag(45, "replica lag 45 s"), "1m"),
        (ReplicaLag(None, "replica lag unknown: no rights"), "30s"),  # unknown: not Stale
        (None, "30s"),  # not a replica
    ],
)
def test_missing_stays_missing_when_the_lag_is_within_the_window_or_unknown(
    lag: ReplicaLag | None, within: str
) -> None:
    assert ledger(run(lag, within))[0] is Outcome.MISSING


def test_a_found_record_is_found_however_far_behind() -> None:
    outcome, _, guards = ledger(run(ReplicaLag(45, "replica lag 45 s"), topup="tp_91f2b9"))
    assert outcome is Outcome.FOUND
    assert "replica lag 45 s" in guards


def test_without_a_window_missing_is_missing() -> None:
    result = run_probe(
        PROBE, ENTITY, "tp_91f2c7", connectors(), replica_lag={"ledger_db": ReplicaLag(45, "x")}
    )
    assert ledger(result)[0] is Outcome.MISSING


# --- Unknown lag in Production: Missing, with a caveat ---------------------------------------

UNKNOWN = ReplicaLag(None, "replica lag unknown: no rights", "missing REPLICA MONITOR")


def caveated(lag: ReplicaLag | None, topup: str = "tp_91f2c7", within: str = "30s") -> ProbeResult:
    lags = {} if lag is None else {"ledger_db": lag}
    return run_probe(
        PROBE, with_window(within), topup, connectors(), replica_lag=lags, caveat_unknown_lag=True
    )


def test_missing_on_a_replica_whose_lag_is_unknown_carries_the_caveat() -> None:
    result = caveated(UNKNOWN)
    [found] = [r for r in result.locations if r.location == "ledger"]
    assert found.outcome is Outcome.MISSING
    assert found.caveat == UNKNOWN_LAG_CAVEAT
    assert found.caveat == "replica lag unknown, the record may not have arrived yet"
    # The status is the same as without the caveat; the expectation that read it says why.
    assert result.status is RunStatus.FAILED
    [count] = [e for e in result.expectations if e.expectation == "count(ledger) == 2"]
    assert count.result is ExpectationResult.FAIL
    assert count.caveat == EXPECTATION_CAVEAT
    # Only the locations that are Missing on that replica carry it.
    assert [r.location for r in result.locations if r.caveat] == ["ledger"]


def test_an_expectation_that_does_not_read_the_missing_record_has_no_caveat() -> None:
    result = caveated(UNKNOWN)
    unread = [e for e in result.expectations if "ledger" not in e.expectation]
    assert unread
    assert all(e.caveat is None for e in unread)


@pytest.mark.parametrize(
    "lag",
    [
        ReplicaLag(10, "replica lag 10 s"),  # known, within the window
        ReplicaLag(45, "replica lag 45 s"),  # known, and Stale says so instead
        None,  # not a replica
    ],
)
def test_a_known_lag_or_no_replica_gives_no_caveat(lag: ReplicaLag | None) -> None:
    result = caveated(lag)
    assert all(r.caveat is None for r in result.locations)
    assert all(e.caveat is None for e in result.expectations)


def test_a_found_record_has_no_caveat_however_unknown_the_lag() -> None:
    result = caveated(UNKNOWN, topup="tp_91f2b9")
    assert all(r.caveat is None for r in result.locations)
    assert all(e.caveat is None for e in result.expectations)


def test_outside_production_unknown_lag_gives_no_caveat() -> None:
    result = run(UNKNOWN)
    assert ledger(result)[0] is Outcome.MISSING
    assert all(r.caveat is None for r in result.locations)


def test_lag_caveat_is_for_missing_only() -> None:
    assert lag_caveat(Outcome.MISSING, UNKNOWN) == UNKNOWN_LAG_CAVEAT
    for outcome in (Outcome.FOUND, Outcome.ERROR, Outcome.AMBIGUOUS, Outcome.BLOCKED):
        assert lag_caveat(outcome, UNKNOWN) is None
    assert lag_caveat(Outcome.MISSING, None) is None
