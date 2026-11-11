# Author: @SaadZarook
"""Check a draft probe, and prepare it for a test run. Nothing here saves a file."""

from collections import Counter
from dataclasses import dataclass

from probeowl.checks.plans import PlanOpener, plan_checks
from probeowl.checks.rules import (
    draft_checks,
    entity_checks,
    parse_draft,
    parse_entity,
    statements_of,
)
from probeowl.checks.time_offsets import ColumnTypes, offset_checks
from probeowl.errors import ConfigError, RequestError
from probeowl.model.check import LoadedApplication
from probeowl.model.entity import Entity
from probeowl.model.probe import Probe
from probeowl.model.save_check import CheckStatus, SaveCheck, SaveCheckReport


@dataclass(frozen=True)
class Draft:
    """A draft probe and the entity it runs on (the loaded one, or a draft of it)."""

    probe: Probe
    entity: Entity


def check_draft(
    app: LoadedApplication,
    probe_yaml: str,
    entity_yaml: str | None = None,
    environment: str | None = None,
    plans: PlanOpener | None = None,
    columns: ColumnTypes | None = None,
) -> SaveCheckReport:
    """Every save check of a draft, as a report; a draft that cannot be read fails one.

    plans: how to open connectors to read plans in environment; None: no plan checks.
    columns: the imported column types; None (no store): no time offset check.
    """
    try:
        probe = parse_draft(probe_yaml)
        draft_entity = parse_entity(entity_yaml) if entity_yaml else None
    except ConfigError as err:
        return _report(None, None, [_unreadable(err)])
    entity, checks = draft_checks(app, probe, draft_entity)
    if columns is not None and entity is not None and not _failed(checks, "Connectors"):
        checks += offset_checks(app, entity, columns)
    if plans is not None and entity is not None and not _failed(checks, "Connectors"):
        checks += plan_checks(app, statements_of(app, entity), environment, plans)
    return _report(probe, entity, checks)


def check_entity(
    app: LoadedApplication,
    entity_yaml: str,
    environment: str | None = None,
    plans: PlanOpener | None = None,
    columns: ColumnTypes | None = None,
) -> SaveCheckReport:
    """Every save check of a new entity, saved without a probe; one that cannot be read fails."""
    try:
        entity = parse_entity(entity_yaml)
    except ConfigError as err:
        return _report(
            None, None, [SaveCheck(check="Entity", status=CheckStatus.FAIL, message=str(err))]
        )
    checks = entity_checks(app, entity)
    if columns is not None and not _failed(checks, "Connectors"):
        checks += offset_checks(app, entity, columns)
    if plans is not None and not _failed(checks, "Connectors"):
        checks += plan_checks(app, statements_of(app, entity), environment, plans)
    return _report(None, entity, checks)


def _failed(checks: list[SaveCheck], name: str) -> bool:
    return any(c.check == name and c.status is CheckStatus.FAIL for c in checks)


def runnable(
    app: LoadedApplication,
    probe_yaml: str,
    entity_yaml: str | None = None,
    columns: ColumnTypes | None = None,
) -> Draft:
    """The draft, if it passes its save checks; RequestError naming each failure if not."""
    refuse_failures(check_draft(app, probe_yaml, entity_yaml, columns=columns))
    probe = parse_draft(probe_yaml)
    entity = parse_entity(entity_yaml) if entity_yaml else app.entities[probe.entity]
    return Draft(probe, entity)


def refuse_failures(report: SaveCheckReport) -> None:
    """RequestError naming each failed check, if any failed."""
    failures = [c for c in report.checks if c.status is CheckStatus.FAIL]
    if failures:
        raise RequestError(
            f"the draft does not pass its save checks: {failures[0].message}",
            {_field(c): c.message for c in failures},
        )


def _report(probe: Probe | None, entity: Entity | None, checks: list[SaveCheck]) -> SaveCheckReport:
    counts = Counter(c.status for c in checks)
    return SaveCheckReport(
        probe=probe.probe if probe else None,
        entity=entity.entity if entity else None,
        checks=checks,
        passed=counts[CheckStatus.PASS],
        warnings=counts[CheckStatus.WARN],
        failed=counts[CheckStatus.FAIL],
        can_save=counts[CheckStatus.FAIL] == 0,
        plans_not_run=sum(c.plan_not_run for c in checks),
    )


def _unreadable(err: ConfigError) -> SaveCheck:
    return SaveCheck(check="Probe", status=CheckStatus.FAIL, message=str(err))


def _field(check: SaveCheck) -> str:
    return f"{check.check}: {check.location}" if check.location else check.check
