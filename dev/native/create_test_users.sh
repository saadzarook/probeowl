#!/bin/bash
# Author: @SaadZarook
# Creates the login and database the database tests use, on one server. Safe to run again.
# Run from Git Bash with the passwords exported (see README.md):
#     bash dev/native/create_test_users.sh mssql|mariadb|oracle|postgresql
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=dev/native/names.sh
source "$HERE/names.sh"
# shellcheck source=dev/native/mariadb_root.sh
source "$HERE/mariadb_root.sh"
# shellcheck source=dev/native/oracle_sysdba.sh
source "$HERE/oracle_sysdba.sh"
# shellcheck source=dev/native/postgresql.sh
source "$HERE/postgresql.sh"

mssql() {
  : "${NATIVE_MSSQL_ADMIN:?run passwords.py set mssql_admin, then export the passwords}"
  export SQLCMDPASSWORD="$NATIVE_MSSQL_ADMIN"
  : "${NATIVE_TEST_MSSQL_ADMIN_PASSWORD:?run passwords.py init, then export the passwords}"
  # Two logins. probeowl_test is what ProbeOwl connects as: what a Production read login has, with
  # no server grant, plus the DDL the store tests need in this database only.
  # probeowl_test_admin is the tests' own: setup, checks and cleanup, with what CI's tests get from
  # running as sa. ALTER ANY LOGIN for the login with no SHOWPLAN one test makes; VIEW SERVER
  # PERFORMANCE STATE for the server's session, connection and plan views; ALTER ANY CONNECTION
  # to end the session that login leaves in the driver's pool, so it can be dropped.
  sqlcmd -C -b -S 127.0.0.1,1433 -U sa <<SQL
IF SUSER_ID('$TEST_USER') IS NULL
    CREATE LOGIN [$TEST_USER] WITH PASSWORD = '$NATIVE_TEST_MSSQL_PASSWORD', CHECK_POLICY = OFF;
IF SUSER_ID('$TEST_ADMIN') IS NULL
    CREATE LOGIN [$TEST_ADMIN] WITH PASSWORD = '$NATIVE_TEST_MSSQL_ADMIN_PASSWORD', CHECK_POLICY = OFF;
-- What an earlier version of this script gave probeowl_test, taken back.
REVOKE ALTER ANY LOGIN FROM [$TEST_USER];
REVOKE VIEW SERVER PERFORMANCE STATE FROM [$TEST_USER];
REVOKE ALTER ANY CONNECTION FROM [$TEST_USER];
GRANT ALTER ANY LOGIN TO [$TEST_ADMIN];
GRANT VIEW SERVER PERFORMANCE STATE TO [$TEST_ADMIN];
GRANT ALTER ANY CONNECTION TO [$TEST_ADMIN];
-- Logins an interrupted test left behind, including the name it used before it said "test".
DECLARE @drop nvarchar(max) = '';
SELECT @drop += 'KILL ' + CAST(s.session_id AS varchar(10)) + ';'
FROM sys.dm_exec_sessions AS s
WHERE s.login_name LIKE 'probe[_]noplan[_]%' OR s.login_name LIKE 'probeowl[_]test[_]noplan[_]%'
   OR s.login_name LIKE 'probeowl[_]test[_]lag[_]%';
SELECT @drop += 'DROP LOGIN ' + QUOTENAME(name) + ';'
FROM sys.server_principals
WHERE name LIKE 'probe[_]noplan[_]%' OR name LIKE 'probeowl[_]test[_]noplan[_]%'
   OR name LIKE 'probeowl[_]test[_]lag[_]%';
EXEC (@drop);
IF DB_ID('$TEST_DATABASE') IS NULL CREATE DATABASE [$TEST_DATABASE];
GO
-- 2022's level on a 2025 server, so plans match CI's.
ALTER DATABASE [$TEST_DATABASE] SET COMPATIBILITY_LEVEL = $SQLSERVER_COMPAT;
GO
USE [$TEST_DATABASE];
IF USER_ID('$TEST_USER') IS NULL CREATE USER [$TEST_USER] FOR LOGIN [$TEST_USER];
IF IS_ROLEMEMBER('db_owner', '$TEST_USER') = 1 ALTER ROLE db_owner DROP MEMBER [$TEST_USER];
ALTER ROLE db_datareader ADD MEMBER [$TEST_USER];
ALTER ROLE db_datawriter ADD MEMBER [$TEST_USER];
-- The store tests create and drop ProbeOwl's own tables here, foreign keys included.
ALTER ROLE db_ddladmin ADD MEMBER [$TEST_USER];
GRANT REFERENCES ON SCHEMA::dbo TO [$TEST_USER];
GRANT SHOWPLAN TO [$TEST_USER];
IF USER_ID('$TEST_ADMIN') IS NULL CREATE USER [$TEST_ADMIN] FOR LOGIN [$TEST_ADMIN];
ALTER ROLE db_owner ADD MEMBER [$TEST_ADMIN];
GO
SQL
}

mariadb_users() {
  : "${NATIVE_MARIADB_ADMIN:?run passwords.py set mariadb_admin, then export the passwords}"
  : "${NATIVE_TEST_MARIADB_ADMIN_PASSWORD:?run passwords.py init, then export the passwords}"
  # probeowl_test is what ProbeOwl connects as: nothing server-wide, only reading and writing in
  # its own database and the DDL the store tests need there. probeowl_test_admin is the tests' own.
  mariadb_root <<SQL
CREATE DATABASE IF NOT EXISTS \`$TEST_DATABASE\`;
CREATE USER IF NOT EXISTS '$TEST_USER'@'%' IDENTIFIED BY '$NATIVE_TEST_MARIADB_PASSWORD';
CREATE USER IF NOT EXISTS '$TEST_ADMIN'@'%' IDENTIFIED BY '$NATIVE_TEST_MARIADB_ADMIN_PASSWORD';
-- What an earlier version of this script gave probeowl_test, taken back.
REVOKE ALL PRIVILEGES, GRANT OPTION FROM '$TEST_USER'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX, REFERENCES
    ON \`$TEST_DATABASE\`.* TO '$TEST_USER'@'%';
GRANT ALL ON \`$TEST_DATABASE\`.* TO '$TEST_ADMIN'@'%';
SQL
}

oracle() {
  : "${NATIVE_TEST_ORACLE_ADMIN_PASSWORD:?run passwords.py init, then export the passwords}"
  oracle_wait_open || exit 1
  # Inside the distro, as its oracle user, who connects as SYSDBA with no password.
  oracle_sysdba <<SQL
WHENEVER SQLERROR EXIT FAILURE
SET VERIFY OFF
ALTER SESSION SET CONTAINER = FREEPDB1;
DECLARE
  found NUMBER;
BEGIN
  SELECT COUNT(*) INTO found FROM dba_users WHERE username = UPPER('$TEST_USER');
  IF found = 0 THEN
    EXECUTE IMMEDIATE 'CREATE USER $TEST_USER IDENTIFIED BY "$NATIVE_TEST_ORACLE_PASSWORD" QUOTA UNLIMITED ON USERS';
  END IF;
  SELECT COUNT(*) INTO found FROM dba_users WHERE username = UPPER('$TEST_ADMIN');
  IF found = 0 THEN
    EXECUTE IMMEDIATE 'CREATE USER $TEST_ADMIN IDENTIFIED BY "$NATIVE_TEST_ORACLE_ADMIN_PASSWORD"';
  END IF;
  -- What an earlier version of this script gave probeowl_test, taken back.
  BEGIN
    EXECUTE IMMEDIATE 'REVOKE SELECT ON sys.v_\$session FROM $TEST_USER';
  EXCEPTION
    WHEN OTHERS THEN
      IF SQLCODE != -1927 THEN RAISE; END IF;  -- ORA-01927: it was never granted
  END;
  -- And what it gave probeowl_test_admin on every schema: it now holds them on one.
  FOR p IN (SELECT privilege FROM dba_sys_privs WHERE grantee = UPPER('$TEST_ADMIN')
            AND privilege LIKE '% ANY %') LOOP
    EXECUTE IMMEDIATE 'REVOKE ' || p.privilege || ' FROM $TEST_ADMIN';
  END LOOP;
END;
/
-- probeowl_test is what ProbeOwl connects as: signing in, and the store tests' tables and
-- sequences in its own schema. Nothing on any other schema.
GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE TO $TEST_USER;
-- probeowl_test_admin is the tests' own: it sets up, locks and drops their tables in
-- probeowl_test's schema, and reads v\$session to see a ProbeOwl session's parallel query status.
-- Each ANY privilege is on that one schema (23ai and later). CREATE ANY INDEX is for the index
-- of a primary key: 23ai refuses a table with one in another schema without it (ORA-41900).
GRANT CREATE SESSION TO $TEST_ADMIN;
GRANT CREATE ANY TABLE, CREATE ANY INDEX, DROP ANY TABLE, SELECT ANY TABLE, INSERT ANY TABLE,
  UPDATE ANY TABLE ON SCHEMA $TEST_USER TO $TEST_ADMIN;
GRANT SELECT ON sys.v_\$session TO $TEST_ADMIN;
EXIT
SQL
}

postgresql() {
  : "${NATIVE_POSTGRESQL_ADMIN:?run passwords.py set postgresql_admin, then export the passwords}"
  : "${NATIVE_TEST_POSTGRESQL_PASSWORD:?run passwords.py init, then export the passwords}"
  # The Windows PostgreSQL 16 service. Only the test login and its own database: no superuser, no
  # CREATEDB or CREATEROLE, and no one else may connect to it. Nothing else on the server changes.
  # The SQL goes on stdin, so the test login's password is never an argument. The SET lines are
  # for this session only: they keep that password out of the server's log and statement stats.
  pg_psql "$NATIVE_POSTGRESQL_ADMIN" postgres postgres -q -v ON_ERROR_STOP=1 <<SQL
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
SET pg_stat_statements.track_utility = off;
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '$TEST_USER') THEN
    CREATE ROLE $TEST_USER LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '$NATIVE_TEST_POSTGRESQL_PASSWORD';
  ELSE
    -- The password in the keychain is the one the tests use.
    ALTER ROLE $TEST_USER LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '$NATIVE_TEST_POSTGRESQL_PASSWORD';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE $TEST_DATABASE OWNER $TEST_USER'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$TEST_DATABASE')\gexec
REVOKE CONNECT ON DATABASE $TEST_DATABASE FROM PUBLIC;
SQL
}

case "${1:-}" in
  mssql) mssql ;;
  mariadb) mariadb_users ;;
  oracle) oracle ;;
  postgresql) postgresql ;;
  *) echo "usage: create_test_users.sh mssql|mariadb|oracle|postgresql" >&2; exit 2 ;;
esac
case "$1" in
  mssql | mariadb | oracle) echo "$1: $TEST_USER and $TEST_ADMIN are ready" ;;
  *) echo "$1: $TEST_USER is ready" ;;
esac
