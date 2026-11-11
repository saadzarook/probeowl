# Writing probes

This page builds the Wallet example's `topup_completed` probe from an empty file. A top-up
completed when the provider captured the payment, sent its webhook, and the ledger posted one
balanced double entry. The data lives in three systems: the wallet database (SQL Server), the
payment provider's API, and the ledger (Oracle).

## 1. The entity: what a top-up is

An entity names the subject's **root key** and the **identifiers** that can find it:

```yaml
# wallet/entities/topup.yaml
entity: topup
title: Top-up
root_key: topup_id
owner: payments-team

identifiers:
  topup_id: { pattern: '^tp_[0-9a-z]{6}$', unique: true }
  provider_payment_id: { pattern: '^pay_[0-9a-z]{6}$', via: record.provider_payment_id, unique: true }
  ledger_txn_id: { pattern: '^lt_[0-9a-z]{6}$', via: record.ledger_txn_id, unique: true }
```

`via` says where an identifier is looked up to find the root key, so a support agent can paste the
provider's payment id instead of the top-up id. Patterns let ProbeOwl tell which one was typed. Add
`pii: true` to an identifier such as an email, and it's masked wherever the run is shown.

## 2. Locations: where its data lives

```yaml
locations:
  record:
    title: Top-up record
    using: wallet_db
    lookup: >
      SELECT id AS topup_id, user_id, amount, currency, status, provider_payment_id,
             ledger_txn_id, customer_email, created_at
      FROM topups WHERE id = :topup_id
    exposes: [topup_id, amount, currency, status, provider_payment_id, ledger_txn_id, created_at]
    pii: [customer_email]

  payment:
    title: Provider payment
    using: checkout_api
    key_from: record.provider_payment_id
    lookup: GET /payments/{{provider_payment_id}}
    fallback: GET /payments?reference={{topup_id}}
    exposes: [status, amount, currency]
    mask: [customer.email, customer.phone]

  webhook:
    title: Webhook deliveries
    using: wallet_db
    key_from: record.provider_payment_id
    lookup: >
      SELECT id, payment_id, event_type, received_at
      FROM provider_webhooks WHERE payment_id = :provider_payment_id
    cardinality: many
    max_rows: 20

  ledger:
    title: Ledger entries
    using: ledger_db
    key_from: record.ledger_txn_id
    lookup: >
      SELECT account, direction, amount, posted_at
      FROM ledger_entries WHERE txn_id = :ledger_txn_id
    cardinality: many
```

- **`lookup`** is a single `SELECT` for a SQL connector, or `GET <path>` for a REST one. Keys are
  `:parameters` in SQL and `{{name}}` in a path, always bound, never pasted in.
- **`key_from`** gives a location its key from another location's field. Here `payment` waits for
  `record`. If `record.ledger_txn_id` is empty, `ledger` is **Blocked**, and the empty link is
  reported: that's the finding.
- **`fallback`** is tried when the lookup finds nothing, such as searching the provider by our own
  reference when the link was never saved.
- **`exposes`** lists the fields other locations, identifiers and expectations may use.
- **`pii`** (SQL columns) and **`mask`** (REST fields, as dotted paths) are masked in every result
  and in the evidence, before anything is stored. Columns the catalog marks as PII are masked too.
- **`cardinality: many`** with **`max_rows`** for lists. The connector's row ceiling (default
  1,000) is the most any lookup can return.
- **`title`** is how the web app and the run header name it. Leave it out and the name is used,
  with spaces: `ledger_entries` becomes "Ledger entries".

## 3. Expectations: what must be true

```yaml
# wallet/probes/topup_completed.yaml
probe: topup_completed
title: Top-up completed
kind: validate
entity: topup
owner: payments-team
description: A top-up was captured, confirmed by webhook and posted to the ledger once.
steps:
  - expect: record.status in ['succeeded', 'failed', 'pending']
  - expect:
      - payment.amount == record.amount
      - payment.currency == record.currency
  - expect: count(webhook) >= 1
  - when: record.status == 'succeeded'
    expect:
      - count(ledger) == 2
      - sum(ledger.amount where direction == 'credit')
        == sum(ledger.amount where direction == 'debit')
  - when: record.status == 'failed'
    expect:
      - record.ledger_txn_id == null
      - payment.status != 'captured'
```

The expression language:

| You write | It means |
| --- | --- |
| `record.status`, `payment.amount` | A field of a location's one record |
| `==`, `!=`, `<`, `<=`, `>`, `>=`, `in [...]`, `not in [...]` | Comparison, by meaning: `500`, `500.00` and `'500.00'` are equal |
| `and`, `or`, `not`, `+`, `-`, `*`, `/` | As usual |
| `count(ledger)`, `exists(ledger)` | How many rows; whether there are any |
| `sum(ledger.amount)`, `min(...)`, `max(...)` | Over a field of every row |
| `unique(cards.card_id)` | No value repeats |
| `all(cards, status == 'active')`, `any(...)`, `none(...)` | Every, some or no row passes |
| `count(ledger where direction == 'credit')` | Only the rows that match |
| `now()` | The current time |

Inside `all`, `any`, `none` and `where`, a bare name such as `status` is a field of that row. A
fan-out location is read per row: `all(cards, controller.status == status)`.

Steps run in order. A step with `when:` runs only when its condition holds; otherwise its
expectations are skipped, which doesn't count against the run. A location that is **Missing** is
seen only by `count()`, `exists()` and `none()`: anything else that reads it is skipped, and the
skip fails the run. Use `when: exists(refund)` for a location that may be absent. See
[Concepts](concepts.md#expectations).

## 4. Save checks

The Probe editor checks a probe before it saves it, and `owl check` runs the same checks on a whole
folder, except those that need a database:

- **Read-only**: every lookup is a single `SELECT` (or `WITH ... SELECT`), with nothing that
  locks or writes.
- **Keyed or time-bounded**: a lookup on a Transaction, Log or Audit table filters by a key or a
  bounded time window, so it never scans a growing table.
- **Index use**: the query plan, read from the database, must seek an index, not scan the table.
  In Production, a login that can't read plans needs you to acknowledge that, and it's recorded.
- **PII masked**: a column the catalog marks as PII, if a lookup selects it, is masked by that
  location's `pii`.
- **Row ceiling**: no `max_rows` above the connector's ceiling.
- **Environments**: every location's connector is bound where the probe may run.
- **Time offsets**: a time with an offset is never compared with a column without one (see
  [Connectors](connectors.md#time-offsets)).

Each check shows what passed and why something didn't, with what to change. Saving shows the diff
of the file before writing it.

## 5. Try it before saving

**Test run** in the Probe editor runs your draft on a real ID, with the same guards and limits as
any run. Its version starts with `draft:`, so it's never mistaken for the saved probe. When the
result is what you expect, save.

## Inspect probes

An inspect probe has the same shape, with `kind: inspect` and usually a flat `expect:` list:

```yaml
probe: visitor_cards
title: Visitor cards
kind: inspect
entity: visitor
owner: access-team
expect:
  - count(cards where status == 'active') <= 1
  - all(cards where status == 'active', valid_to > now())
  - all(cards, controller.status == status)
```

## Healthy comparison

Give the entity a `healthy_sample`: an indexed `SELECT` of recent root keys that look healthy.
Then a failed run can be compared, field by field, with the newest subject that passes the same
probe. Fields marked `expected_to_vary`, such as an amount, are shown but never highlighted as a
likely cause. The Wallet example's `topup.yaml` has one.
