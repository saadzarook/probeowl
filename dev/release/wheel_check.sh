# Author: @SaadZarook
# The release check for the wheel as users install it: build it, `uv tool install` it into a clean
# tool environment (a fresh resolution, not uv.lock), then run owl init end to end for two cases
# (UAT only with no Production; prod and uat), owl --version, owl check, and owl serve's first page.
#
#     bash dev/release/wheel_check.sh [port]      from a checkout; port defaults to 7992
#
# It found #248: uv resolved Typer 0.29, newer than the lock, and owl init crashed. Nothing touches
# the system keychain: the passwords go to a keyring file in the check's own folder.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
PORT=${1:-7992}
command -v uv >/dev/null || export PATH="$PATH:$(cygpath -u "${APPDATA:-}" 2>/dev/null)/Python/Python314/Scripts"
command -v uv >/dev/null || { echo "uv is not on PATH" >&2; exit 1; }
if netstat -an 2>/dev/null | grep -q "127.0.0.1:$PORT .*LISTEN"; then echo "port $PORT is busy" >&2; exit 1; fi

WORK=$(mktemp -d)
command -v cygpath >/dev/null && WORK=$(cygpath -m "$WORK")  # owl is a Windows program there
export UV_TOOL_DIR="$WORK/tools" UV_TOOL_BIN_DIR="$WORK/bin"
pids=()
stop() {
  for pid in ${pids[@]+"${pids[@]}"}; do
    win=$(cat "/proc/$pid/winpid" 2>/dev/null) && taskkill //F //T //PID "$win" >/dev/null 2>&1
    kill "$pid" 2>/dev/null
  done
}
trap stop EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

echo "== build"
(cd "$REPO/backend" && uv build --wheel -o "$WORK/dist" >"$WORK/build.log" 2>&1) || { tail -20 "$WORK/build.log"; fail "the wheel did not build"; }
WHEEL=$(ls "$WORK"/dist/*.whl)

echo "== uv tool install (fresh resolution)"
# keyrings.alt only for this check's own keyring file; everything else as a user gets it.
uv tool install --python 3.12 --with keyrings.alt "$WHEEL" >"$WORK/install.log" 2>&1 || { cat "$WORK/install.log"; fail "uv tool install"; }
TOOL="$UV_TOOL_DIR/probeowl"
PY="$TOOL/Scripts/python.exe"; [ -x "$PY" ] || PY="$TOOL/bin/python"
OWL="$UV_TOOL_BIN_DIR/owl"; [ -x "$OWL.exe" ] && OWL="$OWL.exe"
"$PY" -c "from importlib.metadata import version as v; print('resolved: typer', v('typer'), 'click', v('click'), 'probeowl', v('probeowl'))"

echo "== owl --version"
"$OWL" --version || fail "owl --version"

# The check's keyring: a file under WORK, allowed only by the CI switch, read by owl init alone.
export PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring PROBEOWL_ALLOW_INSECURE_KEYRING=1
export LOCALAPPDATA="$WORK/appdata" XDG_DATA_HOME="$WORK/appdata"
for case in uat prod-uat; do
  echo "== owl init: $case"
  folder="$WORK/$case/probeowl"
  "$PY" -I "$REPO/dev/release/init_answers.py" "$case" "$folder" "sqlite:///$WORK/$case/store.db" >"$WORK/init-$case.log" 2>&1 \
    || { tail -40 "$WORK/init-$case.log"; fail "owl init ($case)"; }
  grep -q "owl serve --config" "$WORK/init-$case.log" || { tail -20 "$WORK/init-$case.log"; fail "owl init ($case) did not finish"; }
  echo "== owl check: $case"
  "$OWL" check "$folder" || fail "owl check ($case)"
done

echo "== owl serve's first page"
"$OWL" serve --config "$WORK/prod-uat/probeowl" --port "$PORT" --url "sqlite:///$WORK/prod-uat/store.db" >"$WORK/serve.log" 2>&1 &
pids+=($!)
for _ in $(seq 60); do curl -sf -o /dev/null "http://127.0.0.1:$PORT/api/applications" && break; sleep 1; done
curl -sf "http://127.0.0.1:$PORT/" | grep -q "<title>ProbeOwl" || { tail -20 "$WORK/serve.log"; fail "the first page"; }
curl -sf "http://127.0.0.1:$PORT/api/applications" | grep -q '"wallet"' || fail "the applications API"
curl -sf -o /dev/null "http://127.0.0.1:$PORT/guide/" || fail "the guide"
echo "== passed: $(basename "$WHEEL")  sha256 $(sha256sum "$WHEEL" | cut -d' ' -f1)"
