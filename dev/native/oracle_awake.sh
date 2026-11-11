#!/bin/bash
# Author: @SaadZarook
# Start the native Oracle in WSL and keep it running while you use ProbeOwl.
#
#     bash dev/native/oracle_awake.sh
#
# WSL stops a distro a few seconds after its last wsl.exe session ends, and Oracle with it: the
# next run then gets "connection refused" on every Oracle location. This holds one session open,
# as verify.sh does during a run, starts the service if it is down, and waits for FREEPDB1 to
# open. Leave the window open; Ctrl-C ends the session and lets the distro stop when idle.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=dev/native/names.sh
source "$HERE/names.sh"
# shellcheck source=dev/native/oracle_sysdba.sh
source "$HERE/oracle_sysdba.sh"

SERVICE=oracle-free-26ai
PORT=1521

listening() {
  powershell.exe -NoProfile -Command "if (Test-NetConnection 127.0.0.1 -Port $PORT -InformationLevel Quiet -WarningAction SilentlyContinue) { exit 0 } else { exit 1 }" >/dev/null 2>&1
}

wsl.exe -d "$ORACLE_DISTRO" -- sleep infinity &
AWAKE=$!
trap 'kill "$AWAKE" 2>/dev/null; echo; echo "released $ORACLE_DISTRO: WSL stops it when idle"' EXIT

if ! listening; then
  echo "starting $SERVICE in $ORACLE_DISTRO"
  wsl.exe -d "$ORACLE_DISTRO" -u root -- systemctl start "$SERVICE" || {
    echo "STOP: $SERVICE did not start in $ORACLE_DISTRO (dev/native/README.md, Installing Oracle in WSL)" >&2
    exit 1
  }
  for _ in $(seq 1 120); do listening && break; sleep 1; done
  listening || { echo "STOP: nothing listens on 127.0.0.1:$PORT after 2 minutes" >&2; exit 1; }
fi
oracle_wait_open >/dev/null || { echo "STOP: FREEPDB1 did not open READ WRITE" >&2; exit 1; }

echo "Oracle is up on 127.0.0.1:$PORT, FREEPDB1 READ WRITE, and $ORACLE_DISTRO stays awake."
echo "Keep this window open while you use ProbeOwl; Ctrl-C to let it stop."
wait "$AWAKE"
