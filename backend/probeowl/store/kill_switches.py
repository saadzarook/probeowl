# Author: @SaadZarook
"""The kill switches that are on: a row is a switch that is on. The audit keeps the history."""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Connection, delete, insert, select

from probeowl.model.kill_switch import KillSwitch
from probeowl.model.run import Actor
from probeowl.store.tables import kill_switches


def _switch(row: Any) -> KillSwitch:
    return KillSwitch(
        scope=row.scope,
        application=row.application,
        connector=row.connector,
        reason=row.reason,
        reference=row.reference,
        actor=Actor(kind=row.actor_kind, name=row.actor_name),
        engaged_at=row.engaged_at,
    )


def read_switches(
    conn: Connection, workspace_id: str, keys: Sequence[str] | None = None
) -> list[KillSwitch]:
    """The switches that are on, optionally only those with these keys."""
    query = select(kill_switches).where(kill_switches.c.workspace_id == workspace_id)
    if keys is not None:
        query = query.where(kill_switches.c.target.in_(keys))
    return [_switch(row) for row in conn.execute(query.order_by(kill_switches.c.target))]


def read_switch(conn: Connection, workspace_id: str, key: str) -> KillSwitch | None:
    """One switch, if it is on."""
    found = read_switches(conn, workspace_id, [key])
    return found[0] if found else None


def insert_switch(conn: Connection, workspace_id: str, switch: KillSwitch) -> None:
    """Record a switch as on."""
    conn.execute(
        insert(kill_switches).values(
            workspace_id=workspace_id,
            target=switch.key,
            scope=switch.scope.value,
            application=switch.application,
            connector=switch.connector,
            reason=switch.reason,
            reference=switch.reference,
            actor_kind=switch.actor.kind.value,
            actor_name=switch.actor.name,
            engaged_at=switch.engaged_at,
        )
    )


def delete_switch(conn: Connection, workspace_id: str, key: str) -> None:
    """Record a switch as off."""
    conn.execute(
        delete(kill_switches).where(
            kill_switches.c.workspace_id == workspace_id, kill_switches.c.target == key
        )
    )
