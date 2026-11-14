# Author: @SaadZarook
"""Read every location of a subject: key graph order, parallel where keys are known.

A location runs once the location it takes its key from (`key_from`) has run. Locations
whose keys are known run at the same time, but never two at once on one connector: a SQL
adapter holds one connection.

A many location with `each` fans out: each child lookup runs once per row it found, for at
most max_fanout rows, and each of those lookups has its own outcome.
"""

import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Protocol

from probeowl.connectors.lookup import LookupResult, ReplicaLag
from probeowl.engine.masking import mask_rows, masked_fields, pii_fields, to_json
from probeowl.engine.typed_values import column_kinds
from probeowl.errors import ConfigError, ProbeOwlError, ReadsStopped
from probeowl.model.common import seconds, split_field_ref
from probeowl.model.entity import Cardinality, Entity, FanOutLocation, Location
from probeowl.model.run import Evidence, LocationResult, Outcome

DEFAULT_MAX_ROWS = 100  # the design's row cap for a many location
MAX_PARALLEL = 4
RUN_TIME_LIMIT = "run time limit reached"
# Stale needs a known lag longer than the window. With the lag unknown, Missing stays Missing, but
# in Production it must not look like a confirmed absence.
UNKNOWN_LAG_CAVEAT = "replica lag unknown, the record may not have arrived yet"


class Connector(Protocol):
    """What the engine needs from a connector: one guarded lookup."""

    def lookup(
        self, lookup: str, parameters: Mapping[str, object], max_rows: int, /
    ) -> LookupResult:
        """Run one lookup; ProbeOwlError if it cannot."""
        ...


@dataclass
class LocationRead:
    """One location's outcome. Rows stay in memory, unmasked, for the expectations only."""

    name: str
    outcome: Outcome
    rows: list[dict[str, Any]] = field(default_factory=list)
    reason: str | None = None
    caveat: str | None = None
    evidence: Evidence | None = None
    # For a fan-out lookup: the many location and the row of it this lookup is for.
    parent: str | None = None
    row: int | None = None
    # Per fan-out location: one read per row of this location; None past max_fanout.
    fanout: dict[str, list["LocationRead | None"]] = field(default_factory=dict)

    def result(self) -> LocationResult:
        """The masked result that may be stored and shown."""
        return LocationResult(
            location=self.name,
            parent=self.parent,
            row=self.row,
            outcome=self.outcome,
            reason=self.reason,
            caveat=self.caveat,
            evidence=self.evidence,
        )

    def results(self) -> list[LocationResult]:
        """This location's result, then its fan-out lookups', row by row."""
        found = [self.result()]
        for reads in self.fanout.values():
            found.extend(read.result() for read in reads if read is not None)
        return found


def read_locations(
    entity: Entity,
    subject: str,
    connectors: Mapping[str, Connector],
    on_read: Callable[[LocationResult], None] | None = None,
    deadline: float | None = None,
    replica_lag: Mapping[str, ReplicaLag] | None = None,
    caveat_unknown_lag: bool = False,
) -> dict[str, LocationRead]:
    """Read every location of the subject, through the connectors named by `using`.

    deadline: a time.monotonic() value; locations not read by then are Error.
    replica_lag: per connector, its replica's lag measured for this run. A Missing record
    on a replica further behind than the location's `within` window is Stale.
    caveat_unknown_lag: in Production, a Missing record on a replica whose lag is unknown
    carries UNKNOWN_LAG_CAVEAT.
    """
    _check(entity, connectors)
    reader = _Reader(entity, subject, connectors, deadline, replica_lag or {}, caveat_unknown_lag)
    done: dict[str, LocationRead] = {}

    def finish(read: LocationRead) -> None:
        done[read.name] = read
        if on_read is not None:
            for result in read.results():
                on_read(result)

    pending = list(entity.locations)
    running: dict[Future[LocationRead], str] = {}
    # Not a `with` block: leaving one waits for every thread, past the deadline.
    pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL)
    try:
        while pending or running:
            # A location starts as soon as the one it takes its key from is done.
            for name in [n for n in pending if _source(entity.locations[n]) in (None, *done)]:
                running[pool.submit(reader.read, name, dict(done))] = name
                pending.remove(name)
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            finished, _ = wait(running, timeout=remaining, return_when=FIRST_COMPLETED)
            # Each location is reported the moment it is read, not with the others.
            for future in finished:
                finish(future.result())
                del running[future]
            if deadline is not None and time.monotonic() >= deadline:
                for name in [*running.values(), *pending]:
                    finish(_out_of_time(name))
                break
    finally:
        # A lookup still running stops at its own statement or request timeout.
        pool.shutdown(wait=False, cancel_futures=True)
    # The entity's order, not the order they finished in.
    return {name: done[name] for name in entity.locations}


def _out_of_time(name: str, parent: str | None = None, row: int | None = None) -> LocationRead:
    return LocationRead(name, Outcome.ERROR, reason=RUN_TIME_LIMIT, parent=parent, row=row)


def _check(entity: Entity, connectors: Mapping[str, Connector]) -> None:
    for name, using in all_connectors(entity):
        if using not in connectors:
            raise ConfigError(f"location {name} uses connector {using}, which is not loaded")


def all_connectors(entity: Entity) -> list[tuple[str, str]]:
    """Every location, fan-out locations included, with the connector it uses."""
    found = []
    for name, location in entity.locations.items():
        found.append((name, location.using))
        found.extend((f"{name}.{child}", spec.using) for child, spec in location.each.items())
    return found


def _source(location: Location) -> str | None:
    return None if location.key_from is None else split_field_ref(location.key_from)[0]


class _Reader:
    def __init__(
        self,
        entity: Entity,
        subject: str,
        connectors: Mapping[str, Connector],
        deadline: float | None,
        replica_lag: Mapping[str, ReplicaLag],
        caveat_unknown_lag: bool,
    ):
        self._entity = entity
        self._subject = subject
        self._connectors = connectors
        self._deadline = deadline
        self._replica_lag = replica_lag
        self._caveat_unknown_lag = caveat_unknown_lag
        self._locks = {name: threading.Lock() for name in connectors}

    def read(self, name: str, done: dict[str, LocationRead]) -> LocationRead:
        location = self._entity.locations[name]
        connector = self._connectors[location.using]
        parameters: dict[str, object] = {self._entity.root_key: self._subject}
        missing_link = None
        if location.key_from is not None:
            source, key = split_field_ref(location.key_from)
            upstream = done[source]
            if upstream.outcome is not Outcome.FOUND:
                return LocationRead(
                    name, Outcome.BLOCKED, reason=f"{source} is {_label(upstream.outcome)}"
                )
            if len(upstream.rows) != 1:
                return LocationRead(
                    name, Outcome.BLOCKED, reason=f"{source} has {len(upstream.rows)} records"
                )
            value = upstream.rows[0].get(key)
            if value is None:
                missing_link = f"missing link: {location.key_from} is empty"
            parameters[key] = value
        if missing_link is not None and location.fallback is None:
            return LocationRead(name, Outcome.BLOCKED, reason=missing_link)
        try:
            read = self._look_up(name, location, connector, parameters, missing_link)
        except ReadsStopped:
            raise  # the whole run stops, not this one location
        except ProbeOwlError as err:
            # A connector that fails, refuses or times out is this location's Error outcome.
            return LocationRead(name, Outcome.ERROR, reason=str(err))
        if location.each and read.outcome is Outcome.FOUND:
            self._fan_out(read, location)
        return read

    def _fan_out(self, read: LocationRead, location: Location) -> None:
        """Run each fan-out lookup for the rows read found, up to its max_fanout."""
        notes = [read.reason] if read.reason else []
        for child, spec in location.each.items():
            reads: list[LocationRead | None] = []
            for index, row in enumerate(read.rows):
                if index >= spec.max_fanout:
                    reads.append(None)
                elif self._deadline is not None and time.monotonic() >= self._deadline:
                    reads.append(_out_of_time(child, read.name, index))
                else:
                    reads.append(self._fan_out_one(read.name, child, spec, row, index))
            read.fanout[child] = reads
            if len(read.rows) > spec.max_fanout:
                notes.append(
                    f"{child} looked up for {spec.max_fanout} of {len(read.rows)} rows "
                    f"(max_fanout {spec.max_fanout})"
                )
        read.reason = "; ".join(notes) or None

    def _fan_out_one(
        self, parent: str, child: str, spec: FanOutLocation, row: dict[str, Any], index: int
    ) -> LocationRead:
        # The child's lookup uses this row's fields, and the root key.
        parameters: dict[str, object] = {**row, self._entity.root_key: self._subject}
        connector = self._connectors[spec.using]
        try:
            read = self._look_up(child, spec, connector, parameters, None, parent=parent)
        except ReadsStopped:
            raise
        except ProbeOwlError as err:
            read = LocationRead(child, Outcome.ERROR, reason=str(err))
        read.parent, read.row = parent, index
        return read

    def _look_up(
        self,
        name: str,
        location: Location | FanOutLocation,
        connector: Connector,
        parameters: dict[str, object],
        missing_link: str | None,
        parent: str | None = None,
    ) -> LocationRead:
        many = location.cardinality is Cardinality.MANY
        max_rows = rows_read(location)
        notes: list[str] = []
        result = None
        if missing_link is None:
            result = self._lookup(location, connector, location.lookup, parameters, max_rows)
        if (result is None or not result.rows) and location.fallback is not None:
            # The fallback searches by our own reference, for when the link was not saved.
            result = self._lookup(location, connector, location.fallback, parameters, max_rows)
            found = "found by the fallback lookup" if result.rows else "the fallback found nothing"
            notes.append(f"{missing_link}; {found}" if missing_link else found)
        assert result is not None  # a lookup runs above when there is no fallback
        outcome = Outcome.FOUND if result.rows else Outcome.MISSING
        if not many and (len(result.rows) > 1 or result.truncated):
            outcome = Outcome.AMBIGUOUS
            notes.append(f"more than one record where {location.cardinality} was expected")
        elif result.rows_total is not None:
            notes.append(f"showing {len(result.rows)} of {result.rows_total}")
        elif result.truncated:
            notes.append(f"{result.truncated}; showing {len(result.rows)}")
        lag = self._replica_lag.get(location.using)
        if outcome is Outcome.MISSING and _stale(lag, location.within):
            # The record may exist on the primary and not have reached the replica yet.
            assert lag is not None and location.within is not None  # _stale checked both
            outcome = Outcome.STALE
            notes.append(
                f"{lag.note} is longer than the {location.within} window: missing cannot be trusted"
            )
        evidence = self._evidence(location, result, parent)
        if lag is not None:
            evidence = evidence.model_copy(update={"guards": [*evidence.guards, lag.note]})
        caveat = lag_caveat(outcome, lag) if self._caveat_unknown_lag else None
        return LocationRead(
            name,
            outcome,
            rows=result.rows,
            reason="; ".join(notes) or None,
            caveat=caveat,
            evidence=evidence,
        )

    def _lookup(
        self,
        location: Location | FanOutLocation,
        connector: Connector,
        lookup: str,
        parameters: dict[str, object],
        max_rows: int,
    ) -> LookupResult:
        with self._locks[location.using]:
            return connector.lookup(lookup, parameters, max_rows)

    def _evidence(
        self, location: Location | FanOutLocation, result: LookupResult, parent: str | None
    ) -> Evidence:
        masked = masked_fields(self._entity, location)
        # A PII identifier, such as a root key that is PII, is masked as a parameter too (#66).
        hidden = {f for f in result.parameters if f.lower() in pii_fields(self._entity)}
        # A key that is a masked field of its source location is masked in evidence too.
        if isinstance(location, Location) and location.key_from is not None:
            source, key = split_field_ref(location.key_from)
            upstream = self._entity.locations[source]
            if key.lower() in masked_fields(self._entity, upstream):
                hidden.add(key)
        if parent is not None:
            # A fan-out lookup's parameters come from its parent's row: masked there, here too.
            upstream = self._entity.locations[parent]
            hidden.update(
                f for f in result.parameters if f.lower() in masked_fields(self._entity, upstream)
            )
        parameters = mask_rows([dict(result.parameters)], hidden)[0]
        return Evidence(
            connector=location.using,
            query=result.query,
            parameters={key: to_json(value) for key, value in parameters.items()},
            guards=result.guards,
            rows=[
                {key: to_json(value) for key, value in row.items()}
                for row in mask_rows(result.rows, masked)
            ],
            rows_total=result.rows_total,
            column_types=column_kinds(result.rows),
            started_at=result.started_at,
            duration_ms=result.duration_ms,
        )


def lag_caveat(outcome: Outcome, lag: ReplicaLag | None) -> str | None:
    """The caveat a Missing record on a replica carries when the replica's lag is unknown."""
    if outcome is Outcome.MISSING and lag is not None and lag.seconds is None:
        return UNKNOWN_LAG_CAVEAT
    return None


def _stale(lag: ReplicaLag | None, within: str | None) -> bool:
    return (
        lag is not None
        and lag.seconds is not None
        and within is not None
        and lag.seconds > seconds(within)
    )


def rows_read(location: Location | FanOutLocation) -> int:
    """The most rows a location's lookup reads: one, unless its cardinality is many."""
    if location.cardinality is not Cardinality.MANY:
        return 1
    if isinstance(location, Location) and location.max_rows is not None:
        return location.max_rows
    return DEFAULT_MAX_ROWS


def _label(outcome: Outcome) -> str:
    return outcome.value.replace("_", " ").capitalize()
