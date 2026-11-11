#!/bin/sh
# Author: @SaadZarook
# The M3 exit check, in CI against the dev stack: importing wallet_db suggests a label for
# every table, and saving a query on a Transaction table without a time filter is rejected
# with a clear message. It also saves a good probe, under the write rules.
# Run from backend/, with owl serve on 7878 (a copy of the examples, $PROBEOWL_M3_CONFIG) and
# on 7879 (the copy whose ledger lookup has no time filter or key, and whose UAT bindings fail).
set -eu

echo "== importing wallet_db suggests a label for every table"
output=$(uv run owl catalog import wallet_db --application wallet --env uat)
echo "$output"
tables=$(echo "$output" | sed -n 's/^Imported wallet_db in uat: \([0-9]*\) tables$/\1/p')
labelled=$(echo "$output" | grep -Ec '^    (suggested|confirmed): ')
if [ -z "$tables" ] || [ "$tables" -eq 0 ] || [ "$tables" != "$labelled" ]; then
    echo "FAIL: $labelled labels for ${tables:-no} tables" >&2
    exit 1
fi

uv run python ci/m3_save_check.py "$PROBEOWL_M3_CONFIG"
uv run owl check "$PROBEOWL_M3_CONFIG"

echo "== owl check runs the same rules: the copy whose ledger lookup has no time filter fails"
if bad=$(uv run owl check "$PROBEOWL_M3_BAD_CONFIG" --no-connect 2>&1); then
    echo "$bad"
    echo "FAIL: owl check passed a lookup that cannot be saved" >&2
    exit 1
fi
echo "$bad"
echo "$bad" | grep -q 'ledger_entries is a Transaction table: filter on its time column posted_at'

# 7879's ledger_db in UAT points at a closed port and its access_db has a login that does not
# exist, so these runs hold what real drivers say when they fail.
reference="--env uat --reason customer_complaint --server http://127.0.0.1:7879"
echo "== a connection that cannot be made"
# shellcheck disable=SC2086
refused=$(uv run owl validate topup tp_91f2b9 $reference 2>&1) || true
echo "$refused"
echo "$refused" | grep -q 'the connection was refused or could not be made'
# owl serve still starts on a lookup that breaks a save rule, and the run says so.
echo "$refused" | grep -q '^warning: Breaks the Time filter save rule in ledger: '
echo "== a login the database refuses"
# shellcheck disable=SC2086
denied=$(uv run owl inspect C204518 $reference 2>&1) || true
echo "$denied"
echo "$denied" | grep -q 'authentication failed'
runs=$(printf '%s\n%s\n' "$refused" "$denied" | sed -n 's/^Run \([0-9a-f-]\{36\}\): .*/\1/p')

echo "== no response names a host, port, login or secret reference"
uv run python ci/scan_responses.py "$PROBEOWL_M3_CONFIG" http://127.0.0.1:7878
# shellcheck disable=SC2086
uv run python ci/scan_responses.py "$PROBEOWL_M3_BAD_CONFIG" http://127.0.0.1:7879 $runs
echo "M3 exit check passed"
