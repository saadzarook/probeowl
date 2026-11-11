# Author: @SaadZarook
"""Build the guide as owl serve serves it at /guide/: no call outside the machine.

hatch_build.py calls it for a release wheel, after `npm ci` (the guide takes the IBM Plex files
from web/node_modules). From a source checkout, build it once into guide/build, which owl serve
then serves:

    cd backend && uv run python guide_build.py

MkDocs runs with the versions pinned in guide/requirements.txt, through uv, which `uv build`
and `uv run` both name in $UV.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

GUIDE = Path(__file__).resolve().parent.parent / "guide"
SOURCE_BUILD = GUIDE / "build"


def find_uv() -> str:
    """The uv that runs this build: $UV, else uv on PATH."""
    found = os.environ.get("UV") or shutil.which("uv")
    if found is None:
        raise RuntimeError("building the guide needs uv: run this with uv run, or uv build")
    return found


def build_guide(out: Path) -> None:
    """Build the bundled guide into out, replacing what is there."""
    command = [
        find_uv(), "tool", "run", "--quiet",
        "--with-requirements", str(GUIDE / "requirements.txt"),
        "--from", "mkdocs", "mkdocs", "build", "--strict", "--clean", "--site-dir", str(out),
    ]  # fmt: skip
    environment = os.environ | {"PROBEOWL_GUIDE_BUNDLED": "1", "NO_MKDOCS_2_WARNING": "1"}
    subprocess.run(command, cwd=GUIDE, env=environment, check=True)


if __name__ == "__main__":
    build_guide(SOURCE_BUILD)
    print(f"Built the guide in {SOURCE_BUILD}; owl serve serves it at /guide/", file=sys.stderr)
