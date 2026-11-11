#!/bin/sh
# Author: @SaadZarook
# Run inside a clean python:3.12-slim container (no ODBC driver, no unixODBC). Checks that
# mssql-python on Debian or Ubuntu needs no separately installed ODBC driver, only these
# three system packages (see docs/decisions.md, 2026-10-31).
set -eu
export DEBIAN_FRONTEND=noninteractive

apt-get update -qq
apt-get install -y -qq libltdl7 libkrb5-3 libgssapi-krb5-2 >/dev/null
pip install --quiet --root-user-action=ignore mssql-python

if dpkg -l | grep -q -i -e msodbcsql -e unixodbc; then
    echo "an ODBC driver or unixODBC is installed, so this check proves nothing" >&2
    exit 1
fi
python /ci/mssql_python_smoke.py
