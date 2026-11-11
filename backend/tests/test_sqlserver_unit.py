# Author: @SaadZarook
"""SQL Server adapter behaviour that needs no server."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import mssql_python
import pytest
from mssql_python.exceptions import ConnectionStringParseError

from probeowl.connectors.failures import Failure
from probeowl.connectors.sql import SqlLimits
from probeowl.connectors.sqlserver import SqlServerAdapter, connection_string, input_size
from probeowl.errors import ConfigError, GuardViolation
from probeowl.model import SqlBinding


def binding(**changes: Any) -> SqlBinding:
    data: dict[str, Any] = {
        "host": "wallet-sql-ro",
        "database": "wallet",
        "user": "probeowl_ro",
        "secret": "env:PW",
    }
    return SqlBinding.model_validate(data | changes)


def test_connection_string_encrypts_and_names_the_app() -> None:
    assert connection_string(binding(), "pw") == (
        "Server=wallet-sql-ro,1433;Database=wallet;UID=probeowl_ro;PWD=pw;"
        "Encrypt=yes;TrustServerCertificate=no"
    )


def test_a_replica_binding_asks_for_read_only_intent() -> None:
    assert "ApplicationIntent=ReadOnly" in connection_string(binding(replica=True), "pw")


def test_trust_server_certificate_is_only_on_when_the_binding_says() -> None:
    assert "TrustServerCertificate=yes" in connection_string(
        binding(trust_server_certificate=True, port=14330), "pw"
    )


@pytest.mark.parametrize("password", ["a;b", "a}b", "{ab", " padded "])
def test_a_password_with_special_characters_is_braced(password: str) -> None:
    text = connection_string(binding(), password)
    braced = "{" + password.replace("}", "}}") + "}"
    assert f"PWD={braced};" in text


@pytest.mark.parametrize(
    ("value", "declared", "expected_type"),
    [
        ("tp_91f2c7", None, mssql_python.SQL_VARCHAR),
        ("tp_91f2c7", "varchar(20)", mssql_python.SQL_VARCHAR),
        ("Zoë", "nvarchar(50)", mssql_python.SQL_WVARCHAR),
        ("Zoë", "NCHAR(10)", mssql_python.SQL_WVARCHAR),
        (None, None, mssql_python.SQL_VARCHAR),
        (True, None, mssql_python.SQL_BIT),
        (42, None, mssql_python.SQL_BIGINT),
        (1.5, None, mssql_python.SQL_DOUBLE),
        (Decimal("500.00"), None, mssql_python.SQL_DECIMAL),
        (datetime(2026, 10, 31, 9, 0), None, mssql_python.SQL_TYPE_TIMESTAMP),
        (date(2026, 10, 31), None, mssql_python.SQL_TYPE_DATE),
    ],
)
def test_parameters_are_sent_with_a_type_that_keeps_the_index(
    value: Any, declared: str | None, expected_type: int
) -> None:
    assert input_size(value, declared)[0] == expected_type


def test_a_decimal_keeps_its_scale() -> None:
    assert input_size(Decimal("500.25"), None) == (mssql_python.SQL_DECIMAL, 38, 2)


def test_the_guard_runs_before_any_connection() -> None:
    # The host does not exist: a GuardViolation (not a connection error) proves no attempt.
    adapter = SqlServerAdapter(binding(host="no-such-host.invalid"), "pw")
    with pytest.raises(GuardViolation, match="only a SELECT"):
        adapter.lookup("DELETE FROM topups WHERE id = :id", {"id": "x"}, max_rows=10)
    with pytest.raises(GuardViolation, match="names database payroll"):
        adapter.lookup("SELECT * FROM payroll.dbo.salaries", {}, max_rows=10)


def classify(message: str) -> Failure:
    error = mssql_python.Error("Statement execution failed", message)
    return SqlServerAdapter(binding(), "pw")._classify(error)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "DDBC Error: [SQL Server]Lock request time out period exceeded.",
            Failure.LOCK_NOT_GRANTED,
        ),
        ("DDBC Error: [HYT00] Query timeout expired", Failure.TIMED_OUT),
        ("[SQL Server]Login failed for user 'probeowl_ro'.", Failure.AUTHENTICATION),
        ("[HYT00] Login timeout expired", Failure.CONNECTION),
        ("TCP Provider: Error code 0x2749", Failure.CONNECTION),
        ("[SQL Server]Invalid object name 'topups'.", Failure.REFUSED),
        ("something new", Failure.UNKNOWN),
    ],
)
def test_driver_errors_have_a_category(message: str, expected: Failure) -> None:
    assert classify(message) is expected


def test_a_connection_string_the_driver_rejects_is_a_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject(*args: Any, **kwargs: Any) -> None:
        raise ConnectionStringParseError(["Error parsing value for keyword 'pwd': s3cret"])

    monkeypatch.setattr(mssql_python, "connect", reject)
    adapter = SqlServerAdapter(binding(), "s3cret")
    with pytest.raises(ConfigError, match="does not form a valid SQL Server connection") as failure:
        adapter.lookup("SELECT id FROM topups WHERE id = :id", {"id": "x"}, max_rows=1)
    assert "s3cret" not in str(failure.value)


def test_the_statement_timeout_is_the_query_timeout_not_the_login_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # mssql-python's connect(timeout=) is the login timeout; a lookup needs the query timeout.
    opened: list[Any] = []

    class FakeConnection:
        timeout = 0

        def cursor(self) -> Any:
            return type("Cursor", (), {"execute": lambda *a: None, "close": lambda *a: None})()

    def connect(*args: Any, **kwargs: Any) -> FakeConnection:
        opened.append(kwargs)
        return FakeConnection()

    monkeypatch.setattr(mssql_python, "connect", connect)
    connection = SqlServerAdapter(binding(), "pw", SqlLimits(statement_timeout_s=3))._connect()
    assert connection.timeout == 3
    assert opened[0]["timeout"] == 5  # the login timeout


def test_encryption_is_never_turned_off() -> None:
    # mssql-python (ODBC Driver 18) encrypts and verifies by default; the string says so too.
    for changes in ({}, {"trust_server_certificate": True}, {"replica": True}):
        text = connection_string(binding(**changes), "pw")
        assert "Encrypt=yes" in text
        assert "Encrypt=no" not in text and "Encrypt=optional" not in text.lower()


def test_an_untrusted_certificate_has_its_own_category() -> None:
    assert (
        classify(
            "TCP Provider: SSL Provider: [error:0A000086:SSL routines::certificate verify failed]"
        )
        is Failure.TLS_NOT_TRUSTED
    )
