# Author: @SaadZarook
"""What the Connectors screen shows for one binding. Never where it points or who logs in."""

from enum import StrEnum

from pydantic import Field

from probeowl.model.common import ProbeOwlModel


class BindingHealth(StrEnum):
    """How a binding is doing right now."""

    HEALTHY = "healthy"
    LAGGING = "lagging"
    PAUSED = "paused"
    STOPPED = "stopped"  # a kill switch covers it
    UNREACHABLE = "unreachable"
    NOT_CHECKED = "not_checked"
    NOT_CONFIGURED = "not_configured"


class PlanAccess(StrEnum):
    """Whether a SQL binding's login can read plans, which the plan save checks need."""

    READABLE = "readable"
    DENIED = "denied"
    NOT_CHECKED = "not_checked"


class ConnectorStatus(ProbeOwlModel):
    """One connector in one environment."""

    connector: str
    type: str
    owner: str | None = None
    environment: str
    production: bool
    health: BindingHealth
    reason: str | None = None
    replica: bool = False
    role: str | None = Field(
        default=None,
        description="What kind of copy this binding reads from: Read replica, Readable secondary "
        "or Active Data Guard standby. Null for a REST API, for a SQL binding that is not a "
        "replica, or with no binding here.",
    )
    limits: list[str] = Field(
        default_factory=list,
        description="The limits a lookup runs under, as enforced today: its time limit, then the "
        "default row cap (a many location can set its own), or a REST API's rate budget and "
        "that it only reads with GET.",
    )
    row_ceiling: int | None = Field(
        default=None,
        description="The most rows one lookup through this connector may return. Null with no "
        "binding here.",
    )
    transport: str | None = None
    time_zone: str | None = Field(
        default=None,
        description="Oracle: the time zone its sessions use, such as '+04:00 (from the binding)' "
        "or 'the database's own (DBTIMEZONE)'. Null for SQL Server and MariaDB, which have none "
        "to set, for a REST API, or with no binding here.",
    )
    lag_seconds: float | None = None
    lag_note: str | None = None
    max_replica_lag_seconds: float | None = None
    plan_access: PlanAccess | None = Field(
        default=None, description="Null for a REST API, which has no plans."
    )
    plan_message: str | None = None
    guards: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(
        default_factory=list,
        description="What the statement guard refuses in this dialect's probe SQL, by example. "
        "Empty for a REST API, whose guards say what it may do.",
    )


class ConnectionTest(ProbeOwlModel):
    """What Test connection found for one binding."""

    connector: str
    environment: str
    reachable: bool
    reason: str | None = Field(default=None, description="Why not, when it could not be reached.")
    duration_ms: int
