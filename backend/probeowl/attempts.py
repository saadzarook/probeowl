# Author: @SaadZarook
"""How often a password may be tried on one binding (#61).

Testing a connection with a password typed in, for a connection not saved yet or a new password
for one that is, is limited to 5 attempts per connector and environment in any minute. Repeated
wrong passwords could otherwise lock the database account: Oracle's default profile locks one
after 10 failed logins.
"""

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from probeowl.errors import TooManyAttempts

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 60.0

Key = tuple[str, str, str]  # (application, connector, environment)


class AttemptLimit:
    """At most MAX_ATTEMPTS per key in any WINDOW_SECONDS; counted whatever their outcome."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._attempts: dict[Key, deque[float]] = {}

    def attempt(self, key: Key) -> None:
        """Count one attempt; TooManyAttempts, counting nothing, when the key has had its five."""
        now = self._clock()
        with self._lock:
            recent = self._attempts.setdefault(key, deque())
            while recent and now - recent[0] >= WINDOW_SECONDS:
                recent.popleft()
            if len(recent) >= MAX_ATTEMPTS:
                raise TooManyAttempts(max(1, math.ceil(WINDOW_SECONDS - (now - recent[0]))))
            recent.append(now)
