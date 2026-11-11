# Author: @SaadZarook
"""The native verification setup, with no database: its config, its safety checks, its seeds.

Each seed is rendered from its script's text as the script would send it. The defaults must stay
what the Docker stack and CI use, and the settings must reach every statement.
"""

import importlib.util
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from probeowl.checks.rules import folder_failures
from probeowl.model.check import check_folder, load_applications

ROOT = Path(__file__).parents[2]
EXAMPLES = ROOT / "examples"
NATIVE = ROOT / "dev" / "native"
SEED = ROOT / "dev" / "seed"
RO_PASSWORD = "Ro_Pw_Marker"
RW_PASSWORD = "Rw_Pw_Marker"


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, NATIVE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_config = load("make_config")


def names() -> dict[str, str]:
    found = {}
    for line in (NATIVE / "names.sh").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            found[key] = value
    return found


# --- Names ------------------------------------------------------------------------------------


def test_every_database_and_login_name_contains_test() -> None:
    for key, value in names().items():
        if key not in {
            "ORACLE_DISTRO",
            "UBUNTU_DISTRO",
            "POSTGRES_SERVICE",
            "POSTGRES_PORT",
            "POSTGRES_BIN",
            "SQLSERVER_COMPAT",
        }:
            assert "test" in value.lower(), key


def test_the_config_writer_and_the_seed_settings_agree_on_the_names() -> None:
    settings = names()
    assert settings["RO_LOGIN"] == make_config.RO_LOGIN
    assert make_config.NAMES["wallet"] == settings["SQLSERVER_DB"]
    assert make_config.NAMES["wallet_uat"] == settings["SQLSERVER_DB_UAT"]
    assert make_config.NAMES["access"] == settings["MARIADB_DB"]
    assert make_config.NAMES["access_uat"] == settings["MARIADB_DB_UAT"]
    assert make_config.NAMES["LEDGER"] == settings["ORACLE_SCHEMA"]
    assert make_config.NAMES["LEDGER_UAT"] == settings["ORACLE_SCHEMA_UAT"]


# --- The config writer ----------------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> Path:
    out = tmp_path / "config"
    make_config.make_config(EXAMPLES, out)
    return out


def connector(root: Path, path: str) -> dict[str, Any]:
    document: dict[str, Any] = yaml.safe_load((root / path).read_text(encoding="utf-8"))
    return document


def test_every_binding_names_a_test_database_and_login(config: Path) -> None:
    wallet = connector(config, "wallet/connectors/wallet_db.yaml")["environments"]
    assert wallet["prod"]["database"] == "wallet_test"
    assert wallet["uat"]["database"] == "wallet_uat_test"
    ledger = connector(config, "wallet/connectors/ledger_db.yaml")["environments"]
    assert ledger["prod"]["schema"] == "LEDGER_TEST"
    assert ledger["uat"]["schema"] == "LEDGER_UAT_TEST"
    assert ledger["prod"]["database"] == "FREEPDB1"
    access = connector(config, "card-access/connectors/access_db.yaml")["environments"]
    assert access["uat"]["database"] == "access_uat_test"
    for binding in [*wallet.values(), *ledger.values(), *access.values()]:
        assert binding["user"] == "probeowl_test_ro"
        assert binding["secret"] == "env:PROBEOWL_NATIVE_RO_PASSWORD"


def test_nothing_of_the_real_names_is_left_in_a_binding(config: Path) -> None:
    for path in config.rglob("connectors/*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert "user: probeowl_ro" not in text
        assert "keyring:" not in text


def test_the_copy_loads_and_passes_owl_check_rules(config: Path) -> None:
    assert check_folder(config).findings == []
    for app in load_applications(config).values():
        assert folder_failures(app) == []


def test_examples_are_left_as_they_were(tmp_path: Path) -> None:
    path = EXAMPLES / "wallet" / "connectors" / "wallet_db.yaml"
    before = path.read_bytes()
    make_config.make_config(EXAMPLES, tmp_path / "copy")
    assert path.read_bytes() == before


def test_the_bad_copy_is_what_m3s_job_makes(tmp_path: Path) -> None:
    out = tmp_path / "bad"
    make_config.make_config(EXAMPLES, out, bad=True)
    entity = (out / "wallet" / "entities" / "topup.yaml").read_text(encoding="utf-8")
    assert "WHERE account = :ledger_txn_id" in entity
    ledger = connector(out, "wallet/connectors/ledger_db.yaml")["environments"]
    assert ledger["uat"]["port"] == 1
    assert ledger["prod"]["port"] == 1521
    access = connector(out, "card-access/connectors/access_db.yaml")["environments"]
    assert access["uat"]["user"] == "probeowl_test_nobody"
    assert access["prod"]["user"] == "probeowl_test_ro"


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"host": "db.example"}, "host db.example is not 127.0.0.1"),
        ({"database": "wallet"}, "database or schema wallet does not contain 'test'"),
        ({"user": "probeowl_ro"}, "login probeowl_ro does not contain 'test'"),
        ({"secret": "keyring:probeowl/wallet_db.prod"}, "its secret is not the native setup's"),
    ],
)
def test_an_unsafe_binding_is_named(config: Path, change: dict[str, str], problem: str) -> None:
    path = config / "wallet" / "connectors" / "wallet_db.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["environments"]["prod"].update(change)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    assert [f"wallet_db in prod: {problem}"] == make_config.unsafe_bindings(config)


def test_a_rest_binding_must_stay_on_this_machine(config: Path) -> None:
    path = config / "wallet" / "connectors" / "checkout_api.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["environments"]["prod"]["base_url"] = "https://api.example/v1"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    assert "is not on 127.0.0.1" in make_config.unsafe_bindings(config)[0]


def test_the_writer_refuses_to_finish_on_an_unsafe_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(make_config, "RO_LOGIN", "probeowl_ro_unsafe")
    with pytest.raises(SystemExit, match="refusing this config"):
        make_config.make_config(EXAMPLES, tmp_path / "out")


def test_the_examples_themselves_are_refused_as_they_stand() -> None:
    assert make_config.unsafe_bindings(EXAMPLES)


# --- SQL Server 2025 at 2022's level --------------------------------------------------------


def test_every_sql_server_test_database_is_held_at_compat_160() -> None:
    assert names()["SQLSERVER_COMPAT"] == "160"
    users = (NATIVE / "create_test_users.sh").read_text(encoding="utf-8")
    assert "ALTER DATABASE [$TEST_DATABASE] SET COMPATIBILITY_LEVEL = $SQLSERVER_COMPAT;" in users
    verify = (NATIVE / "verify.sh").read_text(encoding="utf-8")
    assert "SET COMPATIBILITY_LEVEL = $SQLSERVER_COMPAT" in verify
    assert 'for db in "$SQLSERVER_DB" "$SQLSERVER_DB_UAT"; do' in verify


def test_verify_refuses_another_level_and_says_the_version() -> None:
    verify = (NATIVE / "verify.sh").read_text(encoding="utf-8")
    assert '[ "$level" = "$SQLSERVER_COMPAT" ] ||' in verify
    assert "SERVERPROPERTY('ProductVersion')" in verify
    assert 'check_sqlserver "$TEST_DATABASE"' in verify
    assert "native $year (compat $SQLSERVER_COMPAT) $COMMIT" in verify


def test_the_docker_seed_is_left_at_its_own_level() -> None:
    # CI's 2022 container uses its default; only the native runner sets a level.
    for name in ("seed.sh", "wallet.sql", "logins.sql"):
        assert "COMPATIBILITY_LEVEL" not in (SEED / "sqlserver" / name).read_text(encoding="utf-8")


def mssql_block() -> str:
    users = (NATIVE / "create_test_users.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    return users.split("mssql() {", 1)[1].split("\n}\n", 1)[0]


def test_the_sql_server_admin_login_gets_what_sa_gave_the_tests_in_ci() -> None:
    block = mssql_block()
    for grant in ("ALTER ANY LOGIN", "VIEW SERVER PERFORMANCE STATE", "ALTER ANY CONNECTION"):
        assert f"GRANT {grant} TO [$TEST_ADMIN];" in block
    assert "ALTER ROLE db_owner ADD MEMBER [$TEST_ADMIN];" in block
    assert "DROP LOGIN ' + QUOTENAME(name)" in block
    assert "LIKE 'probeowl[_]test[_]noplan[_]%'" in block
    assert "LIKE 'probeowl[_]test[_]lag[_]%'" in block


def test_probeowls_sql_server_login_gets_no_server_grant_and_loses_what_it_had() -> None:
    block = mssql_block()
    for grant in ("ALTER ANY LOGIN", "VIEW SERVER PERFORMANCE STATE", "ALTER ANY CONNECTION"):
        assert f"GRANT {grant} TO [$TEST_USER]" not in block
        assert f"REVOKE {grant} FROM [$TEST_USER];" in block
    assert "ALTER ROLE db_owner ADD MEMBER [$TEST_USER]" not in block
    assert "ALTER ROLE db_owner DROP MEMBER [$TEST_USER];" in block
    for role in ("db_datareader", "db_datawriter", "db_ddladmin"):
        assert f"ALTER ROLE {role} ADD MEMBER [$TEST_USER];" in block
    assert "GRANT SHOWPLAN TO [$TEST_USER];" in block


def mariadb_block() -> str:
    users = (NATIVE / "create_test_users.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    return users.split("mariadb_users() {", 1)[1].split("\n}\n", 1)[0]


def oracle_block() -> str:
    users = (NATIVE / "create_test_users.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    return users.split("oracle() {", 1)[1].split("\n}\n", 1)[0]


def test_probeowls_oracle_login_gets_only_its_own_schema() -> None:
    block = oracle_block()
    assert "REVOKE SELECT ON sys.v_\\$session FROM $TEST_USER" in block
    grants = [
        line for line in block.splitlines() if line.startswith("GRANT") and "$TEST_USER" in line
    ]
    assert grants == ["GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO $TEST_USER;"]
    assert "GRANT SELECT ON sys.v_\\$session TO $TEST_ADMIN;" in block


def test_the_oracle_admin_gets_its_any_privileges_on_probeowl_tests_schema_only() -> None:
    block = oracle_block()
    # The system-wide grants an earlier version gave are taken back, whichever they were.
    assert "privilege LIKE '% ANY %'" in block
    assert "'REVOKE ' || p.privilege || ' FROM $TEST_ADMIN'" in block
    statement = block.split("GRANT CREATE ANY TABLE", 1)[1].split(";", 1)[0]
    assert "CREATE ANY INDEX" in statement
    assert statement.rstrip().endswith("ON SCHEMA $TEST_USER TO $TEST_ADMIN")
    granted = [line for line in block.splitlines() if line.startswith("GRANT") and "ANY" in line]
    assert granted == [
        "GRANT CREATE ANY TABLE, CREATE ANY INDEX, DROP ANY TABLE, SELECT ANY TABLE, "
        "INSERT ANY TABLE,"
    ]


def test_every_oracle_call_sets_its_own_environment() -> None:
    helper = (NATIVE / "oracle_sysdba.sh").read_text(encoding="utf-8")
    assert "ORACLE_SID=FREE" in helper
    assert "ORACLE_HOME=$(grep ^FREE: /etc/oratab | cut -d: -f2)" in helper
    assert "PATH=$ORACLE_HOME/bin:$PATH" in helper
    # --exec: plain "wsl.exe --" would expand $ORACLE_HOME in the login shell, before it is set.
    assert 'wsl.exe -d "$ORACLE_DISTRO" -u oracle --exec bash -c' in helper
    assert "exec $ORACLE_HOME/bin/sqlplus -s -L / as sysdba" in helper
    for script in ("create_test_users.sh", "verify.sh"):
        text = (NATIVE / script).read_text(encoding="utf-8")
        assert 'source "$HERE/oracle_sysdba.sh"' in text, script
        # No call left that relies on the oracle user's profile finding sqlplus.
        assert "bash -lc 'sqlplus" not in text, script
        assert 'wsl.exe -d "$ORACLE_DISTRO"' not in text, script


def test_the_oracle_setup_waits_for_freepdb1_to_open() -> None:
    helper = (NATIVE / "oracle_sysdba.sh").read_text(encoding="utf-8")
    assert "SELECT open_mode INTO mode_now FROM v$pdbs WHERE name = 'FREEPDB1'" in helper
    assert "oracle_wait_open || exit 1" in oracle_block()


def test_verify_says_the_oracle_version_and_stops_on_a_wrong_listener() -> None:
    verify = (NATIVE / "verify.sh").read_text(encoding="utf-8")
    block = verify.split("check_oracle() {", 1)[1].split("\n}\n", 1)[0]
    assert "i.version_full || ' ' || i.edition || ' ' || p.open_mode" in block
    assert "grep -qF '(HOST=127.0.0.1)(PORT=1521)'" in block
    assert 'Service "freepdb1"' in block
    assert "stop \"Oracle's listener is not on 127.0.0.1:1521 for FREEPDB1" in block
    assert 'check_sign_in.py oracle) ||\n    stop "run create_test_users.sh oracle first"' in block
    assert "if uses oracle; then check_oracle; fi" in verify


def test_verify_stops_unless_postgresql_listens_on_loopback_only_and_warns_off_16() -> None:
    verify = (NATIVE / "verify.sh").read_text(encoding="utf-8")
    block = verify.split("check_postgresql() {", 1)[1].split("\n}\n", 1)[0]
    # What Windows has listening on the port, checked before anything signs in to it.
    windows = block.index("Get-NetTCPConnection -LocalPort $POSTGRES_PORT -State Listen")
    assert windows < block.index('loopback_only "$listeners" ||') < block.index("check_sign_in.py")
    assert "PostgreSQL must listen on 127.0.0.1 and ::1 only" in block
    # And what the server says, as the test login.
    assert "current_setting('listen_addresses'), current_setting('port')" in block
    assert 'pg_psql "$NATIVE_TEST_POSTGRESQL_PASSWORD" "$TEST_USER" "$TEST_DATABASE"' in block
    assert 'loopback_only "$listen" ||' in block
    assert '[ "$port" = "$POSTGRES_PORT" ]' in block
    assert "CI's container 16" in block
    assert (
        'check_sign_in.py postgresql) ||\n    stop "run create_test_users.sh postgresql first"'
        in block
    )
    assert "if uses postgresql; then check_postgresql; fi" in verify
    assert names()["POSTGRES_PORT"] == "5432"


def loopback_only(value: str) -> bool:
    """Whether postgresql.sh's loopback_only accepts this list, run by Git Bash."""
    helper = (NATIVE / "postgresql.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    done = subprocess.run(
        [git_bash(), "-c", f'{helper}\nloopback_only "$1"', "check", value],
        capture_output=True,
        text=True,
        check=False,
    )
    return done.returncode == 0


@pytest.mark.parametrize(
    "value", ["127.0.0.1", "::1", "localhost", "127.0.0.1,::1", "127.0.0.1, ::1", "::1,127.0.0.1"]
)
def test_loopback_addresses_are_accepted(value: str) -> None:
    assert loopback_only(value)


@pytest.mark.parametrize(
    "value", ["", "*", "0.0.0.0", "::", "127.0.0.1,0.0.0.0", "::1,192.168.1.20", "127.0.0.1,,"]
)
def test_anything_but_loopback_is_refused(value: str) -> None:
    assert not loopback_only(value)


def test_the_postgresql_test_login_gets_only_its_own_database() -> None:
    users = (NATIVE / "create_test_users.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = users.split("postgresql() {", 1)[1].split("\n}\n", 1)[0]
    assert block.count("LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD") == 2
    assert "CREATE DATABASE $TEST_DATABASE OWNER $TEST_USER" in block
    assert "REVOKE CONNECT ON DATABASE $TEST_DATABASE FROM PUBLIC;" in block
    # The password goes on stdin, never in an argument, and stays out of the server's log.
    assert (
        'pg_psql "$NATIVE_POSTGRESQL_ADMIN" postgres postgres -q -v ON_ERROR_STOP=1 <<SQL' in block
    )
    sets = ["log_statement = 'none'", "log_min_error_statement = 'panic'", "track_utility = off"]
    assert all(block.index(s) < block.index("PASSWORD '$NATIVE_TEST") for s in sets)


def test_psql_gets_each_password_from_the_environment_on_127_0_0_1() -> None:
    helper = (NATIVE / "postgresql.sh").read_text(encoding="utf-8")
    assert (
        'PGPASSWORD="$password" PGCLIENTENCODING=UTF8 "$POSTGRES_BIN/psql.exe" -X -h 127.0.0.1'
        in helper
    )
    for script in (NATIVE / "verify.sh", NATIVE / "create_test_users.sh"):
        text = script.read_text(encoding="utf-8")
        assert 'source "$HERE/postgresql.sh"' in text
        # Every other way to call psql, with a password or a host of its own.
        assert "PGPASSWORD" not in text and "psql.exe" not in text, script.name


def test_the_sign_in_check_names_the_login_never_the_password() -> None:
    text = (NATIVE / "check_sign_in.py").read_text(encoding="utf-8")
    assert "ORA-01017" in text and "run create_test_users.sh oracle first" in text
    assert "run create_test_users.sh postgresql first" in text
    printed = [line for line in text.splitlines() if "print(" in line or "return f" in line]
    # The login's name and Oracle's code only: no line formats the URL's password or the URL.
    assert printed and not [line for line in printed if "url.password" in line], printed
    assert not [line for line in printed if "{url}" in line or "environ[" in line], printed


def test_probeowls_mariadb_login_gets_nothing_server_wide() -> None:
    block = mariadb_block()
    assert "REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$TEST_USER'@'%';" in block
    grants = [line for line in block.splitlines() if "TO '$TEST_USER'" in line]
    assert grants == ["    ON \\`$TEST_DATABASE\\`.* TO '$TEST_USER'@'%';"]
    assert "ON *.*" not in block
    assert "GRANT ALL ON \\`$TEST_DATABASE\\`.* TO '$TEST_ADMIN'@'%';" in block


def test_the_admin_url_names_the_admin_login(monkeypatch: pytest.MonkeyPatch) -> None:
    passwords = load("passwords")
    monkeypatch.setattr(passwords.keyring, "get_password", lambda service, name: f"Pt{name}")
    found = passwords.environment()
    assert found["PROBEOWL_TEST_MSSQL_ADMIN_URL"].startswith(
        "mssql+mssqlpython://probeowl_test_admin:Pttest_mssql_admin@127.0.0.1:1433/probeowl_test"
    )
    assert found["PROBEOWL_TEST_MSSQL_URL"].startswith("mssql+mssqlpython://probeowl_test:")
    assert found["PROBEOWL_TEST_MARIADB_ADMIN_URL"].startswith(
        "mariadb+pymysql://probeowl_test_admin:Pttest_mariadb_admin@127.0.0.1:3306/probeowl_test"
    )
    assert found["PROBEOWL_TEST_ORACLE_ADMIN_URL"].startswith(
        "oracle+oracledb://probeowl_test_admin:Pttest_oracle_admin@127.0.0.1:1521/"
    )
    assert names()["TEST_ADMIN"] == "probeowl_test_admin"


def test_verify_warns_on_another_edition_and_holds_back_plan_rows() -> None:
    verify = (NATIVE / "verify.sh").read_text(encoding="utf-8")
    assert '*"Enterprise Developer"*) ;;' in verify
    assert "do not record plan-check rows as verified from this run" in verify
    assert "except plan-check rows, until the edition is Enterprise Developer" in verify
    # A warning, not a refusal: the run goes on.
    block = verify.split('*"Enterprise Developer"*) ;;', 1)[1].split("esac", 1)[0]
    assert "stop " not in block


# --- Passwords -------------------------------------------------------------------------------


def test_only_the_passwords_saved_are_exported(monkeypatch: pytest.MonkeyPatch) -> None:
    passwords = load("passwords")
    saved = {"test_mssql": "Pt1", "ro": "Pt2", "rw": "Pt3", "mssql_admin": "Pt4"}
    monkeypatch.setattr(passwords.keyring, "get_password", lambda service, name: saved.get(name))
    found = passwords.environment()
    assert found["PROBEOWL_TEST_MSSQL_URL"] == (
        "mssql+mssqlpython://probeowl_test:Pt1@127.0.0.1:1433/probeowl_test"
        "?TrustServerCertificate=yes"
    )
    assert found["NATIVE_MSSQL_ADMIN"] == "Pt4"
    assert found["PROBEOWL_NATIVE_RO_PASSWORD"] == "Pt2"
    assert "PROBEOWL_TEST_MARIADB_URL" not in found
    assert "NATIVE_MARIADB_ADMIN" not in found


def test_every_url_is_local_and_names_a_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    passwords = load("passwords")
    monkeypatch.setattr(passwords.keyring, "get_password", lambda service, name: "Pt0")
    urls = [v for k, v in passwords.environment().items() if k.startswith("PROBEOWL_TEST_")]
    assert len(urls) == 7
    assert all("@127.0.0.1:" in url and "probeowl_test" in url for url in urls)


def test_a_keychain_that_is_not_the_systems_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    passwords = load("passwords")

    class PlainText:
        pass

    PlainText.__module__ = "keyrings.alt.file"
    monkeypatch.setattr(passwords.keyring, "get_keyring", lambda: PlainText())
    with pytest.raises(SystemExit, match="not the system keychain"):
        passwords._refuse_an_insecure_keychain()


# --- The seeds -----------------------------------------------------------------------------------
# Rendered here, as each script would send them, from the scripts' own text: no stand-in program
# and no shell runs. Stand-ins on PATH look like malware to behaviour-based antivirus, and AGENTS.md
# forbids them.

_DEFAULTED = re.compile(r"\$\{(\w+):-([^}]*)\}")
_PLAIN = re.compile(r"\$\{(\w+)\}|\$(\w+)")


def shell_value(text: str, env: Mapping[str, str], assigned: Mapping[str, str]) -> str:
    """text with ${NAME:-default}, ${NAME} and $NAME expanded as bash would, for these scripts."""
    text = _DEFAULTED.sub(lambda m: env.get(m.group(1)) or m.group(2), text)
    return _PLAIN.sub(
        lambda m: assigned.get(m.group(1) or m.group(2), env.get(m.group(1) or m.group(2), "")),
        text,
    ).replace("\\`", "`")


def assignments(script: str, env: Mapping[str, str]) -> dict[str, str]:
    """Each NAME="..." the script sets, in order, as it would set it."""
    found: dict[str, str] = {}
    for line in script.splitlines():
        for name, value in re.findall(r'(?:^|\s)(\w+)="([^"]*)"', line.removeprefix("export ")):
            if name not in {"DIR", "CONNECT", "MYSQL"}:
                found[name] = shell_value(value, env, found)
    return found


def heredocs(script: str) -> list[str]:
    """The body of each <<SQL heredoc the script sends on stdin."""
    return re.findall(r"<<SQL\n(.*?)\nSQL\n", script, flags=re.S)


def seed_text(database: str) -> str:
    return (SEED / database / "seed.sh").read_text(encoding="utf-8").replace("\r\n", "\n")


def sql_file(database: str, name: str) -> str:
    return (SEED / database / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def sqlcmd_render(text: str, variables: Mapping[str, str]) -> str:
    """A script as sqlcmd sends it: $(NAME) scripting variables filled in."""
    return re.sub(r"\$\((\w+)\)", lambda m: variables[m.group(1)], text)


CUSTOM = {
    "SEED_PASSWORD": "admin",
    "SEED_RO_LOGIN": "probeowl_test_ro",
    "SEED_RW_LOGIN": "seed_test_rw",
    "SEED_RO_PASSWORD": RO_PASSWORD,
    "SEED_RW_PASSWORD": RW_PASSWORD,
}


def sqlserver(env: Mapping[str, str]) -> tuple[str, list[str]]:
    """What seed.sh sends SQL Server: its logins script, and each database it seeds."""
    script = seed_text("sqlserver")
    variables = assignments(script, env)
    databases = [shell_value(d, env, variables) for d in re.findall(r'-v DB="([^"]+)"', script)]
    rendered = sqlcmd_render(sql_file("sqlserver", "logins.sql"), variables)
    for database in databases:
        rendered += sqlcmd_render(
            sql_file("sqlserver", "wallet.sql"), {**variables, "DB": database}
        )
    return rendered, databases


def test_sql_server_defaults_are_what_docker_uses() -> None:
    rendered, databases = sqlserver({})
    assert databases == ["wallet", "wallet_uat"]
    assert "CREATE LOGIN [probeowl_ro] WITH PASSWORD = 'ProbeOwl_Ro_Local1'" in rendered
    assert "CREATE LOGIN [seed_rw] WITH PASSWORD = 'Seed_Rw_Local1'" in rendered
    assert "GRANT SHOWPLAN TO [probeowl_ro];" in rendered
    assert "ALTER ROLE db_datawriter ADD MEMBER [seed_rw];" in rendered


def test_sql_server_settings_reach_every_statement() -> None:
    env = {**CUSTOM, "SEED_DB": "wallet_test", "SEED_DB_UAT": "wallet_uat_test"}
    rendered, databases = sqlserver(env)
    assert databases == ["wallet_test", "wallet_uat_test"]
    assert f"CREATE LOGIN [probeowl_test_ro] WITH PASSWORD = '{RO_PASSWORD}'" in rendered
    assert "GRANT SHOWPLAN TO [probeowl_test_ro];" in rendered
    assert "probeowl_ro]" not in rendered
    assert "seed_rw]" not in rendered


def test_sql_server_passwords_never_go_in_an_argument() -> None:
    # Any process on the machine can read another's arguments; sqlcmd reads its environment.
    for line in seed_text("sqlserver").splitlines():
        if line.lstrip().startswith("$SQLCMD"):
            assert "PASSWORD" not in line


@pytest.mark.parametrize("name", ["logins.sql", "wallet.sql"])
def test_sql_server_scripts_hard_code_no_login(name: str) -> None:
    text = sql_file("sqlserver", name)
    assert "probeowl_ro" not in text
    assert "seed_rw" not in text
    assert "ProbeOwl_Ro_Local1" not in text


def mariadb(env: Mapping[str, str]) -> tuple[str, list[str]]:
    """What seed.sh sends MariaDB: its users on stdin, and each database it creates and grants."""
    script = seed_text("mariadb")
    variables = assignments(script, env)
    users = "\n".join(shell_value(body, env, variables) for body in heredocs(script))
    loop = re.search(r"for db in (.+); do\n(.*?)\ndone", script, flags=re.S)
    assert loop is not None
    databases = [shell_value(d.strip('"'), env, variables) for d in loop.group(1).split()]
    statements = [
        shell_value(text, env, {**variables, "db": database})
        for database in databases
        for text in re.findall(r'-e "([^"]+)"', loop.group(2))
    ]
    return "\n".join([users, *statements]), databases


def test_mariadb_defaults_are_what_docker_uses() -> None:
    rendered, databases = mariadb({})
    assert databases == ["access", "access_uat"]
    assert "'probeowl_ro'@'%' IDENTIFIED BY 'ProbeOwl_Ro_Local1'" in rendered
    assert "'seed_rw'@'%' IDENTIFIED BY 'Seed_Rw_Local1'" in rendered
    assert "CREATE DATABASE `access`" in rendered
    assert "GRANT SELECT ON `access_uat`.* TO 'probeowl_ro'@'%'" in rendered


def test_mariadb_settings_reach_every_statement() -> None:
    env = {**CUSTOM, "SEED_DB": "access_test", "SEED_DB_UAT": "access_uat_test"}
    rendered, databases = mariadb(env)
    assert databases == ["access_test", "access_uat_test"]
    assert f"'probeowl_test_ro'@'%' IDENTIFIED BY '{RO_PASSWORD}'" in rendered
    assert "GRANT SELECT ON `access_test`.* TO 'probeowl_test_ro'@'%'" in rendered
    assert "TO 'seed_test_rw'@'%'" in rendered
    assert "probeowl_ro'" not in rendered


def test_mariadb_passwords_go_on_stdin_never_in_an_argument() -> None:
    script = seed_text("mariadb")
    assert "SEED_RO_PASSWORD" in "".join(heredocs(script))
    for line in script.splitlines():
        if "-e " in line:
            assert "PASSWORD" not in line


def test_every_mariadb_client_verifies_the_servers_certificate() -> None:
    root = (NATIVE / "mariadb_root.sh").read_text(encoding="utf-8")
    assert "mariadb --ssl-verify-server-cert -h 127.0.0.1 -P 3306 -uroot" in root
    assert 'grep -q "ssl-verify-server-cert is disabled"' in root
    for script in (NATIVE / "verify.sh", NATIVE / "create_test_users.sh"):
        text = script.read_text(encoding="utf-8")
        assert 'source "$HERE/mariadb_root.sh"' in text
        # Every other way to call the client, which would verify nothing without saying so.
        assert "MYSQL_PWD" not in text
        assert not re.search(r"(^|[\s(])mariadb\s+-", text, flags=re.M), script.name
    seed = seed_text("mariadb")
    assert 'MYSQL+=(--ssl-verify-server-cert -h "$SEED_HOST"' in seed
    assert 'grep -q "ssl-verify-server-cert is disabled"' in seed


def test_verify_says_the_mariadb_version_and_its_settings() -> None:
    verify = (NATIVE / "verify.sh").read_text(encoding="utf-8")
    assert "SELECT VERSION(), @@lower_case_table_names, @@have_ssl" in verify
    assert "if uses mariadb; then check_mariadb; fi" in verify
    # Each test's name in the log, passed or skipped.
    assert "-q -rA --no-header" in verify
    # One log per set of databases, so a MariaDB run and a SQL Server run keep their own.
    assert 'logs/database-tests-${ONLY//,/-}-output.log"' in verify


def test_each_wsl_step_says_it_started_and_how_wsl_exited() -> None:
    # A WSL call that fails before its command starts leaves only WSL's own messages in the log.
    verify = (NATIVE / "verify.sh").read_text(encoding="utf-8")
    block = verify.split("in_ubuntu() {", 1)[1].split("\n}\n", 1)[0]
    assert "echo 'started in $UBUNTU_DISTRO: $name' && $command" in block
    assert 'echo "wsl.exe exited $code for $name"' in block
    # Written straight to a log file, wsl.exe loses one of its streams: each goes through a pipe.
    assert "2> >(cat >&2) | cat" in block and "code=${PIPESTATUS[0]}" in block
    helper = (NATIVE / "oracle_sysdba.sh").read_text(encoding="utf-8")
    assert '"$ORACLE_ENV$1" 2> >(cat >&2) | cat' in helper
    assert 'return "${PIPESTATUS[0]}"' in helper
    for step in ("wsl_safety_tests() {", "wsl_driver_check() {"):
        body = verify.split(step, 1)[1].split("\n}\n", 1)[0]
        assert "in_ubuntu" in body and "wsl.exe" not in body, step
    # A name in the marker is inside single quotes: an apostrophe would end them.
    names = [line for line in verify.splitlines() if "in_ubuntu " in line and "'" in line]
    assert names == [], names


def test_the_old_mariadb_users_file_is_gone() -> None:
    assert not (SEED / "mariadb" / "users.sql").exists()


def oracle(env: Mapping[str, str]) -> tuple[str, list[str]]:
    """What seed.sh sends Oracle: its DEFINEs on stdin, and each script it runs, with arguments."""
    script = seed_text("oracle")
    variables = assignments(script, env)
    defines = "\n".join(shell_value(body, env, variables) for body in heredocs(script))
    runs = [
        shell_value(line.removeprefix("run ").replace('"', ""), env, variables)
        for line in script.splitlines()
        if line.startswith("run ")
    ]
    return defines, runs


def test_oracle_defaults_are_what_docker_uses() -> None:
    defines, runs = oracle({})
    assert "DEFINE schema = LEDGER\n" in defines
    assert "DEFINE schema_uat = LEDGER_UAT\n" in defines
    assert "DEFINE ro_login = probeowl_ro\n" in defines
    assert "DEFINE ro_password = ProbeOwl_Ro_Local1\n" in defines
    assert runs == [
        "ledger.sql LEDGER",
        "ledger.sql LEDGER_UAT",
        "drift.sql LEDGER_UAT",
        "grants.sql LEDGER probeowl_ro seed_rw",
        "grants.sql LEDGER_UAT probeowl_ro seed_rw",
    ]


def test_oracle_settings_reach_every_statement() -> None:
    env = {
        **CUSTOM,
        "SEED_ORACLE_SCHEMA": "LEDGER_TEST",
        "SEED_ORACLE_SCHEMA_UAT": "LEDGER_UAT_TEST",
    }
    defines, runs = oracle(env)
    assert f"DEFINE ro_password = {RO_PASSWORD}\n" in defines
    assert "DEFINE rw_login = seed_test_rw\n" in defines
    assert "drift.sql LEDGER_UAT_TEST" in runs
    assert "grants.sql LEDGER_TEST probeowl_test_ro seed_test_rw" in runs


def test_oracle_scripts_take_every_name_they_use() -> None:
    users = sql_file("oracle", "users.sql")
    for define in ("&schema ", "&schema_uat ", "&ro_login ", "&rw_login ", '"&ro_password"'):
        assert define in users
    assert "probeowl_ro" not in users + sql_file("oracle", "grants.sql")
    assert "LEDGER_UAT" not in sql_file("oracle", "drift.sql")


def test_oracle_never_echoes_a_password() -> None:
    text = sql_file("oracle", "users.sql")
    assert "SET VERIFY OFF" in text
    assert "ProbeOwl_Ro_Local1" not in text
    for line in seed_text("oracle").splitlines():
        if line.startswith("run ") or "sqlplus" in line:
            assert "PASSWORD" not in line


def test_no_test_changes_path() -> None:
    # AGENTS.md: never write executables or scripts named after real programs onto PATH in a test.
    # No test needs PATH changed at all, so none may change it.
    patterns = ('"PATH":', "'PATH':", 'setenv("PATH"', 'environ["PATH"] =', 'PATH="')
    for path in (ROOT / "backend" / "tests").glob("*.py"):
        if path.name == "test_native_setup.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert not [p for p in patterns if p in text], path.name
    own = Path(__file__).read_text(encoding="utf-8")
    body = own.split("def test_no_test_changes_path", 1)[0]
    assert not [p for p in patterns if p in body]


# --- The scripts ----------------------------------------------------------------------------


def git_bash() -> str:
    """Git's bash: on Windows, plain `bash` can be the one that opens WSL."""
    if sys.platform != "win32":
        found = shutil.which("bash")
    else:
        git = shutil.which("git")
        # git.exe sits in cmd\\ or mingw64\\bin\\ of Git for Windows, and bash.exe in its bin\\.
        candidates = [parent / "bin" / "bash.exe" for parent in Path(git).parents] if git else []
        found = next((str(c) for c in candidates if c.exists()), None)
    if found is None:
        pytest.skip("no Git Bash here, so the scripts cannot be syntax-checked")
    return found


@pytest.mark.parametrize(
    "script",
    [
        NATIVE / "verify.sh",
        NATIVE / "create_test_users.sh",
        NATIVE / "wsl_safety_tests.sh",
        NATIVE / "wsl_driver_check.sh",
        SEED / "sqlserver" / "seed.sh",
        SEED / "mariadb" / "seed.sh",
        SEED / "oracle" / "seed.sh",
        NATIVE / "mariadb_root.sh",
        NATIVE / "oracle_sysdba.sh",
        NATIVE / "postgresql.sh",
    ],
)
def test_each_script_parses(tmp_path: Path, script: Path) -> None:
    # bash -n reads the script and runs nothing.
    copy = tmp_path / "script.txt"
    copy.write_bytes(script.read_bytes().replace(b"\r\n", b"\n"))
    done = subprocess.run(
        [git_bash(), "-n", copy.as_posix()], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


def test_verify_says_to_name_the_commit_and_never_edits_the_repository() -> None:
    text = (NATIVE / "verify.sh").read_text(encoding="utf-8")
    header = "\n".join(text.splitlines()[:16])
    assert "name this commit in your pull request" in header
    assert "It never edits a file in the repository." in header
    assert "db-verification" not in text
    assert "> docs/" not in text


def test_the_exit_checks_scan_the_config_being_served() -> None:
    for name in ("m1_exit_check.sh", "m2_exit_check.sh"):
        text = (ROOT / "backend" / "ci" / name).read_text(encoding="utf-8")
        assert '"${PROBEOWL_CONFIG:-../examples}"' in text


def test_passwords_never_go_in_a_file_or_a_printed_line() -> None:
    text = (NATIVE / "passwords.py").read_text(encoding="utf-8")
    assert "open(" not in text
    assert "write_text" not in text
    verify = (NATIVE / "verify.sh").read_text(encoding="utf-8")
    assert 'echo "$exports"' not in verify
    assert "setx" not in verify


def test_nothing_on_the_windows_postgresql_server_changes_but_the_test_login_and_database() -> None:
    verify = (NATIVE / "verify.sh").read_text(encoding="utf-8")
    # verify.sh asks for the service to be started; it never starts, stops or configures it.
    assert 'start_windows_service "$POSTGRES_SERVICE"' not in verify
    assert 'start the $POSTGRES_SERVICE service"' in verify
    users = (NATIVE / "create_test_users.sh").read_text(encoding="utf-8").replace("\r\n", "\n")
    block = users.split("postgresql() {", 1)[1].split("\n}\n", 1)[0]
    for script in (verify, users):
        for change in ("ALTER SYSTEM", "pg_ctl", "pg_hba", "Restart-Service", "Stop-Service"):
            assert change not in script, change
    # Each statement names the test login or its database, or is a SET for this session only.
    sql = block.split("<<SQL", 1)[1].split("\nSQL", 1)[0]
    keyword = re.compile(r"(CREATE|ALTER|GRANT|REVOKE|DROP|SELECT|SET)\b")
    statements = [s.strip() for s in sql.splitlines() if keyword.match(s.strip())]
    assert len(statements) == 7, statements
    for statement in statements:
        named = "$TEST_USER" in statement or "$TEST_DATABASE" in statement
        assert statement.startswith("SET ") or named, statement
