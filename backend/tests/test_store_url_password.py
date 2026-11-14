# Author: @SaadZarook
"""A store URL with an inline password: owl mcp refuses it (test_mcp), owl serve and owl store
init warn and go on. None of them ever echoes the URL."""

from typing import Any

import pytest
from typer.testing import CliRunner

from probeowl.cli import main as cli
from probeowl.cli.defaults import INLINE_PASSWORD_WARNING, has_password

INLINE = "postgresql+psycopg://probeowl:store-pw-31c9@store.example/probeowl"
PLAIN = "postgresql+psycopg://probeowl@store.example/probeowl"


@pytest.mark.parametrize(
    ("url", "held"),
    [
        (INLINE, True),
        ("mariadb+pymysql://probeowl@store.example/probeowl?password=store-pw-31c9", True),
        ("mssql+mssqlpython://probeowl@store.example/probeowl?PWD=store-pw-31c9", True),
        (PLAIN, False),
        ("sqlite:///C:/Users/you/.probeowl/store.db", False),
    ],
)
def test_a_password_is_found_in_the_user_info_or_the_query(url: str, held: bool) -> None:
    assert has_password(url) is held


@pytest.mark.parametrize("command", [["store", "init"], ["store", "upgrade"]])
def test_owl_store_init_warns_of_an_inline_password_and_still_runs(
    command: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    created: list[str] = []
    monkeypatch.setattr(cli, "create_store", created.append)
    result = CliRunner().invoke(cli.app, [*command, "--url", INLINE])
    assert result.exit_code == 0, result.output
    assert created == [INLINE]  # warned, not refused
    assert f"warning: {INLINE_PASSWORD_WARNING}" in result.output
    assert "store-pw-31c9" not in result.output and "store.example" not in result.output


def test_owl_serve_warns_of_an_inline_password_before_anything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Stop(Exception):
        pass

    def stop(_: Any) -> bool:
        raise Stop  # the store is never reached here: only the warning before it matters

    monkeypatch.setattr(cli, "ensure_default_store", stop)
    result = CliRunner().invoke(cli.app, ["serve", "--url", INLINE])
    assert isinstance(result.exception, Stop)
    assert f"warning: {INLINE_PASSWORD_WARNING}" in result.output
    assert "store-pw-31c9" not in result.output


def test_no_warning_without_a_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "create_store", lambda url: None)
    result = CliRunner().invoke(cli.app, ["store", "init", "--url", PLAIN])
    assert "warning" not in result.output
