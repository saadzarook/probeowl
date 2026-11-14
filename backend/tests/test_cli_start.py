# Author: @SaadZarook
"""owl's entry point: `owl serve` says it is starting before anything heavy is imported."""

import subprocess
import sys
import types

import pytest

from probeowl.cli import start


@pytest.mark.parametrize(
    ("argv", "line"),
    [
        (["serve"], "Starting ProbeOwl…"),
        (["serve", "--port", "7979"], "Starting ProbeOwl…"),
        (["serve", "--help"], None),
        (["check", "examples"], None),
        (["--version"], None),
        ([], None),
    ],
)
def test_only_owl_serve_says_it_is_starting(argv: list[str], line: str | None) -> None:
    assert start.starting_line(argv) == line


def test_the_line_comes_before_owl_itself_is_imported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    printed_before: list[str] = []
    fake = types.ModuleType("probeowl.cli.main")
    fake.main = lambda: printed_before.append(capsys.readouterr().out)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "probeowl.cli.main", fake)
    monkeypatch.setattr(sys, "argv", ["owl", "serve"])
    start.main()
    assert printed_before == ["Starting ProbeOwl…\n"]


def test_the_entry_point_imports_nothing_heavy() -> None:
    # In a fresh interpreter: nothing of owl's own, FastAPI or SQLAlchemy until it has printed.
    code = (
        "import sys, probeowl.cli.start; "
        "heavy = [m for m in ('probeowl.cli.main', 'fastapi', 'sqlalchemy', 'typer') "
        "if m in sys.modules]; print(heavy)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "[]"
