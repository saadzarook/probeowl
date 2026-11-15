# Audit

**Audit** lists everything ProbeOwl recorded, newest first: every run started and finished,
every setup change, password saved, kill switch, connection test and assistant tool call. Each
row says when, who, what, where (application and environment), the run it belongs to, and its
details.

It's read-only: nothing on the screen changes the audit, and reading it isn't recorded.

- **Filters:** Actor, Action, Application and Environment, each by exact name. Press **Show** to
  apply them. Actor finds what a person did, and what an assistant did for them
  (`s.khan via MCP`).
- **Show older** loads the next 50.
- **Open run** opens the run an event belongs to.

Details are masked by today's PII rules, including values recorded before an identifier became
PII, which the audit keeps as they were written. A host, login or password never shows. The same
list from a terminal: [`owl audit`](../cli.md#owl-audit).
