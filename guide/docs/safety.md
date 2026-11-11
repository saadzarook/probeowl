# Safety

ProbeOwl holds read access to Production systems, so it is built to refuse first. This page lists
each refusal, what it protects, and what to do about it.

## Read-only, always

**Every lookup is a single `SELECT`.** A lookup is parsed when it's saved and again before it
runs. Anything else is refused: `INSERT`, `UPDATE`, `DELETE`, DDL, `SELECT ... FOR UPDATE`, lock
hints, procedure calls, several statements, or `UNION` at the top (put it in a subquery). REST
connectors send only `GET`.

*What to do:* rewrite the lookup as one `SELECT`. To change data, write a runbook **write query**:
it's stored for a person to copy and run with their own access. ProbeOwl never runs one.

**Keys are always bound.** `:topup_id` is a parameter, never text pasted into the SQL, so a pasted
ID can't become an injection.

## Production reads replicas

A Production binding must have `replica: true`: a read replica, readable secondary or Active Data
Guard standby. `owl check`, `owl init` and `owl serve` refuse one that doesn't.

*What to do:* ask your DBA for a replica binding.

## Guards on every lookup

| Guard | What it does | If it trips |
| --- | --- | --- |
| Row cap | Each lookup returns at most its location's `max_rows`, or one row for `one` and `optional`, never more than the connector's row ceiling (default 1,000). The cap is added to the query itself (`TOP`, `FETCH FIRST`, `LIMIT`) | The result says "showing N of M", and a `one` location with several rows is **Ambiguous** |
| Statement timeout | 2 seconds per lookup by default | That location is **Error**: "the statement timed out after 2 s" |
| No waiting on locks | `LOCK_TIMEOUT 0` on SQL Server, short lock waits on MariaDB; Oracle readers never block | **Error**: "a lock was not granted in time" |
| Result size | At most 1 MB per lookup | Reading stops there, and the result says it was cut short |
| Run time | 60 seconds per run; locations not read by then are **Error** | The run says which |
| Concurrency | 4 runs at once | A new run is refused until one finishes |

A failure of one location is that location's **Error**, with the reason in its evidence. It never
crashes the run.

## Save checks

When a probe is saved, and with `owl check` for whole folders, ProbeOwl checks each lookup:

- it's read-only, as above;
- on a Transaction, Log or Audit table, it filters by a key or a bounded time window, so it never
  scans a growing table (*time filter*);
- its plan seeks an index rather than scanning the table (*plan check*, which needs the database);
- PII columns it selects are masked;
- `max_rows` is within the row ceiling;
- times with and without offsets are never compared.

*What to do:* each failed check says why, and what would pass. A Production login that can't read
plans makes the plan check impossible. Then saving needs your explicit acknowledgement, which is
recorded with your name. [Connectors](connectors.md#what-the-login-needs) lists the grant that
removes the need.

## Circuit breaker

Before a run's first lookup on a replica, ProbeOwl reads its lag. If the lag is longer than the
binding's `max_replica_lag` (60 seconds by default), the connector is paused, and runs that need it
are **Skipped**, not failed: "access_db is paused: replica lag 120 s (max 60 s)". The Connectors
screen shows the pause and when it ends.

*What to do:* wait. A replica that far behind would report records as Missing that only haven't
arrived yet.

## Kill switch

A kill switch stops every read at its next lookup: **global**, for one **application**, or for one
**connector**. Turning one on or off needs a reason, and is recorded with your name. A banner on
every screen says what's stopped and why, and runs are refused while it's on.

```sh
owl kill on --application wallet --connector ledger_db --reason "ledger failover in progress"
owl kill list
owl kill off --application wallet --connector ledger_db --reason "failover done"
```

## Masking

Personal data is masked in the runner, before anything is stored, shown or logged: columns listed
in a location's `pii`, REST fields in its `mask`, columns the catalog marks as PII, and identifiers
marked `pii: true`. A masked value shows as `[masked]` on screen, in evidence, in the CLI and in
the store. An identifier you typed that is PII, such as an email, is stored masked too.

## Production investigation record

Every run you start records a **reason**. In Production it also needs a **reference**: a Jira key,
support case or change ID. A Production run without one is refused before it starts. Every run
and every change is in the audit, with who, when and where.

## Local only

`owl serve` listens on `127.0.0.1` only. Two more checks stop other web pages in your browser
from using it:

- **Host check:** every request must name `owl serve` itself, which defeats DNS rebinding.
- **Origin check:** every request that changes something must come from the web app's own
  address and carry ProbeOwl's request header. `owl serve` accepts its own address and Vite's
  (`127.0.0.1` and `localhost`, ports 5173 and 4173). For anywhere else on your machine, start it
  with `--web-origin http://127.0.0.1:<port>`.

*What to do* if the web app says `owl serve` refused it: see
[Troubleshooting](troubleshooting.md#owl-serve-refuses-the-web-app).

## Credentials

Passwords live in your system keychain or in environment variables, never in a file. Bindings hold
references (`keyring:probeowl/wallet_db.prod`). Errors shown on screen or returned by the API are
fixed messages, without hosts, logins or passwords. The driver's own error text goes only to the
`owl serve` log, and only with `--debug-driver-errors`.
