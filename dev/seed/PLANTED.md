# Planted cases in the seed data

Both agents test against these exact IDs. Change them only by PR, and update
any test or fixture that uses them in the same PR.

## Wallet (SQL Server `wallet-sql`, Oracle `ledger-ora`)

| ID | Situation | What ProbeOwl should report |
| --- | --- | --- |
| `tp_91f2c7` | Top-up succeeded, payment `pay_7hq2m9` captured for USD 500.00, `ledger_txn_id` = `lt_55e1a0`, but **no rows** in `LEDGER_ENTRIES`; **2** webhook deliveries; payment method Apple Pay; app version 5.3.0 | `topup_completed` fails: ledger Missing, credited event and push Missing (phase 2) |
| `tp_91f2b9` | Healthy top-up: USD 200.00, card, app version 5.2.4, 1 webhook, 2 balanced ledger rows | Passes; used as the healthy comparison for `tp_91f2c7` |
| `tp_92a1b4`, `tp_92a1f0`, `tp_92a2c3` | Pending for 28, 22 and 14 minutes | Offenders for `stuck_topups` (phase 2) |
| `tp_93b001` | Succeeded but `ledger_txn_id` is NULL | Ledger shows Blocked, and the missing link is reported |

Tables:

- `wallet-sql`: `dbo.topups` (id, user_id, amount, currency, status, provider_payment_id, ledger_txn_id, payment_method, app_version, customer_email, created_at), `dbo.provider_webhooks`, `dbo.outbox_events`, `dbo.wallets`, `dbo.currencies`, `dbo.topup_status_history`, `dbo.user_provider_ids`
- `ledger-ora`: `LEDGER.LEDGER_ENTRIES` (TXN_ID, ACCOUNT, DIRECTION, AMOUNT, POSTED_AT), `LEDGER.FEE_RULES`
- UAT is a full copy of the Production tables with the same columns, except `LEDGER_UAT.FEE_RULES`, which has one extra column, `MIN_FEE` (drift case)

Columns (lowercase on SQL Server and MariaDB):

- `dbo.provider_webhooks`: `id` (primary key), `payment_id`, `event_type`, `received_at` (indexed). `tp_91f2c7` (payment `pay_7hq2m9`) has **2** rows; `tp_91f2b9` has **1**.
- `dbo.outbox_events`: `id` (primary key), `aggregate_id` (the top-up ID), `status` (`pending`, `published`, `failed`), `attempts`, `last_error`, `created_at` (indexed).
- `LEDGER.LEDGER_ENTRIES`: no surrogate ID. A transaction has two rows, one per `DIRECTION` (`credit`, `debit`), so the primary key is (`TXN_ID`, `DIRECTION`). `POSTED_AT` is indexed.

Status values: `dbo.topups.status` is `pending`, `succeeded` or `failed`.

## Layout

The example connectors point at exactly these addresses and names.

| Service | Address | Production | UAT |
| --- | --- | --- | --- |
| `wallet-sql` | `127.0.0.1:1433` | database `wallet`, schema `dbo` | database `wallet_uat`, schema `dbo` |
| `ledger-ora` | `127.0.0.1:1521`, service `FREEPDB1` | schema `LEDGER` | schema `LEDGER_UAT` |
| `access-maria` | `127.0.0.1:3306` | database `access` | database `access_uat` |

`probeowl_ro` can read, not write, both copies on each container. `seed_rw` can write.

## Card access (MariaDB `access-maria`)

| ID | Situation | What ProbeOwl should report |
| --- | --- | --- |
| `VP00012931` | Contractor, active, 3 cards | `visitor_cards` finds 3 issues |
| `C204518` | Active, valid from 60 days before seeding to 60 days after, controller active | Consistent (the searched card) |
| `C198220` | Active, valid until 30 days before seeding (expired), controller active | Expired but still active |
| `C177031` | Revoked in the database, controller still active | Mismatch |
| `VP00008807` | Inactive visitor with the same email as `VP00012931` | Searching that email returns Ambiguous |

Status values: `cards.status` is `active` or `revoked`; `visitor_profile.status` is `active` or `inactive`.

Tables: `visitor_profile` (id, employee_no, email, type, status, created_at), `cards` (card_id, visitor_profile_id, status, valid_from, valid_to, access_group)

## Fake APIs

- `fake-payments`: `GET /payments/{id}` and `GET /payments?reference={topup_id}`, matching the wallet seeds
- `fake-door-controller`: `GET /cards/{card_id}`, matching the card seeds, including the `C177031` mismatch

All emails are fake (`@example.com` style); no real people.
