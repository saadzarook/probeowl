# ProbeOwl — roadmap

ProbeOwl is one repository with a Python backend and a React web app, joined by an API contract
written first. This page covers what it is built with, how it runs, and what each release holds.
How to contribute is in [CONTRIBUTING.md](../CONTRIBUTING.md); what ProbeOwl does and why is in
[design.md](design.md).

| Decision | Choice |
| --- | --- |
| Who uses it | Anyone, on their own machine; a team can share one setup |
| Delivery | One package installed with `uv tool install`, plus an optional Docker image; `owl serve` opens the web app on localhost |
| ProbeOwl's own store | SQLite by default for one person; PostgreSQL, SQL Server, Oracle or MariaDB for a shared team store. `owl store init` creates the schema, or exports it as SQL for a DBA |
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| SQL safety | sqlglot to parse and classify queries in the T-SQL, Oracle and MySQL dialects; every query runs exactly as written |
| Frontend | React with TypeScript and Vite, shipped inside the package |
| Config | YAML in a folder or git repository, validated by schemas generated from the backend models |
| Glue | The OpenAPI contract and YAML schemas in `contracts/`; checks hold both sides to them |
| Later | A hosted option on the same package, with SSO and workspaces |

## Tech stack

| Layer | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12 | sqlglot, mature drivers, one language for the engine, API and CLI |
| API | FastAPI with Pydantic v2 | Typed models produce the OpenAPI contract the web app's client is generated from |
| SQL parsing | sqlglot | Classifies statements (only a single `SELECT` may run) and finds the tables for the catalog. MariaDB uses the MySQL dialect; MariaDB-only syntax gets tests |
| SQL Server driver | `mssql-python`, Microsoft's driver | Wheels for Linux, macOS and Windows; no separate ODBC install |
| Oracle driver | `python-oracledb` in thin mode | Connects directly, with no Oracle Client libraries |
| MariaDB driver | PyMySQL | Pure Python, no system library |
| HTTP connectors | httpx | Timeouts and rate budgets per connector |
| ProbeOwl store | SQLAlchemy 2 Core with Alembic migrations, on SQLite, PostgreSQL, SQL Server, Oracle or MariaDB | One schema definition in whichever database the user picks; migrations can also be printed as SQL for a DBA |
| Config | YAML files, Pydantic models exported as JSON Schema | Editors autocomplete probe files; checks validate them |
| CLI | Typer, command `owl` | `owl serve`, `owl init`, `owl store init`, `owl inspect`, `owl validate`, `owl check` |
| Web app | React 18, TypeScript, Vite, TanStack Query, React Router | Built once and served by `owl serve`, so users install nothing extra |
| UI components | Radix UI primitives with CSS variables from the brand sheet | Accessible by default |
| API client | `openapi-typescript` and `openapi-fetch`, MSW mocks from the same spec | The web app builds and tests without a backend |
| Secrets | The operating system's keychain through `keyring`; environment variables as a fallback | Credentials never sit in probe files or plain-text config |
| Identity | Locally, the operating system user, shown in every audit record. Hosted, later: SSO | No login screen on one machine |
| Scheduler (phase 2) | APScheduler inside `owl serve`, jobs kept in the store | Not needed for phase 1 |

## Packaging and ways to run it

ProbeOwl runs on the user's own machine and listens only on localhost. The same package covers one
person and a team; what changes is where the store and the probe files live.

| Mode | Store | Probe files | Who is in the audit | Status |
| --- | --- | --- | --- | --- |
| Personal | A SQLite file in the user's ProbeOwl folder | A local folder | The operating system user | Phase 1 |
| Team | A shared database the team picks: PostgreSQL, SQL Server, Oracle or MariaDB | A shared git repository each member pulls | Each member's operating system user | Phase 1 |
| Hosted | Managed by the service | Synced from git | SSO identity | Later |

**First run:**

1. `uv tool install probeowl` (uv also installs the right Python version).
2. `owl init` asks where the store should live (SQLite by default) and where the probe files are;
   credentials go to the system keychain.
3. `owl store init` creates ProbeOwl's tables. Where only a DBA may create tables,
   `owl store ddl --dialect oracle > probeowl.sql` prints the script instead.
4. `owl serve` opens the web app at `http://127.0.0.1:7878`.

**Rules for the store:**

- It holds only ProbeOwl's own data: runs, evidence, audit, catalog, runbook index. It never sits in
  a database ProbeOwl probes.
- It needs a login that can write to its own schema, and nothing else.
- Each release checks the store's schema version on start and asks for `owl store upgrade` if needed.
- In team mode the audit is as trustworthy as the store's permissions: give members insert-only
  rights on the audit table. Proper identity comes with SSO.

Every stored row carries a workspace ID, identity sits behind one interface, and nothing assumes a
single machine, so a hosted option can follow without a redesign.

## Architecture

The web app and the CLI both call one Python package. Inside it, every lookup goes from the engine
through the guards before a connector touches a database, so no client can reach a system without
the same limits and masking. One package with sub-modules, not separate services, keeps it simple:
the API, the CLI and later the scheduler import the same engine.

| Path | Contents |
| --- | --- |
| `contracts/` | The API contract (`openapi.yaml`) and JSON Schemas for the YAML files |
| `backend/probeowl/model/` | Pydantic models: entity, location, identifier, probe, run, outcome |
| `backend/probeowl/engine/` | Key graph resolution, cardinality, fan-out, expectations, comparison |
| `backend/probeowl/connectors/` | The SQL Server, Oracle and MariaDB adapters; the REST connector |
| `backend/probeowl/guards/` | The statement guard, row caps, the circuit breaker, kill switches |
| `backend/probeowl/checks/` | Save checks: read-only SQL, label rules, plans, time offsets |
| `backend/probeowl/store/` | The store's tables, migrations (Alembic), runs and audit |
| `backend/probeowl/api/`, `cli/` | The FastAPI routes and the `owl` command |
| `web/` | The React app, its MSW mocks and Playwright tests |
| `plugins/` | First-party plugins, such as Compare files |
| `dev/` | The local stack: Docker Compose, seed data, fake payment and door-controller APIs |
| `examples/` | Two example applications, Wallet and Card access |

## Phase 1 milestones

Six milestones, each ending in something a person can run and check.

**M0 — Foundations.** Done. The backend package, models and generated schemas, the first API
contract, the store on SQLite, `owl serve`; the web shell against mocks; the local stack with the
four databases seeded.

**M1 — First validate run.** Done. The SQL Server and Oracle adapters with their guards; the REST
connector; the key graph with `key_from` and fallbacks; outcomes Found, Missing, Blocked and Error;
expectations with conditions; runs, evidence and the investigation record; the Validate screen.
Done when `topup_completed` finds the planted missing-ledger bug from the CLI and the web app.

**M2 — Inspect and many results.** Done. The MariaDB adapter; identifiers and their detection;
cardinality, fan-out caps and Ambiguous; set expectations; environment bindings and Not configured;
replica lag and Stale; the circuit breaker; the Inspect screen.

**M3 — Table catalog and probe editor.** Done. Schema import per dialect, label suggestions, drift
per environment; save checks (read-only SQL, plans per dialect, label rules); test runs of a draft
probe; the Tables, Probe editor and Connectors screens.

**M4 — Compare and knowledge base.** Done. The kill switch; the plugin foundation and the Compare
files plugin on a shared comparison engine; Test connection and the Connectors screen's limits;
compare with a healthy record; runbooks with reviewed reads, write queries copied and never run,
suggestions from failing locations.

**M5 — Package and share.** Next. Store migrations tested on all five databases; `owl init`,
`owl store init`, `owl store ddl`, `owl store upgrade`; team mode with a shared store; the guard
test suite complete; the web app bundled into the package; first-run screens; an install guide.
Also Add connector and Edit guards from the web, after a rule decision on writing config from the
web. Done when a teammate installs ProbeOwl on a clean laptop, points it at a shared store and probe
repository, and runs an inspect against UAT without help.

Left for phase 2, as in the design: Dynatrace and log locations, the event index, scheduled health
probes with limited follow-up, Slack, the Jira story draft, the change timeline, and third-party
plugins.

## Releases

- **v0.x, pre-release.** The repository is public and labelled pre-release, so people can try
  ProbeOwl on test databases. Nothing is relied on against real data yet.
- **v0.2 to v0.5, No YAML required, one phase per release.** The v0.2 design (Phase 0, reviewed)
  ships in four releases, not all at once:
  - **v0.2, phase (a).** First-run setup in the browser, and the Add connection form, with the
    missing-password prompt for a teammate whose keychain has no password yet, and Change password.
    Each secret save (Add connection, the missing-password prompt, Change password) commits
    its audit event with the keychain write: the event is written in a transaction that commits
    last, and if the audit fails the keychain entry is put back as it was and the save is refused,
    as file saves and `owl connector add` do since #273. Also in this phase: revisit setup without
    a store (today `owl init` and `owl connector add` carry on with an on-screen warning).
  - **v0.3, phase (b).** The entity graph builder.
  - **v0.4, phase (c).** Blocks, and probe by example.
  - **v0.5, phase (d).** Templates, and the New runbook form.

  Each release goes out only after the same checks as v0.1: the wheel check
  (`dev/release/wheel_check.sh`), the native runs on each affected dialect, and a newcomer retest
  by the maintainer on the release wheel. A phase that is not ready waits for the next release; a
  later phase does not ship first.
- **v1.0.** When M0 to M5 are complete, with full CI passing on all five store databases, the
  database tests and exit checks verified, and a review of the guards by someone other than their
  author.

## Risks and open decisions

| Risk | Effect | Mitigation |
| --- | --- | --- |
| Five store databases to support | Every schema change must migrate on SQLite, PostgreSQL, SQL Server, Oracle and MariaDB | A small, portable store schema; CI runs every migration on all five |
| SQLite shared by several people | Locking and lost writes | SQLite is for personal mode only; `owl init` refuses a SQLite file on a network share |
| Team audit without SSO | A member with database rights could alter records | Insert-only rights on the audit table; SSO when hosted |
| Database editions differ | Some guards rely on edition features: SQL Server Resource Governor, Oracle Resource Manager, readable standbys | Every guard has a client-side fallback (statement timeout, row cap) that works on any edition |
| MariaDB-only syntax | sqlglot has no separate MariaDB dialect | Parse with the MySQL dialect; tests on real MariaDB catch gaps |
| Contract churn | Each change costs both sides a cycle | Change the contract at milestone boundaries, in one reviewed PR with its code |
| Safety bugs in guards | A probe loads a database or reads unmasked data | The guard test suite, read-only logins as the last line of defence, a review before production use |

Open decisions:

- [ ] Publishing: GitHub Releases, then PyPI.
- [ ] Production database editions, once probes point at production replicas.
- [ ] A hosted model, once the local version is proven.
