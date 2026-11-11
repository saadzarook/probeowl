# Security

ProbeOwl holds read access to production databases and APIs, so a flaw in it can expose data or load
a production system. Please report one privately, and give us the chance to fix it before anyone
else hears of it.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: the **Security** tab of this repository, then
**Report a vulnerability**. Please do not open a public issue, pull request or discussion.

Include what you can of:

- what an attacker, or a mistaken probe, could do, and against which database or API;
- the version or commit, and the steps or the probe YAML that show it;
- whether it needs a crafted config, a pasted ID, a reachable `owl serve`, or nothing at all.

Never include real credentials or real data from a system you probed.

You will hear back within five working days. We will agree a date to publish the fix and an
advisory, and credit you unless you would rather not be named.

## Supported versions

ProbeOwl is pre-release. Fixes go into the latest release and `develop`; there are no backports to
older 0.x releases.

## In scope

- **The statement guard**: anything that lets a lookup or a runbook step run something other than one
  read-only `SELECT`, change data, take a lock, call out of the database, reach another database, or
  get past the row cap or the time limits. That includes SQL the guard reads one way and a database
  runs another, such as an executable comment.
- **Masking**: PII or a secret in a response, the store, an export or a log, unmasked.
- **Injection**: a pasted ID, a parameter or a config value that reaches SQL, a URL or a shell as
  anything but a bound value.
- **The local server**: `owl serve` reachable from anywhere but `127.0.0.1` by default, or its
  host, origin and request-header checks bypassed, for example by a web page in the user's browser.
- **Credentials**: a secret read from the keychain reaching a file, a response or a log.
- **Plugins**: a first-party plugin using more than its manifest's capabilities.
- **The store and the audit**: a way to change or hide an audit record through ProbeOwl.

## Out of scope

- What a database login can do outside ProbeOwl. ProbeOwl expects read-only logins on replicas; a
  login with more rights is a configuration to fix, and ProbeOwl's guards are not its only defence.
- Denial of service against your own `owl serve` from your own machine.
- Findings in the fake APIs and seed data under `dev/`, which exist only for tests.
