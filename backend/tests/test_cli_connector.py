# Author: @SaadZarook
"""`owl connector add`: a new connector, more bindings for one, and everything it refuses."""

import shutil
from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml
from sqlalchemy import select
from typer.testing import CliRunner

from probeowl.cli import connector as connector_cli
from probeowl.cli import connector_setup
from probeowl.cli import main as cli
from probeowl.errors import ConfigError, NotFoundError, ProbeOwlError
from probeowl.model.check import load_applications
from probeowl.store import migrate
from probeowl.store.engine import create_store_engine
from probeowl.store.tables import audit_events
from tests.audit_fails import audit_fails
from tests.conftest import MemoryKeychain

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
PASSWORD = "not-a-real-password-2"


@pytest.fixture
def folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    copy = tmp_path / "config"
    shutil.copytree(EXAMPLES, copy)
    url = f"sqlite:///{tmp_path / 'store.db'}"
    monkeypatch.setenv("PROBEOWL_STORE_URL", url)
    store = create_store_engine(url)  # a store at this release: every change is audited
    migrate.upgrade(store)
    store.dispose()
    return copy


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    saved: dict[str, str] = {}
    monkeypatch.setattr(
        connector_setup, "save_secret", lambda s, u, v: saved.__setitem__(f"{s}/{u}", v)
    )
    monkeypatch.setattr(connector_cli, "check_keychain", lambda: None)
    return saved


def add(folder: Path, answers: list[str], application: str | None = "wallet") -> Any:
    args = ["connector", "add", "--config", str(folder)]
    args += ["--application", application] if application else []
    # A last "n": the import offered after a SQL connector is declined unless a test answers it.
    return CliRunner().invoke(cli.app, args, input="\n".join([*answers, "n"]) + "\n")


# A MariaDB connector bound in prod (a replica) and uat; no connection test.
NEW_MARIADB = [
    "reports_db", "mariadb",
    "y", "10.0.0.5", "", "", "probeowl_ro", PASSWORD, "y", "y", "n",  # prod
    "y", "", "", "reports_uat", "", "", "n", "y", "n",                # uat: prod's defaults
    "n",                                                              # test the bindings
]  # fmt: skip


def test_a_new_connector_is_written_bound_and_its_passwords_kept_in_the_keychain(
    folder: Path, keychain: dict[str, str]
) -> None:
    result = add(folder, NEW_MARIADB)
    assert result.exit_code == 0, result.output
    path = folder / "wallet" / "connectors" / "reports_db.yaml"
    document = yaml.safe_load(path.read_text("utf-8"))
    assert document["type"] == "mariadb"
    assert document["owner"] == "payments-team"
    assert document["environments"]["prod"] == {
        "host": "10.0.0.5",
        "port": 3306,
        "database": "reports",
        "user": "probeowl_ro",
        "secret": "keyring:probeowl/reports_db.prod",
        "replica": True,
    }
    assert document["environments"]["uat"]["database"] == "reports_uat"
    assert "replica" not in document["environments"]["uat"]
    assert PASSWORD not in path.read_text("utf-8")
    assert keychain == {
        "probeowl/reports_db.prod": PASSWORD,
        "probeowl/reports_db.uat": PASSWORD,
    }
    assert "reports_db" in load_applications(folder)["wallet"].connectors
    # Audited, and with the import declined the next steps say how to import.
    assert "Not recorded in the audit" not in result.output
    assert "1. Load reports_db into owl serve: owl reload, or Reload configuration" in result.output
    assert "owl catalog import reports_db --application wallet --env uat" in result.output


def test_a_password_already_saved_under_the_name_is_kept_unless_overwriting_is_chosen(
    folder: Path, keychain: dict[str, str], machine_keychain: MemoryKeychain
) -> None:
    # Another application's connector named reports_db saved this one.
    machine_keychain.entries[("probeowl", "reports_db.prod")] = "another-applications-password"
    kept = list(NEW_MARIADB)
    kept[kept.index(PASSWORD)] = "n"  # don't overwrite: no password is asked for prod
    # Nor is there one to reuse for uat with Enter: it is typed.
    uat_password = kept.index("reports_uat") + 2
    kept[uat_password] = PASSWORD
    result = add(folder, kept)
    assert result.exit_code == 0, result.output
    assert "A password is already saved as probeowl/reports_db.prod" in result.output
    assert "Kept: probeowl/reports_db.prod stays as it is" in result.output
    assert keychain == {"probeowl/reports_db.uat": PASSWORD}


def test_overwriting_a_password_already_saved_is_asked_and_then_done(
    folder: Path, keychain: dict[str, str], machine_keychain: MemoryKeychain
) -> None:
    machine_keychain.entries[("probeowl", "reports_db.prod")] = "another-applications-password"
    replaced = list(NEW_MARIADB)
    replaced.insert(replaced.index(PASSWORD), "y")
    result = add(folder, replaced)
    assert result.exit_code == 0, result.output
    assert keychain["probeowl/reports_db.prod"] == PASSWORD


def test_an_environment_can_be_left_unbound(folder: Path, keychain: dict[str, str]) -> None:
    answers = ["notes_api", "rest", "n", "y", "http://127.0.0.1:9000", "n", "n"]
    result = add(folder, answers)
    assert result.exit_code == 0, result.output
    document = yaml.safe_load((folder / "wallet" / "connectors" / "notes_api.yaml").read_text())
    assert list(document["environments"]) == ["uat"]
    assert "guards" not in document
    assert keychain == {}


def without_uat(path: Path) -> str:
    """The connector's file with its uat binding cut off, as a prod-only connector."""
    text = path.read_text("utf-8").replace("\r\n", "\n")
    kept = text.split("\n  uat:\n")[0] + "\n"
    path.write_text(kept, encoding="utf-8", newline="\n")
    return kept


def test_a_missing_environment_is_added_to_an_existing_connector_keeping_its_comments(
    folder: Path, keychain: dict[str, str]
) -> None:
    path = folder / "wallet" / "connectors" / "ledger_db.yaml"
    before = without_uat(path)
    unbound = load_applications(folder, allow_unbound=True)
    assert "uat" not in unbound["wallet"].connectors["ledger_db"].environments
    answers = [
        "ledger_db",
        "y", "", "", "", "LEDGER_UAT", "", PASSWORD, "n", "n", "Oracle Free has no tcps",
        "n",
    ]  # fmt: skip
    result = add(folder, answers)
    assert result.exit_code == 0, result.output
    assert "ledger_db (oracle) is bound in prod; not in uat" in result.output
    after = path.read_text("utf-8")
    assert after.startswith(before.split("environments:")[0])  # the header comments are kept
    uat = yaml.safe_load(after)["environments"]["uat"]
    assert uat["host"] == "127.0.0.1"  # prod's answers were the defaults
    assert (uat["database"], uat["schema"]) == ("FREEPDB1", "LEDGER_UAT")
    assert uat["secret"] == "keyring:probeowl/ledger_db.uat"
    assert uat["encrypt"] is False
    assert yaml.safe_load(after)["environments"]["prod"]["schema"] == "LEDGER"
    assert keychain == {"probeowl/ledger_db.uat": PASSWORD}
    assert "uat" in load_applications(folder)["wallet"].connectors["ledger_db"].environments
    assert "Added to" in result.output
    # The folder loads as owl serve loads it again: nothing is left waiting for a binding.
    assert "won't start until" not in result.output


def test_a_folder_waiting_for_several_bindings_says_which_are_left(
    folder: Path, keychain: dict[str, str]
) -> None:
    # Before: owl check, and so owl serve, refuse the folder for both missing uat bindings.
    without_uat(folder / "wallet" / "connectors" / "ledger_db.yaml")
    without_uat(folder / "wallet" / "connectors" / "wallet_db.yaml")
    with pytest.raises(ConfigError, match="has no binding"):
        load_applications(folder)
    answers = [
        "ledger_db",
        "y", "", "", "", "LEDGER_UAT", "", PASSWORD, "n", "n", "Oracle Free has no tcps",
        "n",
    ]  # fmt: skip
    result = add(folder, answers)
    assert result.exit_code == 0, result.output
    assert "owl serve won't start until these are bound too" in result.output
    assert "has no binding (wallet_db)" in result.output
    assert "has no binding (ledger_db)" not in result.output


def test_a_connector_bound_everywhere_is_refused(folder: Path, keychain: dict[str, str]) -> None:
    result = add(folder, ["wallet_db"])
    assert isinstance(result.exception, ConfigError)
    assert "bound in every environment of wallet" in str(result.exception)


def test_a_production_binding_that_is_not_a_replica_writes_nothing(
    folder: Path, keychain: dict[str, str]
) -> None:
    answers = NEW_MARIADB.copy()
    answers[8] = "n"  # prod: not a replica
    result = add(folder, answers)
    assert isinstance(result.exception, ConfigError)
    assert "never from the primary" in str(result.exception)
    assert not (folder / "wallet" / "connectors" / "reports_db.yaml").exists()
    assert keychain == {}


def test_binding_no_environment_writes_nothing(folder: Path, keychain: dict[str, str]) -> None:
    result = add(folder, ["reports_db", "mariadb", "n", "n"])
    assert isinstance(result.exception, ConfigError)
    assert "no environment was bound" in str(result.exception)
    assert not (folder / "wallet" / "connectors" / "reports_db.yaml").exists()


def test_an_unknown_application_names_the_ones_there_are(
    folder: Path, keychain: dict[str, str]
) -> None:
    result = add(folder, [], application="payroll")
    assert isinstance(result.exception, NotFoundError)
    assert "there are: card_access, wallet" in str(result.exception)


def test_with_several_applications_it_asks_which(folder: Path, keychain: dict[str, str]) -> None:
    result = add(folder, ["wallet", *NEW_MARIADB], application=None)
    assert result.exit_code == 0, result.output
    assert (folder / "wallet" / "connectors" / "reports_db.yaml").exists()


def test_a_change_the_folder_would_not_load_with_is_undone(
    folder: Path, keychain: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = folder / "wallet" / "connectors" / "ledger_db.yaml"
    before = without_uat(path)
    real = connector_cli.load_applications
    calls = {"n": 0}

    def fails_after_writing(root: Path, **options: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:  # the check right after the write
            raise ConfigError("a probe would no longer load")
        return real(root, **options)

    monkeypatch.setattr(connector_cli, "load_applications", fails_after_writing)
    answers = [
        "ledger_db",
        "y", "", "", "", "LEDGER_UAT", "", PASSWORD, "n", "n", "no tcps here",
    ]  # fmt: skip
    result = add(folder, answers)
    assert isinstance(result.exception, ConfigError)
    assert "so it was undone: a probe would no longer load" in str(result.exception)
    assert path.read_text("utf-8") == before
    assert keychain == {}


class Catalog:
    imported: ClassVar[list[tuple[str, str, str]]] = []

    def __init__(self, *args: object) -> None:
        pass

    def run(self, application: str, connector: str, environment: str) -> Any:
        self.imported.append((application, connector, environment))
        return type("Snapshot", (), {"tables": [1, 2]})()

    def review(self, application: str) -> Any:
        return type("Review", (), {"unconfirmed": 2})()


def test_with_a_store_it_is_audited_and_the_import_is_offered(
    folder: Path, keychain: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    migrate.upgrade(store)
    monkeypatch.setattr(connector_setup, "CatalogService", Catalog)
    result = add(folder, [*NEW_MARIADB, "y"])  # import in uat: yes
    assert result.exit_code == 0, result.output
    assert Catalog.imported == [("wallet", "reports_db", "uat")]
    assert "Imported reports_db in uat: 2 tables, 2 labels suggested" in result.output
    assert "owl catalog import" not in result.output
    with store.connect() as conn:
        [event] = conn.execute(
            select(audit_events).where(audit_events.c.action == "connector_added")
        ).mappings()
    assert event["application"] == "wallet"
    assert "reports_db" in str(event["details"]) and "10.0.0.5" not in str(event["details"])
    store.dispose()


def test_with_a_store_whose_audit_cannot_be_written_nothing_is_written_or_saved(
    folder: Path, keychain: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    migrate.upgrade(store)
    audit_fails(monkeypatch, connector_cli, "connector_added")
    result = add(folder, NEW_MARIADB)
    assert result.exit_code != 0
    assert not (folder / "wallet" / "connectors" / "reports_db.yaml").exists()
    assert keychain == {}
    with store.connect() as conn:
        assert list(conn.execute(select(audit_events))) == []
    store.dispose()


def test_a_password_that_cannot_be_saved_puts_the_file_back_and_records_nothing(
    folder: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    migrate.upgrade(store)

    def refused(service: str, username: str, value: str) -> None:
        raise ConfigError("the system keychain could not be written: locked")

    monkeypatch.setattr(connector_setup, "save_secret", refused)
    monkeypatch.setattr(connector_cli, "check_keychain", lambda: None)
    result = add(folder, NEW_MARIADB)
    assert result.exit_code != 0
    assert isinstance(result.exception, ProbeOwlError) or "keychain" in result.output
    assert not (folder / "wallet" / "connectors" / "reports_db.yaml").exists()
    with store.connect() as conn:
        assert list(conn.execute(select(audit_events))) == []
    store.dispose()
