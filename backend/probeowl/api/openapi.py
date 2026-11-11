# Author: @SaadZarook
"""Export the OpenAPI contract to contracts/openapi.yaml.

Run from backend/: `uv run python -m probeowl.api.openapi`.
"""

import sys
from pathlib import Path

import yaml

from probeowl.api.app import create_app
from probeowl.plugins.registry import load_plugins

CONTRACT_PATH = Path(__file__).resolve().parents[3] / "contracts" / "openapi.yaml"


def build_openapi() -> str:
    """Return the OpenAPI document as YAML."""
    # Every first-party plugin, whatever this machine has turned off: one contract for everyone.
    app = create_app(plugins=load_plugins())
    return yaml.safe_dump(app.openapi(), sort_keys=False, allow_unicode=True)


def write_openapi(path: Path = CONTRACT_PATH) -> Path:
    """Write the OpenAPI document to path."""
    path.write_text(build_openapi(), encoding="utf-8", newline="\n")
    return path


if __name__ == "__main__":
    print(write_openapi(Path(sys.argv[1]) if len(sys.argv) > 1 else CONTRACT_PATH))
