#!/bin/sh
# Author: @SaadZarook
# The M1 exit check, in CI against the dev stack: owl validate finds the planted
# missing-ledger bug in Production, passes the healthy top-up, shows the empty link as
# Blocked, and the run's audit record has its reason and reference.
# Run from backend/, with the stack up and owl serve listening on 127.0.0.1:7878.
set -eu

failures=0
runs=""
validate() {
    expected=$1
    shift
    set +e
    output=$(uv run owl validate "$@" 2>&1)
    code=$?
    set -e
    echo "$output"
    runs="$runs $(echo "$output" | sed -n 's/^Run \([0-9a-f-]\{36\}\): .*/\1/p')"
    if [ "$code" != "$expected" ]; then
        echo "FAIL: owl validate $* exited $code, expected $expected" >&2
        failures=$((failures + 1))
    fi
}
expect() {
    if ! echo "$output" | grep -Eq "$1"; then
        echo "FAIL: expected a line matching: $1" >&2
        failures=$((failures + 1))
    fi
}
prod="--env prod --reason incident --reference WALLET-482"

echo "== tp_91f2c7: captured, but no ledger entries"
# shellcheck disable=SC2086
validate 1 topup tp_91f2c7 $prod
# ledger_db is Oracle Free, never a Data Guard standby, so its lag is unknown, and in Production a
# Missing there, and each expectation reading it, says it may not have arrived yet (#153).
expect '^  ledger +Missing - replica lag unknown, the record may not have arrived yet$'
expect '^  fail count\(ledger\) == 2  \(0 == 2\)  - reads a Missing record that may not have arrived yet: replica lag unknown$'
# The run's line names the probe by its title first: "Run <id>: Top-up completed (wallet/topup_completed) on ...".
missing_run=$(echo "$output" | sed -n 's/^Run \([0-9a-f-]\{36\}\): .*(wallet\/topup_completed) on .*/\1/p')
if [ -z "$missing_run" ]; then
    echo "FAIL: no run line for wallet/topup_completed in the output" >&2
    failures=$((failures + 1))
fi

echo "== tp_91f2b9: the healthy top-up"
# shellcheck disable=SC2086
validate 0 topup tp_91f2b9 $prod
expect ': passed$'

echo "== tp_93b001: succeeded, but ledger_txn_id is empty"
# shellcheck disable=SC2086
validate 1 topup tp_93b001 $prod
expect '^  ledger +Blocked  missing link: record\.ledger_txn_id is empty$'

echo "== the audit record of the tp_91f2c7 run"
uv run python ci/m1_audit.py "$PROBEOWL_STORE_URL" "$missing_run" || failures=$((failures + 1))

echo "== no response names a host, port, login or secret reference"
# shellcheck disable=SC2086
uv run python ci/scan_responses.py "${PROBEOWL_CONFIG:-../examples}" http://127.0.0.1:7878 $runs ||
    failures=$((failures + 1))

if [ "$failures" -ne 0 ]; then
    echo "M1 exit check: $failures failures" >&2
    exit 1
fi
echo "M1 exit check passed"
