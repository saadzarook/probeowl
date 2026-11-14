# Author: @SaadZarook
"""First run and Add connection from the browser: a new application, a new connector or binding,
and Test connection on a binding not saved yet.

Writes follow the rules of every other save (config_folder.py): a preview first, then exactly
that change, kept only once its audit event commits, and loaded at once through the reloader,
which waits for no run to be running. Nothing here takes, stores or returns a password, except
the draft test, which uses the one it is given for that connection only.
"""

import getpass
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from pydantic.types import JsonValue
from sqlalchemy import Engine

from probeowl.attempts import AttemptLimit
from probeowl.config_folder import Change, ConfigFolder
from probeowl.connector_files import (
    DEFAULT_ROW_CEILING,
    file_of,
    keychain_reference,
    new_connector_text,
    with_bindings,
)
from probeowl.connector_status import ConnectorStatuses
from probeowl.errors import (
    ConfigError,
    ConflictError,
    NotFoundError,
    RequestError,
    unknown_application,
)
from probeowl.model.application import Application
from probeowl.model.check import LoadedApplication, check_folder
from probeowl.model.common import ProbeOwlModel
from probeowl.model.connector import RestConnector, SqlConnector
from probeowl.model.connector_status import ConnectionTest
from probeowl.model.run import Actor, ActorKind
from probeowl.model.setup import (
    DraftConnection,
    KeychainInfo,
    RestBindingDraft,
    SaveApplication,
    SaveConnector,
    SetupState,
    SqlBindingDraft,
    StoreInfo,
)
from probeowl.reload import ConfigReloader
from probeowl.secrets import check_keychain, resolve_secret
from probeowl.store.audit import AuditEvent, append_event
from probeowl.store.tables import LOCAL_WORKSPACE

APPLICATION_HEADER = "# Written by ProbeOwl's setup. Edit freely; `owl check <folder>` checks it.\n"
CONNECTOR_HEADER = "# Written by Add connection. Edit freely; `owl check <folder>` checks it.\n"
NOT_A_REPLICA = (
    "ProbeOwl reads Production only from a read replica, readable secondary or Active Data "
    "Guard standby, never from the primary, so a probe can never slow or block it. Ask your "
    "DBA for one and point this binding at it"
)
_DIALECTS = {"mysql": "mariadb"}  # SQLAlchemy names MariaDB's dialect mysql


class SetupWrites:
    """Writes a new application or connector into the folder owl serve loaded, and tests drafts."""

    def __init__(
        self,
        applications: dict[str, LoadedApplication],
        store: Engine,
        folder: ConfigFolder,
        reloader: ConfigReloader,
        statuses: ConnectorStatuses,
        store_created: bool = False,
        actor_name: str | None = None,
        workspace_id: str = LOCAL_WORKSPACE,
        keychain: Callable[[], None] = check_keychain,
        attempts: AttemptLimit | None = None,
    ):
        self._applications = applications
        self._store = store
        self._folder = folder
        self._reloader = reloader
        self._statuses = statuses
        self._store_created = store_created
        self._actor = Actor(kind=ActorKind.PERSON, name=actor_name or getpass.getuser())
        self._workspace = workspace_id
        self._keychain = keychain
        self._attempts = attempts or AttemptLimit()

    def state(self) -> SetupState:
        """How many applications are loaded, where new ones go, the store and the keychain."""
        backend = self._store.url.get_backend_name()
        try:
            self._keychain()
            keychain = KeychainInfo(available=True)
        except ConfigError as err:
            keychain = KeychainInfo(available=False, reason=str(err))
        return SetupState(
            applications=len(self._applications),
            config_folder=str(self._folder.root),
            store=StoreInfo(
                dialect=_DIALECTS.get(backend, backend),
                created_now=self._store_created,
            ),
            keychain=keychain,
        )

    def save_application(self, name: str, body: SaveApplication) -> Change:
        """A new application's application.yaml: without base, previewed; with it, written."""
        if name in self._applications:
            raise ConflictError(f"an application named {name} is already loaded")
        document: dict[str, Any] = {
            "application": name,
            "title": body.title,
            "owner": body.owner,
            "environments": {
                e.name: {"title": e.title, "production": e.production} for e in body.environments
            },
        }
        _valid(Application, document)
        relative = f"{name}/application.yaml"
        if self._folder.read(relative) is not None:
            raise ConflictError(
                f"{relative} exists but is not loaded; reload the configuration to see it"
            )
        text = APPLICATION_HEADER + yaml.safe_dump(document, sort_keys=False, allow_unicode=True)
        details: dict[str, JsonValue] = {
            "environments": [e.name for e in body.environments],
            "file": relative,
            "via": "setup",
        }
        return self._preview_or_write(
            relative, text, body.base, "application_created", name, details
        )

    def save_connector(self, application: str, connector: str, body: SaveConnector) -> Change:
        """A new connector bound in one environment, or a binding for one not bound yet."""
        app = self._loaded(application, body.environment)
        production = app.application.environments[body.environment].production
        binding = _binding(connector, body.type, body.environment, body.binding, production)
        existing = app.connectors.get(connector)
        if existing is None:
            assert app.folder is not None  # _loaded checked it
            relative = self._folder.relative(app.folder / "connectors" / f"{connector}.yaml")
            if self._folder.read(relative) is not None:
                raise ConflictError(f"{relative} already exists; choose another connector id")
            document: dict[str, Any] = {
                "connector": connector,
                "type": body.type,
                "owner": body.owner or app.application.owner,
            }
            if body.type != "rest":
                document["guards"] = {"row_ceiling": DEFAULT_ROW_CEILING}
            document["environments"] = {body.environment: binding}
            _valid(RestConnector if body.type == "rest" else SqlConnector, document)
            text = new_connector_text(CONNECTOR_HEADER, document)
        else:
            if existing.type != body.type:
                raise RequestError(
                    f"{connector} is a {existing.type} connector, not {body.type}",
                    {"type": existing.type},
                )
            if body.environment in existing.environments:
                raise ConflictError(
                    f"{connector} is already bound in {body.environment}; edit its file to "
                    "change the binding"
                )
            relative = self._folder.relative(file_of(app, connector))
            before = self._folder.read(relative)
            if before is None:
                raise ConflictError(f"{relative} no longer exists; reload the configuration")
            text = with_bindings(before, {body.environment: binding})
        details: dict[str, JsonValue] = {
            "connector": connector,
            "environments": [body.environment],
            "new": existing is None,
            "file": Path(relative).name,
            "via": "add_connection",
        }
        return self._preview_or_write(
            relative, text, body.base, "connector_added", application, details, body.environment
        )

    def test_draft(self, application: str, body: DraftConnection) -> ConnectionTest:
        """Connect once to a binding not saved yet, with the password given or the saved one."""
        app = self._loaded(application, body.environment)
        if body.binding is None:
            return self._test_new_password(application, app, body)
        production = app.application.environments[body.environment].production
        binding = _binding(body.connector, body.type, body.environment, body.binding, production)
        document = {
            "connector": body.connector,
            "type": body.type,
            "environments": {body.environment: binding},
        }
        model: SqlConnector | RestConnector = (
            _valid(RestConnector, document)
            if body.type == "rest"
            else _valid(SqlConnector, document)
        )
        if "secret" not in binding:
            secrets: Callable[[str], str] = _no_secret
        elif body.password is not None:
            secrets = _given(body.password.get_secret_value())
        elif body.use_saved_password:
            secrets = resolve_secret
        else:
            raise RequestError(
                "give the password, or use the one saved on this machine",
                {"password": "required unless use_saved_password is true"},
            )
        if body.password is not None:
            # A typed password, so limited: the saved one is the same each time and isn't (#61).
            self._attempts.attempt((application, body.connector, body.environment))
        return self._statuses.test_draft(application, model, body.environment, secrets)

    def _test_new_password(
        self, application: str, app: LoadedApplication, body: DraftConnection
    ) -> ConnectionTest:
        """A saved binding, as saved, with a password not saved yet: Change password's test."""
        existing = app.connectors.get(body.connector)
        if existing is None or body.environment not in existing.environments:
            raise NotFoundError(
                f"{application} has no connector {body.connector} bound in {body.environment}"
            )
        if existing.type != body.type:
            raise RequestError(
                f"{body.connector} is a {existing.type} connector, not {body.type}",
                {"type": existing.type},
            )
        if existing.environments[body.environment].secret is None:
            raise RequestError(
                f"{body.connector} has no password in {body.environment}",
                {"password": "this binding has none to change"},
            )
        if body.password is None:
            raise RequestError(
                "give the new password to test",
                {"password": "required when no binding is given"},
            )
        secrets = _given(body.password.get_secret_value())
        self._attempts.attempt((application, body.connector, body.environment))
        return self._statuses.test_draft(application, existing, body.environment, secrets)

    def _loaded(self, application: str, environment: str) -> LoadedApplication:
        app = self._applications.get(application)
        if app is None:
            raise unknown_application(application, self._applications)
        if environment not in app.application.environments:
            raise NotFoundError(f"{application} has no environment {environment}")
        if app.folder is None:
            raise RequestError("start owl serve with --config <folder> to add connections")
        return app

    def _preview_or_write(
        self,
        relative: str,
        text: str,
        base: str | None,
        action: str,
        application: str,
        details: dict[str, JsonValue],
        environment: str | None = None,
    ) -> Change:
        preview = self._folder.preview(relative, text)
        if base is None:
            return preview
        if base != preview.base:
            raise ConflictError(f"{relative} changed since its preview; preview the change again")
        written: list[Change] = []

        def verify() -> None:
            self._still_loads()
            # Last: if the audit cannot be written, the file is put back and the save refused.
            event = AuditEvent(
                action=action,
                actor=self._actor,
                application=application,
                environment=environment,
                details=details | {"base": preview.base, "after": preview.after},
            )
            with self._store.begin() as conn:
                append_event(conn, self._workspace, event)

        def write() -> None:
            written.extend(self._folder.write_all([(relative, text, preview.base)], verify))

        # Loaded at once, so the next screen finds it; refused while a run is running.
        self._reloader.reload(write)
        return written[0]

    def _still_loads(self) -> None:
        findings = check_folder(self._folder.root).findings
        if findings:
            problems = {self._folder.relative(f.path): f.message for f in findings}
            file, problem = next(iter(problems.items()))
            raise RequestError(f"saving would break the folder: {file}: {problem}", problems)


def _binding(
    connector: str,
    kind: str,
    environment: str,
    draft: SqlBindingDraft | RestBindingDraft,
    production: bool,
) -> dict[str, Any]:
    """The binding as its YAML says it, with the keychain reference the server chooses."""
    if kind == "rest":
        if not isinstance(draft, RestBindingDraft):
            raise RequestError("a rest connector needs base_url and needs_token", {"binding": kind})
        rest: dict[str, Any] = {"base_url": draft.base_url}
        if draft.needs_token:
            rest["secret"] = keychain_reference(connector, environment)
        return rest
    if not isinstance(draft, SqlBindingDraft):
        raise RequestError(
            f"a {kind} connector needs host, port, database and user", {"binding": kind}
        )
    if production and not draft.replica:
        raise RequestError(NOT_A_REPLICA, {"binding.replica": "required in Production"})
    sql: dict[str, Any] = {"host": draft.host, "port": draft.port, "database": draft.database}
    if draft.db_schema is not None:
        sql["schema"] = draft.db_schema
    sql["user"] = draft.user
    sql["secret"] = keychain_reference(connector, environment)
    if draft.replica:
        sql["replica"] = True
    if not draft.encrypt:
        sql["encrypt"] = False
        if draft.encrypt_reason is None:
            raise RequestError(
                "say why this connection is not encrypted",
                {"binding.encrypt_reason": "required when encrypt is false"},
            )
        sql["encrypt_reason"] = draft.encrypt_reason
    if draft.trust_server_certificate:
        sql["trust_server_certificate"] = True
        if draft.trust_server_certificate_reason is None:
            raise RequestError(
                "say why this binding trusts the server's certificate without checking it",
                {"binding.trust_server_certificate_reason": "required when trusting it"},
            )
        sql["trust_server_certificate_reason"] = draft.trust_server_certificate_reason
    return sql


def _valid[M: ProbeOwlModel](model: type[M], document: dict[str, Any]) -> M:
    """The document as its model, or RequestError with the model's own messages."""
    try:
        return model.model_validate(document)
    except ValidationError as err:
        # Messages only, never the input: a value given here is never echoed back.
        fields = {".".join(str(p) for p in e["loc"]): e["msg"] for e in err.errors()}
        first = next(iter(fields.items()))
        raise RequestError(f"{first[0]}: {first[1]}", fields) from None


def _given(password: str) -> Callable[[str], str]:
    """The password typed for a draft, whatever its binding's reference names."""

    def secret(_reference: str) -> str:
        return password

    return secret


def _no_secret(reference: str) -> str:
    raise ConfigError(f"no secret is needed here, but {reference} was asked for")
