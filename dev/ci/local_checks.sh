#!/bin/bash
# Author: @SaadZarook
# What CI's check jobs run, locally, before a push: backend.yml's "check" and web.yml's
# "web-check", with the same commands. Database tests, the dev stack's exit checks and the wheel
# check have their own jobs; e2e.yml's tests against a real owl serve run with --live.
#   bash dev/ci/local_checks.sh          everything in those two jobs, as CI
#   bash dev/ci/local_checks.sh --fast   the same, without the full pytest run and the e2e suite
#   bash dev/ci/local_checks.sh --live   everything, then the live e2e tests on the native
#                                        databases (bash dev/native/verify.sh --live; clean tree)
# One difference: the web client is compared with --ignore-cr-at-eol, so a Windows checkout with
# core.autocrlf=true does not see line endings as a change.
set -euo pipefail
REPO="${PROBEOWL_REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
FAST=""
LIVE=""
case "${1:-}" in
  "") ;;
  --fast) FAST=1 ;;
  --live) LIVE=1 ;;
  *) echo "usage: bash dev/ci/local_checks.sh [--fast | --live]" >&2; exit 2 ;;
esac
step() { echo "== $*"; }

cd "$REPO/backend"
step "backend: uv sync --locked"
uv sync --locked -q
step "backend: lint"
uv run ruff check . ../scripts ../plugins
uv run ruff format --check . ../scripts ../plugins
step "backend: types"
uv run mypy probeowl tests ../plugins
step "backend: examples"
uv run owl check ../examples --no-connect
if [ -n "$FAST" ]; then
  step "backend: the contract matches the code (tests/test_api.py)"
  uv run pytest -q -p no:cacheprovider tests/test_api.py
else
  step "backend: tests"
  uv run pytest -m "not database" --cov=probeowl/guards --cov-fail-under=85
fi

cd "$REPO/web"
step "web: npm ci"
npm ci --silent
step "web: the client is up to date with the contract"
npm run -s generate:api
git diff --exit-code --ignore-cr-at-eol -- src/api/schema.d.ts
step "web: mocks match examples/"
npm run -s check:mocks
step "web: lint"
npm run -s lint
step "web: types"
npm run -s typecheck
if [ -z "$FAST" ]; then
  step "web: smoke and axe tests"
  npm run -s test:e2e
fi
# Line endings only: generate:api rewrites the file with LF on a CRLF checkout.
git -C "$REPO" checkout -- web/src/api/schema.d.ts

if [ -n "$LIVE" ]; then
  step "live: e2e against owl serve on the native databases"
  bash "$REPO/dev/native/verify.sh" --live
fi
echo "== all passed"
