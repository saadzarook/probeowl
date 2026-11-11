# Author: @SaadZarook
"""owl kill: turn kill switches on and off through owl serve, which knows the names."""

from typing import Annotated, Any

import typer

from probeowl.cli import server
from probeowl.model.kill_switch import KillSwitch

kill_app = typer.Typer(help="Stop every read: everywhere, for an application, or for a connector.")

_Everything = Annotated[
    bool, typer.Option("--all", help="The global switch: asked for by name, never by leaving out.")
]
_Application = Annotated[str | None, typer.Option(help="The application it covers.")]
_Connector = Annotated[
    str | None,
    typer.Option(help="The connector it covers, in every environment. Needs --application."),
]
_Reason = Annotated[str, typer.Option(help="Why. Recorded in the audit with your name.")]
_Reference = Annotated[str | None, typer.Option(help="Jira key, support case or change ID.")]
_Server = Annotated[str | None, typer.Option("--server", help=server.SERVER_HELP)]


def _target(everything: bool, application: str | None, connector: str | None) -> dict[str, Any]:
    if everything == (application is not None):
        server.stop("name what it covers: --all, or --application with an optional --connector")
    if connector is not None and application is None:
        server.stop("--connector needs --application")
    if everything:
        return {"scope": "global"}
    if connector is None:
        return {"scope": "application", "application": application}
    return {"scope": "connector", "application": application, "connector": connector}


def _line(switch: KillSwitch) -> str:
    what = "/".join(p for p in (switch.scope.value, switch.application, switch.connector) if p)
    return (
        f"{what}: {switch.reason} (by {switch.actor.name}, {switch.engaged_at:%Y-%m-%d %H:%M} UTC)"
    )


def _send(path: str, body: dict[str, Any], server_url: str | None) -> KillSwitch:
    with server.connected(server_url) as api:
        response = api.post(path, json=body)
        if response.status_code != 200:
            server.problem(response)
    return KillSwitch.model_validate(response.json())


@kill_app.command("on")
def turn_on(
    reason: _Reason,
    everything: _Everything = False,
    application: _Application = None,
    connector: _Connector = None,
    reference: _Reference = None,
    server_url: _Server = None,
    config: server.NoConfig = None,
) -> None:
    """Turn a kill switch on. Reads through what it covers stop at their next lookup."""
    body = _target(everything, application, connector) | {"reason": reason, "reference": reference}
    typer.echo(f"On: {_line(_send('/api/kill-switches', body, server_url))}")


@kill_app.command("off")
def turn_off(
    reason: _Reason,
    everything: _Everything = False,
    application: _Application = None,
    connector: _Connector = None,
    reference: _Reference = None,
    server_url: _Server = None,
    config: server.NoConfig = None,
) -> None:
    """Turn a kill switch off. Broader switches that are on still apply."""
    body = _target(everything, application, connector) | {"reason": reason, "reference": reference}
    typer.echo(f"Off: {_line(_send('/api/kill-switches/release', body, server_url))}")


@kill_app.command("list")
def list_switches(server_url: _Server = None, config: server.NoConfig = None) -> None:
    """List the kill switches that are on."""
    with server.connected(server_url) as api:
        found = [KillSwitch.model_validate(s) for s in server.get(api, "/api/kill-switches")]
    if not found:
        typer.echo("No kill switch is on.")
    for switch in found:
        typer.echo(_line(switch))
