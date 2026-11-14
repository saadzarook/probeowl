# Author: @SaadZarook
"""Run one probe on one subject: read its locations, then test its expectations."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from difflib import get_close_matches
from typing import Any

from probeowl.connectors.lookup import ReplicaLag
from probeowl.engine.expressions import (
    Binary,
    Call,
    ListOf,
    Node,
    Ref,
    Unary,
    Unavailable,
    duplicates,
    evaluate,
    failing_rows,
    locations_used,
    matching_rows,
    parse,
)
from probeowl.engine.locations import Connector, LocationRead, all_connectors, read_locations
from probeowl.engine.masking import MASKED, masked_fields
from probeowl.errors import ConfigError
from probeowl.model.entity import Entity
from probeowl.model.probe import Probe
from probeowl.model.run import (
    ExpectationEvaluation,
    ExpectationResult,
    LocationResult,
    Outcome,
    RunStatus,
)

# Locations whose data could not be read; an expectation that needs one is skipped.
_UNREADABLE = {Outcome.BLOCKED, Outcome.ERROR, Outcome.STALE, Outcome.NOT_CONFIGURED}
# Locations read but holding no record. Only counting reads them; anything else is skipped,
# so `sum(credit) == sum(debit)` never passes as 0 == 0 on a ledger that is not there.
_EMPTY = {Outcome.MISSING, Outcome.WAITING}


@dataclass(frozen=True)
class ProbeResult:
    """What a probe found: every location, every expectation, and the run's status."""

    status: RunStatus
    locations: list[LocationResult]
    expectations: list[ExpectationEvaluation]


@dataclass(frozen=True)
class _Step:
    when: tuple[str, Node] | None
    expect: list[tuple[str, Node]]


def check_probe(probe: Probe, entity: Entity) -> list[_Step]:
    """Parse every condition and expectation; ConfigError if one cannot run on this entity."""
    if probe.entity != entity.entity:
        raise ConfigError(f"probe {probe.probe} is for entity {probe.entity}, not {entity.entity}")
    steps = []
    for step in probe.steps:
        when = (step.when, _parse(step.when, entity)) if step.when else None
        steps.append(_Step(when, [(text, _parse(text, entity)) for text in step.expect]))
    return steps


def _parse(text: str, entity: Entity) -> Node:
    node = parse(text)
    parents = _fan_out_parents(entity)
    known = [*entity.locations, *parents]
    unknown = locations_used(node) - set(known)
    if unknown:
        raise ConfigError(
            f"{text!r} reads {', '.join(sorted(unknown))}, not a location of {entity.entity}"
            f"{_did_you_mean(sorted(unknown), known)}; its locations are {', '.join(known)}"
        )
    _check_fan_out_reads(text, node, parents, None)
    return node


def _did_you_mean(unknown: list[str], known: list[str]) -> str:
    """' (did you mean card?)' when a misspelt location is close to one that exists."""
    close = [m for name in unknown for m in get_close_matches(name, known, n=1, cutoff=0.6)]
    return f" (did you mean {', '.join(close)}?)" if close else ""


def _fan_out_parents(entity: Entity) -> dict[str, str]:
    return {child: name for name, loc in entity.locations.items() for child in loc.each}


def _check_fan_out_reads(
    text: str, node: Node | None, parents: dict[str, str], row_of: str | None
) -> None:
    # A fan-out location has one lookup per row of its parent, so it is read per row:
    # all(cards, controller.status == status), never controller.status on its own.
    if isinstance(node, Ref) and len(node.parts) > 1 and node.parts[0] in parents:
        parent = parents[node.parts[0]]
        if row_of != parent:
            raise ConfigError(
                f"{text!r} reads {node.parts[0]}, which is looked up per row of {parent}: "
                f"read it inside all({parent}, ...), any(...), none(...) or a where on {parent}"
            )
    elif isinstance(node, Call) and node.rows is not None:
        if node.rows.location in parents:
            raise ConfigError(
                f"{text!r} counts {node.rows.location}, which is looked up per row of "
                f"{parents[node.rows.location]}: use {node.rows.location}.<field> inside "
                f"all({parents[node.rows.location]}, ...) instead"
            )
        _check_fan_out_reads(text, node.rows.where, parents, node.rows.location)
        _check_fan_out_reads(text, node.predicate, parents, node.rows.location)
    elif isinstance(node, Unary):
        _check_fan_out_reads(text, node.operand, parents, row_of)
    elif isinstance(node, Binary):
        _check_fan_out_reads(text, node.left, parents, row_of)
        _check_fan_out_reads(text, node.right, parents, row_of)
    elif isinstance(node, ListOf):
        for item in node.items:
            _check_fan_out_reads(text, item, parents, row_of)


def run_probe(
    probe: Probe,
    entity: Entity,
    subject: str,
    connectors: Mapping[str, Connector | None],
    on_location: Callable[[LocationResult], None] | None = None,
    deadline: float | None = None,
    replica_lag: Mapping[str, ReplicaLag] | None = None,
    caveat_unknown_lag: bool = False,
) -> ProbeResult:
    """Run a probe on one subject. on_location sees each location as it is read.

    deadline: a time.monotonic() value; locations not read by then are Error.
    caveat_unknown_lag: in Production, a Missing record on a replica whose lag is unknown
    carries a caveat, and so does every expectation that reads it.
    """
    steps = check_probe(probe, entity)  # before any lookup: a bad probe never runs
    bound = check_run(probe, entity, connectors)
    reads = read_locations(
        entity, subject, bound, on_location, deadline, replica_lag, caveat_unknown_lag
    )
    data = _Data(reads)
    masked = {name: masked_fields(entity, loc) for name, loc in _all_locations(entity)}
    expectations: list[ExpectationEvaluation] = []
    blocked_inputs = False
    for index, step in enumerate(steps):
        data.read.clear()
        skip_all, unreadable = _skip_reason(step, data)
        by_condition = list(data.read)
        for text, node in step.expect:
            if skip_all is not None:
                caveat = _caveat(by_condition)
                expectations.append(
                    _evaluation(index, text, ExpectationResult.SKIP, skip_all, caveat)
                )
                blocked_inputs = blocked_inputs or unreadable
                continue
            data.read[:] = by_condition
            try:
                passed = evaluate(node, data) is True
            except Unavailable as err:
                blocked_inputs = True
                expectations.append(
                    _evaluation(index, text, ExpectationResult.SKIP, str(err), _caveat(data.read))
                )
                continue
            caveat = _caveat(data.read)
            result = ExpectationResult.PASS if passed else ExpectationResult.FAIL
            detail = None if passed else _detail(node, data, masked, entity)
            expectations.append(_evaluation(index, text, result, detail, caveat))
    locations = [result for read in reads.values() for result in read.results()]
    failed = (
        blocked_inputs
        or any(e.result is ExpectationResult.FAIL for e in expectations)
        or any(r.outcome is Outcome.ERROR for r in locations)
    )
    return ProbeResult(
        status=RunStatus.FAILED if failed else RunStatus.PASSED,
        locations=locations,
        expectations=expectations,
    )


def check_run(
    probe: Probe, entity: Entity, connectors: Mapping[str, Connector | None]
) -> dict[str, Connector]:
    """Everything that can be checked before a run starts; ConfigError if it cannot run."""
    check_probe(probe, entity)
    for name, using in all_connectors(entity):
        if using not in connectors:
            raise ConfigError(f"location {name} uses connector {using}, which is not loaded")
    return _bound(entity, connectors)


def _bound(entity: Entity, connectors: Mapping[str, Connector | None]) -> dict[str, Connector]:
    # A run is refused, not partly run, where a location's connector has no binding: its
    # result would always be incomplete. owl check reports the same thing ahead of time.
    unbound = [
        f"{name} ({using})"
        for name, using in all_connectors(entity)
        if using in connectors and connectors[using] is None
    ]
    if unbound:
        raise ConfigError(
            f"the probe cannot run in this environment: {', '.join(unbound)} has no binding here"
        )
    return {name: c for name, c in connectors.items() if c is not None}


def _skip_reason(step: _Step, data: "_Data") -> tuple[str | None, bool]:
    """Why a step's expectations are skipped, and whether it is because data is unreadable."""
    if step.when is None:
        return None, False
    text, node = step.when
    try:
        holds = evaluate(node, data) is True
    except Unavailable as err:
        return f"its condition {err}", True
    # A condition that does not hold is the probe working as written, not a problem.
    return (None, False) if holds else (f"when {text} is false", False)


def _evaluation(
    step: int, text: str, result: ExpectationResult, detail: str | None, caveat: str | None
) -> ExpectationEvaluation:
    return ExpectationEvaluation(
        step=step, expectation=text, result=result, detail=detail, caveat=caveat
    )


# A pass built on such a record is as unconfirmed as a failure: "no refund" may only be late.
EXPECTATION_CAVEAT = "reads a Missing record that may not have arrived yet: replica lag unknown"


def _caveat(read: list[LocationRead]) -> str | None:
    """The caveat an expectation carries when any record it read carries one."""
    return EXPECTATION_CAVEAT if any(r.caveat is not None for r in read) else None


def _all_locations(entity: Entity) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = list(entity.locations.items())
    for location in entity.locations.values():
        found.extend(location.each.items())
    return found


class _Data:
    def __init__(self, reads: dict[str, LocationRead]):
        self._reads = reads
        # Every read an evaluation looked at, so its expectation can carry their caveats.
        self.read: list[LocationRead] = []
        # Rows are matched to their fan-out lookups by identity: the same dicts the
        # evaluator iterates over.
        self._row_of = {
            id(row): (name, index)
            for name, read in reads.items()
            if read.fanout
            for index, row in enumerate(read.rows)
        }

    def rows(self, location: str, *, counting: bool = False) -> list[dict[str, Any]]:
        read = self._reads[location]
        self.read.append(read)
        if read.outcome in _EMPTY and not counting:
            raise Unavailable(
                location,
                _label(read.outcome),
                f"needs {location}, which is {_label(read.outcome)}: if it may be absent, "
                f"start the step with when: exists({location})",
            )
        return _readable(read, location)

    def fanned_out(self, location: str, row: dict[str, Any]) -> list[dict[str, Any]] | None:
        found = self._row_of.get(id(row))
        if found is None or location not in self._reads[found[0]].fanout:
            return None
        parent, index = found
        read = self._reads[parent].fanout[location][index]
        if read is None:
            # A check that could not see every row is skipped, naming the cause and the limit.
            reads = self._reads[parent].fanout[location]
            unchecked = sum(r is None for r in reads)
            raise Unavailable(
                location,
                "not looked up",
                f"skipped: {_counted(unchecked, parent)} not checked "
                f"(max_fanout {len(reads) - unchecked})",
            )
        self.read.append(read)
        if read.outcome in _EMPTY:
            reads = self._reads[parent].fanout[location]
            empty = sum(r is not None and r.outcome is read.outcome for r in reads)
            raise Unavailable(
                location,
                _label(read.outcome),
                f"needs {location}, which is {_label(read.outcome)} for {_counted(empty, parent)}",
            )
        return _readable(read, location)


def _label(outcome: Outcome) -> str:
    return outcome.value.replace("_", " ").capitalize()


def _counted(n: int, location: str) -> str:
    """'1 card', '2 cards': a many location's name, usually plural, counted."""
    plural = location if location.endswith("s") else f"{location}s"
    return f"{n} {plural[:-1] if n == 1 else plural}"


def _readable(read: LocationRead, location: str) -> list[dict[str, Any]]:
    if read.outcome in _UNREADABLE:
        label = _label(read.outcome)
        raise Unavailable(location, f"{label} ({read.reason})" if read.reason else label)
    return read.rows


def _detail(node: Node, data: _Data, masked: dict[str, set[str]], entity: Entity) -> str:
    """The values a failed expectation compared, with masked values hidden."""
    if isinstance(node, Call) and node.rows is not None:
        return _set_detail(node, data, masked, entity)
    if not isinstance(node, Binary) or node.op in ("and", "or"):
        return "false"
    sides = [
        MASKED if _reads_masked(side, masked) else _show(evaluate(side, data))
        for side in (node.left, node.right)
    ]
    return f"{sides[0]} {node.op} {sides[1]}"


_SHOWN_ROWS = 5


def _set_detail(node: Call, data: _Data, masked: dict[str, set[str]], entity: Entity) -> str:
    """Which rows a failed all(), none() or unique() is about, by their first exposed field."""
    assert node.rows is not None  # only set functions reach here
    location = node.rows.location
    if node.name == "unique":
        assert node.rows.field is not None  # unique takes location.field
        repeated = duplicates(matching_rows(node, data), node.rows.field)
        if node.rows.field.lower() in masked.get(location, set()):
            return f"{len(repeated)} masked values repeat"
        shown = [f"{value} ({count} times)" for value, count in repeated.items()]
        return "repeated: " + _listed(shown)
    if node.name not in ("all", "none"):
        return "false"
    rows = failing_rows(node, data)
    exposes = entity.locations[location].exposes
    label = next((f for f in exposes if f.lower() not in masked.get(location, set())), None)
    if label is None:
        return f"{len(rows)} rows"
    verb = "fails for" if node.name == "all" else "found"
    return f"{verb} {_listed([_show_plain(row.get(label)) for row in rows])}"


def _listed(items: list[str]) -> str:
    more = len(items) - _SHOWN_ROWS
    shown = ", ".join(items[:_SHOWN_ROWS])
    return f"{shown} and {more} more" if more > 0 else shown


def _show_plain(value: Any) -> str:
    return "null" if value is None else str(value)


def _reads_masked(node: Node, masked: dict[str, set[str]]) -> bool:
    if isinstance(node, Ref) and len(node.parts) > 1:
        return node.parts[1].lower() in masked.get(node.parts[0], set())
    if isinstance(node, Call) and node.rows is not None and node.rows.field is not None:
        return node.rows.field.lower() in masked.get(node.rows.location, set())
    if isinstance(node, Binary):
        return _reads_masked(node.left, masked) or _reads_masked(node.right, masked)
    return False


def _show(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, str):
        return repr(value)
    return str(value)
