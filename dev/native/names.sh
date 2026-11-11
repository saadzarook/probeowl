# Author: @SaadZarook
# Every name the native setup creates. Each contains "test", which is what keeps the setup away
# from anything else on these servers: verify.sh refuses a config where one does not.

# The database tests create and drop tables here.
TEST_USER=probeowl_test
TEST_DATABASE=probeowl_test
# SQL Server, MariaDB and Oracle: the tests' own setup, checks and cleanup, never ProbeOwl's connections.
TEST_ADMIN=probeowl_test_admin

# The planted data the exit checks read, in copies of its own.
RO_LOGIN=probeowl_test_ro
RW_LOGIN=seed_test_rw
SQLSERVER_DB=wallet_test
SQLSERVER_DB_UAT=wallet_uat_test
MARIADB_DB=access_test
MARIADB_DB_UAT=access_uat_test
ORACLE_SCHEMA=LEDGER_TEST
ORACLE_SCHEMA_UAT=LEDGER_UAT_TEST

# CI runs SQL Server 2022; the native server is 2025, held at 2022's level so plans match.
SQLSERVER_COMPAT=160

# Where each one runs.
ORACLE_DISTRO=OracleLinux_9_5
UBUNTU_DISTRO=Ubuntu
# PostgreSQL 16 is the Windows service, which listens on 127.0.0.1 and ::1 only. The setup makes
# one login and one database on it, and changes nothing else there.
POSTGRES_SERVICE=postgresql-x64-16
POSTGRES_PORT=5432
POSTGRES_BIN="/c/Program Files/PostgreSQL/16/bin"
