# Author: @SaadZarook
"""`owl validate`: start a validate run through owl serve and follow it to the end."""

from typing import Annotated

import httpx
import typer

from probeowl.cli import server
from probeowl.model.probe import ProbeKind
from probeowl.model.run import Reason


def validate(
    entity: Annotated[str, typer.Argument(help="The entity, such as topup.")],
    value: Annotated[
        str, typer.Argument(help="Any identifier of the subject, such as tp_91f2c7 or pay_7hq2m9.")
    ],
    env: Annotated[str, typer.Option("--env", help="The environment, such as prod or uat.")],
    reason: Annotated[Reason, typer.Option(help="Why this investigation is run.")],
    reference: Annotated[
        str | None,
        typer.Option(help="Jira key, support case or change ID. Required in Production."),
    ] = None,
    notes: Annotated[str | None, typer.Option(help="Free text: why this record.")] = None,
    application: Annotated[
        str | None, typer.Option(help="Only when the entity is in several applications.")
    ] = None,
    probe: Annotated[
        str | None, typer.Option(help="Only when the entity has several validate probes.")
    ] = None,
    server_url: Annotated[str | None, typer.Option("--server", help=server.SERVER_HELP)] = None,
    config: server.NoConfig = None,
    timeout: Annotated[float, typer.Option(help="Seconds to follow the run.")] = 120.0,
) -> None:
    """Validate one subject with its entity's validate probe, through owl serve."""
    with server.connected(server_url) as api:
        app_name, probe_name = _find_probe(api, entity, env, application, probe)
        record = server.investigation(reason, reference, notes)
        run_id = server.start_run(api, app_name, probe_name, env, value, record)
        run = server.follow(api, run_id, timeout)
    server.print_expectations(run)
    server.finish(run)


def _find_probe(
    api: httpx.Client, entity: str, env: str, application: str | None, probe: str | None
) -> tuple[str, str]:
    names = (
        [application] if application else [a["name"] for a in server.get(api, "/api/applications")]
    )
    found = [
        (name, p["name"])
        for name in names
        for p in server.get(
            api, f"/api/applications/{name}/probes", {"entity": entity, "environment": env}
        )
        if p["kind"] == ProbeKind.VALIDATE.value and probe in (None, p["name"])
    ]
    if not found:
        server.stop(f"no validate probe for {entity} is enabled in {env}")
    if len(found) > 1:
        listed = ", ".join(f"{a}/{p}" for a, p in found)
        server.stop(
            f"{entity} has several validate probes in {env} ({listed}); "
            "choose one with --probe and --application"
        )
    return found[0]
