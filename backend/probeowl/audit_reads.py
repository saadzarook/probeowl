# Author: @SaadZarook
"""Reading the audit (#79): pages of events, newest first, masked by today's rules.

The audit is append-only, so events written before an identifier became PII, or by v0.1 before
PII keys were masked, still hold its value (see owl store mask-pii). Each page is masked as it
is read, by the entity's PII rules as loaded now; an event whose entity is no longer known has
every identifier, subject and match masked. Nothing here writes.
"""

from datetime import datetime
from typing import Any

from pydantic import Field, JsonValue
from sqlalchemy import Engine, select

from probeowl.engine.masking import MASKED, key_is_pii
from probeowl.model.check import LoadedApplication
from probeowl.model.common import ProbeOwlModel
from probeowl.model.entity import Entity
from probeowl.store.audit import AuditEvent, AuditFilter, list_recent_events
from probeowl.store.tables import LOCAL_WORKSPACE, runs

EVENTS_PER_PAGE = 50


class AuditPage(ProbeOwlModel):
    """A page of audit events, newest first."""

    events: list[AuditEvent]
    next: str | None = Field(
        default=None, description="Pass as before for the next, older page; null when none."
    )


class AuditReader:
    """Pages of the audit for a person to read; never changes an event."""

    def __init__(
        self,
        applications: dict[str, LoadedApplication],
        store: Engine,
        workspace: str = LOCAL_WORKSPACE,
    ):
        self._applications = applications
        self._store = store
        self._workspace = workspace

    def page(
        self, found: AuditFilter, before: tuple[datetime, str] | None, limit: int = EVENTS_PER_PAGE
    ) -> AuditPage:
        """One page of events matching found, and the cursor of the next older page."""
        with self._store.connect() as conn:
            events = list_recent_events(conn, self._workspace, found, before, limit + 1)
            run_ids = {e.run_id for e in events if e.run_id is not None}
            entities: dict[str, tuple[str, str]] = {}
            if run_ids:
                rows = conn.execute(
                    select(runs.c.id, runs.c.application, runs.c.entity).where(
                        runs.c.workspace_id == self._workspace, runs.c.id.in_(run_ids)
                    )
                )
                entities = {row.id: (row.application, row.entity) for row in rows}
        shown = events[:limit]
        masked = [
            e.model_copy(update={"details": self._masked(e, entities.get(e.run_id or ""))})
            for e in shown
        ]
        last = shown[-1] if len(events) > limit else None
        return AuditPage(
            events=masked,
            next=f"{last.occurred_at.isoformat()}|{last.id}" if last is not None else None,
        )

    def _masked(self, event: AuditEvent, run: tuple[str, str] | None) -> dict[str, JsonValue]:
        application, name = run or (event.application, _text(event.details.get("entity")))
        entity = self._entity(application, name)
        return masked_details(event.details, entity)

    def _entity(self, application: str | None, name: str | None) -> Entity | None:
        app = self._applications.get(application or "")
        return app.entities.get(name or "") if app is not None else None


def masked_details(details: dict[str, JsonValue], entity: Entity | None) -> dict[str, JsonValue]:
    """An event's details with what is PII now masked. With no entity known: all of it."""
    shown = dict(details)
    pii_key = entity is None or key_is_pii(entity)
    pii = None if entity is None else {n for n, i in entity.identifiers.items() if i.pii}
    if pii_key and shown.get("subject") is not None:
        shown["subject"] = MASKED
    matches = shown.get("matches")
    if pii_key and isinstance(matches, list):
        shown["matches"] = [MASKED for _ in matches]
    identifiers = shown.get("identifiers")
    if isinstance(identifiers, dict):
        shown["identifiers"] = {
            name: MASKED
            if pii is None or name in pii or (pii_key and _is_root(name, entity))
            else value
            for name, value in identifiers.items()
        }
    return shown


def _is_root(name: str, entity: Entity | None) -> bool:
    return entity is not None and name == entity.root_key


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None
