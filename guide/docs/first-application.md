# Your first application

This page sets ProbeOwl up for one of your own systems: an application, its environments and a
first connector, then an entity and a probe. It assumes ProbeOwl is installed, as in
[Getting started](getting-started.md#install).

## 1. Run `owl init`

```sh
owl init
```

`owl init` asks its questions first, and writes nothing until you have answered all of them. If
you stop part-way, nothing is left behind.

```text
This writes a config folder and saves passwords in your system keychain, never in a file.
Config folder [./probeowl]:
Application id, such as wallet or card_access: wallet
Application title [Wallet]:
Owning team: payments-team
Environments, comma-separated [prod,uat]:
  Which one is Production? (none if none is) [prod]:
  Title for prod [Production]:
  Title for uat [UAT]:

First connector
Type (sqlserver, oracle, mariadb, rest) [sqlserver]:
Connector id [wallet_sqlserver]: wallet_db
  Binding for prod (Production)
    Host [127.0.0.1]: wallet-replica.internal
    Port [1433]:
    Database [wallet]:
    Read-only login: probeowl_ro
    Password (hidden; saved to the keychain as probeowl/wallet_db.prod):
    Is this a read replica, readable secondary or Active Data Guard standby? [y/N]: y
    Trust the server certificate without checking it? [y/N]:
  Binding for uat (UAT)
    ...
```

What to know about the questions:

- **Ids** (`wallet`, `wallet_db`) use a-z, 0-9 and `_`. They become file names and appear in
  lookups, so `owl init` asks again for anything else. The application id has no default: choose
  the product area's own name. An application spans its environments, so an id with an
  environment's word in it, such as `access_uat_test`, is offered again without it (`access`). The connector id suggested is the application and the database
  type, such as `wallet_sqlserver`; naming it after the system, such as `wallet_db`, reads better.
- **Production must be a replica.** ProbeOwl reads Production only from a read replica, readable
  secondary or Active Data Guard standby, never from the primary. If you answer no, `owl init`
  stops and writes nothing: ask your DBA for one, then run it again.
- **The login is read-only.** The [Connectors](connectors.md) page lists what it needs on each
  database.
- **Passwords** go into your system keychain, as `probeowl/<connector>.<environment>`. The files
  refer to them, and never contain them. On a machine whose only keyring keeps secrets in files,
  `owl init` refuses before the first question.
- **TLS:** MariaDB and Oracle connections are encrypted unless you say no and give a reason. SQL
  Server connections are always encrypted. Trusting a certificate without checking it needs a
  reason too.
- **Later environments** offer the earlier one's answers as defaults. Enter at the password
  prompt reuses the earlier password.

Then it asks about the store, before it writes anything: **Create the store**, ProbeOwl's own
database for runs and audit, at `~/.probeowl/store.db`, or `$PROBEOWL_STORE_URL`. With it, the
setup itself is recorded in the audit: if that can't be recorded, nothing is written and no
password is kept. Without it, the setup carries on and says it was not recorded; `owl store init`
creates the store later.

Then it offers two things:

1. **Test the connections**, binding by binding: `prod: reached in 42 ms`, or why not. A
   binding that isn't reached doesn't stop the setup. Fix it in its file and test it again from
   the Connectors screen.
2. **Import the connector's schema** in your first non-Production environment. It reads
   tables, columns and indexes, never data, so the Tables screen can list them with a suggested
   label for each. If you skip it, or it fails, the next steps say how to import later: **Import
   from replica** on the Tables screen, or `owl catalog import` while `owl serve` runs.

It writes two files:

```text
probeowl/wallet/application.yaml
probeowl/wallet/connectors/wallet_db.yaml
```

```yaml
# probeowl/wallet/connectors/wallet_db.yaml
connector: wallet_db
type: sqlserver
owner: payments-team
guards:
  row_ceiling: 1000
environments:
  prod:
    host: wallet-replica.internal
    port: 1433
    database: wallet
    user: probeowl_ro
    secret: keyring:probeowl/wallet_db.prod
    replica: true
  uat:
    ...
```

## 2. Start `owl serve`

```sh
owl serve --config probeowl
```

Open <http://127.0.0.1:7878>: `owl serve` serves the web app itself. The application is in the top
bar, with its environments.

## 3. Confirm the table labels

Open **Tables**. Each table the import found has a suggested label, such as Transaction, Master,
Log or Audit, and the columns that matter: keys, time, status, links and personal data. Confirm
or correct each one. ProbeOwl writes your answers to `catalog.yaml` in the application's folder.
The labels set defaults, and personal-data columns are masked everywhere.

## 4. Write an entity

An entity says what a subject is and where its data lives. The quickest way is **New entity…** in
the **Probe editor**: pick the root table from the imported schema and follow its keys to other
tables, then check and create it. See [New entity](web-app/probes.md#new-entity).

Or write it yourself. Create `probeowl/wallet/entities/topup.yaml`:

```yaml
entity: topup
title: Top-up
root_key: topup_id
owner: payments-team

identifiers:
  topup_id: { pattern: '^tp_[0-9a-z]{6}$', unique: true }

locations:
  record:
    title: Top-up record
    using: wallet_db
    lookup: >
      SELECT id AS topup_id, amount, currency, status, created_at
      FROM topups WHERE id = :topup_id
    exposes: [topup_id, amount, currency, status, created_at]
```

Every lookup is a single `SELECT`, with its keys as `:parameters`, never pasted into the text.
[Writing probes](writing-probes.md) adds more locations and the key graph that links them.

## 5. Write a probe, and run it

Open the **Probe editor**, choose the entity, and start a new probe:

```yaml
probe: topup_completed
title: Top-up completed
kind: validate
entity: topup
owner: payments-team
expect:
  - record.status in ['succeeded', 'failed', 'pending']
```

Saving runs the [save checks](safety.md#save-checks) and shows you the change before writing it.
Then run it from **Validate** with a real ID, or test-run the draft before you save.

Check the whole folder at any time:

```sh
owl check probeowl
```
