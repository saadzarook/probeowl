# Connectors

A **connector** is a read-only client for one system: a SQL Server, Oracle or MariaDB database, or
a REST API. It has one **binding** per environment, saying where it points there.

## Adding a connector

`owl init` writes your first connector. For the next ones, run this in your config folder, the
one `owl serve` was started with:

```sh
owl connector add --application wallet
```

It asks for the connector's type and a binding per environment, keeps each password in your
keychain, and checks the folder still loads before it keeps the file. Given an existing
connector's id, it adds bindings for the environments that connector lacks. See [the CLI
reference](cli.md#owl-connector-add).

Or write the file yourself, in the application's `connectors/` folder:

```yaml
# wallet/connectors/ledger_db.yaml
connector: ledger_db
type: oracle                 # sqlserver, oracle, mariadb or rest
owner: payments-team
guards:
  row_ceiling: 1000          # the most rows any lookup on it can return
environments:
  prod:
    host: ledger-standby.internal
    port: 1521
    database: LEDGERPDB      # Oracle: the service name
    schema: LEDGER           # Oracle only: where unqualified table names resolve
    user: probeowl_ro
    secret: keyring:probeowl/ledger_db.prod
    replica: true            # an Active Data Guard standby
  uat:
    host: ledger-uat.internal
    port: 1521
    database: LEDGERPDB
    schema: LEDGER_UAT
    user: probeowl_ro
    secret: keyring:probeowl/ledger_db.uat
```

Then save each password in your system keychain, under the name its `secret` gives:

```sh
uvx keyring set probeowl ledger_db.prod
```

A `secret` is a reference, never the password itself: `keyring:<service>/<name>` reads the system
keychain, and `env:<VARIABLE>` reads an environment variable. ProbeOwl won't read a keyring that
keeps secrets in files rather than in your operating system's keychain.

Then load it into `owl serve`: **Reload configuration** on the **Connectors** screen, or
`owl reload`. Check the connector there with **Test connection**. `owl check <folder>` checks the files without starting anything.

A connector with no binding in an environment is **Not configured** there: probes that need it
can't run in that environment, and `owl check` says so.

### Binding settings

| Setting | For | Meaning |
| --- | --- | --- |
| `host`, `port` | SQL | Where the database listens |
| `database` | SQL | The database (SQL Server, MariaDB) or service name (Oracle) |
| `schema` | Oracle | The schema unqualified table names resolve in |
| `user`, `secret` | SQL | The read-only login, and where its password is |
| `replica` | SQL | This binding reads a replica. Production bindings must |
| `max_replica_lag` | SQL | Past this lag (default `60s`), the circuit breaker pauses the connector |
| `allowed_databases` | SQL Server, MariaDB | Other databases on the same server that lookups may name |
| `encrypt`, `encrypt_reason` | MariaDB, Oracle | TLS is on unless you turn it off and say why. SQL Server always encrypts |
| `trust_server_certificate`, `..._reason` | SQL | Accept any server certificate, with a reason, as for a local test container |
| `time_zone` | Oracle | Each session's time zone, as an offset; default: the database's own |
| `base_url`, `secret` | REST | The API's address, and its token if it needs one |

`owl check` refuses a Production binding that isn't a replica, and one that turns off TLS or trusts
any certificate without a reason.

## What the login needs

A probe login reads, and nothing else. Ask your DBA for a login with:

| Database | To read | For the index check at save time |
| --- | --- | --- |
| SQL Server | `db_datareader`, or `SELECT` on the tables probes read | `GRANT SHOWPLAN TO <login>` in the database |
| MariaDB | `SELECT` on the tables probes read | Nothing more: `EXPLAIN` needs only `SELECT` |
| Oracle | `SELECT` on the tables probes read | Write access to `PLAN_TABLE` (public by default; a read-only standby has none) |

`SHOWPLAN` shows plans; it doesn't let the login run anything else or read more data. Without the
plan grant, saving a probe for Production needs your acknowledgement that its plan couldn't be
checked, recorded with your name, and `owl check` warns and names the grant.

### Replica lag

Before a run's first lookup on a replica, ProbeOwl reads how far it is behind its primary. This
needs one more grant. It's optional, but without it lag is always unknown:

| Database | Grant |
| --- | --- |
| SQL Server 2022 and later | `GRANT VIEW DATABASE PERFORMANCE STATE TO <login>;` in the database |
| MariaDB 10.5.9 and later | `GRANT REPLICA MONITOR ON *.* TO '<login>'@'<host>';` (same as `SLAVE MONITOR`) |
| Oracle | `GRANT SELECT ON SYS.V_$DATAGUARD_STATS TO <login>;` |

Without it:

- the Connectors screen and each run's evidence say `replica lag unknown: missing <grant>`;
- the circuit breaker can't pause the connector for lag;
- a location is never Stale, so a record that hasn't reached the replica yet shows as Missing,
  with a caveat in Production.

### Runbook steps on Oracle's `v$` views

A runbook read step on a dynamic performance view, such as `v$session`, needs `SELECT` on the view
itself: `GRANT SELECT ON SYS.V_$SESSION TO <login>;`. Without it, the step is copy only, and says
which grant is missing.

## Time offsets

ProbeOwl never compares a time that has an offset (SQL Server `datetimeoffset`, Oracle
`TIMESTAMP WITH TIME ZONE`) with a column that has none, because the database would read one of
them in a time zone it guesses, and match rows hours off. Saving refuses such a lookup unless the
SQL converts the value, for example `CAST(:at AS datetime2)`, and names the conversions that work.
A run refuses a typed time with an offset against a column without one, and the other way round.
These checks use the column types from the latest schema import.

## Test connection

The **Connectors** screen shows each connector's health, role, replica lag and plan access in the
chosen environment. **Test connection** connects once, now, and says how long it took or why it
failed, as a fixed message that never contains hosts, logins or passwords.
