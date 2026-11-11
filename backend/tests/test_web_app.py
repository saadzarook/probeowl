# Author: @SaadZarook
"""owl serve serving the web app's build: the pages, the bundles, and what it never serves."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from probeowl.api import web_app
from probeowl.api.app import create_app
from tests.local_client import local_client

INDEX = '<!doctype html><script type="module" src="/assets/index-abc123.js"></script>'


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
    assert response.text == INDEX
    assert response.headers["content-security-policy"] == web_app.CONTENT_SECURITY_POLICY
    assert "script-src 'self';" in response.headers["content-security-policy"]
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
    assert response.headers["content-security-policy"] == web_app.CONTENT_SECURITY_POLICY


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
