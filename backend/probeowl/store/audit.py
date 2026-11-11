# Author: @SaadZarook
"""The append-only audit: every run started, every write query copied, every conclusion."""

import uuid
from datetime import UTC, datetime

from pydantic import Field, JsonValue
from sqlalchemy import Connection, insert, select

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
    return [
        AuditEvent(
            id=row.id,
            occurred_at=row.occurred_at,
            action=row.action,
            actor=Actor(
                kind=row.actor_kind, name=row.actor_name, on_behalf_of=row.actor_on_behalf_of
            ),
            application=row.application,
            environment=row.environment,
            run_id=row.run_id,
            details=row.details,
        )
        for row in rows
    ]
