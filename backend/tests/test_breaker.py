# Author: @SaadZarook
"""The circuit breaker: lag over the limit, or slow lookups in a row, pause a connector."""

import math

from probeowl.guards.breaker import PAUSE_S, SLOW_IN_A_ROW, SLOW_S, CircuitBreaker

KEY = ("wallet", "wallet_db", "prod")
OTHER = ("wallet", "wallet_db", "uat")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_lag_over_the_limit_pauses_the_connector_for_a_while() -> None:
    clock = Clock()
    breaker = CircuitBreaker(clock)
    reason = breaker.replica_lag(KEY, 120, 60)
    assert reason == "wallet_db is paused: replica lag 120 s (max 60 s)"
    assert breaker.paused(KEY) == f"{reason}; paused for another {PAUSE_S:g} s"
    assert breaker.paused(OTHER) is None  # per environment
    clock.now += PAUSE_S - 15.5
    assert breaker.paused(KEY) == f"{reason}; paused for another 16 s"
    clock.now += 16
    assert breaker.paused(KEY) is None


def test_lag_within_the_limit_or_unknown_does_not_pause() -> None:
    breaker = CircuitBreaker(Clock())
    assert breaker.replica_lag(KEY, 60, 60) is None
    assert breaker.replica_lag(KEY, None, 60) is None
    assert breaker.paused(KEY) is None


def test_replication_that_is_not_running_pauses() -> None:
    breaker = CircuitBreaker(Clock())
    assert breaker.replica_lag(KEY, math.inf, 60) == (
        "wallet_db is paused: replication to the replica is not running (max 60 s)"
    )


def test_several_slow_or_failed_lookups_in_a_row_pause() -> None:
    breaker = CircuitBreaker(Clock())
    breaker.lookup(KEY, SLOW_S + 0.1, failed=False)
    breaker.lookup(KEY, 0.01, failed=True)
    assert breaker.paused(KEY) is None
    breaker.lookup(KEY, SLOW_S + 0.1, failed=False)
    assert breaker.paused(KEY) is not None
    assert f"{SLOW_IN_A_ROW} lookups in a row were slower than {SLOW_S:g} s or failed" in (
        breaker.paused(KEY) or ""
    )


def test_a_quick_lookup_resets_the_count() -> None:
    breaker = CircuitBreaker(Clock())
    for _ in range(SLOW_IN_A_ROW - 1):
        breaker.lookup(KEY, 0.01, failed=True)
    breaker.lookup(KEY, 0.01, failed=False)
    for _ in range(SLOW_IN_A_ROW - 1):
        breaker.lookup(KEY, 0.01, failed=True)
    assert breaker.paused(KEY) is None
