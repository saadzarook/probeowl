# Author: @SaadZarook
"""Runs inside owl serve: checked before they start, run in the background, saved as they go.

A run is refused before any lookup when anything about it is wrong. At most a few run at
once; one more is refused (429), never queued out of sight. Each location is saved as it
completes, so GET /api/runs/{id} shows a run as it progresses.
"""

import getpass
import hashlib
import logging
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from pydantic import JsonValue
from sqlalchemy import Connection, Engine

from probeowl import secrets
from probeowl.api.models import StartRun
from probeowl.checks.rules import rule_warnings
from probeowl.checks.time_offsets import imported_types, refuse_typed_time, typed_time
from probeowl.connectors.factory import OpenConnector, open_connector
from probeowl.connectors.lookup import LookupResult, ReplicaLag
from probeowl.engine.identifiers import (
    MAX_MATCH_ROWS,
    Resolution,
    candidates,
    detect,
    resolution_lookup,
    resolve,
    resolve_root,
    root_location,
    via_connector,
)
from probeowl.engine.locations import RUN_TIME_LIMIT, all_connectors, lag_caveat
from probeowl.engine.masking import (
    MASKED,
    mask_rows,
    masked_key_reason,
    pii_fields,
    to_json,
)
from probeowl.engine.runner import check_run, run_probe
from probeowl.errors import (
    CapacityError,
    ConfigError,
    ConflictError,
    ConnectorError,
    KillSwitchEngaged,
    NotFoundError,
    PasswordMissing,
    ProbeOwlError,
    ReadsStopped,
    RequestError,
    unknown_application,
)
from probeowl.guards.breaker import CircuitBreaker, Key
from probeowl.guards.dialects import RULES_BY_CONNECTOR_TYPE
from probeowl.guards.kill_switch import KillSwitches
from probeowl.guards.statement import ParameterValue
from probeowl.healthy import (
    MAX_CANDIDATES,
    SAMPLE_ROWS,
    Fingerprints,
    compare_runs,
    field_values,
    masked_compare_fields,
    sample_parameters,
)
from probeowl.model.check import LoadedApplication
from probeowl.model.common import seconds, split_field_ref
from probeowl.model.connector import RestConnector, SqlConnector
from probeowl.model.entity import Entity
from probeowl.model.healthy import HealthyChoice, HealthyComparison
from probeowl.model.probe import Probe
from probeowl.model.run import (
    DRAFT_VERSION_PREFIX,
    Actor,
    ActorKind,
    Evidence,
    HealthyAttempt,
    HealthyAttemptStatus,
    LocationResult,
    Outcome,
    Run,
    RunStatus,
    RunSummary,
)
from probeowl.secrets import keychain_entry
from probeowl.store.audit import AuditEvent, append_event
from probeowl.store.runs import get_run, list_runs, running_run_ids, save_run
from probeowl.store.tables import LOCAL_WORKSPACE
from probeowl.stored_masking import masked_run

log = logging.getLogger(__name__)

INTERRUPTED = "interrupted"
RUNS_PER_PAGE = 50
_NOT_LOOKED_UP = "the identifier could not be looked up"

Opener = Callable[[SqlConnector | RestConnector, str], OpenConnector | None]


@dataclass(frozen=True)
class RunLimits:
    """How many runs at once, and how long one may take."""

    max_concurrent: int = 4
    time_limit_s: float = 60.0


@dataclass(frozen=True)
class _Prepared:
    run: Run
    probe: Probe
    entity: Entity
    connectors: dict[str, OpenConnector]
    # The value as entered and the lookup that finds its subject; None for the root key.
    # Kept out of the run, which stores the value masked when it is PII.
    resolve: tuple[str, str] | None = None
    # Replica bindings this run uses: connector name -> the most lag allowed, in seconds.
    max_lag: dict[str, float] = field(default_factory=dict)
    production: bool = False
    # The configuration it was prepared from: one reloaded since must not start (reload).
    generation: int = 0
    # When no pattern placed the value: the value as entered, and each identifier to try in
    # order with its lookup (None for the root key). The first to find the subject is the run's.
    tries: tuple[str, list[tuple[str, str | None]]] | None = None


@dataclass(frozen=True)
class _SamplePlan:
    """The sample lookup a healthy comparison will send."""

    connector: str
    lookup: str
    parameters: dict[str, ParameterValue]


class RunService:
    """Starts, runs and reads runs for one store."""

    def __init__(
        self,
        applications: dict[str, LoadedApplication],
        store: Engine,
        limits: RunLimits | None = None,
        opener: Opener = open_connector,
        actor_name: str | None = None,
        workspace_id: str = LOCAL_WORKSPACE,
        breaker: CircuitBreaker | None = None,
    ):
        self._applications = applications
        self._breaker = breaker or CircuitBreaker()
        self._store = store
        self.kill = KillSwitches(store, workspace_id)
        self._limits = limits or RunLimits()
        self._opener = opener
        # Local mode: the person is the operating system user running owl serve.
        self._actor = Actor(kind=ActorKind.PERSON, name=actor_name or getpass.getuser())
        self._workspace = workspace_id
        self._slots = threading.BoundedSemaphore(self._limits.max_concurrent)
        self._generation = 0  # how many times the configuration was reloaded
        self._fingerprints = Fingerprints()
        self._attempts_lock = threading.Lock()
        self._attempting: set[str] = set()
        self._pool = ThreadPoolExecutor(
            max_workers=self._limits.max_concurrent, thread_name_prefix="probeowl-run"
        )

    def audit(self, action: str, details: dict[str, JsonValue]) -> None:
        """Record an action that is not a run, with the actor."""
        event = AuditEvent(action=action, actor=self._actor, details=details)
        with self._store.begin() as conn:
            append_event(conn, self._workspace, event)

    def record(
        self, action: str, run: Run, details: dict[str, Any], conn: Connection | None = None
    ) -> None:
        """Record an action on a run, with its actor, application and environment.

        conn: the transaction that makes the change, so the change and its event commit together.
        """
        if conn is not None:
            append_event(conn, self._workspace, _event(action, run, details))
        else:
            self._audit(action, run, details)

    @property
    def store(self) -> Engine:
        """The store runs are kept in."""
        return self._store

    @property
    def workspace(self) -> str:
        """The workspace this service reads and writes."""
        return self._workspace

    def start_again(self, run: Run, probe: str) -> str:
        """Run a probe again on this run's subject, under its investigation. Its run id."""
        if run.subject is None or run.investigation is None:
            raise RequestError(
                f"run {run.id} has no subject to run again", {"run": "pick a subject first"}
            )
        related = [*run.investigation.related_runs, run.id]
        _, entity = self._definitions(run)
        return self.start(
            StartRun(
                application=run.application,
                environment=run.environment,
                probe=probe,
                value=run.subject,
                identifier=entity.root_key,
                investigation=run.investigation.model_copy(update={"related_runs": related}),
            )
        )

    def read_once(
        self,
        app: LoadedApplication,
        connector: str,
        environment: str,
        sql: str,
        parameters: Mapping[str, ParameterValue],
        max_rows: int,
    ) -> LookupResult:
        """One guarded lookup that is not part of a run. It counts toward the run limit."""
        if not self._slots.acquire(blocking=False):
            raise CapacityError(
                f"{self._limits.max_concurrent} runs are already running; "
                "run this step when one has finished"
            )
        try:
            return self._guarded_lookup(app, connector, environment, sql, parameters, max_rows)
        finally:
            self._slots.release()

    def _guarded_lookup(
        self,
        app: LoadedApplication,
        connector: str,
        environment: str,
        sql: str,
        parameters: Mapping[str, ParameterValue],
        max_rows: int,
    ) -> LookupResult:
        application = app.application.application
        key = (application, connector, environment)
        self.kill.check(application, connector)
        paused = self._breaker.paused(key)
        if paused is not None:
            raise ConnectorError(paused)
        opened = self._opener(app.connectors[connector], environment)
        if opened is None:
            raise ConfigError(f"{connector} has no binding in {environment}")
        watched = _Watched(opened, self._breaker, key, self.kill)
        try:
            return watched.lookup(sql, parameters, max_rows)
        finally:
            watched.close()

    @property
    def actor(self) -> Actor:
        """Who is acting: the person running owl serve."""
        return self._actor

    @property
    def applications(self) -> dict[str, LoadedApplication]:
        """The applications loaded from the probe files, by name."""
        return self._applications

    def reload(
        self,
        load: Callable[[], dict[str, LoadedApplication]],
        record: Callable[[dict[str, LoadedApplication], dict[str, LoadedApplication]], None],
    ) -> dict[str, LoadedApplication]:
        """Replace the loaded applications with load()'s, only while nothing is running.

        Every run slot is held meanwhile, so no run starts or reads until it is done. If load
        fails, or record(old, new) does, nothing changes: record audits the reload, which only
        takes effect once that has committed. The services share this one dictionary, so it is
        replaced in place, and every one of them sees the new configuration. The old one is
        returned.
        """
        held = 0
        try:
            while held < self._limits.max_concurrent and self._slots.acquire(blocking=False):
                held += 1
            running = self._limits.max_concurrent - held
            if running:
                raise ConflictError(
                    f"{running} {'run is' if running == 1 else 'runs are'} running; "
                    "reload when they have finished"
                )
            new = load()
            old = dict(self._applications)
            record(old, new)
            self._applications.clear()
            self._applications.update(new)
            self._generation += 1
            return old
        finally:
            for _ in range(held):
                self._slots.release()

    def start(self, request: StartRun) -> str:
        """Check and start a run in the background; its id. Errors before any lookup."""
        return self._admit([self._prepare(request)])[0]

    def start_matches(
        self,
        run_id: str,
        subjects: list[str] | None,
        value: str | None = None,
        picks: list[int] | None = None,
    ) -> list[str]:
        """Run the probe for subjects an Ambiguous run matched; their run ids, in order.

        They share one of the concurrent run slots and run one after another, so picking
        several matches never crowds out other runs. When the entity's key is PII its matches
        are stored masked (#66): the person pastes the value searched for again and picks by
        number, and the same lookup runs again to find them.
        """
        ambiguous = self.get(run_id)
        if ambiguous.status is not RunStatus.AMBIGUOUS:
            raise RequestError(
                f"run {run_id} is {ambiguous.status.value}, not Ambiguous",
                {"run": "only an Ambiguous run's matches can be picked"},
            )
        app = self._applications.get(ambiguous.application)
        entity = app.entities.get(ambiguous.entity) if app is not None else None
        if app is None or entity is None:
            raise NotFoundError(f"{ambiguous.application} has no entity {ambiguous.entity}")
        if any(match.subject == MASKED for match in ambiguous.matches):
            subjects = self._picked_again(ambiguous, app, entity, value, picks)
        else:
            subjects = _picked(ambiguous, subjects)
        prepared: list[_Prepared] = []
        try:
            for subject in subjects:
                request = StartRun(
                    application=ambiguous.application,
                    environment=ambiguous.environment,
                    probe=ambiguous.probe,
                    value=subject,
                    identifier=entity.root_key,
                    investigation=ambiguous.investigation,
                )
                prepared.append(self._prepare(request, from_run=run_id))
        except BaseException:
            for each in prepared:
                _close(each.connectors.values())
            raise
        return self._admit(prepared)

    def _admit(self, prepared: list[_Prepared]) -> list[str]:
        # A run skipped by the circuit breaker is recorded at once and takes no slot.
        skipped = [each for each in prepared if each.run.status is RunStatus.SKIPPED]
        ready = [each for each in prepared if each.run.status is RunStatus.RUNNING]
        for each in skipped:
            _close(each.connectors.values())
            self._save_and_audit(
                each.run,
                ("run_started", _started_details(self._masked(each.run))),
                ("run_finished", _finished_details(self._masked(each.run))),
            )
        if ready:
            self._run_in_background(ready)
        return [each.run.id for each in prepared]

    def _run_in_background(self, prepared: list[_Prepared]) -> None:
        if not self._slots.acquire(blocking=False):
            for each in prepared:
                _close(each.connectors.values())
            raise CapacityError(
                f"{self._limits.max_concurrent} runs are already running; "
                "start this one when one has finished"
            )
        if any(each.generation != self._generation for each in prepared):
            self._slots.release()
            for each in prepared:
                _close(each.connectors.values())
            raise ConflictError(
                "the configuration was reloaded while this run was starting; start it again"
            )
        try:
            for each in prepared:
                self._save_and_audit(
                    each.run, ("run_started", _started_details(self._masked(each.run)))
                )
            self._pool.submit(self._execute_all, prepared)
        except BaseException:
            self._slots.release()
            for each in prepared:
                _close(each.connectors.values())
            raise

    def get(self, run_id: str) -> Run:
        """A run as stored: masked, with the locations read so far."""
        with self._store.connect() as conn:
            return get_run(conn, self._workspace, run_id)

    def recent(
        self, application: str, environment: str, before: tuple[datetime, str] | None = None
    ) -> tuple[list[RunSummary], tuple[datetime, str] | None]:
        """A page of runs, newest first, and where the next older page starts (None: no more)."""
        with self._store.connect() as conn:
            found = list_runs(
                conn, self._workspace, application, environment, before, RUNS_PER_PAGE + 1
            )
        page = found[:RUNS_PER_PAGE]
        more = len(found) > RUNS_PER_PAGE
        return page, ((page[-1].started_at, page[-1].id) if more else None)

    def recover_interrupted(self) -> list[str]:
        """Mark runs left running by a stopped owl serve as failed; their ids."""
        with self._store.connect() as conn:
            ids = running_run_ids(conn, self._workspace)
        for run_id in ids:
            run = self.get(run_id)
            failed = _finished(run, RunStatus.FAILED, stop_reason=INTERRUPTED)
            self._save_and_audit(failed, ("run_interrupted", {"stop_reason": INTERRUPTED}))
        return ids

    def shutdown(self) -> None:
        """Stop taking runs and wait for the ones running to finish."""
        self._pool.shutdown(wait=True)

    # --- Before a run starts ------------------------------------------------------------

    def start_draft(self, request: StartRun, probe: Probe, entity: Entity) -> str:
        """Run a draft probe (and entity) that is not saved, like any run: checked, guarded,
        limited, stored and audited. Its version says it was a draft."""
        return self._admit([self._prepare(request, draft=(probe, entity))])[0]

    def _prepare(
        self,
        request: StartRun,
        from_run: str | None = None,
        draft: tuple[Probe, Entity] | None = None,
        healthy_for: str | None = None,
    ) -> _Prepared:
        app = self._applications.get(request.application)
        if app is None:
            raise unknown_application(request.application, self._applications)
        environment = app.application.environments.get(request.environment)
        if environment is None:
            raise NotFoundError(f"{request.application} has no environment {request.environment}")
        probe = draft[0] if draft else app.probes.get(request.probe)
        if probe is None:
            raise NotFoundError(f"{request.application} has no probe named {request.probe}")
        if probe.environments is not None and request.environment not in probe.environments:
            raise RequestError(
                f"probe {probe.probe} is not enabled in {request.environment}",
                {"environment": f"{probe.probe} runs in: {', '.join(probe.environments)}"},
            )
        if environment.production and not request.investigation.reference:
            raise RequestError(
                "a Production investigation needs a reason and a reference",
                {"investigation.reference": "required when the environment is Production"},
            )
        entity = draft[1] if draft else app.entities[probe.entity]
        possible = candidates(entity, request.value, request.identifier)
        identifier = possible[0]
        typed = typed_time(request.value)
        if typed is not None:
            application = app.application.application
            types = imported_types(self._store, self._workspace, application, request.environment)
            for each in possible:
                refuse_typed_time(app, entity, each, typed, request.environment, types)
        tries = None
        if len(possible) > 1:
            tries = (request.value, [(name, _lookup_for(app, entity, name)) for name in possible])
        via = entity.identifiers[identifier].via
        resolve = None
        if via is not None and tries is None:
            resolve = (
                request.value,
                resolution_lookup(entity, identifier, _dialect(app, entity, via)),
            )
        # Before anything is opened. A store it cannot read refuses the run here.
        application_name = app.application.application
        used = sorted({using for _, using in all_connectors(entity)})
        stopped = [r for name in used if (r := self.kill.reason(application_name, name))]
        if not stopped:  # a stopped run is skipped before any lookup: no password is needed
            _refuse_missing_passwords(app, used, request.environment, environment.title)
        connectors = self._open(app, entity, request.environment, stopped=bool(stopped))
        try:
            check_run(probe, entity, connectors)
        except BaseException:
            _close(c for c in connectors.values() if c is not None)
            raise
        # Masked if any identifier it may turn out to be is personal data.
        pii = any(entity.identifiers[name].pii for name in possible)
        keys = {
            name: (app.application.application, name, request.environment) for name in connectors
        }
        paused = [reason for key in keys.values() if (reason := self._breaker.paused(key))]
        paused = [*dict.fromkeys(stopped), *paused]
        run = Run(
            id=str(uuid.uuid4()),
            # owl serve starts with such a lookup, so whoever runs it is told.
            warnings=rule_warnings(app, entity),
            application=request.application,
            environment=request.environment,
            probe=probe.probe,
            kind=probe.kind,
            probe_version=_version(probe, draft[1] if draft else None),
            entity=entity.entity,
            identifier=identifier,
            identifier_value=MASKED if pii else request.value,
            subject=None if via is not None or tries is not None else request.value,
            via=via,
            from_run=from_run,
            healthy_for=healthy_for,
            status=RunStatus.SKIPPED if paused else RunStatus.RUNNING,
            skip_reason="; ".join(paused) or None,
            actor=self._actor,
            # Stored masked, as the investigation record requires.
            investigation=request.investigation.model_copy(
                update={"customer_reference": MASKED}
                if request.investigation.customer_reference
                else {}
            ),
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC) if paused else None,
        )
        remember = self._remembering(run.id, app, entity)
        opened: dict[str, OpenConnector] = {
            name: _Watched(connector, self._breaker, keys[name], self.kill, remember)
            for name, connector in connectors.items()
            if connector is not None
        }
        return _Prepared(
            run,
            probe,
            entity,
            opened,
            resolve,
            _max_lag(app, opened, request.environment),
            environment.production,
            self._generation,
            tries,
        )

    def _remembering(
        self, run_id: str, app: LoadedApplication, entity: Entity
    ) -> Callable[[str, list[dict[str, Any]]], None]:
        """What a run's connectors do with rows before masking: fingerprint compare fields."""
        fields = masked_compare_fields(app, entity)

        def remember(lookup: str, rows: list[dict[str, Any]]) -> None:
            for location, field_name in fields.get(lookup, []):
                self._fingerprints.remember(
                    run_id, location, field_name, field_values(rows, field_name)
                )

        return remember

    def _open(
        self, app: LoadedApplication, entity: Entity, environment: str, stopped: bool
    ) -> dict[str, OpenConnector | None]:
        names = {using for _, using in all_connectors(entity)}
        if stopped:
            # A stopped run says stopped whatever state the secrets are in, and leaves the keyring
            # alone. A connector with no binding here still refuses the run, as it always does.
            return {
                name: _NotOpened() if environment in app.connectors[name].environments else None
                for name in names
            }
        opened: dict[str, OpenConnector | None] = {}
        try:
            for name in sorted(names):
                opened[name] = self._opener(app.connectors[name], environment)
        except BaseException:
            _close(c for c in opened.values() if c is not None)
            raise
        return opened

    # --- Healthy comparison --------------------------------------------------------------

    def _picked_again(
        self,
        ambiguous: Run,
        app: LoadedApplication,
        entity: Entity,
        value: str | None,
        picks: list[int] | None,
    ) -> list[str]:
        """The subjects picked by number, found by the Ambiguous run's own lookup run again.

        Nothing pasted or found is stored or returned: the picked runs store their subjects
        masked. If the lookup no longer finds as many matches, the numbers may point at
        others, so it is refused and the person starts again.
        """
        again = f"pick by number: paste the {ambiguous.identifier} you searched for again"
        if value is None or not picks:
            raise RequestError(again, {"value": again, "picks": "the numbers of the matches"})
        if ambiguous.via is None:
            reason = "this run tried its value as several identifiers: start again with Find by set"
            raise RequestError(reason, {"value": reason})
        detect(entity, value, ambiguous.identifier)
        lookup = _lookup_for(app, entity, ambiguous.identifier)
        assert lookup is not None  # via is set, so the identifier has a lookup
        location = entity.locations[split_field_ref(ambiguous.via)[0]]
        found = self.read_once(
            app,
            location.using,
            ambiguous.environment,
            lookup,
            {ambiguous.identifier: value},
            MAX_MATCH_ROWS,
        )
        subjects = sorted(
            {str(v) for v in field_values(found.rows, entity.root_key) if v is not None}
        )
        if len(subjects) != len(ambiguous.matches):
            reason = "the matches have changed since this run: start again"
            raise RequestError(reason, {"value": reason})
        if len(set(picks)) != len(picks) or any(not 1 <= n <= len(subjects) for n in picks):
            reason = f"pick each number once, from 1 to {len(subjects)}"
            raise RequestError(reason, {"picks": reason})
        return [subjects[n - 1] for n in picks]

    def start_healthy(
        self,
        run_id: str,
        narrow: str | None,
        subject: str | None,
        failing_subject: str | None = None,
    ) -> None:
        """Start looking for a known-good subject to compare a finished run with.

        failing_subject: this run's subject, pasted when its key is PII and so stored masked
        (#66). The sample needs it to leave this run's subject out; it stays in memory.
        """
        failing = self.get(run_id)
        app, entity = self._definitions(failing)
        if failing.subject is None or failing.status not in {RunStatus.PASSED, RunStatus.FAILED}:
            raise RequestError(
                f"run {run_id} is {failing.status.value}: only a finished run on one subject "
                "can be compared",
                {"run": "wait for it to finish, or pick a subject"},
            )
        if failing.investigation is None:
            raise ConfigError("a run started by a person needs an investigation record")
        if narrow is not None and subject is not None:
            raise RequestError(
                "choose a closer match or paste an ID, not both",
                {"narrow": "leave this out when pasting an ID"},
            )
        pasted_failing = failing.subject == MASKED and failing_subject is not None
        if pasted_failing:
            assert failing_subject is not None  # as pasted_failing says
            check_pasted_subject(failing, app, entity, failing_subject)
            failing = failing.model_copy(update={"subject": failing_subject})
        plan = None
        if subject is None:
            if failing.subject == MASKED:
                reason = masked_key_reason(entity)
                raise RequestError(reason, {"failing_subject": reason})
            plan = self._sample_plan(failing, app, entity, narrow)
            self.kill.check(failing.application, plan.connector)
        else:
            detect(entity, subject, None)  # a value it cannot place is refused here
            if subject == failing.subject:
                raise RequestError(
                    "that is the subject of this run", {"subject": "paste another subject's ID"}
                )
        earlier = failing.healthy_attempt
        attempt = HealthyAttempt(
            status=HealthyAttemptStatus.RUNNING,
            narrow=narrow,
            pasted=subject is not None,
            replaced=earlier is not None,
            replaced_subject=(
                self.get(earlier.healthy_run).subject if earlier and earlier.healthy_run else None
            ),
        )
        with self._attempts_lock:
            if run_id in self._attempting:
                raise RequestError(
                    "a comparison of this run is already running", {"run": "wait for it to finish"}
                )
            # Its candidates run one after another in this one slot, and count toward the limit.
            if not self._slots.acquire(blocking=False):
                raise CapacityError(
                    f"{self._limits.max_concurrent} runs are already running; "
                    "compare this one when one has finished"
                )
            self._attempting.add(run_id)
        try:
            details = {
                "narrow": narrow,
                "pasted": subject is not None,
                # This run's own subject, pasted for the sample: audited as used, never its value.
                **({"subject": MASKED} if pasted_failing else {}),
                "replaced_run": earlier.healthy_run if earlier else None,
            }
            self._save_and_audit(
                failing.model_copy(update={"healthy_attempt": attempt}),
                ("healthy_comparison_started", details),
            )
            self._pool.submit(self._attempt_healthy, failing, app, entity, plan, subject, attempt)
        except BaseException:
            self._end_attempt(run_id)
            raise

    def comparison(self, run_id: str, redact: Callable[[Run], Run]) -> HealthyComparison:
        """A run's latest comparison with a known-good subject, or what could start one."""
        # Asked before reading the run: an attempt that ends in between was saved first, so one
        # read as running with nothing live really was cut off.
        with self._attempts_lock:
            live = run_id in self._attempting
        failing = redact(self.get(run_id))
        app, entity = self._definitions(failing)
        sample = entity.healthy_sample
        choices = [
            HealthyChoice(name=name, title=narrowing.title)
            for name, narrowing in (sample.narrow.items() if sample else ())
        ]
        attempt = failing.healthy_attempt
        if attempt is not None and attempt.status is HealthyAttemptStatus.RUNNING and not live:
            attempt = attempt.model_copy(
                update={
                    "status": HealthyAttemptStatus.COULD_NOT_RUN,
                    "message": "interrupted: owl serve stopped during this comparison",
                }
            )
        found = HealthyComparison(
            run=run_id,
            has_sample=sample is not None,
            choices=choices,
            attempt=attempt,
            failing_subject=failing.subject,
        )
        if attempt is None or attempt.healthy_run is None:
            return found
        healthy = redact(self.get(attempt.healthy_run))
        warning = None
        if healthy.status is not RunStatus.PASSED:
            warning = (
                f"{healthy.subject} did not pass ({healthy.status.value}), "
                "so its differences may not point at the cause"
            )
        return found.model_copy(
            update={
                "healthy_subject": healthy.subject,
                "warning": warning,
                "differences": compare_runs(failing, healthy, app, entity, self._fingerprints),
            }
        )

    def _definitions(self, run: Run) -> tuple[LoadedApplication, Entity]:
        app = self._applications.get(run.application)
        entity = app.entities.get(run.entity) if app is not None else None
        if app is None or entity is None:
            raise NotFoundError(f"{run.application} has no entity {run.entity}")
        return app, entity

    def _sample_plan(
        self, failing: Run, app: LoadedApplication, entity: Entity, narrow: str | None
    ) -> _SamplePlan:
        sample = entity.healthy_sample
        if sample is None:
            raise RequestError(
                f"{entity.entity} has no healthy_sample", {"subject": "paste a known-good ID"}
            )
        lookup = sample.lookup
        if narrow is not None:
            narrowing = sample.narrow.get(narrow)
            if narrowing is None:
                raise RequestError(
                    f"{entity.entity} has no closer match named {narrow}",
                    {"narrow": f"one of: {', '.join(sample.narrow)}"},
                )
            lookup = narrowing.lookup
        connector = app.connectors.get(sample.using)
        if not isinstance(connector, SqlConnector):
            raise ConfigError(f"healthy_sample needs a SQL connector, not {sample.using}")
        dialect = RULES_BY_CONNECTOR_TYPE[connector.type].sqlglot_dialect
        parameters = sample_parameters(failing, entity, sample, lookup, dialect)
        return _SamplePlan(sample.using, lookup, parameters)

    def _attempt_healthy(
        self,
        failing: Run,
        app: LoadedApplication,
        entity: Entity,
        plan: _SamplePlan | None,
        subject: str | None,
        attempt: HealthyAttempt,
    ) -> None:
        try:
            try:
                ended = self._find_healthy(failing, app, entity, plan, subject, attempt)
            except ProbeOwlError as err:  # stopped, paused, unbound, or its secret is unreadable
                ended = _could_not_run(attempt, str(err))
            except Exception as err:
                log.exception("the comparison of run %s stopped by an unexpected error", failing.id)
                ended = _could_not_run(
                    attempt, f"stopped by an unexpected error ({type(err).__name__})"
                )
            self._save_and_audit(
                self.get(failing.id).model_copy(update={"healthy_attempt": ended}),
                (
                    "healthy_comparison_finished",
                    {
                        "status": ended.status.value,
                        "runs": ended.runs,
                        "healthy_run": ended.healthy_run,
                    },
                ),
            )
        finally:
            self._end_attempt(failing.id)

    def _end_attempt(self, run_id: str) -> None:
        with self._attempts_lock:
            self._attempting.discard(run_id)
        self._slots.release()

    def _find_healthy(
        self,
        failing: Run,
        app: LoadedApplication,
        entity: Entity,
        plan: _SamplePlan | None,
        subject: str | None,
        attempt: HealthyAttempt,
    ) -> HealthyAttempt:
        if subject is not None:
            run = self._run_candidate(failing, subject, None)
            attempt = attempt.model_copy(update={"runs": [run.id]})
            if run.subject is None or run.status is RunStatus.SKIPPED:
                reason = run.skip_reason or run.stop_reason or f"{subject} is not one subject"
                return _could_not_run(attempt, reason)
            return attempt.model_copy(
                update={"status": HealthyAttemptStatus.COMPARED, "healthy_run": run.id}
            )
        assert plan is not None  # no pasted ID means a sample was planned
        candidates, sample = self._read_candidates(failing, app, entity, plan)
        attempt = attempt.model_copy(update={"sample": sample})
        if not candidates:
            message = "the sample found no candidates; paste a known-good ID"
            return attempt.model_copy(
                update={"status": HealthyAttemptStatus.NO_CANDIDATES, "message": message}
            )
        for candidate in candidates:
            run = self._run_candidate(failing, candidate, entity.root_key)
            attempt = attempt.model_copy(update={"runs": [*attempt.runs, run.id]})
            if run.status is RunStatus.SKIPPED:
                return _could_not_run(attempt, run.skip_reason or "the run was skipped")
            if run.status is RunStatus.PASSED:
                return attempt.model_copy(
                    update={"status": HealthyAttemptStatus.COMPARED, "healthy_run": run.id}
                )
        message = f"none of the {len(candidates)} candidates passed; paste a known-good ID"
        return attempt.model_copy(
            update={"status": HealthyAttemptStatus.NONE_PASSED, "message": message}
        )

    def _read_candidates(
        self, failing: Run, app: LoadedApplication, entity: Entity, plan: _SamplePlan
    ) -> tuple[list[str], Evidence]:
        # One more than needed, in case the failing subject comes back after all.
        result = self._guarded_lookup(
            app,
            plan.connector,
            failing.environment,
            plan.lookup,
            plan.parameters,
            SAMPLE_ROWS,
        )
        found = [str(v) for v in field_values(result.rows, entity.root_key) if v is not None]
        candidates = [s for s in dict.fromkeys(found) if s != failing.subject][:MAX_CANDIDATES]
        # The candidates are root keys, and the failing subject a parameter: masked when PII.
        hidden = pii_fields(entity)
        evidence = Evidence(
            connector=plan.connector,
            query=result.query,
            parameters={
                name: MASKED if name.lower() in hidden else to_json(value)
                for name, value in result.parameters.items()
            },
            guards=result.guards,
            rows=mask_rows([{entity.root_key: subject} for subject in found], hidden),
            started_at=result.started_at,
            duration_ms=result.duration_ms,
        )
        return candidates, evidence

    def _run_candidate(self, failing: Run, value: str, identifier: str | None) -> Run:
        """One candidate's run, to its end, in the slot the comparison holds."""
        assert failing.investigation is not None  # start_healthy refused a run without one
        related = [*failing.investigation.related_runs, failing.id]
        request = StartRun(
            application=failing.application,
            environment=failing.environment,
            probe=failing.probe,
            value=value,
            identifier=identifier,
            # Its reason and reference: this is the same investigation.
            investigation=failing.investigation.model_copy(update={"related_runs": related}),
        )
        prepared = self._prepare(request, healthy_for=failing.id)
        run = prepared.run
        if run.status is RunStatus.SKIPPED:
            _close(prepared.connectors.values())
            self._save_and_audit(
                run,
                ("run_started", _started_details(self._masked(run))),
                ("run_finished", _finished_details(self._masked(run))),
            )
            return run
        self._save_and_audit(run, ("run_started", _started_details(self._masked(run))))
        self._execute(prepared)
        return self.get(run.id)

    # --- The run itself -----------------------------------------------------------------

    def _execute_all(self, prepared: list[_Prepared]) -> None:
        try:
            for each in prepared:
                self._execute(each)
        finally:
            self._slots.release()

    def _execute(self, prepared: _Prepared) -> None:
        run = prepared.run
        read: list[LocationResult] = []

        def saved(location: LocationResult) -> None:
            read.append(location)
            self._save(run.model_copy(update={"locations": list(read)}))

        try:
            lags, paused = self._measure_lag(prepared)
            if paused:
                skipped = _finished(run, RunStatus.SKIPPED, skip_reason="; ".join(paused))
                self._finish(skipped)
                return
            warnings = _lag_warnings(lags) if prepared.production else []
            if warnings:
                run = run.model_copy(update={"warnings": [*run.warnings, *warnings]})
                prepared = replace(prepared, run=run)
                self._save(run)
            if prepared.tries is not None:
                run = self._try_each(prepared)
                if run.subject is None:
                    self._finish(run)
                    return
            if prepared.resolve is not None:
                run = self._resolve(prepared, lags if prepared.production else {})
                if run.subject is None:
                    self._finish(run)
                    return
            result = run_probe(
                prepared.probe,
                prepared.entity,
                run.subject or run.identifier_value,
                prepared.connectors,
                on_location=saved,
                deadline=time.monotonic() + self._limits.time_limit_s,
                replica_lag=lags,
                caveat_unknown_lag=prepared.production,
            )
            timed_out = any(r.reason == RUN_TIME_LIMIT for r in result.locations)
            finished = _finished(
                run,
                result.status,
                stop_reason=RUN_TIME_LIMIT if timed_out else None,
                locations=result.locations,
                expectations=result.expectations,
            )
        except KillSwitchEngaged as err:
            # Before its first read it is a new run that never started; after, it stopped.
            if read:
                finished = _finished(run, RunStatus.FAILED, stop_reason=str(err), locations=read)
            else:
                finished = _finished(run, RunStatus.SKIPPED, skip_reason=str(err))
        except ReadsStopped as err:
            finished = _finished(run, RunStatus.FAILED, stop_reason=str(err), locations=read)
        except Exception as err:
            # Not a connector failure (those are Error outcomes): a fault in ProbeOwl. The
            # run is recorded as failed rather than left running, and the fault is logged.
            log.exception("run %s stopped by an unexpected error", run.id)
            finished = _finished(
                run,
                RunStatus.FAILED,
                stop_reason=f"stopped by an unexpected error ({type(err).__name__})",
                locations=read,
            )
        finally:
            _close(prepared.connectors.values())
        self._finish(finished)

    def _measure_lag(self, prepared: _Prepared) -> tuple[dict[str, ReplicaLag], list[str]]:
        """Each replica's lag for this run, and why the circuit breaker paused any."""
        lags: dict[str, ReplicaLag] = {}
        paused: list[str] = []
        for name, max_seconds in prepared.max_lag.items():
            connector = prepared.connectors[name]
            assert isinstance(connector, _Watched)  # every connector of a run is watched
            lag = connector.replica_lag()
            if lag is None:
                continue
            lags[name] = lag
            reason = self._breaker.replica_lag(connector.key, lag.seconds, max_seconds)
            if reason is not None:
                paused.append(reason)
        return lags, paused

    def _resolve(self, prepared: _Prepared, lags: Mapping[str, ReplicaLag]) -> Run:
        """The run with its identifier resolved: a subject, or finished without one.

        lags: the replica lags to caveat a Missing resolution with, in Production only.
        """
        assert prepared.resolve is not None  # only called for identifiers with via
        value, lookup = prepared.resolve
        run, entity = prepared.run, prepared.entity
        connector = via_connector(entity, run.identifier, prepared.connectors)
        found = resolve(entity, run.identifier, value, lookup, connector)
        via = entity.identifiers[run.identifier].via
        assert via is not None  # as above
        lag = lags.get(entity.locations[split_field_ref(via)[0]].using)
        resolution = found.result.model_copy(
            update={"caveat": lag_caveat(found.result.outcome, lag)}
        )
        run = run.model_copy(update={"resolution": resolution, "matches": found.matches})
        if found.result.outcome is Outcome.FOUND:
            run = run.model_copy(update={"subject": found.subjects[0]})
            self._save(run)
            return run
        if found.result.outcome is Outcome.AMBIGUOUS:
            return _finished(run, RunStatus.AMBIGUOUS)
        if found.result.outcome is Outcome.ERROR:
            return _finished(run, RunStatus.FAILED, stop_reason=_NOT_LOOKED_UP)
        return _finished(run, RunStatus.FAILED, stop_reason=found.result.reason)

    def _try_each(self, prepared: _Prepared) -> Run:
        """The run with the identifier that found its subject, after trying each in turn.

        The root key first, then each via identifier (#253). One subject found: that
        identifier is the run's, said in a warning. None: finished, naming each one tried.
        Different subjects: Ambiguous, with each match.
        """
        assert prepared.tries is not None  # only called when there is more than one to try
        value, tries = prepared.tries
        run, entity = prepared.run, prepared.entity
        found: list[tuple[str, Resolution]] = []
        errors: list[str] = []
        for name, lookup in tries:
            if lookup is None:
                location = entity.locations[root_location(entity)]
                result = resolve_root(entity, value, prepared.connectors[location.using])
            else:
                connector = via_connector(entity, name, prepared.connectors)
                result = resolve(entity, name, value, lookup, connector)
            if result.result.outcome is Outcome.ERROR:
                errors.append(f"{name}: {result.result.reason}")
            elif result.subjects:
                found.append((name, result))
        tried = " or ".join(name for name, _ in tries)
        subjects = list(dict.fromkeys(s for _, r in found for s in r.subjects))
        if not found:
            reason = f"no {entity.entity} has this value as {tried}"
            if errors:
                reason = f"{reason}; could not look up {'; '.join(errors)}"
            return _finished(run, RunStatus.FAILED, stop_reason=reason)
        if len(subjects) > 1:
            names = ", ".join(name for name, _ in found)
            resolution = LocationResult(
                location=found[0][1].result.location,
                outcome=Outcome.AMBIGUOUS,
                reason=f"this value finds {len(subjects)} subjects, as {names}",
            )
            matches = [m for _, r in found for m in r.matches]
            ambiguous = run.model_copy(update={"resolution": resolution, "matches": matches})
            return _finished(ambiguous, RunStatus.AMBIGUOUS)
        name, result = found[0]
        via = entity.identifiers[name].via
        note = (
            f"No identifier pattern placed this value, so each was tried ({tried}): it is a {name}"
        )
        run = run.model_copy(
            update={
                "identifier": name,
                # Masked while it might have been personal data; now it is known.
                "identifier_value": MASKED if entity.identifiers[name].pii else value,
                "via": via,
                "subject": result.subjects[0],
                "resolution": result.result if via is not None else None,
                "matches": result.matches,
                "warnings": [*run.warnings, note],
            }
        )
        self._save(run)
        return run

    def _finish(self, run: Run) -> None:
        self._save_and_audit(run, ("run_finished", _finished_details(self._masked(run))))

    # --- Store --------------------------------------------------------------------------

    def _save(self, run: Run) -> None:
        with self._store.begin() as conn:
            save_run(conn, self._workspace, self._masked(run))

    def _masked(self, run: Run) -> Run:
        """The run as stored: each PII identifier masked, and the subject if the key is (#66).

        Only the run in memory, while it runs, has the real value: never the store, the API,
        the audit or the CLI, which read the stored run.
        """
        app = self._applications.get(run.application)
        entity = app.entities.get(run.entity) if app is not None else None
        return run if entity is None else masked_run(run, entity)

    def _save_and_audit(self, run: Run, *events: tuple[str, dict[str, Any]]) -> None:
        """The run and its audit events in one transaction: never one without the other (#242)."""
        # Saved apart, a reader between the two saw a finished run with no run_finished event.
        with self._store.begin() as conn:
            save_run(conn, self._workspace, self._masked(run))
            for action, details in events:
                append_event(conn, self._workspace, _event(action, run, details))

    def _audit(self, action: str, run: Run, details: dict[str, Any]) -> None:
        with self._store.begin() as conn:
            append_event(conn, self._workspace, _event(action, run, details))


def _event(action: str, run: Run, details: dict[str, Any]) -> AuditEvent:
    return AuditEvent(
        action=action,
        actor=run.actor,
        application=run.application,
        environment=run.environment,
        run_id=run.id,
        details=details,
    )


def _could_not_run(attempt: HealthyAttempt, reason: str) -> HealthyAttempt:
    return attempt.model_copy(
        update={"status": HealthyAttemptStatus.COULD_NOT_RUN, "message": reason}
    )


PASTED_IN_PRODUCTION = (
    "in Production a pasted ID is a new lookup and needs a reason and a reference; this run has "
    "none: start a new run with them"
)


def check_pasted_subject(run: Run, app: LoadedApplication, entity: Entity, subject: str) -> None:
    """A subject pasted because the run stores it masked is a new lookup, so checked as one (#66).

    It must look like the entity's root key. In Production it needs a reason and a reference, as
    a run does: the run's own are reused, and a run without a reference is refused. The action
    it is pasted for writes its own audit event, with the subject masked.
    """
    detect(entity, subject, entity.root_key)
    production = app.application.environments[run.environment].production
    record = run.investigation
    if production and (record is None or not record.reference):
        raise RequestError(PASTED_IN_PRODUCTION, {"subject": PASTED_IN_PRODUCTION})


def _picked(ambiguous: Run, subjects: list[str] | None) -> list[str]:
    """Subjects picked from an Ambiguous run's matches, each once."""
    matched = {match.subject for match in ambiguous.matches}
    if not subjects:
        raise RequestError("pick subjects from the run's matches", {"subjects": "pick one or more"})
    unknown = [subject for subject in subjects if subject not in matched]
    if unknown or len(set(subjects)) != len(subjects):
        raise RequestError(
            "pick each subject once, from the run's matches",
            {"subjects": f"one or more of: {', '.join(sorted(matched))}"},
        )
    return subjects


def _lookup_for(app: LoadedApplication, entity: Entity, name: str) -> str | None:
    """The lookup that finds a subject by this identifier; None for the root key."""
    via = entity.identifiers[name].via
    return None if via is None else resolution_lookup(entity, name, _dialect(app, entity, via))


def _dialect(app: LoadedApplication, entity: Entity, via: str) -> str:
    connector = app.connectors[entity.locations[split_field_ref(via)[0]].using]
    if not isinstance(connector, SqlConnector):
        raise ConfigError(f"identifiers are looked up in SQL locations only, not {via}")
    return RULES_BY_CONNECTOR_TYPE[connector.type].sqlglot_dialect


def _version(probe: Probe, draft_entity: Entity | None = None) -> str:
    # The probe's content identifies its version until probes are versioned in git (M3).
    if draft_entity is None:
        return "sha256:" + hashlib.sha256(probe.model_dump_json().encode()).hexdigest()[:12]
    # A test run of a draft: its probe and entity as they were, never mistaken for a saved one.
    content = probe.model_dump_json() + draft_entity.model_dump_json()
    return f"{DRAFT_VERSION_PREFIX}sha256:" + hashlib.sha256(content.encode()).hexdigest()[:12]


def _finished(run: Run, status: RunStatus, **updates: Any) -> Run:
    data = run.model_dump() | updates | {"status": status, "finished_at": datetime.now(UTC)}
    finished = Run.model_validate(data)
    return finished.model_copy(update={"caveat": _run_caveat(finished)})


def _run_caveat(run: Run) -> str | None:
    """'replica lag unknown: 2 Missing results may not have arrived yet', or None."""
    results = [*run.locations, *([run.resolution] if run.resolution else [])]
    count = sum(result.caveat is not None for result in results)
    if count == 0:
        return None
    noun = "result may" if count == 1 else "results may"
    return f"replica lag unknown: {count} Missing {noun} not have arrived yet"


def _started_details(run: Run) -> dict[str, Any]:
    # Given the run as stored, as _finished_details is.
    investigation = run.investigation
    if investigation is None:
        raise ConfigError("a run started by a person needs an investigation record")
    return {
        "probe": run.probe,
        "entity": run.entity,
        "identifiers": {run.identifier: run.identifier_value},
        "from_run": run.from_run,
        "healthy_for": run.healthy_for,
        "reason": investigation.reason.value,
        "reference": investigation.reference,
    }


def _finished_details(run: Run) -> dict[str, Any]:
    # Given the run as stored: a PII key's subject and matches are masked (#66).
    return {
        "status": run.status.value,
        "stop_reason": run.stop_reason,
        "skip_reason": run.skip_reason,
        "warnings": run.warnings,
        "caveat": run.caveat,
        "subject": run.subject,
        "matches": [match.subject for match in run.matches],
        "locations": dict(Counter(r.outcome.value for r in run.locations)),
        "expectations": dict(Counter(e.result.value for e in run.expectations)),
    }


@runtime_checkable
class _LagSource(Protocol):
    def replica_lag(self) -> ReplicaLag:
        """How far the replica behind this connector is behind its primary."""
        ...


class _NotOpened:
    """A connector in a run that a kill switch stopped. Such a run is Skipped and never reads."""

    def lookup(
        self, lookup: str, parameters: Mapping[str, object], max_rows: int, /
    ) -> LookupResult:
        """Never called: nothing runs a Skipped run's lookups."""
        raise ConnectorError("this run was stopped before it opened its connectors")

    def close(self) -> None:
        """Nothing was opened."""


class _Watched:
    """A run's connector: asks the kill switch first, and reports to the circuit breaker."""

    def __init__(
        self,
        inner: OpenConnector,
        breaker: CircuitBreaker,
        key: Key,
        kill: KillSwitches,
        remember: Callable[[str, list[dict[str, Any]]], None] | None = None,
    ):
        self._inner = inner
        self._remember = remember
        self._breaker = breaker
        self._kill = kill
        self.key = key

    def _check(self) -> None:
        self._kill.check(self.key[0], self.key[1])

    def lookup(
        self, lookup: str, parameters: Mapping[str, object], max_rows: int, /
    ) -> LookupResult:
        """The connector's lookup, if it may still read; timed for the circuit breaker."""
        self._check()
        start = time.monotonic()
        try:
            result = self._inner.lookup(lookup, parameters, max_rows)
        except ConnectorError:
            self._breaker.lookup(self.key, time.monotonic() - start, failed=True)
            raise
        self._breaker.lookup(self.key, time.monotonic() - start, failed=False)
        # The last place the rows are not masked: the engine masks them next.
        if self._remember is not None:
            self._remember(lookup, result.rows)
        return result

    def replica_lag(self) -> ReplicaLag | None:
        """The replica's lag, if this connector can measure one."""
        self._check()
        return self._inner.replica_lag() if isinstance(self._inner, _LagSource) else None

    def close(self) -> None:
        """Close the connector."""
        self._inner.close()


def _lag_warnings(lags: Mapping[str, ReplicaLag]) -> list[str]:
    # Unknown lag is not a failure, but a Missing result on that replica may only be late.
    return [
        f"Replica lag unknown for {name} ({lag.cause}): Missing results can't be confirmed fresh"
        for name, lag in sorted(lags.items())
        if lag.seconds is None
    ]


def _max_lag(
    app: LoadedApplication, connectors: Mapping[str, OpenConnector], environment: str
) -> dict[str, float]:
    found = {}
    for name in connectors:
        connector = app.connectors[name]
        if isinstance(connector, SqlConnector):
            binding = connector.environments[environment]
            if binding.replica:
                found[name] = seconds(binding.max_replica_lag)
    return found


def _close(connectors: Iterable[OpenConnector]) -> None:
    for connector in connectors:
        connector.close()


def _refuse_missing_passwords(
    app: LoadedApplication, used: list[str], environment: str, title: str
) -> None:
    """PasswordMissing for the first binding whose keychain entry this machine lacks.

    Before the run exists, so the person is asked for the password, not shown a failed run.
    """
    for name in used:
        connector = app.connectors.get(name)
        binding = connector.environments.get(environment) if connector else None
        if binding is None or binding.secret is None or keychain_entry(binding.secret) is None:
            continue
        # Looked up at call time, so tests can stand in for the keychain.
        if not secrets.secret_saved(binding.secret):
            raise PasswordMissing(name, environment, title)
