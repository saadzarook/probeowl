# Author: @SaadZarook
"""A value is bound as its real type: a date sent as text can silently flip day and month."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

import mssql_python
import oracledb
import pymysql
import pytest
from mssql_python.constants import ConstantsDDBC

from probeowl.connectors.oracle import _declare_types, input_type
from probeowl.connectors.sqlserver import input_size, plan_sql
from probeowl.engine.masking import to_json
from probeowl.engine.typed_values import column_kinds, kind_of, restore
from probeowl.errors import GuardViolation
from probeowl.guards.dialects import MARIADB, ORACLE, SQLSERVER
from probeowl.guards.statement import prepare
from probeowl.model.run import ValueKind

WHEN = datetime(2026, 3, 4, 23, 41, 7, 123456)
AWARE = datetime(2026, 3, 4, 23, 41, 7, tzinfo=timezone(timedelta(hours=4)))


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (WHEN, ValueKind.DATETIME),
        (date(2026, 3, 4), ValueKind.DATE),
        (time(23, 41, 7), ValueKind.TIME),
        (5, ValueKind.INTEGER),
        (Decimal("500.25"), ValueKind.DECIMAL),
        (0.5, ValueKind.FLOAT),
        (True, ValueKind.BOOLEAN),
        ("03/04/2026", ValueKind.TEXT),
    ],
)
def test_a_value_comes_back_as_it_was_read(value: Any, kind: ValueKind) -> None:
    assert kind_of(value) is kind
    back = restore(to_json(value), kind)
    assert back == value
    assert type(back) is type(value)


def test_an_aware_time_keeps_its_offset() -> None:
    back = restore(to_json(AWARE), ValueKind.DATETIME)
    assert back == AWARE
    assert isinstance(back, datetime)
    assert back.utcoffset() == timedelta(hours=4)


def test_a_column_kind_comes_from_its_first_value_that_is_not_null() -> None:
    rows: list[dict[str, Any]] = [
        {"a": None, "b": "x", "c": {"nested": 1}},
        {"a": WHEN, "b": "y", "c": {"nested": 2}},
    ]
    assert column_kinds(rows) == {"a": ValueKind.DATETIME, "b": ValueKind.TEXT}


@pytest.mark.parametrize(
    ("value", "kind"),
    [("not a date", ValueKind.DATETIME), ("x", ValueKind.INTEGER), ([1], ValueKind.TEXT)],
)
def test_a_value_that_is_not_its_type_is_an_error(value: Any, kind: ValueKind) -> None:
    with pytest.raises(ValueError):
        restore(value, kind)


# --- SQL Server ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # datetime2(7): precision 27, scale 7. Never varchar.
        (WHEN, (mssql_python.SQL_TYPE_TIMESTAMP, 27, 7)),
        (date(2026, 3, 4), (mssql_python.SQL_TYPE_DATE, 10, 0)),
        (time(23, 41, 7), (mssql_python.SQL_TYPE_TIME, 16, 7)),
        (5, (mssql_python.SQL_BIGINT, 19, 0)),
        (Decimal("500.25"), (mssql_python.SQL_DECIMAL, 38, 2)),
        ("tp_91f2c7", (mssql_python.SQL_VARCHAR, 8000, 0)),
    ],
)
def test_sql_server_sends_each_kind_as_its_type(value: Any, expected: tuple[int, int, int]) -> None:
    assert input_size(value, None) == expected


def test_sql_server_sends_a_plus_four_time_as_datetimeoffset_unshifted() -> None:
    assert input_size(AWARE, None) == (ConstantsDDBC.SQL_DATETIMEOFFSET.value, 34, 7)
    query = prepare("SELECT id FROM t WHERE at < :at", {"at": AWARE}, SQLSERVER, 1)
    # The value keeps its own wall clock and offset: nothing here turns it into UTC.
    assert query.parameters == [AWARE]
    [sent] = query.parameters
    assert isinstance(sent, datetime)
    assert (sent.hour, sent.utcoffset()) == (23, timedelta(hours=4))


def test_sql_server_keeps_a_time_with_no_offset_as_datetime2() -> None:
    assert input_size(WHEN, None) == (mssql_python.SQL_TYPE_TIMESTAMP, 27, 7)


def test_a_sql_server_plan_keeps_an_offset_an_offset() -> None:
    query = prepare("SELECT id FROM t WHERE at < :at", {"at": AWARE}, SQLSERVER, 1)
    assert "'2000-01-01 00:00:00 +00:00'" in plan_sql(query, {})


def test_a_sql_server_plan_keeps_a_time_a_time() -> None:
    query = prepare("SELECT id FROM t WHERE at = :at", {"at": time(1, 2)}, SQLSERVER, 1)
    assert "'00:00:00'" in plan_sql(query, {})


# --- Oracle ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (WHEN, oracledb.DB_TYPE_TIMESTAMP),
        (AWARE, oracledb.DB_TYPE_TIMESTAMP_TZ),
        (date(2026, 3, 4), oracledb.DB_TYPE_DATE),
        (5, oracledb.DB_TYPE_NUMBER),
        (Decimal("500.25"), oracledb.DB_TYPE_NUMBER),
        ("tp_91f2c7", None),
    ],
)
def test_oracle_sends_each_kind_as_its_type(value: Any, expected: Any) -> None:
    assert input_type(value) is expected


def test_oracle_has_no_time_of_day_type() -> None:
    with pytest.raises(TypeError, match="no Oracle type"):
        input_type(time(1, 2))


def test_oracle_declares_only_what_the_driver_would_guess() -> None:
    class Cursor:
        def __init__(self) -> None:
            self.declared: dict[str, Any] = {}

        def setinputsizes(self, **types: Any) -> None:
            self.declared = types

    sql = "SELECT id FROM t WHERE at < :at AND id = :id"
    query = prepare(sql, {"at": WHEN, "id": "tp_91f2c7"}, ORACLE, 1)
    cursor = Cursor()
    _declare_types(cursor, query)
    assert cursor.declared == {"at": oracledb.DB_TYPE_TIMESTAMP}


# --- MariaDB --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "literal"),
    [
        # PyMySQL writes the literal itself, and these forms do not depend on any server setting.
        (WHEN, "'2026-03-04 23:41:07.123456'"),
        (date(2026, 3, 4), "'2026-03-04'"),
        (time(23, 41, 7), "'23:41:07'"),
        (5, "5"),
        (Decimal("500.25"), "500.25"),
        ("tp_91f2c7", "'tp_91f2c7'"),
    ],
)
def test_mariadb_keeps_a_native_value_as_its_type(value: Any, literal: str) -> None:
    query = prepare("SELECT id FROM t WHERE at = :at", {"at": value}, MARIADB, 1)
    assert isinstance(query.parameters, dict)
    assert query.parameters["at"] == value
    assert pymysql.converters.escape_item(value, "utf8mb4") == literal


# --- The guard ------------------------------------------------------------------------------


def test_the_guard_accepts_a_time_and_still_refuses_what_is_not_a_single_value() -> None:
    assert prepare("SELECT id FROM t WHERE at = :at", {"at": time(1, 2)}, SQLSERVER, 1)
    with pytest.raises(GuardViolation, match="must be a single value"):
        prepare("SELECT id FROM t WHERE at = :at", {"at": [time(1, 2)]}, SQLSERVER, 1)
