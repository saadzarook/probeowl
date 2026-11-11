# Contributing to ProbeOwl

Thank you for helping. ProbeOwl reads production data, so its rules on safety come before anything
else: read this page and [AGENTS.md](AGENTS.md), which holds the conventions every contributor
follows, before your first change.

ProbeOwl is pre-release (v0.x). Expect things to change, and nothing to be relied on against real
data yet.

## Ground rules

- **Read only, always.** A probe or a runnable runbook step is one `SELECT`, run exactly as written.
  Never write, run or generate SQL that changes data in a database ProbeOwl probes.
- **No credentials anywhere in the repository**: not in files, examples, tests or logs. Use the
  system keychain (`keyring`) or environment variables.
- **Localhost only.** No server binds to anything but `127.0.0.1` by default.
- **PII stays masked.** Masking happens in the backend runner; nothing returns or logs unmasked
  values.
- **Use the glossary.** The terms in [AGENTS.md](AGENTS.md) and [docs/design.md](docs/design.md)
  are the ones the code, the UI and the docs use. If a concept is missing, raise it in your PR.

## Setting up

You need [uv](https://docs.astral.sh/uv/) and a current Node.js LTS.

```sh
cd backend && uv sync          # Python 3.12 and the dependencies
uv run pytest -q               # unit tests; database tests skip without a database
cd ../web && npm ci
VITE_MOCK_API=true npm run dev # the web app on http://127.0.0.1:5173, against mocks
npx playwright test            # UI tests against mocks
```

`uv run owl serve --config ../examples` (from `backend/`) runs the API on 127.0.0.1:7878 with the two
example applications; `npm run dev` without `VITE_MOCK_API` then talks to it.
The full local stack, with SQL Server, Oracle Free, MariaDB and PostgreSQL seeded with planted bugs
and two fake APIs, is in [dev/](dev/README.md): `docker compose -f dev/docker-compose.yml up --wait`.
The database tests run against a database named in `PROBEOWL_TEST_<DB>_URL` (`MSSQL`, `ORACLE`,
`MARIADB`, `POSTGRESQL`), or, in CI, against containers they start themselves; otherwise they skip
and say why.

## Making a change

1. **One issue, one branch, one pull request.** Open or pick an issue first for anything bigger
   than a fix. Branches are named `<prefix>/<issue number>-<slug>`.
2. **Conventional commits**: `feat:`, `fix:`, `test:`, `docs:`, `chore:`.
3. **Every pull request targets `develop`.** `main` holds releases.
4. **The description says** what changed, why, how it was tested, and which milestone it belongs to.
5. **New source files start with the project's author header**, as the existing files do.

### Code quality

Clean, small and readable beats clever and general. [AGENTS.md](AGENTS.md) has the full rules; in
short: build what the issue needs and nothing more; an interface only when there are two real
implementations; comment why, not what; one exception hierarchy under `ProbeOwlError`, never a bare
`except`; type hints everywhere (mypy strict on `engine/` and `guards/`); TypeScript strict with no
`any`; every screen handles loading, empty and error states, and stays accessible (axe clean).

### Changes that need a maintainer's approval

The paths in the table in [AGENTS.md](AGENTS.md#changes-that-need-a-maintainers-approval) change
only with a maintainer's explicit approval on the pull request, whoever writes the change. A
contract change and its code go in one PR, labelled `contract`. A guard change comes with guard
tests.

### Database changes

A change to a connector adapter, the store or a database test is checked against real databases,
not only fakes: it must pass CI's database jobs, which run the database tests on containers. If
you can run the databases on your own machine ([dev/native/](dev/native/README.md)), say in the
pull request which commit you ran.

## Checks

A pull request merges only when every check passes:

| Check | Tool |
| --- | --- |
| Backend lint and types | ruff, mypy (strict on `engine/` and `guards/`) |
| Web lint and types | ESLint, TypeScript strict |
| Unit tests | pytest |
| Database tests | pytest against SQL Server, Oracle, MariaDB and PostgreSQL |
| Guard tests | Forbidden statements, injection through pasted IDs, lock waits: a failure here is a security bug |
| Contract | The API matches `contracts/openapi.yaml`; the generated web client matches it |
| Examples | `owl check examples/` |
| UI and accessibility | Playwright with axe, against mocks |

CI runs them on every pull request. A pull request merges on GitHub once the required checks pass
on its latest commit, and once a maintainer has approved it if it touches a protected path.

## Working with AI coding agents

Much of ProbeOwl has been written with AI coding agents, under the same rules as everyone else: they
read [AGENTS.md](AGENTS.md), work one issue per pull request, and every pull request is checked and
reviewed before it merges. If you use an agent, point it at `AGENTS.md`, and review what it writes as
you would your own code. You are responsible for every line you submit.

## Reporting a security issue

Please do not open a public issue. See [SECURITY.md](SECURITY.md).

## Licence

ProbeOwl is licensed under the [Apache License 2.0](LICENSE). By contributing, you agree that your
contributions are licensed under it too.
