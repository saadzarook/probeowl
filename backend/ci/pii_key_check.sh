#!/bin/sh
# Author: @SaadZarook
# A REST lookup whose key is PII, against the dev stack: the key shows in no response, log, audit
# page or stored run. Its own owl serve, config copy and store, on a spare port; part of the M1
# exit check. Run from backend/, with the stack up.
set -eu
PORT=${PROBEOWL_PII_PORT:-7889}
SERVER=http://127.0.0.1:$PORT
WORK=$(mktemp -d)
command -v cygpath >/dev/null && WORK=$(cygpath -m "$WORK")  # owl is a Windows program there
pid=""
stop() {
    if [ -n "$pid" ]; then
        # On Windows the shell's child is uv: owl serve under it is stopped by its tree.
        win=$(cat "/proc/$pid/winpid" 2>/dev/null) && taskkill //F //T //PID "$win" >/dev/null 2>&1
        kill "$pid" 2>/dev/null || true
    fi
    rm -rf "$WORK" 2>/dev/null || true
}
trap stop EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }

cp -r "${PROBEOWL_CONFIG:-../examples}" "$WORK/config"
uv run python - "$WORK/config" <<'PY'
import sys
from pathlib import Path

def mark(path: Path, name: str) -> None:
    lines = path.read_text(encoding="utf-8").split("\n")
    [at] = [i for i, line in enumerate(lines) if line.startswith(f"  {name}: {{")]
    lines[at] = lines[at].rstrip()[:-1].rstrip() + ", pii: true }"
    path.write_text("\n".join(lines), encoding="utf-8")

config = Path(sys.argv[1])
[topup] = config.glob("*/entities/topup.yaml")
[visitor] = config.glob("*/entities/visitor.yaml")
mark(topup, "provider_payment_id")  # a key_from, and the API echoes it as id
mark(visitor, "card_id")  # a fan-out's key
PY
export PROBEOWL_STORE_URL="sqlite:///$WORK/store.db"
uv run owl store init >/dev/null
uv run owl serve --config "$WORK/config" --port "$PORT" --log-file "$WORK/owl-serve.log" \
    >"$WORK/terminal.log" 2>&1 &
pid=$!
for _ in $(seq 60); do curl -sf -o /dev/null "$SERVER/api/applications" && break; sleep 1; done
curl -sf -o /dev/null "$SERVER/api/applications" || { cat "$WORK/terminal.log"; fail "owl serve"; }

reference="--env prod --reason incident --reference WALLET-482 --server $SERVER"
echo "== a payment ID that is PII: the request line, the API's echo and the webhooks' column"
# shellcheck disable=SC2086
topup=$(uv run owl validate topup tp_91f2c7 $reference 2>&1) || true
echo "$topup"
echo "== card IDs that are PII: one request for each card"
# shellcheck disable=SC2086
visitor=$(uv run owl inspect VP00012931 $reference 2>&1) || true
echo "$visitor"
runs=$(printf '%s\n%s\n' "$topup" "$visitor" | sed -n 's/^Run \([0-9a-f-]\{36\}\): .*/\1/p' | sort -u)
[ "$(echo "$runs" | wc -w)" -eq 2 ] || fail "expected two runs"
# The lookups were made, and masked: not skipped, which would leave nothing to find.
for run in $runs; do
    curl -sf "$SERVER/api/runs/$run" | grep -q 'GET /[a-z]*/\[masked\]' || fail "no masked request in $run"
done
curl -sf -o /dev/null -H "X-ProbeOwl-Request: 1" "$SERVER/api/search?q=pay_7hq2m9" || true
printf '%s\n%s\n' "$topup" "$visitor" >"$WORK/cli.log"
# shellcheck disable=SC2086
uv run python ci/scan_responses.py "$WORK/config" "$SERVER" $runs \
    --pii pay_7hq2m9 --pii C204518 --pii C198220 --pii C177031 \
    --log "$WORK/owl-serve.log" --log "$WORK/terminal.log" --log "$WORK/cli.log"
uv run python - "$WORK/store.db" <<'PY'
import sys
from pathlib import Path

held = Path(sys.argv[1]).read_bytes() + b"".join(
    p.read_bytes() for p in Path(sys.argv[1]).parent.glob("store.db-*")
)
found = [v for v in (b"pay_7hq2m9", b"C204518", b"C198220", b"C177031") if v in held]
if found:
    sys.exit(f"FAIL: the store holds {len(found)} value(s) that must be masked")
print("The store holds none of them")
PY
echo "PII key check passed"
