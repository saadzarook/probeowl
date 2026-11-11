# Author: @SaadZarook
"""owl serve refuses other sites in the person's browser: DNS rebinding and CSRF."""

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from probeowl.api.app import create_app
from probeowl.api.local_only import HEADER, web_origins
from probeowl.cli import main as cli
from probeowl.cli import server
from probeowl.errors import ConfigError
from tests.conftest import Setup
from tests.local_client import LOCAL
from tests.test_cli import EXAMPLES, ready_store
from tests.test_runs import body, no_lookups, runs_stored

VITE = "http://127.0.0.1:5173"


def raw(setup: Setup, base_url: str = LOCAL, **app: Any) -> TestClient:
    """A client that sends only what each test gives it: no header by default."""
    return TestClient(
        create_app(setup.service(), **app), base_url=base_url, raise_server_exceptions=False
    )


def refused(response: Any, detail: str) -> None:
    assert response.status_code == 403, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert detail in response.json()["detail"]
    assert not [h for h in response.headers if h.lower().startswith("access-control-")]


# --- Host: DNS rebinding ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["evil.example", "evil.example:7878", "127.0.0.1:9999", "localhost", "0.0.0.0:7878"]
)
def test_a_host_that_is_not_owl_serve_is_refused_even_for_a_get(setup: Setup, host: str) -> None:
    response = raw(setup).get("/api/applications", headers={"Host": host})
    refused(response, "is not owl serve")


@pytest.mark.parametrize("host", ["127.0.0.1:7878", "localhost:7878", "LOCALHOST:7878"])
def test_owl_serve_by_its_own_name_is_served(setup: Setup, host: str) -> None:
    assert raw(setup).get("/api/applications", headers={"Host": host}).status_code == 200


def test_the_host_follows_the_port_owl_serve_listens_on(setup: Setup) -> None:
    client = raw(setup, base_url="http://127.0.0.1:8080", port=8080)
    assert client.get("/api/applications").status_code == 200
    refused(client.get("/api/applications", headers={"Host": "127.0.0.1:7878"}), "not owl serve")


def test_a_rebound_post_with_the_header_is_still_refused(setup: Setup) -> None:
    response = raw(setup).post(
        "/api/runs", json=body(), headers={"Host": "evil.example", HEADER: "1"}
    )
    refused(response, "is not owl serve")
    assert no_lookups(setup) and runs_stored(setup) == 0


# --- Origin and the header: CSRF ----------------------------------------------------------------


@pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1:6666"])
def test_a_foreign_origin_is_refused_with_or_without_the_header(setup: Setup, origin: str) -> None:
    response = raw(setup).post("/api/runs", json=body(), headers={"Origin": origin, HEADER: "1"})
    refused(response, f"requests from {origin} are not accepted")
    assert no_lookups(setup) and runs_stored(setup) == 0


@pytest.mark.parametrize("value", [None, "0", "true", ""])
def test_a_change_without_the_header_is_refused(setup: Setup, value: str | None) -> None:
    headers = {} if value is None else {HEADER: value}
    response = raw(setup).post("/api/runs", json=body(), headers=headers)
    refused(response, f"POST requests need the header {HEADER}: 1")
    assert no_lookups(setup) and runs_stored(setup) == 0


def test_a_catalog_write_without_the_header_is_refused(setup: Setup) -> None:
    response = raw(setup).put(
        "/api/applications/wallet/catalog/tables/wallet_db/wallets",
        json={"entry": {}},
        headers={"Origin": "https://evil.example"},
    )
    refused(response, "not accepted")


def test_the_cli_sends_no_origin_and_the_header(setup: Setup) -> None:
    response = raw(setup).post("/api/runs", json=body(), headers={HEADER: "1"})
    assert response.status_code == 202, response.text
    assert server.client("http://127.0.0.1:7878").headers[HEADER] == "1"


@pytest.mark.parametrize("origin", ["http://127.0.0.1:7878", "http://localhost:7878", VITE])
def test_the_web_app_from_its_own_origin_with_the_header_is_served(
    setup: Setup, origin: str
) -> None:
    client = raw(setup, web_origins=[VITE])
    response = client.post("/api/runs", json=body(), headers={"Origin": origin, HEADER: "1"})
    assert response.status_code == 202, response.text
    assert not [h for h in response.headers if h.lower().startswith("access-control-")]


@pytest.mark.parametrize(
    "origin",
    [VITE, "http://localhost:5173", "http://127.0.0.1:4173", "http://localhost:4173"],
)
def test_vite_dev_and_preview_on_loopback_are_served_without_being_told(
    setup: Setup, origin: str
) -> None:
    # Vite prints http://localhost:5173 on some machines and 127.0.0.1 on others: both work.
    response = raw(setup).post("/api/runs", json=body(), headers={"Origin": origin, HEADER: "1"})
    assert response.status_code == 202, response.text


@pytest.mark.parametrize("origin", ["http://127.0.0.1:5180", "http://localhost:3000"])
def test_another_loopback_port_is_refused_unless_owl_serve_is_told_about_it(
    setup: Setup, origin: str
) -> None:
    response = raw(setup).post("/api/runs", json=body(), headers={"Origin": origin, HEADER: "1"})
    refused(response, "not accepted")
    told = raw(setup, web_origins=[origin])
    response = told.post("/api/runs", json=body(), headers={"Origin": origin, HEADER: "1"})
    assert response.status_code == 202, response.text


def test_a_get_needs_no_header(setup: Setup) -> None:
    assert raw(setup).get("/api/applications", headers={"Origin": VITE}).status_code == 200


# --- No CORS ------------------------------------------------------------------------------------


def test_a_cors_preflight_is_refused_and_answered_without_cors_headers(setup: Setup) -> None:
    response = raw(setup).options(
        "/api/runs",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": HEADER.lower(),
        },
    )
    refused(response, "not accepted")


def test_no_response_carries_cors_headers(setup: Setup) -> None:
    client = raw(setup, web_origins=[VITE])
    for response in (
        client.get("/api/applications", headers={"Origin": VITE}),
        client.get("/api/runs/nope", headers={"Origin": "https://evil.example"}),
        client.post("/api/runs", json=body(), headers={"Origin": VITE, HEADER: "1"}),
    ):
        assert not [h for h in response.headers if h.lower().startswith("access-control-")]


# --- Web origins owl serve accepts --------------------------------------------------------------


def test_web_origins_are_loopback_only() -> None:
    vite = {f"http://{host}:{port}" for host in ("127.0.0.1", "localhost") for port in (5173, 4173)}
    assert web_origins(7878, [" http://localhost:5180/ ", ""]) == {
        "http://127.0.0.1:7878",
        "http://localhost:7878",
        "http://localhost:5180",
        *vite,
    }
    for bad in (
        "https://evil.example",
        "http://127.0.0.1",  # no port
        "http://127.0.0.1:5173/app",
        "file:///tmp",
        "http://user@127.0.0.1:5173",
    ):
        with pytest.raises(ConfigError, match=re.escape("must be http://127.0.0.1:<port>")):
            web_origins(7878, [bad])


def test_owl_serve_takes_web_origins_and_refuses_a_foreign_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    served: list[Any] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: served.append(app))
    url = ready_store(tmp_path)
    args = ["serve", "--config", str(EXAMPLES), "--url", url]
    monkeypatch.setenv("PROBEOWL_WEB_ORIGINS", "http://127.0.0.1:5180,http://localhost:5181")
    assert CliRunner().invoke(cli.app, args).exit_code == 0
    client = TestClient(served[0], base_url=LOCAL, raise_server_exceptions=False)
    response = client.post(
        "/api/runs/x/matches",
        json={"subjects": ["a"]},
        headers={"Origin": "http://localhost:5181", HEADER: "1"},
    )
    assert response.status_code != 403  # accepted past the guard (404: no such run)
    monkeypatch.delenv("PROBEOWL_WEB_ORIGINS")
    result = CliRunner().invoke(cli.app, [*args, "--web-origin", "https://evil.example"])
    assert result.exit_code != 0
