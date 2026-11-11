<!-- Author: @SaadZarook -->
# ProbeOwl backend

The `probeowl` Python package: models, engine, connectors, guards, store, API and the `owl`
command. Python 3.12, managed with [uv](https://docs.astral.sh/uv/).

## Set up and check

```sh
cd backend
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy probeowl tests
uv run pytest
```

## The `owl` command

| Command | What it does |
| --- | --- |
| `uv run owl serve --config ../examples [--port 7878]` | Serve the API on 127.0.0.1 (`/docs`, `/openapi.json`) and run probes from the folder's YAML (`--config`, else `PROBEOWL_CONFIG`, else this folder) |
| `uv run owl validate topup tp_91f2c7 --env prod --reason incident --reference WALLET-482` | Start a validate run through `owl serve` (`--server`, else `PROBEOWL_SERVER`, else 127.0.0.1:7878), print each location as it is read, then the expectations and the run id. Exit 0 passed, 1 failed, 2 not run |
| `uv run owl check ../examples` | Check every YAML file in a folder and the references between them |
| `uv run owl store init [--url URL]` | Create or upgrade ProbeOwl's own store |
| `uv run owl store upgrade [--url URL]` | Upgrade the store after a new release |
| `uv run owl store ddl --dialect NAME` | Print the store SQL for `sqlite`, `postgresql`, `mssql`, `oracle` or `mariadb` |

The store is `--url`, else `PROBEOWL_STORE_URL`, else `~/.probeowl/store.db` (SQLite). Put a
URL that contains a password in the variable, not on the command line.

## Glossary: how a run ends

The full glossary is in `docs/design.md`. These are the rules the engine applies.

- **Outcome** (per location): Found, Missing, Blocked, Error, Ambiguous. **Blocked** means
  ProbeOwl could not look (the location it takes its key from was not found, or the link field
  is empty); it never means the record does not exist.
- **ExpectationResult**: **pass**, **fail**, or **skip**. An expectation is skipped when its
  step's condition (`when`) is false, or when a location it needs is Blocked or Error.
- **RunStatus**: a run **fails** on any failed expectation, any location with the Error outcome,
  or any expectation skipped because data it needs could not be read. A false condition skips
  its step and does not fail the run. Otherwise the run **passes**.
- A run is **refused**, not started, where any location's connector has no binding in the
  environment; `owl check` reports such a probe ahead of time.

## Store tests on other databases

Every store test runs on SQLite. The same tests also run on PostgreSQL, SQL Server, MariaDB
and Oracle, found in this order:

1. **A URL you give**, in one of these variables:

   | Database | Variable | Example |
   | --- | --- | --- |
   | PostgreSQL | `PROBEOWL_TEST_POSTGRESQL_URL` | `postgresql+psycopg://owl:…@host/probeowl_test` |
   | SQL Server | `PROBEOWL_TEST_MSSQL_URL` | `mssql+mssqlpython://owl:…@host:1433/probeowl_test?TrustServerCertificate=yes` |
   | MariaDB | `PROBEOWL_TEST_MARIADB_URL` | `mariadb+pymysql://owl:…@host/probeowl_test` |
   | Oracle | `PROBEOWL_TEST_ORACLE_URL` | `oracle+oracledb://probeowl_test:…@host:1521/?service_name=FREEPDB1` |

   The tests create and drop ProbeOwl's tables, so a URL is refused unless its database name
   (on Oracle, its user or service name) contains `test`. Set the variable in your shell
   session; never write the URL into a file.
2. **In CI**, a container started by Testcontainers (GitHub's runners have Docker).
3. **Otherwise** the tests skip, saying which variable to set. No one needs Docker locally.

To run only those tests: `uv run pytest -m database`.
