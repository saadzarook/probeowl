# Concepts

One example runs through this page. A contractor reports a lost card, `C204518`. Support needs to
know: whose card is it, which other cards does that person hold, and does the door controller
agree with the database about each one?

## Application and environment

An **application** is a product area, such as Wallet or Card access. It owns its connectors,
entities, probes, runbooks and table catalog, and you choose it in the top bar. Each application
has **environments**, such as Production and UAT. Production has the strictest rules: replicas
only, guards that can't be relaxed, and a reason and reference on every run.

The card belongs to the Card access application, in Production.

## Connector, binding and role

A **connector** is a read-only client for one system: `access_db` for the MariaDB database,
`door_controller_api` for the controller's REST API. A **binding** says where a connector points
in one environment: host, database, login and the keychain entry holding its password. A
connector with no binding in an environment is **Not configured** there.

A Production binding's **role** says what kind of copy it reads: a read replica, a readable
secondary or an Active Data Guard standby. The Connectors screen shows it under the connector's
name.

## Entity, subject, identifiers and root key

An **entity** is a kind of business subject: a top-up, a visitor. A **subject** is one of them:
visitor `VP00012931`.

An **identifier** is any value that can find a subject. A visitor has four: the profile id, a card
id, an employee number and an email. Each says whether it is unique, and may have a pattern, so
ProbeOwl can tell which one you typed: `C204518` matches `^C\d{6}$`, so it's a card id. One
identifier is the **root key**, `visitor_profile_id`: every other lookup starts from it.

To get from the card to the visitor, the card id is looked up in the `cards` location, which gives
the visitor's profile id. If an identifier finds more than one subject, as a shared email can, the
run is **Ambiguous** and lists the matches. You pick one, or run them all.

## Locations, key graph and cardinality

A **location** is one place a subject's data lives, with its own lookup:

```yaml
locations:
  profile:
    using: access_db
    lookup: SELECT ... FROM visitor_profile WHERE id = :visitor_profile_id
  cards:
    using: access_db
    lookup: SELECT ... FROM cards WHERE visitor_profile_id = :visitor_profile_id
    cardinality: many
    each:
      controller:
        using: door_controller_api
        lookup: GET /cards/{{card_id}}
        max_fanout: 10
```

The **key graph** says where each location gets its key. By default it's the root key.
`key_from: record.provider_payment_id` takes it from another location's field instead, so
locations are read in order, and those that can run together do.

**Cardinality** says how many records to expect: `one` (the default), `optional` or `many`. More
records than that is **Ambiguous**. A `many` location returns at most its `max_rows`, and never
more than the connector's **row ceiling**.

## Fan-out

`each:` repeats a lookup for each row of a `many` location: here, the door controller is asked
about every card. That's **fan-out**. It's capped by `max_fanout`, so a visitor with 500 cards
doesn't become 500 API calls. Rows past the cap are not checked, and any check that needed them
is skipped and says so.

## Probe kinds

A **probe** is a saved, reviewed check: the locations to read and the expectations to test.

- **Inspect** shows a subject's current state: the visitor and every card.
- **Validate** checks that a flow ran correctly: a top-up was captured, confirmed and posted.
- **Health** will run on a schedule across an application to find stuck records (phase 2).

## Expectations

An **expectation** is one rule in a probe:

```yaml
expect:
  - count(cards where status == 'active') <= 1
  - all(cards where status == 'active', valid_to > now())
  - all(cards, controller.status == status)
```

Each passes, fails or is skipped. A **condition**, `when:`, applies a step's expectations only in
some states, such as `when: record.status == 'failed'`.

**Missing data never passes a check.** A location that is Missing has no record, so only counting
reads it: `count()`, `exists()` and `none()` see no rows. Anything else that reads it is skipped,
with "needs ledger, which is Missing": `sum`, `min`, `max`, `all`, `any`, `unique`, or a field
such as `payment.amount`. A skip fails the run, because a run can't pass on data it couldn't see.
Without this rule, `sum(credit) == sum(debit)` would pass as 0 == 0 on a ledger that isn't there.
For a location that may be absent, start the step with `when: exists(refund)`.

A filter that leaves no rows of a Found location is different. `all(cards where status ==
'active', ...)` still holds for a visitor whose cards are all revoked: the rule is about the
location, not the filter.

## Outcomes

Each location ends with one **outcome**:

| Outcome | Meaning |
| --- | --- |
| Found | The lookup returned its record or records. |
| Missing | The lookup worked and returned nothing. |
| Waiting | Not found yet, but still inside its window (`within`). |
| Blocked | Its key could not be found, because an earlier location failed or a link was empty. |
| Ambiguous | More records than its cardinality allows. |
| Error | The lookup itself failed: refused, timed out or unreachable. The reason is in the evidence. |
| Stale | The replica is further behind than the window, so "missing" can't be trusted. |
| Not configured | The connector has no binding in this environment. |

Blocked is not Missing. A top-up whose `ledger_txn_id` was never saved can't be looked up in the
ledger at all, and ProbeOwl reports the empty link itself as the finding.

## Caveats and replica lag

ProbeOwl reads Production from replicas, which can be behind. When it knows the lag, and the lag is
longer than a location's window, a Missing becomes **Stale**. When it can't read the lag (the
login lacks the grant; see [Connectors](connectors.md#replica-lag)), a Missing in Production
carries a **caveat**: "replica lag unknown, the record may not have arrived yet". So does every
expectation that read it, and the run.

## Evidence

Every outcome has its **evidence**: the exact query that ran, its parameters, the guards applied,
and the raw rows, with personal data masked. If you doubt a result, the evidence shows exactly
what was asked and what came back.

## Run and investigation

A **run** is one execution of a probe on one subject. Everything is stored: every lookup, every
result and how long it took. A run you start is an **investigation**, with its record: a reason
(always) and a reference such as a Jira key (in Production), plus optional notes.

For C204518, the run finds visitor VP00012931 with three cards and fails three expectations: two
active cards, one active card past its end date, and a revoked card the controller still reports
as active.
