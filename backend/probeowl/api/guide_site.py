# Author: @SaadZarook
"""The guide, served by owl serve at /guide/, so it reads offline and from behind a firewall.

A release wheel carries the guide's bundled build in probeowl/guide_site; a source checkout
serves guide/build once guide_build.py has made it. Each page gets the web app's strict
Content Security Policy, except that it allows that page's own inline scripts by their
SHA-256: MkDocs Material writes a few into every page, and nothing else may run.
"""

import base64
import hashlib
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from probeowl.guide import GUIDE_URL

PACKAGED = Path(__file__).resolve().parents[1] / "guide_site"
SOURCE_BUILD = Path(__file__).resolve().parents[3] / "guide" / "build"
PREFIX = "/guide/"

# Only scripts without src run inline; a JSON block (Material's __config) never runs.
_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)([^>]*)>(.*?)</script>", re.S | re.I)
_NOT_RUN = re.compile(r'type="application/json"', re.I)
# The guide has no form, frame or plugin; its search runs in a worker of its own.
_POLICY = (
    "default-src 'self'; script-src 'self'{hashes}; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; worker-src 'self'; "
    "object-src 'none'; base-uri 'self'; form-action 'none'; frame-ancestors 'none'"
)
# Pages and their files are revalidated each time: a newer wheel serves a newer guide.
REVALIDATE = "no-cache"

NOT_BUILT = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>ProbeOwl guide</title></head>
<body style="font-family: system-ui, sans-serif; margin: 3rem; max-width: 40rem">
<h1>The guide is not built here</h1>
<p>A release of ProbeOwl serves its guide here. In this source checkout, build it once:
<code>cd backend &amp;&amp; uv run python guide_build.py</code> (after <code>npm ci</code> in
<code>web/</code>), then reload. The guide is also online at
<a href="{GUIDE_URL}">{GUIDE_URL}</a>.</p>
</body></html>
"""


def find_guide() -> Path | None:
    """The guide to serve: the one packaged in a release, else this checkout's, else none."""
    for folder in (PACKAGED, SOURCE_BUILD):
        if (folder / "index.html").is_file():
            return folder
    return None


def content_security_policy(page: str) -> str:
    """The guide's policy for one page: its own inline scripts allowed by hash, no others."""
    hashes = "".join(f" 'sha256-{digest}'" for digest in _inline_script_hashes(page))
    return _POLICY.format(hashes=hashes)


def _inline_script_hashes(page: str) -> list[str]:
    digests = []
    for attributes, body in _INLINE_SCRIPT.findall(page):
        if _NOT_RUN.search(attributes):
            continue
        digest = hashlib.sha256(body.encode("utf-8")).digest()
        digests.append(base64.b64encode(digest).decode("ascii"))
    return sorted(set(digests))


def mount_guide(app: FastAPI, build: Path | None) -> None:
    """Serve build at /guide/; without one, a page that says how to build it."""
    root = build.resolve() if build is not None else None

    @app.get("/guide", include_in_schema=False)
    def guide_root() -> Response:
        return RedirectResponse(PREFIX, status_code=308)

    @app.get("/guide/{path:path}", include_in_schema=False)
    def guide(path: str) -> Response:
        if root is None:
            return HTMLResponse(NOT_BUILT, status_code=404, headers=_headers(NOT_BUILT))
        candidate = (root / path).resolve()
        if not candidate.is_relative_to(root):
            return _missing(root)
        if candidate.is_dir():
            if path and not path.endswith("/"):
                # Pages link to each other relatively, so a page's address ends in a slash.
                return RedirectResponse(f"{PREFIX}{path}/", status_code=308)
            candidate = candidate / "index.html"
        if not candidate.is_file():
            return _missing(root)
        if candidate.suffix == ".html":
            return _page(candidate, 200)
        headers = {"Cache-Control": REVALIDATE, "X-Content-Type-Options": "nosniff"}
        return FileResponse(candidate, headers=headers)


def _missing(root: Path) -> Response:
    page = root / "404.html"
    if page.is_file():
        return _page(page, 404)
    return HTMLResponse("Not found", status_code=404, headers=_headers(""))


def _page(path: Path, status: int) -> Response:
    text = path.read_text(encoding="utf-8")
    return HTMLResponse(text, status_code=status, headers=_headers(text))


def _headers(page: str) -> dict[str, str]:
    return {
        "Content-Security-Policy": content_security_policy(page),
        "Cache-Control": REVALIDATE,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
