# Author: @SaadZarook
# A newcomer's first application against the native MariaDB, in the browser: the folder owl init
# writes for access_uat_test, its schema imported and its two tables confirmed (cards as Transaction,
# its link not yet marked), then the live spec e2e-live/newcomer-wizard.spec.ts, then owl check.
#
#     bash dev/native/newcomer_live.sh               cards confirmed, its link not marked: the
#                                                    wizard fixes it inline
#     bash dev/native/newcomer_live.sh --suggested   cards not confirmed yet: the Tables screen
#                                                    confirms it with the link the import suggests
#
# From a checkout with web/node_modules installed.
# Spare ports only: owl serve on 7990, the preview on 4190. It never touches 7878 or 5173, and it
# stops only the processes it started. The password comes from the native setup's keychain entries
# (dev/native/passwords.py) as an environment variable; nothing is written to the keychain.
set -u
CASE=unmarked
[ "${1:-}" = "--suggested" ] && CASE=suggested
REPO=$(cd "$(dirname "$0")/../.." && pwd)
command -v uv >/dev/null || export PATH="$PATH:$(cygpath -u "$APPDATA")/Python/Python314/Scripts"
command -v uv >/dev/null || { echo "uv is not on PATH" >&2; exit 1; }
for port in 7990 4190; do
  if netstat -ano | grep -q "127.0.0.1:$port .*LISTENING"; then echo "port $port is busy" >&2; exit 1; fi
done

# In C:/ form: owl and node are Windows programs, and do not read /tmp.
WORK=$(cygpath -m "$(mktemp -d)")
FOLDER="$WORK/probeowl"
APP="$FOLDER/access_uat_test"
STORE="sqlite:///$WORK/store.db"
SERVER=http://127.0.0.1:7990
pids=()
stop() {
  for pid in ${pids[@]+"${pids[@]}"}; do
    win=$(cat "/proc/$pid/winpid" 2>/dev/null) && taskkill //F //T //PID "$win" >/dev/null 2>&1
    kill "$pid" 2>/dev/null
  done
}
trap stop EXIT

cd "$REPO/backend" || exit 1
exports="$(uv run --quiet python ../dev/native/passwords.py env)" || { echo "the native passwords could not be read" >&2; exit 1; }
eval "$exports"
[ -n "${PROBEOWL_NATIVE_RO_PASSWORD:-}" ] || { echo "no PROBEOWL_NATIVE_RO_PASSWORD: run dev/native/passwords.py init" >&2; exit 1; }

# As owl init writes it, with the names it suggested in the retest; the secret from the environment.
mkdir -p "$APP/connectors" "$APP/probes"
cat >"$APP/application.yaml" <<'YAML'
application: access_uat_test
title: Access uat test
owner: newcomer
environments:
  uat:
    title: UAT
    production: false
YAML
cat >"$APP/connectors/access_uat_test_mariadb.yaml" <<'YAML'
connector: access_uat_test_mariadb
type: mariadb
owner: newcomer
guards:
  row_ceiling: 1000
environments:
  uat:
    host: 127.0.0.1
    port: 3306
    database: access_uat_test
    user: probeowl_test_ro
    secret: env:PROBEOWL_NATIVE_RO_PASSWORD
    trust_server_certificate: true
    trust_server_certificate_reason: the native test server's own certificate
YAML
# The labels as the retest confirmed them, before its link was marked by hand; or, --suggested,
# visitor_profile only, so cards is confirmed in the browser from its import's suggestion.
if [ "$CASE" = suggested ]; then
  cat >"$APP/catalog.yaml" <<'YAML'
catalog: access_uat_test
tables:
  - connector: access_uat_test_mariadb
    table: visitor_profile
    label: master
    owner: newcomer
    columns:
      id: {roles: [primary_key]}
      created_at: {roles: [time]}
      email: {roles: [pii]}
      status: {roles: [status]}
YAML
else
cat >"$APP/catalog.yaml" <<'YAML'
catalog: access_uat_test
tables:
  - connector: access_uat_test_mariadb
    table: visitor_profile
    label: master
    owner: newcomer
    columns:
      id: {roles: [primary_key]}
      created_at: {roles: [time]}
      email: {roles: [pii]}
      status: {roles: [status]}
  - connector: access_uat_test_mariadb
    table: cards
    label: transaction
    owner: newcomer
    columns:
      card_id: {roles: [primary_key]}
      valid_from: {roles: [time]}
      status: {roles: [status]}
YAML
fi

uv run --quiet owl store init --url "$STORE" >/dev/null || exit 1
uv run --quiet owl serve --config "$FOLDER" --port 7990 --url "$STORE" \
  --web-origin http://127.0.0.1:4190 >"$WORK/serve.log" 2>&1 &
pids+=($!)
cd "$REPO/web" || exit 1
npx vite build --logLevel error || exit 1
PROBEOWL_API_URL=$SERVER npx vite preview --port 4190 --strictPort --host 127.0.0.1 >"$WORK/preview.log" 2>&1 &
pids+=($!)
for _ in $(seq 60); do
  curl -sf -o /dev/null "$SERVER/api/applications" && curl -sf -o /dev/null http://127.0.0.1:4190/ && break
  sleep 1
done
(cd "$REPO/backend" && uv run --quiet owl catalog import access_uat_test_mariadb \
  --application access_uat_test --env uat --server "$SERVER") || { tail -20 "$WORK/serve.log"; exit 1; }

PROBEOWL_LIVE_URL=http://127.0.0.1:4190 PROBEOWL_LIVE_NEWCOMER_FOLDER="$FOLDER" PROBEOWL_LIVE_NEWCOMER_CASE="$CASE" \
  npx playwright test -c playwright.live.config.ts e2e-live/newcomer-wizard.spec.ts --reporter=line
code=$?
if [ "$code" -eq 0 ]; then
  echo "== owl check on what the wizard wrote"
  (cd "$REPO/backend" && uv run --quiet owl check "$FOLDER") || code=1
fi
if [ "$code" -eq 0 ]; then
  # #253: the wizard's entity has no identifier patterns. A card ID must be tried as each
  # identifier and find its visitor, in the web app and in the CLI without --identifier.
  echo "== Inspect by card ID, no patterns"
  cat >"$APP/probes/visitor_cards.yaml" <<'YAML'
probe: visitor_cards
title: A visitor's cards
kind: inspect
entity: visitor_profile
owner: newcomer
expect:
  - count(cards where status == 'active') <= 1
YAML
  curl -sf -X POST -H "X-ProbeOwl-Request: 1" "$SERVER/api/config/reload" >/dev/null || code=1
  PROBEOWL_LIVE_URL=http://127.0.0.1:4190 PROBEOWL_LIVE_NEWCOMER_FOLDER="$FOLDER" \
    npx playwright test -c playwright.live.config.ts e2e-live/inspect-by-card.spec.ts --reporter=line || code=1
  inspected=$(cd "$REPO/backend" && uv run --quiet owl inspect C204518 --env uat --reason incident \
    --application access_uat_test --server "$SERVER" 2>&1)
  echo "$inspected" | head -5
  echo "$inspected" | grep -q "Resolved card_id C204518 -> visitor_profile_id VP00012931 via cards.card_id" || code=1
fi
[ "$code" -eq 0 ] || tail -30 "$WORK/serve.log"
exit "$code"
