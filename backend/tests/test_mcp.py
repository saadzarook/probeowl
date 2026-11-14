# Author: @SaadZarook
"""owl mcp's fixed rules (#39), one section each: drafts only; no secrets; no data; stdio only and
audited; the official SDK, bounded, with tools held to contracts/mcp-tools.json."""

import asyncio
import json
import shutil
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from probeowl.drafting import server as mcp_server
from probeowl.drafting.contract import CONTRACT_PATH, build_contract
from probeowl.drafting.scan import scan
from probeowl.drafting.server import create_server
from probeowl.model import Run
from probeowl.model.check import LoadedApplication, load_applications
from probeowl.model.schema import ColumnSchema, SchemaSnapshot, TableSchema
from probeowl.store.audit import list_events
from probeowl.store.engine import create_store_engine
from probeowl.store.migrate import upgrade
from probeowl.store.runs import save_run
from probeowl.store.schemas import save_snapshot
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import EXAMPLES

# Not real: values to look for in every output, the log and the audit.
PASSWORD = "mcp-test-password-5e1b"
HOST = "db-prod-17.corp.example"
LOGIN = "svc_probeowl_ro"
BASE_URL = "https://payments.internal.example/v2"
ENV_VALUE = "env-value-9f02c4"
VISITOR_SUBJECT = "VP00055501"  # in no example: only in a stored run

PROBE_DRAFT = """probe: visitor_active_cards
title: A visitor's active cards
kind: validate
entity: visitor
owner: access-team
steps:
  - expect: count(cards where status == 'active') <= 1
"""


class Mcp:
    """The server over a copy of the examples whose connectors hold real-looking values."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plans: Any = None):
        self.folder = tmp_path / "config"
        shutil.copytree(EXAMPLES, self.folder)
        self._realistic(monkeypatch)
        self.applications: dict[str, LoadedApplication] = load_applications(self.folder)
        self.store: Engine = create_store_engine(f"sqlite:///{tmp_path / 'store.db'}")
        upgrade(self.store)
        self.server = create_server(self.applications, self.store, str(self.folder), plans=plans)

    def _realistic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for path in (self.folder / "wallet" / "connectors").glob("*.yaml"):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            for binding in data["environments"].values():
                if "host" in binding:
                    binding.update(host=HOST, user=LOGIN, secret="env:WALLET_DB_PW")
                if "base_url" in binding:
                    binding["base_url"] = BASE_URL
            path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        monkeypatch.setenv("WALLET_DB_PW", ENV_VALUE)

    def call(self, tool: str, **arguments: Any) -> Any:
        result = asyncio.run(self.server.call_tool(tool, arguments))
        assert not getattr(result, "is_error", False), result
        return result

    def everything(self) -> list[Any]:
        """Every tool, every kind: what an assistant could ever be given."""
        results = []
        for kind in ["entity", "probe", "connector", "catalog", "runbook"]:
            results += [
                self.call("drafting_guide", kind=kind),
                self.call("template", kind=kind),
                self.call("examples", kind=kind),
            ]
        results += [
            self.call("list_config"),
            self.call("schema", application="wallet", connector="wallet_db", environment="uat"),
            self.call(
                "schema", application="card_access", connector="access_db", environment="uat"
            ),
            self.call("check_draft", kind="probe", text=PROBE_DRAFT, application="card_access"),
        ]
        return results

    def events(self) -> list[Any]:
        with self.store.connect() as conn:
            return list(list_events(conn, LOCAL_WORKSPACE))


@pytest.fixture
def mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Mcp]:
    found = Mcp(tmp_path, monkeypatch)
    yield found
    found.store.dispose()


def text(results: list[Any]) -> str:
    return json.dumps([r.model_dump(mode="json") for r in results])


def files(folder: Path) -> dict[str, tuple[int, int]]:
    return {
        str(p): (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.rglob("*") if p.is_file()
    }


# --- 1. Drafts only ---------------------------------------------------------------------------


def test_no_tool_writes_a_file_runs_anything_or_reaches_a_connector(
    mcp: Mcp, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*_: Any, **__: Any) -> None:
        raise AssertionError("a tool reached a connector")

    for target in [
        "probeowl.connectors.factory.open_sql",
        "probeowl.connectors.factory.open_connector",
        "probeowl.checks.plans.open_for_plans",
    ]:
        monkeypatch.setattr(target, never)
    before = files(mcp.folder)
    mcp.everything()
    assert files(mcp.folder) == before  # nothing written or changed in the config folder
    with mcp.store.connect() as conn:
        for table in ["runs", "evidence", "catalog_tables", "schema_snapshots", "kill_switches"]:
            assert conn.exec_driver_sql(f"SELECT count(*) FROM {table}").scalar() == 0, table
    # Only the audit grows: one event per call, and nothing else in the store.
    assert {e.action for e in mcp.events()} == {"mcp_tool_called"}


def test_check_draft_returns_findings_in_the_save_checks_words(mcp: Mcp) -> None:
    passing = mcp.call("check_draft", kind="probe", text=PROBE_DRAFT, application="card_access")
    assert passing.structured_content["ok"] is True
    # In the application's own folder, card-access, not its id.
    assert passing.structured_content["paste_into"].endswith(
        "card-access/probes/visitor_active_cards.yaml"
    )
    wrong = PROBE_DRAFT.replace("count(cards", "count(nowhere")
    failing = mcp.call("check_draft", kind="probe", text=wrong, application="card_access")
    assert failing.structured_content["ok"] is False
    assert any("nowhere" in f["message"] for f in failing.structured_content["findings"])


def test_plan_checks_run_only_with_plan_checks_and_return_pass_or_fail_never_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    def plans(connector: Any, environment: str) -> None:
        opened.append(f"{connector.connector}/{environment}")
        return None  # the guarded reader's answer when a plan cannot be read: it warns

    without = Mcp(tmp_path / "a", monkeypatch)
    without.call("check_draft", kind="probe", text=PROBE_DRAFT, application="card_access")
    assert opened == []
    with_checks = Mcp(tmp_path / "b", monkeypatch, plans=plans)
    result = with_checks.call(
        "check_draft", kind="probe", text=PROBE_DRAFT, application="card_access"
    )
    assert opened  # only now, and through the same plan opener saving uses
    findings = result.structured_content["findings"]
    assert all(set(f) <= {"check", "severity", "message", "line"} for f in findings)
    assert "rows" not in json.dumps(result.structured_content)


# --- 2. No secrets, ever -----------------------------------------------------------------------


def test_no_output_holds_a_host_login_url_keychain_name_or_variable_value(mcp: Mcp) -> None:
    out = text(mcp.everything())
    for value in [HOST, LOGIN, BASE_URL, ENV_VALUE, "127.0.0.1", "WALLET_DB_PW"]:
        assert value not in out, value
    assert "keyring:probeowl/wallet_db" not in out and "keyring:probeowl/access_db" not in out
    assert "keyring:probeowl/<connector>.<env>" in out  # the form, in templates and examples


def test_a_draft_with_a_secret_is_flagged_and_never_echoed(mcp: Mcp, tmp_path: Path) -> None:
    draft = f"""connector: wallet_db
type: sqlserver
owner: payments-team
environments:
  uat:
    host: {HOST}
    port: 1433
    database: wallet
    user: {LOGIN}
    password: {PASSWORD}
    secret: Server={HOST};User Id={LOGIN};Password={PASSWORD}
"""
    result = mcp.call("check_draft", kind="connector", text=draft, application="wallet")
    found = result.structured_content
    assert found["ok"] is False
    assert {c["what"] for c in found["credentials"]} >= {"password", "host"}
    assert found["next"].startswith("Remove this: passwords go in the keychain")
    out = json.dumps(found) + json.dumps([c.model_dump() for c in result.content])
    for value in [PASSWORD, HOST]:
        assert value not in out
    log = Path.home() / ".probeowl" / "owl-mcp.log"
    assert PASSWORD not in (log.read_text(encoding="utf-8") if log.exists() else "")
    assert PASSWORD not in str(mcp.events()) and HOST not in str(mcp.events())


def test_every_template_and_example_passes_the_credential_scan(mcp: Mcp) -> None:
    for kind in ["entity", "probe", "connector", "catalog", "runbook"]:
        yamls = [mcp.call("template", kind=kind).structured_content["yaml"]]
        yamls += [e["yaml"] for e in mcp.call("examples", kind=kind).structured_content["items"]]
        for each in yamls:
            assert scan(each) == [], (kind, [f.shown() for f in scan(each)])


# --- 3. No data ------------------------------------------------------------------------------


def test_schema_and_list_config_give_names_types_labels_and_roles_only(mcp: Mcp) -> None:
    snapshot = SchemaSnapshot(
        application="wallet",
        connector="wallet_db",
        environment="uat",
        imported_at="2026-11-01T00:00:00Z",
        tables=[
            TableSchema(
                table="topups",
                primary_key=["id"],
                columns=[
                    ColumnSchema(name="id", type="varchar(20)", nullable=False),
                    ColumnSchema(name="status", type="varchar(16)", nullable=False),
                    ColumnSchema(name="customer_email", type="varchar(320)", nullable=True),
                ],
            )
        ],
    )
    with mcp.store.begin() as conn:
        save_snapshot(conn, LOCAL_WORKSPACE, snapshot)
    found = mcp.call("schema", application="wallet", connector="wallet_db", environment="uat")
    [table] = found.structured_content["tables"]
    assert table["label"] == "transaction"
    columns = {c["name"]: c for c in table["columns"]}
    assert columns["status"]["roles"] == ["status", "compare"]
    # catalog.yaml lists the status values (pending, succeeded, failed): never returned.
    assert "succeeded" not in json.dumps(found.structured_content)
    config = mcp.call("list_config", application="card_access").structured_content
    [visitor] = config["applications"][0]["entities"]
    assert visitor["pii_identifiers"] == ["email"]  # named as PII, with no value


def test_no_tool_reads_a_run_subject_or_evidence(mcp: Mcp) -> None:
    run = Run.model_validate(
        {
            "id": "run-1",
            "application": "card_access",
            "environment": "uat",
            "probe": "visitor_cards",
            "kind": "inspect",
            "probe_version": "sha256:abc",
            "entity": "visitor",
            "identifier": "visitor_profile_id",
            "identifier_value": VISITOR_SUBJECT,
            "subject": VISITOR_SUBJECT,
            "status": "failed",
            "actor": {"kind": "person", "name": "s.khan"},
            "investigation": {"reason": "incident", "reference": "SUP-1"},
            "started_at": "2026-11-01T09:00:00Z",
            "finished_at": "2026-11-01T09:00:02Z",
        }
    )
    with mcp.store.begin() as conn:
        save_run(conn, LOCAL_WORKSPACE, run)
    assert VISITOR_SUBJECT not in text(mcp.everything())


# --- 4. stdio only, and every call audited ----------------------------------------------------


def test_owl_mcp_serves_on_stdio_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called: list[str] = []

    async def stdio(self: Any) -> None:
        called.append("stdio")

    def network(self: Any, *_: Any, **__: Any) -> None:
        raise AssertionError("owl mcp opened a network listener")

    monkeypatch.setattr(mcp_server.MCPServer, "run_stdio_async", stdio)
    for name in ["run_sse_async", "run_streamable_http_async"]:
        monkeypatch.setattr(mcp_server.MCPServer, name, network)
    from typer.testing import CliRunner

    from probeowl.cli import main as cli

    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    url = f"sqlite:///{tmp_path / 'store.db'}"
    assert CliRunner().invoke(cli.app, ["store", "init", "--url", url]).exit_code == 0
    result = CliRunner().invoke(cli.app, ["mcp", "--config", str(EXAMPLES), "--url", url])
    assert result.exit_code == 0, result.output
    assert called == ["stdio"]
    help_text = CliRunner().invoke(cli.app, ["mcp", "--help"]).output
    assert "--port" not in help_text and "--host" not in help_text


def test_every_call_is_audited_via_mcp_with_the_tool_never_its_arguments(mcp: Mcp) -> None:
    mcp.call("check_draft", kind="probe", text=PROBE_DRAFT, application="card_access")
    mcp.call("list_config")
    events = mcp.events()
    assert [e.action for e in events] == ["mcp_tool_called", "mcp_tool_called"]
    assert events[0].actor.kind == "agent" and events[0].actor.name.endswith(" via MCP")
    assert events[0].details == {
        "tool": "check_draft",
        "kind": "probe",
        "application": "card_access",
        "ok": True,
    }
    assert events[1].details == {
        "tool": "list_config",
        "kind": None,
        "application": None,
        "ok": True,
    }
    assert "visitor_active_cards" not in str(events)  # the draft's text, never kept


# --- 5. The official SDK, bounded; the tools held to their contract ---------------------------


def test_the_mcp_sdk_is_a_dependency_with_an_upper_bound() -> None:
    project = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text("utf-8"))
    [dependency] = [d for d in project["project"]["dependencies"] if d.startswith("mcp")]
    assert dependency == "mcp>=2.3,<3"


def test_the_servers_tools_match_contracts_mcp_tools_json_exactly() -> None:
    assert CONTRACT_PATH.read_text(encoding="utf-8") == build_contract(), (
        f"{CONTRACT_PATH} is out of date; run: uv run python -m probeowl.drafting.contract "
        "and open a contract PR"
    )


def test_every_tool_is_read_only_and_closed_world() -> None:
    tools = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["tools"]
    assert [t["name"] for t in tools] == [
        "check_draft",
        "drafting_guide",
        "examples",
        "list_config",
        "schema",
        "template",
    ]
    for tool in tools:
        assert tool["annotations"]["read_only_hint"] is True, tool["name"]
        assert tool["annotations"]["open_world_hint"] is False, tool["name"]
        assert tool["output_schema"], tool["name"]
