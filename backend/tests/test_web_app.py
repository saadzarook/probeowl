# Author: @SaadZarook
"""owl serve serving the web app's build: the pages, the bundles, and what it never serves."""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

import pytest
from fastapi.testclient import TestClient

from probeowl.api import web_app
from probeowl.api.app import create_app
from tests.local_client import local_client

INDEX = (
    '<!doctype html><meta property="csp-nonce" nonce="PROBEOWL_CSP_NONCE">'
    '<script type="module" src="/assets/index-abc123.js"></script>'
)
NONCE = re.compile(r"'nonce-([A-Za-z0-9_-]{22})'")


class Served(Protocol):
    """A response's headers: Starlette's test client returns httpx's or httpx2's Response."""

    @property
    def headers(self) -> Mapping[str, str]: ...


def nonce_of(response: Served) -> str:
    """The nonce a page's policy names; the test fails if there is not exactly one."""
    [found] = NONCE.findall(response.headers["content-security-policy"])
    return found


@pytest.fixture
def build(tmp_path: Path) -> Path:
    folder = tmp_path / "dist"
    (folder / "assets").mkdir(parents=True)
    (folder / "index.html").write_text(INDEX, encoding="utf-8")
    (folder / "assets" / "index-abc123.js").write_text("console.log('owl')", encoding="utf-8")
    (folder / "owl.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the build", encoding="utf-8")
    return folder


def client(build: Path | None) -> TestClient:
    return local_client(create_app(web_app=True, web_build=build))


@pytest.mark.parametrize("path", ["/", "/inspect", "/validate", "/tools/compare-files", "/runs"])
def test_every_screen_is_index_html_with_the_strict_policy_and_no_caching(
    build: Path, path: str
) -> None:
    response = client(build).get(path)
    assert response.status_code == 200
    nonce = nonce_of(response)
    assert response.text == INDEX.replace("PROBEOWL_CSP_NONCE", nonce)
    policy = response.headers["content-security-policy"]
    assert policy == web_app.CONTENT_SECURITY_POLICY.format(nonce=nonce)
    assert "script-src 'self';" in policy
    assert f"style-src 'self' 'nonce-{nonce}';" in policy
    assert "unsafe-inline" not in policy
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"


def test_a_hashed_bundle_is_cached_for_good_and_other_files_are_not(build: Path) -> None:
    served = client(build)
    bundle = served.get("/assets/index-abc123.js")
    assert bundle.status_code == 200
    assert bundle.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert bundle.headers["x-content-type-options"] == "nosniff"
    icon = served.get("/owl.svg")
    assert icon.text == "<svg/>"
    assert icon.headers["cache-control"] == "no-cache, no-store, must-revalidate"


def test_the_api_keeps_its_own_routes_and_404s(build: Path) -> None:
    served = client(build)
    assert served.get("/api/plugins").status_code == 200
    missing = served.get("/api/no-such-route")
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/problem+json"
    assert served.get("/openapi.json").json()["info"]["title"] == "ProbeOwl API"


@pytest.mark.parametrize(
    "path", ["/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"]
)
def test_nothing_outside_the_build_is_served(build: Path, path: str) -> None:
    response = client(build).get(path)
    assert "outside the build" not in response.text


def test_without_a_build_it_says_how_to_build_one() -> None:
    response = client(None).get("/validate")
    assert response.status_code == 404
    assert "npm run build" in response.text
    policy = response.headers["content-security-policy"]
    assert policy == web_app.CONTENT_SECURITY_POLICY.format(nonce=nonce_of(response))


def test_the_api_alone_serves_no_web_app(build: Path) -> None:
    response = local_client(create_app()).get("/validate")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"


def test_a_packaged_build_comes_before_the_checkouts(
    tmp_path: Path, build: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_app, "PACKAGED", build)
    monkeypatch.setattr(web_app, "SOURCE_BUILD", tmp_path / "none")
    assert web_app.find_web_app() == build
    monkeypatch.setattr(web_app, "PACKAGED", tmp_path / "none")
    assert web_app.find_web_app() is None


def test_each_page_gets_a_nonce_of_its_own_and_only_pages_are_rewritten(build: Path) -> None:
    served = client(build)
    nonces = [nonce_of(served.get(path)) for path in ("/", "/", "/validate", "/index.html")]
    assert len(set(nonces)) == len(nonces)
    page = served.get("/inspect")
    assert f'nonce="{nonce_of(page)}"' in page.text
    assert "PROBEOWL_CSP_NONCE" not in page.text
    # A bundle is served as built, never rewritten and never given a page's policy.
    bundle = served.get("/assets/index-abc123.js")
    assert bundle.text == "console.log('owl')"
    assert "content-security-policy" not in bundle.headers


def test_without_a_build_the_page_saying_so_has_no_inline_style(tmp_path: Path) -> None:
    response = client(None).get("/")
    assert response.status_code == 404
    assert "style=" not in response.text
    assert "unsafe-inline" not in response.headers["content-security-policy"]
