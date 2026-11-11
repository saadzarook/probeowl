# Author: @SaadZarook
"""Make one audit event fail to be written, as a full or unreachable store would (#242, #273)."""

from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Connection

from probeowl.store.audit import AuditEvent


def audit_fails(monkeypatch: pytest.MonkeyPatch, module: ModuleType, action: str) -> None:
    """Writing this action's event fails in module, which imported append_event."""
    append = module.append_event

    def failing(conn: Connection, workspace: str, event: AuditEvent, *rest: Any) -> None:
        if event.action == action:
            raise RuntimeError("the store could not be written")
        append(conn, workspace, event, *rest)

    monkeypatch.setattr(module, "append_event", failing)
