# Author: @SaadZarook
"""Plugins: what a manifest may declare, who turns them off, and the boundary around them."""

import ast
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from probeowl.api.app import create_app
from probeowl.cli import main as cli
from probeowl.errors import CapabilityNotDeclared, ConfigError, InputTooLarge
from probeowl.plugins import api as plugin_api
from probeowl.plugins.api import Capability, PluginContext, PluginManifest
from probeowl.plugins.registry import (
    FIRST_PARTY,
    load_plugins,
    plugins_off,
    unknown_plugins_message,
)
from probeowl.store.audit import list_events
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.local_client import local_client

ROOT = Path(__file__).parents[2]
FIXTURES = "tests.plugin_fixtures"
MANIFEST = Path(__file__).parent / "plugin_fixtures" / "sample" / "plugin.yaml"


def manifest(**changes: Any) -> dict[str, Any]:
    return {
        "name": "sample",
        "version": "1.0.0",
        "kind": "tool",
        "author": "@SaadZarook",
        "description": "A plugin.",
        "capabilities": ["local_input"],
    } | changes


def sample_plugins(off: tuple[str, ...] = ()) -> Any:
    return load_plugins(off, ("sample",), FIXTURES)


@pytest.fixture
def api(setup: Setup) -> TestClient:
    return local_client(create_app(setup.service(), plugins=sample_plugins()))


# --- The manifest --------------------------------------------------------------------------------


def test_a_manifest_is_read() -> None:
    [plugin] = sample_plugins()
    assert plugin.manifest.name == "sample"
    assert plugin.manifest.capabilities == [Capability.LOCAL_INPUT]
    assert plugin.register is not None


@pytest.mark.parametrize("capability", ["read_runs", "run_probes", "write_config", "outbound_http"])
def test_a_capability_not_built_is_refused_and_says_so(capability: str) -> None:
    with pytest.raises(ValueError, match=f"capability {capability} is planned but not built"):
        PluginManifest.model_validate(manifest(capabilities=[capability]))


def test_an_unknown_capability_is_refused() -> None:
    with pytest.raises(ValueError, match="local_input"):
        PluginManifest.model_validate(manifest(capabilities=["delete_everything"]))


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "connector"},
        {"name": "Not-A-Name"},
        {"version": ""},
        {"author": ""},
        {"description": ""},
        {"surprise": True},
    ],
    ids=lambda c: next(iter(c)),
)
def test_a_bad_manifest_is_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        PluginManifest.model_validate(manifest(**changes))


def _broken_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str | None) -> str:
    # A name of its own: Python keeps an imported package for the whole test session.
    package = f"broken_plugins_{uuid.uuid4().hex}"
    folder = tmp_path / package / "sample"
    folder.mkdir(parents=True)
    (tmp_path / package / "__init__.py").write_text("")
    (folder / "__init__.py").write_text("")
    if text is not None:
        (folder / "plugin.yaml").write_text(text)
    monkeypatch.syspath_prepend(str(tmp_path))
    return package


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (None, "plugin sample: plugin.yaml cannot be read"),
        ("name: [", "plugin sample: plugin.yaml cannot be read"),
        (
            "name: sample\nversion: 1\nkind: tool\nauthor: a\ndescription: d\n"
            "capabilities: [run_probes]\n",
            "plugin sample: plugin.yaml: ",
        ),
    ],
    ids=["missing", "unreadable", "invalid"],
)
def test_loading_a_bad_plugin_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str | None, expected: str
) -> None:
    package = _broken_package(tmp_path, monkeypatch, text)
    with pytest.raises(ConfigError, match=expected):
        load_plugins((), ("sample",), package)


def test_a_manifest_must_name_its_own_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "name: other\nversion: '1'\nkind: tool\nauthor: a\ndescription: d\n"
    package = _broken_package(tmp_path, monkeypatch, text)
    with pytest.raises(ConfigError, match="names it other"):
        load_plugins((), ("sample",), package)


def test_a_backend_is_optional(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = "name: sample\nversion: '1'\nkind: tool\nauthor: a\ndescription: d\n"
    package = _broken_package(tmp_path, monkeypatch, text)
    [plugin] = load_plugins((), ("sample",), package)
    assert plugin.register is None


def test_a_backend_without_register_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "name: sample\nversion: '1'\nkind: tool\nauthor: a\ndescription: d\n"
    package = _broken_package(tmp_path, monkeypatch, text)
    backend = tmp_path / package / "sample" / "backend"
    backend.mkdir()
    (backend / "__init__.py").write_text("")
    with pytest.raises(ConfigError, match="no register"):
        load_plugins((), ("sample",), package)


# --- Turning plugins off -------------------------------------------------------------------------


def test_no_settings_file_means_all_on(tmp_path: Path) -> None:
    assert plugins_off(tmp_path / "plugins.yaml") == set()


@pytest.mark.parametrize(
    ("text", "off"),
    [("disabled: [sample]\n", {"sample"}), ("disabled: []\n", set()), ("", set())],
    ids=["one off", "none off", "empty file"],
)
def test_the_settings_file_says_what_is_off(tmp_path: Path, text: str, off: set[str]) -> None:
    path = tmp_path / "plugins.yaml"
    path.write_text(text)
    assert plugins_off(path) == off


@pytest.mark.parametrize(
    "text",
    [
        "disabled: [",
        "disabled: sample\n",
        "- sample\n",
        "off: [sample]\n",
        "disabled: [Not-A-Name]\n",
    ],
    ids=["unreadable yaml", "not a list", "not a mapping", "unknown key", "bad name"],
)
def test_a_malformed_settings_file_is_refused_clearly(tmp_path: Path, text: str) -> None:
    path = tmp_path / "plugins.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=r"plugins\.yaml"):
        plugins_off(path)


def test_a_settings_file_that_cannot_be_decoded_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "plugins.yaml"
    path.write_bytes(b"disabled: [\xff\xfe]\n")
    with pytest.raises(ConfigError, match="cannot be read"):
        plugins_off(path)


def test_a_settings_path_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "plugins.yaml"
    path.mkdir()  # exists, and reading it fails on every system
    with pytest.raises(ConfigError, match="cannot be read"):
        plugins_off(path)


def test_a_plugin_that_is_off_has_no_routes_and_is_not_listed(setup: Setup) -> None:
    api = local_client(create_app(setup.service(), plugins=sample_plugins(off=("sample",))))
    assert api.get("/api/plugins").json() == []
    assert (
        api.post("/api/plugins/sample/measure", json={"name": "a", "text": "b"}).status_code == 404
    )
    assert "/api/plugins/sample/measure" not in api.get("/openapi.json").json()["paths"]


def test_turning_off_something_that_is_not_a_plugin_still_loads_the_rest() -> None:
    assert len(load_plugins(("nope",), ("sample",), FIXTURES)) == 1


def test_a_name_that_is_not_a_plugin_is_named_with_the_known_ones() -> None:
    message = unknown_plugins_message({"compare_flies"}, ("compare_files", "jira"))
    assert message == (
        "plugins.yaml disables compare_flies, not a plugin; the plugins are: compare_files, jira"
    )
    assert unknown_plugins_message({"jira"}, ("compare_files", "jira")) is None


def test_with_no_plugins_the_message_says_so() -> None:
    assert unknown_plugins_message({"x"}, ()) == (
        "plugins.yaml disables x, not a plugin; the plugins are: none yet"
    )


def test_a_plugin_that_is_off_must_still_have_a_good_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _broken_package(tmp_path, monkeypatch, "name: [")
    with pytest.raises(ConfigError):
        load_plugins(("sample",), ("sample",), package)


@pytest.fixture
def serve_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    url = f"sqlite:///{tmp_path / 'store.db'}"
    upgrade(create_store_engine(url))
    monkeypatch.setenv("PROBEOWL_STORE_URL", url)
    monkeypatch.setattr(cli, "default_log_file", lambda: tmp_path / "owl.log")
    settings = tmp_path / "plugins.yaml"
    monkeypatch.setattr(cli, "default_settings_file", lambda: settings)
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    yield settings


def test_owl_serve_refuses_to_start_on_a_malformed_settings_file(serve_setup: Path) -> None:
    serve_setup.write_text("disabled: sample\n")
    result = CliRunner().invoke(cli.app, ["serve", "--config", str(ROOT / "examples")])
    assert result.exit_code != 0
    assert isinstance(result.exception, ConfigError)
    assert "plugins.yaml is not valid" in str(result.exception)


def test_owl_serve_starts_with_no_settings_file(serve_setup: Path) -> None:
    result = CliRunner().invoke(cli.app, ["serve", "--config", str(ROOT / "examples")])
    assert result.exit_code == 0, result.output
    assert "plugins.yaml disables" not in result.output


def test_owl_serve_prints_a_name_that_is_not_a_plugin_and_starts(serve_setup: Path) -> None:
    serve_setup.write_text("disabled: [compare_flies]\n")
    result = CliRunner().invoke(cli.app, ["serve", "--config", str(ROOT / "examples")])
    assert result.exit_code == 0, result.output
    assert "warning: plugins.yaml disables compare_flies, not a plugin" in result.output
    assert "the plugins are:" in result.output


# --- What a plugin gets --------------------------------------------------------------------------


def test_routes_sit_under_the_plugins_name_and_list_it(api: TestClient) -> None:
    [listed] = api.get("/api/plugins").json()
    assert (listed["name"], listed["kind"]) == ("sample", "tool")
    response = api.post("/api/plugins/sample/measure", json={"name": "a.txt", "text": "hello"})
    assert response.status_code == 200
    assert response.json() == {"size": 5}


def test_a_plugins_action_is_audited_with_the_actor_and_its_name(
    api: TestClient, setup: Setup
) -> None:
    api.post("/api/plugins/sample/measure", json={"name": "a.txt", "text": "hello"})
    with setup.store.connect() as conn:
        [event] = [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "plugin_action"]
    assert event.actor.name == "s.khan"
    assert event.details["plugin"] == "sample"
    assert event.details["what"] == "measured"
    assert event.details["file"] == "a.txt"
    assert "hello" not in str(event.details)


def test_a_plugin_cannot_record_another_plugins_name() -> None:
    recorded: dict[str, object] = {}
    context = PluginContext(
        PluginManifest.model_validate(manifest()), lambda what, details: recorded.update(details)
    )
    context.audit("x", {"plugin": "someone_else", "what": "y", "file": "a"})
    assert recorded == {"plugin": "sample", "what": "x", "file": "a"}


def test_input_past_the_limit_is_a_413_problem(api: TestClient) -> None:
    response = api.post("/api/plugins/sample/measure", json={"name": "a.txt", "text": "x" * 17})
    assert response.status_code == 413
    assert response.headers["content-type"] == "application/problem+json"
    assert "a.txt is 17 bytes; the limit is 16 bytes per file" in response.json()["detail"]


def test_a_plugins_routes_sit_behind_the_same_checks(setup: Setup) -> None:
    api = TestClient(create_app(setup.service(), plugins=sample_plugins()))
    body = {"name": "a", "text": "b"}
    bad_host = {"host": "evil.example", "X-ProbeOwl-Request": "1"}
    assert api.post("/api/plugins/sample/measure", json=body, headers=bad_host).status_code == 403
    no_header = {"host": "127.0.0.1:7878"}
    assert api.post("/api/plugins/sample/measure", json=body, headers=no_header).status_code == 403
    other_origin = {
        "host": "127.0.0.1:7878",
        "origin": "http://evil.example",
        "X-ProbeOwl-Request": "1",
    }
    assert (
        api.post("/api/plugins/sample/measure", json=body, headers=other_origin).status_code == 403
    )


def test_a_capability_not_declared_is_not_given() -> None:
    declared = PluginManifest.model_validate(manifest(capabilities=[]))
    context = PluginContext(declared, lambda what, details: None)
    with pytest.raises(CapabilityNotDeclared, match="does not declare local_input"):
        context.local_input("a.txt", "hello", 100)


def test_local_input_measures_and_hashes_in_bytes() -> None:
    context = PluginContext(PluginManifest.model_validate(manifest()), lambda what, details: None)
    found = context.local_input("a.txt", "é", 2)
    assert found.size == 2
    assert len(found.sha256) == 64
    with pytest.raises(InputTooLarge):
        context.local_input("a.txt", "é", 1)


def test_the_first_party_list_loads() -> None:
    assert [p.manifest.name for p in load_plugins()] == list(FIRST_PARTY)


# --- The boundary --------------------------------------------------------------------------------


def test_the_plugin_api_exposes_only_the_agreed_surface() -> None:
    assert set(plugin_api.__all__) == {
        # The plugin's own.
        "Capability",
        "CapabilityNotDeclared",
        "InputTooLarge",
        "LocalInput",
        "PluginContext",
        "PluginManifest",
        "RequestError",
        # The comparison engine: its entry points, and their option and result types.
        "compare",
        "compare_lines",
        "Options",
        "ArrayMatch",
        "Leaf",
        "Side",
        "Comparison",
        "Difference",
        "Change",
        "MASKED",
    }


ALLOWED_FROM_PROBEOWL = {"probeowl.plugins.api"}
# What would reach a database, the network, a secret or the process around the plugin API.
FORBIDDEN = {
    "sqlalchemy",
    "pymysql",
    "oracledb",
    "mssql_python",
    "httpx",
    "requests",
    "urllib",
    "socket",
    "http",
    "keyring",
    "subprocess",
    "importlib",
    "ctypes",
}


def imports_of(source: str) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"__import__", "exec", "eval"}
        ):
            found.add(node.func.id)
    return found


def boundary_breaks(source: str) -> set[str]:
    """What a plugin's source imports beyond the plugin API."""
    breaks = set()
    for name in imports_of(source):
        top = name.split(".")[0]
        if top == "probeowl" and name not in ALLOWED_FROM_PROBEOWL:
            breaks.add(name)
        if top in FORBIDDEN or name in {"__import__", "exec", "eval"}:
            breaks.add(name)
    return breaks


@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy",
        "from probeowl.store.tables import runs",
        "from probeowl.connectors.factory import open_sql",
        "from probeowl.secrets import resolve_secret",
        "import probeowl.runs",
        "import httpx",
        "from urllib.request import urlopen",
        "import keyring",
        "import subprocess",
        "import importlib",
        "__import__('probeowl.secrets')",
    ],
)
def test_the_boundary_check_catches(source: str) -> None:
    assert boundary_breaks(source)


def test_the_boundary_check_allows_the_plugin_api() -> None:
    source = (
        "from probeowl.plugins.api import PluginContext\nfrom fastapi import APIRouter\nimport csv"
    )
    assert boundary_breaks(source) == set()


def _python_in(folder: Path) -> list[Path]:
    return [p for p in folder.rglob("*.py") if "node_modules" not in p.parts]


@pytest.mark.parametrize(
    "folder",
    [ROOT / "plugins", Path(__file__).parent / "plugin_fixtures"],
    ids=["first-party plugins", "the sample plugin"],
)
def test_no_plugin_imports_past_the_plugin_api(folder: Path) -> None:
    for path in _python_in(folder):
        breaks = boundary_breaks(path.read_text(encoding="utf-8"))
        assert not breaks, f"{path} imports {sorted(breaks)}"


def test_core_never_imports_a_plugin() -> None:
    for path in _python_in(ROOT / "backend" / "probeowl"):
        tops = {name.split(".")[0] for name in imports_of(path.read_text(encoding="utf-8"))}
        assert "plugins" not in tops, f"{path} imports a plugin by name"
