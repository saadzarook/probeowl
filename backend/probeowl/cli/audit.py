# Author: @SaadZarook
"""owl audit: read the audit through owl serve, newest first, as the Audit page shows it (#79)."""

import json
from typing import Annotated, Any

import typer

from probeowl.cli import server
from probeowl.store.audit import AuditEvent

_Server = Annotated[str | None, typer.Option("--server", help=server.SERVER_HELP)]


def audit(
    limit: Annotated[
        int, typer.Option(min=1, max=1000, help="How many events, newest first.")
    ] = 50,
    actor: Annotated[
        str | None, typer.Option(help="Who acted, or the person an agent acted for.")
    ] = None,
    action: Annotated[str | None, typer.Option(help="Such as run_started or secret_saved.")] = None,
    application: Annotated[str | None, typer.Option(help="One application.")] = None,
    env: Annotated[str | None, typer.Option("--env", help="One environment.")] = None,
    as_json: Annotated[
        bool, typer.Option("--json", help="One JSON array of events, for scripts.")
    ] = False,
    server_url: _Server = None,
    config: server.NoConfig = None,
) -> None:
    """Read the audit, newest first: who did what, where, with masked details. Never changes it."""
    filters = {"actor": actor, "action": action, "application": application, "environment": env}
    params = {k: v for k, v in filters.items() if v is not None}
    events: list[AuditEvent] = []
    with server.connected(server_url) as api:
        before: str | None = None
        while len(events) < limit:
            page = server.get(api, "/api/audit", params | ({"before": before} if before else {}))
            events += [AuditEvent.model_validate(e) for e in page["events"]]
            before = page.get("next")
            if before is None:
                break
    events = events[:limit]
    if as_json:
        typer.echo(json.dumps([e.model_dump(mode="json") for e in events], indent=2))
        return
    if not events:
        typer.echo("No audit events match.")
        return
    for event in events:
        typer.echo(_line(event))


def _line(event: AuditEvent) -> str:
    """'2026-11-15 10:02:03 UTC  s.khan  run_started  wallet/prod  run 9305…  reason=incident'."""
    when = event.occurred_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    where = "/".join(p for p in (event.application, event.environment) if p) or "-"
    run = f"run {event.run_id}" if event.run_id else ""
    parts = [when, event.actor.name, event.action, where, run, _details(event.details)]
    return "  ".join(p for p in parts if p)


def _details(details: dict[str, Any]) -> str:
    """key=value, compact: nested values as JSON, never wrapped."""
    shown = []
    for key, value in details.items():
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        shown.append(f"{key}={text}")
    return " ".join(shown)
