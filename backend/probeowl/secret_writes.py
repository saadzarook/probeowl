# Author: @SaadZarook
"""The one way a password reaches this machine's keychain from the browser.

Write-only: a password goes in, nothing comes out; no route reads it back. It is saved under the
name its binding's file already gives (keyring:probeowl/<connector>.<environment>), and kept
only once its audit event commits: if the audit fails, the keychain is put back as it was. The
audit records that a password changed, never its value.
"""

import getpass

from sqlalchemy import Engine

from probeowl import secrets
from probeowl.errors import NotFoundError, RequestError, SecretExists, unknown_application
from probeowl.model.check import LoadedApplication
from probeowl.model.run import Actor, ActorKind
from probeowl.model.setup import SaveSecret
from probeowl.store.audit import AuditEvent, append_event
from probeowl.store.tables import LOCAL_WORKSPACE


class SecretWrites:
    """Saves a binding's password or token in the system keychain, audited."""

    def __init__(
        self,
        applications: dict[str, LoadedApplication],
        store: Engine,
        actor_name: str | None = None,
        workspace_id: str = LOCAL_WORKSPACE,
    ):
        self._applications = applications
        self._store = store
        self._actor = Actor(kind=ActorKind.PERSON, name=actor_name or getpass.getuser())
        self._workspace = workspace_id

    def save(self, application: str, connector: str, environment: str, body: SaveSecret) -> None:
        """Save the password for one binding; SecretExists unless replacing was asked."""
        service, username = self._entry(application, connector, environment)
        # Keychain functions are looked up at call time, so tests can stand in for the keychain.
        previous = secrets.read_keychain(service, username)
        if previous is not None and not body.replace:
            raise SecretExists(f"{service}/{username}", connector, environment)
        event = AuditEvent(
            action="secret_saved",
            actor=self._actor,
            application=application,
            environment=environment,
            details={"connector": connector, "replaced": previous is not None},
        )
        saved = False
        try:
            # The event is written first, in a transaction that commits last.
            with self._store.begin() as conn:
                append_event(conn, self._workspace, event)
                secrets.save_secret(service, username, body.password.get_secret_value())
                saved = True
        except BaseException:
            if saved:
                _put_back(service, username, previous)
            raise

    def _entry(self, application: str, connector: str, environment: str) -> tuple[str, str]:
        app = self._applications.get(application)
        if app is None:
            raise unknown_application(application, self._applications)
        definition = app.connectors.get(connector)
        if definition is None:
            raise NotFoundError(f"{application} has no connector named {connector}")
        binding = definition.environments.get(environment)
        if binding is None:
            raise NotFoundError(f"{connector} has no binding in {environment}")
        if binding.secret is None:
            raise RequestError(f"{connector} needs no password or token in {environment}")
        entry = secrets.keychain_entry(binding.secret)
        if entry is None:
            raise RequestError(
                f"{connector} reads its secret in {environment} from {binding.secret}; "
                "set it there, not in the keychain",
                {"password": "this binding does not use the keychain"},
            )
        return entry


def _put_back(service: str, username: str, previous: str | None) -> None:
    """The keychain as it was before a save that could not be audited."""
    if previous is None:
        secrets.delete_keychain(service, username)
    else:
        secrets.save_secret(service, username, previous)
