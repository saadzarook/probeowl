# Author: @SaadZarook
"""What each SQL dialect forbids and how it binds parameters and caps rows.

Dialect knowledge lives here as data, so the statement guard has no per-dialect branches.
"""

from dataclasses import dataclass
from typing import Literal

ParamStyle = Literal["qmark", "named", "pyformat"]


@dataclass(frozen=True)
class DialectRules:
    """The guard rules for one SQL dialect."""

    name: str
    sqlglot_dialect: str
    param_style: ParamStyle
    row_cap: str
    forbidden_functions: frozenset[str]
    # Which part of a qualified table name is a database: SQL Server's db.schema.table puts it
    # in sqlglot's `catalog`, MariaDB's db.table in `db`. Oracle names schemas, not databases.
    database_part: Literal["catalog", "db"] | None = None
    # Oracle package calls (dbms_lock.sleep, utl_http.request) can block or reach the network.
    forbid_qualified_calls: bool = False
    # What the Connectors screen lists as Forbidden; each has a test showing the guard refuses it.
    forbidden_shown: tuple[str, ...] = ()
    # Options the guard adds at the end of every query, right after its last token, so a
    # trailing -- comment cannot swallow them.
    query_options: str | None = None


SQLSERVER = DialectRules(
    name="sqlserver",
    sqlglot_dialect="tsql",
    param_style="qmark",  # mssql-python
    row_cap="TOP",
    database_part="catalog",
    # The client-side resource cap, as Resource Governor's MAXDOP 1 would be on any edition.
    query_options="OPTION (MAXDOP 1)",
    # Linked-server and file access functions read outside the database being probed.
    forbidden_functions=frozenset({"OPENROWSET", "OPENQUERY", "OPENDATASOURCE", "OPENXML"}),
    forbidden_shown=(
        "NOLOCK",
        "HOLDLOCK",
        "TABLOCKX",
        "UPDLOCK",
        "temp tables",
        "OPENROWSET()",
        "OPENQUERY()",
        "OPENDATASOURCE()",
        "OPENXML()",
        "linked servers",
    ),
)

ORACLE = DialectRules(
    name="oracle",
    sqlglot_dialect="oracle",
    param_style="named",  # python-oracledb
    row_cap="FETCH FIRST",
    forbidden_functions=frozenset(),
    forbid_qualified_calls=True,
    forbidden_shown=(
        "FOR UPDATE",
        "LOCK TABLE",
        "PL/SQL blocks",
        "package calls",
        "database links",
    ),
)

MARIADB = DialectRules(
    name="mariadb",
    sqlglot_dialect="mysql",  # sqlglot has no MariaDB dialect; MySQL covers the syntax we allow
    param_style="pyformat",  # PyMySQL
    row_cap="LIMIT",
    database_part="db",
    # Locks, deliberate delays and file reads.
    forbidden_functions=frozenset(
        {"GET_LOCK", "RELEASE_LOCK", "RELEASE_ALL_LOCKS", "IS_FREE_LOCK", "IS_USED_LOCK"}
        | {"SLEEP", "BENCHMARK", "LOAD_FILE"}
    ),
    forbidden_shown=(
        "FOR UPDATE",
        "LOCK IN SHARE MODE",
        "GET_LOCK()",
        "SLEEP()",
        "BENCHMARK()",
        "LOAD_FILE()",
    ),
)

RULES_BY_CONNECTOR_TYPE = {rules.name: rules for rules in (SQLSERVER, ORACLE, MARIADB)}
