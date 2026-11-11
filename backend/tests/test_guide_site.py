# Author: @SaadZarook
"""owl serve serving the guide at /guide/: its pages, each page's own policy, and what it never
serves."""

import base64
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from probeowl.api import guide_site
from probeowl.api.app import create_app
from probeowl.guide import GUIDE_URL
from tests.local_client import local_client

SCOPE = '__md_scope=new URL("../..",location)'
PALETTE = 'var palette=__md_get("__palette")'
CONFIG = '{"base": "../.."}'
PAGE = (
    f"<!doctype html><script>{SCOPE}</script><script>{PALETTE}</script>"
    f'<script id="__config" type="application/json">{CONFIG}</script>'
    '<script src="../../assets/javascripts/bundle.a5f9d56c.min.js"></script>'
)


def sha256(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


@pytest.fixture
def build(tmp_path: Path) -> Path:
    folder = tmp_path / "guide"
    (folder / "web-app" / "connectors").mkdir(parents=True)
    (folder / "assets").mkdir()
    (folder / "index.html").write_text("<script>__md_scope=1</script>home", encoding="utf-8")
    (folder / "web-app" / "connectors" / "index.html").write_text(PAGE, encoding="utf-8")
    (folder / "assets" / "plex.css").write_text(":root {}", encoding="utf-8")
    (folder / "404.html").write_text("<script>missing()</script>not here", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the guide", encoding="utf-8")
    return folder


def client(guide: Path | None, web: Path | None = None) -> TestClient:
    return local_client(create_app(web_app=True, web_build=web, guide_build=guide))


def test_a_page_allows_exactly_its_own_inline_scripts(build: Path) -> None:
    response = client(build).get("/guide/web-app/connectors/")
    assert response.status_code == 200
    assert response.text == PAGE
    policy = response.headers["content-security-policy"]
    script_src = next(p for p in policy.split("; ") if p.startswith("script-src"))
    assert script_src == f"script-src 'self' {' '.join(sorted([sha256(SCOPE), sha256(PALETTE)]))}"
    # The JSON block never runs, so it is not allowed; nothing inline is allowed wholesale.
    assert sha256(CONFIG) not in policy
    assert "frame-ancestors 'none'" in policy
    assert "connect-src 'self'" in policy
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_each_page_has_its_own_hashes(build: Path) -> None:
    served = client(build)
    home = served.get("/guide/").headers["content-security-policy"]
    page = served.get("/guide/web-app/connectors/").headers["content-security-policy"]
    assert sha256("__md_scope=1") in home
    assert sha256("__md_scope=1") not in page


def test_a_page_address_without_its_slash_is_redirected(build: Path) -> None:
    served = client(build)
    response = served.get("/guide/web-app/connectors", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/guide/web-app/connectors/"
    root = served.get("/guide", follow_redirects=False)
    assert (root.status_code, root.headers["location"]) == (308, "/guide/")


def test_a_file_is_served_to_be_revalidated(build: Path) -> None:
    response = client(build).get("/guide/assets/plex.css")
    assert response.text == ":root {}"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize(
    "path", ["/guide/no-such-page/", "/guide/../secret.txt", "/guide/%2e%2e/secret.txt"]
)
def test_a_missing_page_or_one_outside_the_guide_is_its_404_page(build: Path, path: str) -> None:
    response = client(build).get(path)
    assert response.status_code == 404
    assert "outside the guide" not in response.text
    if "secret" not in path:  # an escaped path may be normalised away before it is routed
        assert response.text == "<script>missing()</script>not here"
        assert sha256("missing()") in response.headers["content-security-policy"]


def test_without_a_build_it_says_how_to_build_it_and_where_it_is_online() -> None:
    response = client(None).get("/guide/connectors/")
    assert response.status_code == 404
    assert "uv run python guide_build.py" in response.text
    assert GUIDE_URL in response.text
    assert "script-src 'self';" in response.headers["content-security-policy"]


def test_the_web_app_and_the_api_keep_their_paths(build: Path, tmp_path: Path) -> None:
    web = tmp_path / "dist"
    web.mkdir()
    (web / "index.html").write_text("<!doctype html>app", encoding="utf-8")
    served = client(build, web)
    assert served.get("/connectors").text == "<!doctype html>app"
    assert served.get("/guidebook").text == "<!doctype html>app"
    assert served.get("/api/plugins").status_code == 200
    assert served.get("/guide/").text.endswith("home")


def test_the_release_build_is_found_before_a_checkouts(
    build: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "index.html").write_text("checkout", encoding="utf-8")
    monkeypatch.setattr(guide_site, "PACKAGED", build)
    monkeypatch.setattr(guide_site, "SOURCE_BUILD", checkout)
    assert guide_site.find_guide() == build
    monkeypatch.setattr(guide_site, "PACKAGED", tmp_path / "not-there")
    assert guide_site.find_guide() == checkout
    monkeypatch.setattr(guide_site, "SOURCE_BUILD", tmp_path / "nor-here")
    assert guide_site.find_guide() is None
