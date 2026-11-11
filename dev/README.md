# Dev stack

The database stack runs **only in CI** (`.github/workflows/dev-stack.yml`),
never on developer laptops. No container tool is needed locally; see
[Without containers](#without-containers) for the fake APIs and seeding.

Six containers on `127.0.0.1`, seeded with fake data (see `seed/PLANTED.md`).

```sh
docker compose -f dev/docker-compose.yml up --wait
```

`--wait` returns once every database is seeded and healthy. The first start
pulls images and the Oracle container takes a couple of minutes.

To try the examples on top of it in one command, run `bash dev/try.sh --docker`, or
`--native` for the databases `dev/native/` installs. It starts the stack and the fake APIs,
then copies `examples/` to `~/.probeowl/try`, with every password in the keychain under
`probeowl-try/` (never `probeowl/`). It gives the copy its own store, starts `owl serve` and
the web app, and stops what it started on Ctrl-C. `--reseed` seeds the Docker stack afresh.

| Service | Address | Production | UAT |
| --- | --- | --- | --- |
| `probeowl-store` (PostgreSQL 16) | `127.0.0.1:5432`, database `probeowl`, user `probeowl` | empty | empty |
| `wallet-sql` (SQL Server 2022) | `127.0.0.1:1433` | `wallet` | `wallet_uat` |
| `ledger-ora` (Oracle Free) | `127.0.0.1:1521`, service `FREEPDB1` | schema `LEDGER` | schema `LEDGER_UAT` |
| `access-maria` (MariaDB) | `127.0.0.1:3306` | `access` | `access_uat` |
| `fake-payments` (FastAPI) | `http://127.0.0.1:8081` | same | same |
| `fake-door-controller` (FastAPI) | `http://127.0.0.1:8082` | same | same |

## Logins and passwords (local use only)

Each probed database has `probeowl_ro` (read-only, used by ProbeOwl) and
`seed_rw` (used only by seed scripts and lock tests). On SQL Server,
`probeowl_ro` has `db_datareader` plus `SHOWPLAN`, so the save checks can read
query plans; `SHOWPLAN` lets it see plans, not run anything or read more data.

| Login | Password |
| --- | --- |
| `probeowl_ro` | `ProbeOwl_Ro_Local1` |
| `seed_rw` | `Seed_Rw_Local1` |

Store the Production password in the keychain
(`probeowl/wallet_db.prod`, `probeowl/ledger_db.prod`, `probeowl/access_db.prod`)
and export it for UAT:

```sh
export PROBEOWL_WALLET_DB_UAT=ProbeOwl_Ro_Local1
export PROBEOWL_LEDGER_DB_UAT=ProbeOwl_Ro_Local1
export PROBEOWL_ACCESS_DB_UAT=ProbeOwl_Ro_Local1
```

Never put these passwords in files under `examples/`.

Admin passwords (`sa`, Oracle `SYS`, MariaDB `root`, store user) are in
`docker-compose.yml`. Seeding runs once, on first start; `docker compose down -v`
and `up` again to reseed.

## Without containers

To verify a commit against real databases with no Docker and no Actions minutes, see
[native/README.md](native/README.md).

### Fake APIs

One command each, from the repository root. `uv` installs the dependencies
declared at the top of each script.

```sh
uv run dev/fakes/payments.py          # http://127.0.0.1:8081
uv run dev/fakes/door_controller.py   # http://127.0.0.1:8082
```

Both bind to `127.0.0.1`. Set `FAKE_PORT` to change the port.

### Seed a database

Each script seeds Production and UAT on any server, using settings from
environment variables. It needs the database's command-line client on `PATH`
(`sqlcmd`, `sqlplus` or `mariadb`) and an empty server: seeding creates
databases, schemas and the two logins, so run it once.

| Script | Client | Settings |
| --- | --- | --- |
| `seed/sqlserver/seed.sh` | `sqlcmd` | `SEED_PASSWORD` (required), `SEED_HOST` (`127.0.0.1`), `SEED_PORT` (`1433`), `SEED_USER` (`sa`) |
| `seed/oracle/seed.sh` | `sqlplus` | `SEED_ORACLE_CONNECT`, a SYSDBA connect string such as `sys/<password>@//host:1521/FREEPDB1 as sysdba` |
| `seed/mariadb/seed.sh` | `mariadb` | `SEED_PASSWORD` (required), `SEED_HOST`, `SEED_PORT` (`3306`), `SEED_USER` (`root`) |

```sh
SEED_PASSWORD=... SEED_HOST=db.internal bash dev/seed/sqlserver/seed.sh
```

The containers in CI run these same scripts on first start.

Card dates in the card access seed are relative to the day of seeding (valid
until 60 days after). A long-lived local stack drifts: if the card checks look
wrong, recreate it with `docker compose -f dev/docker-compose.yml down -v`
and start it again.

## Secret scan

Before the repository is made public, and after any history rewrite, scan every commit on every
ref with [gitleaks](https://github.com/gitleaks/gitleaks), its findings redacted:

```sh
gitleaks git --log-opts="--all" --redact --gitleaks-ignore-path dev/security/.gitleaksignore .
```

`dev/security/.gitleaksignore` lists the made-up test values in history that look like keys; a line
changed since carries an inline `gitleaks:allow` and the reason. Anything else it finds is a leak.
