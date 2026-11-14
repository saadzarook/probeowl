# Native verification, without Docker or Actions minutes

Runs the database tests and the m1 to m4 exit checks against real databases on this Windows
machine, and the Linux-only safety tests and driver job inside WSL. `examples/` and the Docker
stack are unchanged: this uses copies with "test" in every name.

| Database | Runs | Port | Admin | Tests use | Planted data |
| --- | --- | --- | --- | --- | --- |
| SQL Server 2025 Enterprise Developer, at compatibility level 160 | Windows service `MSSQLSERVER` | 1433 | `sa` | `probeowl_test` | `wallet_test`, `wallet_uat_test` |
| MariaDB 11 LTS | Windows service `MariaDB` | 3306 | `root` | `probeowl_test` | `access_test`, `access_uat_test` |
| Oracle AI Database 26ai Free (23.26.3) | WSL `OracleLinux_9_5` | 1521 | OS auth as `oracle` | `PROBEOWL_TEST` in `FREEPDB1` | schemas `LEDGER_TEST`, `LEDGER_UAT_TEST` |
| PostgreSQL 16 | Windows service `postgresql-x64-16` | 5432 | `postgres` | `probeowl_test` | none: only the store tests use it |

Logins for the planted data are `probeowl_test_ro` and `seed_test_rw`. All names are in
`names.sh`. On the PostgreSQL service the setup makes only the `probeowl_test` login and its
database, and changes nothing else.

CI runs SQL Server 2022, and Microsoft now offers only 2025 to download. Every SQL Server test
database is held at compatibility level 160, 2022's, so query plans match CI's: `create_test_users.sh`
sets it on `probeowl_test`, and `verify.sh` on the planted databases after each reseed. `verify.sh`
prints the server's version and each level, and refuses to run if one is not 160. On an edition other
than Enterprise Developer it warns, and plan-check rows are not recorded as verified from that run:
Standard Developer plans as Standard does.

On SQL Server, ProbeOwl and the tests connect as different logins. `probeowl_test` is what ProbeOwl
connects as, with what a Production read login has and no server grant: read and write in
`probeowl_test`, `SHOWPLAN`, and `db_ddladmin` with `REFERENCES` for the store tests, which create
ProbeOwl's own tables there. `probeowl_test_admin` is the tests' own, for setup, checks and cleanup,
with what `sa` gives them in CI: `ALTER ANY LOGIN`, `VIEW SERVER PERFORMANCE STATE` and
`ALTER ANY CONNECTION`. A test checks that ProbeOwl's login holds none of those. Its URL is
`PROBEOWL_TEST_MSSQL_ADMIN_URL`. `create_test_users.sh mssql` also drops any login an interrupted
test left behind.

MariaDB splits the same way. `probeowl_test` has nothing server-wide, only reading, writing and the
store tests' DDL in `probeowl_test`; `probeowl_test_admin` has all of `probeowl_test`, and its URL is
`PROBEOWL_TEST_MARIADB_ADMIN_URL`. A test checks ProbeOwl's login has only `USAGE` on `*.*`.

Oracle too. `probeowl_test` can sign in and create tables and sequences in its own schema, and
nothing else: not `v$session`, which CI's one test login is granted. `probeowl_test_admin` sets up,
locks and drops the tests' tables in that schema and reads `v$session`; its URL is
`PROBEOWL_TEST_ORACLE_ADMIN_URL`. Its `ANY` privileges are schema privileges, on `PROBEOWL_TEST`
only (23ai and later), including `CREATE ANY INDEX` for a primary key's index, which 23ai needs to
create a table with one in another schema (ORA-41900). `create_test_users.sh oracle` takes back the
system-wide grants an earlier version gave. A test checks ProbeOwl's login has no other privilege or
grant, and another that the admin holds no `ANY` privilege on every schema.

Every Oracle call sets `ORACLE_HOME` (read from `/etc/oratab`), `ORACLE_SID=FREE` and `PATH` itself,
through `oracle_sysdba.sh`, so nothing depends on the `oracle` user's profile or a wrapper on `PATH`.
`verify.sh` waits for `FREEPDB1` to open, prints Oracle's version, edition and PDB, and stops if the
listener is not on 127.0.0.1:1521 for `FREEPDB1` or a test login cannot sign in.

Every `mariadb` client call verifies the server's certificate. MariaDB 11.4 and later do that by
default, but a password read only from `MYSQL_PWD` makes the client turn it off with "WARNING:
option --ssl-verify-server-cert is disabled, because of an insecure passwordless login". So the
scripts ask for `--ssl-verify-server-cert` by name, through `mariadb_root.sh`, and stop if the
client prints that warning anyway. `verify.sh` prints the MariaDB version, `lower_case_table_names`
and `have_ssl`, and warns where they differ from CI.

If its installer refuses Windows Home, SQL Server runs on Linux in WSL instead. Everything else stays
the same: its clients still reach it on 127.0.0.1:1433.

## Installing Oracle in WSL

What it took on this machine, for Oracle AI Database 26ai Free 23.26.3:

- `OracleLinux_9_5` is a legacy distro, so `wsl --install --location` is not supported: it goes
  where WSL puts it.
- The image has no `hostname` command, which the installer needs: `sudo dnf -y install hostname`.
- The listener failed with "No valid IP Address returned for the host": through WSL's DNS the
  distro's hostname resolved to the Windows host's IPv6 addresses. Add `127.0.0.1 <host>` and
  `::1 <host>` to `/etc/hosts`, and stop WSL rewriting it with `[network]` `generateHosts=false` in
  `/etc/wsl.conf`.
- After failed attempts, `configure` chose port 1539. Set `PORT = 1521` and `HOST = 127.0.0.1` in
  `listener.ora` and `tnsnames.ora`, and `LOCAL_LISTENER` to `127.0.0.1:1521`. `verify.sh` stops
  if the listener is anywhere else.
- "wsl: Failed to start the systemd user session" appears on each call. It is harmless: Oracle
  runs as a system service.

## Keeping Oracle running

WSL stops a distro a few seconds after its last `wsl.exe` session ends, and Oracle stops with it.
A run after that gets "the connection was refused or could not be made" on every Oracle location,
such as Wallet's `ledger`. `verify.sh` keeps the distro awake while it runs. For everything else,
`owl serve` and the web app included, keep it awake yourself in a window of its own:

```sh
bash dev/native/oracle_awake.sh
```

It starts `oracle-free-26ai` if it is down, waits for `FREEPDB1` to open, says so, and holds the
distro awake until you press Ctrl-C.

## PostgreSQL 16, the Windows service

The store tests use the existing Windows service `postgresql-x64-16`, which listens on 127.0.0.1
and ::1 only, port 5432. The setup adds one login and one database, both `probeowl_test`, and
changes nothing else on that server: no other database, role, setting or `pg_hba.conf` line.
`verify.sh` never starts or stops the service; if it is down, it says so.

Once, from `backend/`, save the `postgres` password you chose at install:

```sh
uv run python ../dev/native/passwords.py set postgresql_admin
```

Then from Git Bash at the repo root:

```sh
(cd backend && uv run python ../dev/native/passwords.py init)   # keeps the passwords that exist
eval "$(cd backend && uv run python ../dev/native/passwords.py env)"
bash dev/native/create_test_users.sh postgresql
bash dev/native/verify.sh --tests --only postgresql
```

`create_test_users.sh postgresql` signs in as `postgres` with the password from Credential Manager,
through `postgresql.sh`, which hands it to `psql.exe` in `PGPASSWORD` for that one call. It makes
the `probeowl_test` login with no superuser, `CREATEDB` or `CREATEROLE`, sets its password from the
keychain each time, and gives it its own database, which no other login may connect to. It turns
off statement logging and statement stats for its own session first, so the login's password stays
out of the server's log.

`verify.sh` stops unless every address Windows has listening on 5432 is a loopback one, and the
server's `listen_addresses` holds only 127.0.0.1, ::1 or localhost. It warns if the version is not
16, which CI runs, and checks the login can sign in.

## Passwords

They live in Windows Credential Manager under `probeowl-test`, never in a file. From `backend/`:

```sh
uv run python ../dev/native/passwords.py init               # the test logins
uv run python ../dev/native/passwords.py set mssql_admin    # asks for what you chose at install
uv run python ../dev/native/passwords.py set mariadb_admin
uv run python ../dev/native/passwords.py set postgresql_admin
```

`passwords.py env` prints them as shell exports. `verify.sh` reads that into its own process, so
nothing is left in your environment. For one shell, from `backend/`:

```sh
eval "$(uv run python ../dev/native/passwords.py env)"
```

To paste one into a database tool, copy it to the clipboard. Nothing here prints a password to the
terminal, and you shouldn't either: pipe what a keychain read returns to `Set-Clipboard`, `pbcopy` or
`xclip -selection clipboard`, never to the screen.

```sh
uv run python ../dev/native/passwords.py copy ro            # the read-only seed login's password
```

`verify.sh --first-run` saves passwords to the real keychain through the page, under test-only
names (`probeowl/probeowl_test_*`). It removes exactly those after the run, and at its start it
removes any that a run killed hard left behind, logging each name it removes, never a value
(`dev/native/keychain_test_entries.py`). It never touches any other entry.

## PROBEOWL_TEST_<DB>_URL

`passwords.py env` sets these, in this form:

```text
PROBEOWL_TEST_MSSQL_URL=mssql+mssqlpython://probeowl_test:<pw>@127.0.0.1:1433/probeowl_test?TrustServerCertificate=yes
PROBEOWL_TEST_MSSQL_ADMIN_URL=mssql+mssqlpython://probeowl_test_admin:<pw>@127.0.0.1:1433/probeowl_test?TrustServerCertificate=yes
PROBEOWL_TEST_MARIADB_URL=mariadb+pymysql://probeowl_test:<pw>@127.0.0.1:3306/probeowl_test
PROBEOWL_TEST_ORACLE_URL=oracle+oracledb://probeowl_test:<pw>@127.0.0.1:1521/?service_name=FREEPDB1
PROBEOWL_TEST_POSTGRESQL_URL=postgresql+psycopg://probeowl_test:<pw>@127.0.0.1:5432/probeowl_test
```

The tests refuse a URL whose database or schema name has no "test" in it.

## Test users and seeds

```sh
bash dev/native/create_test_users.sh mssql        # or mariadb, oracle, postgresql
```

`verify.sh` reseeds the planted data on every run, dropping and recreating only the names above,
because card dates are relative to the day of seeding. The seed scripts in `dev/seed/` take these
settings, whose defaults are what the Docker stack uses:

| Setting | Default |
| --- | --- |
| `SEED_RO_LOGIN`, `SEED_RW_LOGIN` | `probeowl_ro`, `seed_rw` |
| `SEED_RO_PASSWORD`, `SEED_RW_PASSWORD` | the documented local passwords |
| `SEED_DB`, `SEED_DB_UAT` (SQL Server) | `wallet`, `wallet_uat` |
| `SEED_DB`, `SEED_DB_UAT` (MariaDB) | `access`, `access_uat` |
| `SEED_ORACLE_SCHEMA`, `SEED_ORACLE_SCHEMA_UAT` | `LEDGER`, `LEDGER_UAT` |

## Running it

From Git Bash at the repo root, on a commit with nothing uncommitted:

```sh
bash dev/native/verify.sh --dry-run    # no password and no database: checks the commit and configs
bash dev/native/verify.sh              # database tests, m1 to m4, and the WSL step
bash dev/native/verify.sh --exit       # only some: --tests, --exit, --wsl, --live
bash dev/native/verify.sh --tests --only mssql   # while the other databases are not installed
```

It runs the exact commit, exported with LF endings to a temporary folder, and stops on any binding
that is not on 127.0.0.1 or has no "test" in its name. Windows services need an elevated prompt to
start, so it asks if one is down. It leaves services running; stop them from an elevated prompt
with `net stop MSSQLSERVER` and `net stop MariaDB`.

## Naming the run in your pull request

`verify.sh` prints the run as `native <commit>`, and a SQL Server run as
`native SQL Server 2025 (compat 160) <commit>`. Name this commit in your pull request, with the
steps that passed. It never edits a file in the repository. CI's database jobs are still required;
a native run is extra evidence, and catches a database problem before you push.

| Step | Covers |
| --- | --- |
| `database-tests`, `exit-m1` to `exit-m4`, `live-e2e` | Database behaviour, as CI's database jobs and exit checks do |
| `wsl-safety-tests` | The 8 safety tests that skip on Windows |
| `wsl-driver-check` | The Linux driver job, `mssql-python-linux` |

The driver check is close to
CI's job but not the same: CI uses a clean container, and this refuses a distro with an ODBC driver
in it rather than proving nothing.
