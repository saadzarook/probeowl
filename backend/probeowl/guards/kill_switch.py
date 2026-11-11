# Author: @SaadZarook
"""The kill switch: while one is on, nothing reads through what it covers.

Every read asks here first, so a switch takes effect at the next lookup: runs, plan checks,
schema import, live status and Test connection. A lookup already in flight ends within its
statement timeout. The switches live in the store, so they survive a restart, and in team mode
reach everyone using it. If the store cannot be read, nothing can be shown to be allowed, so
every read is refused.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from pydantic import JsonValue
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from probeowl.errors import KillSwitchEngaged, KillSwitchUnknown, NotFoundError
from probeowl.model.kill_switch import KillScope, KillSwitch, KillTarget, target_key
from probeowl.model.run import Actor
from probeowl.store.audit import AuditEvent, append_event
from probeowl.store.kill_switches import delete_switch, insert_switch, read_switch, read_switches
from probeowl.store.tables import LOCAL_WORKSPACE

_UNREADABLE = "the store cannot be read, so the kill switch cannot be checked"
Source = TypeVar("Source")


class _Named(Protocol):
    connector: str


Named = TypeVar("Named", bound=_Named)


def _message(switch: KillSwitch) -> str:
    what = {
        KillScope.GLOBAL: "the global kill switch",
        KillScope.APPLICATION: f"the kill switch on application {switch.application}",
        KillScope.CONNECTOR: f"the kill switch on connector {switch.connector}",
    }[switch.scope]
    return f"stopped by {what}: {switch.reason}"


class KillSwitches:
    """The kill switches of one store."""

    def __init__(self, store: Engine, workspace_id: str = LOCAL_WORKSPACE):
        self._store = store
        self._workspace = workspace_id

    def all(self) -> list[KillSwitch]:
        """Every switch that is on."""
        try:
            with self._store.connect() as conn:
                return read_switches(conn, self._workspace)
        except SQLAlchemyError as err:
            raise KillSwitchUnknown(_UNREADABLE) from err

    def reason(self, application: str, connector: str) -> str | None:
        """Why reads through this connector are stopped, or None. KillSwitchUnknown if unsure."""
        keys = [
            target_key(None, None),
            target_key(application, None),
            target_key(application, connector),
        ]
        try:
            with self._store.connect() as conn:
                found = read_switches(conn, self._workspace, keys)
        except SQLAlchemyError as err:
            raise KillSwitchUnknown(_UNREADABLE) from err
        # Broadest first, so the reason named is the last one anyone would turn off.
        for key in keys:
            for switch in found:
                if switch.key == key:
                    return _message(switch)
        return None

    def check(self, application: str, connector: str) -> None:
        """Nothing, if this connector may be read; KillSwitchEngaged or KillSwitchUnknown if not."""
        reason = self.reason(application, connector)
        if reason is not None:
            raise KillSwitchEngaged(reason)

    def guarded(
        self, application: str, opener: Callable[[Named, str], Source]
    ) -> Callable[[Named, str], Source]:
        """The opener, refusing a connector whose reads are stopped."""

        def opened(connector: Named, environment: str) -> Source:
            self.check(application, connector.connector)
            return opener(connector, environment)

        return opened

    def engage(
        self, target: KillTarget, reason: str, reference: str | None, actor: Actor
    ) -> KillSwitch:
        """Turn a switch on, audited. Already on: the switch as it is, with nothing changed."""
        switch = KillSwitch(
            **target.model_dump(),
            reason=reason,
            reference=reference,
            actor=actor,
            engaged_at=datetime.now(UTC),
        )
        with self._store.begin() as conn:
            existing = read_switch(conn, self._workspace, target.key)
            if existing is not None:
                return existing
            insert_switch(conn, self._workspace, switch)
            self._audit(conn, "kill_switch_engaged", target, reason, reference, actor)
        return switch

    def release(
        self, target: KillTarget, reason: str, reference: str | None, actor: Actor
    ) -> KillSwitch:
        """Turn a switch off, audited; NotFoundError if it is not on."""
        with self._store.begin() as conn:
            existing = read_switch(conn, self._workspace, target.key)
            if existing is None:
                raise NotFoundError(f"no kill switch is on for {target.key}")
            delete_switch(conn, self._workspace, target.key)
            self._audit(conn, "kill_switch_released", target, reason, reference, actor)
        return existing

    def _audit(
        self,
        conn: Connection,
        action: str,
        target: KillTarget,
        reason: str,
        reference: str | None,
        actor: Actor,
    ) -> None:
        details: dict[str, JsonValue] = {
            "scope": target.scope.value,
            "connector": target.connector,
            "reason": reason,
            "reference": reference,
        }
        event = AuditEvent(
            action=action, actor=actor, application=target.application, details=details
        )
        append_event(conn, self._workspace, event)
