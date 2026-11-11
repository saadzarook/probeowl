# Author: @SaadZarook
"""What `owl validate` and `owl inspect` share: owl serve's API, a run followed to its end.

The CLI is a client of the API, so a run from the command line is checked, limited, stored
and audited exactly like one from the web app.
"""

import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import httpx
import typer

from probeowl.api.local_only import HEADER
from probeowl.model.run import LocationResult, Reason, Run, RunStatus

SERVER_VARIABLE = "PROBEOWL_SERVER"
DEFAULT_SERVER = "http://127.0.0.1:7878"
SERVER_HELP = f"owl serve's URL. Default: ${SERVER_VARIABLE}, else {DEFAULT_SERVER}."


def _no_config(ctx: typer.Context, value: str | None) -> None:
    # People reach for --config because owl serve and owl check take one; say where it goes.
    if value is not None:
        stop(
            f"{ctx.command_path} takes no --config: it runs through owl serve, which loads the "
            f"config folder. Start it with: owl serve --config {value}, then run this again "
            f"(--server or ${SERVER_VARIABLE} when owl serve is not on {DEFAULT_SERVER})"
        )


NoConfig = Annotated[
    str | None,
    typer.Option("--config", hidden=True, is_eager=True, callback=_no_config, expose_value=False),
]
"""A hidden --config on each command that runs through owl serve: it stops with where it goes."""
POLL_S = 0.5

# Exit codes: the run passed, the run failed, the run could not be started or followed, or
# the value matched several subjects so nothing ran (owl inspect).
PASSED, FAILED, NOT_RUN, AMBIGUOUS = 0, 1, 2, 3


def client(server: str) -> httpx.Client:
    """The HTTP client for owl serve; tests replace it with the API's own test client."""
    # The header owl serve requires on every request that changes something (local_only.py).
    return httpx.Client(base_url=server, timeout=10, headers={HEADER: "1"})


@contextmanager
def connected(server: str | None) -> Iterator[httpx.Client]:
    """owl serve's API; an unreachable server stops the command with how to start it."""
    base = server or os.environ.get(SERVER_VARIABLE) or DEFAULT_SERVER
    with client(base) as api:
        try:
            yield api
        except httpx.TransportError as err:
            stop(
                f"cannot reach owl serve at {base} ({type(err).__name__}); start it with: "
                "owl serve --config <folder>"
            )


def start_run(
    api: httpx.Client,
    application: str,
    probe: str,
    env: str,
    value: str,
    investigation: dict[str, str | None],
    identifier: str | None = None,
) -> str:
    """Start a run; its id. Stops with the problem's detail and field errors if refused."""
    body: dict[str, Any] = {
        "application": application,
        "environment": env,
        "probe": probe,
        "value": value,
        "investigation": {k: v for k, v in investigation.items() if v is not None},
    }
    if identifier is not None:
        body["identifier"] = identifier
    response = api.post("/api/runs", json=body)
    if response.status_code != 202:
        problem(response)
    return str(response.json()["id"])


def investigation(reason: Reason, reference: str | None, notes: str | None) -> dict[str, Any]:
    """The investigation record a person's run carries."""
    return {"reason": reason.value, "reference": reference, "notes": notes}


def follow(api: httpx.Client, run_id: str, timeout: float) -> Run:
    """Poll a run until it is no longer running, printing each location as it is read."""
    shown: set[tuple[str, str | None, int | None]] = set()
    deadline = time.monotonic() + timeout
    run = Run.model_validate(get(api, f"/api/runs/{run_id}"))
    # The value as stored: masked when the identifier is PII, so never echoed in full.
    typer.echo(
        f"Run {run.id}: {run.probe_title} ({run.application}/{run.probe}) "
        f"on {run.identifier_value} in {run.environment}"
    )
    while True:
        for location in run.locations:
            key = (location.location, location.parent, location.row)
            if key not in shown:
                shown.add(key)
                typer.echo(_location_line(location))
        if run.status is not RunStatus.RUNNING:
            return run
        if time.monotonic() > deadline:
            stop(f"run {run_id} is still running after {timeout:g} s; see GET /api/runs/{run_id}")
        time.sleep(POLL_S)
        run = Run.model_validate(get(api, f"/api/runs/{run_id}"))


def _location_line(location: LocationResult) -> str:
    label = location.outcome.value.replace("_", " ").capitalize()
    if location.caveat:
        label = f"{label} - {location.caveat}"
    reason = f"  {location.reason}" if location.reason else ""
    name = location.location
    if location.row is not None:
        # A fan-out lookup: one per row of its parent, numbered from 1 as people count.
        name = f"{name}[{location.parent} {location.row + 1}]"
    return f"  {name:<16} {label}{reason}"


def print_expectations(run: Run) -> None:
    """One line per expectation: its result, its text and, when not passed, the detail."""
    for e in run.expectations:
        detail = f"  ({e.detail})" if e.detail else ""
        caveat = f"  - {e.caveat}" if e.caveat else ""
        typer.echo(f"  {e.result.value:<4} {e.expectation}{detail}{caveat}")


def finish(run: Run) -> None:
    """Print the run's warnings and status, and exit with its code."""
    for warning in run.warnings:
        typer.echo(f"warning: {warning}")
    why = run.stop_reason or run.skip_reason
    caveat = f" - {run.caveat}" if run.caveat else ""
    typer.echo(f"Run {run.id}: {run.status.value}{caveat}" + (f" ({why})" if why else ""))
    codes = {RunStatus.PASSED: PASSED, RunStatus.AMBIGUOUS: AMBIGUOUS}
    raise typer.Exit(codes.get(run.status, FAILED))


def get(api: httpx.Client, path: str, params: dict[str, str] | None = None) -> Any:
    """GET from owl serve; stops with the problem if it is not a 200."""
    response = api.get(path, params=params)
    if response.status_code != 200:
        problem(response)
    return response.json()


def problem(response: httpx.Response) -> None:
    """Stop with an RFC 9457 problem's detail and its field errors."""
    try:
        body = response.json()
    except ValueError:
        stop(f"owl serve answered HTTP {response.status_code}")
    lines = [str(body.get("detail") or body.get("title") or response.status_code)]
    lines += [f"  {e['field']}: {e['message']}" for e in body.get("errors") or []]
    stop("\n".join(lines))


def stop(message: str) -> Any:
    """Print an error and exit: the run was not started or could not be followed."""
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(NOT_RUN)
