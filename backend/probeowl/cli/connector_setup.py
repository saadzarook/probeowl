# Author: @SaadZarook
"""One connector's questions and setup, shared by owl init and owl connector add.

The questions build the connector's YAML document and collect its passwords, which go to the
system keychain only; the document names them by reference. Nothing here writes a file.
"""

import re
from typing import Any

import typer

from probeowl import secrets
from probeowl.catalog.service import CatalogService
from probeowl.connector_files import KEYCHAIN_SERVICE, keychain_reference
from probeowl.connector_status import ConnectorStatuses
from probeowl.errors import ConfigError, ProbeOwlError
from probeowl.guards.breaker import CircuitBreaker
from probeowl.model.check import LoadedApplication
from probeowl.secrets import save_secret
from probeowl.store.engine import create_store_engine

TYPES = ("sqlserver", "oracle", "mariadb", "rest")
DEFAULT_PORTS = {"sqlserver": 1433, "oracle": 1521, "mariadb": 3306}
NAME = re.compile(r"^[a-z][a-z0-9_]*$")
NOT_A_REPLICA = (
    "ProbeOwl reads Production only from a read replica, readable secondary or Active Data "
    "Guard standby, never from the primary, so a probe can never slow or block it. Ask your "
    "DBA for one, then run this again. Nothing was written."
)


def ask_name(text: str, default: str | None) -> str:
    """A name for a file and for lookups: a-z, 0-9 and _, asked again until it is one."""
    while True:
        value = str(typer.prompt(text, default=default)).strip()
        if NAME.match(value) and len(value) <= 64:
            return value
        typer.echo("  Use a-z, 0-9 and _, starting with a letter: it names files and lookups.")


def ask_text(text: str, default: str | None = None) -> str:
    """Any text that isn't empty."""
    while True:
        value = str(typer.prompt(text, default=default)).strip()
        if value:
            return value


def ask_choice(text: str, choices: list[str], default: str | None = None) -> str:
    """One of choices, asked again until it is one; Enter alone takes the default.

    A plain prompt checked here: Typer 0.29 carries its own copy of click and refuses the click
    package's types, such as click.Choice, so the CLI passes no click type to Typer.
    """
    shown = f"{text} ({', '.join(choices)})"
    while True:
        value = str(typer.prompt(shown, default=default)).strip()
        if value in choices:
            return value
        typer.echo(f"  Choose one of: {', '.join(choices)}.")


def ask_type() -> str:
    """sqlserver, oracle, mariadb or rest."""
    return ask_choice("Type", list(TYPES), "sqlserver")


def ask_binding(
    connector: dict[str, Any],
    passwords: dict[str, str],
    env: str,
    production: bool,
    previous: dict[str, Any],
) -> dict[str, Any]:
    """One environment's binding; its password, if any, goes into passwords[env]."""
    if connector["type"] == "rest":
        binding, password = _ask_rest(connector, passwords, env, previous)
    else:
        binding, password = _ask_sql(connector, passwords, env, production, previous)
    if password is not None:
        passwords[env] = password
    return binding


def _ask_sql(
    connector: dict[str, Any],
    passwords: dict[str, str],
    env: str,
    production: bool,
    previous: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    kind = connector["type"]
    name = connector["connector"]
    binding: dict[str, Any] = {
        "host": ask_text("    Host", previous.get("host", "127.0.0.1")),
        "port": typer.prompt(
            "    Port", default=previous.get("port", DEFAULT_PORTS[kind]), type=int
        ),
    }
    if kind == "oracle":
        binding["database"] = ask_text("    Service name", previous.get("database", "FREEPDB1"))
        binding["schema"] = ask_text("    Schema", previous.get("schema"))
    else:
        # wallet_db reads the wallet database; other names suggest nothing.
        guess = name.removesuffix("_db") if name.endswith("_db") else None
        binding["database"] = ask_text("    Database", previous.get("database", guess))
    binding["user"] = ask_text("    Read-only login", previous.get("user"))
    binding["secret"] = keychain_reference(name, env)
    password = _ask_password(name, passwords, env)
    replica = typer.confirm(
        "    Is this a read replica, readable secondary or Active Data Guard standby?",
        default=bool(previous.get("replica", False)),
    )
    if production and not replica:
        raise ConfigError(NOT_A_REPLICA)
    if replica:
        binding["replica"] = True
    if kind != "sqlserver" and not typer.confirm(
        "    Encrypt the connection with TLS?", default=previous.get("encrypt", True)
    ):
        binding["encrypt"] = False
        binding["encrypt_reason"] = ask_text("    Why not?", previous.get("encrypt_reason"))
    elif typer.confirm(
        "    Trust the server certificate without checking it?",
        default=bool(previous.get("trust_server_certificate", False)),
    ):
        binding["trust_server_certificate"] = True
        binding["trust_server_certificate_reason"] = ask_text(
            "    Why?", previous.get("trust_server_certificate_reason")
        )
    return binding, password


def _ask_rest(
    connector: dict[str, Any], passwords: dict[str, str], env: str, previous: dict[str, Any]
) -> tuple[dict[str, Any], str | None]:
    while True:
        url = ask_text("    Base URL", previous.get("base_url"))
        if url.startswith(("http://", "https://")):
            break
        typer.echo("    Start it with https:// (or http:// for a local service).")
    binding: dict[str, Any] = {"base_url": url}
    if not typer.confirm("    Does it need a token?", default="secret" in previous):
        return binding, None
    name = connector["connector"]
    binding["secret"] = keychain_reference(name, env)
    return binding, _ask_password(name, passwords, env, what="Token")


def _ask_password(
    name: str, passwords: dict[str, str], env: str, what: str = "Password"
) -> str | None:
    """The password to save for this binding; None to keep the one saved under its name."""
    where = f"{KEYCHAIN_SERVICE}/{name}.{env}"
    # Another application's connector of the same id shares this name: never overwrite unasked.
    # Looked up at call time, so tests can stand in for the keychain.
    if secrets.read_keychain(KEYCHAIN_SERVICE, f"{name}.{env}") is not None and not typer.confirm(
        f"    A {what.lower()} is already saved as {where}, perhaps for another application's "
        f"connector named {name}. Overwrite it?",
        default=False,
    ):
        typer.echo(f"    Kept: {where} stays as it is, and this binding uses it.")
        return None
    earlier = next(iter(passwords.values()), None)
    hint = "; Enter for the same as before" if earlier is not None else ""
    while True:
        value = str(
            typer.prompt(
                f"    {what} (hidden; saved to the keychain as {where}{hint})",
                default="",
                hide_input=True,
                show_default=False,
            )
        )
        if value:
            return value
        if earlier is not None:
            return earlier


def save_passwords(connector: str, passwords: dict[str, str]) -> list[str]:
    """Save each environment's password in the keychain; the entries' names."""
    saved = []
    for env, password in passwords.items():
        username = f"{connector}.{env}"
        save_secret(KEYCHAIN_SERVICE, username, password)
        saved.append(f"{KEYCHAIN_SERVICE}/{username}")
    return saved


def test_bindings(
    applications: dict[str, LoadedApplication],
    application: str,
    connector: str,
    environments: list[str],
) -> None:
    """Connect once to each binding and say how it went; the setup carries on either way."""
    # A kill switch is not checked here: the store may not exist yet.
    statuses = ConnectorStatuses(applications, CircuitBreaker(), lambda app, connector: None)
    for env in environments:
        test = statuses.test_connection(application, connector, env)
        if test.reachable:
            typer.echo(f"  {env}: reached in {test.duration_ms} ms")
        else:
            typer.echo(f"  {env}: not reached: {test.reason}")


def import_environment(environments: dict[str, Any], bound: list[str]) -> str:
    """Where to import first: a bound environment that isn't Production, if there is one."""
    found = (name for name in bound if not environments[name]["production"])
    return next(found, bound[0])


def import_schema(
    applications: dict[str, LoadedApplication],
    application: str,
    connector: str,
    environment: str,
    store: str,
) -> bool:
    """Import the connector's schema into the store; whether it was imported."""
    engine = create_store_engine(store)
    try:
        catalog = CatalogService(applications, engine)
        snapshot = catalog.run(application, connector, environment)
        suggested = catalog.review(application).unconfirmed
        typer.echo(
            f"  Imported {connector} in {environment}: "
            f"{len(snapshot.tables)} tables, {suggested} labels suggested; "
            "confirm them on the Tables screen"
        )
        return True
    except ProbeOwlError as err:
        typer.echo(f"  Not imported: {err}")
        return False
    finally:
        engine.dispose()


def explain_import(connector: str, title: str) -> None:
    """What the import is for, before it is offered."""
    typer.echo(
        f"The import reads {connector}'s tables, columns and indexes in {title}, never data, "
        "so the Tables screen can list them with a suggested label for each."
    )


def import_later(application: str, connector: str, environment: str) -> str:
    """The next step when no import ran."""
    return (
        f"Import {connector}'s schema: Import from replica on the Tables screen, or "
        f"owl catalog import {connector} --application {application} --env {environment} "
        "while owl serve runs"
    )
