#!/bin/bash
# Author: @SaadZarook
# Inside Ubuntu, in the commit's own tree on the distro's disk: the safety tests that skip on
# Windows, because links need rights there and POSIX modes do not exist. NTFS under /mnt/c has
# neither, so this must not run from the Windows checkout.
set -euo pipefail

cd backend
uv sync --locked --quiet
output=$(uv run pytest tests/test_config_folder.py tests/test_driver_log.py -q -rs --no-header 2>&1) || {
  echo "$output"
  exit 1
}
echo "$output"
# Only the junction test may skip here: junctions are a Windows kind of link.
if echo "$output" | grep '^SKIPPED' | grep -qv 'junctions are a Windows'; then
  echo "FAIL: a safety test skipped, so this proves nothing about it" >&2
  exit 1
fi
if ! echo "$output" | grep -Eq '[0-9]+ passed'; then
  echo "FAIL: no test passed" >&2
  exit 1
fi
