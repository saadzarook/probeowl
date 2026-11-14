# Your first application

This page sets ProbeOwl up for one of your own systems: an application, its environments and a
first connection, then an entity and a probe. It's all done in the browser. It assumes ProbeOwl is
installed, as in [Getting started](getting-started.md#install). To do the setup from a terminal
instead, see [For scripts](#for-scripts-owl-init).

## 1. Start `owl serve`

In a new, empty folder:

```sh
owl serve
```

It prints `Starting ProbeOwl…` at once, then its version and address once it's up. Open
<http://127.0.0.1:7878>. The first start after installing can take about 20 seconds on Windows.

With no `--config`, `owl serve` keeps your applications in `./probeowl`, or `~/probeowl` when it's
started from a system or program folder, and says which. It creates its store the first time, at
`~/.probeowl/store.db`, so every setup step is recorded in the audit. Set `$PROBEOWL_STORE_URL` for a
store on a server.

## 2. Set up the application

With no applications yet, the web app opens **Set up ProbeOwl**.

1. **What do you want to check?** Give the application a **name**, such as *Wallet*, and its
   **owning team**, such as `payments-team`. The id, `wallet`, comes from the name; it names the application's folder
   and appears in every URL. **Change the id** if you want another. An application spans its
   environments, so leave the environment out of its name.
2. **Environments:** tick the copies of it that probes can read: Dev, UAT, Staging and Production.
   Each gets its own connection. As you choose, the page shows the `application.yaml` it will
   write, as a diff. Nothing is written until **Create**, which writes exactly that file.

## 3. Add its first connection

After Create, **Add its first connection** opens **Add connection** in your first environment that
isn't Production. The top bar's environment is the one you're binding: change it there for
another.

- **Type:** SQL Server, Oracle, MariaDB or REST API.
- **Paste a connection string** (optional): JDBC, ODBC or a URL fills the fields below. It's read
  in the browser and never sent or stored, and a password in it moves to the password field.
- **Connector id:** suggested from the application and the type, such as `wallet_sqlserver`.
  Naming it after the system, such as `wallet_db`, reads better. Ids use a-z, 0-9 and `_`.
- **Host, port, database** (Oracle: the service name, and optionally a schema), the **read-only
  login** and its **password**. The [Connectors](connectors.md#what-the-login-needs) page lists
  what the login needs on each database.
- **Production must be a replica.** In Production, tick that it's a read replica, readable
  secondary or Active Data Guard standby. ProbeOwl never reads Production from the primary, and
  won't save the binding until it's ticked.
- **TLS:** **Verify the certificate** by default. **Trust this server's certificate**, or **No
  TLS** for MariaDB and Oracle, needs a reason, written beside the binding.

**Test connection** connects once with what you typed and says how long it took, or why it
failed. **Preview** shows the connector's file as a diff. **Save** writes that file, then puts the
password straight into this machine's keychain, as `probeowl/<connector>.<environment>`. No file or
log has it. If the keychain already holds a password under that name, it asks before overwriting
it. Then it tests the connection again, with the saved password.

## 4. Import its tables and confirm their labels

Once the connection works, **Import wallet_db's tables** reads its tables, columns and indexes, never data,
and suggests a label for each: Transaction, Master, Log, Audit and so on, with the columns that
matter (keys, time, status, links and personal data).

**Confirm all *n* as suggested** writes them to the application's `catalog.yaml`. To check or
correct them one by one, **Review them on Tables** instead. The labels set defaults, and
personal-data columns are masked everywhere.

Commit what setup wrote to git: `application.yaml`, the connector's file and `catalog.yaml`.
Passwords are never in them.

For more environments or systems, use **Add connection** on Connectors. Give an existing
connector's id to bind it in an environment it isn't bound in yet. See [Adding a
connector](connectors.md#adding-a-connector).

## 5. Write an entity

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

## 6. Write a probe, and run it

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

## For scripts: `owl init`

`owl init` does steps 2 to 4 from a terminal: the application, its environments, and a first
connector with a binding per environment. It asks all its questions first and writes nothing until
you have answered them, so stopping part-way leaves nothing behind.

```sh
owl init
owl serve --config probeowl
```

- It keeps the same rules as the browser. Production must be a replica, or it stops and writes
  nothing. Each password goes to your system keychain, never to a file. Trusting a certificate,
  or turning TLS off, needs a reason.
- It asks to create the store before writing anything: the setup is recorded in the audit, so
  without a store nothing is written. On a machine whose only keyring keeps secrets in files, it
  refuses before the first question.
- Then it offers to test each binding and to import the schema in your first non-Production
  environment. If you skip the import, use **Import from replica** on Tables, or `owl catalog
  import` while `owl serve` runs.

It writes `probeowl/<application>/application.yaml` and
`probeowl/<application>/connectors/<connector>.yaml`. Then start `owl serve --config probeowl`,
confirm the labels on **Tables**, and carry on from [step 5](#5-write-an-entity). The [CLI
reference](cli.md#owl-init) has the details.
