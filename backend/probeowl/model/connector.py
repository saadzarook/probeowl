# Author: @SaadZarook
"""Connector and binding models."""

from typing import Annotated, Literal, Self

from pydantic import Field, HttpUrl, model_validator

from probeowl.model.common import Duration, Name, ProbeOwlModel, SecretRef

_SECRET_FORMATS = (
    "Written as a reference, never the value: keyring:<service>/<username> reads the "
    "system keychain entry for that service and username (add it with "
    "`keyring set <service> <username>`); env:<VARIABLE> reads an environment variable; "
    "vault://<path> is accepted but not supported yet."
)


DEFAULT_ROW_CEILING = 1000


class ConnectorGuards(ProbeOwlModel):
    """Limits set on the connector itself, in every environment."""

    row_ceiling: int = Field(
        default=DEFAULT_ROW_CEILING,
        gt=0,
        description="The most rows one lookup through this connector may return. A location's "
        "max_rows above it is refused by owl check and at save, and a lookup never returns more.",
    )


class SqlBinding(ProbeOwlModel):
    """Where a SQL connector points in one environment."""

    host: str = Field(min_length=1)
    port: int | None = Field(default=None, ge=1, le=65535)
    database: str | None = Field(
        default=None,
        description="Database name (SQL Server, MariaDB) or service name (Oracle).",
    )
    db_schema: str | None = Field(
        default=None,
        alias="schema",
        description=(
            "Oracle only: the schema unqualified table names resolve in (CURRENT_SCHEMA). "
            "SQL Server and MariaDB use the database instead."
        ),
    )
    time_zone: str | None = Field(
        default=None,
        pattern=r"^(?:\+(?:0\d|1[0-3]):[0-5]\d|\+14:00|-(?:0\d|1[01]):[0-5]\d|-12:00)$",
        description=(
            "Oracle only: the time zone of each session, as an offset from -12:00 to +14:00, "
            "such as +04:00. It is what CURRENT_DATE, CURRENT_TIMESTAMP and LOCALTIMESTAMP "
            "return in, and how a TIMESTAMP is compared with a TIMESTAMP WITH TIME ZONE. "
            "Omitted: the database's own (DBTIMEZONE), never the time zone of the machine "
            "running ProbeOwl."
        ),
    )
    user: str = Field(min_length=1, description="The read-only login.")
    secret: SecretRef = Field(description=f"The login's password. {_SECRET_FORMATS}")
    replica: bool = Field(
        default=False,
        description="This binding points at a read replica. Production bindings must.",
    )
    max_replica_lag: Duration = Field(
        default="60s",
        description=(
            "Replica bindings: when the replica's measured lag is longer, the circuit breaker "
            "pauses this connector and runs that need it are skipped."
        ),
    )
    allowed_databases: list[str] = Field(
        default_factory=list,
        description=(
            "SQL Server and MariaDB: other databases on the same server that probes may name "
            "(otherdb.dbo.table, otherdb.table). This binding's own database is always allowed."
        ),
    )
    encrypt: bool = Field(
        default=True,
        description=(
            "MariaDB and Oracle: encrypt the connection with TLS (Oracle connects over tcps). "
            "Set false only for a server that cannot offer TLS, such as a local test "
            "container. SQL Server connections are always encrypted."
        ),
    )
    encrypt_reason: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Why this binding is not encrypted. Required by `owl check` when a Production "
            "binding sets encrypt: false."
        ),
    )
    trust_server_certificate: bool = Field(
        default=False,
        description=(
            "Accept any server certificate, skipping the check that it is signed by a trusted "
            "authority and names the host, as on local test containers. Never needed for a "
            "properly configured server."
        ),
    )
    trust_server_certificate_reason: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Why this binding trusts any server certificate. Required by `owl check` when a "
            "Production binding sets trust_server_certificate."
        ),
    )

    def databases_allowed(self) -> set[str]:
        """The databases a probe on this binding may name: its own and allowed_databases."""
        own = {self.database} if self.database else set()
        return own | set(self.allowed_databases)


class RestBinding(ProbeOwlModel):
    """Where a REST connector points in one environment."""

    base_url: HttpUrl
    secret: SecretRef | None = Field(
        default=None, description=f"The API token, if one is needed. {_SECRET_FORMATS}"
    )


class SqlConnector(ProbeOwlModel):
    """A read-only client for one SQL Server, Oracle or MariaDB database."""

    connector: Name
    type: Literal["sqlserver", "oracle", "mariadb"]
    owner: str | None = None
    guards: ConnectorGuards = Field(default_factory=ConnectorGuards)
    environments: dict[Name, SqlBinding] = Field(
        description="One binding per environment; a missing one means Not configured there.",
    )

    @model_validator(mode="after")
    def _fields_for_this_type(self) -> Self:
        for name, binding in self.environments.items():
            # SQL Server has no per-session default schema; MariaDB's schema is its database.
            if binding.db_schema is not None and self.type != "oracle":
                raise ValueError(f"{name}: schema applies only to oracle; use database")
            # SQL Server has no session time zone, and MariaDB's is the server's own.
            if binding.time_zone is not None and self.type != "oracle":
                raise ValueError(f"{name}: time_zone applies only to oracle")
            # Oracle qualifies names by schema, not by database.
            if binding.allowed_databases and self.type == "oracle":
                raise ValueError(f"{name}: allowed_databases does not apply to oracle")
            # SQL Server always offers TLS, so there is never a reason to send it in the clear.
            if not binding.encrypt and self.type == "sqlserver":
                raise ValueError(
                    f"{name}: sqlserver connections are always encrypted; remove encrypt: false "
                    "(use trust_server_certificate for a self-signed certificate)"
                )
            if binding.encrypt_reason and binding.encrypt:
                raise ValueError(f"{name}: encrypt_reason needs encrypt: false")
            if binding.trust_server_certificate and not binding.encrypt:
                raise ValueError(
                    f"{name}: trust_server_certificate needs an encrypted connection; "
                    "remove it or encrypt: false"
                )
            if binding.trust_server_certificate_reason and not binding.trust_server_certificate:
                raise ValueError(
                    f"{name}: trust_server_certificate_reason needs trust_server_certificate: true"
                )
        return self


class RestConnector(ProbeOwlModel):
    """A read-only client for one REST API. Only GET is ever sent."""

    connector: Name
    type: Literal["rest"]
    owner: str | None = None
    guards: ConnectorGuards = Field(default_factory=ConnectorGuards)
    environments: dict[Name, RestBinding] = Field(
        description="One binding per environment; a missing one means Not configured there.",
    )


Connector = Annotated[SqlConnector | RestConnector, Field(discriminator="type")]
