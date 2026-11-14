# Author: @SaadZarook
# The newcomer path, measured (#27): install a release as a user does, answer owl init for the card
# example on the native MariaDB (access_uat_test), then the browser from the first page to a first
# Validate. Counts every command, answer, browser action and character typed, with machine time
# per segment, and prints the table. Run it before each No YAML phase and after it.
#
#     bash dev/measure/newcomer_baseline.sh [version]     a PyPI version, default 0.1.0
#     bash dev/measure/newcomer_baseline.sh --wheel <path.whl>
#
# From a checkout with web/node_modules installed. Spare port 7991 only. The install is cold: its
# own uv tool and cache folders, deleted after. owl init saves the read-only password in the system
# keychain as a newcomer's would, as probeowl/card_baseline_mariadb.uat; the run refuses if that entry
# exists already, and deletes it when done. The password comes from the native setup
# (dev/native/passwords.py) and is never printed.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PORT=7991
SPEC="probeowl==0.1.0"
VERSION=0.1.0
if [ "${1:-}" = "--wheel" ]; then
  SPEC=$(cygpath -m "$2" 2>/dev/null || echo "$2"); VERSION=$(basename "$2" | cut -d- -f2)
elif [ -n "${1:-}" ]; then
  SPEC="probeowl==$1"; VERSION=$1
fi
KEY=card_baseline_mariadb.uat
command -v uv >/dev/null || export PATH="$PATH:$(cygpath -u "${APPDATA:-}" 2>/dev/null)/Python/Python314/Scripts"
command -v uv >/dev/null || { echo "uv is not on PATH" >&2; exit 1; }
if netstat -an 2>/dev/null | grep -q "127.0.0.1:$PORT .*LISTEN"; then echo "port $PORT is busy" >&2; exit 1; fi

WORK=$(mktemp -d)
command -v cygpath >/dev/null && WORK=$(cygpath -m "$WORK")  # owl is a Windows program there
export UV_TOOL_DIR="$WORK/tools" UV_TOOL_BIN_DIR="$WORK/bin" UV_CACHE_DIR="$WORK/uv-cache"
export PROBEOWL_STORE_URL="sqlite:///$WORK/store.db"
FOLDER="$WORK/probeowl"
PY=""
ours=0
pids=()
stop() {
  for pid in ${pids[@]+"${pids[@]}"}; do
    win=$(cat "/proc/$pid/winpid" 2>/dev/null) && taskkill //F //T //PID "$win" >/dev/null 2>&1
    kill "$pid" 2>/dev/null
  done
  if [ "$ours" = 1 ] && [ -n "$PY" ]; then
    "$PY" -I -c "import keyring, keyring.errors
try:
    keyring.delete_password('probeowl', '$KEY')
except keyring.errors.PasswordDeleteError:
    pass" || echo "could not delete the keychain entry probeowl/$KEY: remove it by hand" >&2
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

echo "== install $SPEC (cold)"
t=$(now)
uv tool install --python 3.12 "$SPEC" >"$WORK/install.log" 2>&1 || { cat "$WORK/install.log"; fail "uv tool install"; }
echo "{\"seconds\": $(since "$t")}" >"$WORK/install.json"
PY="$UV_TOOL_DIR/probeowl/Scripts/python.exe"; [ -x "$PY" ] || PY="$UV_TOOL_DIR/probeowl/bin/python"
OWL="$UV_TOOL_BIN_DIR/owl"; [ -x "$OWL.exe" ] && OWL="$OWL.exe"
"$OWL" --version || fail "owl --version"
# The first process starts after a fresh install are slow on Windows while antivirus checks the
# new files (docs/v0.2.md, "Startup after a fresh install"). Both newcomer scripts let them pass
# here, timed as their own row, so owl serve is timed at the same point after install in each.
echo "== first loads after install"
t=$(now)
for _ in 1 2 3; do
  "$PY" -I -c "import probeowl.cli.main, probeowl.api.app, uvicorn" || fail "owl's imports"
done
echo "{\"seconds\": $(since "$t")}" >"$WORK/settle.json"

taken=$("$PY" -I -c "import keyring; print(keyring.get_password('probeowl', '$KEY') is not None)") || fail "the keychain could not be read"
[ "$taken" = False ] || fail "the keychain already has probeowl/$KEY; this run would overwrite it"
ours=1

echo "== owl init"
"$PY" -I "$REPO/dev/measure/newcomer_init.py" "$FOLDER" "$WORK/init.json" >"$WORK/init.log" 2>&1 \
  || { tail -40 "$WORK/init.log"; fail "owl init"; }
grep -q "owl serve --config" "$WORK/init.log" || { tail -20 "$WORK/init.log"; fail "owl init did not finish"; }
# What the test and the import said, as a newcomer reads them (newcomer_init.py masks the password).
sed -n '/Test the connections now/,$p' "$WORK/init.log"

echo "== owl serve"
t=$(now)
"$OWL" serve --config "$FOLDER" --port "$PORT" >"$WORK/serve.log" 2>&1 &
pids+=($!)
for _ in $(seq 120); do
  curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/applications" && break
  sleep 0.5
done
curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/applications" || { tail -20 "$WORK/serve.log"; fail "owl serve did not answer"; }
echo "{\"seconds\": $(since "$t")}" >"$WORK/serve.json"

echo "== the browser, to a first Validate"
(cd "$REPO/web" && PROBEOWL_LIVE_URL="http://127.0.0.1:$PORT" PROBEOWL_MEASURE_OUT="$WORK/browser.json" \
  npx playwright test -c playwright.live.config.ts e2e-live/newcomer-path.spec.ts --reporter=line) \
  || { tail -30 "$WORK/serve.log"; fail "the browser path"; }

echo
"$PY" -I "$REPO/dev/measure/report.py" "$WORK" "$VERSION"
