# Author: @SaadZarook
"""Save a probe from the editor into its YAML file, under the rules for writing from the web.

The rules are in config_folder.py. Here: a probe that fails a save check is never written,
plans included; an entity edited with it is written with it or not at all; and each write is
audited with its actor.
"""

import getpass
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import JsonValue
from sqlalchemy import Engine

from probeowl.checks.plans import PlanOpener, open_for_plans
from probeowl.checks.rules import parse_draft, parse_entity
from probeowl.checks.service import check_draft, check_entity, refuse_failures
from probeowl.checks.time_offsets import imported_types
from probeowl.config_folder import Change, ConfigFolder
from probeowl.errors import ConflictError, NotFoundError, RequestError, unknown_application
from probeowl.guards.kill_switch import KillSwitches
from probeowl.model.check import LoadedApplication, check_folder
from probeowl.model.entity import Entity
from probeowl.model.load import read_yaml
from probeowl.model.probe import Probe
from probeowl.model.run import Actor, ActorKind
from probeowl.model.save_check import SaveCheckReport
from probeowl.store.audit import AuditEvent, append_event
from probeowl.store.tables import LOCAL_WORKSPACE

PROBES_FOLDER = "probes"  # where a new probe goes, in its application's folder
ENTITIES_FOLDER = "entities"  # and a new entity


@dataclass(frozen=True)
class SavedProbe:
    """A probe's change to its file, its entity's if one was sent, and the checks they passed."""

    change: Change
    report: SaveCheckReport
    base: str  # what confirms this change: the file's hash, or one covering both files
    entity_change: Change | None = None


@dataclass(frozen=True)
class SavedEntity:
    """A new entity's change to its file, and the checks it passed."""

    change: Change
    report: SaveCheckReport
    base: str


@dataclass(frozen=True)
class _Written:
    """What a preview or a write produced; unchecked is None when nothing was written."""

    changes: list[Change]
    token: str
    unchecked: list[str] | None  # once written: the plan checks it was saved without


@dataclass(frozen=True)
class YamlFile:
    """One document's file, exactly as it is on disk: what the editor opens and saves back."""

    path: str
    text: str


class ProbeWrites:
    """Saves probes for the applications owl serve loaded, and reads back what it would replace."""

    def __init__(
        self,
        applications: dict[str, LoadedApplication],
        store: Engine,
        folder: ConfigFolder,
        plans: PlanOpener = open_for_plans,
        actor_name: str | None = None,
        workspace_id: str = LOCAL_WORKSPACE,
    ):
        self._applications = applications
        self._store = store
        self._folder = folder
        self._plans = plans
        self._actor = Actor(kind=ActorKind.PERSON, name=actor_name or getpass.getuser())
        self._workspace = workspace_id

    def probe_file(self, application: str, name: str) -> YamlFile:
        """The probe's file, comments and all, or NotFoundError."""
        return self._read_file(application, "probe", name, lambda app: app.probe_paths)

    def entity_file(self, application: str, name: str) -> YamlFile:
        """The entity's file, comments and all, or NotFoundError."""
        return self._read_file(application, "entity", name, lambda app: app.entity_paths)

    def _read_file(
        self,
        application: str,
        kind: str,
        name: str,
        paths: Callable[[LoadedApplication], dict[str, Path]],
    ) -> YamlFile:
        app = self._applications.get(application)
        if app is None:
            raise unknown_application(application, self._applications)
        known = paths(app).get(name)
        if known is None:
            raise NotFoundError(f"{application} has no {kind} named {name}")
        relative = self._folder.relative(known)
        text = self._folder.read(relative)
        if text is None:
            raise NotFoundError(
                f"{relative} no longer exists; reload the configuration (owl reload) "
                "to see the folder as it is"
            )
        # Saving refuses such a file, so opening it says so at once.
        if len(read_yaml(self._folder.resolve(relative))) != 1:
            raise RequestError(
                f"{relative} holds more than {kind} {name}; move the {kind} to its own file"
            )
        return YamlFile(relative, text)

    def save(
        self,
        application: str,
        name: str,
        text: str,
        environment: str,
        base: str | None,
        entity_text: str | None = None,
        without_plan_check: bool = False,
    ) -> SavedProbe:
        """Without base, the change saving would make; with its preview's base, written.

        The checks run both times: what passed at the preview may not pass at the write.
        """
        app = self._saving_into(application, environment)
        # Reads plans from databases, so outside the lock: the base protects what follows.
        plans = KillSwitches(self._store, self._workspace).guarded(application, self._plans)
        columns = imported_types(self._store, self._workspace, application)
        report = check_draft(app, text, entity_text, environment, plans, columns)
        refuse_failures(report)
        probe = parse_draft(text)
        if probe.probe != name:
            raise RequestError(
                f"the YAML is for probe {probe.probe}, not {name}",
                {"probe": f"name it {name}; a probe cannot be renamed by saving"},
            )
        entity = parse_entity(entity_text) if entity_text else None
        with self._folder.lock:
            files = [self._file(app, "probe", probe.probe, PROBES_FOLDER, app.probe_paths, text)]
            if entity is not None and entity_text:
                files.append(
                    self._file(
                        app, "entity", entity.entity, ENTITIES_FOLDER, app.entity_paths, entity_text
                    )
                )
            written = self._preview_or_write(
                app,
                environment,
                files,
                report,
                base,
                without_plan_check,
                lambda changes, unchecked: self._audit(
                    application, environment, probe, entity, changes, app, unchecked
                ),
            )
            if written.unchecked is None:
                return _saved(written.changes, report, written.token)
            self._remember(application, probe, entity, files)
        return _saved(written.changes, report, written.token)

    def save_entity(
        self,
        application: str,
        name: str,
        text: str,
        environment: str,
        base: str | None,
        without_plan_check: bool = False,
    ) -> SavedEntity:
        """A new entity, saved on its own: without base, the change; with its preview's, written.

        Under the same rules as a probe's save. An entity that exists is changed with one of its
        probes instead, so that its probes are checked with it.
        """
        app = self._saving_into(application, environment)
        plans = KillSwitches(self._store, self._workspace).guarded(application, self._plans)
        columns = imported_types(self._store, self._workspace, application)
        report = check_entity(app, text, environment, plans, columns)
        refuse_failures(report)
        entity = parse_entity(text)
        if entity.entity != name:
            raise RequestError(
                f"the YAML is for entity {entity.entity}, not {name}",
                {"entity": f"name it {name}; an entity cannot be renamed by saving"},
            )
        if name in app.entities:
            raise RequestError(
                f"{application} already has an entity {name}: change it with one of its probes "
                "in the Probe editor",
                {"entity": "choose a new name"},
            )

        def audit(changes: list[Change], unchecked: list[str]) -> None:
            change = changes[0]
            details: dict[str, JsonValue] = {
                "path": change.path,
                "entity": name,
                "base": change.base,
                "after": change.after,
            }
            if unchecked:
                details |= {"saved_without_plan_check": True, "plans_not_run": list(unchecked)}
            self._append("entity_created", application, environment, details)

        with self._folder.lock:
            files = [self._file(app, "entity", name, ENTITIES_FOLDER, app.entity_paths, text)]
            written = self._preview_or_write(
                app, environment, files, report, base, without_plan_check, audit
            )
            if written.unchecked is None:
                return SavedEntity(written.changes[0], report, written.token)
            current = self._applications[application]
            self._applications[application] = replace(
                current,
                entities={**current.entities, name: entity},
                entity_paths={**current.entity_paths, name: self._folder.resolve(files[0][0])},
            )
        return SavedEntity(written.changes[0], report, written.token)

    def _saving_into(self, application: str, environment: str) -> LoadedApplication:
        app = self._applications.get(application)
        if app is None:
            raise unknown_application(application, self._applications)
        if environment not in app.application.environments:
            raise NotFoundError(f"{application} has no environment {environment}")
        if app.folder is None:
            raise RequestError("start owl serve with --config <folder> to save probes")
        return app

    def _preview_or_write(
        self,
        app: LoadedApplication,
        environment: str,
        files: list[tuple[str, str]],
        report: SaveCheckReport,
        base: str | None,
        without_plan_check: bool,
        audit: Callable[[list[Change], list[str]], None],
    ) -> "_Written":
        """The previews; or, with their base, the files written and audited. Holding the lock.

        audit: records the write; the files are kept only once it has committed (#273).
        """
        previews = [self._folder.preview(relative, content) for relative, content in files]
        token = _token(previews)
        if base is None or (base == token and not any(p.diff for p in previews)):
            return _Written(previews, token, None)  # nothing to write: no write to audit
        if base != token:
            changed = " and ".join(p.path for p in previews)
            raise ConflictError(f"{changed} changed since its preview; preview the change again")
        production = app.application.environments[environment].production
        unchecked = _plans_not_run(report) if production else []
        if unchecked and not without_plan_check:
            message = (
                f"plan checks could not run in {environment} ({unchecked[0]}); "
                "to save without plan check, confirm it: that is recorded in the audit"
            )
            raise RequestError(message, {"save_without_plan_check": message})

        def verify() -> None:
            self._still_loads()
            # Last: if the audit cannot be written, the files are put back and the save refused.
            audit(previews, unchecked)

        changes = self._folder.write_all(
            [(r, c, p.base) for (r, c), p in zip(files, previews, strict=True)], verify
        )
        return _Written(changes, token, unchecked)

    def _file(
        self,
        app: LoadedApplication,
        kind: str,
        name: str,
        folder: str,
        known: dict[str, Path],
        text: str,
    ) -> tuple[str, str]:
        assert app.folder is not None  # checked by save
        relative = self._folder.relative(known.get(name) or app.folder / folder / f"{name}.yaml")
        target = self._folder.resolve(relative)
        if target.exists():
            # The file's other documents, or a file of the same name that is something else,
            # would be lost by writing over it.
            documents = read_yaml(target)
            if name not in known or len(documents) != 1:
                raise RequestError(
                    f"{relative} holds more than {kind} {name}; move the {kind} to its own file"
                )
        return relative, text if text.endswith("\n") else f"{text}\n"

    def _still_loads(self) -> None:
        # The save checks look at this probe. This looks at everything, so a save can never leave
        # a folder that owl serve refuses to load at its next start.
        findings = check_folder(self._folder.root).findings
        if findings:
            # In owl check's words, so the two can be read side by side.
            problems = {self._folder.relative(f.path): f.message for f in findings}
            file, problem = next(iter(problems.items()))
            raise RequestError(f"saving would break the folder: {file}: {problem}", problems)

    def _remember(
        self,
        application: str,
        probe: Probe,
        entity: Entity | None,
        files: list[tuple[str, str]],
    ) -> None:
        current = self._applications[application]
        probe_path = self._folder.resolve(files[0][0])
        updated = replace(
            current,
            probes={**current.probes, probe.probe: probe},
            probe_paths={**current.probe_paths, probe.probe: probe_path},
        )
        if entity is not None:
            updated = replace(
                updated,
                entities={**current.entities, entity.entity: entity},
                entity_paths={
                    **current.entity_paths,
                    entity.entity: self._folder.resolve(files[1][0]),
                },
            )
        self._applications[application] = updated

    def _audit(
        self,
        application: str,
        environment: str,
        probe: Probe,
        entity: Entity | None,
        changes: list[Change],
        before: LoadedApplication,
        unchecked: list[str],
    ) -> None:
        change = changes[0]
        details: dict[str, JsonValue] = {
            "path": change.path,
            "probe": probe.probe,
            "created": probe.probe not in before.probe_paths,
            "base": change.base,
            "after": change.after,
        }
        if entity is not None:
            details |= {
                "entity": entity.entity,
                "entity_path": changes[1].path,
                "entity_created": entity.entity not in before.entity_paths,
                "entity_base": changes[1].base,
                "entity_after": changes[1].after,
            }
        if unchecked:
            details |= {"saved_without_plan_check": True, "plans_not_run": list(unchecked)}
        self._append("probe_saved", application, environment, details)

    def _append(
        self, action: str, application: str, environment: str, details: dict[str, JsonValue]
    ) -> None:
        event = AuditEvent(
            action=action,
            actor=self._actor,
            application=application,
            environment=environment,
            details=details,
        )
        with self._store.begin() as conn:
            append_event(conn, self._workspace, event)


def _token(changes: list[Change]) -> str:
    # One file: its hash, as for any other write. Two: one value that fails if either file, or
    # either change, is not what was previewed.
    if len(changes) == 1:
        return changes[0].base
    parts = [part for change in changes for part in (change.path, change.base, change.after)]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _plans_not_run(report: SaveCheckReport) -> list[str]:
    return [c.message for c in report.checks if c.plan_not_run]


def _saved(changes: list[Change], report: SaveCheckReport, token: str) -> SavedProbe:
    return SavedProbe(changes[0], report, token, changes[1] if len(changes) > 1 else None)
