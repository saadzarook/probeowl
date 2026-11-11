# Author: @SaadZarook
"""owl runbook: what a reviewer needs to mark a read query as reviewed."""

from pathlib import Path
from typing import Annotated

import typer

from probeowl.model.load import load_file
from probeowl.model.runbook import ReadQueryStep, Runbook, sql_sha256

runbook_app = typer.Typer(help="Work with runbooks.")


@runbook_app.command("hash")
def hash_steps(file: Annotated[Path, typer.Argument(help="A runbook's YAML file.")]) -> None:
    """Print each read query's sql_sha256, for its reviewed block."""
    runbooks = [document for document in load_file(file) if isinstance(document, Runbook)]
    if not runbooks:
        typer.echo(f"error: {file} is not a runbook", err=True)
        raise typer.Exit(1)
    for runbook in runbooks:
        for step in runbook.steps:
            if isinstance(step, ReadQueryStep):
                typer.echo(f"{sql_sha256(step.sql)}  {step.title}")
