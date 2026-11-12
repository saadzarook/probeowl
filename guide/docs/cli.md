# CLI reference

`owl` is ProbeOwl's command line, installed with `uv tool install probeowl`. From a source
checkout, run it as `uv run owl ...` in `backend/`.

`owl serve` is ProbeOwl itself: it loads the config folder, holds the guards and runs every probe.
`owl validate`, `owl inspect`, `owl catalog import`, `owl kill` and `owl reload` are its clients,
like the web app, so a run from the command line is checked, limited and audited exactly like one from the
browser. Start `owl serve` first. They find it at `$PROBEOWL_SERVER`, else
`http://127.0.0.1:7878`, or wherever `--server` says. They take no `--config`.

`owl init`, `owl connector add`, `owl check`, `owl store` and `owl runbook hash` read the config
folder or the store themselves, and need nothing running.

| Command | What it does | Needs running |
| --- | --- | --- |
| `owl init` | Writes a config folder for a first application and connector, step by step | Nothing |
| `owl connector add` | Adds a connector to an application, or bindings for the environments one lacks | Nothing |
| `owl serve` | Serves ProbeOwl on 127.0.0.1: the web app at `/`, this guide at `/guide/`, the API under `/api` | The store (`owl store init`) |
| `owl check <folder>` | Checks every YAML file, the references between them, and every lookup | Nothing |
| `owl validate <entity> <value>` | Runs an entity's validate probe on one subject | `owl serve` |
| `owl inspect <value>` | Runs an inspect probe on a subject | `owl serve` |
| `owl catalog import <connector>` | Imports a SQL connector's schema: tables, columns, keys, indexes, row estimates; never data | `owl serve` |
| `owl kill on`, `off`, `list` | Turns kill switches on and off, and lists those on | `owl serve` |
| `owl reload` | Makes `owl serve` load its config folder again, without a restart | `owl serve` |
| `owl store init`, `upgrade` | Creates or upgrades ProbeOwl's own store | Nothing |
| `owl store ddl --dialect <name>` | Prints the SQL that creates the store, for a DBA to run | Nothing |
| `owl runbook hash <file>` | Prints each read query's `sql_sha256`, for its reviewed block | Nothing |

## Common options

| Option | On | Meaning |
| --- | --- | --- |
| `--env <name>` | validate, inspect, catalog import | The environment, such as `prod` or `uat`. Required |
| `--reason <reason>` | validate, inspect | Why: `incident`, `customer_complaint`, `deploy_verification`, `audit_or_compliance` or `proactive_check`. Required |
| `--reference <text>` | validate, inspect, kill | A Jira key, support case or change ID. Required in Production for runs |
| `--notes <text>` | validate, inspect | Free text, kept with the run |
| `--application <name>` | validate, inspect, catalog import, kill | Which application, when it isn't clear (required for catalog import) |
| `--probe <name>` | validate, inspect | Which probe, when the entity has several |
| `--server <url>` | every client | Where `owl serve` is. Default: `$PROBEOWL_SERVER`, else `http://127.0.0.1:7878` |
| `--timeout <seconds>` | validate, inspect | How long to follow the run (default 120) |
| `--url <url>` | serve, store | The store's SQLAlchemy URL. Default: `$PROBEOWL_STORE_URL`, else `~/.probeowl/store.db`. Use the variable for a URL with a password |

## `owl init`

Asks for the application, its environments and a first connector with a binding per environment,
then writes the folder and saves each password in your keychain. Then it offers to test the
connections, create the store and import the schema. See [Your first
application](first-application.md).

## `owl connector add`

```sh
owl connector add --config probeowl --application wallet
```

Adds a connector to an application, or, given an existing connector's id, bindings for the
environments it isn't bound in yet. It asks the same questions as `owl init`, with the connector's
existing binding as the defaults, and you choose which environments to bind. Each password goes to
your keychain, never to a file.

It writes one file: the new connector's, or the existing one with the new bindings added and its
comments kept. If the folder wouldn't load with that file, the change is undone and nothing is
saved, and the change is recorded in the audit. With no store at `~/.probeowl/store.db` it creates one first; a store named by `$PROBEOWL_STORE_URL` must already be at this release (`owl store init`). Then it offers to test the new bindings
and, for a SQL connector, to import its schema.

| Option | Default | Meaning |
| --- | --- | --- |
| `--config <folder>` | `$PROBEOWL_CONFIG`, else the current folder if it holds applications, else `./probeowl` | The config folder: the one `owl serve` was started with |
| `--application <name>` | Asked, when the folder holds several | The application to add to |

It works on a folder `owl serve` refuses because a probe needs a binding that isn't there, which
is how you add that binding. If others are still missing afterwards, it lists them. Then
`owl reload` loads the change into `owl serve`.

## `owl serve`

```sh
owl serve --config probeowl
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--config <folder>` | `$PROBEOWL_CONFIG`, else the current folder if it holds ProbeOwl applications (an `application.yaml` with `application:` and `environments:`), else `./probeowl`, or `~/probeowl` when the current folder is a system or program folder or can't be written to; created if missing, and printed with how to choose another | The applications to load |
| `--port <n>` | 7878 | The port on 127.0.0.1 |
| `--url <url>` | `$PROBEOWL_STORE_URL`, else `~/.probeowl/store.db`, created on a first run | The store |
| `--web-origin <url>` | `$PROBEOWL_WEB_ORIGINS` | Where else the web app is served from, on loopback only. Vite on 5173 and 4173 is always accepted. Repeat for several |
| `--log-file <path>` | `~/.probeowl/owl-serve.log` | The log, readable only by you |
| `--debug-driver-errors` | off | Log the drivers' raw error text, which can hold values from statements and personal data |

It refuses to start if the folder has errors (run `owl check` to see them all) or the store isn't
at this release (run `owl store upgrade`).

## `owl check`

```sh
owl check probeowl
owl check probeowl --no-connect
```

Checks the YAML against its schema, the references between files, and every lookup against the
save checks that need no database. For Production SQL bindings, it also asks whether the login can
read plans, and warns with the grant if not. `--no-connect` skips that. Exit code 1 when anything
is wrong.

## `owl validate` and `owl inspect`

```sh
owl validate topup tp_91f2c7 --env uat --reason incident
owl validate topup pay_7hq2m9 --env prod --reason customer_complaint --reference SUP-20931
owl inspect C204518 --env prod --reason customer_complaint --reference SUP-20931
```

Any identifier of the subject works: ProbeOwl recognises it by its pattern, or use `--identifier`
on `owl inspect`. The CLI prints each location as it's read, then each expectation, then the run's
status. Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Passed |
| 1 | Failed, or skipped (a paused connector, a kill switch) |
| 2 | Not run: refused before it started, or `owl serve` couldn't be reached or followed |
| 3 | Ambiguous (inspect): the value matched several subjects, and nothing else ran |

The run header names the probe by its title, then its id:
`Run 9305b99c-...: Top-up completed (wallet/topup_completed) on tp_91f2c7 in uat`.

## `owl catalog import`

```sh
owl catalog import wallet_db --application wallet --env uat
```

Reads the schema, never data, and stores it. The Tables screen then suggests a label for each
table.

## `owl kill`

```sh
owl kill on --all --reason "replica on fire"
owl kill on --application wallet --reason "wallet incident"
owl kill on --application wallet --connector ledger_db --reason "ledger failover"
owl kill list
owl kill off --all --reason "resolved" --reference INC-1042
```

`--all` is the global switch, asked for by name. A connector switch covers that connector in every
environment. Turning one off leaves broader switches that are on in place. See
[Safety](safety.md#kill-switch).

## `owl reload`

```sh
owl reload
```

Makes `owl serve` read its config folder again and load it, as it does at start, so added or
changed connectors, bindings, entities and probes are loaded without a restart. It prints what it
loaded and which applications and connectors changed, and is recorded in the audit. The
**Reload configuration** button on the Connectors screen does the same. Screens already open in
the browser read again within about 15 seconds, or at their next request, and say so; a draft in
the Probe editor is kept and checked again against what was loaded.

If `owl serve` would refuse the folder at start, nothing changes and the first error is shown
(`owl check <folder>` lists them all). It's refused while a run is running. Kill switches stay as
they are. Plugins, the port, the store and the log file change only with a restart.

## `owl store`

```sh
owl store init                                  # create, or upgrade to this release
owl store upgrade
owl store ddl --dialect postgresql > store.sql  # for a DBA: sqlite, postgresql, mssql, oracle, mariadb
```

The store holds runs, the audit, schema imports and kill switches. It's never a probed database.
`owl serve` and `owl connector add` create the default SQLite store themselves the first time; a
store named by `--url` or `$PROBEOWL_STORE_URL` is never created for you, as a server database may
need a DBA.
SQLite on a network share loses writes when several people use it, so `owl store init` refuses
one: use a server database for a team store.
