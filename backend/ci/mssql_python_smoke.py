# Author: @SaadZarook
"""Connect to SQL Server with mssql-python and print what the driver needed.

Run by the backend workflow inside a clean python:3.12-slim container, which has no ODBC
driver and no unixODBC, to show whether mssql-python on Linux needs either. Reads the
password from MSSQL_PW and the login from MSSQL_USER (default sa); never prints the password.
"""

import os
import subprocess
import sys
import time

import mssql_python

ODBC_LIBRARIES = subprocess.run(
    ["sh", "-c", "ldconfig -p | grep -i odbc || true"], capture_output=True, text=True, check=True
).stdout.strip()
print(f"ODBC libraries on the system: {ODBC_LIBRARIES or 'none'}")

connection_string = (
    f"Server=127.0.0.1,1433;Database=master;UID={os.environ.get('MSSQL_USER', 'sa')};"
    f"PWD={os.environ['MSSQL_PW']};Encrypt=yes;TrustServerCertificate=yes"
)
deadline = time.monotonic() + 120
while True:
    try:
        connection = mssql_python.connect(connection_string)
        break
    except mssql_python.OperationalError as err:
        if "Failed to load the driver" in str(err):
            sys.exit(f"mssql-python could not load its driver: {err}")
        # SQL Server takes a while to accept logins after its container starts.
        if time.monotonic() > deadline:
            sys.exit(f"SQL Server never accepted the login: {err}")
        time.sleep(3)

cursor = connection.cursor()
cursor.execute("SELECT @@VERSION")
print(f"Connected with mssql-python: {cursor.fetchone()[0].splitlines()[0]}")
connection.close()
