# Author: @SaadZarook
"""Open a connector's adapter for one environment, from its binding and its secret."""

import logging
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Protocol

from probeowl.connectors.lookup import LookupResult
from probeowl.connectors.mariadb import MariaDbAdapter
from probeowl.connectors.oracle import OracleAdapter
from probeowl.connectors.rest import RestAdapter, RestLimits
from probeowl.connectors.sql import SqlAdapter, SqlLimits
from probeowl.connectors.sqlserver import SqlServerAdapter
from probeowl.errors import ConfigError
from probeowl.model.connector import RestConnector, SqlConnector
from probeowl.secrets import resolve_secret

log = logging.getLogger(__name__)

_SQL_ADAPTERS: dict[str, type[SqlAdapter]] = {
    "sqlserver": SqlServerAdapter,
    "oracle": OracleAdapter,
    "mariadb": MariaDbAdapter,
}


class OpenConnector(Protocol):
    """A connector ready for lookups, closed when its run ends."""

    def lookup(
        self, lookup: str, parameters: Mapping[str, object], max_rows: int, /
    ) -> LookupResult:
        """Run one lookup; ProbeOwlError if it cannot."""
        ...

    def close(self) -> None:
        """Release the connection."""
        ...


def open_connector(
    connector: SqlConnector | RestConnector,
    environment: str,
    secrets: Callable[[str], str] = resolve_secret,
) -> RestAdapter | SqlAdapter | None:
    """The connector's adapter in environment, or None when it has no binding there."""
    if isinstance(connector, RestConnector):
        rest = connector.environments.get(environment)
        if rest is None:
            return None
        secret = _secret(connector, environment, rest.secret, secrets) if rest.secret else None
        return RestAdapter(rest, secret, RestLimits(row_ceiling=connector.guards.row_ceiling))
    return open_sql(connector, environment, secrets)


def sql_adapter(connector_type: str) -> type[SqlAdapter]:
    """The adapter class of a SQL connector type, for what it says without connecting."""
    return _SQL_ADAPTERS[connector_type]


def configured_guards(connector: SqlConnector, environment: str) -> list[str]:
    """The guards a lookup on this binding runs under, from config: no secret, no connection."""
    binding = connector.environments[environment]
    # Never connects, so it needs no password.
    return _SQL_ADAPTERS[connector.type](binding, "", _with_ceiling(connector, None)).guards()


def _with_ceiling(connector: SqlConnector, limits: SqlLimits | None) -> SqlLimits:
    return replace(limits or SqlLimits(), row_ceiling=connector.guards.row_ceiling)


def open_sql(
    connector: SqlConnector,
    environment: str,
    secrets: Callable[[str], str] = resolve_secret,
    limits: SqlLimits | None = None,
) -> SqlAdapter | None:
    """The SQL connector's adapter in environment, or None when it has no binding there."""
    binding = connector.environments.get(environment)
    if binding is None:
        return None
    password = _secret(connector, environment, binding.secret, secrets)
    return _SQL_ADAPTERS[connector.type](binding, password, _with_ceiling(connector, limits))


def _secret(
    connector: SqlConnector | RestConnector,
    environment: str,
    reference: str,
    secrets: Callable[[str], str],
) -> str:
    try:
        return secrets(reference)
    except ConfigError as err:
        # The reason names the keychain entry or variable: for the terminal owl serve runs in,
        # never for a response.
        log.warning("connector %s in %s: %s", connector.connector, environment, err)
        raise ConfigError(
            f"the secret of connector {connector.connector} in {environment} cannot be read; "
            "the owl serve terminal says why"
        ) from None
