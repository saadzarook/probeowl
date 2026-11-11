#!/bin/bash
# Author: @SaadZarook
# Verifies one commit against the native databases: the database tests, the m1 to m4 exit checks,
# and inside WSL the Linux-only safety tests and driver job. Run from Git Bash at the repo root:
#
#     bash dev/native/verify.sh [--tests] [--exit] [--wsl] [--live] [--dry-run] [--only DBS]
#
# With no step named it runs --tests, --exit and --wsl. --only mssql,mariadb limits the database
# tests to those databases, while the rest are still being installed; the exit checks need all of
# mssql, mariadb and oracle. --dry-run stops before reading a password
# or touching a database: it exports the commit, writes the configs and checks they are safe.
#
# It runs HEAD, exported with LF line endings to a folder of its own, and prints the result as
# "native <commit>": name this commit in your pull request. It never edits a file in the repository.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
# shellcheck source=dev/native/names.sh
source "$HERE/names.sh"
# shellcheck source=dev/native/oracle_sysdba.sh
source "$HERE/oracle_sysdba.sh"
# shellcheck source=dev/native/mariadb_root.sh
source "$HERE/mariadb_root.sh"
# shellcheck source=dev/native/postgresql.sh
source "$HERE/postgresql.sh"

RUN_TESTS="" RUN_EXIT="" RUN_WSL="" RUN_LIVE="" DRY_RUN="" ONLY="mssql,mariadb,oracle,postgresql"
while [ $# -gt 0 ]; do
  flag=$1
  shift
  case "$flag" in
    --only) ONLY="${1:?--only needs a list, such as mssql,mariadb}"; shift ;;
    --tests) RUN_TESTS=1 ;;
    --exit) RUN_EXIT=1 ;;
    --wsl) RUN_WSL=1 ;;
    --live) RUN_LIVE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown option $flag; see the top of this script" >&2; exit 2 ;;
  esac
done
if [ -z "$RUN_TESTS$RUN_EXIT$RUN_WSL$RUN_LIVE" ]; then RUN_TESTS=1 RUN_EXIT=1 RUN_WSL=1; fi
for db in ${ONLY//,/ }; do
  case "$db" in mssql | mariadb | oracle | postgresql) ;; *) echo "unknown database $db" >&2; exit 2 ;; esac
done
uses() { [[ ",$ONLY," == *",$1,"* ]]; }

RESULTS=()
SERVERS=()
WARNINGS=()
FAILED=0
KEEP_ALIVE=()
stop_ports() {
  for port in "$@"; do
    powershell.exe -NoProfile -Command "Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force -ErrorAction SilentlyContinue }" >/dev/null 2>&1
  done
}
cleanup() {
  stop_ports 7878 7879 8081 8082 4173
  for pid in ${KEEP_ALIVE[@]+"${KEEP_ALIVE[@]}"}; do kill "$pid" 2>/dev/null; done
}
trap cleanup EXIT

stop() { echo "STOP: $*" >&2; exit 1; }

# One step, its output in its own log. A failure is recorded and the others still run.
step() {
  local name=$1
  shift
  local log="$WORK/logs/$name.log"
  echo "== $name"
  if "$@" >"$log" 2>&1; then
    RESULTS+=("pass  $name")
  else
    RESULTS+=("FAIL  $name  (log: $log)")
    FAILED=1
    tail -20 "$log"
  fi
}

wait_for_port() {
  local port=$1
  for _ in $(seq 1 "${2:-60}"); do
    if powershell.exe -NoProfile -Command "if (Test-NetConnection 127.0.0.1 -Port $port -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 0 } else { exit 1 }" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

prepare() {
  command -v git >/dev/null || stop "git is not on PATH"
  command -v uv >/dev/null || export PATH="$PATH:$(cygpath -u "$APPDATA")/Python/Python314/Scripts"
  command -v uv >/dev/null || stop "uv is not on PATH"
  if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
    # It runs the commit, so uncommitted work would be left out of what it claims to verify.
    [ -n "$DRY_RUN" ] && echo "note: the tree has uncommitted changes, which a real run refuses" \
      || stop "commit or stash your changes: this verifies HEAD"
  fi
  COMMIT="$(git -C "$REPO" rev-parse --short HEAD)"
  WORK="$(cygpath -m "${TMP:-/tmp}")/probeowl-native/$COMMIT"
  rm -rf "$WORK"
  mkdir -p "$WORK/logs" "$WORK/tree"
  # autocrlf would give the exit checks CRLF, which sh cannot run.
  git -C "$REPO" -c core.autocrlf=false archive HEAD | tar -x -C "$WORK/tree"
  BACKEND="$WORK/tree/backend"
  (cd "$BACKEND" && uv sync --locked --quiet) || stop "uv sync failed"
  for copy in good bad; do
    local flag=""
    [ "$copy" = bad ] && flag="--bad"
    # shellcheck disable=SC2086
    (cd "$BACKEND" && uv run python ../dev/native/make_config.py ../examples "$WORK/config-$copy" $flag) \
      || stop "the $copy config is not safe to run against"
  done
  echo "commit $COMMIT, in $WORK; both configs bind only to local test databases"
}

read_passwords() {
  local exports
  exports="$(cd "$BACKEND" && uv run python ../dev/native/passwords.py env)" || stop "$exports"
  eval "$exports"
  # What crosses into a WSL distro, untranslated.
  export WSLENV="SEED_RO_PASSWORD/u:SEED_RW_PASSWORD/u:SEED_RO_LOGIN/u:SEED_RW_LOGIN/u:SEED_ORACLE_SCHEMA/u:SEED_ORACLE_SCHEMA_UAT/u:MSSQL_PW/u:MSSQL_USER/u"
}

# Windows services need an elevated prompt to start, so this asks once for each that is down.
start_windows_service() {
  local name=$1 port=$2
  wait_for_port "$port" 1 && return 0
  echo "starting $name: approve the prompt"
  powershell.exe -NoProfile -Command "Start-Process net -ArgumentList 'start','$name' -Verb RunAs -Wait"
  wait_for_port "$port" 60 || stop "$name did not start on port $port"
}

start_wsl_service() {
  local distro=$1 service=$2 port=$3
  # WSL stops an idle distro, and its services with it.
  wsl.exe -d "$distro" -- sleep 14400 &
  KEEP_ALIVE+=("$!")
  wait_for_port "$port" 1 && return 0
  wsl.exe -d "$distro" -u root -- systemctl start "$service" || stop "$service did not start in $distro"
  wait_for_port "$port" 120 || stop "$service in $distro did not listen on port $port"
}

start_services() {
  if [ -n "$RUN_EXIT$RUN_LIVE" ] && ! { uses mssql && uses mariadb && uses oracle; }; then
    stop "the exit checks need mssql, mariadb and oracle: leave out --only, or run only --tests"
  fi
  uses mssql && start_windows_service MSSQLSERVER 1433
  uses mariadb && start_windows_service MariaDB 3306
  uses oracle && start_wsl_service "$ORACLE_DISTRO" oracle-free-26ai 1521
  # It is your own service, so this asks you to start it rather than starting it.
  if uses postgresql && ! wait_for_port "$POSTGRES_PORT" 1; then
    stop "nothing answers on 127.0.0.1:$POSTGRES_PORT: start the $POSTGRES_SERVICE service"
  fi
  return 0
}

# A copy of the commit on a distro's own disk, with Linux line endings and file modes.
to_distro() {
  local distro=$1 user=$2
  git -C "$REPO" -c core.autocrlf=false archive HEAD | wsl.exe -d "$distro" -u "$user" -- bash -c \
    "rm -rf ~/probeowl-verify/$COMMIT && mkdir -p ~/probeowl-verify/$COMMIT && tar -x -C ~/probeowl-verify/$COMMIT"
}

# Every name these drop is in names.sh and contains "test".
reseed_sqlserver() {
  export SQLCMDPASSWORD="$NATIVE_MSSQL_ADMIN"
  for db in "$SQLSERVER_DB" "$SQLSERVER_DB_UAT"; do
    sqlcmd -C -b -S 127.0.0.1,1433 -U sa -Q \
      "IF DB_ID('$db') IS NOT NULL BEGIN ALTER DATABASE [$db] SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE [$db]; END" || return 1
  done
  SEED_PASSWORD="$NATIVE_MSSQL_ADMIN" SEED_DB="$SQLSERVER_DB" SEED_DB_UAT="$SQLSERVER_DB_UAT" \
    SEED_RO_LOGIN="$RO_LOGIN" SEED_RW_LOGIN="$RW_LOGIN" \
    bash "$WORK/tree/dev/seed/sqlserver/seed.sh" || return 1
  # 2022's level on a 2025 server, so plans match CI's. The seed itself stays as Docker runs it.
  for db in "$SQLSERVER_DB" "$SQLSERVER_DB_UAT"; do
    sqlcmd -C -b -S 127.0.0.1,1433 -U sa \
      -Q "ALTER DATABASE [$db] SET COMPATIBILITY_LEVEL = $SQLSERVER_COMPAT" || return 1
  done
  check_sqlserver "$SQLSERVER_DB" "$SQLSERVER_DB_UAT"
}

# The server's version, and each test database's level: a run at another level is refused, since
# its plans would not be CI's.
check_sqlserver() {
  export SQLCMDPASSWORD="$NATIVE_MSSQL_ADMIN"
  local version level
  version="$(sqlcmd -C -b -S 127.0.0.1,1433 -U sa -h -1 -W \
    -Q "SET NOCOUNT ON; SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(20)) + ' ' + CAST(SERVERPROPERTY('Edition') AS varchar(60))" \
    | tr -d '\r' | head -1)" || stop "SQL Server did not say its version"
  SQL_SERVER_VERSION="$version"
  # Standard Developer plans as Standard does, so plan checks prove nothing about Enterprise.
  case "$version" in
    *"Enterprise Developer"*) ;;
    *)
      WARNINGS+=("SQL Server is ${version#* }, not Enterprise Developer Edition: do not record plan-check rows as verified from this run")
      PLANS_UNVERIFIED=1
      ;;
  esac
  for db in "$@"; do
    level="$(sqlcmd -C -b -S 127.0.0.1,1433 -U sa -h -1 -W \
      -Q "SET NOCOUNT ON; SELECT compatibility_level FROM sys.databases WHERE name = '$db'" \
      | tr -d '\r' | head -1)"
    SERVERS+=("SQL Server $version: $db at compatibility level ${level:-missing}")
    [ "$level" = "$SQLSERVER_COMPAT" ] ||
      stop "$db is at compatibility level ${level:-missing}, not $SQLSERVER_COMPAT: run create_test_users.sh mssql"
  done
}

# The server's version and the settings that make it differ from CI's container, for the record.
check_mariadb() {
  local found version case_setting ssl
  found="$(mariadb_root -N -B \
    -e "SELECT VERSION(), @@lower_case_table_names, @@have_ssl" | tr -d '\r' | head -1)" ||
    stop "MariaDB did not say its version"
  read -r version case_setting ssl <<<"$found"
  [ -n "${ssl:-}" ] || stop "MariaDB did not say its version"
  SERVERS+=("MariaDB $version: lower_case_table_names $case_setting, have_ssl $ssl")
  # CI runs MariaDB on Linux, where table names are case-sensitive (0); Windows stores them in
  # lower case (1), so a test of a name's case proves less here.
  [ "$case_setting" = 0 ] ||
    WARNINGS+=("MariaDB has lower_case_table_names $case_setting, CI's container 0: table names are not case-sensitive here")
  [ "$ssl" = YES ] || WARNINGS+=("MariaDB has have_ssl $ssl: the encryption tests cannot pass")
}

# The server's version, edition and PDB, for the record; then that the listener is where the tests
# connect and that each test login can sign in, so neither fails as an error per test.
check_oracle() {
  local found version edition listener
  oracle_wait_open >/dev/null || stop "FREEPDB1 is not open READ WRITE in $ORACLE_DISTRO"
  found="$(oracle_sysdba <<'SQL' | tr -d '\r' | grep -v '^$' | head -1
SET HEADING OFF FEEDBACK OFF PAGESIZE 0
SELECT i.version_full || ' ' || i.edition || ' ' || p.open_mode
FROM v$instance i CROSS JOIN v$pdbs p WHERE p.name = 'FREEPDB1';
EXIT
SQL
)" || stop "Oracle did not say its version"
  read -r version edition _ <<<"$found"
  [ -n "${edition:-}" ] || stop "Oracle did not say its version"
  listener="$(oracle_run 'lsnrctl status' 2>/dev/null | tr -d '\r')"
  if ! grep -qF '(HOST=127.0.0.1)(PORT=1521)' <<<"$listener" ||
    ! grep -qiF 'Service "freepdb1"' <<<"$listener"; then
    stop "Oracle's listener is not on 127.0.0.1:1521 for FREEPDB1: set HOST = 127.0.0.1 and PORT = 1521 in listener.ora and tnsnames.ora (dev/native/README.md)"
  fi
  wait_for_port 1521 5 || stop "127.0.0.1:1521 is not reachable from Windows: is WSL's localhost forwarding on?"
  SERVERS+=("Oracle $version $edition: FREEPDB1 READ WRITE, listener on 127.0.0.1:1521")
  (cd "$BACKEND" && uv run python ../dev/native/check_sign_in.py oracle) ||
    stop "run create_test_users.sh oracle first"
}

# The Windows PostgreSQL service: that it listens on loopback only, both as Windows sees the port
# and as the server's listen_addresses says, its version, and that the test login can sign in.
check_postgresql() {
  local listeners found version listen port
  # Before anything signs in: every address Windows has listening on the port.
  listeners="$(powershell.exe -NoProfile -Command "(Get-NetTCPConnection -LocalPort $POSTGRES_PORT -State Listen -ErrorAction SilentlyContinue).LocalAddress -join ','" | tr -d '\r')"
  loopback_only "$listeners" ||
    stop "port $POSTGRES_PORT listens on '$listeners': PostgreSQL must listen on 127.0.0.1 and ::1 only (listen_addresses in its postgresql.conf)"
  (cd "$BACKEND" && uv run python ../dev/native/check_sign_in.py postgresql) ||
    stop "run create_test_users.sh postgresql first"
  # As the test login, which may read these settings. '|' because listen_addresses can hold spaces.
  found="$(pg_psql "$NATIVE_TEST_POSTGRESQL_PASSWORD" "$TEST_USER" "$TEST_DATABASE" -A -t -F '|' \
    -c "SELECT split_part(current_setting('server_version'), ' ', 1), current_setting('listen_addresses'), current_setting('port')" \
    2>/dev/null | tr -d '\r' | grep -v '^$' | head -1)" ||
    stop "PostgreSQL did not answer on 127.0.0.1:$POSTGRES_PORT: see dev/native/README.md"
  IFS='|' read -r version listen port <<<"$found"
  [ -n "${port:-}" ] || stop "PostgreSQL did not answer on 127.0.0.1:$POSTGRES_PORT"
  loopback_only "$listen" ||
    stop "PostgreSQL's listen_addresses is '$listen': set it to '127.0.0.1,::1' in its postgresql.conf"
  [ "$port" = "$POSTGRES_PORT" ] || stop "PostgreSQL is on port $port, not $POSTGRES_PORT"
  case "$version" in 16.*) ;; *) WARNINGS+=("PostgreSQL is $version, CI's container 16") ;; esac
  SERVERS+=("PostgreSQL $version: listen_addresses $listen, port $POSTGRES_PORT, Windows listeners $listeners")
}

reseed_mariadb() {
  mariadb_root -e "DROP DATABASE IF EXISTS \`$MARIADB_DB\`; DROP DATABASE IF EXISTS \`$MARIADB_DB_UAT\`" || return 1
  SEED_PASSWORD="$NATIVE_MARIADB_ADMIN" SEED_HOST=127.0.0.1 SEED_DB="$MARIADB_DB" SEED_DB_UAT="$MARIADB_DB_UAT" \
    SEED_RO_LOGIN="$RO_LOGIN" SEED_RW_LOGIN="$RW_LOGIN" \
    bash "$WORK/tree/dev/seed/mariadb/seed.sh"
}

reseed_oracle() {
  to_distro "$ORACLE_DISTRO" oracle || return 1
  export SEED_RO_LOGIN="$RO_LOGIN" SEED_RW_LOGIN="$RW_LOGIN" \
    SEED_ORACLE_SCHEMA="$ORACLE_SCHEMA" SEED_ORACLE_SCHEMA_UAT="$ORACLE_SCHEMA_UAT"
  oracle_sysdba <<SQL || return 1
WHENEVER SQLERROR EXIT FAILURE
ALTER SESSION SET CONTAINER = FREEPDB1;
BEGIN
  FOR u IN (SELECT username FROM dba_users WHERE username IN
            (UPPER('$ORACLE_SCHEMA'), UPPER('$ORACLE_SCHEMA_UAT'), UPPER('$RO_LOGIN'), UPPER('$RW_LOGIN'))) LOOP
    EXECUTE IMMEDIATE 'DROP USER "' || u.username || '" CASCADE';
  END LOOP;
END;
/
EXIT
SQL
  oracle_run "cd ~/probeowl-verify/$COMMIT && bash dev/seed/oracle/seed.sh"
}

serve() {
  local config=$1 port=$2 store=$3
  (cd "$BACKEND" && PROBEOWL_STORE_URL="$store" PROBEOWL_WEB_ORIGINS="http://127.0.0.1:4173,http://localhost:4173" \
    nohup .venv/Scripts/owl serve --config "$config" --port "$port" >"$WORK/logs/serve-$port.log" 2>&1 &)
  wait_for_port "$port" 60
}

start_fakes() {
  (cd "$WORK/tree" && nohup uv run dev/fakes/payments.py >"$WORK/logs/payments.log" 2>&1 &)
  (cd "$WORK/tree" && nohup uv run dev/fakes/door_controller.py >"$WORK/logs/door.log" 2>&1 &)
  wait_for_port 8081 60 && wait_for_port 8082 60
}

# Each check gets a fresh store and its own copy of the config, as its CI job does.
exit_check() {
  local name=$1 config="$WORK/config-$1" store="sqlite:///$WORK/store-$1.db"
  rm -rf "$config"
  (cd "$BACKEND" && uv run python ../dev/native/make_config.py ../examples "$config") || return 1
  (cd "$BACKEND" && PROBEOWL_STORE_URL="$store" uv run owl store init) || return 1
  serve "$config" 7878 "$store" || return 1
  local code=0
  (
    cd "$BACKEND"
    export PROBEOWL_STORE_URL="$store" PROBEOWL_CONFIG="$config"
    case "$name" in
      m1) sh ci/m1_exit_check.sh ;;
      m2) sh ci/m2_exit_check.sh ;;
      m3)
        bad="$WORK/config-m3-bad"
        rm -rf "$bad"
        uv run python ../dev/native/make_config.py ../examples "$bad" --bad || exit 1
        (cd "$BACKEND" && serve "$bad" 7879 "$store") || exit 1
        PROBEOWL_M3_CONFIG="$config" PROBEOWL_M3_BAD_CONFIG="$bad" sh ci/m3_exit_check.sh
        ;;
      m4) uv run python ci/m4_exit_check.py "$store" ;;
    esac
  ) || code=$?
  stop_ports 7878 7879
  return "$code"
}

database_tests() {
  # One log per set of databases, so a run of one dialect never overwrites another's.
  local log="$WORK/logs/database-tests-${ONLY//,/-}-output.log"
  local only="${ONLY//,/ or }"
  (cd "$BACKEND" && CI=false uv run pytest -m database -k "$only" -q -rA --no-header) >"$log" 2>&1
  local code=$?
  cat "$log"
  # A database it could not reach would otherwise skip, and read as a pass.
  if grep -E 'is not set, so the database tests' "$log" | grep -Eq "${ONLY//,/|}"; then
    echo "FAIL: a database's tests skipped"
    return 1
  fi
  return "$code"
}

# A WSL call can fail before its command starts, leaving nothing in the log but WSL's own
# messages. So each says when it started inside the distro and how wsl.exe exited, and a log
# tells that apart from the step itself failing.
in_ubuntu() {
  local user=$1 name=$2 command=$3 code
  # Each stream through its own pipe: written straight to a step's log file, wsl.exe loses one
  # of them (seen at 9e0d297, a log with nothing but WSL's own message).
  wsl.exe -d "$UBUNTU_DISTRO" -u "$user" -- bash -lc \
    "echo 'started in $UBUNTU_DISTRO: $name' && $command" 2> >(cat >&2) | cat
  code=${PIPESTATUS[0]}
  [ "$code" -eq 0 ] || echo "wsl.exe exited $code for $name"
  return "$code"
}

wsl_safety_tests() {
  to_distro "$UBUNTU_DISTRO" "$WSL_USER" || return 1
  in_ubuntu "$WSL_USER" wsl_safety_tests.sh \
    "cd ~/probeowl-verify/$COMMIT && bash dev/native/wsl_safety_tests.sh"
}

wsl_driver_check() {
  # The three packages the job's script names, and nothing else.
  in_ubuntu root "driver check packages" \
    "DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libltdl7 libkrb5-3 libgssapi-krb5-2 >/dev/null" ||
    return 1
  MSSQL_USER="$TEST_USER" MSSQL_PW="$NATIVE_TEST_MSSQL_PASSWORD" in_ubuntu "$WSL_USER" \
    wsl_driver_check.sh "cd ~/probeowl-verify/$COMMIT && bash dev/native/wsl_driver_check.sh"
}

live_e2e() {
  local config="$WORK/config-live" store="sqlite:///$WORK/store-live.db"
  (cd "$BACKEND" && uv run python ../dev/native/make_config.py ../examples "$config") || return 1
  (cd "$BACKEND" && PROBEOWL_STORE_URL="$store" uv run owl store init) || return 1
  serve "$config" 7878 "$store" || return 1
  (
    cd "$WORK/tree/web" || exit 1
    npm ci --silent && npm run -s build || exit 1
    nohup npx vite preview --host 127.0.0.1 --port 4173 >"$WORK/logs/preview.log" 2>&1 &
    wait_for_port 4173 60 || exit 1
    npx playwright test -c playwright.live.config.ts --workers=1
  )
  local code=$?
  stop_ports 7878 4173
  return "$code"
}

prepare
if [ -n "$DRY_RUN" ]; then
  echo "dry run: stopping before any password or database"
  exit 0
fi
read_passwords
start_services
WSL_USER="$(wsl.exe -d "$UBUNTU_DISTRO" -- whoami | tr -d '\r\0')"

if [ -n "$RUN_EXIT$RUN_LIVE" ]; then
  # Card dates are relative to the day of seeding, so the planted data starts fresh each run.
  step reseed-sqlserver reseed_sqlserver
  step reseed-mariadb reseed_mariadb
  step reseed-oracle reseed_oracle
  step fake-apis start_fakes
fi
if [ -n "$RUN_TESTS" ] && uses mssql; then check_sqlserver "$TEST_DATABASE"; fi
if uses mariadb; then check_mariadb; fi
if uses oracle; then check_oracle; fi
if uses postgresql; then check_postgresql; fi
[ -n "$RUN_TESTS" ] && step "database-tests-${ONLY//,/-}" database_tests
if [ -n "$RUN_EXIT" ]; then
  for check in m1 m2 m3 m4; do step "exit-$check" exit_check "$check"; done
fi
if [ -n "$RUN_WSL" ]; then
  step wsl-safety-tests wsl_safety_tests
  step wsl-driver-check wsl_driver_check
fi
[ -n "$RUN_LIVE" ] && step live-e2e live_e2e

echo
echo "commit $COMMIT"
printf '%s\n' ${SERVERS[@]+"${SERVERS[@]}"}
for warning in ${WARNINGS[@]+"${WARNINGS[@]}"}; do echo "WARNING: $warning"; done
printf '%s\n' "${RESULTS[@]}"
echo "logs: $WORK/logs"
if [ "$FAILED" -eq 0 ]; then
  echo "Record passing rows as: native $COMMIT"
  if [ -n "${SQL_SERVER_VERSION:-}" ]; then
    year="SQL Server 2025"
    case "$SQL_SERVER_VERSION" in 16.*) year="SQL Server 2022" ;; 17.*) year="SQL Server 2025" ;; *) year="SQL Server $SQL_SERVER_VERSION" ;; esac
    echo "and rows about SQL Server as: native $year (compat $SQLSERVER_COMPAT) $COMMIT"
    [ -n "${PLANS_UNVERIFIED:-}" ] && echo "except plan-check rows, until the edition is Enterprise Developer"
  fi
fi
exit "$FAILED"
