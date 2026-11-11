# Probes across several systems

A subject's data rarely lives in one place. A visitor's cards are rows in the access database,
but whether a door actually opens is up to the door controller, which has its own REST API. This
walkthrough builds the probe that checks the two agree, following the `card-access` example in
`examples/card-access/`.

## Two connectors

Each system is its own connector, in the application's `connectors/` folder:

```yaml
# card-access/connectors/access_db.yaml
connector: access_db
type: mariadb
owner: access-team
environments:
  prod:
    host: 127.0.0.1
    port: 3306
    database: access
    user: probeowl_ro
    secret: keyring:probeowl/access_db.prod
    replica: true
  uat:
    host: 127.0.0.1
    port: 3306
    database: access_uat
    # ...
```

```yaml
# card-access/connectors/door_controller_api.yaml
connector: door_controller_api
type: rest
owner: access-team
environments:
  prod: { base_url: "http://127.0.0.1:8082" }
  uat: { base_url: "http://127.0.0.1:8082" }
```

`owl connector add --application card_access` writes a file like these, and keeps each password
or token in your keychain. See [Connectors](connectors.md#adding-a-connector).

## Each location names its connector

The entity lists where a visitor's data lives. Each location reads from exactly one connector,
named by `using:`:

```yaml
# card-access/entities/visitor.yaml
entity: visitor
root_key: visitor_profile_id

locations:
  profile:
    using: access_db
    lookup: >
      SELECT id AS visitor_profile_id, employee_no, email, type, status, created_at
      FROM visitor_profile WHERE id = :visitor_profile_id
    exposes: [visitor_profile_id, employee_no, email, type, status, created_at]
    pii: [email]

  cards:
    using: access_db
    lookup: >
      SELECT card_id, visitor_profile_id, status, valid_from, valid_to, access_group
      FROM cards WHERE visitor_profile_id = :visitor_profile_id
    cardinality: many
    max_rows: 50
    exposes: [card_id, visitor_profile_id, status, valid_from, valid_to, access_group]
    each:
      controller:
        using: door_controller_api
        lookup: GET /cards/{{card_id}}
        max_fanout: 10
        exposes: [status]
        compare: [status]
```

`profile` and `cards` are SQL lookups on `access_db`: a single `SELECT` each, with the key as a
`:name` parameter. `controller` is a REST location on `door_controller_api`: one `GET`, with the
key in the path as `{{name}}`. Only `GET` is ever sent. A REST location's `exposes` names fields
of the JSON it returns, and `mask` lists any fields holding personal data, which are masked
before anything is stored or shown.

## How the keys flow

A lookup needs a key, and the **key graph** says where each one comes from:

1. `profile` and `cards` take the root key, `visitor_profile_id`: the subject you typed, or the
   one an [identifier](concepts.md#entity-subject-identifiers-and-root-key) such as a card ID
   led to. Neither waits for the other, so they're read together.
2. `controller` sits under `each:`, so it runs once for every row `cards` returned, with that
   row's `card_id` in its path: `GET /cards/C204518`, then the next card.

A location can also take its key from another location's field with `key_from`, such as
`key_from: record.provider_payment_id` in the wallet example. It then waits for that location,
and if that field is empty it is **Blocked**: ProbeOwl couldn't look, which is not the same as
Missing.

## Fan-out, and its cap

Repeating a lookup per row is **fan-out**. It is capped twice, so one subject can never become a
flood of calls:

- `max_rows: 50` caps the cards read, never more than `access_db`'s row ceiling.
- `max_fanout: 10` caps the controller calls: a visitor with 30 cards gets 10. Rows past the cap
  aren't checked, and any expectation that needed them is skipped and says so.

Each controller call has its own outcome and evidence, so one card the controller doesn't know is
that card's Missing, not a failed run.

## The expectation that spans both systems

```yaml
# card-access/probes/visitor_cards.yaml
probe: visitor_cards
kind: inspect
entity: visitor
expect:
  - count(cards where status == 'active') <= 1
  - all(cards where status == 'active', valid_to > now())
  - all(cards, controller.status == status)     # database and controller agree
```

The last line checks the two systems agree. Inside `all(cards, ...)`, each card is checked in
turn: `status` is that card's status in the database, and `controller.status` is what the door
controller said about the same card. One card that disagrees fails the expectation, and the
result lists the cards that do. The example visitor `VP00012931` has three planted problems for you to find.

## Why there are no joins across databases

There is no SQL that joins `access_db` to another database, and no lookup reads two connectors
at once. Each lookup is one statement, or one `GET`, on one system, and ProbeOwl matches the
results by key itself. That keeps every read small and checkable on its own:

- it runs under its own connector's guards: the row cap, the statement timeout, no waiting on
  locks;
- the save checks, including the plan check, can judge it against its own database;
- its evidence shows the exact query, parameters and result for that one system;
- the systems needn't be the same kind of database, or databases at all.

## Each connector keeps its own guards and kill switch

The limits belong to the connector, not to the probe:

- `access_db` has its own row ceiling (`guards.row_ceiling`, 1,000 by default), and its
  Production binding reads a replica, paused by the
  [circuit breaker](safety.md#circuit-breaker) if the replica falls behind.
- A [kill switch](safety.md#kill-switch) can stop one connector without touching the others:

  ```sh
  owl kill on --application card_access --connector door_controller_api --reason "controller upgrade"
  ```

  While it's on, a run that needs the door controller is **Skipped**: it reads nothing, not even
  from `access_db`, and says which switch stopped it. Probes that don't use the controller, and
  other applications, keep running.

## When a connector has no binding in an environment

A connector with no binding in an environment is **Not configured** there, and the Connectors
screen says so. In this release, a probe that needs it can't run in that environment at all:
`owl check`, and so `owl serve`, refuse the folder until you either

- bind it there: `owl connector add --application card_access`, then give
  `door_controller_api`'s id and answer for the missing environment; or
- leave that environment out of the probe, so it is offered only where every location is bound:

  ```yaml
  probe: visitor_cards
  environments: [prod]
  ```

A later release will run the locations it can and show just the unbound one as Not configured.
