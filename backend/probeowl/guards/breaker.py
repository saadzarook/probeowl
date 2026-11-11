# Author: @SaadZarook
"""The circuit breaker: pause a connector whose replica lags or whose lookups slow down.

While a connector is paused, runs that need it are skipped before any lookup, with the
reason. A pause ends on its own; the next run measures again, and pauses again if the
replica is still behind.
"""

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

PAUSE_S = 60.0
SLOW_S = 1.0  # half the default 2 s statement timeout
SLOW_IN_A_ROW = 3

# A connector in one environment of one application.
Key = tuple[str, str, str]


@dataclass(frozen=True)
class _Pause:
    reason: str
    until: float


class CircuitBreaker:
    """Which connectors are paused, and why. Shared by every run in owl serve."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._paused: dict[Key, _Pause] = {}
        self._slow: dict[Key, int] = {}

    def paused(self, key: Key) -> str | None:
        """Why the connector is paused and for how much longer, or None if it is not."""
        with self._lock:
            pause = self._paused.get(key)
            if pause is None:
                return None
            left = pause.until - self._clock()
            if left <= 0:
                del self._paused[key]
                return None
            return f"{pause.reason}; paused for another {math.ceil(left)} s"

    def replica_lag(self, key: Key, seconds: float | None, max_seconds: float) -> str | None:
        """Pause the connector if its replica is further behind than allowed; the reason."""
        if seconds is None or seconds <= max_seconds:
            return None
        behind = (
            "replication to the replica is not running"
            if math.isinf(seconds)
            else f"replica lag {seconds:g} s"
        )
        return self._pause(key, f"{key[1]} is paused: {behind} (max {max_seconds:g} s)")

    def lookup(self, key: Key, duration_s: float, failed: bool) -> None:
        """Record one lookup. Several slow or failed ones in a row pause the connector."""
        with self._lock:
            if not failed and duration_s <= SLOW_S:
                self._slow.pop(key, None)
                return
            self._slow[key] = self._slow.get(key, 0) + 1
            if self._slow[key] < SLOW_IN_A_ROW:
                return
        self._pause(
            key,
            f"{key[1]} is paused: {SLOW_IN_A_ROW} lookups in a row were slower than "
            f"{SLOW_S:g} s or failed",
        )

    def _pause(self, key: Key, reason: str) -> str:
        with self._lock:
            self._paused[key] = _Pause(reason, self._clock() + PAUSE_S)
            self._slow.pop(key, None)
        return reason
