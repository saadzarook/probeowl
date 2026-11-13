# Troubleshooting

The problems people meet on a first run, and how to fix each one.

## `owl` isn't found after installing

`uv tool install probeowl` puts `owl` in uv's tool folder, which isn't on your PATH until you run:

```sh
uv tool update-shell
```

once, and open a new terminal. `uv tool dir --bin` shows the folder it adds.

## The web app shows 422 or 404 errors on first load

Fixed in ProbeOwl's first-run work: the Probe editor no longer checks an empty draft, and the
Tables screen no longer asks for schemas never imported. If you still see one, update your
checkout. A schema never imported is now an empty state with the command to import it.

## `owl serve` refuses the web app

The web app says `owl serve` refused it, or writes such as starting a run fail with a 403.

`owl serve` accepts writes only from its own address and from Vite on `127.0.0.1` or `localhost`,
ports 5173 and 4173. If your web app runs anywhere else on your machine, name it:

```sh
owl serve --config probeowl --web-origin http://127.0.0.1:5180
```

Only loopback addresses are accepted: no other machine can use your `owl serve`.

## `npm ci` fails with EPERM

On Windows, `npm ci` fails when another process holds files in `web/node_modules`, such as a Vite
dev server still running, or another `npm ci` sharing npm's cache. Stop every Vite and Node process
running from that folder, then run `npm ci` again. ProbeOwl's own merge checks use a separate
install and cache, so they don't collide with yours.

## Oracle stops when idle (WSL)

The native Oracle runs in a WSL distribution, which WSL shuts down when nothing is using it. Keep
it running in a window of its own:

```sh
bash dev/native/oracle_awake.sh
```

It starts the database if it's down, waits until `FREEPDB1` is open, and holds the distribution
awake until Ctrl-C.

## "takes no --config"

```text
owl validate takes no --config: it runs through owl serve
```

`owl validate`, `owl inspect`, `owl catalog import` and `owl kill` are clients of `owl serve`, which
already has the config. Start `owl serve --config <folder>`, then run the command without
`--config`. See the [CLI reference](cli.md).

## A schema was never imported

The Tables screen, or saving a probe, says a connector's schema was never imported in an
environment. Import it:

```sh
owl catalog import wallet_db --application wallet --env uat
```

Or use **Import from replica** on the Tables screen. The import reads tables, columns, keys and indexes, never
data.

## "the store has no ProbeOwl tables yet" or "this release needs ..."

```text
the store has no ProbeOwl tables yet; run: owl store init
the store schema is at <version>, this release needs <version>; run: owl store upgrade
```

`owl serve` needs ProbeOwl's own store, at this release's version. It creates the default one,
`~/.probeowl/store.db`, the first time, so this comes from a store named by `--url` or
`PROBEOWL_STORE_URL`, or one made by an older release:

```sh
owl store init          # creates ~/.probeowl/store.db, or upgrades an existing store
```

Set `PROBEOWL_STORE_URL` for a server database. A store on a network share is refused.

## "authentication failed"

```text
authentication failed; check the binding's login and secret
```

The login or its password is wrong. Check the binding's `user`, and that its `secret` names an
entry that exists. Copy it to the clipboard rather than printing it, so it never lands in your
terminal's scrollback:

```sh
uvx keyring get probeowl wallet_db.prod | Set-Clipboard              # Windows PowerShell
uvx keyring get probeowl wallet_db.prod | pbcopy                     # macOS
uvx keyring get probeowl wallet_db.prod | xclip -selection clipboard # Linux
uvx keyring set probeowl wallet_db.prod                              # to save it again
```

An empty clipboard means there's no such entry.

`no keychain entry for service 'probeowl' and user 'wallet_db.prod'` means the entry is missing.
On Linux without a desktop keychain, ProbeOwl refuses a keyring that keeps secrets in files.
Use an `env:` secret instead.

## "connection refused"

```text
the connection was refused or could not be made; check the database is up and the binding is right
```

The database isn't listening where the binding says. Check `host` and `port`, that the database is
up, and that nothing blocks the port. **Test connection** on the Connectors screen tries again
straight away.

## TLS: "could not be verified" or "could not be made over TLS"

A server with a self-signed certificate fails verification. Install a certificate signed by a
trusted authority, or, for a test server only, set `trust_server_certificate: true` with a
`trust_server_certificate_reason`. A MariaDB or Oracle server that offers no TLS needs
`encrypt: false` with an `encrypt_reason`. Production refuses either without a reason.

## Everything on a connector is Skipped

The circuit breaker paused it because its replica is too far behind, or a kill switch is on. The
run's reason says which, and the Connectors screen shows the pause or switch. See
[Safety](safety.md#circuit-breaker).

## A record exists but ProbeOwl says Missing

Production reads a replica, which can be behind. If the run carries the caveat "replica lag
unknown", the login can't read the lag, so ProbeOwl can't tell a late record from a missing one.
Grant the lag permission ([Connectors](connectors.md#replica-lag)), and Missing becomes **Stale**
when the replica is too far behind.
