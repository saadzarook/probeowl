#!/bin/bash
# Author: @SaadZarook
# Sourced by create_test_users.sh and verify.sh: the mariadb client as root on this machine, over
# TLS with the server's certificate verified.
#
# MariaDB 11.4 and later verify the certificate by default, checking a self-signed one against the
# password. A password the client reads only from MYSQL_PWD does not count for that default, so it
# turns verification off with a warning; asked for by name, verification stays on. The password
# stays in the environment, never in an argument or a file. Should a client turn verification off
# anyway, this stops rather than go on unverified.

mariadb_root() {
  local err code=0
  err="$(mktemp)"
  MYSQL_PWD="$NATIVE_MARIADB_ADMIN" mariadb --ssl-verify-server-cert -h 127.0.0.1 -P 3306 -uroot \
    "$@" 2>"$err" || code=$?
  cat "$err" >&2
  if grep -q "ssl-verify-server-cert is disabled" "$err"; then
    echo "STOP: the mariadb client turned off verifying the server's certificate" >&2
    code=1
  fi
  rm -f "$err"
  return "$code"
}
