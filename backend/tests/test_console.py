# Author: @SaadZarook
"""Colour only where the console shows it: owl's own output and owl serve's log (uvicorn)."""

import io
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from probeowl.cli import main as cli
from probeowl.cli.console import colour_supported, plain_unless_coloured

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


class Terminal(io.StringIO):
    """A stream that says it is a terminal."""

    def isatty(self) -> bool:
        return True


def vt(on: bool) -> Any:
    return lambda: on


@pytest.mark.parametrize(
    ("environ", "windows", "turns_on", "expected"),
    [
        ({}, False, True, True),  # a POSIX terminal
        ({"NO_COLOR": "1"}, False, True, False),  # no-color.org: present and not empty
        ({"NO_COLOR": ""}, False, True, True),  # empty means unset
        ({"TERM": "dumb"}, False, True, False),
        ({}, True, True, True),  # Windows with virtual terminal processing on
        ({}, True, False, False),  # an administrator console that can't take colour codes
        ({"NO_COLOR": "yes"}, True, True, False),
    ],
)
def test_colour_only_on_a_terminal_that_shows_it(
    environ: dict[str, str], windows: bool, turns_on: bool, expected: bool
) -> None:
    found = colour_supported(Terminal(), environ, windows, vt(turns_on))
    assert found is expected


def test_never_colour_into_a_file_or_a_pipe() -> None:
    assert colour_supported(io.StringIO(), {}, windows=False) is False
    assert colour_supported(io.StringIO(), {}, windows=True, turn_on_vt=vt(True)) is False


def test_without_colour_owl_sets_no_color_for_every_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environ: dict[str, str] = {}
    monkeypatch.setattr("probeowl.cli.console.colour_supported", lambda **kw: False)
    plain_unless_coloured(environ)
    assert environ["NO_COLOR"] == "1"
    kept: dict[str, str] = {}
    monkeypatch.setattr("probeowl.cli.console.colour_supported", lambda **kw: True)
    plain_unless_coloured(kept)
    assert "NO_COLOR" not in kept


@pytest.mark.parametrize("supported", [False, True])
def test_owl_serve_tells_uvicorn_whether_to_colour_its_log(
    supported: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[dict[str, Any]] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: served.append(kwargs))
    monkeypatch.setattr(cli, "colour_supported", lambda: supported)
    monkeypatch.setattr(cli, "default_log_file", lambda: tmp_path / "owl-serve.log")
    monkeypatch.setattr(cli, "default_settings_file", lambda: tmp_path / "plugins.yaml")
    url = f"sqlite:///{tmp_path / 'store.db'}"
    assert CliRunner().invoke(cli.app, ["store", "init", "--url", url]).exit_code == 0
    result = CliRunner().invoke(cli.app, ["serve", "--config", str(EXAMPLES), "--url", url])
    assert result.exit_code == 0, result.output
    assert served[0]["use_colors"] is supported
