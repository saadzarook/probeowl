# Author: @SaadZarook
"""A known test password, sent through every route that takes one, is never seen again (#36).

The paths: a new binding's password, the missing-password prompt and Change password (all the
secret endpoint: new, 409, replace), Test connection on a draft (right and wrong password, and
a draft refused), a request refused for its missing header, and one refused for its length.
Then everything ProbeOwl keeps or answers is scanned: each response's body and headers, the
server log file owl serve writes, every log record at any level, the audit records, and the
store's database file byte for byte. ProbeOwl has no traces to scan.
"""

import logging
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from probeowl.api.app import create_app
from probeowl.api.local_only import HEADER
from probeowl.config_folder import ConfigFolder
from probeowl.connector_status import ConnectorStatuses
from probeowl.guards.breaker import CircuitBreaker
from probeowl.model.connector import RestConnector, SqlConnector
from probeowl.reload import ConfigReloader
from probeowl.secret_writes import SecretWrites
from probeowl.server_log import configure_server_log
from probeowl.setup_writes import SetupWrites
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import EXAMPLES, MemoryKeychain, Setup
from tests.local_client import local_client

TEST_PASSWORD = "leak-scan-password-5d0e"  # not a real password: a value to look for
SECRET = "/api/applications/wallet/connectors/ledger_db/environments/prod/secret"
DRAFT = "/api/applications/wallet/connectors/test-draft"
BINDING = {
    "host": "127.0.0.1",
    "port": 1,
    "database": "reports",
    "user": "probeowl_ro",
    "replica": True,
}


class Refusing:
    """A draft connection whose login fails, as a wrong password does."""

    def unreachable(self) -> str | None:
        return "authentication failed; check the binding's login and secret"

    def close(self) -> None:
        pass


@pytest.fixture
def server_log(tmp_path: Path) -> Iterator[Path]:
    """owl serve's own log file, at debug level, put back as it was afterwards."""
    path = tmp_path / "owl-serve.log"
    logger = logging.getLogger("probeowl")
    handlers, level = list(logger.handlers), logger.level
    configure_server_log(path)
    logger.setLevel(logging.DEBUG)
    yield path
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    for handler in handlers:
        logger.addHandler(handler)
    logger.setLevel(level)


def test_a_known_password_is_never_in_a_response_a_log_the_audit_or_the_store(
    setup: Setup,
    tmp_path: Path,
    server_log: Path,
    caplog: pytest.LogCaptureFixture,
    machine_keychain: MemoryKeychain,
) -> None:
    caplog.set_level(logging.DEBUG)
    runs = setup.service()

    def draft_opener(
        connector: SqlConnector | RestConnector, env: str, secrets: Callable[[str], str]
    ) -> Refusing:
        reference = connector.environments[env].secret
        assert reference is not None and secrets(reference) == TEST_PASSWORD  # it was used
        return Refusing()

    statuses = ConnectorStatuses(
        setup.applications, CircuitBreaker(), runs.kill.reason, draft_opener=draft_opener
    )
    copy = tmp_path / "config"  # a copy, so a write could never touch the checkout
    shutil.copytree(EXAMPLES, copy)
    folder = ConfigFolder(copy)
    reloader = ConfigReloader(folder.root, folder, runs)
    app = create_app(
        runs,
        connectors=statuses,
        reloader=reloader,
        setup=SetupWrites(setup.applications, setup.store, folder, reloader, statuses),
        secrets=SecretWrites(setup.applications, setup.store),
    )
    api = local_client(app)
    draft = {"connector": "reports_db", "type": "mariadb", "environment": "prod"}

    answers = [
        api.put(SECRET, json={"password": TEST_PASSWORD}),  # a new binding, or the prompt
        api.put(SECRET, json={"password": TEST_PASSWORD}),  # 409: one is saved already
        api.put(SECRET, json={"password": TEST_PASSWORD, "replace": True}),  # Change password
        api.put(SECRET, json={"password": TEST_PASSWORD * 50}),  # too long: 422
        api.post(DRAFT, json=draft | {"binding": BINDING, "password": TEST_PASSWORD}),
        # Production without a replica: refused before the password is used.
        api.post(
            DRAFT,
            json=draft | {"binding": BINDING | {"replica": False}, "password": TEST_PASSWORD},
        ),
        # No header: refused before any route.
        api.put(SECRET, json={"password": TEST_PASSWORD}, headers={HEADER: ""}),
    ]
    assert [a.status_code for a in answers] == [204, 409, 204, 422, 200, 422, 403]
    assert answers[4].json()["reachable"] is False
    # It did go through: the scan below would see it, had it leaked anywhere.
    assert machine_keychain.entries[("probeowl", "ledger_db.prod")] == TEST_PASSWORD

    for answer in answers:
        assert TEST_PASSWORD not in answer.text
        assert TEST_PASSWORD not in str(answer.headers)
    for handler in logging.getLogger("probeowl").handlers:
        handler.flush()
    assert server_log.exists()
    assert TEST_PASSWORD not in server_log.read_text(encoding="utf-8")
    assert TEST_PASSWORD not in caplog.text
    for record in caplog.records:
        assert TEST_PASSWORD not in repr(record.args)
    with setup.store.connect() as conn:
        audit = list_events(conn, LOCAL_WORKSPACE)
    assert {e.action for e in audit} >= {"secret_saved", "connection_tested"}
    assert TEST_PASSWORD not in repr(audit)
    setup.store.dispose()  # so every page of the file is on disk
    store_file = Path(str(setup.store.url.database))
    assert TEST_PASSWORD.encode() not in store_file.read_bytes()
