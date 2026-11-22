# Author: @SaadZarook
"""The built web app, served by owl serve itself, so a release needs no Node.js.

Every path outside /api and /guide/ is the web app: a file of the build if there is one, else
index.html, which routes in the browser. A release wheel carries the build in probeowl/webapp;
a source checkout serves web/dist once `npm run build` has made it.
"""

import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response

from probeowl.errors import NotFoundError

PACKAGED = Path(__file__).resolve().parents[1] / "webapp"
SOURCE_BUILD = Path(__file__).resolve().parents[3] / "web" / "dist"

# Only the web app's own files: no inline script, no other site, never framed, and no inline
# style but the ones carrying this page's nonce: CodeMirror's and the Sheet's scroll lock, which
# read it from the page (#110, decided 2026-11-15). React's style props are set through the CSSOM,
# which a style-src does not govern.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'nonce-{nonce}'; "
    "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
# What the build writes where the nonce goes (web/vite.config.ts, html.cspNonce): each page
# served gets a fresh one in its place, and the same one in its policy.
NONCE_PLACEHOLDER = "PROBEOWL_CSP_NONCE"
# index.html names the hashed bundles of its build, so it is fetched afresh every time; the
# bundles themselves never change under their names.
NO_CACHE = "no-cache, no-store, must-revalidate"
IMMUTABLE = "public, max-age=31536000, immutable"

NOT_BUILT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>ProbeOwl</title></head>
<body>
<h1>The web app is not built</h1>
<p>owl serve is running, and its API is at <a href="/docs">/docs</a>. To use the web app from
this source checkout, build it once: <code>cd web &amp;&amp; npm ci &amp;&amp; npm run build</code>,
then reload. While working on the web app, use <code>npm run dev</code> instead.</p>
</body></html>
"""


def find_web_app() -> Path | None:
    """The build to serve: the one packaged in a release, else this checkout's, else none."""
    for folder in (PACKAGED, SOURCE_BUILD):
        if (folder / "index.html").is_file():
            return folder
    return None


def mount_web_app(app: FastAPI, build: Path | None) -> None:
    """Serve build at every path outside /api; without one, a page that says how to build it."""
    root = build.resolve() if build is not None else None

    @app.get("/{path:path}", include_in_schema=False)
    def web_app(path: str, request: Request) -> Response:
        if path == "api" or path.startswith("api/"):
            # An API path no route matched is the API's 404, never the web app.
            raise NotFoundError(f"no API route {request.url.path}")
        if root is None:
            return HTMLResponse(NOT_BUILT, status_code=404, headers=_page_headers(new_nonce()))
        found = _file(root, path)
        if found is None or found.suffix == ".html":
            return _page(found or root / "index.html")
        cache = IMMUTABLE if found.parent.name == "assets" else NO_CACHE
        return FileResponse(
            found, headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"}
        )


def _file(root: Path, path: str) -> Path | None:
    """The build's file at path, or None; never anything outside the build."""
    if not path:
        return None
    candidate = (root / path).resolve()
    if not candidate.is_relative_to(root) or not candidate.is_file():
        return None
    return candidate


def new_nonce() -> str:
    """A nonce for one page: 128 bits, base64url, never reused."""
    return secrets.token_urlsafe(16)


def _page(path: Path) -> HTMLResponse:
    """A page of the build, its nonce written in, under a policy naming the same nonce."""
    nonce = new_nonce()
    text = path.read_text(encoding="utf-8").replace(NONCE_PLACEHOLDER, nonce)
    return HTMLResponse(text, headers=_page_headers(nonce))


def _page_headers(nonce: str) -> dict[str, str]:
    return {
        "Content-Security-Policy": CONTENT_SECURITY_POLICY.format(nonce=nonce),
        "Cache-Control": NO_CACHE,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }
