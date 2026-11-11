# ProbeOwl — design

Oct 30, 2026 · @Saad Zarook

## Problem and goals

The platform validates that a flow executed correctly: every expected write, call and side effect happened, with the right values, and nothing extra happened. Today engineers check this by hand with a SQL client, a provider dashboard, a log search and a queue consumer. The platform turns that into configured validations that run in seconds and show exactly which expectation failed.

Goals:

- Look up any business record (user, transaction, top-up, order) by any of its IDs and verify it across every system it touches.
- Run the same checks on a schedule to catch stuck, orphaned or inconsistent records before users report them.
- Configure checks as code, without writing a new script per incident.
- Stay safe on production: read-only, bounded load, masked PII, full audit trail.

Existing tools each cover a slice: Soda for table-level data checks, Checkly for API checks, Steampipe for SQL over APIs, Appsmith or ToolJet for internal UIs. None follows one record across databases, APIs, queues and logs.

## Name

Decided: **ProbeOwl**, a watcher that probes in the dark. The command line is `owl` (`owl inspect C204518 --env prod`). No widely used developer tool was found under the name.

Identity, kept restrained because this is an operations tool:

- **Mark:** a geometric owl head with two large eyes, usable from 16 px to app icon.
- **Owl amber (#F2A33A)** is for the brand mark only. Status colours carry meaning everywhere else, so amber never means "warning".
- **Type:** IBM Plex Sans for the interface, IBM Plex Mono for IDs, keys and queries.
- **Voice:** calm and exact. Say what was found, where, with the number. The owl appears only in the logo and empty states.

Names considered:

| Name | Theme | Why it fits | Watch out for |
| --- | --- | --- | --- |
| ProbeOwl | Owl | Says what it does and who does it: a watcher that probes in the dark. Memorable; the owl gives an easy logo | Long for a command; use `owl` as the CLI |
| Patchbay | Connection | A studio patch panel connects any input to any output, like connectors and the key graph | Two small open-source projects use the name |
| Junction | Connection | Where one record's paths across systems meet | Several companies use it |
| Switchboard | Connection | An operator connecting lines to find the right party | Used by several products |
| Plumb | Depth | Probe to the bottom; a plumb line checks something is true | Close to Plumbr (Splunk) |

## Glossary

One word per idea, used the same way in the UI, the YAML and conversation. The flow probe is called **validate** (previously trace), because "trace" already means a Dynatrace or OpenTelemetry trace. "Trace" and "tracing" in this doc now only mean those.

**ProbeOwl** is the platform; **owl** is its command line. The three probe kinds are **Inspect**, **Validate** and **Health**.

**Structure**

| Term | Meaning |
| --- | --- |
| Application | A product area that owns its probes, entities, runbooks and catalog, such as Wallet or Card access. Chosen once in the top bar. |
| Environment | Production, UAT, Staging or Dev within an application. Production has the strictest rules. |
| Connector | A read-only client for one system: a database, an API, Dynatrace. |
| Binding | Where a connector points in one environment: host, credentials, replica. |
| Role | What kind of copy a binding reads from: Read replica, Readable secondary or Active Data Guard standby. Shown under the connector name. |
| Owner | The team responsible for a probe, runbook, table or connector. |

**Data model**

| Term | Meaning |
| --- | --- |
| Table catalog | The list of tables an application's probes read, each with a label and marked columns. |
| Table label | What kind of table it is: Transaction, Master, Audit, Log, Reference, External mapping, Outbox. Sets defaults. |
| Column role | A marked column: primary key, time, status, link, external key, PII, compare. |
| Entity | A business subject: a top-up, a visitor, a card. Defined once, used by many probes. |
| Subject | One specific entity instance, such as visitor VP00012931. |
| Identifier | Any value that finds a subject: visitor\_profile\_id, card\_id, email. Each says whether it is unique. |
| Root key | The identifier every other lookup starts from. |
| Location | One place a subject's data lives, with its own key and lookup: a table, an API call, a log search. |
| Key graph | How locations get their keys from each other (`key_from`). |
| Cardinality | How many records a location should return: one, optional, or many. |
| Fan-out | A lookup repeated for each row of a many location, capped by `max_fanout`. |

**Probes**

| Term | Meaning |
| --- | --- |
| Probe | A saved, reviewed check: locations to read and expectations to test. |
| Inspect probe | Checks a subject's current state. |
| Validate probe | Checks a flow executed correctly everywhere (previously "trace"). |
| Health probe | Runs on a schedule across the application to find stuck or inconsistent records. |
| Expectation | One rule in a probe, such as `count(ledger) == 2`. Passes, fails or is skipped. |
| Condition | A `when` that applies expectations only in some states, such as a failed top-up. |
| Window | How long to wait for something that arrives later (`within`). |
| Anchor time | The record's own timestamp that windows start from. |
| Template | A reusable probe or expectation pattern offered by a table label. |

**Runs and results**

| Term | Meaning |
| --- | --- |
| Run | One execution of a probe, with every lookup, result and timing stored. |
| Run status | Where a run is: Running, Passed, Failed, Skipped or Ambiguous. |
| Investigation | A run started by a person, with its investigation record. |
| Investigation record | Reason and reference (mandatory in Production), notes, customer reference and conclusion (optional), plus who, when and where. |
| Outcome | What one location returned: Found, Missing, Waiting, Blocked, Ambiguous, Error, Stale or Not configured. |
| Found / Missing | Record returned / lookup worked and returned nothing. |
| Waiting | Not found yet, still inside its window. |
| Blocked | Its key could not be resolved because an earlier location failed. |
| Ambiguous | More records than its cardinality allows, or an identifier matching several subjects. |
| Stale | Replica lag is longer than the window, so "missing" cannot be trusted. |
| Not configured | The connector has no binding in this environment. |
| Evidence | The exact query, parameters, guards applied and raw result behind an outcome. |
| Offender | A record a health probe flagged. |
| Limited follow-up | The cap on how many offenders a scheduled run follows up: 5 per run, 100 per day. |
| Healthy comparison | The same probe run on a known-good subject and shown side by side. |
| Expected to vary | A compare field that normally differs between two healthy records, such as an amount or a timestamp. Shown in the differences, but never highlighted as a likely cause. |

**Knowledge and hand-off**

| Term | Meaning |
| --- | --- |
| Runbook | An ordered list of steps for a symptom, stored in the knowledge base. |
| Step | One item in a runbook: read query, reference query, write query, external query, manual step or probe. |
| Write query | A query that changes data. Stored for reference; never run by the platform. |
| Jira story draft | The prepared story for a failed run, copied or downloaded and created by hand. |

**Safety**

| Term | Meaning |
| --- | --- |
| Guard | A limit applied to every lookup: read-only, lock timeout, statement timeout, row cap, rate budget. |
| Row ceiling | The most rows any lookup on a connector can return, set in the connector's guards (default 1,000). A location's `max_rows` can't exceed it, and the adapter enforces it at run time too. |
| Save checks | Checks run when a probe or runbook is saved: read-only SQL, index use, guards, labels. |
| Circuit breaker | Pauses a connector when its replica lags or slows; affected runs report Skipped. |
| Kill switch | Stops every read immediately: global, per application or per connector. |
| Masking | Hiding PII values in results; applied in the runner, never only in the UI. |
| Actor | Who started a run: a person, the scheduler, or (later) an agent acting for a person. |

## Running example: wallet top-up

A user tops up USD 500. The app writes a `topups` row, the payment provider captures the card, a webhook confirms it, the ledger posts a double entry, an event goes to Kafka, and a push notification goes out. Each system knows the top-up by a different key.

&#91;embedded content: wallet top-up · 1 record, 4 keys, 6 locations\]

Only the root is looked up by `topup_id` directly in the DB; every other location gets its key from a field on the record, which is exactly where real failures hide.

## Core model

Five concepts carry the whole design. A probe never talks to a system directly; it asks an entity for a location, and the location's connector does the fetch.

| Concept | What it is | Example |
| --- | --- | --- |
| Connector | A typed, read-only client for one system, implementing `get`, `find`, `count` | `ledger_db` (Oracle replica), `checkout_api` (REST) |
| Entity | A business object with a root key and its known locations | `topup`, root key `topup_id` |
| Location | Where one facet of an entity lives: its own key, its own lookup, the fields it exposes | `payment` via `key_from: topup.provider_payment_id` |
| Probe | A set of assertions over locations; `inspect for a subject's current state, validate for one flow, health for the whole application` | `topup_completed`, `stuck_topups` |
| Run | One execution, storing every lookup, result and timing | Run 8812 on `topup_id=tp_91f2` |

Every location resolves to one of the outcomes defined in the glossary, so the UI and alerts treat a SQL row, an API response, a Kafka message and a log line the same way:

| Outcome | Meaning |
| --- | --- |
| Found | Record fetched, assertions evaluated |
| Missing | Lookup succeeded and returned nothing |
| Waiting | Not found yet, still inside its `within` window |
| Blocked | Key could not be resolved because an upstream location failed |
| Error | The connector itself failed (timeout, auth, rate limit) |

Locations form a dependency graph. The engine resolves the root first, then runs every location whose key is now known in parallel. A run can start from any node that exposes a reverse link, for example a provider payment ID pasted by support.

Database connectors start with MariaDB, Oracle and SQL Server: one SQL connector with a dialect adapter per engine, always pointed at the existing read replicas. Probes write each lookup in its database's own dialect.

The platform is shared across every application, not built for one. Each application is a namespace that owns its connectors, entities and probes, with an owning team. Connector types (SQL, REST, Dynatrace, Jira) and probe templates are shared across all applications. An entity can span applications: a `user` created in onboarding and used by the wallet resolves through both namespaces.

Logs, traces and errors come from Dynatrace through one connector that runs DQL against Grail (`fetch logs`, `fetch spans`, filtered by service, key and time window). The top-up example's `push` location becomes a Dynatrace log lookup; where a `trace_id` exists, the connector pulls the request's spans too.

## Beyond transactions: inspect, identifiers and many results

Not every check is about a flow. Before debugging, engineers usually look at the current state of a subject: which cards a visitor holds, which roles a user has, which devices are registered. So probes come in three kinds, on the same engine:

| Kind | Question it answers | Card access example |
| --- | --- | --- |
| Inspect | What is the current state of this subject, and is it consistent? | Cards currently assigned to visitor VP00012931, and whether the door controller agrees |
| Validate | Did this flow execute correctly everywhere? | Card issued: row created, controller provisioned, audit written |
| Health | Is anything across the application stuck or inconsistent right now? | Cards active in the database but expired, across all visitors |

**Any identifier.** An entity declares every identifier it can be found by, not one ID. Each says how it resolves to the entity's root key and whether it is unique. The search box detects the type from its pattern, or the user picks it.

```yaml
entity: visitor
root_key: visitor_profile_id
identifiers:
  visitor_profile_id: { pattern: '^VP\d{8}$', unique: true }
  card_id:            { pattern: '^C\d{6}$', via: cards.card_id, unique: true }
  employee_no:        { via: visitor_profile.employee_no, unique: false }
  email:              { via: visitor_profile.email, unique: false, pii: true }

locations:
  profile:
    using: access_db
    lookup: SELECT * FROM visitor_profile WHERE id = :visitor_profile_id
    cardinality: one
  cards:
    using: access_db
    lookup: >
      SELECT card_id, status, valid_from, valid_to, access_group
      FROM cards WHERE visitor_profile_id = :visitor_profile_id
    cardinality: many
    max_rows: 50
    each:                         # per card, bounded
      controller:
        using: door_controller_api
        lookup: GET /cards/{{card_id}}
        max_fanout: 10

---
probe: visitor_cards
kind: inspect
entity: visitor
expect:
  - count(cards where status = 'active') <= 1
  - all(cards where status = 'active', valid_to > now())
  - all(cards, controller.status == status)   # database and controller agree
```

**Many results, handled on purpose.**

- **Cardinality is declared per location:** `one`, `optional` (zero or one) or `many`. A `one` location that returns two rows is a finding, not a pick-the-first.
- **A new outcome, Ambiguous,** joins Found, Missing, Waiting, Blocked and Error: more rows than the declaration allows.
- **Identifier matches more than one subject** (an email shared by two visitor profiles): the run stops and lists the matches; the user picks one, or runs all of them up to 5.
- **`many` locations show as a table**, one row per record, each with its own outcome. Expectations work over sets: `count`, `all`, `any`, `none`, `unique`.
- **Fan-out is bounded:** `max_rows` per location, `max_fanout` for per-row lookups; results say "showing 50 of 312" rather than cutting silently.
- **Health probes over Master tables** are allowed when they filter on an indexed column, since state checks ("active but expired") rarely have a time window.

Compare with a healthy record works for inspect too: pick another subject in a known-good state (a visitor whose card works) and diff the two.

## Worked example config

The entity declares where a top-up lives and how each location is found; probes then assert over those locations. Note the branch on status: a failed top-up must have no ledger entry, so absence is asserted, not just presence.

```yaml
entity: topup
root_key: topup_id
reverse_keys: [provider_payment_id, ledger_txn_id]

locations:
  record:
    using: wallet_db                # SQL Server read replica
    lookup: SELECT * FROM topups WHERE id = :topup_id
    exposes: [user_id, amount, currency, status,
              provider_payment_id, ledger_txn_id, created_at]

  payment:
    using: checkout_api
    key_from: record.provider_payment_id
    lookup: GET /payments/{{provider_payment_id}}
    fallback: GET /payments?reference={{topup_id}}
    exposes: [status, amount, currency]

  webhook:
    using: wallet_db
    lookup: >
      SELECT * FROM provider_webhooks
      WHERE payment_id = :provider_payment_id
    cardinality: many

  ledger:
    using: ledger_db                # Oracle read replica
    key_from: record.ledger_txn_id
    lookup: >
      SELECT account, direction, amount FROM ledger_entries
      WHERE txn_id = :ledger_txn_id
    cardinality: many

  balance:
    using: ledger_api
    key_from: record.user_id
    lookup: GET /wallets/{{user_id}}/balance

  credited_event:
    using: event_index              # see gap 7
    lookup: { topic: wallet.credited, key: '{{topup_id}}' }
    within: 2m

  push:
    using: logs
    key_from: record.user_id
    lookup: 'service="notifier" user_id="{{user_id}}" template="topup_success"'
    within: 5m
    anchor_time: record.created_at

---
probe: topup_completed
kind: validate
entity: topup
steps:
  - expect: record.status in ['succeeded', 'failed', 'pending']
  - expect: payment.amount == record.amount and payment.currency == record.currency
  - expect: count(webhook) >= 1
  - when: record.status == 'succeeded'
    expect:
      - count(ledger) == 2                       # exactly one double entry
      - sum(ledger.amount where direction='credit')
        == sum(ledger.amount where direction='debit')
      - exists(credited_event)
      - exists(push)
  - when: record.status == 'failed'
    expect:
      - not exists(ledger)                       # no money moved
      - payment.status != 'captured'             # provider agrees

---
probe: stuck_topups
kind: health
schedule: every 5m
using: wallet_db
query: >
  SELECT id AS topup_id FROM topups
  WHERE status = 'pending'
    AND created_at BETWEEN DATEADD(hour, -1, SYSUTCDATETIME())
                       AND DATEADD(minute, -10, SYSUTCDATETIME())
expect: row_count == 0
on_failure:
  run_validate: topup_completed     # capped sample, see Scheduled runs
  notify: [slack:#payments-oncall]
  jira_draft: true           # optional; phase 2: a story draft to copy or download
```

What a failing run looks like for support: record found with status `succeeded`, payment captured, webhook found, ledger **missing**, credited event and push **waiting** then **missing**. One glance says the ledger write failed after a successful capture, which is a money-at-risk incident, not a notification bug. If `ledger_txn_id` were null on the record, the ledger would show **blocked** instead, pointing at the write that should have saved the link.

**Missing data never passes a check.** A location that is Missing (or Waiting) has no record, so only counting reads it: `count()`, `exists()` and `none()` see no rows. Any other read of it is skipped, with "needs ledger, which is Missing": `sum`, `min`, `max`, `all`, `any`, `unique`, or a field such as `payment.amount`, in an expectation or a `when:`. A skip fails the run, as Blocked does, because a run cannot pass on data it could not see. Without this rule, `sum(credit) == sum(debit)` would pass as 0 == 0 on a ledger that isn't there, and `payment.status != 'captured'` would pass on a payment that was never found. For a location that may be absent, start the step with `when: exists(refund)`. A fan-out lookup that is Missing skips the check too ("needs controller, which is Missing for 1 card"). A filter that leaves no rows of a Found location is different: `all(cards where status == 'active', valid_to > now())` still holds for a visitor whose cards are all revoked. The rule is about the location, not the filter.

## Where this fits well

The model fits any flow where a business record is written to a few systems that can each be read by a key, a search, or a time-bounded scan. That covers most product backends.

| Use case | Entity | Typical locations | Probe kind |
| --- | --- | --- | --- |
| Signup and profile creation | user | users table, auth provider, CRM, welcome email log | validate |
| Payments, top-ups, refunds | transaction | app DB, payment provider, ledger, webhooks, events | validate + health |
| Orders and fulfilment | order | orders, items, inventory API, shipping API | validate |
| KYC and onboarding | application | app DB, KYC vendor API, document store | validate |
| A single failed request | request | logs and traces by `trace_id`, error tracker | validate |
| Stuck or orphaned records | any | one DB query over a time window | health |
| Error spikes per service | service | log or metrics query | health |
| Post-deploy smoke | any | sample last N records, run validate probes | health |

The common thread is a known happy path. If you can write down "after X happens, Y and Z should exist with these values," it can be a probe.

## Gaps and how to address them

The biggest risks are false failures from timing, sources that cannot be looked up by key, and the probe itself loading or leaking production data. Each gap below is tied to the top-up example.

| # | Gap | Top-up example | How to address |
| --- | --- | --- | --- |
| 1 | Keys differ per system and links can be missing | `provider_payment_id` null because the save failed after the API call | `key_from` per location, `fallback` search by our reference, and report the missing link itself as a finding |
| 2 | One-to-many and retries | Two payment attempts for one top-up; webhook delivered three times | Declare `cardinality: one / many`; assert counts explicitly (`count(ledger) == 2`) to catch double credits |
| 3 | Eventual consistency | Webhook arrives 40 s after capture; probe runs at 10 s | `within` windows, a `waiting` outcome, automatic re-check, and `anchor_time` so windows start from the record's time, not the run's |
| 4 | Legitimate alternative paths | A failed top-up should have no ledger entry | Conditional steps (`when:`) on state, and absence assertions (`not exists`) |
| 5 | Derived values | Wallet balance is a sum of ledger entries, not a stored copy | Computed expectations (`sum`, `count`, cross-location arithmetic) instead of field equality only |
| 6 | Record changed since the event | Top-up later refunded; status no longer `succeeded` | Check history or audit tables when present; store a snapshot of every lookup in the run so evidence survives |
| 7 | Sources with no key lookup | Kafka has no "get by key"; scanning a topic is slow and limited by retention | A small event index: a consumer that records `topic, key, offset, time` for watched topics; or query the consumer's processed-events table instead |
| 8 | Logs: retention, sampling, cost | Push log sampled at 10% or rotated after 7 days | Mark log locations `confidence: low`; never fail a probe on a log alone; bound every search by time and service |
| 9 | Third-party APIs: rate limits, no search | Provider allows 100 req/min; no lookup by metadata | Per-connector rate budgets, response caching, and storing webhook payloads locally so most checks hit our DB |
| 10 | Sharding, regions, tenants | Users in two regions live in different databases | A routing rule on the connector (`shard_by: user.region`) resolved before the lookup |
| 11 | Encrypted or hashed fields | Card fingerprint or phone stored encrypted | Read through the owning service's API instead of the DB, or compare hashes, never decrypt in the platform |
| 12 | Schema drift breaks configs | `ledger_entries.direction` renamed to `side` | Validate probe configs in CI against live schemas and API specs; version configs with the service that owns them |
| 13 | Heavy queries on production | Health probe scans `topups` without an index | Read replicas only, statement timeout (2 s), row limits, `EXPLAIN` check at config time, and per-connector concurrency caps |
| 14 | Noisy alerts | Provider outage fails 300 validations at once | Group failures by failing location, dedupe, thresholds on health probes, mute windows |
| 15 | Config sprawl and ownership | 40 probes, nobody knows who owns `stuck_topups` | Configs in git next to the service, a required `owner` field, reusable templates, and a report of probes not run in 30 days |
| 16 | Unknown unknowns | A bug writes the ledger to the wrong wallet; every check passes | Assert on values, not just existence (`ledger.account == wallet_of(record.user_id)`), and keep a free-form query console for what no probe covers |

Two more gaps come from your environment:

| # | Gap | Top-up example | How to address |
| --- | --- | --- | --- |
| 17 | Three SQL dialects with different limits and timeouts | Oracle has no `LIMIT`; timeouts are `max_statement_time` in MariaDB, a resource profile in Oracle, and a client-side timeout in SQL Server | A dialect adapter per engine that applies row caps and timeouts the engine's own way; lint each probe's SQL against its target dialect in CI |
| 18 | `trace_id` exists in some services only | The notifier logs `trace_id`; the ledger does not | Correlate by business keys plus a time window anchored on the record (`anchor_time`); use `trace_id` where present; add propagation service by service, starting with the flows that fail most |

## Safety, security and access

The platform holds read access to every production system, so it must be the most locked-down tool in the stack.

- **Read-only by construction.** DB connectors use read-only roles on replicas; API connectors allow only GET and declared search endpoints. No write path exists in the engine.
- **Parameterised lookups only.** Keys are bound as parameters, never interpolated into SQL or log queries, so a pasted ID cannot become an injection.
- **Secrets in a vault.** Configs reference `vault://` paths; the runner fetches credentials at run time and never returns them.
- **Field masking.** Each location declares sensitive fields (email, phone, card data); results show masked values unless the viewer's role allows more.
- **Access by entity and connector.** Support can run `topup_completed`; only payments engineers see raw ledger rows; nobody runs free-form queries on `ledger_db` without approval.
- **Audit everything.** Every run stores who ran it, which IDs, which connectors, and what was returned (masked).
- **Load guardrails.** Statement timeouts, row caps, rate budgets per connector, and a global kill switch.

## Table catalog

Each application keeps a catalog of the tables its probes read, each with a label, an owner and a few marked columns. Labels are not only documentation: each one sets defaults for guards, masking, probe templates and the healthy-record comparison, so a probe author starts from safe settings instead of remembering them.

| Label | Example | What the label sets by default |
| --- | --- | --- |
| Transaction | `topups`, `ledger_entries` | Large and growing: a time column is required, health probes must filter on it, row cap 100; offers "stuck in status" and "count per window" templates |
| Master | `users`, `wallets` | Keyed lookups only; PII columns masked by default; used as the root of entities |
| Audit or history | `topup_status_history` | Append-only; used to show how a record changed since the event (gap 6); time-bounded |
| Log | `provider_webhooks`, `notification_log` | High volume, short retention; time bound required; lookups marked low confidence if retention is shorter than the probe's window |
| Reference or config | `currencies`, `fee_rules` | Small and slow-changing; may be cached; a change to it is worth flagging on runs |
| External mapping | `user_provider_ids` | Holds another system's key; each such column becomes an edge in the key graph, so `key_from` can be suggested |
| Outbox or queue | `outbox_events` | Rows that should be published; offers "unpublished for over N minutes"; can stand in for the event index (gap 7) |

For each table the catalog records: connector, schema and table, label, owner, description, primary key, time column (and whether it is indexed), status column with its values, PII columns, links (a column that points to another table or to another system's key), retention, and approximate row count.

How it fills:

- **Import**: read table and column definitions from the replica (`INFORMATION_SCHEMA` in SQL Server and MariaDB, `ALL_TAB_COLUMNS` and `ALL_INDEXES` in Oracle), never the data.
- **Suggest**: guess a label from names and shape (`_audit` or `_history` suffix, `created_at` plus `status` columns, foreign keys, very small row counts); a person confirms every label.
- **Check**: per environment, flag drift between the catalog and the live schema, and tables used by probes but missing from the catalog.

Probes refer to catalog tables, and the save checks use the labels: a health probe on a Transaction table without a time filter is rejected, and a query that selects a PII column shows it masked. The comparison with a healthy record takes its default fields from the marked status, key and link columns.

## Environments

Each application has environments (Production, UAT, Staging, Dev; names set per application). Entities and probes are written once; only connectors are bound per environment, so the same `topup_completed` runs against production replicas or a UAT database by switching the environment.

```yaml
connector: wallet_db
type: sqlserver
environments:
  prod:    { host: wallet-sql-ro,  secret: vault://prod/wallet-ro, replica: required }
  uat:     { host: wallet-sql-uat, secret: vault://uat/wallet-ro }
  staging: { host: wallet-sql-stg, secret: vault://stg/wallet-ro }
```

Production keeps the strictest rules; other environments start from the same defaults and may relax a few.

| Setting | Production | Non-production |
| --- | --- | --- |
| Database target | Read replica only | Replica or primary |
| Query guards | Full set; cannot be relaxed | Same defaults; owners may raise timeouts and row caps |
| Forbidden SQL and read-only | Enforced | Enforced |
| Audit fields | Reason and Reference mandatory | Reason mandatory, Reference optional |
| Masking | Always on | Configurable for test data |
| Scheduled probes | Capped follow-up | Capped, or off |
| Jira draft | Labelled `env-prod` | Labelled with its environment |

Guardrails for switching:

- The environment is part of every run, run link, audit record and Jira draft.
- Production is shown on a red banner on every screen; switching environment clears the current result.
- A location whose connector has no binding in the chosen environment shows `Not configured`, not `Missing`.
- Secrets and access are separate per environment: a UAT credential can never reach production.

## Investigation audit

Every manual investigation records why it was run before any lookup executes. Validate stays disabled until the mandatory fields are filled; the record is append-only and kept with the run.

| Field | Required | Filled by | Notes |
| --- | --- | --- | --- |
| Reason | Mandatory | User | Incident, Customer complaint, Deploy verification, Audit or compliance, Proactive check |
| Reference | Mandatory | User | Jira key, support case ID or change ID; phase 1 records it as entered, with a format check only (e.g. WALLET-482); checking it against Jira comes later |
| Investigator | Mandatory | System | From SSO login |
| Time, IDs looked up, probe and version, connectors touched | Mandatory | System | Captured per run |
| Notes | Optional | User | Free text, why this record |
| Customer reference | Optional | User | Stored masked |
| Related runs | Optional | User | Links earlier runs on the same issue |
| Conclusion | Optional | User | Added after the run: Root cause found, No issue, Escalated |

Once the Jira lookup is available, a run whose reference is a Jira key can also post its summary as a comment on that issue (optional per application). Free-form console queries need the same fields. Scheduled runs record a system actor with reason `Scheduled` and the probe name.

## Scheduled runs: limited follow-up

Scheduled probes detect; they do not investigate. A health probe runs one aggregate query over an indexed time window and may validate only a capped sample of what it finds.

```yaml
probe: stuck_topups
kind: health
schedule: every 5m
validate_on_failure:
  probe: topup_completed
  max_records: 5            # per run, hard ceiling 10
  sample: newest
  max_per_day: 100          # then detect-only until midnight
  locations: [record, payment, ledger]   # DB and API only
```

- At most 5 validations per run (ceiling 10) and 100 per probe per day; past that the probe reports counts only.
- Scheduled validations skip log and Dynatrace locations by default; those stay for manual investigations.
- Scheduled runs use the lowest connector priority and stop first when a circuit opens.
- The full list of offending IDs is stored, so an engineer can investigate the rest manually, with audit fields.

## Query guards

A probe must never block a writer, never wait on a lock, and never report "missing" because its own read was stale or cut short. Guards apply in four layers: the login, the session, the statement, and the runner.

**No locks, per engine.** Each engine gets non-blocking reads differently, and dirty reads are avoided by default because a half-written transaction would show up as a false finding.

| Guard | SQL Server | Oracle | MariaDB |
| --- | --- | --- | --- |
| Non-blocking reads | Readable secondary maps reads to snapshot isolation automatically; on a primary use `SNAPSHOT` or RCSI | Readers never block writers (multi-version reads from undo) | InnoDB consistent reads; session set to `READ COMMITTED` so no long snapshot holds back purge |
| Never wait for a lock | `SET LOCK_TIMEOUT 0`, `SET DEADLOCK_PRIORITY LOW` | Reject `FOR UPDATE`; nothing else waits | `lock_wait_timeout` and `innodb_lock_wait_timeout` set to 1 s, so a metadata lock fails fast instead of blocking DDL |
| Statement timeout | Client command timeout plus Resource Governor `REQUEST_MAX_CPU_TIME_SEC` for the probe login | Resource Manager consumer group cancelling SQL past N seconds; profile `CPU_PER_CALL`, `LOGICAL_READS_PER_CALL` | `SET SESSION max_statement_time = 2` |
| Resource cap | Resource Governor workload group: CPU %, memory grant %, `MAXDOP 1` | Consumer group CPU share and parallel degree limit | `max_user_connections` on the probe user; query cache off for the session |
| Row cap | `TOP (n)` injected by the adapter | `FETCH FIRST n ROWS ONLY` | `LIMIT n` |
| Replica lag check | Redo queue and `last_commit_time` on the secondary | Apply lag from `V$DATAGUARD_STATS` | `Seconds_Behind_Master` |
| Forbidden in probe SQL | `NOLOCK` by default, `HOLDLOCK`, `TABLOCKX`, `UPDLOCK`, temp tables | `FOR UPDATE`, `LOCK TABLE`, PL/SQL blocks | `FOR UPDATE`, `LOCK IN SHARE MODE`, `GET_LOCK()` |

`NOLOCK` stays available as an explicit opt-in for large health counts where approximate numbers are acceptable; inspect and validate probes never use it.

**Statement guards, all engines.**

- Parse every probe's SQL at save time; allow only a single `SELECT` or `WITH … SELECT`.
- Run `EXPLAIN` at save time against the replica; inspect and validate probes must use an index lookup, health probes must filter on an indexed time column.
- Bind all keys as parameters; no string-built SQL.
- Cap result size (rows and bytes) in the runner as a second line behind the SQL row cap.

**Runner guards.**

- Per-connector connection pool of 2 to 4, and a global concurrency cap per database host.
- Circuit breaker: if replica latency or lag crosses a threshold, pause probes on that connector and mark runs `skipped`, not failed.
- Stale reads become their own outcome: if replica lag exceeds a step's `within` window, report `stale` instead of `missing`.
- Health probes scan incremental windows (the last 5 to 60 minutes), never the full table.
- A kill switch per connector and per application.
- A row ceiling per connector, set in its guards (default 1,000): the most rows any lookup on it can return. A location's `max_rows` can't exceed it, and the adapter enforces it at run time too.

**Dynatrace guards.** Every DQL lookup is time-bounded from the record's own timestamp, filtered on indexed attributes first, and capped with `limit` and a scanned-bytes ceiling, because Grail charges and slows by data scanned.

## Where it does not fit

The platform verifies records against a known happy path. It is the wrong tool when the question is not about a specific record's state.

| Situation | Why it doesn't fit | Use instead |
| --- | --- | --- |
| Validating every event in real time at high volume | Per-record lookups across systems don't scale to thousands per second | Stream reconciliation or CDC-based checks; the platform samples |
| Why is it slow? | Latency lives in spans, not in stored records | APM and distributed tracing |
| Which line of code is wrong? | Probes show where the data diverged, not why | Error tracker, logs, debugger; link to them from failing runs |
| Warehouse and analytics data quality | Batch tables, column statistics, freshness | Soda or dbt tests |
| Systems with no read access | Black-box vendors with no API or exports | Store their callbacks locally, then probe those |
| Fixing the data | Write access would make the tool a risk | Separate, approved runbooks; a failing run can link to one |

## AI agent access (MCP) — parked

**Parked for now.** The MCP server is not in phase 1, but the design keeps room for it, so adding it later is a thin layer rather than a rework:

- The UI is a client of an internal runner API; nothing it does is UI-only, so an MCP server can call the same API.
- Runs return typed outcome codes and structured results, not formatted text.
- The audit record's actor field already allows a person, the scheduler, or an agent acting for a person.
- Masking happens in the runner, not the UI, so every client gets masked values.
- Rate budgets are per actor, so an agent can get its own cap.

The design below is the target when it is picked up: a read-only layer over that API. An agent could validate records, read runs and health, and prepare Jira drafts; it could not write SQL, change probes or touch connectors.

Why this shape: an agent connected straight to production data combines private data, tool calling and instructions it reads from that data. Supabase's write-up of this risk concludes that read-only mode and wrapping results reduce it but "guardrails alone aren't enough" ([Supabase](https://supabase.com/blog/defense-in-depth-mcp)). Fixed, reviewed probes instead of free-form queries remove most of that risk.

**Tools**, all marked `readOnlyHint` and returning typed results (`outputSchema` with `structuredContent`) so agents get the same outcome codes as the UI:

| Tool | What it does | Notes |
| --- | --- | --- |
| `list_catalog` | Applications, environments, entities and probes this user may run | Discovery |
| `describe_entity` | Locations, key graph and exposed fields of an entity | Lets the agent pick the right ID type |
| `validate_record` | Runs an inspect or validate probe on any ID; returns the run | Requires `environment`, `reason`, and `reference` in production |
| `get_run` | A run's locations, expectations and evidence | Masked values only |
| `list_health` | Health probes, status and offending IDs | Read only; cannot trigger scheduled runs |
| `compare_with_healthy` | Diff of a failed run against a passing record | See the phase 1 addition below |
| `draft_jira_story` | Returns the story draft text | Never creates the issue |

Not exposed: free-form SQL or DQL, probe or connector edits, guard settings, the kill switch.

**Resources and prompts.** Entity definitions, probe YAML and run reports are readable as resources (for example `run://wallet/prod/8812`). Two prompts give agents a known starting point: `investigate_record` and `triage_health_failure`.

**Agent-specific limits.**

- **Identity:** OAuth 2.1 per user; the agent acts as that user, and the audit shows `s.khan via Claude Desktop`. No shared service token, and the user's token is never passed on to other systems.
- **Environments:** agent access to Production is off by default per application; the owning team turns it on.
- **Budget:** a per-user cap for MCP calls (for example 20 validations per hour) so an agent loop cannot flood replicas.
- **Output:** masking is always on for MCP, in every environment. Record values are returned in fields labelled as data, never as instructions.
- **Audit:** reason and reference come from the user; if the agent omits them, the call fails with a message telling it to ask.

**Transport.** Locally, stdio, so Claude Desktop or Claude Code on a laptop can use it with the Docker Compose stack. Shared, Streamable HTTP inside the network behind SSO, following the current stateless revision of the spec (2026-07-28, per [WZ-IT](https://wz-it.com/en/knowledge/ki/mcp-model-context-protocol/)); confirm against the official spec when building.

**Alongside Dynatrace's own MCP server.** Dynatrace hosts an MCP server for generating and running DQL, investigating problems and resolving entities, authenticated with a platform token ([Dynatrace docs](https://docs.dynatrace.com/docs/dynatrace-intelligence/dynatrace-mcp)). An agent can use both: ours to validate a specific record across systems, theirs for broad log and trace questions.

## Phase 1 addition: compare with a healthy record

Add "Compare with a healthy record" to phase 1: after a failed run, the platform runs the same probe on a recent passing record and shows the two side by side, with differences highlighted. It needs no new connectors and answers the first question in most investigations: what does normal look like, and what is different here?

The pain it targets is investigation by memory. Lightrun's 2026 survey reports that 54% of high-severity incident resolutions still rely on tribal knowledge rather than diagnostic evidence, and that developers spend about 38% of their week debugging and verifying ([IT Brief, Lightrun report](https://itbrief.co.uk/story/ai-code-needs-production-debugging-lightrun-report-finds)).

In the top-up example:

| Location or field | Failing tp\_91f2c7 | Healthy tp\_91f2b9 |
| --- | --- | --- |
| Ledger entries | **0 rows** | 2 rows |
| Webhooks | **2 deliveries** | 1 delivery |
| Payment method | **Apple Pay** | Card |
| Client app version | **5.3.0** | 5.2.4 |
| Credited event | **Missing** | Found after 3 s |

Read together, the differences point at a duplicate webhook from a new payment path, which a single-record view does not show.

How it works:

- Each entity declares a `healthy_sample` query that picks the newest passing record before the failing one, in the same environment and application, on an indexed column.
- The user can narrow the match (same payment method, same region) or paste a known-good ID instead.
- Fields to compare come from the table catalog's marked columns for SQL and a `compare:` list on REST locations; masked fields compare as equal or different without showing values.
- The comparison is a second run with the same guards and audit fields, linked to the first.

## Phase 1 addition: knowledge base

A knowledge base of runbooks keeps every follow-up query and step in one place, including queries the platform will never run: fix scripts, DBA-only views, Dynatrace queries, and steps in other tools. Each runbook is an ordered list of steps, and each step is labelled with whether the platform runs it.

| Step kind | Does the platform run it? | Notes |
| --- | --- | --- |
| Read query, reviewed and passing guards | Yes, one click | Same guards and audit as a probe; parameters filled from the run |
| Read query, reference only | No, copy only | Not reviewed, or fails a guard (for example reads a system view); shows why |
| Write or fix query | Never | Red label; copy adds a header naming the environment and run, and the copy is recorded in the audit |
| External query | No, copy and open the tool | Dynatrace DQL, a controller console, a vendor dashboard |
| Manual step | Not applicable | A checklist item; ticks are saved with the run |
| Probe | Yes | Inspect or validate again, for example after a fix |

A runbook for the top-up example:

```yaml
runbook: topup_not_credited
title: Top-up captured but wallet not credited
application: wallet
applies_to: { entity: topup, when: [ledger.missing, credited_event.missing] }
owner: payments-team
last_verified: 2026-10-12
steps:
  - kind: read_query
    title: Is the ledger posting stuck in the outbox?
    using: wallet_db
    sql: |
      SELECT TOP (20) id, status, attempts, last_error, created_at
      FROM dbo.outbox_events WHERE aggregate_id = :topup_id
      ORDER BY created_at DESC
  - kind: external_query
    title: Ledger service errors for this top-up
    tool: dynatrace
    query: |
      fetch logs
      | filter loglevel == "ERROR" and contains(content, :topup_id)
      | limit 100
  - kind: manual
    title: Confirm the capture in the provider dashboard
    text: Search the payment ID; check for a refund, dispute or partial capture.
  - kind: write_query
    title: Re-queue the ledger posting
    using: wallet_db
    requires: change approval; run by a DBA
    sql: |
      UPDATE dbo.outbox_events SET status = 'pending', attempts = 0
      WHERE aggregate_id = :topup_id AND status = 'failed'
  - kind: probe
    title: Re-validate after the fix
    probe: topup_completed
```

Rules that keep write queries safe:

- **The platform labels the statement, not the author.** SQL is parsed on save: anything other than a single `SELECT` becomes a write query, and the label cannot be lowered.
- **Write queries have no run button,** and the platform's database logins are read-only anyway, so a mistake in the label still cannot write.
- **Copy with values** fills parameters from the current run and adds a comment header, for example `-- WRITE · Production · run #8812 · needs change approval`.
- **Read queries become runnable only after review** and passing the same save checks as probes; until then they are copy only.

How it connects:

- A failed run suggests runbooks whose `applies_to` matches its failing locations, so the next step appears where the failure is.
- Runbooks are searchable by symptom, entity, catalog table and identifier type, and stored in git next to the probes.
- Each runbook has an owner and a last-verified date; it is flagged for review after 90 days, or when a table it uses drifts from the catalog.

## Plugins

ProbeOwl gets a small plugin system so new tools and integrations can be added without touching the engine. For now every plugin is first-party: built by us, in this repository, reviewed like any other code. The shape is chosen so that, if ProbeOwl is open-sourced, other people could later build plugins on the same base without a redesign.

**What a plugin is.** A folder under `plugins/<name>/` with three parts:

| Part | Contents |
| --- | --- |
| Manifest (`plugin.yaml`) | Name, version, kind (`tool`, `integration`, later `connector`), author, a one-line description, and the capabilities it needs |
| Backend (optional) | A Python module exposing API routes under `/api/plugins/<name>/`, registered through one entry point |
| Web (optional) | Screens added under a **Tools** section in the sidebar, built into the web app |

**Capabilities, declared and enforced.** A plugin states what it needs, and ProbeOwl gives it nothing else:

| Capability | Grants | Example |
| --- | --- | --- |
| `local_input` | Files and text the user uploads or pastes in that session | Compare tool |
| `read_runs` | Read runs and evidence, already masked | Comparing two runs later |
| `run_probes` | Start inspect or validate runs through the normal API, with the investigation record | A bulk-check tool |
| `write_config` | Write files in the loaded folder through the write rules (preview, confirm, audited) | An entity wizard |
| `outbound_http` | Call one named external host | Jira integration |

Rules every plugin follows, whoever writes it:

- No direct database connections. Data comes only through connectors and the engine, so guards and masking always apply.
- Its routes sit behind the same host, origin and custom-header checks as the rest of `owl serve`.
- Every action is audited with the actor and the plugin name.
- Plugins can be turned on or off per user in config; a disabled plugin's routes and screens don't exist.

What changes if ProbeOwl is open-sourced: plugins move to separately installed Python packages discovered through an entry point, web parts load into an isolated frame instead of being built in, and capabilities are shown to the user for approval at install. The manifest and capabilities above stay the same.

## First plugin: compare files

A tool to compare two files a person uploads or pastes: a config from two environments, two exported records, two API responses. It runs entirely on the user's machine.

| Area | Behaviour |
| --- | --- |
| Formats | JSON, YAML, CSV, XML, `.properties`, `.ini`, `.env`, plain text; detected from the content, overridable |
| Comparison | Structural for structured formats: added, removed and changed keys by path (`payments.retry.max`); CSV rows matched by a chosen key column; plain text line by line |
| Options | Ignore key order, ignore array order or match arrays by a key field, ignore paths (`*.updated_at`), ignore whitespace or case, numeric tolerance |
| Output | A summary (12 changed, 3 added, 1 removed), a list of differences by path, and a side-by-side view; export as Markdown or CSV |
| Secrets | Values that look like secrets (keys named `password`, `token`, `secret`, connection strings) are masked in the view by default, with a per-value reveal |
| Storage | Files are processed in memory and never stored. The audit records only that a comparison ran, the file names, sizes and hashes |
| Limits | 10 MB per file; larger files are refused with a clear message |

The comparison engine is shared with compare with a healthy record (M4), so both use the same rules for paths, ignore lists and masking. Comparing records from runs, or one config across Production and UAT, become later additions to the same tool using the `read_runs` capability.

**Jira as the second plugin.** The Jira story draft (phase 2) moves into a Jira integration plugin: today it builds the draft locally; once Jira access exists, the same plugin gains `outbound_http` to create issues.

## Other pain points considered

| Pain point | Feature | Recommended phase |
| --- | --- | --- |
| "What changed?" Google's SRE practice attributes roughly 70% of outages to changes in a live system, as cited by [NeuBird](https://neubird.ai/resources/change-intelligence-correlating-changes-to-incidents) | Change timeline on each run: deployments of the services a probe touches, from Dynatrace events, in the hours before the record | Phase 1 if pipelines already send deployment events to Dynatrace; otherwise phase 2 |
| Confirming a fix works in production. Lightrun reports 88% of teams need two to three redeploy cycles to verify an AI-suggested fix | Re-validate after deploy: re-run a run's IDs or a health probe's offenders after a named deploy and show fixed vs still failing | Phase 2 |
| "How many customers are affected?" | Blast radius: count records matching the failing pattern over a time window | Phase 2 |
| Waiting for production database access | The platform itself: guarded, audited read access through probes | Phase 1 |

## Build vs reuse and MVP plan

Build the engine (entity graph, outcomes, run storage); reuse proven patterns around it rather than products.

- **Reuse:** OpenTelemetry `trace_id` for request probes where services already propagate it, business keys plus time windows elsewhere; Steampipe's plugin pattern for connectors (one typed plugin per system); Soda for warehouse-side health checks instead of rebuilding them.
- **Build:** the entity and location model, key resolution with fallbacks, the five outcomes, conditional assertions, and the run viewer.

Phases, each shippable on its own:

1. **Inspect and validate probes, two connectors.** One SQL connector for MariaDB, SQL Server and Oracle, plus REST, YAML in git, run by ID from a CLI, and a simple web page, with the table catalog, compare with a healthy record, and the knowledge base. Prove it on `topup_completed`.
2. **Async sources and schedules.** Logs and the event index, the change timeline (if not already in phase 1), re-validate after deploy, blast radius, `within` windows, health probes on a schedule, Slack alerts, Jira story drafts, health-to-validate linking.
3. **Scale and access.** Entity registry UI, reverse lookup from any ID, templates, RBAC and masking for the support rollout, CI schema validation.
4. **Breadth.** More connectors (Redis, S3, Kafka direct, vendor APIs), post-deploy sampling, links to runbooks and the error tracker, the Jira API (create issues, check references, post run summaries), and the MCP server for AI agents.

Deployment is self-hosted, starting on a laptop. Phase 0 is a local Docker Compose stack: the runner, the UI, a metadata store, and containers for MariaDB, SQL Server and Oracle Free seeded with the top-up schema and a few broken records. Once probes behave locally, the same stack points at real read replicas from inside the network, with credentials from the internal vault. Phases 1 and 2 serve engineers only; support access arrives with masking and RBAC in phase 3.

Jira in phase 2 is a story draft, not an integration, because the platform cannot reach the Jira API yet. Every failed run, manual or scheduled, produces a ready-to-paste story:

| Part | Content |
| --- | --- |
| Summary | `[Wallet] topup_completed failed: ledger entries missing for tp_91f2c7` |
| Fields | Project, issue type (Bug), suggested priority, labels, the investigator's reference |
| Description | First failure, every location with its outcome, failed expectations, and the audit record |
| Formats | Copy as Jira wiki markup or Markdown; download as .txt, or a one-row CSV for Jira's CSV import |

The engineer creates the story in Jira by hand. The draft is saved with the run, so the audit trail holds exactly what was handed over. Scheduled probes attach one draft per failing probe, listing the offending IDs, never one per record. Creating issues and posting run summaries through the API comes once Jira access is available.

## Decisions and open questions

| Question | Decision |
| --- | --- |
| Databases | MariaDB, Oracle, SQL Server |
| Read replicas | Available; production probes use them only |
| Environments | Selectable per application; production and non-production with different defaults |
| `trace_id` propagation | Partial; business keys and time windows fill the gaps |
| Users | Engineers first, support later |
| Scope | Shared platform for all applications |
| Purpose | Validating that flows executed correctly, not only tracing them |
| Logs and traces | Dynatrace (Grail, DQL) |
| Incidents | Phase 1: story draft to copy or download; Jira API later |
| Hosting | Downloadable package that anyone runs on their own machine (localhost); a team shares a store database and a probe repo; PaaS or SaaS later. ProbeOwl's own store goes in the user's choice of SQLite, PostgreSQL, SQL Server, Oracle or MariaDB |

Still open:

- [ ] Which three flows, in which applications, fail most today? They become the first probes.

* [ ] Do deployment pipelines send deployment events to Dynatrace? This decides whether the change timeline fits phase 1.
* [ ] Which AI clients will connect first: Claude Desktop, Claude Code, or an internal agent?
