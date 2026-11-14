# Draft with an AI assistant

`owl mcp` gives an AI assistant ProbeOwl's drafting tools over the Model Context Protocol. The
assistant drafts entities, probes, connectors, catalog entries and runbooks, and checks them with
the same save checks ProbeOwl uses. **You paste them in, and you reload.** It never writes a file
for you.

## Connect your assistant

`owl mcp` runs on stdio: the assistant's app starts it, and they talk over its input and output. It
opens no port. Point it at your config folder with `--config`, as you would `owl serve`.

**Claude Desktop.** In Settings, Developer, Edit Config, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "probeowl": { "command": "owl", "args": ["mcp", "--config", "C:/Users/you/probeowl"] }
  }
}
```

**VS Code, with GitHub Copilot.** Add `.vscode/mcp.json` to your workspace, or use **MCP: Add
Server** from the command palette:

```json
{
  "servers": {
    "probeowl": { "type": "stdio", "command": "owl", "args": ["mcp", "--config", "${workspaceFolder}/probeowl"] }
  }
}
```

**Claude Code:**

```sh
claude mcp add probeowl -- owl mcp --config ./probeowl
```

**The store.** `owl mcp` records every tool call in the audit, in ProbeOwl's store, so it uses the
same store as `owl serve`: by default `~/.probeowl/store.db`, or `$PROBEOWL_STORE_URL` for a team
store on a server. Set that variable in your environment, or keep the default. Never put a
password in `--url` in the assistant's config, such as `claude mcp add`: those files are plain text,
and `owl mcp` refuses a store URL with a password in it. Keep a server store's URL, with its
password, in `$PROBEOWL_STORE_URL`, set in the environment the assistant's app starts from.

Restart the app after adding it. Its list of tools should show `drafting_guide`, `template`,
`examples`, `check_draft`, `list_config` and `schema`.

## How a draft goes

1. Ask for what you want, such as *"a Validate probe for the card example: a visitor has at most one
   active card"*.
2. The assistant reads what's loaded (`list_config`), how such a file is written (`drafting_guide`,
   `template`, `examples`) and, for an entity, the tables last imported (`schema`).
3. It drafts the YAML and runs `check_draft`. The findings are the save checks' own, word for word.
   It fixes them and checks again.
4. When `check_draft` says `ok`, it tells you where the file goes: *"Paste this into
   …/card-access/probes/visitor_active_cards.yaml, then Reload configuration."*
5. You paste it, read it, and reload: **Reload configuration** on Connectors, or `owl reload`. Then
   run it from Validate as any probe.

`check_draft` reads no database. Started as `owl mcp --plan-checks`, it also reads each lookup's
plan, by default in the application's first environment that isn't Production. It reads it
through the same guards as saving, never data, and says only pass or fail, with the reason.

Plan checks through an assistant never touch Production. Asking for a Production environment is
refused, and so is an application with nothing else: *"plan checks through an assistant run in
non-Production environments only"*.

## What it will never do

- **Write anything.** No tool writes a file, saves configuration, imports a schema or changes the
  catalog. You paste, and you reload.
- **Run anything.** No tool starts a probe, a lookup or a runbook step, or reaches a connector.
  The one exception is `--plan-checks`, which reads plans only, and only when you start it that
  way.
- **Give out a secret.** No tool returns a password, token, keychain value, host, URL, login or the
  value of an environment variable. That includes what's in your loaded configuration: a
  connector shows only its name, type and environments. Templates and examples use placeholders
  such as `<host>` and `keyring:probeowl/<connector>.<env>`.
- **Repeat a secret back.** If a draft holds something that looks like a password, a token, a
  connection string, a private key, a host or an IP address, `check_draft` says so by kind and
  line, such as `password (12 characters)`, and never shows any of it. Passwords go in the
  keychain through Add connection or `owl connector add`. Hosts stay in the connector file you
  write yourself.
- **Show data.** No tool returns rows, evidence, run subjects or a catalog's sample values.
  `schema` gives table and column names, types, labels and roles. A PII column is named as PII,
  with no value.
- **Listen on the network.** It runs on stdio only.

Every tool call is recorded in the audit as you, *via MCP*, with the tool's name and whether it
succeeded, never what was sent to it. `~/.probeowl/owl-mcp.log` holds the same.
