#!/bin/sh
# Author: @SaadZarook
# The M2 exit check, in CI against the dev stack: owl inspect C204518 in card access finds
# its visitor's three planted card issues, the email two profiles share is Ambiguous with
# both, and UAT uses its own bindings.
# Run from backend/, with the stack up and owl serve listening on 127.0.0.1:7878.
set -eu

failures=0
runs=""
inspect() {
    expected=$1
    shift
    set +e
    output=$(uv run owl inspect "$@" 2>&1)
    code=$?
    set -e
    echo "$output"
    runs="$runs $(echo "$output" | sed -n 's/^Run \([0-9a-f-]\{36\}\): .*/\1/p')"
    if [ "$code" != "$expected" ]; then
        echo "FAIL: owl inspect $* exited $code, expected $expected" >&2
        failures=$((failures + 1))
    fi
}
expect() {
    if ! echo "$output" | grep -Eq "$1"; then
        echo "FAIL: expected a line matching: $1" >&2
        failures=$((failures + 1))
    fi
}
reject() {
    if echo "$output" | grep -Fq "$1"; then
        echo "FAIL: the output shows $1" >&2
        failures=$((failures + 1))
    fi
}
prod="--env prod --reason customer_complaint --reference SUP-20931"

echo "== C204518 in Production: the card resolves to its visitor, with three issues"
# shellcheck disable=SC2086
inspect 1 C204518 $prod
expect '^Resolved card_id C204518 -> visitor_profile_id VP00012931 via cards\.card_id$'
expect '^  controller\[cards 3\] +Found$'
expect "^  fail count\(cards where status == 'active'\) <= 1  \(2 <= 1\)$"
expect "^  fail all\(cards where status == 'active', valid_to > now\(\)\)  \(fails for C198220\)$"
expect '^  fail all\(cards, controller\.status == status\)  \(fails for C177031\)$'
# The dev replica login cannot read replication status: a warning, not a failure. It names the
# privilege MariaDB refused: SLAVE MONITOR on 11.8, REPLICA MONITOR on some others, the same one.
expect "^warning: Replica lag unknown for access_db \(missing (REPLICA|SLAVE) MONITOR\): Missing results can't be confirmed fresh$"

echo "== the email VP00012931 and VP00008807 share: Ambiguous, nothing else runs"
# shellcheck disable=SC2086
inspect 3 contractor.one@example.com $prod
expect '^email \[masked\] matches 2 subjects via profile\.email; nothing else ran:$'
expect '^  VP00012931  '
expect '^  VP00008807  '
expect ': ambiguous$'
reject 'contractor.one@example.com'
reject 'controller['

echo "== C204518 in UAT: the same planted data, through the UAT bindings"
inspect 1 C204518 --env uat --reason customer_complaint
expect '^Run [0-9a-f-]{36}: Visitor cards \(card_access/visitor_cards\) on C204518 in uat$'
expect '^  fail all\(cards, controller\.status == status\)  \(fails for C177031\)$'

echo "== no response names a host, port, login or secret reference"
# shellcheck disable=SC2086
uv run python ci/scan_responses.py "${PROBEOWL_CONFIG:-../examples}" http://127.0.0.1:7878 $runs ||
    failures=$((failures + 1))

if [ "$failures" -ne 0 ]; then
    echo "M2 exit check: $failures failures" >&2
    exit 1
fi
echo "M2 exit check passed"
