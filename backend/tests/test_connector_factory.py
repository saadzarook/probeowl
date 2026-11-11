# Author: @SaadZarook
"""Opening a connector's adapter for one environment."""

from typing import Any

import pytest
from pydantic import TypeAdapter

from probeowl.connectors.factory import open_connector
from probeowl.connectors.mariadb import MariaDbAdapter
from probeowl.connectors.oracle import OracleAdapter
from probeowl.connectors.rest import RestAdapter
from probeowl.connectors.sqlserver import SqlServerAdapter
from probeowl.errors import ConfigError
from probeowl.model import Connector

connector: TypeAdapter[Connector] = TypeAdapter(Connector)


def sql(kind: str) -> Any:
    return connector.validate_python(
        {
            "connector": "db",
            "type": kind,
            "environments": {
                "uat": {"host": "h", "user": "probeowl_ro", "secret": "env:PROBEOWL_DB_UAT"}
            },
        }
    )


def test_each_sql_type_gets_its_adapter_with_the_resolved_secret() -> None:
    asked: list[str] = []

    def secrets(ref: str) -> str:
        asked.append(ref)
        return "pw"

    assert isinstance(open_connector(sql("sqlserver"), "uat", secrets), SqlServerAdapter)
    assert isinstance(open_connector(sql("oracle"), "uat", secrets), OracleAdapter)
    assert asked == ["env:PROBEOWL_DB_UAT", "env:PROBEOWL_DB_UAT"]


def test_a_rest_connector_resolves_its_token_only_when_it_has_one() -> None:
    rest = connector.validate_python(
        {
            "connector": "api",
            "type": "rest",
            "environments": {
                "uat": {"base_url": "https://api.example", "secret": "env:API_TOKEN"},
                "dev": {"base_url": "http://127.0.0.1:8081"},
            },
        }
    )
    asked: list[str] = []

    def token(ref: str) -> str:
        asked.append(ref)
        return "token"

    adapter = open_connector(rest, "uat", token)
    assert isinstance(adapter, RestAdapter)
    adapter.close()
    adapter = open_connector(rest, "dev", token)
    assert isinstance(adapter, RestAdapter)
    adapter.close()
    assert asked == ["env:API_TOKEN"]


def test_no_binding_in_the_environment_is_none() -> None:
    assert open_connector(sql("sqlserver"), "prod", lambda ref: "pw") is None


def test_a_mariadb_connector_gets_its_adapter() -> None:
    assert isinstance(open_connector(sql("mariadb"), "uat", lambda ref: "pw"), MariaDbAdapter)


def test_an_unreadable_secret_stops_the_run_from_starting(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def missing(ref: str) -> str:
        raise ConfigError("environment variable PROBEOWL_DB_UAT is not set")

    with pytest.raises(ConfigError, match="cannot be read") as failure:
        open_connector(sql("oracle"), "uat", missing)
    assert "PROBEOWL_DB_UAT" not in str(failure.value)  # a secret reference: never in a response
    assert failure.value.__cause__ is None
    # The person running owl serve still learns what to fix.
    assert "PROBEOWL_DB_UAT is not set" in caplog.text
