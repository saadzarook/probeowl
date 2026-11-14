# ProbeOwl

Validate production data across databases and APIs, safely.

When a payment is captured but never reaches the ledger, or a visitor's card is active in one system
and revoked in another, someone opens a SQL client, a provider dashboard and a log search to find
out. ProbeOwl turns that into a check you run by ID: it looks the record up in every system it
touches, checks each one is in the state you expect, and shows the evidence behind every answer.

```text
$ owl validate topup tp_91f2c7 --env prod --reason incident --reference INC-1042
Run d0331681-…: wallet/topup_completed on tp_91f2c7 in prod
  record           Found
  webhook          Found
  ledger           Missing - replica lag unknown, the record may not have arrived yet
  payment          Found
  pass payment.amount == record.amount
  fail count(ledger) == 2  (0 == 2)  - reads a Missing record that may not have arrived yet: replica lag unknown
  …
Run d0331681-…: failed - replica lag unknown: 1 Missing result may not have arrived yet
```

> **Status: pre-release (v0.x).** ProbeOwl works end to end on test databases. It is not yet
> verified for production use; see the [roadmap](docs/implementation-plan.md).

## What it does

- **Inspect** a subject's current state, or **Validate** that a flow executed correctly, from the
  web app or the `owl` command, by any of the subject's IDs.
- Follows one record across **SQL Server, Oracle, MariaDB and REST APIs**: where it lives, how each
  location gets its key, one row or many.
- Shows the **evidence** for every outcome: the exact query, its parameters, the guards it ran
  under, and the result, with PII masked.
- **Compares** a failing record with a known-good one, and suggests the **runbook** for what it
  found: reviewed read queries run, write queries are only ever copied for a change request.
- Keeps a **table catalog** of what each table is, and checks every probe before it is saved.

## Safe by construction

ProbeOwl holds read access to production, so it is built to be the most locked-down tool you run:

- **Read only.** A probe is one `SELECT`, run exactly as written, with bound parameters. Anything
  else, including a comment the database would execute, is refused.
- **Bounded.** Row caps, statement and lock timeouts, no parallel query, a circuit breaker per
  connector, and kill switches globally, per application and per connector.
- **Masked.** PII is masked in every result, in evidence and in the store.
- **Audited.** Every run records who ran it, why (a reason, and a reference in Production) and what
  it returned.
- **Local.** It runs on your machine and listens only on `127.0.0.1`. Credentials stay in your
  system keychain, never in files.

## Install

You need [uv](https://docs.astral.sh/uv/). Nothing else: the web app comes in the package.

```sh
uv tool install probeowl
uv tool update-shell    # once: puts owl on your PATH; then open a new terminal
owl serve               # ProbeOwl and its web app on http://127.0.0.1:7878
```

`uv tool install` puts `owl` in uv's tool folder, which a new terminal doesn't find until
`uv tool update-shell` has added it to your PATH. That's needed once per machine.

Then open <http://127.0.0.1:7878>. With no applications yet, it opens **Set up ProbeOwl**, which
walks you through an application, its environments and a first connection, then importing its
tables. Each password goes to your system keychain, never to a file. Run `owl serve` in a new,
empty folder: it keeps your applications in `./probeowl` and says where. The guide is at
<http://127.0.0.1:7878/guide/>, served by `owl serve` too, so it reads offline:
[Your first application](https://saadzarook.github.io/probeowl/guide/first-application/) goes
through the setup step by step.

For scripts, `owl init` does the same setup in the terminal. It asks its questions, writes the
config folder and saves the passwords in your keychain; then start `owl serve --config probeowl`.

## Try the examples

Two example applications come with data that has problems planted in it. They run from a checkout,
with Node.js and either Docker or the databases `dev/native/` installs:

```sh
git clone https://github.com/saadzarook/probeowl.git && cd probeowl
bash dev/try.sh --docker     # or --native
```

It starts the databases and fake APIs, copies the examples with their passwords in your keychain
under `probeowl-try/`, and starts `owl serve` and the web app. Ctrl-C stops everything it started.
Then inspect `C204518` in Card access, or validate top-up `tp_91f2c7` in Wallet. From another
terminal, the same through the CLI:

```sh
cd backend && uv run owl validate topup tp_91f2c7 --env uat --reason incident
```

## Working on ProbeOwl

From a checkout, `cd backend && uv sync` installs the backend, and `uv run owl ...` runs it.
`owl serve` serves `web/dist` once you've built it with `cd web && npm ci && npm run build`. While
working on the web app, run `npm run dev` instead (Vite on <http://127.0.0.1:5173>), or
`VITE_MOCK_API=true npm run dev` for every screen against mocks, with no `owl serve` at all. `uv
build --wheel` in `backend/` builds a release wheel with the web app inside. See
[CONTRIBUTING.md](CONTRIBUTING.md).

### The CLI and `owl serve`

`owl serve` is ProbeOwl: it loads the config folder (`--config`), holds the guards, and runs every
probe. `owl validate`, `owl inspect`, `owl catalog import` and `owl kill` are its clients, like the
web app, so a run from the command line is checked, limited and audited exactly like one from the
browser. They take no `--config`: start `owl serve` first, and they reach it at `$PROBEOWL_SERVER`,
else `http://127.0.0.1:7878` (`--server` to change). Only `owl check` and `owl store` read the
config folder and the store themselves.

## Documentation

- [Design](docs/design.md): what ProbeOwl does and why, and the glossary
- [Roadmap](docs/implementation-plan.md): the stack, milestones and releases
- [Connectors](docs/connectors.md): what a read login needs on each database
- [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), [Code of conduct](CODE_OF_CONDUCT.md)

## Licence

[Apache License 2.0](LICENSE). See [NOTICE](NOTICE).
