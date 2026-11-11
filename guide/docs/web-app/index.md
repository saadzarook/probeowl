# Using the web app

The web app is ProbeOwl's main screen. It talks only to `owl serve` on your own machine.

Choose the **application** and the **environment** in the top bar. In Production, a red banner
says so on every screen, and every run needs a reason and a reference. A kill switch that's on
shows as a banner on every screen too.

| Screen | For |
| --- | --- |
| [Inspect](inspect.md) | A subject's current state, from any of its identifiers |
| [Validate](validate.md) | Whether a flow ran correctly for one subject |
| [Probe editor](probes.md) | Writing and testing probes, with the save checks |
| [Tables](tables.md) | The table catalog: labels, marked columns, schema imports |
| [Connectors](connectors.md) | Each connector's health, role, lag and plan access |
| [Knowledge base](knowledge-base.md) | Runbooks: the steps for a known problem, filled in from a run |
| [Compare files](compare-files.md) | Two files side by side, masked (a tool) |

**Health** (scheduled checks) and **Runs** (every investigation) are in the sidebar, but aren't
built in this release. A run opens on the screen that started it, and the CLI prints its id.
