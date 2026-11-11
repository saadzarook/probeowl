# Author: @SaadZarook
"""Times on their way to and from each database: the exact instant, never this machine's zone."""

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import oracledb
import pytest
from pydantic import ValidationError

from probeowl.connectors.mariadb import MariaDbAdapter
from probeowl.connectors.oracle import (
    OracleAdapter,
    bound_value,
    input_type,
    time_zone_setting,
    time_zone_shown,
)
from probeowl.errors import ConnectorError
from probeowl.model import SqlBinding
from probeowl.model.connector import SqlConnector

PLUS_FOUR = timezone(timedelta(hours=4))


def binding(**changes: Any) -> SqlBinding:
    return SqlBinding.model_validate(
        {"host": "127.0.0.1", "database": "FREEPDB1", "user": "probe", "secret": "env:PW"} | changes
    )


def connector(kind: str, **changes: Any) -> SqlConnector:
    return SqlConnector.model_validate(
        {
            "connector": "ledger_db",
            "type": kind,
            "environments": {"prod": binding(**changes).model_dump(by_alias=True)},
        }
    )


# --- The binding's time zone -------------------------------------------------------------------


@pytest.mark.parametrize("offset", ["+04:00", "-05:30", "+00:00", "+14:00", "-12:00", "+13:45"])
def test_an_offset_is_a_time_zone(offset: str) -> None:
    assert binding(time_zone=offset).time_zone == offset


@pytest.mark.parametrize(
    "value",
    # Region names are not accepted for now, and nothing else reaches ALTER SESSION.
    ["Asia/Dubai", "UTC", "+4:00", "+14:30", "-12:30", "+04", "'+04:00'", "+04:00'; DROP"],
)
def test_anything_but_an_offset_is_refused(value: str) -> None:
    with pytest.raises(ValidationError):
        binding(time_zone=value)


@pytest.mark.parametrize("kind", ["sqlserver", "mariadb"])
def test_only_oracle_takes_a_time_zone(kind: str) -> None:
    with pytest.raises(ValidationError, match="time_zone applies only to oracle"):
        connector(kind, time_zone="+04:00")
    assert connector("oracle", time_zone="+04:00")


def test_the_session_is_set_to_the_bindings_offset_or_the_databases_own() -> None:
    assert time_zone_setting(binding()) == ("DBTIMEZONE", "the database's own")
    assert time_zone_setting(binding(time_zone="+04:00")) == ("'+04:00'", "from the binding")
    assert time_zone_shown(binding()) == "the database's own (DBTIMEZONE)"
    assert time_zone_shown(binding(time_zone="-05:30")) == "-05:30 (from the binding)"


def test_the_guards_say_the_time_zone_before_a_connection_is_open() -> None:
    guards = OracleAdapter(binding(time_zone="+04:00"), "").guards()
    assert "TIME_ZONE '+04:00' (from the binding)" in guards
    assert "TIME_ZONE DBTIMEZONE (the database's own)" in OracleAdapter(binding(), "").guards()


# --- Oracle: a time with an offset is sent as its instant --------------------------------------


def test_a_time_with_an_offset_is_sent_as_its_utc_instant() -> None:
    sent = bound_value(datetime(2026, 11, 7, 14, 0, tzinfo=PLUS_FOUR))
    assert isinstance(sent, datetime)
    assert sent == datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    assert sent.utcoffset() == timedelta(0)
    assert input_type(sent) is oracledb.DB_TYPE_TIMESTAMP_TZ


def test_a_time_without_an_offset_and_other_values_are_sent_as_they_are() -> None:
    plain = datetime(2026, 11, 7, 14, 0)
    assert bound_value(plain) is plain
    assert bound_value("tp_91f2c7") == "tp_91f2c7"


# --- MariaDB: a time with an offset needs the lookup to convert it ------------------------------


def mariadb() -> MariaDbAdapter:
    return MariaDbAdapter(binding(database="access"), "")


AT = {"at": datetime(2026, 11, 7, 14, 0, tzinfo=PLUS_FOUR)}


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id FROM visits WHERE at = :at",
        "SELECT id FROM visits WHERE at >= DATE_SUB(:at, INTERVAL 1 DAY)",
        # Converted in one place but not the other is still sent as its clock time there.
        "SELECT id FROM visits WHERE at = CAST(:at AS DATETIME) OR seen = :at",
    ],
)
def test_mariadb_refuses_a_time_with_an_offset_the_lookup_does_not_convert(sql: str) -> None:
    with pytest.raises(ConnectorError, match="MariaDB has no type with one"):
        mariadb()._refuse_values(sql, AT)


@pytest.mark.parametrize(
    ("sql", "parameters"),
    [
        ("SELECT id FROM visits WHERE at = CAST(:at AS DATETIME)", AT),
        ("SELECT id FROM visits WHERE at = :at", {"at": datetime(2026, 11, 7, 14, 0)}),
        ("SELECT id FROM visits WHERE id = :at", {"at": "v_1"}),
    ],
)
def test_mariadb_passes_a_converted_time_and_anything_without_an_offset(
    sql: str, parameters: dict[str, object]
) -> None:
    mariadb()._refuse_values(sql, parameters)


def test_mariadb_refuses_before_connecting() -> None:
    # No server here: the refusal comes first, so nothing is sent.
    with pytest.raises(ConnectorError, match=r"CAST\(:at AS DATETIME\)"):
        mariadb().lookup("SELECT id FROM visits WHERE at = :at", AT, 1)
