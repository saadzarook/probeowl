#!/bin/bash
# Author: @SaadZarook
# Sourced by create_test_users.sh and verify.sh: the Windows PostgreSQL 16 service, through its own
# psql. A password goes in PGPASSWORD for that one call, never in an argument or a file.

# psql as one login on 127.0.0.1, with the password given; the rest are psql's own arguments.
pg_psql() {
  local password=$1 login=$2 database=$3
  shift 3
  PGPASSWORD="$password" PGCLIENTENCODING=UTF8 "$POSTGRES_BIN/psql.exe" -X -h 127.0.0.1 \
    -p "$POSTGRES_PORT" -U "$login" -d "$database" "$@"
}

# True when every address in a comma-separated list is a loopback one: listen_addresses, or what
# Windows says listens on the port. An empty list is not, nor is '*', 0.0.0.0 or ::.
loopback_only() {
  local address
  local -a addresses
  IFS=',' read -r -a addresses <<<"$1"
  [ "${#addresses[@]}" -gt 0 ] || return 1
  for address in "${addresses[@]}"; do
    address="${address//[[:space:]]/}"
    case "$address" in 127.0.0.1 | ::1 | localhost) ;; *) return 1 ;; esac
  done
}
