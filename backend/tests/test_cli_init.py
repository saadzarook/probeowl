# Author: @SaadZarook
"""`owl init`: the questions, the files it writes, and where passwords go (a fake keychain)."""

from pathlib import Path
from typing import Any, ClassVar

import pytest
import yaml
from sqlalchemy import Connection
from typer.testing import CliRunner

from probeowl.cli import connector_setup
from probeowl.cli import init as init_wizard
from probeowl.cli import main as cli
from probeowl.errors import ConfigError
from probeowl.guide import GUIDE_URL
from probeowl.model.check import load_applications
from probeowl.model.connector_status import ConnectionTest
from probeowl.store.audit import AuditEvent, list_events
from probeowl.store.engine import create_store_engine
from probeowl.store.tables import LOCAL_WORKSPACE

PASSWORD = "not-a-real-password-1"


class Keychain:
    """Stands in for the system keychain: what owl init saved, by service and user."""

    def __init__(self) -> None:
        self.saved: dict[tuple[str, str], str] = {}

    def save(self, service: str, username: str, value: str) -> None:
        self.saved[(service, username)] = value


@pytest.fixture
def keychain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Keychain:
    found = Keychain()
    monkeypatch.setattr(connector_setup, "save_secret", found.save)
    monkeypatch.setattr(init_wizard, "check_keychain", lambda: None)
    monkeypatch.setenv("PROBEOWL_STORE_URL", f"sqlite:///{tmp_path / 'store.db'}")
    return found


def sqlserver_answers(folder: Path, *after: str) -> list[str]:
    return [
        str(folder),
        "wallet",
        "",  # title: Wallet
        "payments-team",
        "",  # environments: prod,uat
        "",  # Production: prod
        "",  # Production
        "",  # UAT
        "",  # sqlserver
        "wallet_db",
        # prod
        "",  # host 127.0.0.1
        "",  # port 1433
        "",  # database wallet
        "probeowl_ro",
        PASSWORD,
        "y",  # a replica
        "n",  # trust the certificate
        # uat: prod's answers by default
        "",
        "",
        "wallet_uat",
        "",  # the same login
        "",  # the same password
        "n",  # not a replica
        "n",
        *after,
    ]


def init(answers: list[str]) -> Any:
    return CliRunner().invoke(cli.app, ["init"], input="\n".join(answers) + "\n")


def test_a_sql_server_application_is_written_and_passes_owl_check(
    keychain: Keychain, tmp_path: Path
) -> None:
    folder = tmp_path / "probeowl"
    result = init(sqlserver_answers(folder, "n", "n"))  # no test, no store
    assert result.exit_code == 0, result.output
    application = yaml.safe_load((folder / "wallet" / "application.yaml").read_text("utf-8"))
    assert application == {
        "application": "wallet",
        "title": "Wallet",
        "owner": "payments-team",
        "environments": {
            "prod": {"title": "Production", "production": True},
            "uat": {"title": "UAT", "production": False},
        },
    }
    text = (folder / "wallet" / "connectors" / "wallet_db.yaml").read_text("utf-8")
    connector = yaml.safe_load(text)
    assert connector["type"] == "sqlserver"
    assert connector["guards"] == {"row_ceiling": 1000}
    assert connector["environments"]["prod"] == {
        "host": "127.0.0.1",
        "port": 1433,
        "database": "wallet",
        "user": "probeowl_ro",
        "secret": "keyring:probeowl/wallet_db.prod",
        "replica": True,
    }
    assert connector["environments"]["uat"]["database"] == "wallet_uat"
    assert "replica" not in connector["environments"]["uat"]
    # The password goes to the keychain only, under the name the file refers to.
    assert PASSWORD not in text
    assert keychain.saved == {
        ("probeowl", "wallet_db.prod"): PASSWORD,
        ("probeowl", "wallet_db.uat"): PASSWORD,
    }
    assert set(load_applications(folder)) == {"wallet"}
    assert "Passwords are in your keychain: probeowl/wallet_db.prod, probeowl/wallet_db.uat" in (
        result.output
    )
    assert f"owl serve --config {folder.as_posix()}" in result.output
    # The web app comes with owl serve: no Node.js, no second server.
    assert "open http://127.0.0.1:7878" in result.output
    assert "owl isn't found: uv tool update-shell" in result.output
    assert f"{GUIDE_URL}first-application/" in result.output
    assert PASSWORD not in result.output


def test_one_environment_and_no_production_taken_with_enter(
    keychain: Keychain, tmp_path: Path
) -> None:
    # The release blocker (#248): Enter at "Which one is Production?" with its default, none.
    folder = tmp_path / "probeowl"
    answers = [
        str(folder),
        "access",
        "",  # title: Access
        "access-team",
        "uat",  # environments
        "",  # Production: none
        "",  # UAT
        "mariadb",
        "access_db",
        "",  # host 127.0.0.1
        "",  # port 3306
        "access_uat",
        "probeowl_ro",
        PASSWORD,
        "n",  # not a replica
        "",  # TLS: yes
        "n",  # verify the certificate
        "n",  # no store
        "n",  # no test
    ]
    result = init(answers)
    assert result.exit_code == 0, result.output
    assert "Which one is Production? (none if none is) (uat, none) [none]:" in result.output
    application = yaml.safe_load((folder / "access" / "application.yaml").read_text("utf-8"))
    assert application["environments"] == {"uat": {"title": "UAT", "production": False}}
    assert set(load_applications(folder)) == {"access"}


def test_an_answer_that_is_not_a_choice_is_asked_again(keychain: Keychain, tmp_path: Path) -> None:
    answers = sqlserver_answers(tmp_path / "probeowl", "n", "n")
    production = answers.index("", 5)  # the Production question, after the environments
    answers[production:production] = ["staging"]
    kind = answers.index("wallet_db") - 1  # the type question, just before the connector id
    answers[kind:kind] = ["postgres"]
    result = init(answers)
    assert result.exit_code == 0, result.output
    assert "Choose one of: prod, uat, none." in result.output
    assert "Choose one of: sqlserver, oracle, mariadb, rest." in result.output


def test_a_production_binding_that_is_not_a_replica_stops_and_writes_nothing(
    keychain: Keychain, tmp_path: Path
) -> None:
    answers = sqlserver_answers(tmp_path / "probeowl")
    answers[answers.index("y")] = "n"
    result = init(answers)
    assert isinstance(result.exception, ConfigError)
    assert "never from the primary" in str(result.exception)
    assert not (tmp_path / "probeowl").exists()
    assert keychain.saved == {}


def test_a_folder_with_files_in_it_is_refused(keychain: Keychain, tmp_path: Path) -> None:
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "notes.txt").write_text("mine", encoding="utf-8")
    result = init([str(tmp_path / "used")])
    assert isinstance(result.exception, ConfigError)
    assert "is not empty" in str(result.exception)


def test_a_name_that_cannot_be_a_file_or_lookup_name_is_asked_again(
    keychain: Keychain, tmp_path: Path
) -> None:
    answers = sqlserver_answers(tmp_path / "probeowl", "n", "n")
    answers.insert(1, "My App")
    result = init(answers)
    assert result.exit_code == 0, result.output
    assert "Use a-z, 0-9 and _" in result.output


def test_no_keychain_stops_before_any_question(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_keychain() -> None:
        raise ConfigError("the keyring backend keeps secrets in files")

    monkeypatch.setattr(init_wizard, "check_keychain", no_keychain)
    result = init([str(tmp_path / "probeowl")])
    assert isinstance(result.exception, ConfigError)
    assert "keeps secrets in files" in str(result.exception)
    assert "Config folder" not in result.output


def test_an_oracle_binding_asks_for_its_service_schema_and_why_it_is_not_encrypted(
    keychain: Keychain, tmp_path: Path
) -> None:
    folder = tmp_path / "probeowl"
    answers = [
        str(folder), "ledger", "", "payments-team", "uat", "none", "",
        "oracle", "ledger_db",
        "", "", "", "LEDGER_UAT", "probeowl_ro", PASSWORD, "n", "n", "Oracle Free has no tcps",
        "n", "n",
    ]  # fmt: skip
    result = init(answers)
    assert result.exit_code == 0, result.output
    connector = yaml.safe_load((folder / "ledger" / "connectors" / "ledger_db.yaml").read_text())
    assert connector["environments"]["uat"] == {
        "host": "127.0.0.1",
        "port": 1521,
        "database": "FREEPDB1",
        "schema": "LEDGER_UAT",
        "user": "probeowl_ro",
        "secret": "keyring:probeowl/ledger_db.uat",
        "encrypt": False,
        "encrypt_reason": "Oracle Free has no tcps",
    }
    assert set(load_applications(folder)) == {"ledger"}


def test_a_rest_connector_keeps_its_token_in_the_keychain(
    keychain: Keychain, tmp_path: Path
) -> None:
    folder = tmp_path / "probeowl"
    answers = [
        str(folder), "payments", "", "payments-team", "prod", "", "",
        "rest", "checkout_api",
        "https://checkout.example.com", "y", "token-value-1",
        "n", "n",
    ]  # fmt: skip
    result = init(answers)
    assert result.exit_code == 0, result.output
    connector = yaml.safe_load(
        (folder / "payments" / "connectors" / "checkout_api.yaml").read_text()
    )
    assert connector["environments"]["prod"] == {
        "base_url": "https://checkout.example.com",
        "secret": "keyring:probeowl/checkout_api.prod",
    }
    assert "guards" not in connector
    assert keychain.saved == {("probeowl", "checkout_api.prod"): "token-value-1"}
    assert "Tables:" not in result.output


class Statuses:
    def __init__(self, *args: object) -> None:
        pass

    def test_connection(self, application: str, connector: str, environment: str) -> ConnectionTest:
        if environment == "prod":
            return ConnectionTest(
                connector=connector, environment=environment, reachable=True, duration_ms=42
            )
        return ConnectionTest(
            connector=connector,
            environment=environment,
            reachable=False,
            reason="authentication failed",
            duration_ms=3,
        )


class Snapshot:
    tables: ClassVar[list[object]] = [object()] * 7


class Review:
    unconfirmed = 7


class Catalog:
    imported: ClassVar[list[tuple[str, str, str]]] = []

    def __init__(self, *args: object) -> None:
        pass

    def run(self, application: str, connector: str, environment: str) -> Snapshot:
        self.imported.append((application, connector, environment))
        return Snapshot()

    def review(self, application: str) -> Review:
        return Review()


def test_it_tests_each_binding_creates_the_store_and_imports_outside_production(
    keychain: Keychain, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(connector_setup, "ConnectorStatuses", Statuses)
    monkeypatch.setattr(connector_setup, "CatalogService", Catalog)
    result = init(sqlserver_answers(tmp_path / "probeowl", "y", "y", "y"))
    assert result.exit_code == 0, result.output
    assert "prod: reached in 42 ms" in result.output
    # A binding that cannot be reached does not stop the setup.
    assert "uat: not reached: authentication failed" in result.output
    assert (tmp_path / "store.db").exists()
    assert Catalog.imported == [("wallet", "wallet_db", "uat")]
    assert "Imported wallet_db in uat: 7 tables, 7 labels suggested" in result.output


def test_the_application_id_has_no_default_and_the_connector_is_named_for_its_type(
    keychain: Keychain, tmp_path: Path
) -> None:
    answers = sqlserver_answers(tmp_path / "probeowl", "n", "n")
    answers[1:2] = ["", "wallet"]  # Enter alone is not an answer: it is asked again
    answers[answers.index("wallet_db")] = ""  # accept the suggested connector id
    # Only an id ending in _db suggests a database name, so this one is typed.
    answers[answers.index("probeowl_ro") - 1] = "wallet"
    result = init(answers)
    assert result.exit_code == 0, result.output
    assert "Application id, such as wallet or card_access:" in result.output
    assert "Connector id [wallet_sqlserver]:" in result.output
    assert (tmp_path / "probeowl" / "wallet" / "connectors" / "wallet_sqlserver.yaml").exists()


@pytest.mark.parametrize(
    ("typed", "suggested"),
    [
        ("access_uat_test", "access"),
        ("wallet_prod", "wallet"),
        ("dev_orders", "orders"),
        ("card_access", "card_access"),
        ("test", "test"),  # nothing would be left
        ("uat_2", "uat_2"),  # no valid id would be left
    ],
)
def test_an_id_without_its_environment_words(typed: str, suggested: str) -> None:
    assert init_wizard.without_environment(typed) == suggested


def test_an_application_id_naming_an_environment_is_offered_without_it(
    keychain: Keychain, tmp_path: Path
) -> None:
    answers = sqlserver_answers(tmp_path / "probeowl", "n", "n")
    answers[1:2] = ["wallet_uat_test", ""]  # accept the id without uat and test
    result = init(answers)
    assert result.exit_code == 0, result.output
    assert (
        "An application spans its environments, each a binding: wallet, not wallet_uat_test?"
        in (result.output)
    )
    assert "Application id [wallet]:" in result.output
    assert (tmp_path / "probeowl" / "wallet" / "application.yaml").exists()


def test_without_an_import_the_next_steps_say_how_to_import(
    keychain: Keychain, tmp_path: Path
) -> None:
    # The store created, no connection test, and the import declined.
    result = init(sqlserver_answers(tmp_path / "probeowl", "y", "n", "n"))
    assert result.exit_code == 0, result.output
    assert "never data, so the Tables screen can list them" in result.output
    assert (
        "3. Import wallet_db's schema: Import from replica on the Tables screen, or owl catalog "
        "import wallet_db --application wallet --env uat while owl serve runs"
    ) in result.output
    assert "4. Tables: confirm the labels suggested for wallet_db" in result.output


# --- The store first, so the setup is audited (#276) --------------------------------------------


def recorded(tmp_path: Path) -> list[AuditEvent]:
    store = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
    try:
        with store.connect() as conn:
            return list_events(conn, LOCAL_WORKSPACE)
    finally:
        store.dispose()


def test_the_store_is_asked_for_first_and_the_setup_is_recorded_in_its_audit(
    keychain: Keychain, tmp_path: Path
) -> None:
    result = init(sqlserver_answers(tmp_path / "probeowl", "y", "n", "n"))
    assert result.exit_code == 0, result.output
    assert result.output.index("Create the store at") < result.output.index("Test the connections")
    [event] = recorded(tmp_path)
    assert (event.action, event.application) == ("application_created", "wallet")
    assert event.details == {
        "connector": "wallet_db",
        "type": "sqlserver",
        "environments": ["prod", "uat"],
        "folder": "probeowl",
    }
    assert PASSWORD not in str(event.details)
    assert "Not recorded in the audit" not in result.output


def test_a_setup_whose_audit_cannot_be_written_leaves_no_folder_and_no_password(
    keychain: Keychain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(conn: Connection, workspace: str, event: AuditEvent) -> None:
        raise RuntimeError("the store could not be written")

    monkeypatch.setattr(init_wizard, "append_event", failing)
    result = init(sqlserver_answers(tmp_path / "probeowl", "y", "n", "n"))
    assert result.exit_code != 0
    assert not (tmp_path / "probeowl" / "wallet").exists()
    assert keychain.saved == {}
    assert recorded(tmp_path) == []


def test_a_folder_that_would_not_load_is_removed_and_not_recorded(
    keychain: Keychain, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refused(folder: Path) -> Any:
        raise ConfigError("the folder would not load")

    monkeypatch.setattr(init_wizard, "load_applications", refused)
    result = init(sqlserver_answers(tmp_path / "probeowl", "y", "n", "n"))
    assert result.exit_code != 0
    assert not (tmp_path / "probeowl" / "wallet").exists()
    assert recorded(tmp_path) == []


def test_without_the_store_it_carries_on_and_says_it_was_not_recorded(
    keychain: Keychain, tmp_path: Path
) -> None:
    result = init(sqlserver_answers(tmp_path / "probeowl", "n", "n"))
    assert result.exit_code == 0, result.output
    assert "Not recorded in the audit: no store yet (owl store init creates it)." in result.output
    assert (tmp_path / "probeowl" / "wallet" / "application.yaml").exists()
    assert not (tmp_path / "store.db").exists()
