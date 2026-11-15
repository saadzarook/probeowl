# Author: @SaadZarook
"""The append-only audit: every run started, every write query copied, every conclusion."""

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, JsonValue
from sqlalchemy import Connection, and_, insert, or_, select

from probeowl.model.common import Name, ProbeOwlModel
from probeowl.model.run import Actor
from probeowl.store.tables import audit_events


class AuditEvent(ProbeOwlModel):
    """One audit record. Never changed or removed once written."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: Name = Field(description="What happened, such as run_started or write_query_copied.")
    actor: Actor
    application: Name | None = None
    environment: Name | None = None
    run_id: str | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict, description="Masked values only.")


def append_event(conn: Connection, workspace_id: str, event: AuditEvent) -> None:
    """Append one event to the audit."""
    conn.execute(
        insert(audit_events).values(
            workspace_id=workspace_id,
            id=event.id,
            occurred_at=event.occurred_at,
            action=event.action,
            actor_kind=event.actor.kind.value,
            actor_name=event.actor.name,
            actor_on_behalf_of=event.actor.on_behalf_of,
            application=event.application,
            environment=event.environment,
            run_id=event.run_id,
            details=event.details,
        )
    )


def list_events(conn: Connection, workspace_id: str, run_id: str | None = None) -> list[AuditEvent]:
    """List a workspace's audit events, oldest first, optionally for one run."""
    query = select(audit_events).where(audit_events.c.workspace_id == workspace_id)
    if run_id is not None:
        query = query.where(audit_events.c.run_id == run_id)
    rows = conn.execute(query.order_by(audit_events.c.occurred_at, audit_events.c.id))
    return [_event(row) for row in rows]


class AuditFilter(ProbeOwlModel):
    """Which events to list; each one given narrows the list. Exact names, never patterns."""

    actor: str | None = Field(
        default=None, description="Who acted, or the person an agent acted for."
    )
    action: Name | None = None
    application: Name | None = None
    environment: Name | None = None


def list_recent_events(
    conn: Connection,
    workspace_id: str,
    found: AuditFilter,
    before: tuple[datetime, str] | None,
    limit: int,
) -> list[AuditEvent]:
    """Audit events newest first, filtered, starting after before: the (occurred_at, id) of the
    last event already listed, the id breaking ties."""
    c = audit_events.c
    query = select(audit_events).where(c.workspace_id == workspace_id)
    if found.actor is not None:
        query = query.where(or_(c.actor_name == found.actor, c.actor_on_behalf_of == found.actor))
    for column, value in (
        (c.action, found.action),
        (c.application, found.application),
        (c.environment, found.environment),
    ):
        if value is not None:
            query = query.where(column == value)
    if before is not None:
        at, event_id = before
        query = query.where(or_(c.occurred_at < at, and_(c.occurred_at == at, c.id < event_id)))
    query = query.order_by(c.occurred_at.desc(), c.id.desc()).limit(limit)
    return [_event(row) for row in conn.execute(query)]


def _event(row: Any) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        occurred_at=row.occurred_at,
        action=row.action,
        actor=Actor(kind=row.actor_kind, name=row.actor_name, on_behalf_of=row.actor_on_behalf_of),
        application=row.application,
        environment=row.environment,
        run_id=row.run_id,
        details=row.details,
    )
