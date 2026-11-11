# Author: @SaadZarook
"""Plan checks: each lookup uses an index, read with EXPLAIN in one environment.

A lookup whose plan reads a whole large table fails; so does an identifier whose lookup
would (the identifier-index rule). A whole small table only warns. A plan that cannot be read
(a missing privilege, no binding there) warns with the reason, so saving is never blocked by
the plan reader itself.
"""

from collections.abc import Callable, Iterable, Mapping
from typing import Protocol

from probeowl.checks.rules import Placeholders, Statement, refusal
from probeowl.checks.sql_shape import shape
from probeowl.connectors.factory import open_sql
from probeowl.connectors.plan import Access, PlanStep
from probeowl.connectors.sql import SqlLimits
from probeowl.errors import ProbeOwlError
from probeowl.model.catalog import CatalogTable, TableLabel
from probeowl.model.check import LoadedApplication
from probeowl.model.connector import SqlConnector
from probeowl.model.save_check import CheckStatus, SaveCheck

# A table this small may be read whole; past it, a lookup needs an index.
SMALL_TABLE_ROWS = 1000
# Plans answer quickly, but Oracle's EXPLAIN PLAN takes longer than a keyed lookup.
PLAN_LIMITS = SqlLimits(statement_timeout_s=10)
IDENTIFIER_INDEX = "Identifier index"
_DIALECTS = {"sqlserver": "SQL Server", "oracle": "Oracle", "mariadb": "MariaDB"}
# SQL Server reads a plan with fixed literals (sqlserver.plan_sql); MariaDB's EXPLAIN gets
# the placeholders' values from the check, not a subject's.
PLACEHOLDER_NOTE = "planned with placeholder values; the real plan can differ for unusual values"


class PlanSource(Protocol):
    """What a plan check needs from a SQL connector."""

    def explain(
        self,
        sql: str,
        parameters: Mapping[str, object],
        parameter_types: Mapping[str, str] | None = None,
    ) -> list[PlanStep]:
        """The plan, without running the lookup."""
        ...

    def close(self) -> None:
        """Release the connection."""
        ...


PlanOpener = Callable[[SqlConnector, str], PlanSource | None]


def open_for_plans(connector: SqlConnector, environment: str) -> PlanSource | None:
    """The connector's adapter in environment, with the plan time limit."""
    return open_sql(connector, environment, limits=PLAN_LIMITS)


def plan_checks(
    app: LoadedApplication,
    statements: Iterable[Statement],
    environment: str | None,
    opener: PlanOpener = open_for_plans,
) -> list[SaveCheck]:
    """One check per SQL lookup: its plan in the environment, index or scan."""
    sql = [s for s in statements if s.rules is not None and refusal(s) is None]
    if not sql:
        return []
    if environment is None:
        return [
            _check(
                CheckStatus.WARN,
                "Index use",
                "not checked: choose an environment to read plans in",
                plan_not_run=True,
            )
        ]
    catalog = {
        (t.connector, t.table.lower()): t for t in (app.catalog.tables if app.catalog else [])
    }
    sources: dict[str, PlanSource | None] = {}
    found = []
    try:
        for statement in sql:
            found.append(_plan_check(app, statement, environment, catalog, sources, opener))
    finally:
        for source in sources.values():
            if source is not None:
                source.close()
    return found


def _plan_check(
    app: LoadedApplication,
    statement: Statement,
    environment: str,
    catalog: dict[tuple[str, str], CatalogTable],
    sources: dict[str, PlanSource | None],
    opener: PlanOpener,
) -> SaveCheck:
    connector = app.connectors[statement.connector]
    assert isinstance(connector, SqlConnector)  # only SQL statements have rules
    name = (
        IDENTIFIER_INDEX
        if statement.identifier
        else f"{statement.connector} plan · {_DIALECTS[connector.type]}"
    )

    def check(status: CheckStatus, message: str, not_run: bool = False) -> SaveCheck:
        return _check(status, name, message, statement.location, environment, not_run)

    try:
        if statement.connector not in sources:
            sources[statement.connector] = opener(connector, environment)
        source = sources[statement.connector]
        if source is None:
            return check(
                CheckStatus.WARN,
                f"index use not checked: {statement.connector} has no binding in {environment}",
                not_run=True,
            )
        steps = source.explain(statement.sql, Placeholders())
    except ProbeOwlError as err:
        return check(CheckStatus.WARN, f"index use not checked: {err}", not_run=True)
    assert statement.rules is not None  # filtered above
    aliases = shape(statement.sql, statement.rules.sqlglot_dialect).tables
    status, message = _verdict(statement, steps, aliases, catalog)
    if connector.type in {"sqlserver", "mariadb"}:
        message = f"{message} ({PLACEHOLDER_NOTE})"
    return check(status, message)


def _verdict(
    statement: Statement,
    steps: list[PlanStep],
    aliases: dict[str, str],
    catalog: dict[tuple[str, str], CatalogTable],
) -> tuple[CheckStatus, str]:
    """What a plan that could be read says about the lookup."""
    scans = [s for s in steps if s.access is Access.SCAN]
    large = [s for s in scans if not _small(s, aliases, statement.connector, catalog)]
    if large:
        return CheckStatus.FAIL, _scan_message(statement, large[0], aliases)
    if scans:
        step = scans[0]
        rows = _rows(step.table_rows)
        return CheckStatus.WARN, (
            f"{step.describe()} ({rows}): fine while {_table(step, aliases)} is small; "
            "index it before it grows"
        )
    used = [s.describe() for s in steps if s.access is Access.INDEX]
    return CheckStatus.PASS, "; ".join(used) if used else "reads no table"


def _rows(count: int | None) -> str:
    if count is None:
        return "small"
    return "about 1 row" if count == 1 else f"about {count} rows"


def _small(
    step: PlanStep,
    aliases: dict[str, str],
    connector: str,
    catalog: dict[tuple[str, str], CatalogTable],
) -> bool:
    entry = catalog.get((connector, _table(step, aliases)))
    if entry is not None and entry.label is TableLabel.REFERENCE:
        return True
    return step.table_rows is not None and step.table_rows <= SMALL_TABLE_ROWS


def _table(step: PlanStep, aliases: dict[str, str]) -> str:
    named = step.table or step.index or "a table"
    return aliases.get(named, named)


def _scan_message(statement: Statement, step: PlanStep, aliases: dict[str, str]) -> str:
    table = _table(step, aliases)
    if statement.identifier is not None:
        name, via = statement.identifier
        field = via.split(".")[1]
        return (
            f"identifier {name}: {step.describe()}. Looking a subject up by {name} would read "
            f"all of {table}: index the column behind {via} ({field})"
        )
    return (
        f"{step.describe()}: the lookup would read all of {table}. Index the column it filters "
        "on, or look rows up by a key"
    )


def _check(
    status: CheckStatus,
    name: str,
    message: str,
    location: str | None = None,
    environment: str | None = None,
    plan_not_run: bool = False,
) -> SaveCheck:
    return SaveCheck(
        check=name,
        status=status,
        message=message,
        location=location,
        environment=environment,
        plan_not_run=plan_not_run,
    )
