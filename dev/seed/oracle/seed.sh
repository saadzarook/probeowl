#!/bin/bash
# Author: @SaadZarook
# Seeds Production (LEDGER) and UAT (LEDGER_UAT) in FREEPDB1 on any Oracle; needs sqlplus on PATH.
# Settings: SEED_ORACLE_CONNECT, a SYSDBA connect string such as
# "sys/<password>@//host:1521/FREEPDB1 as sysdba" (default: "/ as sysdba", for the local server).
# SEED_ORACLE_SCHEMA (LEDGER), SEED_ORACLE_SCHEMA_UAT (LEDGER_UAT), SEED_RO_LOGIN, SEED_RW_LOGIN,
# SEED_RO_PASSWORD, SEED_RW_PASSWORD. Without them it makes what dev/README.md documents.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONNECT="${SEED_ORACLE_CONNECT:-/ as sysdba}"

run() {
  sqlplus -s "$CONNECT" @"$DIR/$1" "${@:2}"
}

SCHEMA="${SEED_ORACLE_SCHEMA:-LEDGER}"
SCHEMA_UAT="${SEED_ORACLE_SCHEMA_UAT:-LEDGER_UAT}"
RO_LOGIN="${SEED_RO_LOGIN:-probeowl_ro}"
RW_LOGIN="${SEED_RW_LOGIN:-seed_rw}"

# On stdin: in its arguments, any process could see a password.
sqlplus -s "$CONNECT" <<SQL
DEFINE schema = $SCHEMA
DEFINE schema_uat = $SCHEMA_UAT
DEFINE ro_login = $RO_LOGIN
DEFINE rw_login = $RW_LOGIN
DEFINE ro_password = ${SEED_RO_PASSWORD:-ProbeOwl_Ro_Local1}
DEFINE rw_password = ${SEED_RW_PASSWORD:-Seed_Rw_Local1}
@$DIR/users.sql
SQL
run ledger.sql "$SCHEMA"
run ledger.sql "$SCHEMA_UAT"
run drift.sql "$SCHEMA_UAT"
# Grants come last so the read-only login works only once everything is seeded.
run grants.sql "$SCHEMA" "$RO_LOGIN" "$RW_LOGIN"
run grants.sql "$SCHEMA_UAT" "$RO_LOGIN" "$RW_LOGIN"
