# Writing runbooks

A **runbook** is the steps for a known problem, such as "top-up captured but wallet not credited".
When a run finds that problem, the run suggests the runbook, and opening it from the run fills in
the run's values. Runbooks live in git next to the probes, and are reviewed like code. This page
follows `examples/wallet/runbooks/topup_not_credited.yaml`.

## Where a runbook goes

One runbook per file, in the application's folder, by convention under `runbooks/`:

```text
probeowl/
  wallet/
    application.yaml
    connectors/
    entities/
    probes/
    runbooks/
      topup_not_credited.yaml
```

`owl check <folder>` checks it with the rest. To load a new or changed runbook into a running
`owl serve`, use **Reload configuration** on the Connectors screen, or `owl reload`.

## The smallest runbook

```yaml
# wallet/runbooks/topup_record_missing.yaml
runbook: topup_record_missing
title: record missing for a topup
application: wallet
applies_to: { entity: topup, when: [record.missing] }
owner: payments-team
last_verified: 2026-11-09
steps:
  - kind: manual
    id: check-the-source
    title: Check where the record should have been written
    text: Look the subject up in the system that writes it, and note what you find.
  - kind: probe
    id: check-again
    title: Check again after the fix
    probe: topup_completed
```

The Knowledge base offers one like it, built from your application's entity and probe, when the
application has no runbooks yet.

| Field | What it is |
| --- | --- |
| `runbook` | Its id: a-z, 0-9 and `_` |
| `title` | The symptom, as a person would search for it |
| `application` | The application it belongs to |
| `applies_to` | When a run suggests it: the `entity`, and `when`, a list of `<location>.<outcome>` |
| `owner` | The team that keeps it right |
| `last_verified` | When someone last checked that the steps still work |
| `steps` | What to do, in order: at least one |

`when` names a location of the entity and an outcome: `found`, `missing`, `waiting`, `blocked`,
`ambiguous`, `error`, `stale` or `not_configured`. No run reports `waiting` yet: it is accepted so a
runbook can be ready for it. `[ledger.missing]` is suggested by any run whose
`ledger` location is Missing. List several to be suggested by any of them.

## Steps

Each step has a `kind`, an `id` (a-z, 0-9 and `-`, unique in the runbook) and a `title`. Results,
ticks and the audit key on the id, so keep it when you edit a step.

### Read query

```yaml
  - kind: read_query
    id: check-outbox
    title: Is the ledger posting stuck in the outbox?
    using: wallet_db
    sql: |
      SELECT TOP (20) id, status, attempts, last_error, created_at
      FROM outbox_events WHERE aggregate_id = :topup_id
      ORDER BY created_at DESC
    reviewed:
      by: s.khan
      date: 2026-10-12
      sql_sha256: 6cd2729de728c782f470a5978a49188ed078f281ef7afff7c41bf32a76b591ab
```

A single `SELECT` on one connector, with its values as `:parameters` filled in from the run. It can
be run from the runbook, read-only and through the connector's guards, only when all of these hold:

- it has a `reviewed` block;
- its `sql_sha256` matches the SQL exactly as it is now;
- it passes the save checks, as a probe's lookup must.

Until then it's a **reference query**: shown, to copy, with the reason it can't run. Changing its
SQL sends it back to reference only until it's reviewed again.

The `reviewed` block is added in the pull request where someone reviewed the query. `owl runbook
hash <file>` prints each read query's `sql_sha256`, for that block. ProbeOwl never adds it itself.

### Write query

```yaml
  - kind: write_query
    id: requeue-posting
    title: Re-queue the ledger posting
    using: wallet_db
    requires: change approval; run by a DBA
    sql: |
      UPDATE outbox_events SET status = 'pending', attempts = 0
      WHERE aggregate_id = :topup_id AND status = 'failed'
```

A query that changes data. **ProbeOwl never runs a write query.** It shows it to copy, headed with
where it applies and the approval it needs (`requires`), and records the copy in the audit. A
`read_query` whose SQL changes data is shown as a write query too.

### External query

```yaml
  - kind: external_query
    id: ledger-errors
    title: Ledger service errors for this top-up
    tool: dynatrace
    query: |
      fetch logs
      | filter loglevel == "ERROR" and contains(content, :topup_id)
      | limit 100
```

A query for another tool, such as Dynatrace DQL, with the run's values filled in. Shown, to copy.

### Manual step

```yaml
  - kind: manual
    id: confirm-capture
    title: Confirm the capture in the provider dashboard
    text: Search the payment ID; check for a refund, dispute or partial capture.
```

A checkbox. Ticking it is saved with the run and audited.

### Probe

```yaml
  - kind: probe
    id: revalidate
    title: Re-validate after the fix
    probe: topup_completed
```

Runs a probe again on the run's subject, for example after a fix.

## Keeping runbooks right

A runbook is flagged **Review due** 90 days after its oldest reviewed read query was reviewed, or,
with none, 90 days after `last_verified`. It's flagged **Table drifted** when a table one of its
queries reads differs between the catalog and its latest schema import, or between environments:
the [Tables](web-app/tables.md) screen shows how. Check the steps, then update the dates in a pull
request.

See [Knowledge base](web-app/knowledge-base.md) for how runbooks look and run in the web app.
