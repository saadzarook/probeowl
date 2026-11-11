# Author: @SaadZarook
"""Sign in as each test login of one database before verify.sh runs the tests, so a missing user
stops the run with one message instead of an error per test.

    uv run python ../dev/native/check_sign_in.py oracle|postgresql

Reads the URLs passwords.py exports. Prints login names and the database's error, never a
password.
"""

import os
import sys
from collections.abc import Callable

import oracledb
import psycopg
from sqlalchemy.engine import URL, make_url

URLS = {
    "oracle": ("PROBEOWL_TEST_ORACLE_URL", "PROBEOWL_TEST_ORACLE_ADMIN_URL"),
    "postgresql": ("PROBEOWL_TEST_POSTGRESQL_URL",),
}


def oracle_problem(url: URL) -> str | None:
    """Why this Oracle login cannot sign in, or None when it can."""
    try:
        oracledb.connect(
            user=url.username,
            password=url.password,
            host=url.host,
            port=url.port or 1521,
            service_name=str(url.query.get("service_name", "FREEPDB1")),
        ).close()
    except oracledb.DatabaseError as err:
        code = getattr(err.args[0], "full_code", "") if err.args else ""
        if code == "ORA-01017":  # no such user, or another password
            return (
                f"{url.username} cannot sign in (ORA-01017): run create_test_users.sh oracle first"
            )
        return f"{url.username} cannot sign in ({code or type(err).__name__})"
    return None


def postgresql_problem(url: URL) -> str | None:
    """Why this PostgreSQL login cannot sign in, or None when it can."""
    try:
        psycopg.connect(
            host=url.host,
            port=url.port or 5432,
            user=url.username,
            password=url.password,
            dbname=url.database,
            connect_timeout=5,
        ).close()
    except psycopg.OperationalError as err:
        # 28P01: wrong password; 28000: no such role; 3D000: no such database. A refusal while
        # connecting may carry no state, only the server's words.
        state = getattr(err, "sqlstate", None)
        refused = ("password authentication failed", "does not exist")
        if state in ("28P01", "28000", "3D000") or any(text in str(err) for text in refused):
            return (
                f"{url.username} cannot sign in to {url.database} ({state}): "
                "run create_test_users.sh postgresql first"
            )
        return f"{url.username} cannot sign in ({state or 'no answer on its port'})"
    return None


PROBLEM: dict[str, Callable[[URL], str | None]] = {
    "oracle": oracle_problem,
    "postgresql": postgresql_problem,
}


def problem(dialect: str, name: str) -> str | None:
    """Why the login in the URL named cannot sign in, or None when it can."""
    if not os.environ.get(name):
        return f"{name} is not set: run passwords.py init, then export the passwords"
    return PROBLEM[dialect](make_url(os.environ[name]))


def main(argv: list[str]) -> int:
    """Print each login that cannot sign in; exit 1 if any cannot, 2 for no such dialect."""
    if len(argv) != 1 or argv[0] not in URLS:
        print(f"usage: check_sign_in.py {'|'.join(URLS)}", file=sys.stderr)
        return 2
    found = [p for p in (problem(argv[0], name) for name in URLS[argv[0]]) if p]
    for line in found:
        print(f"STOP: {line}", file=sys.stderr)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
