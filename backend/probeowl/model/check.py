# Author: @SaadZarook
"""Check a folder of ProbeOwl YAML: every file valid, and every reference between files resolves.

Each application lives in a folder with an application.yaml; every other file belongs to the
nearest application folder above it.
"""

from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

from probeowl.engine.identifiers import resolution_lookup
from probeowl.engine.runner import check_probe
from probeowl.errors import ConfigError
from probeowl.guards.dialects import RULES_BY_CONNECTOR_TYPE
from probeowl.model.application import Application
from probeowl.model.catalog import Catalog
from probeowl.model.common import split_field_ref
from probeowl.model.connector import RestConnector, SqlConnector
from probeowl.model.entity import Entity, LocationBase
from probeowl.model.load import Document, load_file
from probeowl.model.probe import Probe
from probeowl.model.runbook import ProbeStep, ReadQueryStep, Runbook, WriteQueryStep

APPLICATION_FILE = "application.yaml"


@dataclass(frozen=True)
class Finding:
    """One problem in one file."""

    path: Path
    message: str
    # A probe enabled where one of its connectors has no binding: what owl connector add fixes.
    unbound: bool = False

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


@dataclass
class CheckReport:
    """What `owl check` found."""

    files: int = 0
    applications: int = 0
    findings: list[Finding] = field(default_factory=list)

    def add(self, path: Path, message: str, unbound: bool = False) -> None:
        """Record one finding."""
        self.findings.append(Finding(path, message, unbound))


@dataclass
class _App:
    application: Application
    connectors: set[str] = field(default_factory=set)
    entities: set[str] = field(default_factory=set)
    probes: set[str] = field(default_factory=set)
    documents: list[tuple[Path, Document]] = field(default_factory=list)
    connector_types: dict[str, str] = field(default_factory=dict)
    entity_documents: dict[str, Entity] = field(default_factory=dict)
    bound_in: dict[str, set[str]] = field(default_factory=dict)  # connector -> environments


@dataclass(frozen=True)
class LoadedApplication:
    """One application's checked configuration, by name."""

    application: Application
    connectors: dict[str, SqlConnector | RestConnector]
    entities: dict[str, Entity]
    probes: dict[str, Probe]
    catalog: Catalog | None = None
    folder: Path | None = None  # where application.yaml is
    catalog_path: Path | None = None  # the file the catalog was read from
    probe_paths: dict[str, Path] = field(default_factory=dict)  # the file each probe came from
    runbooks: dict[str, Runbook] = field(default_factory=dict)
    entity_paths: dict[str, Path] = field(default_factory=dict)  # the file each entity came from


def check_folder(root: Path) -> CheckReport:
    """Check every YAML file under root."""
    return _check(root)[0]


def load_applications(root: Path, allow_unbound: bool = False) -> dict[str, LoadedApplication]:
    """Load every application under root; ConfigError if its files or references are wrong.

    allow_unbound: accept probes enabled where a connector has no binding yet, for the command
    that adds the binding. owl serve and owl check never accept them.
    """
    report, apps = _check(root)
    findings = [f for f in report.findings if not (allow_unbound and f.unbound)]
    if findings:
        first = findings[0]
        raise ConfigError(
            f"{root} has {len(findings)} errors, the first: {first}; run: owl check {root}"
        )
    loaded = {}
    for folder, app in apps.items():
        documents = [document for _, document in app.documents]
        catalog_path = next((p for p, d in app.documents if isinstance(d, Catalog)), None)
        loaded[app.application.application] = LoadedApplication(
            application=app.application,
            connectors={
                d.connector: d for d in documents if isinstance(d, SqlConnector | RestConnector)
            },
            entities={d.entity: d for d in documents if isinstance(d, Entity)},
            runbooks={d.runbook: d for d in documents if isinstance(d, Runbook)},
            probes={d.probe: d for d in documents if isinstance(d, Probe)},
            catalog=next((d for d in documents if isinstance(d, Catalog)), None),
            folder=folder,
            catalog_path=catalog_path,
            probe_paths={d.probe: p for p, d in app.documents if isinstance(d, Probe)},
            entity_paths={d.entity: p for p, d in app.documents if isinstance(d, Entity)},
        )
    return loaded


def _check(root: Path) -> tuple[CheckReport, dict[Path, _App]]:
    if not root.is_dir():
        raise ConfigError(f"{root} is not a folder")
    report = CheckReport()
    loaded: dict[Path, list[Document]] = {}
    for path in sorted([*root.rglob("*.yaml"), *root.rglob("*.yml")]):
        report.files += 1
        try:
            loaded[path] = load_file(path)
        except ConfigError as err:
            report.add(path, str(err).removeprefix(f"{path}: "))

    apps = _find_applications(loaded, report)
    report.applications = len(apps)
    for path, documents in loaded.items():
        if path.name == APPLICATION_FILE:
            continue
        app = next((apps[f] for f in path.parents if f in apps), None)
        if app is None:
            report.add(path, f"not inside an application folder (no {APPLICATION_FILE} above it)")
            continue
        for document in documents:
            _register(app, path, document, report)
    for app in apps.values():
        for path, document in app.documents:
            _check_references(app, path, document, report)
    return report, apps


def _find_applications(loaded: dict[Path, list[Document]], report: CheckReport) -> dict[Path, _App]:
    apps = {}
    for path, documents in loaded.items():
        if path.name != APPLICATION_FILE:
            continue
        if len(documents) == 1 and isinstance(documents[0], Application):
            apps[path.parent] = _App(documents[0])
        else:
            report.add(path, "must hold exactly one application document")
    return apps


def _register(app: _App, path: Path, document: Document, report: CheckReport) -> None:
    app.documents.append((path, document))
    if isinstance(document, Application):
        report.add(path, f"an application belongs in its own {APPLICATION_FILE}")
        return
    if isinstance(document, SqlConnector | RestConnector):
        names, name = app.connectors, document.connector
        app.connector_types[name] = document.type
        app.bound_in[name] = set(document.environments)
    elif isinstance(document, Entity):
        names, name = app.entities, document.entity
        app.entity_documents[name] = document
    elif isinstance(document, Probe):
        names, name = app.probes, document.probe
    else:
        return
    if name in names:
        report.add(path, f"{name} is defined twice in this application")
    names.add(name)


def _check_identifiers(app: _App, path: Path, entity: Entity, report: CheckReport) -> None:
    # Each identifier with via must be found by rewriting its location's lookup; a run
    # would otherwise be refused only when someone enters that kind of value.
    for name, identifier in entity.identifiers.items():
        if identifier.via is None:
            continue
        location = entity.locations[split_field_ref(identifier.via)[0]]
        connector_type = app.connector_types.get(location.using)
        if connector_type is None:
            continue  # reported as an undefined connector
        rules = RULES_BY_CONNECTOR_TYPE.get(connector_type)
        if rules is None:
            report.add(path, f"identifier {name} via {identifier.via}: use a SQL location")
            continue
        try:
            resolution_lookup(entity, name, rules.sqlglot_dialect)
        except ConfigError as err:
            report.add(path, str(err))


def _check_references(app: _App, path: Path, document: Document, report: CheckReport) -> None:
    def need(names: Collection[str], name: str, kind: str, used_by: str) -> None:
        if name not in names:
            report.add(path, f"{used_by} uses {kind} {name}, which is not defined")

    environments = app.application.environments
    application = app.application.application
    if isinstance(document, SqlConnector | RestConnector):
        for name in document.environments:
            if name not in environments:
                report.add(path, f"environment {name} is not in {APPLICATION_FILE}")
    if isinstance(document, SqlConnector):
        for name, binding in document.environments.items():
            if name not in environments or not environments[name].production:
                continue
            if not binding.replica:
                report.add(
                    path, f"the {name} binding is Production and must use a replica (replica: true)"
                )
            # Without encryption, or trusting any certificate, anyone on the network path can
            # read production data and the password.
            if not binding.encrypt and not binding.encrypt_reason:
                report.add(
                    path,
                    f"the {name} binding is Production and is not encrypted; "
                    "remove encrypt: false or say why in encrypt_reason",
                )
            if binding.trust_server_certificate and not binding.trust_server_certificate_reason:
                report.add(
                    path,
                    f"the {name} binding is Production and trusts any server certificate; "
                    "remove trust_server_certificate or say why in trust_server_certificate_reason",
                )
    elif isinstance(document, Entity):
        for name, location in document.locations.items():
            need(app.connectors, location.using, "connector", f"location {name}")
            _check_masking(app, path, f"location {name}", location, report)
            for child, fan_out in location.each.items():
                need(app.connectors, fan_out.using, "connector", f"location {name}.{child}")
                _check_masking(app, path, f"location {name}.{child}", fan_out, report)
        if document.healthy_sample is not None:
            need(app.connectors, document.healthy_sample.using, "connector", "healthy_sample")
        _check_identifiers(app, path, document, report)
    elif isinstance(document, Probe):
        need(app.entities, document.entity, "entity", "the probe")
        entity = app.entity_documents.get(document.entity)
        if entity is not None:
            try:
                check_probe(document, entity)
            except ConfigError as err:
                report.add(path, str(err))
            _check_probe_environments(app, path, document, entity, report)
    elif isinstance(document, Catalog):
        if document.catalog != application:
            report.add(path, f"catalog {document.catalog} is in application {application}")
        for table in document.tables:
            need(app.connectors, table.connector, "connector", f"table {table.table}")
    elif isinstance(document, Runbook):
        if document.application != application:
            report.add(path, f"runbook names application {document.application}, not {application}")
        need(app.entities, document.applies_to.entity, "entity", "applies_to")
        for step in document.steps:
            if isinstance(step, ReadQueryStep | WriteQueryStep):
                need(app.connectors, step.using, "connector", f"step {step.title!r}")
                if app.connector_types.get(step.using) == "rest":
                    report.add(path, f"step {step.title!r} has SQL: use a SQL connector")
            elif isinstance(step, ProbeStep):
                need(app.probes, step.probe, "probe", f"step {step.title!r}")


def _check_masking(
    app: _App, path: Path, used_by: str, location: LocationBase, report: CheckReport
) -> None:
    # pii names SQL columns and mask names REST fields; the wrong one would mask nothing.
    kind = app.connector_types.get(location.using)
    if kind == "rest" and location.pii:
        report.add(path, f"{used_by} is a REST location: list its fields in mask, not pii")
    if kind is not None and kind != "rest" and location.mask:
        report.add(path, f"{used_by} is a SQL location: list its columns in pii, not mask")
    if kind is not None and kind != "rest" and location.compare:
        report.add(
            path, f"{used_by} is a SQL location: mark its compare columns in the table catalog"
        )


def _check_probe_environments(
    app: _App, path: Path, probe: Probe, entity: Entity, report: CheckReport
) -> None:
    # A run is refused where a location has no binding, so say so before anyone tries.
    known = app.application.environments
    for name in probe.environments or []:
        if name not in known:
            report.add(path, f"probe {probe.probe} is enabled in {name}, not an environment here")
    locations = [(name, location.using) for name, location in entity.locations.items()] + [
        (f"{name}.{child}", fan_out.using)
        for name, location in entity.locations.items()
        for child, fan_out in location.each.items()
    ]
    for environment in probe.environments or list(known):
        if environment not in known:
            continue
        for location, connector in locations:
            if connector in app.bound_in and environment not in app.bound_in[connector]:
                report.add(
                    path,
                    f"probe {probe.probe} is enabled in {environment}, where location {location} "
                    f"has no binding ({connector}); bind it or leave {environment} out of the "
                    "probe's environments",
                    unbound=True,
                )
