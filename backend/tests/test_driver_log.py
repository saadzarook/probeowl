# Author: @SaadZarook
"""What the server log may hold of a driver's error, and who can read the file."""

import logging
import os
import stat
import sys
from collections.abc import Iterator
from pathlib import Path

import pymysql
import pytest

from probeowl.connectors.failures import driver_log_text, show_raw_driver_errors
from probeowl.connectors.mariadb import MariaDbAdapter
from probeowl.errors import ConnectorError
from probeowl.model import SqlBinding
from probeowl.server_log import configure_server_log
from tests.test_sql_count import LOOKUP, Unreachable


@pytest.fixture(autouse=True)
def default_mode() -> Iterator[None]:
    yield
    show_raw_driver_errors(False)
    logger = logging.getLogger("probeowl")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


@pytest.mark.parametrize(
    ("text", "logged"),
    [
        (
            "[SQL Server]Conversion failed when converting the varchar value 'VP00012931'.",
            "[SQL Server]Conversion failed when converting the varchar value [masked].",
        ),
        ('Unknown column "email" in list', "Unknown column [masked] in list"),
        ("Unknown table `cards`", "Unknown table [masked]"),
        ("value 'never closed", "value [masked]"),
        ("ORA-00942: table or view does not exist", "ORA-00942: table or view does not exist"),
    ],
)
def test_quoted_values_are_masked_and_the_code_stays(text: str, logged: str) -> None:
    assert driver_log_text(text, text, "pw") == logged


def test_a_password_is_removed_quoted_or_not() -> None:
    logged = driver_log_text("login with hunter2 refused", "login with hunter2 refused", "hunter2")
    assert logged == "login with [redacted] refused"


def test_parameter_values_are_masked_even_unquoted() -> None:
    text = "no match for VP00012931 near VP000129310 or 7"
    assert driver_log_text(text, text, "pw", ["VP00012931", 7, None]) == (
        "no match for [masked] near VP000129310 or [masked]"
    )


def test_a_lookup_failure_logs_no_parameter_value(caplog: pytest.LogCaptureFixture) -> None:
    adapter = Unreachable("cannot use VP00012931 for 'a value'")
    with pytest.raises(ConnectorError):
        adapter.lookup(LOOKUP, {"v": "VP00012931"}, 5)
    assert "VP00012931" not in caplog.text
    assert "a value" not in caplog.text
    assert "cannot use [masked] for [masked]" in caplog.text


def test_mariadb_keeps_its_code_and_message_apart_from_the_tuple() -> None:
    binding = SqlBinding(host="db", database="d", user="ro", secret="env:PW")
    adapter = MariaDbAdapter(binding, "pw")
    error = pymysql.err.OperationalError(1045, "Access denied for user 'ro'@'10.0.0.1'")
    assert adapter._driver_text(error) == "1045 Access denied for user 'ro'@'10.0.0.1'"


def test_raw_text_needs_the_flag_and_still_loses_the_password() -> None:
    masked = "value [masked]"
    text = "value 'VP00012931' with hunter2"
    assert driver_log_text("value 'VP00012931'", "value 'VP00012931'", "hunter2") == masked
    show_raw_driver_errors(True)
    assert driver_log_text("value 'VP00012931'", text, "hunter2") == (
        "value 'VP00012931' with [redacted]"
    )


def test_the_log_goes_to_a_file(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "owl-serve.log"
    configure_server_log(path)
    logging.getLogger("probeowl.connectors.sql").warning("failed: %s", "connection refused")
    assert "failed: connection refused" in path.read_text(encoding="utf-8")


def test_configuring_again_does_not_log_twice(tmp_path: Path) -> None:
    path = tmp_path / "owl-serve.log"
    configure_server_log(path)
    configure_server_log(path)
    logging.getLogger("probeowl").warning("once")
    assert path.read_text(encoding="utf-8").count("once") == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes; Windows uses the profile's ACL")
def test_the_file_is_readable_only_by_its_owner(tmp_path: Path) -> None:
    created = tmp_path / "new.log"
    configure_server_log(created)
    assert stat.S_IMODE(os.stat(created).st_mode) == 0o600

    existing = tmp_path / "wide.log"
    existing.write_text("", encoding="utf-8")
    existing.chmod(0o644)
    configure_server_log(existing)
    assert stat.S_IMODE(os.stat(existing).st_mode) == 0o600
