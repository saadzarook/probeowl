# Author: @SaadZarook
"""Where owl looks when not told: the config folder and the default store (#36, decisions 1-3)."""

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from probeowl.cli import defaults
from probeowl.cli import main as cli
from probeowl.cli.defaults import CONFIG_VARIABLE, STORE_URL_VARIABLE, config_path
from probeowl.connectors.failures import show_raw_driver_errors

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture(autouse=True)
def nothing_told(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """No variables, an empty working folder, and a home folder of the test's own."""
    monkeypatch.delenv(CONFIG_VARIABLE, raising=False)
    monkeypatch.delenv(STORE_URL_VARIABLE, raising=False)
    home = tmp_path / "home"
    monkeypatch.setattr(defaults, "default_store_path", lambda: home / ".probeowl" / "store.db")
    monkeypatch.setattr(cli, "default_log_file", lambda: tmp_path / "logs" / "owl-serve.log")
    monkeypatch.setattr(cli, "default_settings_file", lambda: tmp_path / "plugins.yaml")
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    yield work
    show_raw_driver_errors(False)


# --- The config folder ------------------------------------------------------------------------


def test_the_folder_given_comes_first_then_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert config_path(tmp_path / "given") == tmp_path / "given"
    monkeypatch.setenv(CONFIG_VARIABLE, str(tmp_path / "named"))
    assert config_path(None) == tmp_path / "named"


def test_this_folder_when_it_holds_applications(nothing_told: Path) -> None:
    shutil.copytree(EXAMPLES / "wallet", nothing_told / "wallet")
    assert config_path(None) == Path(".")


def test_otherwise_a_probeowl_folder_not_this_one(nothing_told: Path) -> None:
    (nothing_told / "notes.txt").write_text("a home folder's own files", encoding="utf-8")
    assert config_path(None) == Path("probeowl")


# --- The default store ------------------------------------------------------------------------


def test_the_default_store_is_created_once(tmp_path: Path) -> None:
    store = tmp_path / "home" / ".probeowl" / "store.db"
    assert defaults.ensure_default_store(None) is True
    assert store.exists()
    assert defaults.ensure_default_store(None) is False


def test_a_store_that_was_named_is_never_created_here(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert defaults.ensure_default_store(f"sqlite:///{tmp_path / 'given.db'}") is False
    monkeypatch.setenv(STORE_URL_VARIABLE, f"sqlite:///{tmp_path / 'named.db'}")
    assert defaults.ensure_default_store(None) is False
    assert not (tmp_path / "given.db").exists() and not (tmp_path / "named.db").exists()
    assert not (tmp_path / "home" / ".probeowl" / "store.db").exists()


# --- owl serve on a first run -----------------------------------------------------------------


def test_a_first_owl_serve_makes_its_folder_and_store_and_says_so(
    nothing_told: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[Any] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: served.append(app))
    result = CliRunner().invoke(cli.app, ["serve"])
    assert result.exit_code == 0, result.output
    assert (nothing_told / "probeowl").is_dir()
    assert "(new, empty)" in result.output
    assert (tmp_path / "home" / ".probeowl" / "store.db").exists()
    [app] = served
    state = app.state.setup.state()
    assert state.applications == 0
    assert state.store.created_now is True
    assert Path(state.config_folder) == (nothing_told / "probeowl").resolve()


def test_owl_serve_refuses_a_named_store_without_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    result = CliRunner().invoke(cli.app, ["serve", "--config", str(EXAMPLES), "--url", url])
    assert result.exit_code != 0
    assert "owl store init" in str(result.exception)


# --- owl connector add ------------------------------------------------------------------------


def test_connector_add_refuses_a_named_store_without_tables_before_any_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "config"
    shutil.copytree(EXAMPLES, folder)
    monkeypatch.setenv(STORE_URL_VARIABLE, f"sqlite:///{tmp_path / 'empty.db'}")
    monkeypatch.setattr("probeowl.cli.connector.check_keychain", lambda: None)
    args = ["connector", "add", "--config", str(folder), "--application", "wallet"]
    result = CliRunner().invoke(cli.app, args, input="reports_db\n")
    assert result.exit_code != 0
    assert "has no ProbeOwl tables at this release" in str(result.exception)
    assert "Connector id" not in result.output
    assert not (folder / "wallet" / "connectors" / "reports_db.yaml").exists()


def test_connector_add_creates_the_default_store_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "config"
    shutil.copytree(EXAMPLES, folder)
    monkeypatch.setattr("probeowl.cli.connector.check_keychain", lambda: None)
    args = ["connector", "add", "--config", str(folder), "--application", "wallet"]
    CliRunner().invoke(cli.app, args, input="\n")  # stops at the first question: no id
    assert (tmp_path / "home" / ".probeowl" / "store.db").exists()
