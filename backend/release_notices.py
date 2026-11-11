# Author: @SaadZarook
"""The licences a release wheel ships for the web app it carries, made from web/'s lock file.

Called by hatch_build.py at build time, after `npm ci`, so the notices list exactly the
packages that build used and can never go stale. Development dependencies are left out: they
are not in the build. A package with no licence at all stops the build.
"""

import json
import re
from pathlib import Path
from typing import Any

# The IBM Plex fonts, under the SIL Open Font License: their own licence files ship as they are.
FONT_PACKAGES = ("@fontsource/ibm-plex-sans", "@fontsource/ibm-plex-mono")
_LICENCE_FILE = re.compile(r"^(licen[cs]e|copying)", re.IGNORECASE)
MIT_TEXT = """Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""


def shipped_packages(lock: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Every package the production build can use, by name, from package-lock.json."""
    found = []
    for path, entry in lock.get("packages", {}).items():
        if not path or entry.get("dev") or entry.get("devOptional"):
            continue  # "" is the web app itself
        found.append((path.rsplit("node_modules/", 1)[-1], entry))
    return sorted(found, key=lambda item: item[0])


def licence_text(folder: Path, name: str, spdx: str) -> str:
    """The package's own licence file; for an MIT package without one, the standard text."""
    files = sorted(f for f in folder.iterdir() if f.is_file() and _LICENCE_FILE.match(f.name))
    if files:
        return files[0].read_text(encoding="utf-8", errors="replace").strip()
    if spdx == "MIT":
        author = _author(folder)
        return (
            f"{name} ships no licence file; its package.json declares MIT.\n\n"
            f"Copyright (c) {author}\n\n{MIT_TEXT}".strip()
        )
    raise RuntimeError(f"{name} ({spdx}) ships no licence text: add it before releasing")


def third_party_notices(lock: dict[str, Any], node_modules: Path) -> str:
    """THIRD_PARTY_NOTICES: each shipped package, its version and licence, and the text."""
    sections = [
        "Third-party software in the ProbeOwl web app\n"
        "============================================\n\n"
        "The web app served by owl serve includes the following packages, listed from its\n"
        "package-lock.json when this release was built.\n"
    ]
    for name, entry in shipped_packages(lock):
        spdx = entry.get("license")
        if not spdx:
            raise RuntimeError(f"{name} declares no licence: check it before releasing")
        text = licence_text(node_modules / name, name, spdx)
        rule = "-" * 72
        sections.append(f"{rule}\n{name} {entry.get('version', '')} ({spdx})\n{rule}\n\n{text}\n")
    return "\n".join(sections)


def write_licences(web: Path, out: Path) -> list[Path]:
    """Write THIRD_PARTY_NOTICES and the fonts' OFL files into out/licenses."""
    folder = out / "licenses"
    folder.mkdir(parents=True, exist_ok=True)
    lock = json.loads((web / "package-lock.json").read_text(encoding="utf-8"))
    written = [folder / "THIRD_PARTY_NOTICES.txt"]
    written[0].write_text(third_party_notices(lock, web / "node_modules"), encoding="utf-8")
    for package in FONT_PACKAGES:
        target = folder / f"{package.rsplit('/', 1)[-1]}-OFL.txt"
        target.write_text((web / "node_modules" / package / "LICENSE").read_text(encoding="utf-8"))
        written.append(target)
    return written


def _author(folder: Path) -> str:
    manifest = json.loads((folder / "package.json").read_text(encoding="utf-8"))
    author = manifest.get("author")
    if isinstance(author, dict):
        author = author.get("name")
    return str(author or "its authors")
