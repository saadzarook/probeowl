# Author: @SaadZarook
"""Runbooks as the API shows them: what each step is, and whether ProbeOwl may run it.

The server decides both from the statement itself, never from what its author wrote.
"""

import re
from collections.abc import Iterable
from datetime import date, timedelta

from sqlglot.errors import ParseError

from probeowl.api.models import (
    ExternalQueryStepView,
    ManualStepView,
    ProbeStepView,
    ReadQueryStepView,
    RunbookStepView,
    RunbookView,
    SuggestedRunbook,
    WriteQueryStepView,
)
from probeowl.checks.rules import read_step_problems, read_step_statement
from probeowl.checks.sql_shape import parameters_of, shape
from probeowl.guards.dialects import RULES_BY_CONNECTOR_TYPE
from probeowl.guards.statement import is_single_select
from probeowl.model.check import LoadedApplication
from probeowl.model.connector import SqlConnector
from probeowl.model.run import Run
from probeowl.model.runbook import (
    ExternalQueryStep,
    ManualStep,
    ProbeStep,
    ReadQueryStep,
    Runbook,
    WriteQueryStep,
    sql_sha256,
)

REVIEW_AFTER = timedelta(days=90)
# The label a person gets when the platform overrules theirs.
RELABELLED = "labelled a read query, but it is not a single SELECT: review it as a write query"
_PARAMETER = re.compile(r":([A-Za-z_]\w*)")

TableRef = tuple[str, str]  # connector, table in lower case


def runbook_view(
    app: LoadedApplication, runbook: Runbook, today: date, drifted: Iterable[TableRef] = ()
) -> RunbookView:
    """A runbook with each step labelled by the server, its review flag and its drifted tables."""
    tables = sorted(_tables(app, runbook))
    drift = set(drifted)
    return RunbookView(
        **runbook.model_dump(exclude={"steps"}),
        steps=[_step(app, step) for step in runbook.steps],
        review_due=_review_due(runbook, today),
        tables=[f"{connector}.{table}" for connector, table in tables],
        drifted_tables=[f"{c}.{t}" for c, t in tables if (c, t) in drift],
    )


def matching(
    app: LoadedApplication,
    runbooks: Iterable[Runbook],
    q: str | None = None,
    entity: str | None = None,
    table: str | None = None,
    identifier: str | None = None,
) -> list[Runbook]:
    """The runbooks that match every filter given: symptom, entity, catalog table, identifier."""
    found = []
    for runbook in runbooks:
        if entity is not None and runbook.applies_to.entity != entity:
            continue
        if q is not None and q.lower() not in _symptom(runbook):
            continue
        if table is not None and table.lower() not in {t for _, t in _tables(app, runbook)}:
            continue
        if identifier is not None and identifier not in _parameters(app, runbook):
            continue
        found.append(runbook)
    return found


def is_write_step(app: LoadedApplication, step: object) -> bool:
    """Whether the server treats this step as a write query, whatever its author called it."""
    if isinstance(step, WriteQueryStep):
        return True
    if not isinstance(step, ReadQueryStep):
        return False
    connector = app.connectors.get(step.using)
    if not isinstance(connector, SqlConnector):
        return False
    return not is_single_select(step.sql, RULES_BY_CONNECTOR_TYPE[connector.type])


def _step(app: LoadedApplication, step: object) -> RunbookStepView:
    if isinstance(step, ReadQueryStep):
        return _read_step(app, step)
    if isinstance(step, WriteQueryStep):
        return WriteQueryStepView(**step.model_dump(), runnable=False)
    if isinstance(step, ExternalQueryStep):
        return ExternalQueryStepView(**step.model_dump(), runnable=False)
    if isinstance(step, ManualStep):
        return ManualStepView(**step.model_dump(), runnable=False)
    assert isinstance(step, ProbeStep)  # the union's last kind
    return ProbeStepView(**step.model_dump(), runnable=step.probe in app.probes)


def _read_step(app: LoadedApplication, step: ReadQueryStep) -> RunbookStepView:
    # The label cannot be lowered, and a read query that changes data is not one.
    if is_write_step(app, step):
        return WriteQueryStepView(
            kind="write_query",
            id=step.id,
            title=step.title,
            using=step.using,
            sql=step.sql,
            requires=RELABELLED,
            relabelled=True,
            runnable=False,
        )
    why = why_copy_only(app, step)
    return ReadQueryStepView(**step.model_dump(), runnable=why is None, why_copy_only=why)


def why_copy_only(app: LoadedApplication, step: ReadQueryStep) -> str | None:
    """Why a read query is a reference query, or None if it may run."""
    if step.reviewed is None:
        return "not reviewed yet"
    if step.reviewed.sql_sha256 != sql_sha256(step.sql):
        return "its SQL changed after it was reviewed"
    statement = read_step_statement(app, step)
    if statement is None:
        return f"{step.using} is not a SQL connector"
    problems = read_step_problems(app, statement)
    return problems[0] if problems else None


def _review_due(runbook: Runbook, today: date) -> bool:
    reviewed = [
        step.reviewed.date
        for step in runbook.steps
        if isinstance(step, ReadQueryStep) and step.reviewed is not None
    ]
    # With no reviewed step, its last-verified date is all there is to go on.
    oldest = min(reviewed) if reviewed else runbook.last_verified
    return today - oldest > REVIEW_AFTER


def _symptom(runbook: Runbook) -> str:
    words = [runbook.runbook, runbook.title, *runbook.applies_to.when]
    words += [step.title for step in runbook.steps]
    return " ".join(words).replace("_", " ").replace(".", " ").lower() + " " + runbook.runbook


def _sql_steps(app: LoadedApplication, runbook: Runbook) -> Iterable[tuple[str, str, str]]:
    for step in runbook.steps:
        if isinstance(step, ReadQueryStep | WriteQueryStep):
            connector = app.connectors.get(step.using)
            if isinstance(connector, SqlConnector):
                dialect = RULES_BY_CONNECTOR_TYPE[connector.type].sqlglot_dialect
                yield step.using, step.sql, dialect


def _tables(app: LoadedApplication, runbook: Runbook) -> set[TableRef]:
    found: set[TableRef] = set()
    for connector, sql, dialect in _sql_steps(app, runbook):
        try:
            found |= {(connector, table) for table in shape(sql, dialect).tables.values()}
        except ParseError:
            continue  # a write query in another tool's syntax names no table it can read
    return found


def _parameters(app: LoadedApplication, runbook: Runbook) -> set[str]:
    found: set[str] = set()
    for _, sql, dialect in _sql_steps(app, runbook):
        try:
            found |= parameters_of(sql, dialect)
        except ParseError:
            found |= set(_PARAMETER.findall(sql))
    for step in runbook.steps:
        if isinstance(step, ExternalQueryStep):
            found |= set(_PARAMETER.findall(step.query))
    return found


def suggested(
    app: LoadedApplication, run: Run, today: date, drifted: Iterable[TableRef] = ()
) -> list[SuggestedRunbook]:
    """The runbooks for this run's entity whose applies_to matches its outcomes, closest first."""
    outcomes = {f"{r.location}.{r.outcome.value}" for r in run.locations}
    found = []
    for runbook in sorted(app.runbooks.values(), key=lambda r: r.runbook):
        if runbook.applies_to.entity != run.entity:
            continue
        matched = [when for when in runbook.applies_to.when if when in outcomes]
        if matched:
            view = runbook_view(app, runbook, today, drifted)
            found.append(SuggestedRunbook(runbook=view, matched=matched))
    return sorted(found, key=lambda s: -len(s.matched))
