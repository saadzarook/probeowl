# Author: @SaadZarook
"""owl mcp's six tools as plain functions over the loaded configuration and the store (#39).

Drafts only: nothing here writes a file, saves configuration, starts a run, reads a probed
database or opens a connector, except check_draft's plan checks when owl mcp was started with
--plan-checks, through the same guarded plan reader as saving. No output holds a password, token,
keychain value, host, URL, login or an environment variable's value, nor any row, evidence, run
subject or catalog value.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field
from sqlalchemy import Engine

from probeowl.catalog.review import build_review
from probeowl.checks.plans import PlanOpener
from probeowl.checks.rules import read_step_problems, read_step_statement
from probeowl.checks.service import check_draft as check_probe_draft
from probeowl.checks.service import check_entity
from probeowl.drafting.content import (
    FILES,
    GUIDE_PAGES,
    MISTAKES,
    RULES,
    TEMPLATES,
    DraftKind,
    example_files,
    examples_folder,
    required_fields,
    safe_example,
)
from probeowl.drafting.scan import redacted, scan
from probeowl.errors import ConfigError, NotFoundError, RequestError, unknown_application
from probeowl.model.check import LoadedApplication
from probeowl.model.common import ProbeOwlModel
from probeowl.model.load import parse_document, read_text
from probeowl.model.runbook import ReadQueryStep, Runbook
from probeowl.model.save_check import CheckStatus, SaveCheckReport
from probeowl.store.schemas import latest_snapshot
from probeowl.store.tables import LOCAL_WORKSPACE

MAX_DRAFT = 64 * 1024
PLAN_CHECKS_NOT_PRODUCTION = (
    "plan checks through an assistant run in non-Production environments only"
)
REMOVE_THIS = (
    "Remove this: passwords go in the keychain via Add connection or owl connector add; hosts "
    "stay in the connector file you write yourself"
)

# --- Outputs: these are contracts/mcp-tools.json's output schemas ---------------------------


class RequiredField(ProbeOwlModel):
    """One field the file must have, and what it means."""

    field: str
    meaning: str


class DraftingGuide(ProbeOwlModel):
    """How to draft one kind of file, from its schema and the save checks."""

    kind: DraftKind
    file: str = Field(description="Where it goes, with placeholders.")
    required: list[RequiredField]
    rules: list[str]
    mistakes: list[str] = Field(description="The save checks' common findings, as their fix.")
    guide: str = Field(description="The bundled guide page, as /guide/<page>/.")


class Template(ProbeOwlModel):
    """A file to start from: placeholders only, and its secret a keyring: reference."""

    kind: DraftKind
    path: str
    yaml: str


class Example(ProbeOwlModel):
    """One example file; a connector's hosts, logins, URLs and secrets are placeholders."""

    name: str
    application: str
    path: str
    yaml: str


class Examples(ProbeOwlModel):
    """The bundled examples of one kind."""

    kind: DraftKind
    items: list[Example]


class DraftFinding(ProbeOwlModel):
    """One check's finding, in the save checks' own words."""

    check: str
    severity: Literal["error", "warning"]
    message: str
    line: int | None = None


class Credential(ProbeOwlModel):
    """Something in the draft that looks like a secret or an address. Never any part of it."""

    what: Literal["password", "token", "connection_string", "private_key", "host", "ip_address"]
    line: int
    shown: str = Field(description="The kind and length only, such as 'password (12 characters)'.")


class DraftCheck(ProbeOwlModel):
    """What the checks say about a draft. Its text is never repeated, here or anywhere."""

    ok: bool = Field(description="False on any error, and on any credential found.")
    findings: list[DraftFinding]
    credentials: list[Credential]
    paste_into: str | None = Field(description="Where to paste it, when ok.")
    next: str


class LocationSummary(ProbeOwlModel):
    name: str
    connector: str
    cardinality: str


class EntitySummary(ProbeOwlModel):
    id: str
    title: str
    root_key: str
    identifiers: list[str]
    pii_identifiers: list[str] = Field(description="Named as PII: their values are always masked.")
    locations: list[LocationSummary]


class ProbeSummary(ProbeOwlModel):
    id: str
    title: str
    kind: str
    entity: str


class ConnectorSummary(ProbeOwlModel):
    """A connector by name and type: never where it points or who logs in."""

    id: str
    type: str
    environments: list[str]


class RunbookSummary(ProbeOwlModel):
    id: str
    title: str
    entity: str | None


class EnvironmentSummary(ProbeOwlModel):
    name: str
    title: str
    production: bool


class ApplicationConfig(ProbeOwlModel):
    id: str
    title: str
    environments: list[EnvironmentSummary]
    entities: list[EntitySummary]
    probes: list[ProbeSummary]
    connectors: list[ConnectorSummary]
    runbooks: list[RunbookSummary]


class ConfigList(ProbeOwlModel):
    """The loaded configuration by names, titles and kinds only."""

    applications: list[ApplicationConfig]


class ColumnInfo(ProbeOwlModel):
    """A column's name, type and roles. PII is a role; no value is ever given."""

    name: str
    type: str
    nullable: bool
    roles: list[str]
    links_to: str | None = None


class IndexInfo(ProbeOwlModel):
    columns: list[str]
    unique: bool


class TableInfo(ProbeOwlModel):
    name: str
    label: str | None
    confirmed: bool
    columns: list[ColumnInfo]
    primary_key: list[str]
    indexes: list[IndexInfo]


class Schema(ProbeOwlModel):
    """A connector's tables as last imported: names, types, labels and roles, never rows."""

    imported_at: str | None = Field(description="null: not imported yet; next says how.")
    tables: list[TableInfo]
    next: str | None = None


# --- The tools ------------------------------------------------------------------------------


def drafting_guide(kind: DraftKind) -> DraftingGuide:
    """How to draft a file of this kind."""
    return DraftingGuide(
        kind=kind,
        file=FILES[kind],
        required=[RequiredField(**r) for r in required_fields(kind)],
        rules=RULES[kind],
        mistakes=MISTAKES[kind],
        guide=GUIDE_PAGES[kind],
    )


def template(kind: DraftKind) -> Template:
    """A file of this kind to start from."""
    return Template(kind=kind, path=FILES[kind], yaml=TEMPLATES[kind])


def examples(kind: DraftKind, name: str | None = None) -> Examples:
    """The bundled examples of this kind, or the one named."""
    items = []
    for path in example_files(kind):
        if name is not None and path.stem != name:
            continue
        application = _application_id(path, kind)
        # Where it is among the examples, whose folders can be named otherwise than their ids.
        relative = path.relative_to(examples_folder()).as_posix()
        items.append(
            Example(
                name=path.stem,
                application=application,
                path=relative,
                yaml=safe_example(kind, path),
            )
        )
    return Examples(kind=kind, items=items)


def check_draft(
    applications: dict[str, LoadedApplication],
    folder: str,
    kind: DraftKind,
    text: str,
    application: str | None = None,
    name: str | None = None,
    plans: PlanOpener | None = None,
    environment: str | None = None,
) -> DraftCheck:
    """The save checks on a draft, after the credential scan; it writes nothing.

    plans: given only when owl mcp was started with --plan-checks. environment: where plans
    are read; default the application's first environment that isn't Production.
    """
    if len(text.encode("utf-8")) > MAX_DRAFT:
        return DraftCheck(
            ok=False,
            findings=[
                DraftFinding(check="Size", severity="error", message="the draft is over 64 KB")
            ],
            credentials=[],
            paste_into=None,
            next="Send a smaller draft: one file at a time.",
        )
    found = scan(text)
    findings = [
        DraftFinding(
            check=f.check, severity=f.severity, message=redacted(f.message, found), line=f.line
        )
        for f in _findings(applications, kind, text, application, plans, environment)
    ]
    credentials = [Credential(what=f.kind.value, line=f.line, shown=f.shown()) for f in found]
    ok = not credentials and not any(f.severity == "error" for f in findings)
    path = _paste_into(applications, folder, kind, text, application, name) if ok else None
    if credentials:
        next_step = REMOVE_THIS
    elif not ok:
        next_step = "Fix each error, then check the draft again."
    else:
        next_step = f"Paste this into {path}, then Reload configuration."
    return DraftCheck(
        ok=ok, findings=findings, credentials=credentials, paste_into=path, next=next_step
    )


def list_config(
    applications: dict[str, LoadedApplication], application: str | None = None
) -> ConfigList:
    """The loaded configuration by names, titles and kinds: never where anything points."""
    if application is not None and application not in applications:
        raise unknown_application(application, applications)
    chosen = [applications[application]] if application else list(applications.values())
    return ConfigList(applications=[_summary(app) for app in chosen])


def schema(
    applications: dict[str, LoadedApplication],
    store: Engine,
    application: str,
    connector: str,
    environment: str,
) -> Schema:
    """A connector's tables as last imported, with the catalog's labels and roles."""
    app = applications.get(application)
    if app is None:
        raise unknown_application(application, applications)
    if connector not in app.connectors:
        raise NotFoundError(f"{application} has no connector {connector}")
    try:
        with store.connect() as conn:
            snapshot = latest_snapshot(conn, LOCAL_WORKSPACE, application, connector, environment)
    except NotFoundError:
        return Schema(
            imported_at=None,
            tables=[],
            next=f"Import {connector}'s tables in {environment} first: Tables, Import, or "
            f"owl catalog import --application {application} --connector {connector}",
        )
    review = {(t.connector, t.table.lower()): t for t in build_review(app, [snapshot]).tables}
    marked = {
        (entry.table.lower(), column.lower()): spec
        for entry in (app.catalog.tables if app.catalog else [])
        if entry.connector == connector
        for column, spec in entry.columns.items()
    }
    tables = []
    for table in snapshot.tables:
        reviewed = review.get((connector, table.table.lower()))
        label = (
            reviewed.label or (reviewed.suggestion.label if reviewed.suggestion else None)
            if reviewed
            else None
        )
        columns = []
        for column in table.columns:
            spec = marked.get((table.table.lower(), column.name.lower()))
            target = spec.links_to if spec else None
            columns.append(
                ColumnInfo(
                    name=column.name,
                    type=column.type,
                    nullable=column.nullable,
                    roles=[str(r) for r in spec.roles] if spec else [],
                    links_to=None
                    if target is None
                    else ".".join(p for p in (target.connector, target.table, target.column) if p),
                )
            )
        tables.append(
            TableInfo(
                name=table.table,
                label=str(label) if label else None,
                confirmed=bool(reviewed and reviewed.label),
                columns=columns,
                primary_key=table.primary_key,
                indexes=[IndexInfo(columns=i.columns, unique=i.unique) for i in table.indexes],
            )
        )
    return Schema(imported_at=snapshot.imported_at.isoformat(), tables=tables)


# --- check_draft's checks -------------------------------------------------------------------


Severity = Literal["error", "warning"]


class _Finding:
    """A check's finding before its message is redacted."""

    def __init__(self, check: str, severity: Severity, message: str):
        self.check, self.severity, self.message = check, severity, message
        self.line: int | None = None


def _findings(
    applications: dict[str, LoadedApplication],
    kind: DraftKind,
    text: str,
    application: str | None,
    plans: PlanOpener | None,
    environment: str | None = None,
) -> list[_Finding]:
    app = applications.get(application) if application else None
    if application is not None and app is None:
        return [_Finding("Application", "error", f"no application named {application} is loaded")]
    if app is not None and environment is not None:
        _not_production(app, environment)  # named or not, plans are never read in Production
    if kind in (DraftKind.PROBE, DraftKind.ENTITY):
        if app is None:
            return [_Finding("Application", "error", "name the application this draft is for")]
        where = _plan_environment(app, environment) if plans else None
        report = (
            check_probe_draft(app, text, environment=where, plans=plans)
            if kind is DraftKind.PROBE
            else check_entity(app, text, environment=where, plans=plans)
        )
        return _from_report(report)
    try:
        document = parse_document(read_text(text), "the draft")
    except (ConfigError, yaml.YAMLError) as err:
        return [_Finding("Schema", "error", str(err))]
    wanted = {
        DraftKind.CONNECTOR: ("SqlConnector", "RestConnector"),
        DraftKind.CATALOG: ("Catalog",),
    }
    expected = wanted.get(kind, ("Runbook",))
    if type(document).__name__ not in expected:
        return [
            _Finding(
                "Kind", "error", f"the draft is a {type(document).__name__}, not a {kind.value}"
            )
        ]
    if isinstance(document, Runbook) and app is not None:
        return _runbook_findings(app, document)
    return []


def _plan_environment(app: LoadedApplication, environment: str | None) -> str:
    """Where plans are read for an assistant: the one named, else the first that isn't
    Production. Never Production: refused, also for an application with nothing else."""
    environments = app.application.environments
    if environment is None:
        environment = next((n for n, e in environments.items() if not e.production), None)
        if environment is None:
            raise RequestError(
                PLAN_CHECKS_NOT_PRODUCTION, {"environment": PLAN_CHECKS_NOT_PRODUCTION}
            )
    _not_production(app, environment)
    return environment


def _not_production(app: LoadedApplication, environment: str) -> None:
    found = app.application.environments.get(environment)
    if found is None:
        raise NotFoundError(f"{app.application.application} has no environment {environment}")
    if found.production:
        raise RequestError(PLAN_CHECKS_NOT_PRODUCTION, {"environment": PLAN_CHECKS_NOT_PRODUCTION})


def _from_report(report: SaveCheckReport) -> list[_Finding]:
    severities: dict[CheckStatus, Severity] = {
        CheckStatus.FAIL: "error",
        CheckStatus.WARN: "warning",
    }
    found = []
    for check in report.checks:
        severity = severities.get(check.status)
        if severity is None:
            continue
        where = f" ({check.location})" if check.location else ""
        found.append(_Finding(check.check, severity, f"{check.message}{where}"))
    return found


def _runbook_findings(app: LoadedApplication, runbook: Runbook) -> list[_Finding]:
    found = []
    for step in runbook.steps:
        if isinstance(step, ReadQueryStep):
            statement = read_step_statement(app, step)
            for problem in read_step_problems(app, statement) if statement else []:
                found.append(_Finding("Read query", "error", f"step {step.id}: {problem}"))
    return found


def _paste_into(
    applications: dict[str, LoadedApplication],
    folder: str,
    kind: DraftKind,
    text: str,
    application: str | None,
    name: str | None,
) -> str | None:
    """Where the draft goes: in its application's own folder, which can be named otherwise."""
    try:
        data = read_text(text)
    except (ConfigError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    key = {DraftKind.CATALOG: "catalog"}.get(kind, kind.value)
    stem = name or str(data.get(key, f"<{kind.value}>"))
    owner = application or str(data.get("application", "<application>"))
    app = applications.get(owner)
    within = FILES[kind].split("/", 1)[1].replace(f"<{kind.value}>", stem)
    if app is not None and app.folder is not None:
        return (app.folder / within).as_posix()
    return f"{folder}/{owner}/{within}"


def _summary(app: LoadedApplication) -> ApplicationConfig:
    return ApplicationConfig(
        id=app.application.application,
        title=app.application.title,
        environments=[
            EnvironmentSummary(name=name, title=env.title, production=env.production)
            for name, env in app.application.environments.items()
        ],
        entities=[
            EntitySummary(
                id=entity.entity,
                title=entity.title or entity.entity,
                root_key=entity.root_key,
                identifiers=sorted(entity.identifiers),
                pii_identifiers=sorted(n for n, i in entity.identifiers.items() if i.pii),
                locations=[
                    LocationSummary(name=n, connector=loc.using, cardinality=loc.cardinality.value)
                    for n, loc in entity.locations.items()
                ],
            )
            for entity in app.entities.values()
        ],
        probes=[
            ProbeSummary(id=p.probe, title=p.title or p.probe, kind=p.kind.value, entity=p.entity)
            for p in app.probes.values()
        ],
        connectors=[
            ConnectorSummary(id=c.connector, type=c.type, environments=sorted(c.environments))
            for c in app.connectors.values()
        ],
        runbooks=[
            RunbookSummary(id=r.runbook, title=r.title, entity=r.applies_to.entity)
            for r in app.runbooks.values()
        ],
    )


def _application_id(path: Path, kind: DraftKind) -> str:
    folder = path.parent if kind is DraftKind.CATALOG else path.parent.parent
    data = yaml.safe_load((folder / "application.yaml").read_text(encoding="utf-8"))
    return str(data["application"])
