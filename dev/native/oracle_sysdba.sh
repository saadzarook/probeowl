#!/bin/bash
# Author: @SaadZarook
# Sourced by create_test_users.sh and verify.sh: commands inside the Oracle distro as its oracle
# user, with the environment set here rather than by that user's profile or a wrapper on PATH.
#
#     oracle_sysdba <<SQL           sqlplus as SYSDBA, SQL on stdin
#     oracle_run "lsnrctl status"   a command with ORACLE_HOME, ORACLE_SID and PATH set

# The remote shell's own preamble, run with --exec: plain "wsl.exe --" passes its arguments through
# the login shell first, which would expand $ORACLE_HOME before it is set. ORACLE_HOME comes from
# /etc/oratab, where the installer records it for the FREE database; the path has no spaces.
# shellcheck disable=SC2016
ORACLE_ENV='export ORACLE_SID=FREE; export ORACLE_HOME=$(grep ^FREE: /etc/oratab | cut -d: -f2); test -x $ORACLE_HOME/bin/sqlplus || { echo STOP: /etc/oratab names no FREE database with sqlplus in its home >&2; exit 1; }; export PATH=$ORACLE_HOME/bin:$PATH; '

oracle_run() {
  # Each stream through its own pipe: written straight to a log file, wsl.exe can lose one.
  wsl.exe -d "$ORACLE_DISTRO" -u oracle --exec bash -c "$ORACLE_ENV$1" 2> >(cat >&2) | cat
  return "${PIPESTATUS[0]}"
}

oracle_sysdba() {
  # shellcheck disable=SC2016
  oracle_run 'exec $ORACLE_HOME/bin/sqlplus -s -L / as sysdba'
}

# A distro that was idle starts Oracle on the first call, and FREEPDB1 opens a little after the
# instance: a change made before then fails with ORA-01109 (database not open).
oracle_wait_open() {
  oracle_sysdba <<'SQL'
WHENEVER SQLERROR EXIT FAILURE
SET FEEDBACK OFF
DECLARE
  mode_now VARCHAR2(20);
BEGIN
  FOR i IN 1 .. 120 LOOP
    SELECT open_mode INTO mode_now FROM v$pdbs WHERE name = 'FREEPDB1';
    EXIT WHEN mode_now = 'READ WRITE';
    DBMS_SESSION.SLEEP(1);
  END LOOP;
  IF mode_now <> 'READ WRITE' THEN
    RAISE_APPLICATION_ERROR(-20001, 'FREEPDB1 is ' || mode_now || ', not READ WRITE, after 2 minutes');
  END IF;
END;
/
EXIT
SQL
}
