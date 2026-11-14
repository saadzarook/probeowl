# Author: @SaadZarook
"""MkDocs hooks: the guide's address comes from backend/probeowl/guide.py, written once there.

Every build is the same offline build: the guide makes no call outside the site serving it.
Material's Google Fonts and the repository widget, which asks the GitHub API, are left out, and
the IBM Plex files the web app already ships (web/node_modules, after npm ci) are copied in. So
the copy on GitHub Pages loads nothing from another site either, as the wheel's doesn't.

The only difference is the address. With PROBEOWL_GUIDE_BUNDLED=1 it is built for owl serve at
/guide/, as the release wheel carries it; otherwise for GitHub Pages, at GUIDE_URL.

Every build also leaves out what isn't needed: source maps, and the search's language packs
(lunr/), which English search never loads. Material's licence goes in licenses/; its bundles keep
the headers of what they include.
"""

import os
import re
import shutil
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[1]
GUIDE_FILE = REPOSITORY / "backend" / "probeowl" / "guide.py"
FONTSOURCE = REPOSITORY / "web" / "node_modules" / "@fontsource"
BUNDLED = os.environ.get("PROBEOWL_GUIDE_BUNDLED") == "1"
# Where owl serve serves it. Only the path counts: the 404 page finds its files from it.
BUNDLED_URL = "http://127.0.0.1:7878/guide/"
PLEX_CSS = "assets/plex.css"
# The weights Material uses: text 400 and 700 (and italics), code 400.
FONTS = {
    "IBM Plex Sans": ("ibm-plex-sans", ("400-normal", "400-italic", "700-normal", "700-italic")),
    "IBM Plex Mono": ("ibm-plex-mono", ("400-normal",)),
}


def on_config(config: Any) -> Any:
    """Set site_url (owl serve's, or GUIDE_URL for Pages) and load nothing from another site."""
    found = re.search(r'^GUIDE_URL = "([^"]+)"$', GUIDE_FILE.read_text(encoding="utf-8"), re.M)
    if found is None:
        raise SystemExit(f"GUIDE_URL not found in {GUIDE_FILE}")
    config["site_url"] = BUNDLED_URL if BUNDLED else found.group(1)
    offline(config)
    return config


def offline(config: Any) -> None:
    """Configure a build that loads nothing from another site."""
    for package, _ in FONTS.values():
        if not (FONTSOURCE / package / "files").is_dir():
            raise SystemExit(f"{FONTSOURCE / package} not found: run npm ci in web/ first")
    config["theme"]["font"] = False  # no Google Fonts: plex.css names the local files
    config["repo_url"] = None  # no repository widget, so no GitHub API call
    config["repo_name"] = None
    config["edit_uri"] = None
    config["extra_css"].append(PLEX_CSS)


def on_post_build(config: Any) -> None:
    """Copy the IBM Plex files in and write the CSS that names them."""
    fonts = Path(config["site_dir"]) / "assets" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    rules = []
    for family, (package, styles) in FONTS.items():
        for style in styles:
            weight, slant = style.split("-")
            name = f"{package}-latin-{style}.woff2"
            shutil.copyfile(FONTSOURCE / package / "files" / name, fonts / name)
            rules.append(
                f'@font-face {{ font-family: "{family}"; font-style: {slant}; '
                f'font-weight: {weight}; font-display: swap; src: url("fonts/{name}") '
                'format("woff2"); }'
            )
    rules.append(':root { --md-text-font: "IBM Plex Sans"; --md-code-font: "IBM Plex Mono"; }')
    css = Path(config["site_dir"]) / PLEX_CSS
    css.write_text("\n".join(rules) + "\n", encoding="utf-8", newline="\n")
    _leave_out_unused(Path(config["site_dir"]))
    _write_licence(Path(config["site_dir"]) / "licenses")


def _leave_out_unused(site: Path) -> None:
    shutil.rmtree(site / "assets" / "javascripts" / "lunr", ignore_errors=True)
    for source_map in site.rglob("*.map"):
        source_map.unlink()


def _write_licence(folder: Path) -> None:
    """Material for MkDocs' licence, from the package this build used."""
    material = distribution("mkdocs-material")
    licence = next((f for f in material.files or [] if f.name.upper().startswith("LICENSE")), None)
    if licence is None:
        raise SystemExit("mkdocs-material's licence file was not found")
    folder.mkdir(parents=True, exist_ok=True)
    text = f"Material for MkDocs {material.version}\n\n{licence.read_text(encoding='utf-8')}"
    (folder / "MKDOCS_MATERIAL_LICENSE.txt").write_text(text, encoding="utf-8", newline="\n")
