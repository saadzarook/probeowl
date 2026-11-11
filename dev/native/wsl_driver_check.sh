#!/bin/bash
# Author: @SaadZarook
# Inside Ubuntu, the Linux driver job: mssql-python connects with no ODBC driver installed, only
# the three system packages (docs/decisions.md, 2026-10-31). verify.sh installs those as root
# first. CI does this in a clean container; here it is a venv, and the check below refuses a
# distro that has an ODBC driver, since that would prove nothing.
set -euo pipefail

if dpkg -l | grep -q -i -e msodbcsql -e unixodbc; then
  echo "an ODBC driver or unixODBC is installed in this distro, so this check proves nothing" >&2
  exit 1
fi
python3 -m venv .driver-check
.driver-check/bin/pip install --quiet mssql-python
.driver-check/bin/python backend/ci/mssql_python_smoke.py
