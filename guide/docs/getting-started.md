# Getting started

## Install

You need [uv](https://docs.astral.sh/uv/). The web app comes in the package, so nothing else:

```sh
uv tool install probeowl
uv tool update-shell
```

`uv tool update-shell` adds uv's tool folder to your PATH, once per machine. Open a new terminal
after it. `owl serve` prints its version on its first line as it starts, which shows the install
worked. If a new terminal says `owl` isn't found, run `uv tool update-shell` again and open another
one.

Then [Your first application](first-application.md) sets it up for one of your own systems. To
see what it does first, try the examples.

## The examples

ProbeOwl comes with two example applications. Their test data has problems planted in it on
purpose, so you can see what ProbeOwl reports before pointing it at anything of yours:

- **Wallet**: top-ups, read from SQL Server, Oracle and a payments API.
- **Card access**: visitors and their cards, read from MariaDB and a door controller API.

The examples run from a source checkout. You need uv, [Node.js](https://nodejs.org/), and for the
example databases, either Docker or the native install described below.

```sh
git clone https://github.com/saadzarook/probeowl.git
cd probeowl
```

## Try the examples

### With Docker

```sh
bash dev/try.sh --docker
```

This one command:

1. Starts the example databases and the two fake APIs with Docker Compose. The first start pulls
   the images, and Oracle takes a few minutes to seed.
2. Copies `examples/` to `~/.probeowl/try`. The examples' passwords go into your system keychain
   under `probeowl-try/`, so they can never overwrite an entry a real connector uses.
3. Gives the copy its own store, and starts `owl serve` and the web app.

Open <http://127.0.0.1:5173> when it says it's up. Ctrl-C stops everything it started. Add
`--reseed` to start the planted data afresh: some card dates are relative to the day the data was
seeded.

### With native databases

If you installed SQL Server, MariaDB and Oracle on your machine with `dev/native/` (see its
README), use:

```sh
bash dev/try.sh --native
```

Oracle runs in WSL and stops when nothing uses it. Keep it running in a window of its own with
`bash dev/native/oracle_awake.sh`.

### Ports

`try.sh` uses 7878 for `owl serve` and 5173 for the web app, and refuses to start if either is
taken. Choose others with `--api-port` and `--web-port`.

## What to try

| Where | Enter | What you should see |
| --- | --- | --- |
| Validate, Wallet, UAT | `tp_91f2c7` | Failed: the ledger entries are Missing, though the payment was captured |
| Validate, Wallet, UAT | `tp_91f2b9` | Passed: every location Found |
| Validate, Wallet, UAT | `tp_93b001` | Failed: the ledger is Blocked, because the top-up never saved its ledger link |
| Inspect, Card access | `C204518` | Three card issues for visitor VP00012931 |
| Inspect, Card access | `a.rahman@example.com` | Ambiguous: the email belongs to two visitors |

In Production, every run needs a reason and a reference, as it would for real.

From another terminal, the same through the command line:

```sh
cd backend
uv run owl validate topup tp_91f2c7 --env uat --reason incident
```

## Without databases: mocks only

To see every screen without any database, run the web app against its built-in mocks:

```sh
cd web
npm ci
VITE_MOCK_API=true npm run dev
```

Nothing here talks to `owl serve`: the answers are fixed, and match the examples.

## Next

[Your first application](first-application.md) sets ProbeOwl up for one of your own systems.
