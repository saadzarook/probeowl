# Author: @SaadZarook
"""The FastAPI app. Every error leaves as RFC 9457 problem details, never a stack trace."""

from collections.abc import Awaitable, Callable, Iterable, Sequence
from http import HTTPStatus
from importlib.metadata import version
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import JsonValue
from starlette.exceptions import HTTPException

from probeowl.api.guide_site import mount_guide
from probeowl.api.local_only import HEADER, LocalOnly
from probeowl.api.local_only import web_origins as allowed_origins
from probeowl.api.models import FieldError, Problem
from probeowl.api.redaction import Redactor
from probeowl.api.routes import router
from probeowl.api.web_app import mount_web_app
from probeowl.catalog.service import CatalogService
from probeowl.checks.plans import PlanOpener, open_for_plans
from probeowl.checks.saving import ProbeWrites
from probeowl.connector_status import ConnectorStatuses
from probeowl.errors import (
    CapacityError,
    ConfigError,
    ConflictError,
    ConnectorError,
    GuardViolation,
    InputTooLarge,
    KeychainUnavailable,
    KillSwitchEngaged,
    KillSwitchUnknown,
    NotFoundError,
    ProbeOwlError,
    RequestError,
)
from probeowl.plugins.registry import LoadedPlugin, mount_plugins
from probeowl.reload import ConfigReloader
from probeowl.runs import RunService
from probeowl.secret_writes import SecretWrites
from probeowl.setup_writes import SetupWrites

DEFAULT_PORT = 7878

_STATUS_BY_ERROR: dict[type[ProbeOwlError], int] = {
    NotFoundError: 404,
    RequestError: 422,
    KeychainUnavailable: 503,  # before ConfigError, which it is
    ConfigError: 422,
    ConflictError: 409,
    CapacityError: 429,
    GuardViolation: 403,
    ConnectorError: 502,
    InputTooLarge: 413,
    KillSwitchEngaged: 403,
    KillSwitchUnknown: 503,
}


def _no_audit(action: str, details: dict[str, JsonValue]) -> None:
    # Only when generating the contract: owl serve always has a store. Nothing may go unaudited.
    raise ConfigError("nothing can be audited without a store")


def problem(
    request: Request,
    status: int,
    detail: str | None,
    fields: dict[str, str] | None = None,
    problem_type: str = "about:blank",
    extensions: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a problem details response, with the fields not accepted when there are any."""
    redactor: Redactor = request.app.state.redactor
    body = Problem(
        type=problem_type,
        title=HTTPStatus(status).phrase,
        status=status,
        detail=redactor.text(detail) if detail is not None else None,
        instance=request.url.path,
        errors=(
            [FieldError(field=f, message=redactor.text(m)) for f, m in fields.items()]
            if fields
            else None
        ),
        **(extensions or {}),
    )
    return JSONResponse(
        body.model_dump(exclude_none=True),
        status_code=status,
        media_type="application/problem+json",
        headers=headers,
    )


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    return problem(request, exc.status_code, str(exc.detail))


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # The first part of a location is where the value came from (body, query, path).
    fields = {".".join(str(part) for part in e["loc"][1:]): e["msg"] for e in exc.errors()}
    detail = "; ".join(f"{field}: {message}" for field, message in fields.items())
    return problem(request, 422, detail, fields)


async def _probeowl_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProbeOwlError)  # registered for ProbeOwlError only
    status = next((s for t, s in _STATUS_BY_ERROR.items() if isinstance(exc, t)), 500)
    fields = exc.fields if isinstance(exc, RequestError) else None
    return problem(
        request, status, str(exc), fields, exc.problem_type, exc.extensions(), exc.headers()
    )


async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Starlette re-raises after this handler so the server logs the traceback;
    # the client only learns that something failed.
    return problem(request, 500, "An unexpected error occurred; see the ProbeOwl log.")


def _declare_local_only(schema: dict[str, Any]) -> None:
    # The LocalOnly middleware answers 403 before any route: say so on every operation, and
    # that every request changing something needs the header (local_only.py).
    schema["components"].setdefault("securitySchemes", {})["ProbeOwlRequest"] = {
        "type": "apiKey",
        "in": "header",
        "name": HEADER,
        "description": (
            f"Send `{HEADER}: 1` on every request that is not a GET. owl serve also refuses a "
            "Host other than 127.0.0.1 or localhost on its port, and an Origin other than its "
            "web app's. It sends no CORS headers."
        ),
    }
    for operations in schema["paths"].values():
        for method, operation in operations.items():
            operation["responses"].setdefault("403", {"description": "Forbidden"})
            if method not in ("get", "head"):
                operation["security"] = [{"ProbeOwlRequest": []}]


def _use_problem_details(schema: dict[str, Any]) -> None:
    # FastAPI documents errors as application/json and 422 as its own shape; the
    # handlers above return problem details, so the contract must say so.
    _declare_local_only(schema)
    for operations in schema["paths"].values():
        for operation in operations.values():
            for code, response in operation["responses"].items():
                if code[0] in "45":
                    response["content"] = {
                        "application/problem+json": {
                            "schema": {"$ref": "#/components/schemas/Problem"}
                        }
                    }
    for unused in ("HTTPValidationError", "ValidationError"):
        schema["components"]["schemas"].pop(unused, None)


def _operation_id(route: APIRoute) -> str:
    # The generated web client names its methods after these.
    return route.name


class _ProbeOwlAPI(FastAPI):
    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is None:
            _use_problem_details(super().openapi())
        return super().openapi()


# How many times owl serve reloaded its configuration: when it changes, the web app reads again.
CONFIGURATION_HEADER = "X-ProbeOwl-Configuration"


def _configuration_header(
    reloader: ConfigReloader,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def add(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers[CONFIGURATION_HEADER] = str(reloader.reloads)
        return response

    return add


def create_app(
    runs: RunService | None = None,
    catalog: CatalogService | None = None,
    port: int = DEFAULT_PORT,
    web_origins: Iterable[str] = (),
    plans: PlanOpener = open_for_plans,
    probes: ProbeWrites | None = None,
    connectors: ConnectorStatuses | None = None,
    plugins: Sequence[LoadedPlugin] = (),
    web_app: bool = False,
    web_build: Path | None = None,
    guide_build: Path | None = None,
    reloader: ConfigReloader | None = None,
    setup: SetupWrites | None = None,
    secrets: SecretWrites | None = None,
) -> FastAPI:
    """Create the ProbeOwl API; without runs or the catalog, their routes answer 501.

    port: where owl serve listens, which Host must name. web_origins: where the web app is
    served from when not by owl serve itself, such as the Vite dev server. web_app: serve the
    web app's build (web_build) at every path outside /api, and the guide (guide_build) at
    /guide/, as owl serve does.
    """
    app = _ProbeOwlAPI(
        title="ProbeOwl API",
        version=version("probeowl"),
        description="Validate production data across databases and APIs, safely.",
        generate_unique_id_function=_operation_id,
    )
    app.state.runs = runs
    app.state.catalog = catalog
    app.state.plans = plans
    app.state.probes = probes
    app.state.connectors = connectors
    app.state.reloader = reloader
    app.state.setup = setup
    app.state.secrets = secrets
    loaded = runs or catalog or connectors
    app.state.redactor = Redactor(loaded.applications if loaded else {})
    app.state.kill = runs.kill if runs else None
    app.state.plugins = [plugin.manifest for plugin in plugins]
    app.include_router(router)
    mount_plugins(app, plugins, runs.audit if runs else _no_audit)
    if web_app:
        mount_guide(app, guide_build)
        mount_web_app(app, web_build)  # last: every other route is matched first
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(ProbeOwlError, _probeowl_error)
    app.add_exception_handler(Exception, _unexpected_error)
    if reloader is not None:
        app.middleware("http")(_configuration_header(reloader))
    # Outermost, so no route, error handler or docs page answers a refused request.
    app.add_middleware(LocalOnly, port=port, origins=allowed_origins(port, web_origins))
    return app
