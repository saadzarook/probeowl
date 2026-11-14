# Author: @SaadZarook
"""API routes. Stubs for now: each answers 501 until its milestone implements it."""

from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from probeowl.api.models import (
    ApplicationSummary,
    CatalogChange,
    ConfirmTable,
    DraftProbe,
    DraftRun,
    EntityChange,
    EnvironmentSummary,
    ImportSchema,
    PickMatches,
    ProbeChange,
    ProbeSummary,
    Problem,
    RunbookView,
    RunList,
    RunRunbookStep,
    RunsStarted,
    RunStarted,
    SaveEntity,
    SaveProbe,
    SetKillSwitch,
    StartHealthyComparison,
    StartRun,
    SuggestedRunbook,
    TickRunbookStep,
    YamlFile,
)
from probeowl.api.redaction import Redactor
from probeowl.api.titles import titled
from probeowl.catalog.service import CatalogService
from probeowl.checks.saving import ProbeWrites
from probeowl.checks.service import check_draft, runnable
from probeowl.checks.time_offsets import ColumnTypes, imported_types
from probeowl.connector_status import ConnectorStatuses
from probeowl.errors import NotFoundError, RequestError, unknown_application
from probeowl.model import Catalog, Entity, Run, RunStatus
from probeowl.model.check import LoadedApplication
from probeowl.model.common import Name, humanized
from probeowl.model.connector_status import ConnectionTest, ConnectorStatus
from probeowl.model.healthy import HealthyComparison
from probeowl.model.kill_switch import KillSwitch, KillTarget
from probeowl.model.reload import ConfigReload
from probeowl.model.review import CatalogReview
from probeowl.model.run import Actor
from probeowl.model.runbook_step import CopiedWriteQuery, StepInRun, StepRan
from probeowl.model.save_check import SaveCheckReport
from probeowl.model.schema import SchemaSnapshot
from probeowl.model.search import SearchResults
from probeowl.model.setup import (
    DraftConnection,
    SaveApplication,
    SaveConnector,
    SaveSecret,
    SetupState,
)
from probeowl.plugins.api import PluginManifest
from probeowl.reload import ConfigReloader
from probeowl.runbook_steps import RunbookSteps
from probeowl.runbooks import matching, runbook_view, suggested
from probeowl.runs import RunService
from probeowl.search import MAX_QUERY, search
from probeowl.secret_writes import SecretWrites
from probeowl.setup_writes import SetupWrites

# The media type is set to application/problem+json in app.py.
_PROBLEM = {"model": Problem}


def _problems(*codes: int) -> dict[int | str, dict[str, Any]]:
    return {code: _PROBLEM for code in codes}


router = APIRouter(prefix="/api", responses=_problems(500, 501))


def _not_implemented(milestone: str) -> NoReturn:
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, f"Not implemented yet ({milestone}).")


@router.get("/applications", tags=["applications"])
def list_applications(http: Request) -> list[ApplicationSummary]:
    """List the applications this ProbeOwl knows."""
    apps = _runs(http).applications
    return [_summary(apps[name]) for name in sorted(apps)]


@router.get("/applications/{application}", tags=["applications"], responses=_problems(404))
def get_application(application: str, http: Request) -> ApplicationSummary:
    """Get one application."""
    return _summary(_application(http, application))


@router.get(
    "/applications/{application}/environments", tags=["applications"], responses=_problems(404)
)
def list_environments(application: str, http: Request) -> list[EnvironmentSummary]:
    """List an application's environments."""
    return _summary(_application(http, application)).environments


@router.get("/applications/{application}/entities", tags=["entities"], responses=_problems(404))
def list_entities(application: str, http: Request) -> list[Entity]:
    """List an application's entities with their identifiers and locations."""
    entities = _application(http, application).entities
    return [entities[name] for name in sorted(entities)]


@router.get(
    "/applications/{application}/entities/{entity}", tags=["entities"], responses=_problems(404)
)
def get_entity(application: str, entity: str, http: Request) -> Entity:
    """Get one entity: identifiers, locations and key graph."""
    found = _application(http, application).entities.get(entity)
    if found is None:
        raise NotFoundError(f"{application} has no entity named {entity}")
    return found


@router.put(
    "/applications/{application}/entities/{entity}",
    tags=["entities"],
    responses=_problems(404, 409, 422),
)
def save_entity(application: str, entity: str, body: SaveEntity, http: Request) -> EntityChange:
    """Save a new entity into its own YAML file in the folder owl serve loaded.

    As a probe's save: first without `base`, nothing is written and the response is the change
    and the save checks' report, plans included; then with that `base` to write exactly that
    change. An entity failing a save check is a 422 and is never written; 409 if the file
    changed in between. Only a new entity: one that exists is changed with one of its probes,
    so that they are checked together. Never commits or pushes; audited.
    """
    saved = _writes(http).save_entity(
        application, entity, body.entity, body.environment, body.base, body.save_without_plan_check
    )
    return EntityChange(**(asdict(saved.change) | {"base": saved.base}), report=saved.report)


@router.get("/applications/{application}/probes", tags=["probes"], responses=_problems(404))
def list_probes(
    application: str,
    http: Request,
    entity: str | None = None,
    environment: str | None = None,
) -> list[ProbeSummary]:
    """List an application's inspect and validate probes.

    entity: only that entity's probes. environment: only the probes enabled there.
    """
    app = _application(http, application)
    if environment is not None and environment not in app.application.environments:
        raise NotFoundError(f"{application} has no environment {environment}")
    return [
        ProbeSummary(
            name=probe.probe,
            title=probe.title,
            kind=probe.kind,
            entity=probe.entity,
            owner=probe.owner,
            description=probe.description,
        )
        for name, probe in sorted(app.probes.items())
        if entity in (None, probe.entity)
        and (environment is None or probe.environments is None or environment in probe.environments)
    ]


def _applications(http: Request) -> dict[str, LoadedApplication]:
    # The run service and the catalog share one dictionary of applications in owl serve.
    catalog = getattr(http.app.state, "catalog", None)
    return catalog.applications if isinstance(catalog, CatalogService) else _runs(http).applications


def _application(http: Request, name: str) -> LoadedApplication:
    app = _applications(http).get(name)
    if app is None:
        raise unknown_application(name, _applications(http))
    return app


def _summary(app: LoadedApplication) -> ApplicationSummary:
    # Names, titles and owners only: bindings, hosts and secrets never leave the backend.
    return ApplicationSummary(
        name=app.application.application,
        title=app.application.title,
        owner=app.application.owner,
        environments=[
            EnvironmentSummary(name=name, title=env.title, production=env.production)
            for name, env in app.application.environments.items()
        ],
    )


@router.post(
    "/applications/{application}/probes/checks",
    tags=["probes"],
    responses=_problems(404),
)
def check_draft_probe(application: str, body: DraftProbe, http: Request) -> SaveCheckReport:
    """The save checks of a draft probe (and entity), without saving anything.

    Read-only SQL for every lookup the probe can run (its entity's lookups, fallbacks,
    fan-out and identifier lookups), the expectations against the entity, the environments'
    bindings, and the label rules: a Transaction, Log or Audit table is read by key or within
    a time window, and every PII column selected is masked. A failed check means it cannot be
    saved; a draft that cannot be read fails the first check.
    """
    app = _application(http, application)
    if body.environment is not None and body.environment not in app.application.environments:
        raise NotFoundError(f"{application} has no environment {body.environment}")
    plans = getattr(http.app.state, "plans", None)
    # With no store there is no knowing whether a switch is on, so no plan is read.
    kill = http.app.state.kill
    guarded = kill.guarded(application, plans) if kill is not None and plans is not None else None
    columns = _column_types(http, application)
    return check_draft(app, body.probe, body.entity, body.environment, guarded, columns)


@router.get("/plugins", tags=["plugins"])
def list_plugins(http: Request) -> list[PluginManifest]:
    """The plugins that are on. One that is off is not listed, and its routes do not exist."""
    plugins: list[PluginManifest] = http.app.state.plugins
    return plugins


@router.get("/me", tags=["runs"], responses=_problems(501))
def who_am_i(http: Request) -> Actor:
    """Who is acting: the person running owl serve, as every run and audit record names them."""
    return _runs(http).actor


@router.post("/config/reload", tags=["connectors"], responses=_problems(409, 422))
def reload_configuration(http: Request) -> ConfigReload:
    """Read owl serve's config folder again and load it, without a restart. Audited.

    The folder is checked exactly as at start: if owl serve would refuse it, the reload is
    refused (422) and the configuration loaded before stays. Refused (409) while a run is
    running. Kill switches stay as they are: they are kept in the store, not the folder.
    """
    reloader = getattr(http.app.state, "reloader", None)
    if not isinstance(reloader, ConfigReloader):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "reloading needs a config folder: start the API with owl serve --config <folder>",
        )
    return reloader.reload()


@router.get("/kill-switches", tags=["connectors"], responses=_problems(503))
def list_kill_switches(http: Request) -> list[KillSwitch]:
    """The kill switches that are on. 503 if the store cannot be read."""
    return _runs(http).kill.all()


def _kill_target(http: Request, body: SetKillSwitch) -> KillTarget:
    # A switch on a misspelt name would stop nothing, and look as if it had.
    target = KillTarget(scope=body.scope, application=body.application, connector=body.connector)
    if target.application is not None:
        app = _application(http, target.application)
        if target.connector is not None and target.connector not in app.connectors:
            raise NotFoundError(f"{target.application} has no connector named {target.connector}")
    return target


@router.post("/kill-switches", tags=["connectors"], responses=_problems(404, 422, 503))
def engage_kill_switch(body: SetKillSwitch, http: Request) -> KillSwitch:
    """Turn a kill switch on: global, for an application, or for a connector in every environment.

    It stops every read through what it covers: runs, plan checks, schema import, live status and
    Test connection. A lookup in flight finishes within its statement timeout, then the run stops
    with this reason; new runs are Skipped. If it is already on, returns it as it is. Audited.
    """
    runs = _runs(http)
    return runs.kill.engage(_kill_target(http, body), body.reason, body.reference, runs.actor)


@router.post("/kill-switches/release", tags=["connectors"], responses=_problems(404, 422, 503))
def release_kill_switch(body: SetKillSwitch, http: Request) -> KillSwitch:
    """Turn a kill switch off, and return what was on. Audited. 404 if it is not on.

    Broader switches that are on still apply.
    """
    runs = _runs(http)
    return runs.kill.release(_kill_target(http, body), body.reason, body.reference, runs.actor)


def _writes(http: Request) -> ProbeWrites:
    writes = getattr(http.app.state, "probes", None)
    if not isinstance(writes, ProbeWrites):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "saving probes needs a config folder: start the API with owl serve --config <folder>",
        )
    return writes


@router.get(
    "/applications/{application}/probes/{probe}/yaml",
    tags=["probes"],
    responses=_problems(404, 422),
)
def get_probe_yaml(application: str, probe: str, http: Request) -> YamlFile:
    """Get a probe's file as it is on disk, to edit: a rebuilt copy would lose its comments.

    404 if there is no such probe; 422 if the file holds more than this probe.
    """
    return YamlFile(**asdict(_writes(http).probe_file(application, probe)))


@router.get(
    "/applications/{application}/entities/{entity}/yaml",
    tags=["entities"],
    responses=_problems(404, 422),
)
def get_entity_yaml(application: str, entity: str, http: Request) -> YamlFile:
    """Get an entity's file as it is on disk, to edit: a rebuilt copy would lose its comments.

    404 if there is no such entity; 422 if the file holds more than this entity.
    """
    return YamlFile(**asdict(_writes(http).entity_file(application, entity)))


@router.put(
    "/applications/{application}/probes/{probe}",
    tags=["probes"],
    responses=_problems(404, 409, 422),
)
def save_probe(application: str, probe: str, body: SaveProbe, http: Request) -> ProbeChange:
    """Save a probe into its YAML file in the folder owl serve loaded.

    Call it first without `base`: nothing is written, and the response is the change as a
    diff with its `base` and the save checks' report. Then call it again with that `base` to
    write exactly that change, atomically. The save checks run both times, plans included: a
    probe that fails one is a 422 naming each failure, and is never written. 409 if a file
    changed in between. Never commits or pushes; audited.

    With `entity`, its file is saved too, under the same rules: one `base` covers both, and
    both are written or neither. Other probes of the entity must still pass.

    In a Production environment, a write whose plan checks could not run is a 422 unless
    `save_without_plan_check` is true; that acknowledgement is audited with the actor.
    """
    saved = _writes(http).save(
        application,
        probe,
        body.probe,
        body.environment,
        body.base,
        body.entity,
        body.save_without_plan_check,
    )
    entity = saved.entity_change
    return ProbeChange(
        **(asdict(saved.change) | {"base": saved.base}),
        report=saved.report,
        entity_change=CatalogChange(**asdict(entity)) if entity else None,
    )


@router.post(
    "/applications/{application}/probes/test-runs",
    tags=["probes"],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "The test run has started.",
            "headers": {
                "Location": {
                    "description": "The run's URL, /api/runs/{id}.",
                    "schema": {"type": "string"},
                }
            },
        },
        **_problems(404, 422, 429),
    },
)
def run_draft(application: str, body: DraftRun, http: Request, response: Response) -> RunStarted:
    """Run a draft probe without saving it. Poll the Location until it is no longer running.

    Like any run: an investigation record (with a reference in Production), every guard, the
    run limits, stored and audited; its probe_version starts with draft:. 422 when the draft
    fails a save check, naming each failure.
    """
    runs = _runs(http)
    app = _application(http, application)
    draft = runnable(app, body.probe, body.entity, _column_types(http, application))
    request = StartRun(
        application=application,
        environment=body.environment,
        probe=draft.probe.probe,
        value=body.value,
        identifier=body.identifier,
        investigation=body.investigation,
    )
    run_id = runs.start_draft(request, draft.probe, draft.entity)
    response.headers["Location"] = f"/api/runs/{run_id}"
    return RunStarted(id=run_id, status=RunStatus.RUNNING)


@router.post(
    "/runs",
    tags=["runs"],
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "The run has started.",
            "headers": {
                "Location": {
                    "description": "The run's URL, /api/runs/{id}.",
                    "schema": {"type": "string"},
                }
            },
        },
        **_problems(404, 422, 429),
    },
)
def start_run(body: StartRun, http: Request, response: Response) -> RunStarted:
    """Start an inspect or validate run. Poll the Location until it is no longer running.

    The identifier is detected from the value's pattern unless one is given. A value of
    any identifier other than the root key is looked up first: one subject runs the
    probe; several make the run Ambiguous, with its matches and nothing else run.

    404 for an unknown application, environment or probe. 422 when the value matches no
    identifier or several, the probe is not enabled in the environment, the investigation
    record is incomplete for it (reason and reference in Production), or a location has no
    binding there. 429 when the maximum number of runs is already running; nothing is queued.
    """
    run_id = _runs(http).start(body)
    response.headers["Location"] = f"/api/runs/{run_id}"
    return RunStarted(id=run_id, status=RunStatus.RUNNING)


@router.post(
    "/runs/{run_id}/matches",
    tags=["runs"],
    status_code=status.HTTP_202_ACCEPTED,
    responses=_problems(404, 422, 429),
)
def pick_matches(run_id: str, body: PickMatches, http: Request) -> RunsStarted:
    """Run an Ambiguous run's probe for subjects picked from its matches, up to 5.

    Each picked subject gets its own run, with the Ambiguous run's investigation record and
    from_run pointing back to it. They run one after another and count as one run toward
    the limit. 404 for an unknown run; 422 when the run is not Ambiguous or a subject is not
    one of its matches; 429 when the maximum number of runs is already running.
    """
    ids = _runs(http).start_matches(run_id, body.subjects, body.value, body.picks)
    return RunsStarted(runs=[RunStarted(id=i, status=RunStatus.RUNNING) for i in ids])


@router.post(
    "/runs/{run_id}/healthy-comparison",
    tags=["runs"],
    status_code=status.HTTP_202_ACCEPTED,
    responses=_problems(403, 404, 422, 429, 503),
)
def start_healthy_comparison(
    run_id: str, body: StartHealthyComparison, http: Request
) -> HealthyComparison:
    """Compare a finished run with a known-good subject. Poll the same path until it has ended.

    The sample lookup finds candidates, and the probe runs on each until one passes, up to 3
    audited runs linked by healthy_for, with this run's investigation record. A pasted ID runs
    once and is compared even if it does not pass, with a warning. They run one after another
    and count as one run toward the limit. A new comparison replaces the last. 403 if a kill
    switch covers the sample's connector. 422 if the run is not finished or lacks a field the
    sample needs. 429 when the maximum number of runs is already running.
    """
    runs = _runs(http)
    runs.start_healthy(run_id, body.narrow, body.subject, body.failing_subject)
    redactor: Redactor = http.app.state.redactor
    return runs.comparison(run_id, redactor.run)


@router.get("/runs/{run_id}/healthy-comparison", tags=["runs"], responses=_problems(404))
def get_healthy_comparison(run_id: str, http: Request) -> HealthyComparison:
    """A run's latest comparison, field by field. A masked field says only whether it matches."""
    redactor: Redactor = http.app.state.redactor
    return _runs(http).comparison(run_id, redactor.run)


@router.get("/search", tags=["search"], responses=_problems(404, 422))
def search_everything(
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY)],
    http: Request,
    application: Name | None = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 8,
) -> SearchResults:
    """Global search: names and titles, imported tables and columns, and runs. Not audited.

    Prefix matches rank before substring matches, names before titles. A run is found by its
    id (4 or more characters) or by its subject or identifier as stored: exactly, or by a
    prefix of 4 or more characters when it isn't PII. A masked value matches nothing. Never
    matched: run results, evidence, lookups' SQL, runbook steps, hosts, URLs, logins or
    keychain names. application: omitted, every application. limit: per group.
    """
    service = _runs(http)
    return search(service.applications, service.store, q, application, limit)


@router.get("/runs", tags=["runs"], responses=_problems(404, 422))
def list_recent_runs(
    application: str, environment: str, http: Request, before: str | None = None
) -> RunList:
    """Runs in one application and environment, newest first, 50 at a time.

    Each opens with GET /api/runs/{id}. A draft's test run is marked draft. A healthy
    comparison's candidates are left out: they show on the run they were compared with.
    Identifiers that are PII stay masked. before: the next value of the previous page.
    """
    app = _application(http, application)
    if environment not in app.application.environments:
        raise NotFoundError(f"{application} has no environment {environment}")
    page, more = _runs(http).recent(application, environment, _before(before))
    redactor: Redactor = http.app.state.redactor
    shown = [
        run.model_copy(
            update={
                "probe_title": app.probes[run.probe].title
                if run.probe in app.probes
                else humanized(run.probe),
                "stop_reason": redactor.text(run.stop_reason) if run.stop_reason else None,
                "skip_reason": redactor.text(run.skip_reason) if run.skip_reason else None,
                "caveat": redactor.text(run.caveat) if run.caveat else None,
            }
        )
        for run in page
    ]
    return RunList(runs=shown, next=f"{more[0].isoformat()}|{more[1]}" if more else None)


def _before(cursor: str | None) -> tuple[datetime, str] | None:
    # "<started_at>|<id>" of the last run on the page before, as next gave it.
    if cursor is None:
        return None
    at, _, run_id = cursor.partition("|")
    try:
        return datetime.fromisoformat(at), run_id
    except ValueError as err:
        raise RequestError(
            "before is not a cursor this list gave", {"before": "pass next as it was"}
        ) from err


@router.get("/runs/{run_id}", tags=["runs"], responses=_problems(404))
def get_run(run_id: str, http: Request) -> Run:
    """Get a run as it progresses: location outcomes fill in as they arrive (masked)."""
    # Runs already stored may hold what newer ones never would.
    redactor: Redactor = http.app.state.redactor
    run = redactor.run(_runs(http).get(run_id))
    return titled(run, _applications(http).get(run.application))


def _column_types(http: Request, application: str) -> ColumnTypes | None:
    # With no store there are no imports, so no time offset check.
    runs = getattr(http.app.state, "runs", None)
    if not isinstance(runs, RunService):
        return None
    return imported_types(runs.store, runs.workspace, application)


def _runs(http: Request) -> RunService:
    runs = getattr(http.app.state, "runs", None)
    if not isinstance(runs, RunService):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "runs need a store and probe files: start the API with owl serve --config <folder>",
        )
    return runs


@router.get("/applications/{application}/catalog", tags=["catalog"], responses=_problems(404))
def get_catalog(application: str, http: Request) -> Catalog:
    """Get an application's table catalog: its confirmed labels and marked columns (YAML)."""
    catalog = _application(http, application).catalog
    if catalog is None:
        raise NotFoundError(f"{application} has no catalog yet")
    return catalog


@router.post(
    "/applications/{application}/catalog/imports",
    tags=["catalog"],
    status_code=status.HTTP_201_CREATED,
    responses=_problems(404, 422, 502),
)
def import_schema(application: str, body: ImportSchema, http: Request) -> SchemaSnapshot:
    """Import a SQL connector's schema in one environment from its binding, never data.

    Tables, columns with types, primary keys, indexes and row estimates, read from the
    database's catalog views through the connector's read-only login (in Production, the
    replica). The snapshot is stored and the import audited. 404 for an unknown application,
    environment or connector; 422 for a REST connector or one with no binding there; 502
    when the database cannot be read.
    """
    return _catalog(http).run(application, body.connector, body.environment)


@router.get(
    "/applications/{application}/catalog/schema", tags=["catalog"], responses=_problems(404)
)
def get_schema(application: str, connector: str, environment: str, http: Request) -> SchemaSnapshot:
    """The most recent schema import of a connector in one environment."""
    return _catalog(http).latest(application, connector, environment)


@router.get(
    "/applications/{application}/catalog/review", tags=["catalog"], responses=_problems(404)
)
def review_catalog(application: str, http: Request) -> CatalogReview:
    """Tables to review: suggested labels, drift from each environment's latest import, usage.

    Every table in catalog.yaml, in an import, or read by a probe. A suggestion is guessed from
    the table's name and shape; a person confirms each label. Drift compares the catalog with
    each environment's latest import, and environments with one another.
    """
    return _catalog(http).review(application)


@router.put(
    "/applications/{application}/catalog/tables/{connector}/{table}",
    tags=["catalog"],
    responses=_problems(404, 409, 422),
)
def confirm_table(
    application: str, connector: str, table: str, body: ConfirmTable, http: Request
) -> CatalogChange:
    """Confirm a table's label, owner and marked columns into the application's catalog.yaml.

    Call it first without `base`: nothing is written, and the response is the change as a
    diff with the file's `base`. Then call it again with that `base` to write exactly that
    change, atomically. 409 if the file changed in between; 422 if the catalog would not be
    valid or the path leaves the folder owl serve loaded. Never commits or pushes; audited.
    """
    if (body.entry.connector, body.entry.table.lower()) != (connector, table.lower()):
        raise RequestError(
            f"the entry is for {body.entry.connector}.{body.entry.table}, not {connector}.{table}",
            {"entry.table": f"use {connector}.{table}"},
        )
    change = _catalog(http).confirm(application, body.entry, body.base)
    return CatalogChange(**asdict(change))


def _catalog(http: Request) -> CatalogService:
    catalog = getattr(http.app.state, "catalog", None)
    if not isinstance(catalog, CatalogService):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "the catalog needs a store and probe files: start the API with owl serve",
        )
    return catalog


@router.get("/applications/{application}/connectors", tags=["connectors"], responses=_problems(404))
def list_connectors(application: str, environment: str, http: Request) -> list[ConnectorStatus]:
    """Each connector in one environment: health, lag, plan access and guards, read live.

    Never where a connector points or who logs in.
    """
    connectors = http.app.state.connectors
    if not isinstance(connectors, ConnectorStatuses):
        _not_implemented("M3")
    return connectors.for_environment(application, environment)


@router.post(
    "/applications/{application}/connectors/{connector}/test",
    tags=["connectors"],
    responses=_problems(403, 404, 503),
)
def test_connection(
    application: str, connector: str, environment: str, http: Request
) -> ConnectionTest:
    """Connect once to a binding, or GET a REST API's base URL once, and say how it went. Audited.

    Reads no data. 403 if a kill switch covers it, before its secret is read. A connector the
    circuit breaker paused is left alone, and reported as not reached with the reason.
    """
    connectors = http.app.state.connectors
    if not isinstance(connectors, ConnectorStatuses):
        _not_implemented("M3")
    result = connectors.test_connection(application, connector, environment)
    _runs(http).audit(
        "connection_tested",
        {
            "application": application,
            "connector": connector,
            "environment": environment,
            "reachable": result.reachable,
        },
    )
    return result


@router.get("/setup", tags=["setup"])
def get_setup(http: Request) -> SetupState:
    """What first run needs: applications loaded, where new ones go, the store and the keychain.

    Never the store's URL or login.
    """
    return _setup(http).state()


@router.put("/applications/{application}", tags=["setup"], responses=_problems(409, 422))
def save_application(application: Name, body: SaveApplication, http: Request) -> CatalogChange:
    """Write a new application's application.yaml into the folder owl serve loaded.

    As every save: first without `base`, nothing is written and the response is the change;
    then with that `base` to write exactly that change. Loaded at once, so the next screen
    finds it. 409 if an application of that id is loaded already, if the file changed since
    its preview, or while a run is running. Never commits or pushes; audited.
    """
    return CatalogChange(**asdict(_setup(http).save_application(application, body)))


@router.put(
    "/applications/{application}/connectors/{connector}",
    tags=["setup"],
    responses=_problems(404, 409, 422),
)
def save_connector(
    application: str, connector: Name, body: SaveConnector, http: Request
) -> CatalogChange:
    """Write a new connector with one binding, or a binding for an environment not bound yet.

    Preview, then write, as every save. The binding's secret is always the keychain reference
    keyring:probeowl/<connector>.<environment>, chosen here: the request never carries one,
    and no password goes into a file. A Production SQL binding must be a replica (422). 409
    if the binding exists already: editing one is done in its file. Loaded at once; audited
    without host, login or URL.
    """
    return CatalogChange(**asdict(_setup(http).save_connector(application, connector, body)))


@router.post(
    "/applications/{application}/connectors/test-draft",
    tags=["setup"],
    responses=_problems(403, 404, 422),
)
def test_draft_connection(application: str, body: DraftConnection, http: Request) -> ConnectionTest:
    """Connect once to a binding that is not saved yet, and say how it went. Audited.

    With the password given, used for this one connection and never stored, logged or
    returned; or with the one this machine's keychain has. The same fixed reasons as Test
    connection, never the server's own text. Reads no data. 403 if a kill switch covers the
    application, or the connector if it exists.
    """
    result = _setup(http).test_draft(application, body)
    _runs(http).audit(
        "connection_tested",
        {
            "application": application,
            "connector": body.connector,
            "environment": body.environment,
            "reachable": result.reachable,
            "draft": True,
        },
    )
    return result


@router.put(
    "/applications/{application}/connectors/{connector}/environments/{environment}/secret",
    tags=["setup"],
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_problems(404, 409, 422, 503),
)
def save_secret(
    application: str, connector: str, environment: str, body: SaveSecret, http: Request
) -> Response:
    """Save a binding's password or token in this machine's keychain. 204, with no body.

    Write-only: no route returns it. It is never written to a file, logged, or put in an error
    or the audit, which records only that it changed. Saved under the name the binding's file
    gives; 409 secret-exists if one is saved there already and `replace` is false, perhaps for
    another application's connector of the same id. 422 if the binding reads its secret from
    an environment variable; 503 if this machine has no system keychain.
    """
    writes = getattr(http.app.state, "secrets", None)
    if not isinstance(writes, SecretWrites):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "saving a password needs owl serve: start it with owl serve --config <folder>",
        )
    writes.save(application, connector, environment, body)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _setup(http: Request) -> SetupWrites:
    setup = getattr(http.app.state, "setup", None)
    if not isinstance(setup, SetupWrites):
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "setting up needs a config folder: start the API with owl serve --config <folder>",
        )
    return setup


@router.get("/applications/{application}/runbooks", tags=["runbooks"], responses=_problems(404))
def list_runbooks(
    application: str,
    http: Request,
    entity: str | None = None,
    q: str | None = None,
    table: str | None = None,
    identifier: str | None = None,
) -> list[RunbookView]:
    """List an application's runbooks, matching every filter given.

    q searches the symptom: its title, its steps' titles and the outcomes it applies to.
    table is a catalog table it uses; identifier is a type its queries take as a parameter.
    """
    app = _application(http, application)
    found = matching(app, app.runbooks.values(), q, entity, table, identifier)
    drifted = _drifted_tables(http, application)
    return [
        runbook_view(app, runbook, date.today(), drifted)
        for runbook in sorted(found, key=lambda r: r.runbook)
    ]


@router.get(
    "/applications/{application}/runbooks/{runbook}", tags=["runbooks"], responses=_problems(404)
)
def get_runbook(application: str, runbook: str, http: Request) -> RunbookView:
    """Get one runbook. Write query steps are never runnable."""
    app = _application(http, application)
    found = app.runbooks.get(runbook)
    if found is None:
        raise NotFoundError(f"{application} has no runbook named {runbook}")
    return runbook_view(app, found, date.today(), _drifted_tables(http, application))


@router.get("/runs/{run_id}/runbooks", tags=["runbooks"], responses=_problems(404))
def suggest_runbooks(run_id: str, http: Request) -> list[SuggestedRunbook]:
    """The runbooks whose applies_to matches this run's outcomes, closest first."""
    run = _runs(http).get(run_id)
    app = _application(http, run.application)
    return suggested(app, run, date.today(), _drifted_tables(http, run.application))


def _steps(http: Request) -> RunbookSteps:
    return RunbookSteps(_runs(http), getattr(http.app.state, "plans", None))


@router.get(
    "/runs/{run_id}/runbooks/{runbook}/steps", tags=["runbooks"], responses=_problems(404, 422)
)
def list_steps_in_run(run_id: str, runbook: str, http: Request) -> list[StepInRun]:
    """A runbook's steps with this run's values, ticks and latest results.

    A masked value is never filled in: its parameter stays as :name, and says why.
    """
    return _steps(http).in_run(run_id, runbook)


@router.post(
    "/runs/{run_id}/runbooks/{runbook}/steps/{step}/run",
    tags=["runbooks"],
    responses=_problems(403, 404, 422, 429, 503),
)
def run_step(run_id: str, runbook: str, step: str, body: RunRunbookStep, http: Request) -> StepRan:
    """Run one step the server marks runnable: a reviewed read query, or a probe. Audited.

    It takes a step's ID and never SQL: the SQL comes from the runbook and its values from the
    run. A read query's plan is read first: a full scan of a large table is refused, and in
    Production a plan that cannot be read needs run_without_plan_check. A write query is always
    refused. 403 if a kill switch covers it; 429 when the maximum number of runs is running.
    """
    return _steps(http).run_step(run_id, runbook, step, body.run_without_plan_check, body.subject)


@router.post(
    "/runs/{run_id}/runbooks/{runbook}/steps/{step}/tick",
    tags=["runbooks"],
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_problems(404, 422),
)
def tick_step(run_id: str, runbook: str, step: str, body: TickRunbookStep, http: Request) -> None:
    """Save a manual step's tick with the run. Audited."""
    _steps(http).tick(run_id, runbook, step, body.done)


@router.post(
    "/runs/{run_id}/runbooks/{runbook}/steps/{step}/copy",
    tags=["runbooks"],
    responses=_problems(404, 422),
)
def copy_write_query(run_id: str, runbook: str, step: str, http: Request) -> CopiedWriteQuery:
    """A write query with this run's values, under its header. The copy is audited. Never run."""
    return _steps(http).copy_write(run_id, runbook, step)


@router.post(
    "/applications/{application}/runbooks/{runbook}/steps/{step}/copy",
    tags=["runbooks"],
    responses=_problems(404, 422),
)
def copy_write_query_without_a_run(
    application: str, runbook: str, step: str, http: Request
) -> CopiedWriteQuery:
    """A write query as written, for a runbook opened with no run. The copy is audited."""
    return _steps(http).copy_write_without_a_run(application, runbook, step)


def _drifted_tables(http: Request, application: str) -> set[tuple[str, str]]:
    catalog = getattr(http.app.state, "catalog", None)
    if not isinstance(catalog, CatalogService):
        return set()
    tables = catalog.review(application).tables
    return {(table.connector, table.table.lower()) for table in tables if table.drift}
