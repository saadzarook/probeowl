# Author: @SaadZarook
"""A connector's row ceiling: refused by owl check and at save, and never exceeded at a lookup."""

from collections.abc import Generator, Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from probeowl.checks.rules import folder_failures, rule_warnings
from probeowl.connectors.factory import open_connector
from probeowl.connectors.mariadb import MariaDbAdapter
from probeowl.connectors.sql import SqlLimits
from probeowl.guards.statement import GuardedQuery
from probeowl.model.check import LoadedApplication, load_applications
from probeowl.model.connector import ConnectorGuards, RestConnector, SqlBinding, SqlConnector
from tests.test_entity_save import Both

EXAMPLES = Path(__file__).parents[2] / "examples"


@pytest.fixture
def applications() -> dict[str, LoadedApplication]:
    return load_applications(EXAMPLES)


def many_location(app: LoadedApplication) -> tuple[str, str, Any]:
    """An entity and its many location that a test can set max_rows on."""
    for entity_name, entity in sorted(app.entities.items()):
        for name, location in sorted(entity.locations.items()):
            if location.cardinality.value == "many":
                return entity_name, name, location
    raise AssertionError("the examples have a many location")


def test_the_default_ceiling_is_1000() -> None:
    assert ConnectorGuards().row_ceiling == 1000


@pytest.mark.parametrize("value", [0, -1])
def test_a_ceiling_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        ConnectorGuards(row_ceiling=value)


def test_max_rows_at_the_ceiling_passes(applications: dict[str, LoadedApplication]) -> None:
    app = applications["wallet"]
    _, _, location = many_location(app)
    location.max_rows = app.connectors[location.using].guards.row_ceiling
    assert not [f for f in folder_failures(app) if "row ceiling" in f]


def test_owl_check_refuses_max_rows_above_the_ceiling(
    applications: dict[str, LoadedApplication],
) -> None:
    app = applications["wallet"]
    entity, name, location = many_location(app)
    location.max_rows = 1001
    failures = [f for f in folder_failures(app) if "row ceiling" in f]
    assert failures == [
        f"wallet: {entity}.{name}: max_rows 1,001 is above connector {location.using}'s row "
        "ceiling of 1,000; lower max_rows, or have the connector's owner raise row_ceiling in "
        "its guards"
    ]


def test_a_connector_can_set_a_lower_ceiling(applications: dict[str, LoadedApplication]) -> None:
    app = applications["wallet"]
    _, _, location = many_location(app)
    location.max_rows = 200
    assert not [f for f in folder_failures(app) if "row ceiling" in f]
    app.connectors[location.using].guards.row_ceiling = 150
    assert [f for f in folder_failures(app) if "ceiling of 150" in f]


def test_a_run_of_a_location_above_it_is_warned(
    applications: dict[str, LoadedApplication],
) -> None:
    app = applications["wallet"]
    entity, _, location = many_location(app)
    location.max_rows = 5000
    warnings = rule_warnings(app, app.entities[entity])
    assert [w for w in warnings if "Row ceiling" in w and "5,000" in w]


def test_a_save_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    both = Both(tmp_path)
    too_many = both.entity_text.replace("max_rows: 20", "max_rows: 5000")
    assert too_many != both.entity_text
    response = both.save_both(entity=too_many)
    assert response.status_code == 422
    assert "row ceiling of 1,000" in str(response.json())
    assert both.unchanged()


class Rows(MariaDbAdapter):
    """A SQL adapter whose database has as many rows as anyone asks for."""

    asked = ""

    def _connect(self) -> Any:
        return object()

    def _run(
        self, query: GuardedQuery, parameter_types: Mapping[str, str]
    ) -> Generator[dict[str, Any]]:
        # A capped lookup then counts what it left out, in a statement of its own.
        if "COUNT(" in query.sql.upper():
            yield {"total": 5000}
            return
        self.asked = query.sql
        yield from ({"id": n} for n in range(5000))


def sql_binding() -> SqlBinding:
    return SqlBinding(host="db.example", user="reader", secret="env:PROBEOWL_NOPE")


def test_a_sql_lookup_never_returns_more_than_the_ceiling() -> None:
    adapter = _rows_adapter(row_ceiling=10)
    found = adapter.lookup("SELECT id FROM t", {}, 5000)
    assert len(found.rows) == 10
    assert found.truncated == "more than 10 rows"
    assert "LIMIT 11" in adapter.asked


def test_a_sql_lookup_below_the_ceiling_keeps_what_it_asked_for() -> None:
    adapter = _rows_adapter(row_ceiling=1000)
    assert len(adapter.lookup("SELECT id FROM t", {}, 3).rows) == 3


def _rows_adapter(row_ceiling: int) -> Rows:
    return Rows(sql_binding(), "pw", SqlLimits(row_ceiling=row_ceiling))


def test_the_factory_gives_each_adapter_its_connectors_ceiling() -> None:
    sql = SqlConnector(
        connector="a_db",
        type="mariadb",
        guards=ConnectorGuards(row_ceiling=7),
        environments={"uat": sql_binding()},
    )
    rest = RestConnector.model_validate(
        {
            "connector": "an_api",
            "type": "rest",
            "guards": {"row_ceiling": 8},
            "environments": {"uat": {"base_url": "https://api.example/v1"}},
        }
    )
    assert open_connector(sql, "uat", lambda r: "pw").guards()[-2] == "row ceiling 7"  # type: ignore[union-attr]
    assert "row ceiling 8" in open_connector(rest, "uat", lambda r: "pw")._guards()  # type: ignore[union-attr]
