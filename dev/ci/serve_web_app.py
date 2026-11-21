# Author: @SaadZarook
"""owl serve's web app and guide, mounted exactly as owl serve mounts them, on a given build.

For web/e2e-csp (#148): the browser tests load every screen of a mock build under the real
Content-Security-Policy, nonce and all, which Vite's own servers never send. Loopback only.

    uv run --project backend python dev/ci/serve_web_app.py --build web/dist-csp --port 7990
"""

import argparse
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from probeowl.api.guide_site import find_guide, mount_guide
from probeowl.api.web_app import mount_web_app
from probeowl.errors import NotFoundError


def main() -> None:
    """Serve the build and this checkout's guide on 127.0.0.1 until stopped."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--build", type=Path, required=True, help="a web app build: its index.html")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if not (args.build / "index.html").is_file():
        raise SystemExit(f"no web app build at {args.build}: build it first")
    guide = find_guide()
    if guide is None:
        raise SystemExit("no guide build: run `uv run python guide_build.py` in backend/ first")
    app = FastAPI()

    # The mocks answer /api in the browser; one that reaches here gets owl serve's 404, not a 500.
    @app.exception_handler(NotFoundError)
    def not_found(_: Request, error: NotFoundError) -> JSONResponse:
        return JSONResponse(
            {"title": "Not Found", "status": 404, "detail": str(error)}, status_code=404
        )

    mount_guide(app, guide)
    mount_web_app(app, args.build)  # last, as in owl serve: every other route is matched first
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
