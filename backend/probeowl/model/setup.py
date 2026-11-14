# Author: @SaadZarook
"""First run and Add connection: what the setup screens send, and what first run is told."""

from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator

from probeowl.model.common import Name, ProbeOwlModel


class StoreInfo(ProbeOwlModel):
    """ProbeOwl's own store, as first run reports it: never its URL or login."""

    dialect: Literal["sqlite", "postgresql", "mssql", "oracle", "mariadb"]
    created_now: bool = Field(description="owl serve created it when it started.")


class KeychainInfo(ProbeOwlModel):
    """Whether this machine has a system keychain to keep passwords in."""

    available: bool
    reason: str | None = Field(default=None, description="Why not, when it is not available.")


class SetupState(ProbeOwlModel):
    """What first run needs to know before it asks anything."""

    applications: int = Field(description="Applications loaded; none opens the first-run setup.")
    config_folder: str = Field(description="Where new applications are written.")
    store: StoreInfo
    keychain: KeychainInfo


class EnvironmentDraft(ProbeOwlModel):
    """One environment of a new application."""

    name: Name
    title: str = Field(min_length=1, max_length=80)
    production: bool = False


class SaveApplication(ProbeOwlModel):
    """A new application. Without base: preview its file. With it: write exactly that."""

    title: str = Field(min_length=1, max_length=80)
    owner: str = Field(min_length=1, max_length=80, description="The team responsible for it.")
    environments: list[EnvironmentDraft] = Field(min_length=1, max_length=6)
    base: str | None = Field(
        default=None,
        description=(
            "The base of the preview being confirmed. Omitted: nothing is written and the "
            "change is returned. Given: written only if the file is still as it was then."
        ),
    )

    @model_validator(mode="after")
    def _environments_once(self) -> Self:
        names = [e.name for e in self.environments]
        repeated = sorted({n for n in names if names.count(n) > 1})
        if repeated:
            raise ValueError(f"each environment once: {', '.join(repeated)} is given twice")
        return self


class SqlBindingDraft(ProbeOwlModel):
    """Where a SQL connector points in one environment, as the Add connection form asks it.

    Never a secret or its reference: the server names the keychain entry itself.
    """

    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    database: str = Field(
        min_length=1, max_length=128, description="Database (SQL Server, MariaDB) or service name."
    )
    db_schema: str | None = Field(
        default=None, alias="schema", min_length=1, max_length=128, description="Oracle only."
    )
    user: str = Field(min_length=1, max_length=128, description="The read-only login.")
    replica: bool = False
    encrypt: bool = Field(default=True, description="MariaDB and Oracle; always on for SQL Server.")
    encrypt_reason: str | None = Field(default=None, min_length=1, max_length=200)
    trust_server_certificate: bool = False
    trust_server_certificate_reason: str | None = Field(default=None, min_length=1, max_length=200)


class RestBindingDraft(ProbeOwlModel):
    """Where a REST connector points in one environment."""

    base_url: str = Field(min_length=1, max_length=2048)
    needs_token: bool = Field(description="Whether the API needs a token, kept in the keychain.")


ConnectorType = Literal["sqlserver", "oracle", "mariadb", "rest"]


class SaveConnector(ProbeOwlModel):
    """A new connector with one binding, or a binding for an environment not bound yet."""

    type: ConnectorType = Field(description="An existing connector's type must match.")
    owner: str | None = Field(
        default=None, min_length=1, max_length=80, description="Used for a new connector only."
    )
    environment: Name
    binding: SqlBindingDraft | RestBindingDraft
    base: str | None = Field(
        default=None,
        description=(
            "The base of the preview being confirmed. Omitted: nothing is written and the "
            "change is returned. Given: written only if the file is still as it was then."
        ),
    )


class DraftConnection(ProbeOwlModel):
    """A binding that is not saved yet, to connect to once; or a saved one, with a new password."""

    connector: Name = Field(description="Its connector, which may not exist yet.")
    type: ConnectorType
    environment: Name
    binding: SqlBindingDraft | RestBindingDraft | None = Field(
        default=None,
        description=(
            "Where it points. Omitted for a connector already bound in environment: its saved "
            "binding is tested with the password given, as Change password does before saving."
        ),
    )
    password: SecretStr | None = Field(
        default=None,
        min_length=1,
        max_length=1024,
        description="Used for this one test only: never stored, logged or returned.",
    )
    use_saved_password: bool = Field(
        default=False,
        description="Use the password this machine's keychain has for the binding.",
    )


class SaveSecret(ProbeOwlModel):
    """A binding's password or token, for this machine's keychain only."""

    password: SecretStr = Field(
        min_length=1,
        max_length=1024,
        description="Saved to the system keychain; never written to a file, logged or returned.",
    )
    replace: bool = Field(
        default=False,
        description=(
            "Overwrite a password already saved under the binding's keychain name. Without it, "
            "an existing one is a 409 secret-exists, so the person can be asked first."
        ),
    )
