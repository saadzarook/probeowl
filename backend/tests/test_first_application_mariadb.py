# Author: @SaadZarook
"""A newcomer's first application, end to end on a real MariaDB: owl init, its import, then the
Tables screen's API lists the imported tables, and confirming one creates catalog.yaml. Then a
second connector, added with owl connector add, connected to and imported the same way; and one
added while owl serve runs, loaded by a reload and reached without a restart.

The keychain is a stand-in (a dict), so the test never touches the real one; everything else is
real: the wizard, the connection, the schema import, the store, the review and the write rules.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pymysql
import pytest
from sqlalchemy.engine import make_url
from typer.testing import CliRunner

from probeowl import secrets
from probeowl.api.app import create_app
from probeowl.catalog.service import CatalogService
from probeowl.cli import connector as connector_cli
from probeowl.cli import connector_setup
from probeowl.cli import init as init_wizard
from probeowl.cli import main as cli
from probeowl.config_folder import ConfigFolder
from probeowl.connector_status import ConnectorStatuses
from probeowl.guards.breaker import CircuitBreaker
from probeowl.model.check import check_folder, load_applications
from probeowl.reload import ConfigReloader
from probeowl.runs import RunService
from probeowl.store.engine import create_store_engine
from tests.local_client import local_client
from tests.store_databases import store_database_url

pytestmark = pytest.mark.database


@pytest.fixture
def mariadb() -> Iterator[tuple[str, int, str, str, str, str]]:
    """The test database, with one table of its own: host, port, database, user, password, table."""
    with store_database_url("mariadb") as url:
        parsed = make_url(url)
        host, port = parsed.host or "127.0.0.1", parsed.port or 3306
        user, password, database = parsed.username or "", parsed.password or "", parsed.database
        assert database is not None
        table = f"first_app_{uuid.uuid4().hex[:8]}"
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password, database=database
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE TABLE {table} (id VARCHAR(12) NOT NULL PRIMARY KEY, "
                    "status VARCHAR(10) NOT NULL, created_at DATETIME NOT NULL)"
                )
            yield host, port, database, user, password, table
        finally:
            with conn.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
            conn.close()


def test_init_import_then_the_tables_are_listed_and_confirming_writes_the_catalog(
    mariadb: tuple[str, int, str, str, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, port, database, user, password, table = mariadb
    keychain: dict[str, str] = {}
    monkeypatch.setattr(init_wizard, "check_keychain", lambda: None)
    monkeypatch.setattr(
        connector_setup, "save_secret", lambda s, u, v: keychain.__setitem__(f"{s}/{u}", v)
    )
    monkeypatch.setattr(secrets, "_from_keyring", lambda target: keychain[target])
    store = f"sqlite:///{tmp_path / 'store.db'}"
    monkeypatch.setenv("PROBEOWL_STORE_URL", store)
    folder = tmp_path / "probeowl"

    answers = [
        str(folder), "access", "", "access-team", "uat", "none", "",
        "mariadb", "access_db",
        host, str(port), database, user, password, "n", "y", "y", "self-signed test certificate",
        "y", "y", "y",  # create the store, test the connection, import the schema
    ]  # fmt: skip
    result = CliRunner().invoke(cli.app, ["init"], input="\n".join(answers) + "\n")
    assert result.exit_code == 0, result.output
    assert "uat: reached in" in result.output
    assert "Imported access_db in uat:" in result.output
    assert "confirm them on the Tables screen" in result.output
    assert not (folder / "access" / "catalog.yaml").exists()

    # owl serve, as it is built for this folder.
    applications = load_applications(folder)
    engine = create_store_engine(store)
    api = local_client(
        create_app(catalog=CatalogService(applications, engine, folder=ConfigFolder(folder)))
    )
    assert api.get("/api/applications/access/catalog").status_code == 404
    review = api.get("/api/applications/access/catalog/review").json()
    listed = {t["table"].lower(): t for t in review["tables"]}
    assert table in listed, sorted(listed)
    assert listed[table]["imported_in"] == ["uat"]
    assert listed[table]["label"] is None
    assert listed[table]["suggestion"] is not None

    entry = {
        "connector": "access_db",
        "table": listed[table]["table"],
        "label": "master",
        "owner": "access-team",
        "columns": {"id": {"roles": ["primary_key"]}},
    }
    url = f"/api/applications/access/catalog/tables/access_db/{listed[table]['table']}"
    preview = api.put(url, json={"entry": entry}).json()
    assert (preview["base"], preview["path"], preview["written"]) == (
        "absent",
        "access/catalog.yaml",
        False,
    )
    written = api.put(url, json={"entry": entry, "base": preview["base"]})
    assert written.status_code == 200, written.text
    assert (folder / "access" / "catalog.yaml").exists()
    assert check_folder(folder).findings == []
    review = api.get("/api/applications/access/catalog/review").json()
    assert {t["table"].lower(): t["label"] for t in review["tables"]}[table] == "master"
    engine.dispose()


def test_a_second_connector_added_with_owl_connector_add_is_reached_and_imported(
    mariadb: tuple[str, int, str, str, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, port, database, user, password, table = mariadb
    keychain: dict[str, str] = {}
    monkeypatch.setattr(init_wizard, "check_keychain", lambda: None)
    monkeypatch.setattr(connector_cli, "check_keychain", lambda: None)
    monkeypatch.setattr(
        connector_setup, "save_secret", lambda s, u, v: keychain.__setitem__(f"{s}/{u}", v)
    )
    monkeypatch.setattr(secrets, "_from_keyring", lambda target: keychain[target])
    store = f"sqlite:///{tmp_path / 'store.db'}"
    monkeypatch.setenv("PROBEOWL_STORE_URL", store)
    folder = tmp_path / "probeowl"
    tls = ["n", "y", "y", "self-signed test certificate"]  # not a replica; TLS, a test certificate
    first = [
        str(folder), "access", "", "access-team", "uat", "none", "",
        "mariadb", "access_db", host, str(port), database, user, password, *tls,
        "y", "n", "n",  # create the store; no connection test; no import
    ]  # fmt: skip
    result = CliRunner().invoke(cli.app, ["init"], input="\n".join(first) + "\n")
    assert result.exit_code == 0, result.output

    second = [
        "visits_db", "mariadb",
        "y", host, str(port), database, user, password, *tls,
        "y", "y",  # test the binding; import its schema
    ]  # fmt: skip
    result = CliRunner().invoke(
        cli.app, ["connector", "add", "--config", str(folder)], input="\n".join(second) + "\n"
    )
    assert result.exit_code == 0, result.output
    assert "uat: reached in" in result.output
    assert "Imported visits_db in uat:" in result.output
    assert keychain["probeowl/visits_db.uat"] == password
    assert password not in (folder / "access" / "connectors" / "visits_db.yaml").read_text()

    applications = load_applications(folder)
    assert set(applications["access"].connectors) == {"access_db", "visits_db"}
    engine = create_store_engine(store)
    api = local_client(
        create_app(catalog=CatalogService(applications, engine, folder=ConfigFolder(folder)))
    )
    review = api.get("/api/applications/access/catalog/review").json()
    imported = {(t["connector"], t["table"].lower()) for t in review["tables"]}
    assert ("visits_db", table) in imported
    assert ("access_db", table) not in imported  # access_db's import was declined
    engine.dispose()


def test_a_connector_added_while_owl_serve_runs_is_loaded_by_a_reload_and_reached(
    mariadb: tuple[str, int, str, str, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, port, database, user, password, _ = mariadb
    keychain: dict[str, str] = {}
    monkeypatch.setattr(init_wizard, "check_keychain", lambda: None)
    monkeypatch.setattr(connector_cli, "check_keychain", lambda: None)
    monkeypatch.setattr(
        connector_setup, "save_secret", lambda s, u, v: keychain.__setitem__(f"{s}/{u}", v)
    )
    monkeypatch.setattr(secrets, "_from_keyring", lambda target: keychain[target])
    store = f"sqlite:///{tmp_path / 'store.db'}"
    monkeypatch.setenv("PROBEOWL_STORE_URL", store)
    folder = tmp_path / "probeowl"
    tls = ["n", "y", "y", "self-signed test certificate"]  # not a replica; TLS, a test certificate
    first = [
        str(folder), "access", "", "access-team", "uat", "none", "",
        "mariadb", "access_db", host, str(port), database, user, password, *tls,
        "y", "n", "n",  # create the store; no connection test; no import
    ]  # fmt: skip
    assert CliRunner().invoke(cli.app, ["init"], input="\n".join(first) + "\n").exit_code == 0

    # owl serve, as it is built for this folder, running from here on.
    engine = create_store_engine(store)
    runs = RunService(load_applications(folder), engine)
    statuses = ConnectorStatuses(runs.applications, CircuitBreaker(), runs.kill.reason)
    reloader = ConfigReloader(folder, ConfigFolder(folder), runs)
    api = local_client(create_app(runs, connectors=statuses, reloader=reloader))
    test = "/api/applications/access/connectors/visits_db/test?environment=uat"
    assert api.post(test).status_code == 404  # not loaded yet

    second = [
        "visits_db", "mariadb",
        "y", host, str(port), database, user, password, *tls,
        "n", "n",  # no connection test; no import
    ]  # fmt: skip
    result = CliRunner().invoke(
        cli.app, ["connector", "add", "--config", str(folder)], input="\n".join(second) + "\n"
    )
    assert result.exit_code == 0, result.output
    assert "owl reload" in result.output

    reloaded = api.post("/api/config/reload")
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json()["connectors_added"] == ["access/visits_db"]
    reached = api.post(test)
    assert reached.status_code == 200, reached.text
    assert reached.json()["reachable"] is True, reached.json()
    runs.shutdown()
    engine.dispose()
