# Author: @SaadZarook
"""Oracle adapter behaviour that needs no database."""

import ssl
from typing import Any

import oracledb
import pytest

from probeowl.connectors.failures import Failure
from probeowl.connectors.oracle import OracleAdapter
from probeowl.connectors.sql import SqlLimits
from probeowl.errors import ConfigError, GuardViolation, MissingGrant
from probeowl.model import SqlBinding


def binding(**changes: Any) -> SqlBinding:
    data: dict[str, Any] = {
        "host": "ledger-adg",
        "database": "FREEPDB1",
        "schema": "ledger",
        "user": "probeowl_ro",
        "secret": "env:PW",
    }
    return SqlBinding.model_validate(data | changes)


class FakeConnection:
    call_timeout = 0
    outputtypehandler: Any = None
    current_schema: str | None = None

    def __init__(self) -> None:
        self.executed: list[str] = []

    def cursor(self) -> Any:
        executed = self.executed

        class Cursor:
            def execute(self, sql: str) -> None:
                executed.append(sql)

            def fetchone(self) -> tuple[str] | None:
                # The session's time zone, as the database answers once it is set.
                return ("+00:00",) if executed[-1] == "SELECT SESSIONTIMEZONE FROM dual" else None

            def close(self) -> None:
                pass

        return Cursor()


def connect_with(
    monkeypatch: pytest.MonkeyPatch, adapter: OracleAdapter
) -> tuple[Any, dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def connect(**kwargs: Any) -> FakeConnection:
        calls.append(kwargs)
        return FakeConnection()

    monkeypatch.setattr(oracledb, "connect", connect)
    return adapter._connect(), calls[0]


def test_connects_in_thin_mode_to_the_service(monkeypatch: pytest.MonkeyPatch) -> None:
    _, kwargs = connect_with(monkeypatch, OracleAdapter(binding(), "pw"))
    assert kwargs == {
        "user": "probeowl_ro",
        "password": "pw",
        "host": "ledger-adg",
        "port": 1521,
        "service_name": "FREEPDB1",
        "tcp_connect_timeout": 5,
        "protocol": "tcps",
        "ssl_server_dn_match": True,
    }
    assert oracledb.is_thin_mode()


def test_the_call_timeout_is_set_in_milliseconds(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OracleAdapter(binding(), "pw", SqlLimits(statement_timeout_s=3))
    connection, _ = connect_with(monkeypatch, adapter)
    assert connection.call_timeout == 3000


def test_the_binding_schema_becomes_current_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _ = connect_with(monkeypatch, OracleAdapter(binding(), "pw"))
    assert connection.current_schema == "LEDGER"
    connection, _ = connect_with(monkeypatch, OracleAdapter(binding(schema=None), "pw"))
    assert connection.current_schema is None


@pytest.mark.parametrize("schema", ["ledger; drop user x", '"Ledger"', "1ledger", "led ger"])
def test_a_schema_that_is_not_a_plain_name_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch, schema: str
) -> None:
    with pytest.raises(ConfigError, match="not a plain Oracle name"):
        connect_with(monkeypatch, OracleAdapter(binding(schema=schema), "pw"))


def test_session_guards_name_the_schema_and_the_timeout() -> None:
    assert OracleAdapter(binding(), "pw")._session_guards() == [
        "readers never wait for writers (multi-version reads)",
        "call timeout 2 s",
        "ALTER SESSION DISABLE PARALLEL QUERY",
        "CURRENT_SCHEMA LEDGER",
        "TIME_ZONE DBTIMEZONE (the database's own)",
    ]


def test_the_guard_runs_before_any_connection() -> None:
    adapter = OracleAdapter(binding(host="no-such-host.invalid"), "pw")
    with pytest.raises(GuardViolation, match="locking clause"):
        adapter.lookup("SELECT * FROM ledger_entries WHERE txn_id = :id FOR UPDATE", {"id": "x"}, 1)
    with pytest.raises(GuardViolation, match="database links"):
        adapter.lookup("SELECT * FROM ledger_entries@remote", {}, 1)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("DPY-4024: call timeout of 2000 ms exceeded", Failure.TIMED_OUT),
        ("ORA-01017: invalid credential or not authorized", Failure.AUTHENTICATION),
        ("ORA-12541: no listener at host db port 1521", Failure.CONNECTION),
        ("ORA-00942: table or view does not exist", Failure.REFUSED),
        ("DPY-9999: something new", Failure.UNKNOWN),
    ],
)
def test_driver_errors_have_a_category(message: str, expected: Failure) -> None:
    assert OracleAdapter(binding(), "pw")._classify(oracledb.Error(message)) is expected


def test_every_connection_disables_parallel_query(monkeypatch: pytest.MonkeyPatch) -> None:
    connection, _ = connect_with(monkeypatch, OracleAdapter(binding(), "pw"))
    assert connection.executed[0] == "ALTER SESSION DISABLE PARALLEL QUERY"


def test_every_connection_sets_the_time_zone_never_this_machines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = OracleAdapter(binding(), "pw")
    connection, _ = connect_with(monkeypatch, adapter)
    assert connection.executed[1:] == [
        "ALTER SESSION SET TIME_ZONE = DBTIMEZONE",
        "SELECT SESSIONTIMEZONE FROM dual",
    ]
    # Once connected, evidence says the zone the session got.
    assert "TIME_ZONE +00:00 (the database's own)" in adapter._session_guards()
    connection, _ = connect_with(monkeypatch, OracleAdapter(binding(time_zone="+04:00"), "pw"))
    assert "ALTER SESSION SET TIME_ZONE = '+04:00'" in connection.executed


def test_trust_server_certificate_still_connects_over_tcps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = OracleAdapter(binding(trust_server_certificate=True), "pw")
    _, kwargs = connect_with(monkeypatch, adapter)
    assert kwargs["protocol"] == "tcps"
    assert kwargs["ssl_server_dn_match"] is False
    assert kwargs["ssl_context"].verify_mode == ssl.CERT_NONE


def test_encrypt_false_connects_over_plain_tcp(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = OracleAdapter(binding(encrypt=False, encrypt_reason="local container"), "pw")
    _, kwargs = connect_with(monkeypatch, adapter)
    assert kwargs["protocol"] == "tcp"
    assert "ssl_context" not in kwargs


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "DPY-6005: cannot connect to database. [SSL: CERTIFICATE_VERIFY_FAILED]",
            Failure.TLS_NOT_TRUSTED,
        ),
        ("DPY-6005: cannot connect to database. reset", Failure.TLS_NOT_OFFERED),
    ],
)
def test_tls_failures_have_their_own_category(message: str, expected: Failure) -> None:
    assert OracleAdapter(binding(), "pw")._classify(oracledb.Error(message)) is expected


def test_a_connection_failure_without_tls_is_not_about_tls() -> None:
    adapter = OracleAdapter(binding(encrypt=False), "pw")
    failure = adapter._classify(oracledb.Error("DPY-6005: cannot connect to database."))
    assert failure is Failure.CONNECTION


@pytest.mark.parametrize(
    ("refusal", "grant"),
    [
        ('ORA-00942: table or view "SYS"."V_$SESSION" does not exist', "SELECT on SYS.V_$SESSION"),
        ('ORA-00942: table or view "SYS"."GV_$LOCK" does not exist', "SELECT on SYS.GV_$LOCK"),
        # Any other table may simply not exist: no grant is named for it.
        ('ORA-00942: table or view "LEDGER"."ENTRIES" does not exist', None),
        ("ORA-00904: invalid identifier", None),
    ],
)
def test_a_refused_sys_view_names_the_grant_it_needs(refusal: str, grant: str | None) -> None:
    adapter = OracleAdapter(binding(), "pw")
    assert adapter._missing_grant(Exception(refusal)) == grant
    error = adapter._error(Exception(refusal))
    if grant is None:
        assert not isinstance(error, MissingGrant)
    else:
        assert isinstance(error, MissingGrant) and error.grant == grant
        assert str(error) == f"missing {grant}: a DBA grants it to the binding's login"
