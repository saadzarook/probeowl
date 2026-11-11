#!/bin/bash
# Author: @SaadZarook
# Seeds Production (access) and UAT (access_uat) on any MariaDB; needs the mariadb client on PATH.
# Settings: SEED_PASSWORD (required), SEED_USER (root), SEED_HOST and SEED_PORT (default: local socket).
# SEED_DB (access), SEED_DB_UAT (access_uat), SEED_RO_LOGIN, SEED_RW_LOGIN, SEED_RO_PASSWORD,
# SEED_RW_PASSWORD. Without them it makes what dev/README.md documents.
set -euo pipefail

: "${SEED_PASSWORD:?Set SEED_PASSWORD to the admin password}"
DIR="$(cd "$(dirname "$0")" && pwd)"
export MYSQL_PWD="$SEED_PASSWORD"
MYSQL=(mariadb -u"${SEED_USER:-root}")
# Over TCP, verify the server's certificate, asked for by name: a password the client reads only
# from MYSQL_PWD would otherwise turn it off (see dev/native/mariadb_root.sh). Docker's seed uses
# the local socket, which has no TLS.
[ -n "${SEED_HOST:-}" ] && MYSQL+=(--ssl-verify-server-cert -h "$SEED_HOST" -P "${SEED_PORT:-3306}")
if "${MYSQL[@]}" -e "SELECT 1" 2>&1 >/dev/null | grep -q "ssl-verify-server-cert is disabled"; then
  echo "STOP: the mariadb client turned off verifying the server's certificate" >&2
  exit 1
fi

RO_LOGIN="${SEED_RO_LOGIN:-probeowl_ro}"
RW_LOGIN="${SEED_RW_LOGIN:-seed_rw}"

# On stdin: in its arguments, any process could see a password.
"${MYSQL[@]}" <<SQL
CREATE USER IF NOT EXISTS '$RO_LOGIN'@'%' IDENTIFIED BY '${SEED_RO_PASSWORD:-ProbeOwl_Ro_Local1}';
CREATE USER IF NOT EXISTS '$RW_LOGIN'@'%' IDENTIFIED BY '${SEED_RW_PASSWORD:-Seed_Rw_Local1}';
SQL
for db in "${SEED_DB:-access}" "${SEED_DB_UAT:-access_uat}"; do
  "${MYSQL[@]}" -e "CREATE DATABASE \`$db\`"
  "${MYSQL[@]}" "$db" < "$DIR/access.sql"
  "${MYSQL[@]}" -e "GRANT SELECT ON \`$db\`.* TO '$RO_LOGIN'@'%'; GRANT SELECT, INSERT, UPDATE, DELETE ON \`$db\`.* TO '$RW_LOGIN'@'%'"
done
