# ProbeOwl — guide for coding agents

ProbeOwl validates production data across databases and APIs, safely. Engineers
inspect a subject's current state, validate that a flow executed correctly, and
(later) run health probes on a schedule. It runs locally on each user's machine.

These rules apply to every contributor, person or agent. [CONTRIBUTING.md](CONTRIBUTING.md)
covers setting up and opening a pull request.

Read these before any task:

- `docs/design.md` — what ProbeOwl does and why
- `docs/implementation-plan.md` — the roadmap: stack, architecture, milestones and releases
- `docs/screens/` — the UI designs, one image per screen

If a task conflicts with these documents, stop and say so in the PR or issue.
Do not guess.

## Changes that need a maintainer's approval

Read anything, and change what your issue needs. These areas change only with a maintainer's
explicit approval on the PR, whoever writes the change:

| Path | Why |
| --- | --- |
| `contracts/` | The API contract and YAML schemas that both sides build against |
| `backend/probeowl/guards/` | The guards are the safety boundary |
| `.github/workflows/` | They decide what is checked |
| `AGENTS.md` | These rules |

## Never

- Never write, run or generate SQL that changes data in a probed database.
  Probes and runnable runbook steps are a single `SELECT` only.
- Never put credentials in files, examples, tests or logs. Use the keychain
  (`keyring`) or environment variables.
- Never bind a server to anything but `127.0.0.1` by default.
- Never log or return unmasked PII. Masking happens in the backend runner.
- Never change `contracts/` without a maintainer's approval. A contract change and its code go in
  the same PR, labelled `contract`: they cannot be split, because the contract test would fail on
  each half.
- Never invent new terms. Use the glossary below; if a concept is missing,
  raise it in the PR description.
- Never write executables or scripts named after real programs (`sqlcmd`, `python`, `bash` and the
  like) onto `PATH` in a test. Behaviour-based antivirus treats that as malware and removes real
  programs. Test what a script would send by rendering it instead.

## Glossary (short form)

Full glossary: `docs/design.md`, section Glossary.

- **Application**: a product area (Wallet, Card access). Chosen in the top bar.
- **Environment**: Production, UAT, Staging, Dev within an application.
- **Connector**: a read-only client for one system. **Binding**: where it points in one environment.
- **Role**: what kind of copy a binding reads from: Read replica, Readable secondary or Active Data Guard standby. Shown under the connector name.
- **Entity**: a business subject type (top-up, visitor). **Subject**: one instance (visitor VP00012931).
- **Identifier**: any value that finds a subject; **root key**: the one lookups start from.
- **Location**: one place a subject's data lives, with its own key and lookup. **Key graph**: how locations get keys (`key_from`).
- **Cardinality**: `one`, `optional` or `many`. **Fan-out**: a lookup repeated per row, capped.
- **Probe kinds**: **Inspect** (current state), **Validate** (a flow executed correctly), **Health** (scheduled, application-wide; phase 2).
- **Expectation**: one rule in a probe. **Run**: one execution. **Investigation**: a run started by a person, with its **investigation record** (reason and reference mandatory in Production).
- **Outcomes**: Found, Missing, Waiting, Blocked, Ambiguous, Error, Stale, Not configured.
- **Evidence**: the exact query, parameters, guards and raw result behind an outcome.
- **Table catalog**, **table label** (Transaction, Master, Audit, Log, Reference, External mapping, Outbox), **column role**.
- **Healthy comparison**: the same probe on a known-good subject, side by side.
- **Expected to vary**: a compare field that normally differs between two healthy records, such as an amount or a timestamp. Shown in the differences, but never highlighted as a likely cause.
- **Runbook** and **step** (read query, reference query, write query, external query, manual step, probe). **Write queries are never run.**
- **Row ceiling**: the most rows any lookup on a connector can return, set in the connector's guards (default 1,000). A location's `max_rows` can't exceed it, and the adapter enforces it at run time too.
- **Guard**, **save checks**, **circuit breaker**, **kill switch** (global, per application or per connector), **masking**, **actor**.
- **Plugin**: a first-party tool or integration in `plugins/<name>/`. **Capability**: what its manifest declares, and all it is given. **Manifest**: its `plugin.yaml`.
- **Store**: ProbeOwl's own database (SQLite, PostgreSQL, SQL Server, Oracle or MariaDB). Never a probed database.
- Do not use "trace" for a probe kind. "Trace" only means a Dynatrace or OpenTelemetry trace.

## Author

The author of ProbeOwl is **@SaadZarook** (Saad Zarook).

- Every new source file starts with a one-line header:
  `# Author: @SaadZarook` (Python, YAML, shell) or `// Author: @SaadZarook` (TypeScript, JavaScript).
- Package metadata names him as author: `authors` in `backend/pyproject.toml`,
  `author` in `web/package.json`.
- `.github/CODEOWNERS` lists him as owner of the whole repository.

## Code quality

Clean, small and readable beats clever and general.

**Keep it simple**

- Build what the current issue needs, nothing more. No speculative options,
  generic repositories or dependency-injection frameworks. The plugin system in
  `docs/design.md` is the one agreed exception; see `docs/decisions.md`.
- Add an interface or base class only when there are two real implementations
  today. The connector adapters (SQL Server, Oracle, MariaDB) qualify; most
  other code does not.
- Prefer plain functions and small classes. A function does one thing and fits
  on a screen.
- Add a dependency only when it saves real work; say why in the PR.

**SOLID, applied with judgement**

- Single responsibility: engine, guards, connectors, store and API do not reach
  into each other's internals.
- Open for extension where it matters: a new database dialect or connector type
  is added by a new adapter, not by `if dialect == ...` spread across files.
- Depend on small interfaces at module boundaries (for example, the engine
  calls a connector interface, not a driver).
- Do not split code into extra layers just to satisfy a principle.

**Comments**

- Comment why, not what. Short comments where the reason is not obvious:
  a guard, a dialect quirk, a workaround.
- Public functions and classes get a one-line docstring or JSDoc.
- No commented-out code. No TODO without an issue number.

**Errors**

- Backend: one exception hierarchy under `ProbeOwlError`
  (for example `ConfigError`, `ConnectorError`, `GuardViolation`,
  `NotFoundError`). Raise the specific one with a message a user can act on.
- Never use a bare `except`, and never swallow an exception. Catch only what
  you can handle; otherwise let it propagate.
- A connector failure becomes the Error outcome for that location, with the
  reason in evidence, not a crashed run.
- The API returns errors as RFC 9457 problem details, never stack traces.
- Web: every screen handles loading, empty and error states. An error boundary
  wraps each page; failed requests show a clear message and a retry.

**Nulls and missing values**

- Python: type hints everywhere; `Optional` only where `None` is a real value.
  mypy strict on `engine/` and `guards/`. Validate input at the edges with
  Pydantic so the inside of the code can trust its data.
- A missing record is the Missing outcome, not `None` leaking through layers.
- TypeScript: `strict` on, no `any`, no non-null assertion (`!`) without a
  comment explaining why it is safe. Handle `undefined` from the API explicitly.

## Conventions

Backend (`backend/`):

- Python 3.12, managed with `uv`. Lint `ruff`, types `mypy` (strict on `engine/` and `guards/`), tests `pytest`.
- Database tests use Testcontainers. SQL parsing and rewriting use `sqlglot`.
- Store access through SQLAlchemy 2 Core and Alembic. Every stored row has `workspace_id`.

Web (`web/`):

- React 18, TypeScript strict, Vite, TanStack Query, React Router, Radix UI primitives.
- Colours and type from `docs/screens/brand.png`: Ink #14171C, Owl amber #F2A33A (logo only), Action blue #1F5BD8, Production red #B42318. IBM Plex Sans and IBM Plex Mono.
- API calls only through the client generated from `contracts/openapi.yaml`. Mocks with MSW.
- Accessible by default: real buttons, labels on inputs, axe clean.

Both:

- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- One issue, one PR. The PR description says what changed, why, how it was tested, and which milestone.

## Pull requests

- Every PR targets `develop`, never another PR's branch. `main` holds releases.
- A PR merges only when every check passes on its latest commit.
- A PR that changes adapter code (`backend/probeowl/connectors/`), the store
  (`backend/probeowl/store/`) or a test marked `database` passes the database tests on each
  affected dialect against a real database, not only fakes. CI runs them on containers;
  `dev/native/` runs them on databases installed on your own machine.

## Definition of done for a PR

- Every check passes: lint, types, tests, contract checks.
- New files carry the author header; the code follows the Code quality rules.
- Errors and missing values are handled, with a test for each failure path.
- New behaviour has tests. Guard changes have guard tests.
- No new terms outside the glossary.
- The milestone's exit check is not broken.
