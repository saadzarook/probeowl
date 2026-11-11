# Author: @SaadZarook
"""The native setup's passwords, in the system keychain and never in a file.

    init        generate the test logins' passwords, keeping any that exist
    set <name>  ask for an admin password: mssql_admin, mariadb_admin or postgresql_admin
    env         print the ones that exist as shell exports, for verify.sh to read

Run through uv from backend/, where keyring is installed. Nothing here prints a password except
`env`, which verify.sh captures and never echoes.
"""

import getpass
import secrets
import shlex
import sys

import keyring

SERVICE = "probeowl-test"
GENERATED = (
    "test_mssql",
    "test_mssql_admin",
    "test_mariadb",
    "test_mariadb_admin",
    "test_oracle",
    "test_oracle_admin",
    "test_postgresql",
    "ro",
    "rw",
)
ASKED = ("mssql_admin", "mariadb_admin", "postgresql_admin")
TEST_USER = "probeowl_test"
POSTGRES_PORT = 5432


def _refuse_an_insecure_keychain() -> None:
    backend = type(keyring.get_keyring()).__module__
    if backend.startswith(("keyrings.alt", "keyring.backends.fail", "keyring.backends.null")):
        sys.exit(f"refusing to keep passwords in {backend}: it is not the system keychain")


def _generated() -> str:
    # Letters and digits only: these go in URLs and in SQL. Starts with a letter for Oracle.
    return f"Pt{secrets.token_hex(16)}x1"


def init() -> None:
    """Generate each test login's password that does not exist yet."""
    for name in GENERATED:
        if keyring.get_password(SERVICE, name) is None:
            keyring.set_password(SERVICE, name, _generated())
            print(f"{name}: generated")
        else:
            print(f"{name}: kept")


def set_admin(name: str) -> None:
    """Ask for an admin password chosen at install time."""
    if name not in ASKED:
        sys.exit(f"name one of: {', '.join(ASKED)}")
    keyring.set_password(SERVICE, name, getpass.getpass(f"{name}: "))
    print(f"{name}: saved")


def environment() -> dict[str, str]:
    """What verify.sh exports. Every URL is local and names a database with "test" in it.

    Only what the keychain has: databases are installed one at a time, and a missing admin
    password only matters to a run that needs it, which then says so.
    """
    saved = {name: keyring.get_password(SERVICE, name) for name in (*GENERATED, *ASKED)}
    found = {name: value for name, value in saved.items() if value is not None}
    return {key: value for key, value in _wanted(found).items() if value is not None}


def _wanted(found: dict[str, str]) -> dict[str, str | None]:
    user = TEST_USER
    host = "127.0.0.1"

    def _get(name: str) -> str | None:
        return found.get(name)

    def url(name: str, template: str) -> str | None:
        password = _get(name)
        return None if password is None else template.format(password=password)

    return {
        "PROBEOWL_TEST_MSSQL_URL": url(
            "test_mssql",
            f"mssql+mssqlpython://{user}:{{password}}@{host}:1433/{user}?TrustServerCertificate=yes",
        ),
        # The tests' own login for their checks and cleanup; ProbeOwl never connects as it.
        "PROBEOWL_TEST_MSSQL_ADMIN_URL": url(
            "test_mssql_admin",
            f"mssql+mssqlpython://probeowl_test_admin:{{password}}@{host}:1433/{user}"
            "?TrustServerCertificate=yes",
        ),
        "PROBEOWL_TEST_MARIADB_URL": url(
            "test_mariadb", f"mariadb+pymysql://{user}:{{password}}@{host}:3306/{user}"
        ),
        "PROBEOWL_TEST_MARIADB_ADMIN_URL": url(
            "test_mariadb_admin",
            f"mariadb+pymysql://probeowl_test_admin:{{password}}@{host}:3306/{user}",
        ),
        "PROBEOWL_TEST_ORACLE_URL": url(
            "test_oracle",
            f"oracle+oracledb://{user}:{{password}}@{host}:1521/?service_name=FREEPDB1",
        ),
        "PROBEOWL_TEST_ORACLE_ADMIN_URL": url(
            "test_oracle_admin",
            f"oracle+oracledb://probeowl_test_admin:{{password}}@{host}:1521/"
            "?service_name=FREEPDB1",
        ),
        "PROBEOWL_TEST_POSTGRESQL_URL": url(
            "test_postgresql",
            f"postgresql+psycopg://{user}:{{password}}@{host}:{POSTGRES_PORT}/{user}",
        ),
        "NATIVE_TEST_MSSQL_PASSWORD": _get("test_mssql"),
        "NATIVE_TEST_MSSQL_ADMIN_PASSWORD": _get("test_mssql_admin"),
        "NATIVE_TEST_MARIADB_PASSWORD": _get("test_mariadb"),
        "NATIVE_TEST_MARIADB_ADMIN_PASSWORD": _get("test_mariadb_admin"),
        "NATIVE_TEST_ORACLE_PASSWORD": _get("test_oracle"),
        "NATIVE_TEST_ORACLE_ADMIN_PASSWORD": _get("test_oracle_admin"),
        "NATIVE_TEST_POSTGRESQL_PASSWORD": _get("test_postgresql"),
        "NATIVE_MSSQL_ADMIN": _get("mssql_admin"),
        "NATIVE_MARIADB_ADMIN": _get("mariadb_admin"),
        "NATIVE_POSTGRESQL_ADMIN": _get("postgresql_admin"),
        "SEED_RO_PASSWORD": _get("ro"),
        "SEED_RW_PASSWORD": _get("rw"),
        # The secret every binding in the generated config names.
        "PROBEOWL_NATIVE_RO_PASSWORD": _get("ro"),
    }


def main() -> None:
    """Run one command."""
    _refuse_an_insecure_keychain()
    match sys.argv[1:]:
        case ["init"]:
            init()
        case ["set", name]:
            set_admin(name)
        case ["env"]:
            for name, value in environment().items():
                print(f"export {name}={shlex.quote(value)}")
        case _:
            sys.exit(__doc__)


if __name__ == "__main__":
    main()
