# Author: @SaadZarook
"""`owl connector add`: a new connector for an application, or bindings for the environments an
existing one lacks.

It reads and writes the config folder itself, as owl init does: owl serve need not run. Every
answer is asked before anything is written. The one file it writes is checked, with the whole
folder, as owl serve would load it, and put back as it was if the folder would not load.
Passwords go to the system keychain only, and only once the file is kept.
"""

import getpass
import io
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from ruamel.yaml import YAML
from sqlalchemy.engine import make_url

from probeowl.cli import connector_setup as setup
from probeowl.cli.defaults import config_path, default_store_url
from probeowl.errors import ConfigError, ProbeOwlError, unknown_application
from probeowl.model.check import LoadedApplication, check_folder, load_applications
from probeowl.model.connector import RestConnector, SqlConnector
from probeowl.model.run import Actor, ActorKind
from probeowl.secrets import check_keychain
from probeowl.store import migrate
from probeowl.store.audit import AuditEvent, append_event
from probeowl.store.engine import create_store_engine
from probeowl.store.tables import LOCAL_WORKSPACE

HEADER = "# Written by owl connector add. Edit freely; `owl check <folder>` checks it.\n"

connector_app = typer.Typer(help="Add a connector to an application, or bindings to one.")


@connector_app.command("add")
def add(
    config: Annotated[
        Path | None,
        typer.Option(
            help="Folder of application YAML. Default: $PROBEOWL_CONFIG, else this folder.",
            show_default=False,
        ),
    ] = None,
    application: Annotated[
        str | None, typer.Option(help="The application, when the folder holds several.")
    ] = None,
) -> None:
    """Add a connector to an application, or bindings to an existing one for missing environments.

    Passwords go to the system keychain, never to a file. Then it offers to test the new
    bindings and import the connector's schema. owl reload loads the change into owl serve.
    """
    run(config_path(config), application, default_store_url())


def run(folder: Path, application: str | None, store_url: str) -> None:
    """Ask, write one file, then offer the connection test and the import."""
    typer.echo(
        "This adds a connector, or bindings to one, and saves passwords in your system keychain, "
        "never in a file."
    )
    check_keychain()  # before any question: without a keychain nothing could be saved
    # Errors are refused here, as at start, except probes waiting for the binding this may add.
    applications = load_applications(folder, allow_unbound=True)
    app = _pick(applications, application)
    name = app.application.application
    existing = sorted(app.connectors)
    typer.echo(f"{name}'s connectors: {', '.join(existing) if existing else 'none yet'}")
    connector = setup.ask_name("Connector id: a new one, or an existing one to bind further", None)
    if connector in app.connectors:
        change = _bind_more(app, connector)
    else:
        change = _new_connector(app, connector)
    saved = _write_audited(folder, store_url, name, connector, change)
    reloaded = load_applications(folder, allow_unbound=True)
    if typer.confirm("Test the new bindings now?", default=True):
        setup.test_bindings(reloaded, name, connector, change.bound)
    import_in = _offer_import(reloaded, app, connector, change, store_url)
    _next_steps(name, connector, change, saved, import_in)
    _still_unbound(folder)


class _Change:
    """The file to write, and what the questions gathered for it."""

    def __init__(
        self, path: Path, before: str | None, after: str, kind: str, bound: list[str]
    ) -> None:
        self.path = path
        self.before = before  # None: a new file
        self.after = after
        self.kind = kind
        self.bound = bound
        self.passwords: dict[str, str] = {}


def _pick(applications: dict[str, LoadedApplication], name: str | None) -> LoadedApplication:
    if not applications:
        raise ConfigError(
            "this folder has no applications: run owl init first, or pass --config <folder>"
        )
    if name is None:
        if len(applications) == 1:
            return next(iter(applications.values()))
        name = setup.ask_choice("Application", sorted(applications))
    if name not in applications:
        raise unknown_application(name, applications)
    return applications[name]


def _environments(app: LoadedApplication) -> dict[str, dict[str, Any]]:
    return {
        env: {"title": spec.title, "production": spec.production}
        for env, spec in app.application.environments.items()
    }


def _ask_bindings(
    app: LoadedApplication,
    document: dict[str, Any],
    environments: list[str],
    previous: dict[str, Any],
    passwords: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """A binding for each environment the person chooses to bind; at least one."""
    specs = _environments(app)
    bindings: dict[str, dict[str, Any]] = {}
    for env in environments:
        if not typer.confirm(f"  Bind {env} ({specs[env]['title']})?", default=True):
            continue
        bindings[env] = setup.ask_binding(
            document, passwords, env, specs[env]["production"], previous
        )
        previous = bindings[env]
    if not bindings:
        raise ConfigError("no environment was bound, so nothing was written")
    return bindings


def _new_connector(app: LoadedApplication, connector: str) -> _Change:
    assert app.folder is not None  # loaded from a folder
    path = app.folder / "connectors" / f"{connector}.yaml"
    if path.exists():
        raise ConfigError(f"{path} already exists; choose another connector id")
    kind = setup.ask_type()
    document: dict[str, Any] = {
        "connector": connector,
        "type": kind,
        "owner": app.application.owner,
    }
    if kind != "rest":
        document["guards"] = {"row_ceiling": 1000}
    passwords: dict[str, str] = {}
    document["environments"] = _ask_bindings(
        app, document, list(app.application.environments), {}, passwords
    )
    (RestConnector if kind == "rest" else SqlConnector).model_validate(document)
    text = HEADER + yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
    change = _Change(path, None, text, kind, list(document["environments"]))
    change.passwords = passwords
    return change


def _bind_more(app: LoadedApplication, connector: str) -> _Change:
    current = app.connectors[connector]
    missing = [env for env in app.application.environments if env not in current.environments]
    if not missing:
        raise ConfigError(
            f"{connector} is bound in every environment of {app.application.application}: "
            f"{', '.join(current.environments)}. Edit its file to change a binding"
        )
    typer.echo(
        f"{connector} ({current.type}) is bound in {', '.join(current.environments) or 'none'}; "
        f"not in {', '.join(missing)}"
    )
    path = _file_of(app, connector)
    before = path.read_text(encoding="utf-8")
    first = next(iter(current.environments.values()), None)
    # The existing binding's answers are the defaults: often only the host or database differs.
    previous = first.model_dump(mode="json", by_alias=True, exclude_none=True) if first else {}
    previous.pop("secret", None)
    document = {"connector": connector, "type": current.type}
    passwords: dict[str, str] = {}
    bindings = _ask_bindings(app, document, missing, previous, passwords)
    change = _Change(path, before, _with_bindings(before, bindings), current.type, list(bindings))
    change.passwords = passwords
    return change


def _file_of(app: LoadedApplication, connector: str) -> Path:
    """The file that defines the connector; ConfigError if it shares the file with others."""
    assert app.folder is not None  # loaded from a folder
    for path in sorted(app.folder.rglob("*.yaml")):
        documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        if any(isinstance(d, dict) and d.get("connector") == connector for d in documents):
            if len(documents) > 1:
                raise ConfigError(f"{path} holds more than {connector}; move it to its own file")
            return path
    raise ConfigError(f"no file in {app.folder} defines connector {connector}")


def _round_trip() -> YAML:
    # As the examples are written: two-space mappings, list items indented under their key.
    editor = YAML()
    editor.width = 100
    editor.indent(mapping=2, sequence=4, offset=2)
    return editor


def _with_bindings(text: str, bindings: dict[str, dict[str, Any]]) -> str:
    """The connector's file with the new bindings added, its comments and order kept."""
    editor = _round_trip()
    document = editor.load(text)
    for env, binding in bindings.items():
        document["environments"][env] = binding
    out = io.StringIO()
    editor.dump(document, out)
    return out.getvalue()


def _write(folder: Path, change: _Change) -> None:
    """Write the file, then keep it only if the whole folder still loads."""
    change.path.parent.mkdir(parents=True, exist_ok=True)
    change.path.write_text(change.after, encoding="utf-8", newline="\n")
    try:
        load_applications(folder, allow_unbound=True)
    except ProbeOwlError as err:
        _put_back(change)
        raise ConfigError(
            f"the folder would not load with this change, so it was undone: {err}"
        ) from err


def _usable_store(store_url: str) -> str | None:
    """The store, if it exists and is at this release; None says why not."""
    parsed = make_url(store_url)
    sqlite_file = parsed.database if parsed.get_backend_name() == "sqlite" else None
    if sqlite_file and not Path(sqlite_file).exists():
        return None
    engine = create_store_engine(store_url)
    try:
        migrate.check_current(engine)
    except ConfigError:
        return None
    finally:
        engine.dispose()
    return store_url


def _put_back(change: _Change) -> None:
    """The file as it was before the change: removed if the change created it."""
    if change.before is None:
        change.path.unlink(missing_ok=True)
    else:
        change.path.write_text(change.before, encoding="utf-8", newline="\n")


def _write_audited(
    folder: Path, store_url: str, application: str, connector: str, change: _Change
) -> list[str]:
    """Write the file and save the passwords; kept only once their audit event commits (#273).

    The event is written first, in a transaction that commits last: if the file, a password or
    the commit fails, the file is put back and nothing is recorded. The keychain entries names.
    """
    store = _usable_store(store_url)
    if store is None:
        typer.echo("Not recorded in the audit: no store at this release yet (owl store init).")
        _write(folder, change)
        return setup.save_passwords(connector, change.passwords)
    engine = create_store_engine(store)
    written = False
    try:
        with engine.begin() as conn:
            append_event(conn, LOCAL_WORKSPACE, _event(application, connector, change))
            _write(folder, change)  # puts the file back itself if the folder would not load
            written = True
            return setup.save_passwords(connector, change.passwords)
    except BaseException:
        if written:
            _put_back(change)
        raise
    finally:
        engine.dispose()


def _event(application: str, connector: str, change: _Change) -> AuditEvent:
    return AuditEvent(
        action="connector_added",
        actor=Actor(kind=ActorKind.PERSON, name=getpass.getuser()),
        application=application,
        details={
            "connector": connector,
            "environments": list(change.bound),
            "new": change.before is None,
            "file": change.path.name,
        },
    )


def _offer_import(
    applications: dict[str, LoadedApplication],
    app: LoadedApplication,
    connector: str,
    change: _Change,
    store_url: str,
) -> str | None:
    """Offer the import; the environment still to import if none ran (None for REST)."""
    if change.kind == "rest":
        return None
    environment = setup.import_environment(_environments(app), change.bound)
    store = _usable_store(store_url)
    if store is None:
        return environment
    title = app.application.environments[environment].title
    setup.explain_import(connector, title)
    if not typer.confirm(f"Import {connector}'s schema in {title} now?", default=True):
        return environment
    name = app.application.application
    imported = setup.import_schema(applications, name, connector, environment, store)
    return None if imported else environment


def _next_steps(
    application: str, connector: str, change: _Change, saved: list[str], import_in: str | None
) -> None:
    written = "Written" if change.before is None else "Added to"
    typer.echo(f"\n{written} {change.path}")
    if saved:
        typer.echo(f"Passwords are in your keychain: {', '.join(saved)}")
    steps = [
        f"Load {connector} into owl serve: owl reload, "
        "or Reload configuration on the Connectors screen"
    ]
    if import_in is not None:
        steps.append(setup.import_later(application, connector, import_in))
    if change.kind != "rest":
        steps.append(f"Tables: confirm the labels suggested for {connector}")
    typer.echo("Next:")
    for number, step in enumerate(steps, start=1):
        typer.echo(f"  {number}. {step}")


def _still_unbound(folder: Path) -> None:
    """Probes still enabled where a connector has no binding: owl serve won't start until fixed."""
    waiting = [f for f in check_folder(folder).findings if f.unbound]
    if waiting:
        typer.echo("\nowl serve won't start until these are bound too (run this again for each):")
        for finding in waiting:
            typer.echo(f"  {finding.message}")
