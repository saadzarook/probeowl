# Author: @SaadZarook
"""Running a runbook's steps: values from the run, reviewed reads, and recorded write copies.

Nothing here takes SQL from a caller. A step is named by its ID, its SQL comes from the runbook,
its values from the run, and the server decides whether it may run. Write queries are never run.
"""

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import JsonValue

from probeowl.checks.plans import PlanOpener, PlanSource, plan_checks
from probeowl.checks.rules import read_step_statement
from probeowl.connectors.plan import PlanStep
from probeowl.engine.identifiers import detect
from probeowl.engine.locations import DEFAULT_MAX_ROWS
from probeowl.engine.masking import (
    MASKED,
    mask_rows,
    masked_fields,
    masked_key_reason,
    pii_fields,
    to_json,
)
from probeowl.engine.typed_values import column_kinds, restore
from probeowl.errors import (
    ConfigError,
    ConnectorError,
    MissingGrant,
    NotFoundError,
    RequestError,
)
from probeowl.guards.statement import ParameterValue
from probeowl.healthy import field_values
from probeowl.model.catalog import ColumnRole
from probeowl.model.check import LoadedApplication
from probeowl.model.connector import SqlConnector
from probeowl.model.entity import Cardinality, Entity
from probeowl.model.run import Evidence, Outcome, Run, RunStatus, ValueKind
from probeowl.model.runbook import (
    ExternalQueryStep,
    ManualStep,
    ProbeStep,
    ReadQueryStep,
    Runbook,
    RunbookStep,
    WriteQueryStep,
)
from probeowl.model.runbook_step import (
    CopiedWriteQuery,
    StepInRun,
    StepRan,
    StepResult,
    UnfilledParameter,
)
from probeowl.model.save_check import CheckStatus
from probeowl.runbooks import is_write_step, why_copy_only
from probeowl.runs import RunService
from probeowl.store.runbook_steps import save_step, step_states

# A parameter, skipping what is inside a quoted string and a PostgreSQL-style :: cast.
_SQL_PARAMETER = re.compile(r"'(?:[^']|'')*'|::\w+|:([A-Za-z_]\w*)")
_QUERY_PARAMETER = re.compile(r'"(?:[^"\\]|\\.)*"|::\w+|:([A-Za-z_]\w*)')

MASKED_PARAMETER = "masked in this run, so it is never filled in"
NOT_IN_RUN = "this run has no such value"
SEVERAL_ROWS = "this run has several rows with it"
DIFFERS = "two locations of this run hold different values for it"


@dataclass(frozen=True)
class _Value:
    """A parameter's value from a run, as evidence keeps it, with its type."""

    value: JsonValue
    kind: ValueKind


def parameter_value(run: Run, entity: Entity, name: str) -> _Value | str:
    """A parameter's value from the run, or why it has none."""
    if name == entity.root_key and run.subject not in (None, MASKED):
        # The key is the run's subject: pasted for this step when it is stored masked (#66),
        # where every location's rows show it masked.
        return _Value(run.subject, ValueKind.TEXT)
    found: list[_Value] = []
    several = masked = False
    for location_name, location in entity.locations.items():
        for result in run.locations:
            if result.location != location_name or result.parent is not None:
                continue
            rows = result.evidence.rows if result.evidence else []
            columns = {column.lower() for row in rows for column in row}
            if name.lower() not in columns:
                continue
            if name.lower() in masked_fields(entity, location):
                masked = True
            elif location.cardinality is Cardinality.MANY or len(rows) != 1:
                several = True
            elif result.evidence:
                [value] = field_values(rows, name)
                kinds = {c.lower(): k for c, k in result.evidence.column_types.items()}
                if value is not None:
                    found.append(_Value(value, kinds.get(name.lower(), ValueKind.TEXT)))
    if masked:
        return MASKED_PARAMETER
    if len({json.dumps(each.value) for each in found}) > 1:
        return DIFFERS
    if found:
        return found[0]
    if several:
        return SEVERAL_ROWS
    if name == entity.root_key and run.subject is not None:
        # Stored masked when the key is PII: a step that runs asks for it to be pasted.
        if run.subject == MASKED:
            return MASKED_PARAMETER
        return _Value(run.subject, ValueKind.TEXT)
    if name == run.identifier:
        if run.identifier_value == MASKED:
            return MASKED_PARAMETER
        return _Value(run.identifier_value, ValueKind.TEXT)
    return NOT_IN_RUN


def _with_pasted_subject(run: Run, entity: Entity, subject: str | None) -> Run:
    """The run with its real subject for one step, when its key is PII; refused without one."""
    if run.subject != MASKED:
        return run
    if subject is None:
        raise RequestError(masked_key_reason(entity), {"subject": masked_key_reason(entity)})
    detect(entity, subject, entity.root_key)  # a value that can't be its root key is refused
    return run.model_copy(update={"subject": subject})


def _sql_literal(found: _Value, backslash_escapes: bool) -> str:
    if found.kind in {ValueKind.INTEGER, ValueKind.DECIMAL, ValueKind.FLOAT}:
        return str(found.value)
    if found.kind is ValueKind.BOOLEAN:
        return "1" if found.value else "0"
    text = str(found.value)
    if backslash_escapes:
        text = text.replace("\\", "\\\\")
    return "'" + text.replace("'", "''") + "'"


def fill(
    text: str,
    run: Run,
    entity: Entity,
    pattern: re.Pattern[str],
    literal: Callable[[_Value], str],
) -> tuple[str, list[UnfilledParameter]]:
    """The text with this run's values. A parameter it cannot fill stays as :name, and says why."""
    unfilled: dict[str, str] = {}

    def replaced(match: re.Match[str]) -> str:
        name = match.group(1)
        if name is None:
            return match.group(0)  # a quoted string or a cast
        found = parameter_value(run, entity, name)
        if isinstance(found, str):
            unfilled[name] = found
            return match.group(0)
        return literal(found)

    filled = pattern.sub(replaced, text)
    return filled, [UnfilledParameter(parameter=n, why=w) for n, w in unfilled.items()]


class RunbookSteps:
    """A runbook's steps in a run, through the run service's guards and store."""

    def __init__(self, runs: RunService, plans: PlanOpener | None):
        self._runs = runs
        self._plans = plans

    def in_run(self, run_id: str, runbook_name: str) -> list[StepInRun]:
        """Each step with this run's values, its tick and its latest result."""
        run, app, entity, runbook = self._context(run_id, runbook_name)
        with self._runs.store.connect() as conn:
            states = step_states(conn, self._runs.workspace, run_id, runbook_name)
        found = []
        for step in runbook.steps:
            done, result = states.get(step.id, (False, None))
            filled, unfilled = _filled(step, run, app, entity)
            found.append(
                StepInRun(id=step.id, filled=filled, unfilled=unfilled, done=done, result=result)
            )
        return found

    def run_step(
        self,
        run_id: str,
        runbook_name: str,
        step: str,
        without_plan_check: bool = False,
        subject: str | None = None,
    ) -> StepRan:
        """Run a step the server marks runnable: a reviewed read query, or a probe.

        subject: the run's subject, pasted when its key is PII and so stored masked (#66). Used
        for this step only, in memory: never stored, logged, audited or returned.
        """
        run, app, entity, runbook = self._context(run_id, runbook_name)
        chosen = _step(runbook, step)
        if is_write_step(app, chosen):
            raise RequestError(
                "a write query is never run by ProbeOwl: copy it for a change request",
                {"step": f"step {step} is a write query"},
            )
        if isinstance(chosen, (ProbeStep, ReadQueryStep)):
            run = _with_pasted_subject(run, entity, subject)
        if isinstance(chosen, ProbeStep):
            return self._run_probe(run, app, runbook, step, chosen)
        if not isinstance(chosen, ReadQueryStep):
            raise RequestError(
                "ProbeOwl does not run this step", {"step": f"step {step} is {chosen.kind}"}
            )
        why = why_copy_only(app, chosen)
        if why is not None:
            raise RequestError(f"this step is copy only: {why}", {"step": why})
        return self._run_read(run, app, entity, runbook_name, step, chosen, without_plan_check)

    def tick(self, run_id: str, runbook_name: str, step: str, done: bool) -> None:
        """Save a manual step's tick with the run. Audited."""
        run, _, _, runbook = self._context(run_id, runbook_name)
        chosen = _step(runbook, step)
        if not isinstance(chosen, ManualStep):
            raise RequestError(
                "only a manual step can be ticked", {"step": f"step {step} is {chosen.kind}"}
            )
        with self._runs.store.begin() as conn:
            save_step(conn, self._runs.workspace, run.id, runbook_name, step, done=done)
            self._runs.record(
                "runbook_step_ticked",
                run,
                {"runbook": runbook_name, "step": step, "title": chosen.title, "done": done},
                conn,
            )

    def copy_write(self, run_id: str, runbook_name: str, step: str) -> CopiedWriteQuery:
        """A write query with this run's values, under its header. The copy is audited."""
        run, app, entity, runbook = self._context(run_id, runbook_name)
        chosen = _write_step(app, runbook, step)
        environment = app.application.environments[run.environment].title
        requires = _one_line(_requires(chosen))
        header = f"-- WRITE · {environment} · run {run.id} · needs {requires}"
        filled, unfilled = _filled(chosen, run, app, entity)
        self._runs.record(
            "write_query_copied",
            run,
            {
                "runbook": runbook_name,
                "step": step,
                "title": chosen.title,
                "unfilled": [u.parameter for u in unfilled],
            },
        )
        return CopiedWriteQuery(text=f"{header}\n{filled or chosen.sql}", unfilled=unfilled)

    def copy_write_without_a_run(
        self, application: str, runbook_name: str, step: str
    ) -> CopiedWriteQuery:
        """A write query as written, for a runbook opened with no run. The copy is audited."""
        app = self._runs.applications.get(application)
        runbook = app.runbooks.get(runbook_name) if app else None
        if app is None or runbook is None:
            raise NotFoundError(f"{application} has no runbook named {runbook_name}")
        chosen = _write_step(app, runbook, step)
        requires = _one_line(_requires(chosen))
        self._runs.audit(
            "write_query_copied",
            {
                "application": application,
                "runbook": runbook_name,
                "step": step,
                "title": chosen.title,
                "unfilled": "every parameter: no run",
            },
        )
        return CopiedWriteQuery(text=f"-- WRITE · no run · needs {requires}\n{chosen.sql}")

    def _context(
        self, run_id: str, runbook_name: str
    ) -> tuple[Run, LoadedApplication, Entity, Runbook]:
        run = self._runs.get(run_id)
        app = self._runs.applications.get(run.application)
        entity = app.entities.get(run.entity) if app else None
        runbook = app.runbooks.get(runbook_name) if app else None
        if app is None or entity is None or runbook is None:
            raise NotFoundError(f"{run.application} has no runbook named {runbook_name}")
        if runbook.applies_to.entity != run.entity:
            raise RequestError(
                f"{runbook_name} is for {runbook.applies_to.entity}; this run is on {run.entity}",
                {"runbook": f"choose a runbook for {run.entity}"},
            )
        return run, app, entity, runbook

    def _run_probe(
        self, run: Run, app: LoadedApplication, runbook: Runbook, step: str, chosen: ProbeStep
    ) -> StepRan:
        probe = app.probes.get(chosen.probe)
        if probe is None:
            raise RequestError(
                f"{app.application.application} has no probe named {chosen.probe}",
                {"step": "this step's probe does not exist"},
            )
        started = self._runs.start_again(run, probe.probe)
        self._runs.record(
            "runbook_step_run",
            run,
            {"runbook": runbook.runbook, "step": step, "title": chosen.title, "run": started},
        )
        return StepRan(id=step, run=started)

    def _run_read(
        self,
        run: Run,
        app: LoadedApplication,
        entity: Entity,
        runbook_name: str,
        step: str,
        chosen: ReadQueryStep,
        without_plan_check: bool,
    ) -> StepRan:
        if run.status is RunStatus.RUNNING or run.subject is None:
            raise RequestError(
                f"run {run.id} is {run.status.value}: wait for it to finish on one subject",
                {"run": "a step takes its values from a finished run"},
            )
        # Before anything is read, the plan included: a stopped connector stays alone.
        self._runs.kill.check(run.application, chosen.using)
        parameters = _bound_parameters(chosen, run, entity)
        warnings, skipped = self._check_plan(run, app, chosen, without_plan_check)
        result = self._read(run, app, pii_fields(entity), chosen, parameters, warnings)
        with self._runs.store.begin() as conn:
            save_step(conn, self._runs.workspace, run.id, runbook_name, step, result=result)
            self._runs.record(
                "runbook_step_run",
                run,
                {
                    "runbook": runbook_name,
                    "step": step,
                    "title": chosen.title,
                    "outcome": result.outcome.value,
                    "rows": len(result.evidence.rows) if result.evidence else 0,
                    "run_without_plan_check": bool(skipped),
                    "plans_not_run": skipped,
                },
                conn,
            )
        return StepRan(id=step, result=result)

    def _check_plan(
        self, run: Run, app: LoadedApplication, chosen: ReadQueryStep, without_plan_check: bool
    ) -> tuple[list[str], list[str]]:
        """The step's plan, by the rules a probe's save goes by: warnings, and what was not run."""
        statement = read_step_statement(app, chosen)
        assert statement is not None  # why_copy_only refused one with no SQL connector
        if self._plans is None:
            raise ConfigError("reading a plan needs owl serve: start the API with owl serve")
        plans = self._runs.kill.guarded(run.application, self._plans)
        missing: list[MissingGrant] = []
        checks = plan_checks(app, [statement], run.environment, _noting(plans, missing))
        if missing:
            # The database named a grant the login lacks: the step cannot run until a DBA gives it.
            why = (
                f"{chosen.using}'s login in {run.environment} is missing {missing[0].grant}, "
                "which a DBA grants, as for the lag grants (docs/connectors.md)"
            )
            raise RequestError(f"this step is copy only: {why}", {"step": why})
        failed = [c.message for c in checks if c.status is CheckStatus.FAIL]
        if failed:
            raise RequestError(f"this step cannot run: {failed[0]}", {"step": failed[0]})
        not_run = [c.message for c in checks if c.plan_not_run]
        production = app.application.environments[run.environment].production
        if not_run and production and not without_plan_check:
            message = (
                f"plan checks could not run in {run.environment} ({not_run[0]}); "
                "to run without plan check, confirm it: that is recorded in the audit"
            )
            raise RequestError(message, {"run_without_plan_check": message})
        warnings = [c.message for c in checks if c.status is CheckStatus.WARN]
        return warnings, not_run if production else []

    def _read(
        self,
        run: Run,
        app: LoadedApplication,
        hidden: set[str],
        chosen: ReadQueryStep,
        parameters: dict[str, ParameterValue],
        warnings: list[str],
    ) -> StepResult:
        now = datetime.now(UTC)
        actor = self._runs.actor.name
        try:
            read = self._runs.read_once(
                app, chosen.using, run.environment, chosen.sql, parameters, DEFAULT_MAX_ROWS
            )
        except (ConnectorError, ConfigError) as err:
            # As for a location: a connector that fails is an Error, with the reason. A kill
            # switch and a full service are not, and go back to the person as a refusal.
            return StepResult(
                outcome=Outcome.ERROR, reason=str(err), warnings=warnings, actor=actor, at=now
            )
        # The catalog's PII columns, and every PII identifier (hidden): a pasted key is never kept.
        rows = mask_rows(read.rows, {*_catalog_pii(app, chosen), *hidden})
        evidence = Evidence(
            connector=chosen.using,
            query=read.query,
            parameters={
                name: MASKED if name.lower() in hidden else to_json(value)
                for name, value in parameters.items()
            },
            guards=read.guards,
            rows=[{key: to_json(value) for key, value in row.items()} for row in rows],
            rows_total=read.rows_total,
            column_types=column_kinds(read.rows),
            started_at=read.started_at,
            duration_ms=read.duration_ms,
        )
        outcome = Outcome.FOUND if rows else Outcome.MISSING
        return StepResult(
            outcome=outcome, evidence=evidence, warnings=warnings, actor=actor, at=now
        )


class _Noted:
    """A plan source that notes a grant the database named, before the plan check sees it."""

    def __init__(self, source: PlanSource, missing: list[MissingGrant]):
        self._source = source
        self._missing = missing

    def explain(
        self,
        sql: str,
        parameters: Mapping[str, object],
        parameter_types: Mapping[str, str] | None = None,
    ) -> list[PlanStep]:
        try:
            return self._source.explain(sql, parameters, parameter_types)
        except MissingGrant as err:
            self._missing.append(err)
            raise

    def close(self) -> None:
        self._source.close()


def _noting(plans: PlanOpener, missing: list[MissingGrant]) -> PlanOpener:
    def opener(connector: SqlConnector, environment: str) -> PlanSource | None:
        source = plans(connector, environment)
        return None if source is None else _Noted(source, missing)

    return opener


def _step(runbook: Runbook, step: str) -> RunbookStep:
    for candidate in runbook.steps:
        if candidate.id == step:
            return candidate
    raise NotFoundError(f"{runbook.runbook} has no step {step}")


def _write_step(
    app: LoadedApplication, runbook: Runbook, step: str
) -> ReadQueryStep | WriteQueryStep:
    chosen = _step(runbook, step)
    if not is_write_step(app, chosen):
        raise RequestError(
            "only a write query's copy is recorded here", {"step": f"step {step} is {chosen.kind}"}
        )
    assert isinstance(chosen, ReadQueryStep | WriteQueryStep)  # is_write_step said so
    return chosen


def _requires(step: ReadQueryStep | WriteQueryStep) -> str:
    return step.requires if isinstance(step, WriteQueryStep) else "review: it was labelled a read"


def _one_line(text: str) -> str:
    # A newline in it would end the comment, and put the rest in the SQL a person pastes.
    return " ".join(text.split())


def _filled(
    step: RunbookStep, run: Run, app: LoadedApplication, entity: Entity
) -> tuple[str | None, list[UnfilledParameter]]:
    if isinstance(step, ReadQueryStep | WriteQueryStep):
        connector = app.connectors.get(step.using)
        backslashes = isinstance(connector, SqlConnector) and connector.type == "mariadb"
        return fill(step.sql, run, entity, _SQL_PARAMETER, lambda v: _sql_literal(v, backslashes))
    if isinstance(step, ExternalQueryStep):
        return fill(step.query, run, entity, _QUERY_PARAMETER, lambda v: json.dumps(str(v.value)))
    return None, []


def _bound_parameters(step: ReadQueryStep, run: Run, entity: Entity) -> dict[str, ParameterValue]:
    """The values a read query binds, each as its real type. Without one it cannot run."""
    bound: dict[str, ParameterValue] = {}
    for name in _names(step.sql):
        found = parameter_value(run, entity, name)
        if isinstance(found, str):
            raise RequestError(f"this step needs :{name}: {found}", {name: found})
        try:
            bound[name] = restore(found.value, found.kind)
        except (ValueError, ArithmeticError):
            why = f"this run's value is not the {found.kind.value} it was"
            raise RequestError(f"this step needs :{name}: {why}", {name: why}) from None
    return bound


def _names(sql: str) -> list[str]:
    return sorted({match.group(1) for match in _SQL_PARAMETER.finditer(sql) if match.group(1)})


def _catalog_pii(app: LoadedApplication, step: ReadQueryStep) -> list[str]:
    """Every PII column of the connector's catalog. Masked again, whatever the save check saw."""
    tables = app.catalog.tables if app.catalog else []
    return [
        column
        for table in tables
        if table.connector == step.using
        for column, spec in table.columns.items()
        if ColumnRole.PII in spec.roles
    ]
