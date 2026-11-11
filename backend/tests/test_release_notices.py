# Author: @SaadZarook
"""release_notices.py: the licences a release wheel ships for its web app."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_notices", BACKEND / "release_notices.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


notices = load()


def package(root: Path, name: str, licence: str | None = None, **manifest: Any) -> None:
    folder = root / "node_modules" / name
    folder.mkdir(parents=True)
    (folder / "package.json").write_text(json.dumps({"name": name, **manifest}), encoding="utf-8")
    if licence is not None:
        (folder / "LICENSE").write_text(licence, encoding="utf-8")


@pytest.fixture
def web(tmp_path: Path) -> Path:
    package(tmp_path, "react", "MIT License\n\nCopyright (c) Meta")
    package(tmp_path, "scroll-bar", author={"name": "Anton Korzunov"})
    package(tmp_path, "vite", "MIT License (a dev tool)")
    for font in notices.FONT_PACKAGES:
        package(tmp_path, font, "SIL Open Font License, Version 1.1")
    lock = {
        "packages": {
            "": {"name": "probeowl-web"},
            "node_modules/react": {"version": "18.3.1", "license": "MIT"},
            "node_modules/scroll-bar": {"version": "2.3.8", "license": "MIT"},
            "node_modules/vite": {"version": "7.0.0", "license": "MIT", "dev": True},
            **{
                f"node_modules/{font}": {"version": "5.3.0", "license": "OFL-1.1"}
                for font in notices.FONT_PACKAGES
            },
        }
    }
    (tmp_path / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    return tmp_path


def test_the_notices_list_what_ships_with_each_licence_text(web: Path, tmp_path: Path) -> None:
    written = notices.write_licences(web, tmp_path / "out")
    text = (tmp_path / "out" / "licenses" / "THIRD_PARTY_NOTICES.txt").read_text("utf-8")
    assert "react 18.3.1 (MIT)" in text
    assert "Copyright (c) Meta" in text
    assert "@fontsource/ibm-plex-sans 5.3.0 (OFL-1.1)" in text
    # A development tool is not in the build, so it is not in the notices.
    assert "vite" not in text
    assert [p.name for p in written] == [
        "THIRD_PARTY_NOTICES.txt",
        "ibm-plex-sans-OFL.txt",
        "ibm-plex-mono-OFL.txt",
    ]
    ofl = (tmp_path / "out" / "licenses" / "ibm-plex-sans-OFL.txt").read_text("utf-8")
    assert ofl == "SIL Open Font License, Version 1.1"


def test_an_mit_package_with_no_licence_file_gets_the_standard_text(web: Path) -> None:
    lock = json.loads((web / "package-lock.json").read_text("utf-8"))
    text = notices.third_party_notices(lock, web / "node_modules")
    assert "scroll-bar ships no licence file; its package.json declares MIT." in text
    assert "Copyright (c) Anton Korzunov" in text
    assert "Permission is hereby granted, free of charge" in text


def test_a_package_with_no_licence_text_and_no_known_licence_stops_the_build(
    web: Path,
) -> None:
    package(web, "mystery")
    lock = {"packages": {"node_modules/mystery": {"version": "1.0.0", "license": "SEE LICENSE"}}}
    with pytest.raises(RuntimeError, match=r"mystery .* ships no licence text"):
        notices.third_party_notices(lock, web / "node_modules")


def test_a_package_that_declares_no_licence_stops_the_build(web: Path) -> None:
    lock = {"packages": {"node_modules/react": {"version": "18.3.1"}}}
    with pytest.raises(RuntimeError, match="react declares no licence"):
        notices.third_party_notices(lock, web / "node_modules")


def test_nested_packages_are_named_by_their_own_name() -> None:
    lock = {"packages": {"node_modules/a/node_modules/b": {"version": "1.0.0", "license": "ISC"}}}
    assert [name for name, _ in notices.shipped_packages(lock)] == ["b"]
