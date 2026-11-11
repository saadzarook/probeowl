#!/bin/bash
# Author: @SaadZarook
# Seeds Production (wallet) and UAT (wallet_uat) on any SQL Server; needs sqlcmd on PATH.
# Settings: SEED_PASSWORD (required), SEED_HOST (127.0.0.1), SEED_PORT (1433), SEED_USER (sa),
# SEED_DB (wallet), SEED_DB_UAT (wallet_uat), SEED_RO_LOGIN, SEED_RW_LOGIN, SEED_RO_PASSWORD,
# SEED_RW_PASSWORD. Without them it makes what dev/README.md documents.
set -euo pipefail

: "${SEED_PASSWORD:?Set SEED_PASSWORD to the admin password}"
DIR="$(cd "$(dirname "$0")" && pwd)"
export SQLCMDPASSWORD="$SEED_PASSWORD"
SQLCMD="sqlcmd -C -b -S ${SEED_HOST:-127.0.0.1},${SEED_PORT:-1433} -U ${SEED_USER:-sa}"

# sqlcmd reads these as scripting variables: in its arguments, any process could see a password.
export RO_LOGIN="${SEED_RO_LOGIN:-probeowl_ro}" RW_LOGIN="${SEED_RW_LOGIN:-seed_rw}"
export RO_PASSWORD="${SEED_RO_PASSWORD:-ProbeOwl_Ro_Local1}"
export RW_PASSWORD="${SEED_RW_PASSWORD:-Seed_Rw_Local1}"

$SQLCMD -i "$DIR/logins.sql"
$SQLCMD -v DB="${SEED_DB:-wallet}" -i "$DIR/wallet.sql"
$SQLCMD -v DB="${SEED_DB_UAT:-wallet_uat}" -i "$DIR/wallet.sql"
