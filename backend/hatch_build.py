# Author: @SaadZarook
"""Build the web app and the guide into a release wheel, so owl serve serves both and no one
installs Node.js or reads the guide online.

Runs for a wheel built from this checkout (`uv build --wheel`), never for the editable install
`uv sync` makes: development serves web/dist, or uses Vite. The mock service worker, which only
the mocked dev server uses, is left out. The licences of everything the build ships go in
probeowl/webapp/licenses: THIRD_PARTY_NOTICES.txt and the IBM Plex fonts' OFL. The guide
goes in probeowl/guide_site, built by guide_build.py, with Material for MkDocs' licence in its
own licenses/.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

# release_notices sits next to this file, which hatch loads by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from guide_build import build_guide
from release_notices import write_licences

WEB = Path(__file__).resolve().parent.parent / "web"
LEFT_OUT = ("mockServiceWorker.js",)


class WebAppBuildHook(BuildHookInterface):  # type: ignore[type-arg]
    """Adds the web app's production build as probeowl/webapp, and the guide as guide_site."""

    PLUGIN_NAME = "custom"
    _work: tempfile.TemporaryDirectory[str] | None = None

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        """Build web/ and include the result; editable installs are left alone."""
        if self.target_name != "wheel" or version == "editable":
            return
        if not (WEB / "package.json").is_file():
            raise RuntimeError(
                f"{WEB} not found: build the wheel from a checkout: uv build --wheel"
            )
        npm = shutil.which("npm")
        if npm is None:
            raise RuntimeError("building a release wheel needs Node.js: npm was not found")
        self._work = tempfile.TemporaryDirectory(prefix="probeowl-webapp-")
        out = Path(self._work.name) / "webapp"
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=WEB, check=True)
        build = [npm, "run", "build", "--", "--outDir", str(out), "--emptyOutDir"]
        subprocess.run(build, cwd=WEB, check=True)
        for name in LEFT_OUT:
            (out / name).unlink(missing_ok=True)
        # From the lock file this build used, so the notices always match what ships.
        write_licences(WEB, out)
        build_data["force_include"][str(out)] = "probeowl/webapp"
        guide = Path(self._work.name) / "guide_site"
        build_guide(guide)  # after npm ci: it takes the IBM Plex files from web/node_modules
        build_data["force_include"][str(guide)] = "probeowl/guide_site"

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        """Remove the temporary build once it is in the wheel."""
        if self._work is not None:
            self._work.cleanup()
            self._work = None
