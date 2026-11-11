# Connectors

Connectors shows each connector of the application in the chosen environment.

![Connectors: wallet's connectors in UAT, with health and limits](../assets/screens/connectors.png){ .screen }

For each connector:

- its **type**, and in Production its **role** under its name: Read replica, Readable secondary or
  Active Data Guard standby;
- its **limits**: the row ceiling and statement timeout;
- its **replica lag**, or why it's unknown, with the grant that would show it;
- its **plan access**: whether the login can read query plans for the index check;
- its **status**: healthy, paused by the circuit breaker, or stopped by a kill switch.

**Test connection** connects once, now, and says how long it took or why it failed. Hosts, logins
and passwords never appear on this screen. To add a connector, see [Connectors](../connectors.md).

## Reload configuration

**Reload configuration** makes `owl serve` read its config folder again, so a connector, binding,
entity or probe you added or changed is loaded without a restart. It says what it loaded and which
applications and connectors changed, and it is recorded in the audit.

- The folder is checked exactly as when `owl serve` starts. If it would refuse to start, nothing
  changes: what was loaded stays loaded, and the message says what is wrong (`owl check <folder>`
  lists every error).
- It is refused while a run is running. Try again once it has finished.
- Kill switches stay as they are: they're kept in the store, not in the folder. A switch on a
  connector you removed is still on if you add the connector back.
- Plugins, the port, the store and the log file change only when `owl serve` is restarted.

`owl reload` does the same from the command line.
