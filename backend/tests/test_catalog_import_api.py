# Author: @SaadZarook
"""Schema imports through the API and `owl catalog import`: stored, audited, never data."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from probeowl.api.app import create_app
from probeowl.catalog.service import CatalogService
from probeowl.cli import main as cli
from probeowl.cli import server
from probeowl.errors import ConnectorError
from probeowl.model.connector import SqlConnector
from probeowl.model.schema import ColumnSchema, IndexSchema, TableSchema
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.local_client import local_client

TOPUPS = TableSchema(
    schema="dbo",
    table="topups",
    columns=[
        ColumnSchema(name="id", type="varchar(20)", nullable=False),
        ColumnSchema(name="created_at", type="datetime2", nullable=False),
    ],
    primary_key=["id"],
    indexes=[IndexSchema(name="PK_topups", columns=["id"], unique=True)],
    approx_rows=1200,
)


class Database:
    """A connector's database as an import sees it."""

    def __init__(self, tables: list[TableSchema], error: Exception | None = None):
        self.tables, self.error = tables, error
        self.opened: list[tuple[str, str]] = []
        self.closed = 0

    def import_schema(self) -> tuple[list[TableSchema], str | None]:
        if self.error is not None:
            raise self.error
        return self.tables, None

    def close(self) -> None:
        self.closed += 1


def client(setup: Setup, database: Database) -> TestClient:
    def opener(connector: SqlConnector, environment: str) -> Database | None:
        database.opened.append((connector.connector, environment))
        return database if environment in connector.environments else None

    imports = CatalogService(setup.applications, setup.store, opener, actor_name="s.khan")
    return local_client(create_app(setup.service(), imports))


def post(api: TestClient, **body: str) -> Any:
    data = {"connector": "wallet_db", "environment": "prod"} | body
    return api.post("/api/applications/wallet/catalog/imports", json=data)


def test_an_import_is_stored_audited_and_served(setup: Setup) -> None:
    database = Database([TOPUPS])
    api = client(setup, database)
    response = post(api)
    assert response.status_code == 201, response.text
    snapshot = response.json()
    assert (snapshot["connector"], snapshot["environment"]) == ("wallet_db", "prod")
    assert snapshot["tables"][0]["schema"] == "dbo"
    assert snapshot["tables"][0]["indexes"] == [
        {"name": "PK_topups", "columns": ["id"], "unique": True}
    ]
    assert database.opened == [("wallet_db", "prod")] and database.closed == 1

    latest = api.get(
        "/api/applications/wallet/catalog/schema",
        params={"connector": "wallet_db", "environment": "prod"},
    ).json()
    assert latest["tables"] == snapshot["tables"]

    with setup.store.connect() as conn:
        [event] = [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "schema_imported"]
    assert (event.actor.name, event.application, event.environment) == ("s.khan", "wallet", "prod")
    assert event.details == {"connector": "wallet_db", "tables": 1, "truncated": None}


def test_the_latest_import_is_served(setup: Setup) -> None:
    database = Database([TOPUPS])
    api = client(setup, database)
    post(api)
    database.tables = [TOPUPS, TOPUPS.model_copy(update={"table": "provider_webhooks"})]
    post(api)
    latest = api.get(
        "/api/applications/wallet/catalog/schema",
        params={"connector": "wallet_db", "environment": "prod"},
    ).json()
    assert [t["table"] for t in latest["tables"]] == ["topups", "provider_webhooks"]


def test_before_any_import_the_schema_is_a_404_saying_how(setup: Setup) -> None:
    response = client(setup, Database([])).get(
        "/api/applications/wallet/catalog/schema",
        params={"connector": "ledger_db", "environment": "uat"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == (
        "ledger_db has not been imported in uat; "
        "run: owl catalog import ledger_db --application wallet --env uat"
    )


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        ({"connector": "nope"}, 404, "wallet has no connector named nope"),
        ({"environment": "staging"}, 404, "wallet has no environment staging"),
        ({"connector": "checkout_api"}, 422, "checkout_api is a REST connector"),
    ],
)
def test_requests_that_cannot_import_are_refused_before_any_connection(
    setup: Setup, body: dict[str, str], status: int, detail: str
) -> None:
    database = Database([TOPUPS])
    response = post(client(setup, database), **body)
    assert response.status_code == status
    assert response.json()["detail"].startswith(detail)
    assert database.opened == []


def test_a_database_that_cannot_be_read_is_a_502_and_nothing_is_stored(setup: Setup) -> None:
    database = Database([], ConnectorError("the statement timed out after 30 s"))
    api = client(setup, database)
    response = post(api)
    assert response.status_code == 502
    assert response.json()["detail"] == "the statement timed out after 30 s"
    assert database.closed == 1
    assert (
        api.get(
            "/api/applications/wallet/catalog/schema",
            params={"connector": "wallet_db", "environment": "prod"},
        ).status_code
        == 404
    )


def test_the_catalog_is_the_confirmed_yaml(setup: Setup) -> None:
    catalog = client(setup, Database([])).get("/api/applications/wallet/catalog").json()
    assert catalog["catalog"] == "wallet"
    assert {t["table"] for t in catalog["tables"]} >= {"topups", "ledger_entries"}


def test_owl_catalog_import_prints_each_table(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = client(setup, Database([TOPUPS]))
    monkeypatch.setattr(server, "client", lambda url: api)
    result = CliRunner().invoke(
        cli.app, ["catalog", "import", "wallet_db", "--application", "wallet", "--env", "prod"]
    )
    assert result.exit_code == 0, result.output
    assert "Imported wallet_db in prod: 1 tables" in result.output
    assert "dbo.topups" in result.output
    assert "2 columns, primary key id, 1 indexes, ~1200 rows" in result.output


def test_owl_catalog_import_shows_the_problem(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = client(setup, Database([TOPUPS]))
    monkeypatch.setattr(server, "client", lambda url: api)
    result = CliRunner().invoke(
        cli.app, ["catalog", "import", "checkout_api", "--application", "wallet", "--env", "prod"]
    )
    assert result.exit_code == 2
    assert "checkout_api is a REST connector" in result.output
