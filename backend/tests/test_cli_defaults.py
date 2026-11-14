# Author: @SaadZarook
"""Where owl looks when not told: the config folder and the default store (#36, decisions 1-3)."""

import os
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from probeowl.cli import defaults
from probeowl.cli import main as cli
from probeowl.cli.defaults import (
    CONFIG_VARIABLE,
    STORE_URL_VARIABLE,
    choose_config_folder,
    config_path,
    unsuitable_for_config,
)
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
    assert config_path(None) == nothing_told.absolute()


def test_otherwise_a_probeowl_folder_not_this_one(nothing_told: Path) -> None:
    (nothing_told / "notes.txt").write_text("a home folder's own files", encoding="utf-8")
    choice = choose_config_folder(None)
    assert choice.folder == nothing_told.absolute() / "probeowl"
    assert choice.by_default and choice.not_here is None


WINDOWS = {"SystemRoot": r"C:\WINDOWS", "ProgramFiles": r"C:\Program Files"}


@pytest.mark.parametrize(
    ("folder", "why"),
    [
        (r"C:\Windows\System32", "system or program folder"),  # PowerShell as administrator
        (r"c:\windows", "system or program folder"),
        (r"C:\Program Files\Some App", "system or program folder"),
        (r"C:\Program Files (x86)", "system or program folder"),
        (r"C:\ProgramData\somewhere", "system or program folder"),
        ("C:\\", "root of a drive"),
        ("D:\\", "root of a drive"),
    ],
)
def test_a_windows_system_folder_or_drive_root_is_never_used(folder: str, why: str) -> None:
    reason = unsuitable_for_config(Path(folder), windows=True, environ=WINDOWS)
    assert reason is not None and why in reason


@pytest.mark.parametrize("folder", [r"C:\Users\ana", r"C:\Users\ana\work", r"D:\projects"])
def test_a_windows_user_folder_is_fine(folder: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults.os, "access", lambda path, mode: True)
    assert unsuitable_for_config(Path(folder), windows=True, environ=WINDOWS) is None


@pytest.mark.parametrize("folder", ["/", "/usr", "/usr/local/bin", "/etc", "/System/Library"])
def test_a_posix_system_folder_is_never_used(folder: str) -> None:
    assert unsuitable_for_config(Path(folder), windows=False) is not None


@pytest.mark.parametrize("folder", ["/home/ana", "/Users/ana/work", "/tmp/x", "/srv/data"])
def test_a_posix_user_folder_is_fine(folder: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(defaults.os, "access", lambda path, mode: True)
    assert unsuitable_for_config(Path(folder), windows=False) is None


def test_a_folder_that_cannot_be_written_to_is_not_used(
    nothing_told: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(defaults.os, "access", lambda path, mode: False)
    reason = unsuitable_for_config(nothing_told, windows=os.name == "nt", environ={})
    assert reason is not None and "can't be written to" in reason


def test_started_in_system32_as_administrator_it_uses_the_home_folder(tmp_path: Path) -> None:
    # PowerShell run as administrator starts in C:\Windows\System32, and may write there.
    choice = choose_config_folder(
        None,
        here=Path(r"C:\Windows\System32"),
        home=tmp_path / "home",
        windows=True,
        environ={"SystemRoot": r"C:\Windows"},
    )
    assert choice.folder == tmp_path / "home" / "probeowl"
    assert choice.not_here is not None and "System32" in choice.not_here


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


def test_owl_serve_started_in_a_system_folder_uses_the_home_folder_and_says_so(
    nothing_told: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    monkeypatch.setattr(defaults, "unsuitable_for_config", lambda *a, **k: "it is a system folder")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    result = CliRunner().invoke(cli.app, ["serve"])
    assert result.exit_code == 0, result.output
    home_folder = tmp_path / "home" / "probeowl"
    assert home_folder.is_dir() and not (nothing_told / "probeowl").exists()
    assert f"Config folder: {home_folder.resolve()} (new, empty)" in result.output
    assert "Not ./probeowl here: it is a system folder." in result.output
    assert "Choose another with --config <folder> or PROBEOWL_CONFIG." in result.output


def test_a_first_owl_serve_makes_its_folder_and_store_and_says_so(
    nothing_told: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served: list[Any] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: served.append(app))
    result = CliRunner().invoke(cli.app, ["serve"])
    assert result.exit_code == 0, result.output
    assert (nothing_told / "probeowl").is_dir()
    assert f"Config folder: {(nothing_told / 'probeowl').resolve()} (new, empty)" in result.output
    assert "Choose another with --config <folder> or PROBEOWL_CONFIG." in result.output
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


# --- owl init ---------------------------------------------------------------------------------


def test_owl_init_takes_a_store_url_and_says_how_when_the_store_is_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("probeowl.cli.init.check_keychain", lambda: None)
    answers = [str(tmp_path / "probeowl"), "access", "", "access-team", "uat", "", "",
               "mariadb", "", "", "", "access_uat", "probeowl_ro", "not-a-real-password-3",
               "n", "", "n", "n"]  # fmt: skip
    url = f"sqlite:///{tmp_path / 'chosen.db'}"
    result = CliRunner().invoke(cli.app, ["init", "--url", url], input="\n".join(answers) + "\n")
    assert result.exit_code != 0
    assert "chosen.db" in result.output  # the store offered is the one given
    message = str(result.exception)
    assert "without one it can't set up" in message
    assert "--url <url> or PROBEOWL_STORE_URL" in message
    assert not (tmp_path / "probeowl" / "access").exists()


# --- A folder with YAML that isn't ProbeOwl's (#46's retest: v0.1.0 in System32, "205 errors") ---


def spring_project(folder: Path) -> None:
    """A project folder of another kind, with an application.yaml of its own."""
    (folder / "service").mkdir()
    (folder / "service" / "application.yaml").write_text(
        "spring:\n  application:\n    name: wallet\nserver:\n  port: 8080\n", encoding="utf-8"
    )


def test_a_folder_whose_application_yaml_is_not_probeowls_is_not_loaded(nothing_told: Path) -> None:
    spring_project(nothing_told)
    choice = choose_config_folder(None)
    assert choice.folder == nothing_told / "probeowl"
    assert choice.not_loaded is not None
    assert "not ProbeOwl's" in choice.not_loaded and "service/application.yaml" in choice.not_loaded


@pytest.mark.parametrize(
    "text",
    [": not yaml at all\n  - [", "application: [a list]\nenvironments: {}\n", "\xff\xfe binary"],
)
def test_broken_or_other_yaml_is_not_probeowls_either(nothing_told: Path, text: str) -> None:
    (nothing_told / "x").mkdir()
    (nothing_told / "x" / "application.yaml").write_bytes(text.encode("latin-1"))
    assert choose_config_folder(None).folder == nothing_told / "probeowl"


def test_probeowls_own_applications_are_still_loaded_from_this_folder(nothing_told: Path) -> None:
    spring_project(nothing_told)  # beside them, harmless
    shutil.copytree(EXAMPLES / "wallet", nothing_told / "wallet")
    choice = choose_config_folder(None)
    assert choice.folder == nothing_told and choice.not_loaded is None


def test_owl_serve_says_why_it_did_not_load_this_folder(
    nothing_told: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    spring_project(nothing_told)
    result = CliRunner().invoke(cli.app, ["serve"])
    assert result.exit_code == 0, result.output
    assert "Not this folder: its application.yaml files are not ProbeOwl's" in result.output
    assert f"Config folder: {(nothing_told / 'probeowl').resolve()} (new, empty)" in result.output


def test_a_folder_named_explicitly_keeps_its_errors(
    nothing_told: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    spring_project(nothing_told)
    result = CliRunner().invoke(cli.app, ["serve", "--config", str(nothing_told)])
    assert result.exit_code != 0
    assert "errors" in str(result.exception)
    monkeypatch.setenv(CONFIG_VARIABLE, str(nothing_told))
    named = CliRunner().invoke(cli.app, ["serve"])
    assert named.exit_code != 0 and "errors" in str(named.exception)
