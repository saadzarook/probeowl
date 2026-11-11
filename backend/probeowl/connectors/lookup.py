# Author: @SaadZarook
"""What every connector returns from one lookup: the material for a location's evidence."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from probeowl.guards.statement import ParameterValue


@dataclass(frozen=True)
class ReplicaLag:
    """How far a replica is behind its primary, or why that is not known."""

    seconds: float | None  # None: unknown; math.inf: replication is not running
    note: str  # for evidence: "replica lag 3 s", "replica lag unknown: ..."
    cause: str | None = None  # why unknown, briefly: "missing REPLICA MONITOR"


@dataclass(frozen=True)
class LookupResult:
    """What one lookup ran, how, and what came back."""

    query: str  # the SQL or the request exactly as sent
    parameters: dict[str, ParameterValue]
    guards: list[str]
    rows: list[dict[str, Any]]
    truncated: str | None
    started_at: datetime
    duration_ms: int
    rows_total: int | None = None  # rows that matched, when counted because more did
