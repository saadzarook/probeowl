# Author: @SaadZarook
"""contracts/mcp-tools.json: owl mcp's tools, their input and output schemas and annotations.

Held to the server by a test, and changed only with a maintainer's approval, like the API's
contract (#39). Regenerate with: uv run python -m probeowl.drafting.contract
"""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from probeowl.drafting.server import create_server
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "mcp-tools.json"


def build_contract() -> str:
    """The tools as the server lists them, as stable JSON."""
    with tempfile.TemporaryDirectory() as folder:
        store = create_store_engine(f"sqlite:///{Path(folder) / 'store.db'}")
        try:
            upgrade(store)
            listed = asyncio.run(create_server({}, store, "probeowl").list_tools())
        finally:
            store.dispose()
    tools: list[dict[str, Any]] = [
        {
            "name": tool.name,
            "description": tool.description,
            "annotations": tool.annotations.model_dump(exclude_none=True)
            if tool.annotations
            else {},
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
        }
        for tool in sorted(listed, key=lambda t: t.name)
    ]
    return json.dumps({"server": "probeowl", "tools": tools}, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    CONTRACT_PATH.write_text(build_contract(), encoding="utf-8", newline="\n")
    print(CONTRACT_PATH)
