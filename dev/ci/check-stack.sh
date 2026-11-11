#!/bin/bash
# Author: @SaadZarook
# CI only: checks the running dev stack. Each database is seeded (Production and UAT),
# probeowl_ro cannot write, and both fake APIs answer for the IDs in dev/seed/PLANTED.md.
# The write attempts delete no rows (WHERE 1=0), so they change nothing even if a grant is wrong.
set -euo pipefail

COMPOSE=(docker compose -f "$(dirname "$0")/../docker-compose.yml")
RO_PASSWORD=ProbeOwl_Ro_Local1
SQLCMD=(/opt/mssql-tools18/bin/sqlcmd -C -b -h -1 -W -S localhost -U probeowl_ro -P "$RO_PASSWORD")

sqlserver() { "${COMPOSE[@]}" exec -T wallet-sql "${SQLCMD[@]}" -Q "$1"; }
oracle() {
  printf 'WHENEVER SQLERROR EXIT FAILURE\nSET HEADING OFF FEEDBACK OFF\n%s\nEXIT\n' "$1" |
    "${COMPOSE[@]}" exec -T ledger-ora sqlplus -s "probeowl_ro/$RO_PASSWORD@//localhost:1521/FREEPDB1"
}
maria() { "${COMPOSE[@]}" exec -T access-maria mariadb -N -h127.0.0.1 -uprobeowl_ro "-p$RO_PASSWORD" -e "$1"; }

# expect_rows <label> <output>: the seeded planted row must be there.
expect_one() {
  if [ "$(echo "$2" | tr -d '[:space:]')" != "1" ]; then
    echo "FAIL: $1 is not seeded (got: $2)"; exit 1
  fi
  echo "ok: $1 seeded"
}

# expect_denied <label> <command...>: the write must fail.
expect_denied() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then
    echo "FAIL: probeowl_ro can write in $label"; exit 1
  fi
  echo "ok: probeowl_ro cannot write in $label"
}

for db in wallet wallet_uat; do
  expect_one "$db tp_91f2c7" "$(sqlserver "SET NOCOUNT ON; SELECT COUNT(*) FROM $db.dbo.topups WHERE id = 'tp_91f2c7'")"
  expect_denied "$db" sqlserver "DELETE FROM $db.dbo.topups WHERE 1=0"
done
for schema in LEDGER LEDGER_UAT; do
  expect_one "$schema fee rules" "$(oracle "SELECT CASE WHEN COUNT(*) > 0 THEN 1 ELSE 0 END FROM $schema.FEE_RULES;")"
  expect_denied "$schema" oracle "DELETE FROM $schema.LEDGER_ENTRIES WHERE 1=0;"
done
for db in access access_uat; do
  expect_one "$db VP00012931" "$(maria "SELECT COUNT(*) FROM $db.visitor_profile WHERE id = 'VP00012931'")"
  expect_denied "$db" maria "DELETE FROM $db.cards WHERE 1=0"
done

for url in \
  http://127.0.0.1:8081/payments/pay_7hq2m9 \
  "http://127.0.0.1:8081/payments?reference=tp_91f2c7" \
  http://127.0.0.1:8082/cards/C177031 \
  http://127.0.0.1:8082/cards/C204518; do
  curl -fsS "$url" >/dev/null
  echo "ok: $url"
done
