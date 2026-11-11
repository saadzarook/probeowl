# Author: @SaadZarook
"""Time offsets: a time with an offset is never compared with one without, either way round.

SQL Server compares a datetimeoffset with a datetime2 column by reading the column as UTC, and
Oracle reads a plain TIMESTAMP in the session's time zone, so a +04:00 value matches rows four
hours off when the column holds local time. The other way round, a value without an offset
against a datetimeoffset column is read as UTC too. ProbeOwl cannot know which zone a time
without an offset is in, so the lookup must convert the value itself. A typed time is refused at
run time for the same reason, with an offset against a column without one or without one
against a column with one: SQL Server drops the offset of text, and reads text without one as
UTC.

Column types come from the latest schema import of each connector.
"""

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlglot.errors import ParseError

from probeowl.checks.sql_shape import (
    Comparison,
    Conversion,
    comparisons,
    parameters_of,
    selected,
    shape,
)
from probeowl.connectors.oracle import WITH_TIME_ZONE
from probeowl.engine.identifiers import resolution_lookup
from probeowl.errors import ConfigError, RequestError
from probeowl.guards.dialects import RULES_BY_CONNECTOR_TYPE
from probeowl.model.catalog import ColumnRole
from probeowl.model.check import LoadedApplication
from probeowl.model.common import split_field_ref
from probeowl.model.connector import SqlConnector
from probeowl.model.entity import Entity, LocationBase
from probeowl.model.save_check import CheckStatus, SaveCheck
from probeowl.model.schema import SchemaSnapshot
from probeowl.store.schemas import latest_snapshots

TIME_OFFSET = "Time offset"

ColumnKey = tuple[str, str, str]  # connector, table, column; table and column in lower case

# A typed time, with or without an offset: 2026-11-07T14:00, ...14:00+04:00, ...14:00 +0400, ...Z.
_TYPED_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?\s*(Z|[+-]\d{2}(?::?\d{2})?)?$",
    re.IGNORECASE,
)
_WITH_OFFSET = ("datetimeoffset", "with time zone", "with local time zone")
# datetime also covers datetime2; SQL Server's timestamp is a row version, never compared to a time.
_TIMES = ("datetime", "smalldatetime", "date", "time", "timestamp")
# A value with an offset against a column without one.
_HOW_COMPARED = {
    "tsql": "SQL Server compares them in UTC, so a +04:00 value matches rows 4 hours off "
    "when the column holds local time",
    "oracle": "Oracle reads the column in the session's time zone, so the value can match rows "
    "hours off",
    "mysql": "MariaDB has no type with an offset, so the comparison can be hours off",
}
# The other way round: a value without an offset against a column with one. MariaDB has none.
_HOW_READ = {
    "tsql": "SQL Server reads the value as UTC, so a value in local time matches rows hours off",
    "oracle": "Oracle reads the value in the session's time zone, so it can match rows hours off",
}
_ENTER_WITH_OFFSET = "enter the time with its offset, such as 2026-11-07T14:00+04:00"


@dataclass(frozen=True)
class ColumnTypes:
    """Column types as imported, and which connectors have an import at all."""

    types: dict[ColumnKey, str]
    imported: frozenset[str]

    def of(self, connector: str, table: str, column: str) -> str | None:
        """The column's type in lower case, or None when it was not imported."""
        return self.types.get((connector, table.lower(), column.lower()))

    def columns_of(self, connector: str, table: str) -> dict[str, str]:
        """Every imported column of a table, by name, with its type."""
        wanted = (connector, table.lower())
        return {key[2]: kind for key, kind in self.types.items() if key[:2] == wanted}


def column_types(snapshots: Iterable[SchemaSnapshot]) -> ColumnTypes:
    """Each column's type, from the newest import of each connector."""
    types: dict[ColumnKey, str] = {}
    ordered = sorted(snapshots, key=lambda s: s.imported_at)
    for snapshot in ordered:
        for table in snapshot.tables:
            for column in table.columns:
                key = (snapshot.connector, table.table.lower(), column.name.lower())
                types[key] = column.type.lower()
    return ColumnTypes(types, frozenset(s.connector for s in ordered))


def imported_types(
    store: Engine, workspace_id: str, application: str, environment: str | None = None
) -> ColumnTypes:
    """The column types of an application's latest imports, in one environment or any."""
    with store.connect() as conn:
        snapshots = latest_snapshots(conn, workspace_id, application)
    return column_types(s for s in snapshots if environment in (None, s.environment))


def has_offset(column_type: str) -> bool | None:
    """True or False for a time type; None for any other type."""
    if any(marker in column_type for marker in _WITH_OFFSET):
        return True
    return False if column_type.startswith(_TIMES) else None


@dataclass(frozen=True)
class _Lookup:
    label: str
    connector: str
    sql: str
    dialect: str
    # Where each parameter's value comes from: <location>.<field>, or None when a person typed it.
    sources: dict[str, str | None]


# What is wrong with one comparison: lookup, comparison, source ref, source type, target type.
_Message = Callable[[_Lookup, Comparison, str, str, str], SaveCheck]


def offset_checks(app: LoadedApplication, entity: Entity, types: ColumnTypes) -> list[SaveCheck]:
    """A failure for each lookup comparing a time with an offset with one without, either way.

    A warning for each connector whose schema is not imported, when a lookup compares a column
    the catalog marks as time there: its types are unknown.
    """
    failures: list[SaveCheck] = []
    for lookup in _lookups(app, entity):
        for compared in _compared(lookup):
            ref = lookup.sources.get(compared.parameter)
            source = _source_column(app, entity, ref) if ref else None
            if ref is None or source is None:
                continue  # typed by a person, or not a SQL column: checked when the run starts
            source_type = types.of(*source)
            target_type = types.of(lookup.connector, compared.table, compared.column)
            if source_type is None or target_type is None:
                continue
            # The conversion each mismatch needs, and how to say what is wrong.
            rules: dict[tuple[bool | None, bool | None], tuple[Conversion, _Message]] = {
                (True, False): (Conversion.DROPS, _mismatch),
                (False, True): (Conversion.SETS, _unset),
            }
            rule = rules.get((has_offset(source_type), has_offset(target_type)))
            if rule is not None and compared.conversion is not rule[0]:
                failures.append(rule[1](lookup, compared, ref, source_type, target_type))
    failures += _offsets_unread(app, entity, types)
    warnings = [
        SaveCheck(
            check=TIME_OFFSET,
            status=CheckStatus.WARN,
            message=f"{connector}'s schema is not imported, so its time columns are not checked "
            "for offsets: import it on the Tables page",
        )
        for connector in sorted(unchecked_connectors(app, entity, types))
    ]
    passed = SaveCheck(
        check=TIME_OFFSET,
        status=CheckStatus.PASS,
        message="no lookup compares a time with an offset with one without",
    )
    return failures + warnings or [passed]


def _offsets_unread(app: LoadedApplication, entity: Entity, types: ColumnTypes) -> list[SaveCheck]:
    """Oracle lookups that select a TIMESTAMP WITH TIME ZONE column as it is: thin mode reads it
    without its offset, so at run time the adapter refuses it."""
    found = []
    for lookup in _lookups(app, entity):
        if lookup.dialect != "oracle":
            continue
        try:
            read = shape(lookup.sql, lookup.dialect)
        except ParseError:
            continue  # the Read-only SQL check reports it
        for table, source, name in selected(read.projections, read.tables):
            if table is None:
                continue
            columns = types.columns_of(lookup.connector, table)
            picked = columns if source == "*" else {source.lower(): columns.get(source.lower())}
            for column, kind in picked.items():
                if kind and "with time zone" in kind and "local" not in kind:
                    shown = column if source == "*" else name
                    found.append(
                        SaveCheck(
                            check=TIME_OFFSET,
                            status=CheckStatus.FAIL,
                            location=lookup.label,
                            message=WITH_TIME_ZONE.format(column=shown),
                        )
                    )
    return found


def unchecked_connectors(app: LoadedApplication, entity: Entity, types: ColumnTypes) -> set[str]:
    """Connectors with no import where a lookup compares a column the catalog marks as time."""
    unknown: set[str] = set()
    times = _catalog_times(app)
    for lookup in _lookups(app, entity):
        for compared in _compared(lookup):
            ref = lookup.sources.get(compared.parameter)
            source = _source_column(app, entity, ref) if ref else None
            target = (lookup.connector, compared.table, compared.column)
            if source is not None and (source in times or target in times):
                unknown |= {source[0], lookup.connector} - types.imported
    return unknown


def unchecked_without_imports(app: LoadedApplication) -> list[str]:
    """For owl check, which reads no imports: each connector the check would need one for."""
    none = column_types([])
    return sorted({c for e in app.entities.values() for c in unchecked_connectors(app, e, none)})


@dataclass(frozen=True)
class TypedTime:
    """A time a person typed, and its offset as typed: +04:00, Z, or None when it has none."""

    offset: str | None


def typed_time(value: str) -> TypedTime | None:
    """The time typed, such as 2026-11-07T14:00+04:00; None for a value that is not a time."""
    match = _TYPED_TIME.match(value.strip())
    return TypedTime(match.group(1)) if match else None


def refuse_typed_time(
    app: LoadedApplication,
    entity: Entity,
    identifier: str,
    typed: TypedTime,
    environment: str,
    types: ColumnTypes,
) -> None:
    """RequestError when a typed time's offset does not fit its column, either way round.

    Also when the column's type is unknown because its connector's schema is not imported: the
    run does not guess.
    """
    # The conversion that makes the typed time fit whatever the column holds.
    fits = Conversion.DROPS if typed.offset else Conversion.SETS
    for lookup in _lookups(app, entity):
        if identifier not in lookup.sources or lookup.sources[identifier] is not None:
            continue  # this lookup gets the identifier from a database, not from the person
        for compared in _compared(lookup):
            if compared.parameter != identifier or compared.conversion is fits:
                continue
            where = f"{compared.table}.{compared.column}"
            column_type = types.of(lookup.connector, compared.table, compared.column)
            if column_type is None and lookup.connector not in types.imported:
                how = (
                    f"enter it without {typed.offset}, in the column's own time"
                    if typed.offset
                    else _ENTER_WITH_OFFSET
                )
                raise RequestError(
                    f"{lookup.connector}'s schema is not imported in {environment}, so ProbeOwl "
                    f"cannot tell whether {where} stores time with an offset",
                    {"value": f"{how}, or import {lookup.connector}'s schema first"},
                )
            offset = has_offset(column_type) if column_type is not None else None
            if typed.offset and offset is False:
                raise RequestError(
                    f"{where} is {column_type}: this column stores time without an offset",
                    {
                        "value": "this column stores time without an offset; enter it without "
                        f"{typed.offset}, in the column's own time"
                    },
                )
            if typed.offset is None and offset:
                raise RequestError(
                    f"{where} is {column_type}: this column stores time with an offset",
                    {"value": f"this column stores time with an offset; {_ENTER_WITH_OFFSET}"},
                )


def _mismatch(
    lookup: _Lookup, compared: Comparison, ref: str, source_type: str, target_type: str
) -> SaveCheck:
    p = f":{compared.parameter}"
    examples = {
        "tsql": f"CAST({p} AS datetime2) for the value's own clock time, or "
        f"CAST({p} AT TIME ZONE '<the column's zone>' AS datetime2); AT TIME ZONE alone "
        "keeps the offset, so it is not enough",
        # Thin mode sends the value as its UTC instant, so a plain CAST gives its UTC time.
        "oracle": f"CAST({p} AT TIME ZONE '<the column's zone>' AS TIMESTAMP) for the column's "
        f"local time, or SYS_EXTRACT_UTC({p}) when the column holds UTC",
        "mysql": f"CAST({p} AS DATETIME)",
    }
    return SaveCheck(
        check=TIME_OFFSET,
        status=CheckStatus.FAIL,
        location=lookup.label,
        message=f"{compared.table}.{compared.column} is {target_type}, with no offset, but {p} "
        f"comes from {ref}, a {source_type}. {_HOW_COMPARED[lookup.dialect]}. ProbeOwl cannot "
        "know whether the column holds local time or UTC: convert the value explicitly, for "
        f"example {examples[lookup.dialect]}",
    )


def _unset(
    lookup: _Lookup, compared: Comparison, ref: str, source_type: str, target_type: str
) -> SaveCheck:
    p = f":{compared.parameter}"
    examples = {
        "tsql": f"TODATETIMEOFFSET({p}, '+04:00') with the value's own offset, or "
        f"{p} AT TIME ZONE '<the value's zone>'",
        "oracle": f"FROM_TZ({p}, '+04:00') with the value's own offset",
    }
    return SaveCheck(
        check=TIME_OFFSET,
        status=CheckStatus.FAIL,
        location=lookup.label,
        message=f"{compared.table}.{compared.column} is {target_type}, with an offset, but {p} "
        f"comes from {ref}, a {source_type}, with none. {_HOW_READ[lookup.dialect]}. ProbeOwl "
        "cannot know which zone the value is in: give it its offset explicitly, for example "
        f"{examples[lookup.dialect]}",
    )


def _catalog_times(app: LoadedApplication) -> set[ColumnKey]:
    tables = app.catalog.tables if app.catalog else []
    return {
        (table.connector, table.table.lower(), name.lower())
        for table in tables
        for name, column in table.columns.items()
        if ColumnRole.TIME in column.roles
    }


def _compared(lookup: _Lookup) -> list[Comparison]:
    try:
        return comparisons(lookup.sql, lookup.dialect)
    except ParseError:
        return []  # the Read-only SQL check reports it


def _lookups(app: LoadedApplication, entity: Entity) -> Iterator[_Lookup]:
    """Every SQL lookup of the entity, with where each of its parameters comes from."""
    typed: dict[str, str | None] = {entity.root_key: None}
    for name, location in entity.locations.items():
        sources = dict(typed)
        if location.key_from is not None:
            sources[split_field_ref(location.key_from)[1]] = location.key_from
        yield from _of(app, name, location, sources)
        for child, fan_out in location.each.items():
            yield from _of(app, f"{name}.{child}", fan_out, typed, parent=name)
    for name, identifier in entity.identifiers.items():
        if identifier.via is not None:
            yield from _resolution(app, entity, name, identifier.via)
    sample = entity.healthy_sample
    connector = app.connectors.get(sample.using) if sample else None
    if sample is not None and isinstance(connector, SqlConnector):
        dialect = RULES_BY_CONNECTOR_TYPE[connector.type].sqlglot_dialect
        sources = dict[str, str | None](sample.parameters)
        yield _Lookup("healthy_sample", sample.using, sample.lookup, dialect, sources)
        for narrow, narrowing in sample.narrow.items():
            label = f"healthy_sample.{narrow}"
            yield _Lookup(label, sample.using, narrowing.lookup, dialect, sources)


def _of(
    app: LoadedApplication,
    label: str,
    location: LocationBase,
    sources: dict[str, str | None],
    parent: str | None = None,
) -> Iterator[_Lookup]:
    connector = app.connectors.get(location.using)
    if not isinstance(connector, SqlConnector):
        return
    dialect = RULES_BY_CONNECTOR_TYPE[connector.type].sqlglot_dialect
    for suffix, sql in (("", location.lookup), (" (fallback)", location.fallback)):
        if sql is None:
            continue
        try:
            names = parameters_of(sql, dialect)
        except ParseError:
            continue  # the Read-only SQL check reports it
        found = {p: sources[p] if p in sources else _of_row(parent, p) for p in names}
        yield _Lookup(label + suffix, location.using, sql, dialect, found)


def _of_row(parent: str | None, parameter: str) -> str | None:
    # A fan-out lookup's other parameters are its parent's row, by column name.
    return f"{parent}.{parameter}" if parent else None


def _resolution(app: LoadedApplication, entity: Entity, name: str, via: str) -> Iterator[_Lookup]:
    location = entity.locations[split_field_ref(via)[0]]
    connector = app.connectors.get(location.using)
    if not isinstance(connector, SqlConnector):
        return
    dialect = RULES_BY_CONNECTOR_TYPE[connector.type].sqlglot_dialect
    try:
        sql = resolution_lookup(entity, name, dialect)
    except ConfigError:
        return  # owl check reports a via lookup it cannot rewrite
    yield _Lookup(f"identifier {name}", location.using, sql, dialect, {name: None})


def _source_column(app: LoadedApplication, entity: Entity, ref: str) -> ColumnKey | None:
    """The table column a <location>.<field> is read from, when its location is SQL."""
    location_name, field = split_field_ref(ref)
    location = entity.locations.get(location_name)
    connector = app.connectors.get(location.using) if location else None
    if location is None or not isinstance(connector, SqlConnector):
        return None
    dialect = RULES_BY_CONNECTOR_TYPE[connector.type].sqlglot_dialect
    try:
        read = shape(location.lookup, dialect)
    except ParseError:
        return None
    columns = selected(read.projections, read.tables)
    for table, source, name in columns:
        if table and source != "*" and name.lower() == field.lower():
            return (location.using, table, source.lower())
    stars = [table for table, source, _ in columns if source == "*" and table]
    return (location.using, stars[0], field.lower()) if len(stars) == 1 else None
