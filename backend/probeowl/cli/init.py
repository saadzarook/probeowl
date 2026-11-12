# Author: @SaadZarook
"""`owl init`: a config folder for a first application and connector, step by step.

Every answer is asked before anything is written, so stopping part-way leaves nothing behind.
The store comes first: with it, the folder and the passwords are kept only once their audit event
commits (#276). Passwords go to the system keychain only; the files name them by reference. The
connector's questions are shared with owl connector add (connector_setup.py).
"""

import getpass
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
import yaml
from sqlalchemy.engine import make_url

from probeowl.cli import connector_setup as setup
from probeowl.errors import ConfigError
from probeowl.guide import guide_page
from probeowl.model.application import Application
from probeowl.model.check import LoadedApplication, load_applications
from probeowl.model.connector import RestConnector, SqlConnector
from probeowl.model.run import Actor, ActorKind
from probeowl.secrets import check_keychain
from probeowl.store.audit import AuditEvent, append_event
from probeowl.store.engine import create_store_engine
from probeowl.store.tables import LOCAL_WORKSPACE

HEADER = "# Written by owl init. Edit freely; `owl check <folder>` checks it.\n"


@dataclass
class Answers:
    """Everything owl init was told, before anything is written."""

    folder: Path
    application: dict[str, Any]
    connector: dict[str, Any]
    # By environment; never written to a file.
    passwords: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """The application's name."""
        return str(self.application["application"])

    @property
    def connector_name(self) -> str:
        """The connector's name."""
        return str(self.connector["connector"])


def run(store_url: str, create_store: Callable[[str], None]) -> None:
    """Ask; create the store; write the folder and passwords, audited; offer the test and import."""
    typer.echo(
        "This writes a config folder and saves passwords in your system keychain, never in a file."
    )
    check_keychain()  # before any question: a machine without a keychain cannot finish
    answers = ask()
    # Before anything is written: every setup is recorded in the store's audit.
    if not typer.confirm(f"Create the store at {_shown(store_url)}?", default=True):
        raise ConfigError(
            "nothing was written: ProbeOwl records every setup in its store's audit. Run owl "
            "init again and create the store, or create it first with owl store init"
        )
    create_store(store_url)
    saved, applications = _write_audited(answers, store_url)
    bound = list(answers.connector["environments"])
    if typer.confirm("Test the connections now?", default=True):
        setup.test_bindings(applications, answers.name, answers.connector_name, bound)
    imported = answers.connector["type"] == "rest"  # a REST connector has no schema to import
    environment = setup.import_environment(answers.application["environments"], bound)
    if not imported:
        title = answers.application["environments"][environment]["title"]
        setup.explain_import(answers.connector_name, title)
        if typer.confirm(f"Import {answers.connector_name}'s schema in {title} now?", default=True):
            imported = setup.import_schema(
                applications, answers.name, answers.connector_name, environment, store_url
            )
    _next_steps(answers, saved, None if imported else environment)


def ask() -> Answers:
    """Every question, in order."""
    folder = Path(typer.prompt("Config folder", default="./probeowl"))
    if folder.exists() and any(folder.iterdir()):
        raise ConfigError(f"{folder} is not empty; choose a new or empty folder")
    # No default: a name accepted without thinking stays in every file, URL and run.
    name = setup.ask_name("Application id, such as wallet or card_access", None)
    spanning = without_environment(name)
    if spanning != name:
        # access_uat_test names one environment's database; the application is all of them.
        typer.echo(
            f"  An application spans its environments, each a binding: {spanning}, not {name}?"
        )
        name = setup.ask_name("Application id", spanning)
    application = {
        "application": name,
        "title": typer.prompt("Application title", default=_titled(name)),
        "owner": setup.ask_text("Owning team"),
        "environments": _ask_environments(),
    }
    typer.echo("\nFirst connector")
    kind = setup.ask_type()
    # Named after what it connects to, to change to the system's own name.
    connector_name = setup.ask_name("Connector id", f"{name}_{'api' if kind == 'rest' else kind}")
    answers = Answers(folder, application, {"connector": connector_name, "type": kind})
    answers.connector["owner"] = application["owner"]
    if kind != "rest":
        answers.connector["guards"] = {"row_ceiling": 1000}
    bindings: dict[str, dict[str, Any]] = {}
    previous: dict[str, Any] = {}
    for env, spec in application["environments"].items():
        typer.echo(f"  Binding for {env} ({spec['title']})")
        bindings[env] = setup.ask_binding(
            answers.connector, answers.passwords, env, spec["production"], previous
        )
        previous = bindings[env]
    answers.connector["environments"] = bindings
    return answers


def _write_audited(answers: Answers, store: str) -> tuple[list[str], dict[str, LoadedApplication]]:
    """Save the passwords and write the folder, kept only once audited (#276).

    The event is written first, in a transaction that commits last: if a password, a file, the
    folder's check or the commit fails, the application's folder is removed and nothing is
    recorded. Returns the keychain entries' names and the folder as owl serve will load it.
    """
    engine = create_store_engine(store)
    try:
        with engine.begin() as conn:
            append_event(conn, LOCAL_WORKSPACE, _event(answers))
            saved = setup.save_passwords(answers.connector_name, answers.passwords)
            write(answers)
            return saved, load_applications(answers.folder)  # what owl serve will load, checked
    except BaseException:
        # ask() refused a folder that was not empty, so all of this one is the setup's own.
        shutil.rmtree(answers.folder / answers.name, ignore_errors=True)
        raise
    finally:
        engine.dispose()


def _event(answers: Answers) -> AuditEvent:
    return AuditEvent(
        action="application_created",
        actor=Actor(kind=ActorKind.PERSON, name=getpass.getuser()),
        application=answers.name,
        details={
            "connector": answers.connector_name,
            "type": answers.connector["type"],
            "environments": list(answers.application["environments"]),
            "folder": answers.folder.name,
        },
    )


def write(answers: Answers) -> list[Path]:
    """Write application.yaml and the connector's file, checked against their schemas first."""
    Application.model_validate(answers.application)
    model = RestConnector if answers.connector["type"] == "rest" else SqlConnector
    model.model_validate(answers.connector)
    folder = answers.folder / answers.name
    files = {
        folder / "application.yaml": answers.application,
        folder / "connectors" / f"{answers.connector_name}.yaml": answers.connector,
    }
    for path, document in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        path.write_text(HEADER + text, encoding="utf-8", newline="\n")
    return list(files)


def _ask_environments() -> dict[str, dict[str, Any]]:
    while True:
        listed = typer.prompt("Environments, comma-separated", default="prod,uat")
        names = [n.strip() for n in str(listed).split(",")]
        if names and all(setup.NAME.match(n) for n in names) and len(set(names)) == len(names):
            break
        typer.echo("  Each one: a-z, 0-9 and _, starting with a letter, and no repeats.")
    production = setup.ask_choice(
        "  Which one is Production? (none if none is)",
        [*names, "none"],
        "prod" if "prod" in names else "none",
    )
    environments = {}
    for name in names:
        default = "Production" if name == production else _titled(name)
        title = typer.prompt(f"  Title for {name}", default=default)
        environments[name] = {"title": title, "production": name == production}
    return environments


# Words that name one environment, as database and schema names often carry them.
_ENVIRONMENT_WORDS = {
    "prod", "production", "prd", "live", "uat", "test", "tst", "qa", "sit", "stg", "staging",
    "preprod", "dev", "development", "local",
}  # fmt: skip


def without_environment(name: str) -> str:
    """The id without the words that name one environment: access_uat_test is access.

    The id as it is when nothing, or no valid id, would be left.
    """
    words = [w for w in name.split("_") if w and w not in _ENVIRONMENT_WORDS]
    left = "_".join(words)
    return left if left and left[0].isalpha() else name


def _titled(name: str) -> str:
    # Short names are usually abbreviations: uat is UAT, dev is DEV.
    return name.upper() if len(name) <= 3 else name.replace("_", " ").capitalize()


def _next_steps(answers: Answers, saved: list[str], import_in: str | None) -> None:
    """What to do next; import_in names the environment still to import, if no import ran."""
    if saved:
        typer.echo(f"\nDone. Passwords are in your keychain: {', '.join(saved)}")
    else:
        typer.echo("\nDone.")
    steps = [
        f"owl serve --config {answers.folder.as_posix()}",
        "open http://127.0.0.1:7878",
    ]
    if import_in is not None:
        steps.append(setup.import_later(answers.name, answers.connector_name, import_in))
    if answers.connector["type"] != "rest":
        steps.append(f"Tables: confirm the labels suggested for {answers.connector_name}")
    steps.append(f"Write your first entity and probe: {guide_page('first-application/')}")
    # A new terminal finds owl only once uv's tool folder is on PATH.
    typer.echo("If a new terminal says owl isn't found: uv tool update-shell, then open another.")
    typer.echo("Next:")
    for number, step in enumerate(steps, start=1):
        typer.echo(f"  {number}. {step}")


def _shown(url: str) -> str:
    return make_url(url).render_as_string(hide_password=True)
