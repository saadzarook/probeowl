# Author: @SaadZarook
"""Only the person's own tools may call owl serve: no other site in their browser.

owl serve listens on 127.0.0.1, but any page the person has open can still send requests
there. Two defences, before any route runs:

- DNS rebinding: a page at evil.example whose name later resolves to 127.0.0.1 sends
  `Host: evil.example`. Every request must name owl serve itself in Host.
- Cross-site requests (CSRF): a page can send a POST to 127.0.0.1 without reading the reply,
  enough to start a run or write the catalog. Every request that is not a GET or HEAD must
  come from the web app's own origin (or no origin, as the CLI sends) and carry
  `X-ProbeOwl-Request: 1`. A browser sends a custom header cross-site only after a CORS
  preflight, and owl serve answers no CORS at all, so that preflight always fails.
"""

import json
from collections.abc import Awaitable, Callable, Iterable, MutableMapping
from typing import Any
from urllib.parse import urlsplit

from probeowl.errors import ConfigError

HEADER = "X-ProbeOwl-Request"
_SAFE_METHODS = {"GET", "HEAD"}
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
# The web app's Vite dev (5173) and preview (4173) servers, accepted without --web-origin so the
# dev setup works as cloned. Still loopback only: a page there is already on the person's machine.
VITE_PORTS = (5173, 4173)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def web_origins(port: int, extra: Iterable[str] = ()) -> frozenset[str]:
    """owl serve's own origins, Vite's, and the web app's when served elsewhere on loopback.

    extra: origins such as http://127.0.0.1:5180 (Vite on another port); ConfigError for one
    that is not http(s) on 127.0.0.1 or localhost with a port.
    """
    origins = {f"http://{host}:{p}" for host in _LOOPBACK_HOSTS for p in (port, *VITE_PORTS)}
    for origin in extra:
        origin = origin.strip().rstrip("/")
        if not origin:
            continue
        parts = urlsplit(origin)
        if (
            parts.scheme not in ("http", "https")
            or parts.hostname not in _LOOPBACK_HOSTS
            or parts.port is None
            or parts.path
            or parts.query
            or parts.username
        ):
            raise ConfigError(
                f"web origin {origin!r} must be http://127.0.0.1:<port> or "
                "http://localhost:<port>: only the person's own machine may call owl serve"
            )
        origins.add(f"{parts.scheme}://{parts.hostname}:{parts.port}")
    return frozenset(origins)


class LocalOnly:
    """ASGI middleware refusing requests that did not come from the person's own tools."""

    def __init__(self, app: ASGIApp, port: int, origins: frozenset[str]):
        self._app = app
        self._hosts = {f"{host}:{port}" for host in _LOOPBACK_HOSTS}
        self._origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        reason = self._refused(scope)
        if reason is None:
            await self._app(scope, receive, send)
            return
        await _forbidden(scope, send, reason)

    def _refused(self, scope: Scope) -> str | None:
        headers = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope.get("headers", [])
        }
        host = headers.get("host", "").lower()
        if host not in self._hosts:
            return f"Host {host!r} is not owl serve: use http://127.0.0.1 or http://localhost"
        if scope["method"] in _SAFE_METHODS:
            return None
        origin = headers.get("origin")
        if origin is not None and origin.rstrip("/") not in self._origins:
            return f"requests from {origin} are not accepted: only the ProbeOwl web app may"
        if headers.get(HEADER.lower()) != "1":
            return f"{scope['method']} requests need the header {HEADER}: 1"
        return None


async def _forbidden(scope: Scope, send: Send, detail: str) -> None:
    # Problem details (RFC 9457), as every other error; no CORS headers, ever.
    body = json.dumps(
        {
            "type": "about:blank",
            "title": "Forbidden",
            "status": 403,
            "detail": detail,
            "instance": scope.get("path", ""),
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
