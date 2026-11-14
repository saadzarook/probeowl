# Author: @SaadZarook
"""`owl mcp`: ProbeOwl's drafting tools for an AI assistant, over the Model Context Protocol (#39).

stdio only: an MCP client starts it and talks on its stdin and stdout. It opens no port.
"""

import contextlib
import sys
from pathlib import Path
from typing import Annotated

import anyio
import typer
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from probeowl.checks.plans import open_for_plans
from probeowl.cli.defaults import (
    CONFIG_VARIABLE,
    STORE_URL_VARIABLE,
    choose_config_folder,
    default_store_url,
    ensure_default_store,
)
from probeowl.drafting.server import create_server, serve_stdio
from probeowl.errors import ConfigError
from probeowl.model.check import load_applications
from probeowl.server_log import configure_server_log
from probeowl.store import migrate
from probeowl.store.engine import create_store_engine

INLINE_PASSWORD = (
    "the store URL contains a password; keep the password in the keychain or "
    "PROBEOWL_STORE_URL, not in the assistant's config"
)
_PASSWORD_PARAMETERS = {"password", "pwd", "passwd"}


def has_password(url: str) -> bool:
    """Whether a store URL carries a password, as user:password@ or as a query parameter."""
    try:
        parsed = make_url(url)
    except ArgumentError:
        return "@" in url  # unreadable, with credentials of some kind: treated as holding one
    if parsed.password:
        return True
    return any(key.lower() in _PASSWORD_PARAMETERS for key in parsed.query)


def mcp(
    config: Annotated[
        Path | None,
        typer.Option(
            help=(
                f"Folder of application YAML. Default: ${CONFIG_VARIABLE}, else this folder if it "
                "holds applications, else ./probeowl"
            ),
            show_default=False,
        ),
    ] = None,
    url: Annotated[
        str | None,
        typer.Option(
            help=(
                f"SQLAlchemy URL of the store. Default: ${STORE_URL_VARIABLE}, else "
                "~/.probeowl/store.db. Use the variable for a URL with a password."
            )
        ),
    ] = None,
    plan_checks: Annotated[
        bool,
        typer.Option(
            "--plan-checks",
            help=(
                "Let check_draft read lookups' plans: it connects through the bindings' guards "
                "and reads no data. Off by default: the tools reach no database."
            ),
        ),
    ] = False,
) -> None:
    """Serve the drafting tools to an AI assistant on stdio: they draft, you paste.

    Nothing it does writes a file, saves configuration, starts a run or returns a credential,
    host, URL, login, row or run subject. Each tool call is audited as you, via MCP.
    """
    if url is not None and has_password(url):
        # The URL sits in the assistant's config file: never with a password, never echoed.
        raise ConfigError(INLINE_PASSWORD)
    configure_server_log(Path.home() / ".probeowl" / "owl-mcp.log")
    # stdout carries the protocol and nothing else: what setting up says goes to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        folder = choose_config_folder(config).folder
        applications = load_applications(folder) if folder.exists() else {}
        ensure_default_store(url)
        store = create_store_engine(url or default_store_url())
        migrate.check_current(store)
    server = create_server(
        applications, store, str(folder), plans=open_for_plans if plan_checks else None
    )
    anyio.run(serve_stdio, server)
