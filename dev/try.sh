# Author: @SaadZarook
# Try the examples in one command: the databases with their planted cases, the fake APIs,
# owl serve on a copy of examples/ with its own store, and the web app. Ctrl-C stops it all.
#
#   bash dev/try.sh --docker      the Docker stack in dev/docker-compose.yml
#   bash dev/try.sh --native      the databases dev/native/ installs on this machine
#
# Options: --reseed (Docker: seed the planted cases afresh), --api-port N (default 7878),
# --web-port N (default 5173).
#
# Passwords are saved in the keychain under probeowl-try/, never probeowl/, so trying the
# examples cannot overwrite a real connector's secret. Everything else lives in
# ~/.probeowl/try: the config copy, the store and the logs.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Python and SQLite on Windows read /c/Users/... as C:\c\Users\..., so they get C:/Users/... there.
native_path() { if command -v cygpath >/dev/null; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
TRY="$(native_path "${HOME}/.probeowl/try")"
SETUP="" RESEED="" API_PORT=7878 WEB_PORT=5173
PIDS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --docker) SETUP=docker ;;
    --native) SETUP=native ;;
    --reseed) RESEED=1 ;;
    --api-port) API_PORT=$2; shift ;;
    --web-port) WEB_PORT=$2; shift ;;
    -h | --help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option $1; see: bash dev/try.sh --help" >&2; exit 2 ;;
  esac
  shift
done

stop() { echo "try.sh: $*" >&2; exit 1; }
[ -n "$SETUP" ] || stop "say which databases: --docker or --native"
command -v uv >/dev/null || stop "uv is needed: https://docs.astral.sh/uv/"
command -v npm >/dev/null || stop "Node.js is needed for the web app: https://nodejs.org/"

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
wait_for_port() {
  local port=$1 seconds=$2
  for _ in $(seq 1 "$seconds"); do port_open "$port" && return 0; sleep 1; done
  return 1
}

# Each process it started, and its children. On Windows a child such as Vite's node is not
# Git Bash's, so taskkill stops the whole tree by its Windows process id.
cleanup() {
  for pid in "${PIDS[@]}"; do
    if command -v taskkill >/dev/null && [ -r "/proc/$pid/winpid" ]; then
      taskkill //F //T //PID "$(cat "/proc/$pid/winpid")" >/dev/null 2>&1
    fi
    kill "$pid" 2>/dev/null
  done
}
trap cleanup EXIT
trap 'exit 130' INT TERM

for port in "$API_PORT" "$WEB_PORT"; do
  port_open "$port" && stop "port $port is in use: stop what holds it, or pick another with --api-port or --web-port"
done

mkdir -p "$TRY/logs"

echo "1. Databases"
if [ "$SETUP" = docker ]; then
  command -v docker >/dev/null || stop "Docker is needed for --docker; or install the databases natively (dev/native/README.md)"
  compose=(docker compose -f "$ROOT/dev/docker-compose.yml")
  # The seed runs once, on a fresh volume, so a fresh seed starts from no volume.
  [ -n "$RESEED" ] && { "${compose[@]}" down -v || stop "docker compose down failed"; }
  "${compose[@]}" up --wait || stop "the Docker stack did not become healthy; see: ${compose[*]} logs"
  TRY_RO_PASSWORD=ProbeOwl_Ro_Local1  # the dev stack's local-only login, in dev/README.md
else
  for spec in "SQL Server:1433" "MariaDB:3306" "Oracle:1521"; do
    port_open "${spec##*:}" || stop "${spec%%:*} does not answer on 127.0.0.1:${spec##*:}; dev/native/README.md says how to start it (Oracle: bash dev/native/oracle_awake.sh)"
  done
  [ -n "$RESEED" ] && echo "   native databases are reseeded by: bash dev/native/verify.sh --exit"
  exports="$(cd "$ROOT/backend" && uv run python ../dev/native/passwords.py env)" || stop "the native passwords could not be read; see dev/native/README.md"
  TRY_RO_PASSWORD="$(eval "$exports"; printf '%s' "${PROBEOWL_NATIVE_RO_PASSWORD:-}")"
  unset exports
  [ -n "$TRY_RO_PASSWORD" ] || stop "no native read-only password yet; run: bash dev/native/verify.sh --dry-run"
fi

echo "2. Fake APIs"
if [ "$SETUP" = native ]; then
  for fake in payments:8081 door_controller:8082; do
    port_open "${fake##*:}" && continue
    (cd "$ROOT" && exec uv run "dev/fakes/${fake%%:*}.py") >"$TRY/logs/${fake%%:*}.log" 2>&1 &
    PIDS+=($!)
  done
fi
wait_for_port 8081 60 && wait_for_port 8082 60 || stop "the fake APIs did not start; see $TRY/logs"

echo "3. Config: a copy of examples/ in $TRY/config"
(cd "$ROOT/backend" && TRY_RO_PASSWORD="$TRY_RO_PASSWORD" uv run python ../dev/try_config.py ../examples "$TRY/config" "$SETUP") ||
  stop "the config copy was not made"
unset TRY_RO_PASSWORD

echo "4. Store: $TRY/store.db"
export PROBEOWL_STORE_URL="sqlite:///$TRY/store.db"
(cd "$ROOT/backend" && uv run owl store init) || stop "the store was not created"

echo "5. owl serve and the web app"
origins=()
case "$WEB_PORT" in 5173 | 4173) ;; *) origins=(--web-origin "http://127.0.0.1:$WEB_PORT") ;; esac
(cd "$ROOT/backend" && exec uv run owl serve --config "$TRY/config" --port "$API_PORT" "${origins[@]}" \
  --log-file "$TRY/logs/owl-serve.log") >"$TRY/logs/owl-serve.out" 2>&1 &
PIDS+=($!)
wait_for_port "$API_PORT" 60 || stop "owl serve did not start; see $TRY/logs/owl-serve.out"
if [ ! -d "$ROOT/web/node_modules" ]; then
  (cd "$ROOT/web" && npm ci --no-audit --no-fund) || stop "npm ci failed in web/"
fi
(cd "$ROOT/web" && PROBEOWL_API_URL="http://127.0.0.1:$API_PORT" exec npx vite --port "$WEB_PORT" --strictPort) \
  >"$TRY/logs/vite.log" 2>&1 &
PIDS+=($!)
wait_for_port "$WEB_PORT" 60 || stop "the web app did not start; see $TRY/logs/vite.log"

cat <<EOF

ProbeOwl is up: open http://127.0.0.1:$WEB_PORT

The planted cases (dev/seed/PLANTED.md):
  Validate, Wallet, UAT:     tp_91f2c7 (ledger entries Missing), tp_91f2b9 (passes),
                             tp_93b001 (ledger Blocked: the link was never saved)
  Inspect, Card access:      C204518 (three card issues), a.rahman@example.com (Ambiguous)
Production needs a reason and a reference, as it would for real.

Logs are in $TRY/logs. Ctrl-C stops everything this started.
EOF
wait
