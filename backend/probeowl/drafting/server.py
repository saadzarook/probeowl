# Author: @SaadZarook
"""owl mcp: the drafting tools over the Model Context Protocol, on stdio only (#39).

The assistant drafts; the person pastes and reloads. There is no network listener: the protocol
runs on stdin and stdout, and nothing else is printed on stdout. Every call is audited as the
person "via MCP", with the tool's name and kind and whether it succeeded, never its arguments'
values or its output; the log (~/.probeowl/owl-mcp.log) holds the same.
"""

import getpass
import logging
from collections.abc import Callable
from typing import TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic.types import JsonValue
from sqlalchemy import Engine

from probeowl.checks.plans import PlanOpener
from probeowl.drafting import tools
from probeowl.drafting.content import DraftKind
from probeowl.model.check import LoadedApplication
from probeowl.model.run import Actor, ActorKind
from probeowl.store.audit import AuditEvent, append_event
from probeowl.store.tables import LOCAL_WORKSPACE

log = logging.getLogger("probeowl.mcp")

# Each tool only reads what owl mcp loaded, and reaches nothing outside it.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, open_world_hint=False)

INSTRUCTIONS = (
    "ProbeOwl's drafting tools. Draft entities, probes, connectors, catalog entries and runbooks "
    "for a person to paste into their config folder, then check them with check_draft. These "
    "tools write nothing, run nothing and never return a credential, host, URL, login, row or run "
    "subject. Use placeholders such as <host> and keyring:probeowl/<connector>.<env> in drafts."
)

T = TypeVar("T")


def create_server(
    applications: dict[str, LoadedApplication],
    store: Engine,
    folder: str,
    plans: PlanOpener | None = None,
    actor_name: str | None = None,
) -> MCPServer:
    """The MCP server over the loaded configuration. plans: only with owl mcp --plan-checks."""
    server = MCPServer(name="probeowl", instructions=INSTRUCTIONS)
    person = actor_name or getpass.getuser()
    actor = Actor(kind=ActorKind.AGENT, name=f"{person} via MCP", on_behalf_of=person)

    def audited(
        tool: str, kind: DraftKind | None, application: str | None, call: Callable[[], T]
    ) -> T:
        details: dict[str, JsonValue] = {
            "tool": tool,
            "kind": kind.value if kind else None,
            "application": application,
        }
        try:
            result = call()
        except Exception:
            _audit(store, actor, details | {"ok": False}, application)
            log.info("mcp %s %s: refused", tool, details["kind"] or "")
            raise
        ok = getattr(result, "ok", True)
        _audit(store, actor, details | {"ok": bool(ok)}, application)
        log.info("mcp %s %s: %s", tool, details["kind"] or "", "ok" if ok else "not ok")
        return result

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def drafting_guide(kind: DraftKind) -> tools.DraftingGuide:
        """How to draft one kind of file: where it goes, its required fields, the rules and the
        save checks' common findings."""
        return audited("drafting_guide", kind, None, lambda: tools.drafting_guide(kind))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def template(kind: DraftKind) -> tools.Template:
        """A file of this kind to start from: placeholders only, its secret a keyring: reference."""
        return audited("template", kind, None, lambda: tools.template(kind))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def examples(kind: DraftKind, name: str | None = None) -> tools.Examples:
        """The bundled examples of this kind, or the one named. A connector's host, port,
        database, login, URL and secret are placeholders."""
        return audited("examples", kind, None, lambda: tools.examples(kind, name))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def check_draft(
        kind: DraftKind,
        text: str,
        application: str | None = None,
        name: str | None = None,
        environment: str | None = None,
    ) -> tools.DraftCheck:
        """The save checks on a draft, after a scan for credentials. It writes nothing. Findings
        name a credential's kind and line, never its value; the draft's text is not repeated.
        environment: where plans are read, when owl mcp was started with --plan-checks."""
        return audited(
            "check_draft",
            kind,
            application,
            lambda: tools.check_draft(
                applications, folder, kind, text, application, name, plans, environment
            ),
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_config(application: str | None = None) -> tools.ConfigList:
        """The loaded configuration by names, titles and kinds: applications, environments,
        entities, probes, connectors and runbooks. Never where a connector points."""
        return audited(
            "list_config", None, application, lambda: tools.list_config(applications, application)
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def schema(application: str, connector: str, environment: str) -> tools.Schema:
        """A connector's tables as last imported, with the catalog's labels and column roles.
        Names and types only: never rows or values."""
        return audited(
            "schema",
            None,
            application,
            lambda: tools.schema(applications, store, application, connector, environment),
        )

    return server


def _audit(
    store: Engine, actor: Actor, details: dict[str, JsonValue], application: str | None
) -> None:
    event = AuditEvent(
        action="mcp_tool_called", actor=actor, application=application, details=details
    )
    with store.begin() as conn:
        append_event(conn, LOCAL_WORKSPACE, event)


async def serve_stdio(server: MCPServer) -> None:
    """Serve on stdin and stdout only: owl mcp opens no port."""
    await server.run_stdio_async()
