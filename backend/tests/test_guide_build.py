# Author: @SaadZarook
"""The guide's bundled build, as a release wheel carries it: guide_build.py and guide/hooks.py."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guide_build = load("guide_build", BACKEND / "guide_build.py")


@pytest.fixture
def hooks(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setenv("PROBEOWL_GUIDE_BUNDLED", "1")
    return load("guide_hooks", BACKEND.parent / "guide" / "hooks.py")


def test_the_build_runs_the_pinned_mkdocs_bundled_with_the_uv_that_runs_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ran: list[dict[str, Any]] = []
    monkeypatch.setenv("UV", "C:/tools/uv.exe")
    monkeypatch.setattr(
        guide_build.subprocess,
        "run",
        lambda command, **options: ran.append(options | {"c": command}),
    )
    guide_build.build_guide(tmp_path / "out")
    [call] = ran
    assert call["c"][0] == "C:/tools/uv.exe"
    assert str(guide_build.GUIDE / "requirements.txt") in call["c"]
    assert call["c"][-5:] == ["build", "--strict", "--clean", "--site-dir", str(tmp_path / "out")]
    assert call["env"]["PROBEOWL_GUIDE_BUNDLED"] == "1"
    assert (call["cwd"], call["check"]) == (guide_build.GUIDE, True)


def test_without_uv_the_build_says_how_to_run_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("UV", raising=False)
    monkeypatch.setattr(guide_build.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="needs uv: run this with uv run, or uv build"):
        guide_build.find_uv()


def config(site: Path) -> dict[str, Any]:
    return {
        "site_url": None,
        "theme": {"font": {"text": "IBM Plex Sans"}},
        "repo_url": "https://github.com/saadzarook/probeowl",
        "repo_name": "GitHub",
        "edit_uri": "edit/develop/guide/docs/",
        "extra_css": ["assets/probeowl.css"],
        "site_dir": str(site),
    }


def fontsource(root: Path) -> Path:
    for package, styles in (
        ("ibm-plex-sans", ("400-normal", "400-italic", "700-normal", "700-italic")),
        ("ibm-plex-mono", ("400-normal",)),
    ):
        files = root / package / "files"
        files.mkdir(parents=True)
        for style in styles:
            (files / f"{package}-latin-{style}.woff2").write_bytes(b"wOF2")
    return root


def test_the_bundled_config_loads_nothing_from_another_site(
    hooks: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hooks, "FONTSOURCE", fontsource(tmp_path / "fontsource"))
    bundled = hooks.on_config(config(tmp_path / "site"))
    assert bundled["site_url"] == "http://127.0.0.1:7878/guide/"
    assert bundled["theme"]["font"] is False  # no Google Fonts
    assert (bundled["repo_url"], bundled["repo_name"], bundled["edit_uri"]) == (None, None, None)
    assert bundled["extra_css"] == ["assets/probeowl.css", "assets/plex.css"]


def test_the_bundled_config_needs_the_web_apps_fonts(
    hooks: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hooks, "FONTSOURCE", tmp_path / "empty")
    with pytest.raises(SystemExit, match="run npm ci in web/ first"):
        hooks.on_config(config(tmp_path / "site"))


class Licence:
    name = "LICENSE"

    def read_text(self, encoding: str) -> str:
        return "Copyright (c) 2016-2025 Martin Donath"


def test_after_the_build_the_fonts_and_licence_are_in_and_the_unused_files_out(
    hooks: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(hooks, "FONTSOURCE", fontsource(tmp_path / "fontsource"))
    material = type("Material", (), {"version": "9.8.1", "files": [Licence()]})()
    monkeypatch.setattr(hooks, "distribution", lambda name: material)
    site = tmp_path / "site"
    (site / "assets" / "javascripts" / "lunr").mkdir(parents=True)
    (site / "assets" / "javascripts" / "lunr" / "wordcut.js").write_text("x", encoding="utf-8")
    (site / "assets" / "javascripts" / "bundle.js.map").write_text("{}", encoding="utf-8")
    hooks.on_post_build(config(site))
    css = (site / "assets" / "plex.css").read_text(encoding="utf-8")
    assert 'url("fonts/ibm-plex-sans-latin-700-italic.woff2")' in css
    assert '--md-text-font: "IBM Plex Sans"' in css
    assert (site / "assets" / "fonts" / "ibm-plex-mono-latin-400-normal.woff2").is_file()
    assert not (site / "assets" / "javascripts" / "lunr").exists()
    assert not list(site.rglob("*.map"))
    licence = (site / "licenses" / "MKDOCS_MATERIAL_LICENSE.txt").read_text(encoding="utf-8")
    assert licence.startswith("Material for MkDocs 9.8.1") and "Martin Donath" in licence


def test_the_pages_build_is_left_as_it_is(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PROBEOWL_GUIDE_BUNDLED", raising=False)
    pages = load("guide_hooks_pages", BACKEND.parent / "guide" / "hooks.py")
    built = pages.on_config(config(tmp_path / "site"))
    assert built["site_url"] == "https://saadzarook.github.io/probeowl/guide/"
    assert built["repo_url"] == "https://github.com/saadzarook/probeowl"
    pages.on_post_build(config(tmp_path / "site"))
    assert not (tmp_path / "site").exists()
