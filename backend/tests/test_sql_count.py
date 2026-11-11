# Author: @SaadZarook
"""Counting the rows a capped lookup matched, for "showing 50 of 312"."""

from collections.abc import Generator, Mapping
from typing import Any

import pytest

from probeowl.connectors.failures import Failure
from probeowl.connectors.plan import PlanStep
from probeowl.connectors.sql import SqlAdapter
from probeowl.errors import ConnectorError
from probeowl.guards.dialects import MARIADB, ORACLE, SQLSERVER, DialectRules
from probeowl.guards.statement import GuardedQuery, count_query, prepare
from probeowl.model import SqlBinding
from probeowl.model.schema import TableSchema

LOOKUP = "SELECT card_id FROM cards WHERE visitor_profile_id = :v ORDER BY card_id"


@pytest.mark.parametrize("rules", [SQLSERVER, ORACLE, MARIADB], ids=lambda r: r.name)
def test_the_count_query_drops_the_order_and_passes_the_guard(rules: DialectRules) -> None:
    sql = count_query(LOOKUP, rules)
    assert sql.startswith("SELECT COUNT(*) AS total FROM (SELECT card_id")
    assert "ORDER BY" not in sql
    guarded = prepare(sql, {"v": "VP00012931"}, rules, 1)
    assert "VP00012931" not in guarded.sql


def test_an_authors_own_limit_is_kept_with_its_order() -> None:
    sql = count_query(LOOKUP + " LIMIT 20", MARIADB)
    assert "ORDER BY card_id LIMIT 20" in sql


class Driver(Exception):
    """The fake driver's errors."""


class Fake(SqlAdapter):
    """An adapter whose 'database' has 30 matching rows, or fails to count them."""

    rules = MARIADB

    def __init__(self, count_fails: bool = False):
        binding = SqlBinding(host="db", database="access", user="ro", secret="env:PW")
        super().__init__(binding, "pw")
        self.count_fails = count_fails
        self.sent: list[str] = []

    def _connect(self) -> Any:
        return object()

    def _run(
        self, query: GuardedQuery, parameter_types: Mapping[str, str]
    ) -> Generator[dict[str, Any]]:
        self.sent.append(query.sql)
        if "COUNT(*)" in query.sql:
            if self.count_fails:
                raise Driver("the statement timed out after 2 s")
            yield {"total": 30}
            return
        for i in range(30):
            yield {"card_id": f"C{i:06d}"}

    def _sql_sent(self, query: GuardedQuery) -> str:
        return query.sql

    def _session_guards(self) -> list[str]:
        return []

    def _read_lag(self) -> float:
        return 0.0

    def _read_schema(self) -> tuple[list[TableSchema], str | None]:
        return [], None

    def _explain(self, query: GuardedQuery, parameter_types: Mapping[str, str]) -> list[PlanStep]:
        return []

    def _driver_error(self) -> type[Exception]:
        return Driver

    def _classify(self, err: Exception) -> Failure:
        return Failure.TIMED_OUT if "timed out" in str(err) else Failure.CONNECTION


def test_a_capped_lookup_is_counted() -> None:
    result = Fake().lookup(LOOKUP, {"v": "VP00012931"}, 5)
    assert (len(result.rows), result.truncated, result.rows_total) == (5, "more than 5 rows", 30)
    assert result.guards[-1].startswith("rows counted with: SELECT COUNT(*) AS total")


def test_a_lookup_within_its_cap_is_not_counted() -> None:
    adapter = Fake()
    result = adapter.lookup(LOOKUP, {"v": "VP00012931"}, 50)
    assert result.rows_total is None
    assert len(adapter.sent) == 1


def test_a_count_that_fails_leaves_the_rows_and_says_why() -> None:
    result = Fake(count_fails=True).lookup(LOOKUP, {"v": "VP00012931"}, 5)
    assert len(result.rows) == 5
    assert result.rows_total is None
    assert result.guards[-1] == "rows not counted: the statement timed out after 2 s"


class Unreachable(Fake):
    """A driver whose error names the server, the login and the password, as drivers can."""

    lag_privilege = "REPLICA MONITOR"
    lag_denied = ("denied",)

    def __init__(self, error: str) -> None:
        super().__init__()
        self._binding = SqlBinding(
            host="wallet-sql-ro.corp", database="wallet", user="sa", secret="env:PW"
        )
        self._password = "hunter2"
        self.error = error

    def _connect(self) -> Any:
        raise Driver(self.error)


def test_a_driver_error_stays_in_the_log(caplog: pytest.LogCaptureFixture) -> None:
    error = "cannot connect to wallet-sql-ro.corp:1433 as 'sa' with hunter2"
    adapter = Unreachable(error)
    with pytest.raises(ConnectorError) as failure:
        adapter.lookup(LOOKUP, {"v": "x"}, 5)
    fixed = "the connection was refused or could not be made"
    assert str(failure.value).startswith(fixed)
    assert failure.value.__cause__ is None  # its text would print in a logged traceback
    lag = adapter.replica_lag()
    assert lag.note.startswith(f"replica lag unknown: {fixed}")
    assert lag.cause is not None and lag.cause.startswith(fixed)
    # The person running owl serve can still see what happened: not the password, or quoted values.
    assert "wallet-sql-ro.corp:1433" in caplog.text
    assert "hunter2" not in caplog.text and "'sa'" not in caplog.text
