# Author: @SaadZarook
"""owl reload: owl serve reads its config folder again, without a restart."""

from typing import Annotated

import typer

from probeowl.cli import server
from probeowl.model.reload import ConfigReload

_Server = Annotated[str | None, typer.Option("--server", help=server.SERVER_HELP)]


def reload(server_url: _Server = None, config: server.NoConfig = None) -> None:
    """Make owl serve load its config folder again, as it does at start.

    If owl serve would refuse the folder, nothing changes and the errors are shown (owl check
    lists them all). Refused while a run is running. Kill switches stay as they are.
    """
    with server.connected(server_url) as api:
        response = api.post("/api/config/reload")
        if response.status_code != 200:
            server.problem(response)
    for line in summary(ConfigReload.model_validate(response.json())):
        typer.echo(line)


def summary(report: ConfigReload) -> list[str]:
    """What was loaded and what changed, a line each."""
    lines = [
        f"Reloaded: {_count(report.applications, 'application')}, "
        f"{_count(report.connectors, 'connector')}, {_count(report.probes, 'probe')}."
    ]
    changes = [
        ("Applications added", report.applications_added),
        ("Applications removed", report.applications_removed),
        ("Connectors added", report.connectors_added),
        ("Connectors removed", report.connectors_removed),
        ("Connectors changed", report.connectors_changed),
    ]
    lines += [f"{label}: {', '.join(names)}" for label, names in changes if names]
    if len(lines) == 1:
        lines.append("No application or connector changed.")
    lines.append(report.note)
    return lines


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"
