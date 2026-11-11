# Author: @SaadZarook
"""MariaDB adapter behaviour that needs no server."""

import ssl
from typing import Any

import pymysql
import pytest

from probeowl.connectors.failures import Failure
from probeowl.connectors.mariadb import MariaDbAdapter
from probeowl.connectors.sql import SqlLimits
from probeowl.errors import GuardViolation
from probeowl.model import SqlBinding


def binding(**changes: Any) -> SqlBinding:
    data: dict[str, Any] = {
        "host": "access-ro",
        "database": "access",
        "user": "probeowl_ro",
        "secret": "env:PW",
    }
    return SqlBinding.model_validate(data | changes)


class FakeConnection:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def cursor(self) -> Any:
        executed = self.executed

        class Cursor:
            def execute(self, sql: str) -> None:
                executed.append(sql)

            def close(self) -> None:
                pass

        return Cursor()


def connect_with(
    monkeypatch: pytest.MonkeyPatch, adapter: MariaDbAdapter
) -> tuple[Any, dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def connect(**kwargs: Any) -> FakeConnection:
        calls.append(kwargs)
        return FakeConnection()

    monkeypatch.setattr(pymysql, "connect", connect)
    return adapter._connect(), calls[0]


def test_connects_with_timeouts_and_never_with_several_statements_at_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, kwargs = connect_with(
        monkeypatch, MariaDbAdapter(binding(), "pw", SqlLimits(statement_timeout_s=3))
    )
    assert kwargs["host"] == "access-ro"
    assert kwargs["port"] == 3306
    assert kwargs["database"] == "access"
    assert kwargs["autocommit"] is True
    assert kwargs["connect_timeout"] == 5
    assert kwargs["read_timeout"] == 8  # the server stops it at 3 s; the client 5 s later
    # Several statements in one call stay off: only the guard's single statement is sent.
    assert "client_flag" not in kwargs
    assert "init_command" not in kwargs


def test_each_session_setting_runs_on_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _ = connect_with(monkeypatch, MariaDbAdapter(binding(), "pw"))
    assert connection.executed == [
        "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED, READ ONLY",
        "SET SESSION max_statement_time = 2",
        "SET SESSION lock_wait_timeout = 1",
        "SET SESSION innodb_lock_wait_timeout = 1",
    ]


def test_session_guards_for_evidence() -> None:
    assert MariaDbAdapter(binding(), "pw")._session_guards() == [
        "READ COMMITTED, READ ONLY",
        "max_statement_time 2 s",
        "lock_wait_timeout 1 s",
        "innodb_lock_wait_timeout 1 s",
    ]


@pytest.mark.parametrize(
    ("sql", "match"),
    [
        ("SELECT * FROM cards WHERE card_id = :id LOCK IN SHARE MODE", "locking clause"),
        ("SELECT * FROM cards WHERE card_id = :id FOR UPDATE", "locking clause"),
        ("SELECT GET_LOCK('probe', 10)", "GET_LOCK"),
        ("SELECT * FROM mysql.user", "names database mysql"),
        ("SELECT /*+ MAX_EXECUTION_TIME(100000) */ * FROM cards", "optimizer hint"),
    ],
)
def test_the_guard_runs_before_any_connection(sql: str, match: str) -> None:
    adapter = MariaDbAdapter(binding(host="no-such-host.invalid"), "pw")
    with pytest.raises(GuardViolation, match=match):
        adapter.lookup(sql, {"id": "C204518"}, 1)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (1205, Failure.LOCK_NOT_GRANTED),
        (1969, Failure.TIMED_OUT),
        (1045, Failure.AUTHENTICATION),
        (2003, Failure.CONNECTION),
        (1146, Failure.REFUSED),
    ],
)
def test_driver_errors_have_a_category(code: int, expected: Failure) -> None:
    error = pymysql.err.OperationalError(code, "server message")
    assert MariaDbAdapter(binding(), "pw")._classify(error) is expected


def test_tls_is_required_and_the_certificate_verified_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, kwargs = connect_with(monkeypatch, MariaDbAdapter(binding(), "pw"))
    # Any of these makes PyMySQL require TLS; without them it only prefers it.
    assert kwargs["ssl_verify_cert"] is True
    assert kwargs["ssl_verify_identity"] is True
    assert "ssl_disabled" not in kwargs


def test_trust_server_certificate_still_requires_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = MariaDbAdapter(binding(trust_server_certificate=True), "pw")
    _, kwargs = connect_with(monkeypatch, adapter)
    context = kwargs["ssl"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_NONE
    assert not context.check_hostname


def test_encrypt_false_turns_tls_off(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = MariaDbAdapter(binding(encrypt=False, encrypt_reason="local container"), "pw")
    _, kwargs = connect_with(monkeypatch, adapter)
    assert kwargs["ssl_disabled"] is True
    assert not any(key.startswith("ssl_verify") or key == "ssl" for key in kwargs)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            pymysql.err.OperationalError(2003, "[SSL: CERTIFICATE_VERIFY_FAILED] self-signed"),
            Failure.TLS_NOT_TRUSTED,
        ),
        (
            pymysql.err.OperationalError(2026, "SSL is required but the server doesn't support it"),
            Failure.TLS_NOT_OFFERED,
        ),
    ],
)
def test_tls_failures_have_their_own_category(error: Exception, expected: Failure) -> None:
    assert MariaDbAdapter(binding(), "pw")._classify(error) is expected
