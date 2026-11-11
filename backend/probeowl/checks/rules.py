# Author: @SaadZarook
"""The save checks that need no database: the draft itself, read-only SQL, the label rules.

Every lookup the probe can run is checked: its entity's location lookups and fallbacks
(fan-out included) and each identifier's resolution lookup.
"""

from collections.abc import Iterator
from dataclasses import dataclass

import yaml
from sqlglot.errors import ParseError

from probeowl.checks.sql_shape import (
    TimeBound,
    keyed,
    parameters_of,
    selected,
    shape,
    time_bound,
)
from probeowl.connectors.rest import build_request
from probeowl.engine.identifiers import MAX_MATCH_ROWS, resolution_lookup
from probeowl.engine.locations import DEFAULT_MAX_ROWS, rows_read
from probeowl.engine.runner import check_probe
from probeowl.errors import ConfigError, GuardViolation
from probeowl.guards.dialects import RULES_BY_CONNECTOR_TYPE, DialectRules
from probeowl.guards.statement import is_single_select, prepare
from probeowl.healthy import SAMPLE_ROWS
from probeowl.model.catalog import CatalogTable, ColumnRole
from probeowl.model.check import LoadedApplication
from probeowl.model.common import split_field_ref
from probeowl.model.connector import DEFAULT_ROW_CEILING, SqlConnector
from probeowl.model.entity import Entity, FanOutLocation, HealthySample, Location
from probeowl.model.load import parse_document, read_text
from probeowl.model.probe import Probe
from probeowl.model.runbook import ReadQueryStep
from probeowl.model.save_check import CheckStatus, SaveCheck

READ_ONLY = "Read-only SQL"
TIME_FILTER = "Time filter"
PII_MASKED = "PII masked"
HEALTHY_SAMPLE = "Healthy sample"


@dataclass(frozen=True)
class Statement:
    """One lookup the probe can run."""

    location: str  # 'record', 'cards.controller', 'record (fallback)', 'identifier email'
    connector: str
    sql: str
    rules: DialectRules | None  # None for a REST lookup
    pii: list[str]
    allowed_databases: set[str]
    identifier: tuple[str, str] | None = None  # (name, via) for an identifier's lookup
    max_rows: int = 1  # the rows the lookup reads; the guard refuses its own limit above that
    row_ceiling: int = DEFAULT_ROW_CEILING  # the connector's


def parse_draft(text: str) -> Probe:
    """The draft's probe; ConfigError with the reason if it is not one."""
    try:
        data = read_text(text)
    except yaml.YAMLError as err:
        raise ConfigError(f"the YAML cannot be read: {err}") from err
    document = parse_document(data, "the draft")
    if not isinstance(document, Probe):
        raise ConfigError("the draft is not a probe: it needs probe, kind, entity and expect")
    return document


def parse_entity(text: str) -> Entity:
    """A draft entity; ConfigError with the reason if it is not one."""
    try:
        data = read_text(text)
    except yaml.YAMLError as err:
        raise ConfigError(f"the entity YAML cannot be read: {err}") from err
    document = parse_document(data, "the draft entity")
    if not isinstance(document, Entity):
        raise ConfigError("the draft entity is not an entity")
    return document


def draft_checks(
    app: LoadedApplication, probe: Probe, draft_entity: Entity | None = None
) -> tuple[Entity | None, list[SaveCheck]]:
    """Every check that needs no database, in the order the editor shows them.

    draft_entity: an entity being edited with the probe, in place of the loaded one.
    """
    if draft_entity is not None and draft_entity.entity != probe.entity:
        return None, [
            _fail(
                "Entity",
                f"the probe is for {probe.entity}, the draft entity is {draft_entity.entity}",
            )
        ]
    entity = draft_entity or app.entities.get(probe.entity)
    if entity is None:
        return None, [
            _fail("Entity", f"{app.application.application} has no entity {probe.entity}")
        ]
    unknown = sorted({using for _, using in _locations(entity) if using not in app.connectors})
    if unknown:
        return entity, [_fail("Connectors", f"no connector named {', '.join(unknown)}")]
    checks = [_expectations(probe, entity), *_environments(app, probe, entity)]
    if draft_entity is not None:
        checks += _other_probes(app, probe, draft_entity)
    return entity, checks + _entity_checks(app, entity)


def entity_checks(app: LoadedApplication, entity: Entity) -> list[SaveCheck]:
    """Every check that needs no database, of an entity saved on its own, with no probe yet."""
    unknown = sorted({using for _, using in _locations(entity) if using not in app.connectors})
    if unknown:
        return [_fail("Connectors", f"no connector named {', '.join(unknown)}")]
    return _entity_checks(app, entity)


def folder_failures(app: LoadedApplication) -> list[str]:
    """Each save check that needs no database and fails, for every entity: for owl check.

    A probe's expectations and environments are checked with the references, when loading.
    """
    found: list[str] = []
    for name, entity in sorted(app.entities.items()):
        for check in _broken_rules(app, entity):
            where = f"{name}.{check.location}" if check.location else name
            found.append(f"{app.application.application}: {where}: {check.message}")
    found.extend(_mislabelled_steps(app))
    return found


def _mislabelled_steps(app: LoadedApplication) -> Iterator[str]:
    for name, runbook in sorted(app.runbooks.items()):
        for step in runbook.steps:
            connector = app.connectors.get(step.using) if isinstance(step, ReadQueryStep) else None
            if not isinstance(step, ReadQueryStep) or not isinstance(connector, SqlConnector):
                continue
            if not is_single_select(step.sql, RULES_BY_CONNECTOR_TYPE[connector.type]):
                yield (
                    f"{app.application.application}: runbook {name}: step {step.title!r} is "
                    "not a single SELECT: make it a write_query"
                )


def read_step_statement(app: LoadedApplication, step: ReadQueryStep) -> Statement | None:
    """A read query as the save checks see it, or None if its connector is not a SQL one."""
    connector = app.connectors.get(step.using)
    if not isinstance(connector, SqlConnector):
        return None
    rules = RULES_BY_CONNECTOR_TYPE[connector.type]
    return Statement(
        step.title,
        step.using,
        step.sql,
        rules,
        [],
        _allowed(connector),
        max_rows=DEFAULT_MAX_ROWS,
        row_ceiling=connector.guards.row_ceiling,
    )


def read_step_problems(app: LoadedApplication, statement: Statement) -> list[str]:
    """The save checks a read query fails: the guard, the time filter and masking.

    It has no masking list of its own, so selecting a PII column fails: results are masked by the
    catalog, and a step that needs a person's data stays copy only.
    """
    reason = refusal(statement)
    if reason is not None:
        return [reason]
    tables = _catalog(app)
    return [*_time_problems(statement, tables), *_unmasked(statement, tables)]


def rule_warnings(app: LoadedApplication, entity: Entity) -> list[str]:
    """For a run: each save rule the entity's lookups break, which owl check fails on."""
    return [
        f"Breaks the {check.check} save rule"
        + (f" in {check.location}" if check.location else "")
        + f": {check.message}"
        for check in _broken_rules(app, entity)
    ]


def _broken_rules(app: LoadedApplication, entity: Entity) -> list[SaveCheck]:
    if any(using not in app.connectors for _, using in _locations(entity)):
        return []  # reported with the references, when loading
    return [c for c in _entity_checks(app, entity) if c.status is CheckStatus.FAIL]


def _entity_checks(app: LoadedApplication, entity: Entity) -> list[SaveCheck]:
    statements = list(statements_of(app, entity))
    tables = _catalog(app)
    return [
        *_identifiers(app, entity),
        *_row_ceilings(app, entity),
        *_healthy_sample(app, entity),
        *_read_only(statements),
        *_time_filters(statements, tables),
        *_pii(statements, tables),
    ]


def _other_probes(app: LoadedApplication, probe: Probe, entity: Entity) -> list[SaveCheck]:
    # An entity is shared: saving it must not break the probes not being edited, which would
    # stop owl serve from loading at its next start.
    found = []
    for other in app.probes.values():
        if other.entity != entity.entity or other.probe == probe.probe:
            continue
        for check in (_expectations(other, entity), *_environments(app, other, entity)):
            if check.status is CheckStatus.FAIL:
                found.append(_fail(check.check, f"probe {other.probe}: {check.message}"))
    return found


def statements_of(app: LoadedApplication, entity: Entity) -> Iterator[Statement]:
    """Every lookup of the entity, and the lookup behind each identifier."""
    for name, location in entity.locations.items():
        yield from _of_location(app, name, location)
        for child, fan_out in location.each.items():
            yield from _of_location(app, f"{name}.{child}", fan_out)
    for name, identifier in entity.identifiers.items():
        if identifier.via is None:
            continue
        location = entity.locations[identifier.via.split(".")[0]]
        connector = app.connectors.get(location.using)
        if not isinstance(connector, SqlConnector):
            continue  # owl check refuses a via on a REST location
        rules = RULES_BY_CONNECTOR_TYPE[connector.type]
        try:
            sql = resolution_lookup(entity, name, rules.sqlglot_dialect)
        except ConfigError:
            continue  # owl check reports a via lookup it cannot rewrite
        yield Statement(
            f"identifier {name}",
            location.using,
            sql,
            rules,
            location.pii,
            _allowed(connector),
            (name, identifier.via),
            max_rows=MAX_MATCH_ROWS,
            row_ceiling=connector.guards.row_ceiling,
        )
    yield from _of_healthy_sample(app, entity)


def _of_location(
    app: LoadedApplication, name: str, location: Location | FanOutLocation
) -> Iterator[Statement]:
    connector = app.connectors.get(location.using)
    if isinstance(connector, SqlConnector):
        rules: DialectRules | None = RULES_BY_CONNECTOR_TYPE[connector.type]
        allowed, ceiling = _allowed(connector), connector.guards.row_ceiling
    else:
        rules, allowed, ceiling = None, set(), DEFAULT_ROW_CEILING
    rows = rows_read(location)
    for label, sql in ((name, location.lookup), (f"{name} (fallback)", location.fallback)):
        if sql is not None:
            yield Statement(
                label,
                location.using,
                sql,
                rules,
                location.pii,
                allowed,
                max_rows=rows,
                row_ceiling=ceiling,
            )


def _of_healthy_sample(app: LoadedApplication, entity: Entity) -> Iterator[Statement]:
    sample = entity.healthy_sample
    connector = app.connectors.get(sample.using) if sample else None
    if sample is None or not isinstance(connector, SqlConnector):
        return  # the Healthy sample check says so
    rules = RULES_BY_CONNECTOR_TYPE[connector.type]
    for where, sql in _sample_lookups(sample):
        yield Statement(
            where,
            sample.using,
            sql,
            rules,
            [],
            _allowed(connector),
            max_rows=SAMPLE_ROWS,
            row_ceiling=connector.guards.row_ceiling,
        )


def _sample_lookups(sample: HealthySample) -> Iterator[tuple[str, str]]:
    yield "healthy_sample", sample.lookup
    for name, narrowing in sample.narrow.items():
        yield f"healthy_sample.{name}", narrowing.lookup


def _healthy_sample(app: LoadedApplication, entity: Entity) -> list[SaveCheck]:
    """The sample's own rules; its statements also get every check a lookup gets."""
    sample = entity.healthy_sample
    if sample is None:
        return []
    connector = app.connectors.get(sample.using)
    if not isinstance(connector, SqlConnector):
        message = f"{sample.using} is not a SQL connector: the sample needs an indexed SELECT"
        return [_fail(HEALTHY_SAMPLE, message, "healthy_sample")]
    found = []
    for parameter, ref in sample.parameters.items():
        source = app.connectors.get(entity.locations[split_field_ref(ref)[0]].using)
        if not isinstance(source, SqlConnector):
            # JSON has no dates, so a date from a REST field would be bound as text, which the
            # server converts by its language and date format.
            message = f"parameter {parameter} comes from {ref}: use a SQL location's field"
            found.append(_fail(HEALTHY_SAMPLE, message, "healthy_sample"))
    for statement in _of_healthy_sample(app, entity):
        assert statement.rules is not None  # a SQL connector's
        if refusal(statement) is not None:
            continue  # the read-only check reports it
        dialect = statement.rules.sqlglot_dialect
        read = shape(statement.sql, dialect)
        names = {name.lower() for _, _, name in selected(read.projections, read.tables)}
        if entity.root_key.lower() not in names:
            message = f"it must select the root key as {entity.root_key}, the subject to run on"
            found.append(_fail(HEALTHY_SAMPLE, message, statement.location))
        missing = sorted(parameters_of(statement.sql, dialect) - set(sample.parameters))
        if missing:
            message = f"say where {', '.join(missing)} comes from in healthy_sample's parameters"
            found.append(_fail(HEALTHY_SAMPLE, message, statement.location))
    return found or [
        _pass(HEALTHY_SAMPLE, f"it selects {entity.root_key}, with each parameter's source")
    ]


def _allowed(connector: SqlConnector) -> set[str]:
    # The databases a lookup may name in any environment the connector is bound in.
    return {db for binding in connector.environments.values() for db in binding.databases_allowed()}


def _identifiers(app: LoadedApplication, entity: Entity) -> list[SaveCheck]:
    found = []
    for name, identifier in entity.identifiers.items():
        if identifier.via is None:
            continue
        connector = app.connectors[entity.locations[identifier.via.split(".")[0]].using]
        if not isinstance(connector, SqlConnector):
            found.append(
                _fail("Identifiers", f"identifier {name} via {identifier.via}: use a SQL location")
            )
            continue
        try:
            resolution_lookup(entity, name, RULES_BY_CONNECTOR_TYPE[connector.type].sqlglot_dialect)
        except ConfigError as err:
            found.append(_fail("Identifiers", str(err), f"identifier {name}"))
    return found


def _expectations(probe: Probe, entity: Entity) -> SaveCheck:
    try:
        check_probe(probe, entity)
    except ConfigError as err:
        return _fail("Expectations", str(err))
    count = sum(len(step.expect) for step in probe.steps)
    return _pass("Expectations", f"{count} expectations read locations of {entity.entity}")


def _environments(app: LoadedApplication, probe: Probe, entity: Entity) -> list[SaveCheck]:
    known = app.application.environments
    unknown = [e for e in probe.environments or [] if e not in known]
    if unknown:
        return [
            _fail(
                "Environments",
                f"not environments of {app.application.application}: {', '.join(unknown)}",
            )
        ]
    found = []
    for environment in probe.environments or list(known):
        unbound = sorted(
            {
                f"{name} ({using})"
                for name, using in _locations(entity)
                if environment not in getattr(app.connectors.get(using), "environments", {})
            }
        )
        if unbound:
            found.append(
                _fail(
                    "Environments",
                    f"enabled in {environment}, where {', '.join(unbound)} has no binding; "
                    f"bind it or leave {environment} out of the probe's environments",
                )
            )
    return found or [
        _pass(
            "Environments",
            f"every location is bound in {', '.join(probe.environments or list(known))}",
        )
    ]


def _locations(entity: Entity) -> Iterator[tuple[str, str]]:
    for name, location in entity.locations.items():
        yield name, location.using
        for child, fan_out in location.each.items():
            yield f"{name}.{child}", fan_out.using


def _row_ceilings(app: LoadedApplication, entity: Entity) -> list[SaveCheck]:
    found = []
    for name, location in entity.locations.items():
        connector = app.connectors.get(location.using)
        if connector is None or location.max_rows is None:
            continue
        ceiling = connector.guards.row_ceiling
        if location.max_rows > ceiling:
            found.append(
                _fail(
                    "Row ceiling",
                    f"max_rows {location.max_rows:,} is above connector {location.using}'s row "
                    f"ceiling of {ceiling:,}; lower max_rows, or have the connector's owner raise "
                    "row_ceiling in its guards",
                    name,
                )
            )
    return found or [_pass("Row ceiling", "every location's max_rows is within its connector's")]


def _read_only(statements: list[Statement]) -> list[SaveCheck]:
    failed = []
    for statement in statements:
        reason = refusal(statement)
        if reason is not None:
            failed.append(_fail(READ_ONLY, reason, statement.location))
    if failed:
        return failed
    return [
        _pass(
            READ_ONLY,
            "every SQL lookup is a single SELECT with its keys bound as parameters; "
            "every REST lookup is a GET",
        )
    ]


def refusal(statement: Statement) -> str | None:
    """Why the guard would refuse the statement, or None. Checked with placeholder values."""
    try:
        if statement.rules is None:
            build_request(statement.sql, Placeholders())
        else:
            # The cap the lookup runs with, as the adapter sets it: one more row than it reads.
            cap = min(statement.max_rows, statement.row_ceiling) + 1
            prepare(
                statement.sql, Placeholders(), statement.rules, cap, statement.allowed_databases
            )
    except GuardViolation as err:
        return str(err)
    return None


class Placeholders(dict[str, object]):
    """Parameters for the classifier: any name has a value, so only the SQL is judged."""

    def __contains__(self, key: object) -> bool:
        return True

    def __getitem__(self, key: str) -> object:
        return "0"


def _catalog(app: LoadedApplication) -> dict[tuple[str, str], CatalogTable]:
    tables = app.catalog.tables if app.catalog else []
    return {(t.connector, t.table.lower()): t for t in tables}


def _time_filters(
    statements: list[Statement], tables: dict[tuple[str, str], CatalogTable]
) -> list[SaveCheck]:
    found = [
        _fail(TIME_FILTER, message, statement.location)
        for statement in statements
        for message in _time_problems(statement, tables)
    ]
    return found or [
        _pass(
            TIME_FILTER,
            "every lookup on a Transaction, Log or Audit table is keyed or time-bounded",
        )
    ]


def _time_problems(statement: Statement, tables: dict[tuple[str, str], CatalogTable]) -> list[str]:
    """For each table the lookup reads that needs a time window, what is missing."""
    if statement.rules is None or refusal(statement) is not None:
        return []
    read = shape(statement.sql, statement.rules.sqlglot_dialect)
    problems = []
    for table in sorted(set(read.tables.values())):
        entry = tables.get((statement.connector, table))
        time = _time_column(entry) if entry else None
        if entry is None or time is None:
            continue  # only labels that need a time column carry the rule
        if keyed(read.conditions, table, read.tables, _keys(entry)):
            continue
        bound = time_bound(read.conditions, table, time, read.tables)
        if bound is TimeBound.CONSTANT:
            problems.append(
                f"{entry.table} is a {_label(entry)} table, and {time} is bounded by a fixed "
                "value, which can reach far into the past: bound it by a parameter "
                f"({time} >= :since) or relative to now"
            )
        elif bound is TimeBound.NONE:
            problems.append(
                f"{entry.table} is a {_label(entry)} table: filter on its time column {time} "
                f"from a parameter (for example {time} >= :since), or look rows up by a key "
                "(a primary key, link or external key column = :parameter, or IN a short list)"
            )
    return problems


def _keys(entry: CatalogTable) -> set[str]:
    roles = {ColumnRole.PRIMARY_KEY, ColumnRole.LINK, ColumnRole.EXTERNAL_KEY}
    return {name.lower() for name, column in entry.columns.items() if roles & set(column.roles)}


def _label(entry: CatalogTable) -> str:
    return entry.label.value.replace("_", " ").capitalize()


def _time_column(entry: CatalogTable) -> str | None:
    return next((name for name, c in entry.columns.items() if ColumnRole.TIME in c.roles), None)


def _pii(
    statements: list[Statement], tables: dict[tuple[str, str], CatalogTable]
) -> list[SaveCheck]:
    found = [
        _fail(PII_MASKED, message, statement.location)
        for statement in statements
        for message in _unmasked(statement, tables)
    ]
    return found or [_pass(PII_MASKED, "every PII column a lookup selects is masked")]


def _unmasked(statement: Statement, tables: dict[tuple[str, str], CatalogTable]) -> list[str]:
    """For each PII column the lookup selects without masking it, what to add."""
    if statement.rules is None or refusal(statement) is not None:
        return []
    read = shape(statement.sql, statement.rules.sqlglot_dialect)
    masked = {name.lower() for name in statement.pii}
    problems = []
    for table, source, name in selected(read.projections, read.tables):
        entry = tables.get((statement.connector, table)) if table else None
        if entry is None:
            continue
        pii = [c for c, column in entry.columns.items() if ColumnRole.PII in column.roles]
        exposed = pii if source == "*" else [c for c in pii if c.lower() == source.lower()]
        for column in exposed:
            shown = column if source == "*" else name
            if shown.lower() not in masked:
                problems.append(
                    f"{column} is PII in the catalog ({statement.connector}.{entry.table}): "
                    f"add {shown} to the location's pii so it is masked"
                )
    return problems


def _pass(check: str, message: str, location: str | None = None) -> SaveCheck:
    return SaveCheck(check=check, status=CheckStatus.PASS, message=message, location=location)


def _fail(check: str, message: str, location: str | None = None) -> SaveCheck:
    return SaveCheck(check=check, status=CheckStatus.FAIL, message=message, location=location)


def parse_failure(err: ConfigError | ParseError) -> SaveCheck:
    """The check a draft that cannot be read fails."""
    return _fail("Probe", str(err).removeprefix("the draft: "))
