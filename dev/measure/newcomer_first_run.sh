# Author: @SaadZarook
# The newcomer path on v0.2, measured (#27): build the wheel from this checkout, install it cold as
# a user does, start `owl serve` in an empty folder with a home folder of its own (no store, no
# applications), then the browser: first-run setup, Add connection on the native MariaDB
# (access_uat_test), Confirm all, a new entity and Validate probe, and a first Validate on the card
# example. Counts every command, browser action and character typed, with machine time per segment,
# by the same rules as newcomer_baseline.sh, and prints the table.
#
#     bash dev/measure/newcomer_first_run.sh
#
# From a checkout with web/node_modules installed (the wheel build needs it). Spare port 7993 only.
# The install is cold: its own uv tool and cache folders, deleted after. Add connection saves the
# read-only password to the system keychain under a test-only connector, probeowl/probeowl_test_*;
# leftovers from a run killed hard are swept first, and this run's entry is deleted when it ends,
# whatever happened. The password comes from the native setup (dev/native/passwords.py) and is
# never printed.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PORT=7993
command -v uv >/dev/null || export PATH="$PATH:$(cygpath -u "${APPDATA:-}" 2>/dev/null)/Python/Python314/Scripts"
command -v uv >/dev/null || { echo "uv is not on PATH" >&2; exit 1; }
if netstat -an 2>/dev/null | grep -q "127.0.0.1:$PORT .*LISTEN"; then echo "port $PORT is busy" >&2; exit 1; fi

WORK=$(mktemp -d)
command -v cygpath >/dev/null && WORK=$(cygpath -m "$WORK")  # owl is a Windows program there
export UV_TOOL_DIR="$WORK/tools" UV_TOOL_BIN_DIR="$WORK/bin" UV_CACHE_DIR="$WORK/uv-cache"
CONNECTOR="probeowl_test_measure_${RANDOM}${RANDOM}"
PY=""
pids=()
keychain() { "$PY" -I "$REPO/dev/native/keychain_test_entries.py" "$@"; }
stop() {
  for pid in ${pids[@]+"${pids[@]}"}; do
    win=$(cat "/proc/$pid/winpid" 2>/dev/null) && taskkill //F //T //PID "$win" >/dev/null 2>&1
    kill "$pid" 2>/dev/null
  done
  # Whatever still listens on the port is this run's owl serve: its folder can't go while it runs.
  powershell.exe -NoProfile -Command "Get-NetTCPConnection -State Listen -LocalPort $PORT -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id \$_.OwningProcess -Force -ErrorAction SilentlyContinue }" >/dev/null 2>&1
  if [ -n "$PY" ]; then
    keychain delete "$CONNECTOR.uat" || echo "could not delete probeowl/$CONNECTOR.uat: remove it by hand" >&2
  fi
  rm -rf "$WORK"
}
trap stop EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
now() { date +%s.%N; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }

exports="$(cd "$REPO/backend" && uv run --quiet python ../dev/native/passwords.py env)" || fail "the native passwords could not be read"
eval "$exports"
[ -n "${PROBEOWL_NATIVE_RO_PASSWORD:-}" ] || fail "no PROBEOWL_NATIVE_RO_PASSWORD: run dev/native/passwords.py init"

echo "== build the wheel from $(git -C "$REPO" rev-parse --short HEAD)"
(cd "$REPO/backend" && uv build --wheel -o "$WORK/dist" >"$WORK/build.log" 2>&1) || { tail -20 "$WORK/build.log"; fail "the wheel did not build"; }
WHEEL=$(ls "$WORK"/dist/*.whl)
# A development version names no single commit: name the commit too.
VERSION="$(basename "$WHEEL" | cut -d- -f2) (develop at $(git -C "$REPO" rev-parse --short HEAD))"

echo "== install $(basename "$WHEEL") (cold)"
t=$(now)
# A fresh resolution, as wheel_check.sh does: not uv.lock.
uv tool install --python 3.12 "$WHEEL" >"$WORK/install.log" 2>&1 || { cat "$WORK/install.log"; fail "uv tool install"; }
echo "{\"seconds\": $(since "$t")}" >"$WORK/install.json"
PY="$UV_TOOL_DIR/probeowl/Scripts/python.exe"; [ -x "$PY" ] || PY="$UV_TOOL_DIR/probeowl/bin/python"
OWL="$UV_TOOL_BIN_DIR/owl"; [ -x "$OWL.exe" ] && OWL="$OWL.exe"
"$OWL" --version || fail "owl --version"
keychain sweep || fail "the keychain could not be swept"

echo "== owl serve in an empty folder"
mkdir -p "$WORK/empty" "$WORK/home"
t=$(now)
# Started directly, not in a subshell, so stop() reaches owl serve itself.
env -C "$WORK/empty" -u PROBEOWL_STORE_URL -u PROBEOWL_CONFIG USERPROFILE="$WORK/home" HOME="$WORK/home" \
  "$OWL" serve --port "$PORT" >"$WORK/serve.log" 2>&1 &
pids+=($!)
for _ in $(seq 120); do
  curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/applications" && break
  sleep 0.5
done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/applications" || { tail -20 "$WORK/serve.log"; fail "owl serve did not answer"; }
echo "{\"seconds\": $(since "$t")}" >"$WORK/serve.json"

echo "== the browser, from first-run setup to a first Validate"
(cd "$REPO/web" && PROBEOWL_LIVE_URL="http://127.0.0.1:$PORT" PROBEOWL_MEASURE_PATH=first-run \
  PROBEOWL_MEASURE_OUT="$WORK/browser.json" PROBEOWL_MEASURE_CONNECTOR="$CONNECTOR" \
  PROBEOWL_MEASURE_PASSWORD="$PROBEOWL_NATIVE_RO_PASSWORD" \
  npx playwright test -c playwright.live.config.ts e2e-live/newcomer-first-run.spec.ts --reporter=line) \
  || { tail -30 "$WORK/serve.log"; fail "the browser path"; }
keychain exists "$CONNECTOR.uat" || fail "Add connection did not save probeowl/$CONNECTOR.uat"

echo
"$PY" -I "$REPO/dev/measure/report.py" "$WORK" "$VERSION" first-run
