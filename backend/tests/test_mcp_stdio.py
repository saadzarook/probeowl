# Author: @SaadZarook
"""owl mcp end to end (#39): a real MCP client starts `owl mcp` on stdio and drafts the card
example's Validate probe from the tools alone, and check_draft passes it.

PROBEOWL_MCP_TRANSCRIPT=<file> keeps the client's transcript, each call and its reply, for review.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from typer.testing import CliRunner

from probeowl.cli import main as cli
from probeowl.model.check import load_applications
from tests.conftest import EXAMPLES


def owl() -> str:
    folder = Path(sys.executable).parent
    found = folder / ("owl.exe" if os.name == "nt" else "owl")
    if not found.exists():
        pytest.skip("owl is not installed next to this Python")
    return str(found)


class Transcript:
    """Each call the client makes and what came back, as an assistant would see it."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def add(self, call: str, arguments: dict[str, Any], reply: Any) -> None:
        shown = json.dumps(reply, indent=2)
        self.lines.append(f">>> {call}({json.dumps(arguments)})\n{shown}\n")


async def draft_from_the_tools(
    params: StdioServerParameters, transcript: Transcript
) -> dict[str, Any]:
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        transcript.add("list_tools", {}, [t.name for t in tools.tools])

        async def call(tool: str, **arguments: Any) -> Any:
            result = await session.call_tool(tool, arguments)
            assert not result.is_error, result
            transcript.add(tool, arguments, result.structured_content)
            return result.structured_content

        config = await call("list_config", application="card_access")
        guide = await call("drafting_guide", kind="probe")
        template = await call("template", kind="probe")
        example = await call("examples", kind="probe", name="visitor_cards")
        await call("schema", application="card_access", connector="access_db", environment="uat")

        # What an assistant does with them: the entity and its many location from list_config,
        # the owner from the example, the shape from the template.
        [app] = config["applications"]
        [entity] = app["entities"]
        [cards] = [
            loc
            for loc in entity["locations"]
            if loc["cardinality"] == "many" and loc["name"] == "cards"
        ]
        owner = yaml.safe_load(example["items"][0]["yaml"])["owner"]
        draft = (
            template["yaml"]
            .replace("<probe>", "visitor_active_cards")
            .replace("<Probe title>", "A visitor's active cards")
            .replace("<entity>", entity["id"])
            .replace("<owning team>", owner)
            .replace(
                "count(<location>) == 1", f"count({cards['name']} where status == 'active') <= 1"
            )
        )
        assert guide["file"] == "<application>/probes/<probe>.yaml"
        checked = await call("check_draft", kind="probe", text=draft, application=app["id"])
        # Plan checks through an assistant never touch Production: refused, with its reason.
        production = await session.call_tool(
            "check_draft",
            {"kind": "probe", "text": draft, "application": app["id"], "environment": "prod"},
        )
        said = " ".join(getattr(part, "text", "") for part in production.content)
        transcript.add(
            "check_draft",
            {"environment": "prod", "...": "the same draft"},
            {"is_error": production.is_error, "text": said},
        )
        return {"draft": draft, "checked": checked, "production": (production.is_error, said)}


def test_a_real_client_drafts_the_card_probe_over_stdio_and_check_draft_passes_it(
    tmp_path: Path,
) -> None:
    folder = tmp_path / "config"
    shutil.copytree(EXAMPLES, folder)
    url = f"sqlite:///{(tmp_path / 'store.db').as_posix()}"
    assert CliRunner().invoke(cli.app, ["store", "init", "--url", url]).exit_code == 0
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}
    params = StdioServerParameters(
        command=owl(), args=["mcp", "--config", str(folder), "--url", url], env=env
    )
    transcript = Transcript()
    done = anyio.run(draft_from_the_tools, params, transcript)
    keep = os.environ.get("PROBEOWL_MCP_TRANSCRIPT")
    if keep:
        Path(keep).write_text("\n".join(transcript.lines), encoding="utf-8")

    assert done["checked"]["ok"] is True, done["checked"]
    is_error, said = done["production"]
    assert is_error is True
    assert "plan checks through an assistant run in non-Production environments only" in said
    # Pasted exactly where it says, the folder loads with it, as Reload configuration would.
    target = Path(done["checked"]["paste_into"])
    assert target == folder / "card-access" / "probes" / "visitor_active_cards.yaml"
    target.write_text(done["draft"], encoding="utf-8")
    loaded = load_applications(folder)
    assert "visitor_active_cards" in loaded["card_access"].probes
    assert loaded["card_access"].probes["visitor_active_cards"].kind == "validate"
