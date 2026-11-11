# ProbeOwl

ProbeOwl validates production data across databases and APIs, safely. When a customer says their
top-up never arrived, or a visitor's card still opens doors it shouldn't, you type one ID and
ProbeOwl looks everywhere that subject's data lives: tables in SQL Server, Oracle and MariaDB, and
REST APIs. Then it shows you what it found, what's missing and which rule broke, with the exact
query behind every answer.

It runs on your own machine. It reads only from replicas in Production, never writes, and every
lookup goes through guards: one `SELECT`, a row cap, timeouts, and masked personal data.

There are two ways to look:

- **Inspect** shows a subject's current state: a visitor and every card they hold.
- **Validate** checks that a flow ran correctly: a top-up was captured, confirmed by webhook and
  posted to the ledger once.

## Where to start

<div class="grid cards" markdown>

- **[Getting started](getting-started.md)**: run the examples, with their planted problems,
  in one command.
- **[Your first application](first-application.md)**: `owl init`, then your own connector,
  entity and probe.
- **[Concepts](concepts.md)**: applications, connectors, entities, locations, probes and
  outcomes, on one worked example.
- **[Safety](safety.md)**: every refusal, what it protects, and what to do.

</div>

The [CLI reference](cli.md) lists every `owl` command, and [Troubleshooting](troubleshooting.md)
covers the problems people meet on their first run.
