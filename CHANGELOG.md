# Changelog

All notable changes to ProbeOwl are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0, a minor version may change
behaviour or the config format.

## [Unreleased]

<!-- Each release from v0.2 to v0.5 carries one phase of No YAML required, and only that phase:
v0.2 phase (a) first-run setup and Add connection, v0.3 phase (b) the entity graph builder,
v0.4 phase (c) blocks and probe by example, v0.5 phase (d) templates and New runbook.
See docs/implementation-plan.md, Releases. -->

## [0.1.0] - 2026-11-11

The first pre-release: phase 1, milestones M0 to M4, verified on test databases only.

### Added

- **Getting started.** `owl init` asks for a first application and its connector, creates the
  store, then writes the config folder and keeps passwords in the system keychain, never in a file;
  with the store, the setup is recorded in the audit. It offers a connection test and a schema
  import. `owl connector add` adds a connector, or bindings to one. `owl reload` loads a changed
  config folder into a running `owl serve`, refused while a run is running; open screens see it.
- **Entity wizard.** A new entity in the browser: locations from the table catalog, links
  suggested from the imported schema, and identifiers with unique and id-like columns first,
  checked on real plans before the file is written.
- **Probes.** Inspect and Validate probes in YAML: entities, identifiers and the root key,
  locations and the key graph (`key_from`, fallbacks), cardinality `one`, `optional` and `many`,
  fan-out with caps, expectations with conditions and set functions. A value no identifier pattern
  places is tried as each identifier, root key first, and the run says which one matched.
- **Connectors.** SQL Server (`mssql-python`), Oracle (`python-oracledb`, thin mode) and MariaDB
  (PyMySQL) adapters, and a REST connector, each with bindings per environment and a role.
- **Outcomes and evidence.** Found, Missing, Waiting, Blocked, Ambiguous, Error, Stale and Not
  configured, each with the exact query, parameters, guards and masked result behind it. A Missing on
  a Production replica whose lag is unknown carries a caveat.
- **Guards.** One read-only `SELECT`, run exactly as written; comments a database would run refused;
  a row ceiling per connector; statement, lock and call timeouts; parallel query off on Oracle,
  `MAXDOP 1` on SQL Server; times with an offset compared only when the SQL converts them; Oracle
  sessions on the binding's time zone or the database's own; replica lag and the circuit breaker;
  kill switches globally, per application and per connector.
- **Masking and audit.** PII masked in results, evidence and the store; every run, investigation
  record (reason, and a reference in Production) and copied write query audited. A change and its
  audit event commit together; a change whose event cannot be written is refused, and a file it
  wrote is put back.
- **Table catalog.** Schema import per dialect, suggested table labels, drift between the catalog
  and each environment, column roles.
- **Probe editor.** YAML editing with save checks (read-only SQL, label rules, PII masking, plans per
  dialect, time offsets), Undo, Redo and Revert to saved, and test runs of a draft.
- **Compare with a healthy record.** A known-good subject found by a sample lookup and proved by
  running the probe, compared field by field, with fields expected to vary shown but never
  highlighted.
- **Knowledge base.** Runbooks with steps: reviewed read queries that run with their plan checked,
  write queries that are only ever copied, external queries, manual steps and probe steps; runbooks
  suggested from a run's failing locations.
- **Plugins.** A first-party plugin system with declared capabilities, and the Compare files plugin.
- **Web app.** Validate, Inspect, Runs, Tables, Probe editor, Connectors and Knowledge base
  screens, served by `owl serve` from the wheel. The top bar names the person running it.
- **Guide.** From getting started and a first application to writing probes and runbooks, each
  web screen, safety, the CLI and troubleshooting; in the wheel and served by `owl serve` at
  `/guide/`, with no call outside the machine.
- **Store.** ProbeOwl's own data on SQLite, PostgreSQL, SQL Server, Oracle or MariaDB, with migrations.
- **CLI.** `owl serve` (the API, the web app and the guide), `owl init`, `owl connector add`,
  `owl reload`, `owl validate`, `owl inspect`, `owl check`, `owl store init`, `owl store upgrade`,
  `owl store ddl`, `owl catalog import`, `owl kill on`, `owl kill off`, `owl kill list`,
  `owl runbook hash`.

[Unreleased]: https://github.com/saadzarook/probeowl/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/saadzarook/probeowl/releases/tag/v0.1.0
