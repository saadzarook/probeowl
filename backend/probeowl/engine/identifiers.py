# Author: @SaadZarook
"""Identifiers: which one a value is, and the subjects it finds.

An identifier other than the root key is looked up through its `via` location: the
location's own lookup, with its root key condition replaced by one on the identifier's
field. The rows found give the root keys; one is the subject, several are Ambiguous.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from pydantic import JsonValue
from sqlglot import exp
from sqlglot.errors import ParseError

from probeowl.engine.locations import Connector
from probeowl.engine.masking import MASKED, mask_rows, to_json
from probeowl.errors import ConfigError, ProbeOwlError, ReadsStopped, RequestError
from probeowl.model.common import split_field_ref
from probeowl.model.entity import Entity
from probeowl.model.run import Evidence, LocationResult, Match, Outcome

MAX_MATCH_ROWS = 50  # rows read to find the subjects; more is reported, not read


def candidates(entity: Entity, value: str, chosen: str | None) -> list[str]:
    """The identifiers to try for value, in order; RequestError when none can hold it.

    One when it is chosen or a pattern places it. When no pattern does, every identifier
    without a pattern could hold it: the root key first, then each via identifier, so that a
    card ID finds its visitor with no patterns written (#253).
    """
    if chosen is not None or _matching(entity, value):
        return [detect(entity, value, chosen)]
    unplaced = [
        name
        for name, identifier in entity.identifiers.items()
        if identifier.pattern is None and (name == entity.root_key or identifier.via is not None)
    ]
    unplaced.sort(key=lambda name: name != entity.root_key)
    if not unplaced:
        raise RequestError(
            f"this value does not look like any identifier of {entity.entity}",
            {"identifier": f"pick one of: {', '.join(entity.identifiers)}"},
        )
    return unplaced


def detect(entity: Entity, value: str, chosen: str | None) -> str:
    """The identifier value is, chosen or detected from patterns; RequestError if unclear."""
    identifiers = entity.identifiers
    if chosen is not None:
        identifier = identifiers.get(chosen)
        if identifier is None:
            raise RequestError(
                f"{entity.entity} has no identifier {chosen}",
                {"identifier": f"one of: {', '.join(identifiers)}"},
            )
        if identifier.pattern is not None and not re.fullmatch(identifier.pattern, value):
            # C204518 given as a visitor_profile_id: say which identifier it does look like.
            fits = _matching(entity, value)
            hint = f"; it looks like {_one_of(fits)}: choose that identifier" if fits else ""
            raise RequestError(
                f"this value does not look like a {chosen}{hint}",
                {
                    "value": f"a {chosen} matches {identifier.pattern}",
                    **({"identifier": f"use {' or '.join(fits)}"} if fits else {}),
                },
            )
        return chosen
    detected = _matching(entity, value)
    if len(detected) == 1:
        return detected[0]
    if len(detected) > 1:
        raise RequestError(
            f"this value looks like several identifiers: {', '.join(detected)}",
            {"identifier": f"pick one of: {', '.join(detected)}"},
        )
    # A root key without a pattern accepts any value, as before identifiers were detected.
    if identifiers[entity.root_key].pattern is None:
        return entity.root_key
    raise RequestError(
        f"this value does not look like any identifier of {entity.entity}",
        {"identifier": f"pick one of: {', '.join(identifiers)}"},
    )


def _matching(entity: Entity, value: str) -> list[str]:
    """The identifiers whose pattern the value matches."""
    return [
        name
        for name, identifier in entity.identifiers.items()
        if identifier.pattern is not None and re.fullmatch(identifier.pattern, value)
    ]


def _one_of(names: list[str]) -> str:
    return f"a {names[0]}" if len(names) == 1 else f"one of {', '.join(names)}"


def resolution_lookup(entity: Entity, identifier: str, sqlglot_dialect: str) -> str:
    """The via location's lookup, finding rows by the identifier instead of the root key.

    ConfigError when the lookup cannot be rewritten; `owl check` reports it.
    """
    via = entity.identifiers[identifier].via
    if via is None:
        raise ConfigError(f"identifier {identifier} is the root key; it needs no lookup")
    location_name, field_name = split_field_ref(via)
    location = entity.locations[location_name]
    where = f"identifier {identifier} via {via}"
    if location.key_from is not None:
        raise ConfigError(f"{where}: location {location_name} must be keyed by the root key")
    try:
        tree = sqlglot.parse_one(location.lookup, read=sqlglot_dialect)
    except ParseError as err:
        raise ConfigError(f"{where}: the lookup of {location_name} cannot be read") from err
    if not isinstance(tree, exp.Select):
        raise ConfigError(f"{where}: the lookup of {location_name} must be a plain SELECT")
    condition = _root_key_condition(tree, entity.root_key, where)
    others = {p.name for p in tree.find_all(exp.Placeholder)} - {entity.root_key}
    if others:
        raise ConfigError(f"{where}: the lookup may use only :{entity.root_key}")
    condition.replace(
        exp.EQ(
            this=_field_expression(tree, field_name, where),
            expression=exp.Placeholder(this=identifier),
        )
    )
    return tree.sql(dialect=sqlglot_dialect)


def _root_key_condition(tree: exp.Select, root_key: str, where: str) -> exp.EQ:
    # Only a condition every row must meet (ANDed at the top) can be swapped for another.
    found: list[exp.EQ] = []
    clause = tree.args.get("where")
    pending = [clause.this] if clause is not None else []
    while pending:
        node = pending.pop()
        if isinstance(node, exp.And):
            pending.extend([node.left, node.right])
        elif isinstance(node, exp.Paren):
            pending.append(node.this)
        elif isinstance(node, exp.EQ) and _is_parameter(node.expression, root_key):
            found.append(node)
        elif isinstance(node, exp.EQ) and _is_parameter(node.this, root_key):
            found.append(exp.EQ(this=node.expression, expression=node.this))
            node.replace(found[-1])
    if len(found) != 1:
        raise ConfigError(
            f"{where}: the lookup needs exactly one condition <column> = :{root_key}, "
            "ANDed with any others"
        )
    return found[0]


def _is_parameter(node: exp.Expression, name: str) -> bool:
    return isinstance(node, exp.Placeholder) and node.name == name


def _field_expression(tree: exp.Select, field_name: str, where: str) -> exp.Expression:
    for projection in tree.expressions:
        if isinstance(projection, exp.Star):
            return exp.column(field_name)
        if projection.alias_or_name.lower() == field_name.lower():
            selected: exp.Expression = projection.unalias().copy()
            return selected
    raise ConfigError(f"{where}: the lookup does not select {field_name}")


@dataclass(frozen=True)
class Resolution:
    """What an identifier found: the subjects' root keys, and the lookup's result."""

    subjects: list[str]
    result: LocationResult
    matches: list[Match] = field(default_factory=list)


def root_location(entity: Entity) -> str:
    """The location the root key is tried in: the first one looked up by the root key itself."""
    return next(name for name, location in entity.locations.items() if location.key_from is None)


def resolve_root(entity: Entity, value: str, connector: Connector) -> Resolution:
    """The value tried as the root key: found when the root location has a row for it.

    Never raises for the lookup. Its rows are read again by the run, with their evidence.
    """
    name = root_location(entity)
    try:
        found = connector.lookup(
            entity.locations[name].lookup, {entity.root_key: value}, MAX_MATCH_ROWS
        )
    except ReadsStopped:
        raise
    except ProbeOwlError as err:
        return Resolution([], LocationResult(location=name, outcome=Outcome.ERROR, reason=str(err)))
    if found.rows:
        return Resolution([value], LocationResult(location=name, outcome=Outcome.FOUND))
    reason = f"no {entity.entity} has this {entity.root_key}"
    return Resolution([], LocationResult(location=name, outcome=Outcome.MISSING, reason=reason))


def resolve(
    entity: Entity,
    identifier: str,
    value: str,
    lookup: str,
    connector: Connector,
) -> Resolution:
    """Look the value up through the identifier's via location. Never raises for the lookup."""
    via = entity.identifiers[identifier].via
    assert via is not None  # only identifiers with via are resolved
    location_name, _ = split_field_ref(via)
    location = entity.locations[location_name]
    try:
        found = connector.lookup(lookup, {identifier: value}, MAX_MATCH_ROWS)
    except ReadsStopped:
        raise  # the whole run stops, not this one lookup
    except ProbeOwlError as err:
        error = LocationResult(location=location_name, outcome=Outcome.ERROR, reason=str(err))
        return Resolution([], error)
    rows_by_subject: dict[str, dict[str, Any]] = {}
    for row in found.rows:
        key = row.get(entity.root_key)
        if key is not None:
            rows_by_subject.setdefault(str(key), row)
    subjects = list(rows_by_subject)
    masked_fields = [*location.pii, *location.mask]
    evidence = Evidence(
        connector=location.using,
        query=found.query,
        parameters={
            name: MASKED if entity.identifiers[identifier].pii else to_json(v)
            for name, v in found.parameters.items()
        },
        guards=found.guards,
        rows=[_json_row(row) for row in mask_rows(found.rows, masked_fields)],
        started_at=found.started_at,
        duration_ms=found.duration_ms,
    )
    reason = None
    if len(subjects) > 1:
        outcome = Outcome.AMBIGUOUS
        reason = f"{identifier} matches {len(subjects)} subjects"
    elif subjects:
        outcome = Outcome.FOUND
    else:
        outcome = Outcome.MISSING
        reason = f"no {entity.entity} has this {identifier}"
    if found.truncated:
        reason = f"{reason or ''}; {found.truncated}".lstrip("; ")
    matches = [
        Match(subject=subject, fields=_json_row(mask_rows([row], masked_fields)[0]))
        for subject, row in rows_by_subject.items()
    ]
    result = LocationResult(
        location=location_name, outcome=outcome, reason=reason, evidence=evidence
    )
    return Resolution(subjects, result, matches)


def via_connector(
    entity: Entity, identifier: str, connectors: Mapping[str, Connector]
) -> Connector:
    """The connector the identifier's via location uses."""
    via = entity.identifiers[identifier].via
    assert via is not None
    return connectors[entity.locations[split_field_ref(via)[0]].using]


def _json_row(row: dict[str, Any]) -> dict[str, JsonValue]:
    return {key: to_json(value) for key, value in row.items()}
