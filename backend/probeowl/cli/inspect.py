# Author: @SaadZarook
"""`owl inspect`: the current state of a subject, from any of its identifiers, through owl serve."""

import re
from typing import Annotated, Any

import httpx
import typer

from probeowl.cli import server
from probeowl.model.probe import ProbeKind
from probeowl.model.run import Reason, Run, RunStatus


def inspect(
    value: Annotated[
        str, typer.Argument(help="Any identifier of the subject, such as C204518 or an email.")
    ],
    env: Annotated[str, typer.Option("--env", help="The environment, such as prod or uat.")],
    reason: Annotated[Reason, typer.Option(help="Why this investigation is run.")],
    reference: Annotated[
        str | None,
        typer.Option(help="Jira key, support case or change ID. Required in Production."),
    ] = None,
    notes: Annotated[str | None, typer.Option(help="Free text: why this record.")] = None,
    identifier: Annotated[
        str | None,
        typer.Option(help="Which identifier the value is. Default: detected from patterns."),
    ] = None,
    application: Annotated[
        str | None, typer.Option(help="Only when several applications could match.")
    ] = None,
    entity: Annotated[
        str | None, typer.Option(help="Only when several entities could match.")
    ] = None,
    probe: Annotated[
        str | None, typer.Option(help="Only when there are several inspect probes.")
    ] = None,
    server_url: Annotated[str | None, typer.Option("--server", help=server.SERVER_HELP)] = None,
    config: server.NoConfig = None,
    timeout: Annotated[float, typer.Option(help="Seconds to follow the run.")] = 120.0,
) -> None:
    """Inspect a subject with an inspect probe, through owl serve.

    Exit codes: 0 passed, 1 failed, 2 not run, 3 Ambiguous (the value matched several
    subjects and nothing else ran).
    """
    with server.connected(server_url) as api:
        app_name, probe_name, root_key = _find_probe(
            api, value, env, identifier, application, entity, probe
        )
        record = server.investigation(reason, reference, notes)
        run_id = server.start_run(api, app_name, probe_name, env, value, record, identifier)
        run = server.follow(api, run_id, timeout)
    _print_resolution(run, root_key)
    server.print_expectations(run)
    server.finish(run)


def _find_probe(
    api: httpx.Client,
    value: str,
    env: str,
    identifier: str | None,
    application: str | None,
    entity: str | None,
    probe: str | None,
) -> tuple[str, str, str]:
    """The one inspect probe for this value: application, probe and its entity's root key."""
    names = (
        [application] if application else [a["name"] for a in server.get(api, "/api/applications")]
    )
    params = {"environment": env} | ({"entity": entity} if entity else {})
    candidates = [
        (name, p)
        for name in names
        for p in server.get(api, f"/api/applications/{name}/probes", params)
        if p["kind"] == ProbeKind.INSPECT.value and probe in (None, p["name"])
    ]
    found = []
    for name, p in candidates:
        definition = server.get(api, f"/api/applications/{name}/entities/{p['entity']}")
        # Only entities the value can be an identifier of: by its name, or by a pattern.
        if _accepts(definition, value, identifier):
            found.append((name, p["name"], str(definition["root_key"])))
    if not found:
        where = "" if not candidates else " whose identifiers accept this value"
        server.stop(f"no inspect probe{where} is enabled in {env}")
    if len(found) > 1:
        listed = ", ".join(f"{a}/{p}" for a, p, _ in found)
        server.stop(
            f"several inspect probes accept this value in {env} ({listed}); "
            "choose one with --probe and --application"
        )
    return found[0]


def _accepts(entity: dict[str, Any], value: str, identifier: str | None) -> bool:
    identifiers: dict[str, dict[str, Any]] = entity["identifiers"]
    if identifier is not None:
        return identifier in identifiers
    patterns = [i.get("pattern") for i in identifiers.values()]
    if any(p is not None and re.fullmatch(p, value) for p in patterns):
        return True
    # A root key without a pattern accepts any value, as the server does.
    return identifiers.get(entity["root_key"], {}).get("pattern") is None


def _print_resolution(run: Run, root_key: str) -> None:
    if run.via is None:
        return
    found = f"{run.identifier} {run.identifier_value}"
    if run.status is RunStatus.AMBIGUOUS:
        typer.echo(f"{found} matches {len(run.matches)} subjects via {run.via}; nothing else ran:")
        for match in run.matches:
            fields = ", ".join(f"{k} {v}" for k, v in match.fields.items() if k != root_key)
            typer.echo(f"  {match.subject}  {fields}")
        typer.echo(
            f"Inspect one with: owl inspect <{root_key}> --env {run.environment} ..., "
            "or pick up to 5 together in the web app"
        )
    elif run.subject is not None:
        typer.echo(f"Resolved {found} -> {root_key} {run.subject} via {run.via}")
    elif run.resolution is not None:
        typer.echo(
            f"{found} via {run.via}: {run.resolution.reason or run.resolution.outcome.value}"
        )
