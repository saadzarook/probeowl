# Author: @SaadZarook
"""A SQL location keyed from a field a REST location returned, with both adapters real: an HTTP
API served here, and a real MariaDB (CI's container, or the native server in verify.sh)."""

import json
import threading
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pymysql
import pytest
from sqlalchemy.engine import make_url

from probeowl.connectors.factory import open_connector
from probeowl.engine.runner import run_probe
from probeowl.model import Entity, Outcome, Probe, RunStatus
from probeowl.model.connector import RestConnector, SqlConnector
from tests.store_databases import store_database_url

pytestmark = pytest.mark.database


class _Payments(BaseHTTPRequestHandler):
    """GET /payments/<ref>: the provider's payment, naming the ledger entry it was booked as."""

    def do_GET(self) -> None:
        found = self.path == "/payments/pay_77"
        body = json.dumps({"status": "captured", "ledger_id": "LT-9"} if found else {"error": "no"})
        self.send_response(200 if found else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format: str, *args: Any) -> None:
        return None


@pytest.fixture
def api() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Payments)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def ledger() -> Iterator[tuple[dict[str, Any], str, str]]:
    """A ledger table of the test's own: the binding, its password and the table's name."""
    with store_database_url("mariadb") as url:
        parsed = make_url(url)
        host, port = parsed.host or "127.0.0.1", parsed.port or 3306
        user, password, database = parsed.username or "", parsed.password or "", parsed.database
        assert database is not None
        table = f"ledger_{uuid.uuid4().hex[:8]}"
        binding = {
            "host": host,
            "port": port,
            "database": database,
            "user": user,
            "secret": "env:PROBEOWL_UNUSED",  # the test hands the password over itself
            "trust_server_certificate": True,
        }
        conn = pymysql.connect(
            host=host, port=port, user=user, password=password, database=database
        )
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE TABLE {table} (txn_id VARCHAR(12) NOT NULL, amount INT NOT NULL, "
                    "INDEX (txn_id))"
                )
                cursor.execute(
                    f"INSERT INTO {table} VALUES ('LT-9', 10), ('LT-9', -10), ('LT-1', 5)"
                )
            conn.commit()
            yield binding, password, table
        finally:
            with conn.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {table}")
            conn.close()


def test_mariadb_a_ledger_is_looked_up_by_the_id_a_rest_api_returned(
    api: str, ledger: tuple[dict[str, Any], str, str]
) -> None:
    binding, password, table = ledger
    entity = Entity.model_validate(
        {
            "entity": "payment",
            "root_key": "provider_ref",
            "identifiers": {"provider_ref": {"unique": True}},
            "locations": {
                "payment": {
                    "using": "checkout_api",
                    "lookup": "GET /payments/{{provider_ref}}",
                    "exposes": ["status", "ledger_id"],
                },
                "ledger": {
                    "using": "ledger_db",
                    "key_from": "payment.ledger_id",
                    "lookup": f"SELECT txn_id, amount FROM {table} WHERE txn_id = :ledger_id",
                    "cardinality": "many",
                    "max_rows": 10,
                    "exposes": ["txn_id", "amount"],
                },
            },
        }
    )
    probe = Probe.model_validate(
        {
            "probe": "booked",
            "kind": "inspect",
            "entity": "payment",
            "owner": "payments-team",
            "expect": ["count(ledger) == 2", "sum(ledger.amount) == 0"],
        }
    )
    rest = RestConnector.model_validate(
        {"connector": "checkout_api", "type": "rest", "environments": {"uat": {"base_url": api}}}
    )
    sql = SqlConnector.model_validate(
        {"connector": "ledger_db", "type": "mariadb", "environments": {"uat": binding}}
    )
    connectors = {
        "checkout_api": open_connector(rest, "uat"),
        "ledger_db": open_connector(sql, "uat", secrets=lambda _ref: password),
    }
    try:
        result = run_probe(probe, entity, "pay_77", connectors)
    finally:
        for connector in connectors.values():
            if connector is not None:
                connector.close()
    found = {r.location: r for r in result.locations}
    assert found["payment"].outcome is Outcome.FOUND
    assert found["ledger"].outcome is Outcome.FOUND, found["ledger"].reason
    evidence = found["ledger"].evidence
    assert evidence is not None
    assert evidence.parameters["ledger_id"] == "LT-9"  # the ID the API returned
    assert sorted(str(r["amount"]) for r in evidence.rows) == ["-10", "10"]
    assert result.status is RunStatus.PASSED
