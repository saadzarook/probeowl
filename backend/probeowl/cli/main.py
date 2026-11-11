# Author: @SaadZarook
"""The `owl` command."""

import os
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from sqlalchemy.engine import make_url

from probeowl.api.app import DEFAULT_PORT, create_app
from probeowl.api.guide_site import find_guide
from probeowl.api.web_app import find_web_app
from probeowl.catalog.service import CatalogService
from probeowl.checks.rules import folder_failures
from probeowl.checks.saving import ProbeWrites
from probeowl.checks.time_offsets import unchecked_without_imports
from probeowl.cli import init as init_wizard
from probeowl.cli.catalog import catalog_app
from probeowl.cli.connector import connector_app
from probeowl.cli.defaults import (
    CONFIG_VARIABLE,
    STORE_URL_VARIABLE,
    config_path,
    default_store_url,
)
from probeowl.cli.inspect import inspect
from probeowl.cli.kill import kill_app
from probeowl.cli.reload import reload as reload_config
from probeowl.cli.runbook import runbook_app
from probeowl.cli.validate import validate
from probeowl.config_folder import ConfigFolder
from probeowl.connector_status import ConnectorStatuses, unreadable_plans
from probeowl.connectors.failures import show_raw_driver_errors
from probeowl.errors import ConfigError, ProbeOwlError
from probeowl.guards.breaker import CircuitBreaker
from probeowl.guards.kill_switch import KillSwitches
from probeowl.model.check import check_folder, load_applications
from probeowl.plugins.registry import (
    default_settings_file,
    load_plugins,
    plugins_off,
    unknown_plugins_message,
)
from probeowl.reload import ConfigReloader
from probeowl.runs import RunService
from probeowl.server_log import configure_server_log, default_log_file
from probeowl.store import migrate
from probeowl.store.engine import create_store_engine

HOST = "127.0.0.1"  # never anything else by default; see AGENTS.md
WEB_ORIGINS_VARIABLE = "PROBEOWL_WEB_ORIGINS"

app = typer.Typer(
    help=(
        "ProbeOwl: validate production data across databases and APIs, safely.\n\n"
        "owl serve loads the config folder and runs every probe. validate, inspect, catalog, "
        "kill and reload are its clients: they take no --config, so start "
        "owl serve --config <folder> first. "
        "They reach it at $PROBEOWL_SERVER, else http://127.0.0.1:7878 (--server to change). "
        "check, store and connector add read the folder and the store themselves. "
        "New here? owl init writes a first application and connector, step by step."
    )
)
store_app = typer.Typer(help="Create and inspect ProbeOwl's own store.")
app.add_typer(store_app, name="store")
app.add_typer(catalog_app, name="catalog")
app.add_typer(connector_app, name="connector")
app.add_typer(kill_app, name="kill")
app.add_typer(runbook_app, name="runbook")
app.command("reload")(reload_config)
app.command()(validate)
app.command()(inspect)


def _show_version(show: bool) -> None:
    if show:
        typer.echo(f"owl {version('probeowl')}")
        raise typer.Exit()


@app.callback()
def _options(
    show_version: Annotated[
        bool,
        typer.Option(
            "--version", is_eager=True, callback=_show_version, help="Show the version and exit."
        ),
    ] = False,
) -> None:
    """ProbeOwl: validate production data across databases and APIs, safely."""


def counted(n: int, word: str) -> str:
    """'1 application', '2 applications'."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


StoreUrl = Annotated[
    str | None,
    typer.Option(
        "--url",
        help=(
            f"SQLAlchemy URL of the store. Default: ${STORE_URL_VARIABLE}, else "
            "~/.probeowl/store.db. Use the variable for a URL with a password."
        ),
        show_default=False,
    ),
]


@app.command()
def serve(
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port on 127.0.0.1.")] = DEFAULT_PORT,
    config: Annotated[
        Path | None,
        typer.Option(
            help=f"Folder of application YAML. Default: ${CONFIG_VARIABLE}, else this folder.",
            show_default=False,
        ),
    ] = None,
    url: StoreUrl = None,
    web_origin: Annotated[
        list[str] | None,
        typer.Option(
            help=(
                "Where else the web app is served from, such as http://127.0.0.1:5180. "
                "Vite on 5173 and 4173 is always accepted. Loopback only; repeat for several. "
                f"Default: ${WEB_ORIGINS_VARIABLE}, comma-separated."
            ),
            show_default=False,
        ),
    ] = None,
    log_file: Annotated[
        Path | None,
        typer.Option(
            help="The log file, readable only by you. Default: ~/.probeowl/owl-serve.log."
        ),
    ] = None,
    debug_driver_errors: Annotated[
        bool,
        typer.Option(
            "--debug-driver-errors",
            help="Log drivers' raw error text, which can hold values from statements and PII.",
        ),
    ] = False,
) -> None:
    """Serve ProbeOwl on 127.0.0.1: the web app at /, its API under /api."""
    configure_server_log(log_file or default_log_file())
    show_raw_driver_errors(debug_driver_errors)
    if debug_driver_errors:
        typer.echo(
            "warning: --debug-driver-errors is on: the log gets drivers' raw error text, "
            "which can hold values from statements, PII included",
            err=True,
        )
    # Before anything starts: a settings file it cannot read refuses here, with what is wrong.
    disabled = plugins_off(default_settings_file())
    plugins = load_plugins(disabled)
    unknown = unknown_plugins_message(disabled)
    if unknown is not None:
        typer.echo(f"warning: {unknown}", err=True)
    folder = config_path(config)
    origins = web_origin or os.environ.get(WEB_ORIGINS_VARIABLE, "").split(",")
    applications = load_applications(folder)
    store = create_store_engine(url or default_store_url())
    migrate.check_current(store)
    # One breaker: the Connectors screen shows the pauses runs caused.
    breaker = CircuitBreaker()
    runs = RunService(applications, store, breaker=breaker)
    interrupted = runs.recover_interrupted()
    if interrupted:
        typer.echo(f"{len(interrupted)} runs were left running and are now failed: interrupted")
    # One folder object: its lock covers the catalog's writes and the probes' together.
    config_folder = ConfigFolder(folder)
    build = find_web_app()
    typer.echo(f"ProbeOwl on http://{HOST}:{port} (API docs at /docs, contract at /openapi.json)")
    if build is None:
        typer.echo("The web app is not built here: cd web && npm run build, or npm run dev")
    guide = find_guide()
    if guide is None:
        typer.echo("The guide is not built here: cd backend && uv run python guide_build.py")
    try:
        uvicorn.run(
            create_app(
                runs,
                CatalogService(applications, store, folder=config_folder),
                port=port,
                web_origins=origins,
                probes=ProbeWrites(applications, store, config_folder),
                connectors=ConnectorStatuses(applications, breaker, runs.kill.reason),
                plugins=plugins,
                web_app=True,
                web_build=build,
                guide_build=guide,
                reloader=ConfigReloader(folder, config_folder, runs),
            ),
            host=HOST,
            port=port,
        )
    finally:
        runs.shutdown()
        store.dispose()


def _plans_may_be_read() -> bool:
    # No store yet means no switch can be on, but asking would create an empty file here.
    url = make_url(default_store_url())
    if url.get_backend_name() == "sqlite" and url.database and not Path(url.database).exists():
        typer.echo("Plan access not checked: no store yet; run: owl store init", err=True)
        return False
    return True


@app.command()
def check(
    folder: Annotated[Path, typer.Argument(help="Folder of application YAML to check.")],
    no_connect: Annotated[
        bool,
        typer.Option(
            "--no-connect", help="Do not ask Production bindings whether they can read plans."
        ),
    ] = False,
) -> None:
    """Check every YAML file in a folder, the references between them, and every lookup.

    Lookups get each save check that needs no database: the guard, the key and time-filter
    rules, and PII masking. Plan checks need a database and stay at save time. Production SQL
    bindings whose login cannot read plans are warnings, naming the grant.
    """
    report = check_folder(folder)
    for finding in report.findings:
        typer.echo(f"error: {finding}", err=True)
    summary = f"{counted(report.files, 'file')} in {counted(report.applications, 'application')}"
    if report.findings:
        typer.echo(f"{counted(len(report.findings), 'error')} in {summary}", err=True)
        raise typer.Exit(1)
    applications = load_applications(folder)
    failures = [failure for app in applications.values() for failure in folder_failures(app)]
    for failure in failures:
        typer.echo(f"error: {failure}", err=True)
    if failures:
        typer.echo(f"{counted(len(failures), 'error')} in {summary}", err=True)
        raise typer.Exit(1)
    typer.echo(f"OK: {summary}")
    for name, loaded in applications.items():
        # owl check reads no schema imports, so the time offset check is left to saving.
        for connector in unchecked_without_imports(loaded):
            typer.echo(
                f"warning: {name}: time-offset check not run: no schema import for {connector}",
                err=True,
            )
    if not no_connect and _plans_may_be_read():
        store = create_store_engine(default_store_url())
        statuses = ConnectorStatuses(applications, CircuitBreaker(), KillSwitches(store).reason)
        for name, loaded in applications.items():
            for warning in unreadable_plans(statuses, name, loaded):
                typer.echo(f"warning: {warning}", err=True)


@app.command()
def init() -> None:
    """Write a config folder for a first application and connector, step by step.

    Passwords go to the system keychain, never to a file. It creates the store first, so the
    setup is audited; then it offers to test the connections and import the connector's schema.
    """
    init_wizard.run(default_store_url(), _migrate)


@store_app.command("init")
def store_init(url: StoreUrl = None) -> None:
    """Create ProbeOwl's tables in the store, or upgrade them to this release."""
    _migrate(url or default_store_url())


@store_app.command("upgrade")
def store_upgrade(url: StoreUrl = None) -> None:
    """Upgrade the store's tables to this release."""
    _migrate(url or default_store_url())


@store_app.command("ddl")
def store_ddl(
    dialect: Annotated[
        str, typer.Option(help=f"One of: {', '.join(migrate.DIALECTS)}.", show_default=False)
    ],
) -> None:
    """Print the SQL that creates the store, for a DBA to run."""
    typer.echo(migrate.ddl(dialect), nl=False)


def _migrate(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database:
        _prepare_sqlite_file(Path(parsed.database))
    engine = create_store_engine(url)
    try:
        migrate.upgrade(engine)
    finally:
        engine.dispose()
    typer.echo(f"Store ready: {parsed.render_as_string(hide_password=True)}")


def _prepare_sqlite_file(path: Path) -> None:
    # SQLite on a network share loses writes when several people use it; a team store
    # belongs in a server database.
    if str(path).startswith(("\\\\", "//")):
        raise ConfigError(
            f"{path} is on a network share; use a server database for a team store "
            f"(set {STORE_URL_VARIABLE})"
        )
    path.parent.mkdir(parents=True, exist_ok=True)


def main() -> None:
    """Run `owl`, turning ProbeOwl errors into a message and exit code 1."""
    try:
        app()
    except ProbeOwlError as err:
        typer.echo(f"error: {err}", err=True)
        raise SystemExit(1) from err
