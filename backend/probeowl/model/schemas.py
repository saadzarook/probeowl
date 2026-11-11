# Author: @SaadZarook
"""Export JSON Schemas for the YAML files to contracts/schemas/.

Run from backend/: `uv run python -m probeowl.model.schemas`.
"""

import json
import sys
from pathlib import Path

from probeowl.model.load import DOCUMENT_KINDS

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "contracts" / "schemas"


def build_schemas() -> dict[str, str]:
    """Return each schema file's name and its JSON text."""
    # One schema per kind of YAML file. Run models belong to the OpenAPI contract instead.
    files = {}
    for kind, adapter in sorted(DOCUMENT_KINDS.items()):
        schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": f"ProbeOwl {kind}",
            **adapter.json_schema(mode="validation"),
        }
        files[f"{kind}.schema.json"] = json.dumps(schema, indent=2) + "\n"
    return files


def write_schemas(out_dir: Path = CONTRACTS_DIR) -> list[Path]:
    """Write every schema file into out_dir and return their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in build_schemas().items():
        path = out_dir / name
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for path in write_schemas(Path(sys.argv[1]) if len(sys.argv) > 1 else CONTRACTS_DIR):
        print(path)
