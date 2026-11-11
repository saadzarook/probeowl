# Author: @SaadZarook
"""Where the store tests find PostgreSQL, SQL Server, MariaDB and Oracle.

In order: a URL in PROBEOWL_TEST_<DB>_URL; else, in CI, a container; else the tests skip.
The tests create and drop tables, so a URL is used only if its database (or, on Oracle, its
schema) name contains "test".
"""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import docker
import oracledb
import pytest
from docker.errors import DockerException
from sqlalchemy.engine import make_url
from testcontainers.community.mssql import SqlServerContainer
from testcontainers.community.mysql import MySqlContainer
from testcontainers.community.oracle import OracleDbContainer
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer

_CONTAINERS: dict[str, Callable[[], DockerContainer]] = {
    "postgresql": lambda: PostgresContainer("postgres:16", driver="psycopg"),
    # mssql-python is the SQL Server driver ProbeOwl uses (see docs/decisions.md).
    "mssql": lambda: SqlServerContainer(
        "mcr.microsoft.com/mssql/server:2022-latest", dbname="master", dialect="mssql+mssqlpython"
    ),
    "mariadb": lambda: MySqlContainer("mariadb:11", dialect="pymysql"),
    # A user of its own, as a real store would have: SQLAlchemy does not reflect tables in
    # the SYSTEM tablespace, where the default SYSTEM user would put them.
    "oracle": lambda: OracleDbContainer(
        "gvenzl/oracle-free:23-slim-faststart", username="probeowl", password="probeowl"
    ),
}

STORE_DATABASES = tuple(_CONTAINERS)


def url_variable(name: str) -> str:
    """The environment variable that points the store tests at an existing database."""
    return f"PROBEOWL_TEST_{name.upper()}_URL"


def check_test_database(url: str) -> None:
    """Fail unless the URL's database or schema name contains "test"."""
    parsed = make_url(url)
    names = [parsed.database or ""]
    if parsed.get_backend_name() == "oracle":
        # On Oracle the schema is the user, and the database part is often empty.
        names += [parsed.username or "", str(parsed.query.get("service_name", ""))]
    if not any("test" in name.lower() for name in names):
        shown = parsed.render_as_string(hide_password=True)
        pytest.fail(
            f"refusing to run the store tests against {shown}: they create and drop tables, "
            "so the database or schema name must contain 'test'"
        )


@contextmanager
def store_database_url(name: str) -> Iterator[str]:
    """Yield a SQLAlchemy URL for the named database, or skip."""
    variable = url_variable(name)
    url = os.environ.get(variable)
    if url:
        check_test_database(url)
        yield url
        return
    if os.environ.get("CI") != "true":
        pytest.skip(
            f"{variable} is not set, so the database tests on {name} are skipped; they run in CI"
        )
    _require_docker()
    with _CONTAINERS[name]() as container:
        if name == "oracle":
            _grant_session_view(container)
        url = container.get_connection_url()  # type: ignore[attr-defined]
        yield _for_sqlalchemy(url)


def _grant_session_view(container: Any) -> None:
    # CI only: lets the test login read v$session, to show its probe session has parallel
    # query disabled. A real probe login never gets this grant.
    sys = oracledb.connect(
        user="sys",
        password=container.oracle_password,
        host=container.get_container_host_ip(),
        port=int(container.get_exposed_port(1521)),
        service_name="FREEPDB1",
        mode=oracledb.AUTH_MODE_SYSDBA,
    )
    sys.cursor().execute(f"GRANT SELECT ON sys.v_$session TO {container.username}")
    sys.close()


def _for_sqlalchemy(url: str) -> str:
    # MySqlContainer speaks for MariaDB too; SQLAlchemy has a dialect of its own for it.
    url = url.replace("mysql+pymysql://", "mariadb+pymysql://", 1)
    if url.startswith("mssql+mssqlpython://"):
        # The container's certificate is self-signed.
        url += "?TrustServerCertificate=yes"
    return url


def _require_docker() -> None:
    try:
        docker.from_env().ping()
    except DockerException as err:
        pytest.fail(f"CI needs Docker for the store tests: {err}")
