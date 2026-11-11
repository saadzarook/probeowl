#!/bin/bash
# Author: @SaadZarook
# Starts SQL Server, seeds Production and UAT once, then writes the marker the health check waits for.
set -euo pipefail

export SQLCMDPASSWORD="$MSSQL_SA_PASSWORD"
SQLCMD="/opt/mssql-tools18/bin/sqlcmd -C -b -S localhost -U sa"
MARKER=/var/opt/mssql/.seeded

/opt/mssql/bin/sqlservr &
SERVER_PID=$!

if [ ! -f "$MARKER" ]; then
  until $SQLCMD -Q "SELECT 1" >/dev/null 2>&1; do
    kill -0 "$SERVER_PID"
    sleep 2
  done
  PATH="$PATH:/opt/mssql-tools18/bin" SEED_PASSWORD="$MSSQL_SA_PASSWORD" SEED_HOST=localhost \
    /bin/bash /seed/sqlserver/seed.sh
  touch "$MARKER"
fi

wait "$SERVER_PID"
