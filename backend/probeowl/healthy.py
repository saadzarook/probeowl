# Author: @SaadZarook
"""Healthy comparison: which fields to compare, the sample's parameters, and the field diff."""

import hashlib
import hmac
import json
import secrets
import threading
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import JsonValue
from sqlglot.errors import ParseError

from probeowl.checks.sql_shape import parameters_of, selected, shape
from probeowl.comparison import ArrayMatch, Options, compare
from probeowl.engine.masking import MASKED, masked_fields, to_json
from probeowl.engine.typed_values import restore
from probeowl.errors import RequestError
from probeowl.guards.dialects import RULES_BY_CONNECTOR_TYPE
from probeowl.guards.statement import ParameterValue
from probeowl.model.catalog import CatalogTable, ColumnRole
from probeowl.model.check import LoadedApplication
from probeowl.model.common import split_field_ref
from probeowl.model.connector import SqlConnector
from probeowl.model.entity import Entity, HealthySample, LocationBase
from probeowl.model.healthy import ComparedField, FieldResult
from probeowl.model.run import LocationResult, Outcome, Run

# Enough to tell a person what they need across several open investigations, without keeping
# fingerprints of everything a long-running owl serve has ever read.
KEEP_RUNS = 500
_READ = {Outcome.FOUND, Outcome.MISSING}  # the lookup answered, so its answer can be compared

MAX_CANDIDATES = 3  # each is a whole run on a production replica
# The healthy sample reads one more than it uses, as one may be the failing subject itself.
SAMPLE_ROWS = MAX_CANDIDATES + 1


@dataclass(frozen=True)
class CompareFields:
    """A location's compare fields: each name as its rows have it, and the ones expected to vary."""

    fields: tuple[str, ...]
    varying: frozenset[str]


class Fingerprints:
    """Keyed fingerprints of masked compare fields, for one owl serve session. Memory only.

    The key is random and never leaves this object, and nothing here can return a fingerprint:
    a caller can only ask whether two runs' values match. A stored or returned hash of a short
    value, such as a phone number, could be brute-forced.
    """

    def __init__(self, keep_runs: int = KEEP_RUNS):
        self._key = secrets.token_bytes(32)
        self._keep_runs = keep_runs
        self._lock = threading.Lock()
        self._runs: OrderedDict[str, dict[tuple[str, str], list[bytes | None]]] = OrderedDict()

    def __repr__(self) -> str:
        return f"Fingerprints({len(self._runs)} runs)"

    def remember(self, run_id: str, location: str, field: str, values: Iterable[Any]) -> None:
        """Fingerprint a masked field's values as they are read. A null stays null."""
        found = [None if value is None else self._digest(value) for value in values]
        with self._lock:
            run = self._runs.setdefault(run_id, {})
            self._runs.move_to_end(run_id)
            run.setdefault((location, field), []).extend(found)
            while len(self._runs) > self._keep_runs:
                self._runs.popitem(last=False)

    def compare(self, left: str, right: str, location: str, field: str) -> FieldResult:
        """Whether two runs read the same values. Not compared if either was not fingerprinted."""
        with self._lock:
            ours = self._runs.get(left, {}).get((location, field))
            theirs = self._runs.get(right, {}).get((location, field))
        if ours is None or theirs is None:
            return FieldResult.NOT_COMPARED
        same = _in_order(ours) == _in_order(theirs)
        return FieldResult.SAME if same else FieldResult.DIFFERENT

    def _digest(self, value: Any) -> bytes:
        text = json.dumps(to_json(value), sort_keys=True).encode()
        return hmac.new(self._key, text, hashlib.sha256).digest()


def _in_order(values: list[bytes | None]) -> list[bytes]:
    return sorted(value or b"" for value in values)


def compare_fields(app: LoadedApplication, location: LocationBase) -> CompareFields:
    """The catalog's marked columns for a SQL location, its compare list for a REST one."""
    connector = app.connectors.get(location.using)
    if not isinstance(connector, SqlConnector):
        return CompareFields(tuple(location.compare), frozenset(location.expected_to_vary))
    dialect = RULES_BY_CONNECTOR_TYPE[connector.type].sqlglot_dialect
    try:
        read = shape(location.lookup, dialect)
    except ParseError:
        return CompareFields((), frozenset())  # the read-only save check reports it
    tables = {
        (t.connector, t.table.lower()): t for t in (app.catalog.tables if app.catalog else [])
    }
    found: dict[str, bool] = {}
    for table, source, name in selected(read.projections, read.tables):
        entry = tables.get((location.using, table)) if table else None
        if entry is not None:
            found.update(_marked(entry, source, name))
    return CompareFields(tuple(found), frozenset(f for f, varies in found.items() if varies))


def _marked(entry: CatalogTable, source: str, name: str) -> Iterator[tuple[str, bool]]:
    for column, spec in entry.columns.items():
        if ColumnRole.COMPARE in spec.roles and source.lower() in ("*", column.lower()):
            shown = column if source == "*" else name
            yield shown, ColumnRole.EXPECTED_TO_VARY in spec.roles


def masked_compare_fields(
    app: LoadedApplication, entity: Entity
) -> dict[str, list[tuple[str, str]]]:
    """For each lookup a run can send, the (location, field) to fingerprint from its rows."""
    found: dict[str, list[tuple[str, str]]] = {}
    for name, location in _locations(entity):
        masked = masked_fields(entity, location)
        fields = [f for f in compare_fields(app, location).fields if f.lower() in masked]
        for lookup in filter(None, (location.lookup, location.fallback)):
            found.setdefault(lookup, []).extend((name, field) for field in fields)
    return found


def field_values(rows: Iterable[Mapping[str, Any]], field: str) -> list[Any]:
    """A field's value in each row, column case ignored, a dotted path read through REST JSON."""
    return [_read(row, field.lower().split(".")) for row in rows]


def _read(value: Any, path: list[str]) -> Any:
    for part in path:
        if not isinstance(value, Mapping):
            return None
        value = next((item for key, item in value.items() if key.lower() == part), None)
    return value


def sample_parameters(
    run: Run, entity: Entity, sample: HealthySample, sql: str, dialect: str
) -> dict[str, ParameterValue]:
    """The sample lookup's parameters from the failing run, each bound as its real type."""
    return {
        name: _sample_parameter(run, entity, sample.parameters[name], name)
        for name in sorted(parameters_of(sql, dialect))
    }


def _sample_parameter(run: Run, entity: Entity, ref: str, name: str) -> ParameterValue:
    source, field = split_field_ref(ref)
    paste = "this run cannot find a healthy record by itself; paste a known-good ID"
    found = next(
        (r for r in run.locations if r.location == source and r.parent is None and r.evidence),
        None,
    )
    if found is None or found.evidence is None or len(found.evidence.rows) != 1:
        raise RequestError(paste, {name: f"the sample needs {ref}, which this run did not read"})
    [value] = field_values(found.evidence.rows, field)
    if field.lower() == entity.root_key.lower() and run.subject not in (None, MASKED):
        # The key is the run's subject: stored masked when it is PII, and pasted for the
        # comparison (#66), where the rows show it masked.
        value = run.subject
    if value is None:
        raise RequestError(paste, {name: f"the sample needs {ref}, which is empty in this run"})
    kinds = {column.lower(): kind for column, kind in found.evidence.column_types.items()}
    kind = kinds.get(field.lower())
    if kind is None:
        # Bound as text it could silently match the wrong day. An older run says nothing else.
        raise RequestError(
            "this run does not say what type its fields are; run the probe again and compare",
            {name: f"no type recorded for {ref}"},
        )
    try:
        return restore(value, kind)
    except (ValueError, ArithmeticError):
        raise RequestError(paste, {name: f"{ref} is not the {kind.value} it was"}) from None


def compare_runs(
    failing: Run,
    healthy: Run,
    app: LoadedApplication,
    entity: Entity,
    fingerprints: Fingerprints,
) -> list[ComparedField]:
    """Every location's outcome and compare fields on the two subjects, in location order."""
    compared: list[ComparedField] = []
    for name, location in _locations(entity):
        ours = _results(failing, name)
        theirs = _results(healthy, name)
        compared.append(_outcomes(name, location.using, ours, theirs))
        fields = compare_fields(app, location)
        masked = masked_fields(entity, location)
        for field in fields.fields:
            if field.lower() in masked:
                result = fingerprints.compare(failing.id, healthy.id, name, field)
                compared.append(
                    ComparedField(
                        location=name,
                        connector=location.using,
                        field=field,
                        result=result,
                        masked=True,
                    )
                )
            else:
                compared.append(
                    _plain(name, location.using, field, fields.varying, ours, theirs),
                )
    return compared


def _locations(entity: Entity) -> Iterator[tuple[str, LocationBase]]:
    for name, location in entity.locations.items():
        yield name, location
        yield from location.each.items()


def _results(run: Run, location: str) -> list[LocationResult]:
    return [r for r in run.locations if r.location == location]


def _rows(results: list[LocationResult]) -> list[dict[str, JsonValue]]:
    return [row for r in results if r.evidence is not None for row in r.evidence.rows]


def _summary(results: list[LocationResult]) -> str:
    if not results:
        return "Not read"
    outcomes = sorted({r.outcome.value.replace("_", " ").capitalize() for r in results})
    return f"{', '.join(outcomes)}, {_count(len(_rows(results)))}"


def _count(rows: int) -> str:
    return "1 row" if rows == 1 else f"{rows} rows"


def _outcomes(
    name: str, connector: str, ours: list[LocationResult], theirs: list[LocationResult]
) -> ComparedField:
    left, right = _summary(ours), _summary(theirs)
    if left != right:
        result = FieldResult.DIFFERENT
    elif all(r.outcome in _READ for r in [*ours, *theirs]) and (ours or theirs):
        result = FieldResult.SAME
    else:
        # Two connector errors, or two blocked lookups, show nothing about the data.
        result = FieldResult.NOT_COMPARED
    return ComparedField(
        location=name, connector=connector, failing=left, healthy=right, result=result
    )


def _plain(
    name: str,
    connector: str,
    field: str,
    varying: frozenset[str],
    ours: list[LocationResult],
    theirs: list[LocationResult],
) -> ComparedField:
    left = field_values(_rows(ours), field)
    right = field_values(_rows(theirs), field)
    if not _readable(ours) or not _readable(theirs):
        result = FieldResult.NOT_COMPARED  # one side read nothing, which its outcome row says
    elif compare(left, right, Options(arrays=ArrayMatch.ANY_ORDER)).differences:
        result = FieldResult.EXPECTED_TO_VARY if field in varying else FieldResult.DIFFERENT
    else:
        result = FieldResult.SAME
    return ComparedField(
        location=name,
        connector=connector,
        field=field,
        failing=_shown(left),
        healthy=_shown(right),
        result=result,
    )


def _readable(results: list[LocationResult]) -> bool:
    return any(r.outcome is Outcome.FOUND for r in results)


def _shown(values: list[Any]) -> JsonValue:
    if not values:
        return None
    return values[0] if len(values) == 1 else values
