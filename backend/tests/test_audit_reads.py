# Author: @SaadZarook
"""Reading the audit (#79): GET /api/audit and owl audit, newest first, filtered, masked."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from probeowl.audit_reads import masked_details
from probeowl.cli import main as cli
from probeowl.cli import server
from probeowl.engine.masking import MASKED
from probeowl.model.run import Actor, ActorKind
from probeowl.store.audit import AuditEvent, append_event, list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.test_runs import finished, start

PERSON = Actor(kind=ActorKind.PERSON, name="s.khan")
AGENT = Actor(kind=ActorKind.AGENT, name="s.khan via MCP", on_behalf_of="s.khan")
EMAIL = "c.customer@example.com"


def add(setup: Setup, minutes: int, **event: Any) -> AuditEvent:
    """An event written straight to the store, minutes after a fixed start."""
    at = datetime(2026, 11, 15, 9, 0, tzinfo=UTC) + timedelta(minutes=minutes)
    found = AuditEvent(occurred_at=at, **({"actor": PERSON} | event))
    with setup.store.begin() as conn:
        append_event(conn, LOCAL_WORKSPACE, found)
    return found


def audit(api: TestClient, **params: Any) -> dict[str, Any]:
    response = api.get("/api/audit", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_audit_is_newest_first_and_says_who_did_what_where(setup: Setup) -> None:
    api = setup.client()
    finished(api, start(api))
    page = audit(api)
    actions = [e["action"] for e in page["events"]]
    assert actions[:2] == ["run_finished", "run_started"]
    first = page["events"][1]
    assert first["actor"]["name"] == "s.khan"
    assert (first["application"], first["environment"]) == ("wallet", "prod")
    assert first["details"]["reason"] == "incident"
    assert page["next"] is None


def test_each_filter_narrows_the_list(setup: Setup) -> None:
    add(setup, 1, action="kill_switch_on", application="wallet", environment="prod")
    add(setup, 2, action="secret_saved", application="card_access", environment="uat")
    add(setup, 3, action="mcp_tool_called", actor=AGENT, application="card_access")
    add(setup, 4, action="secret_saved", actor=Actor(kind=ActorKind.PERSON, name="a.other"))
    api = setup.client()

    def actions(**params: Any) -> list[str]:
        return [e["action"] for e in audit(api, **params)["events"]]

    assert actions(action="secret_saved") == ["secret_saved", "secret_saved"]
    assert actions(application="card_access") == ["mcp_tool_called", "secret_saved"]
    assert actions(environment="prod") == ["kill_switch_on"]
    # The person an agent acted for finds the agent's events too.
    assert actions(actor="s.khan") == ["mcp_tool_called", "secret_saved", "kill_switch_on"]
    assert actions(actor="a.other") == ["secret_saved"]
    assert actions(action="secret_saved", application="card_access") == ["secret_saved"]
    assert actions(action="nothing_like_this") == []


def test_pages_of_fifty_follow_on_with_next(setup: Setup) -> None:
    for minute in range(53):
        add(setup, minute, action="secret_saved", details={"n": minute})
    api = setup.client()
    first = audit(api)
    assert len(first["events"]) == 50 and first["next"] is not None
    assert first["events"][0]["details"]["n"] == 52  # newest first
    rest = audit(api, before=first["next"])
    assert [e["details"]["n"] for e in rest["events"]] == [2, 1, 0]
    assert rest["next"] is None


def test_a_cursor_this_list_never_gave_is_refused(setup: Setup) -> None:
    response = setup.client().get("/api/audit", params={"before": "yesterday|x"})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")


def test_values_that_are_pii_now_are_masked_when_read_never_changed_in_the_store(
    setup: Setup,
) -> None:
    # As v0.1 wrote them: the email, a PII identifier, in the clear. The audit keeps it.
    add(
        setup,
        1,
        action="run_started",
        application="card_access",
        environment="uat",
        details={"entity": "visitor", "identifiers": {"email": EMAIL}, "reason": "incident"},
    )
    page = audit(setup.client())
    [event] = page["events"]
    assert event["details"]["identifiers"] == {"email": MASKED}
    assert EMAIL not in json.dumps(page)
    with setup.store.connect() as conn:
        [stored] = list_events(conn, LOCAL_WORKSPACE)
    assert stored.details["identifiers"] == {"email": EMAIL}  # append-only: never rewritten


def test_an_identifier_that_is_not_pii_is_shown() -> None:
    from probeowl.model.check import load_applications
    from tests.conftest import EXAMPLES

    visitor = load_applications(EXAMPLES)["card_access"].entities["visitor"]
    details = masked_details({"identifiers": {"card_id": "C204518", "email": EMAIL}}, visitor)
    assert details["identifiers"] == {"card_id": "C204518", "email": MASKED}


def test_with_its_entity_gone_every_identifier_subject_and_match_is_masked() -> None:
    details = {"identifiers": {"x": "1"}, "subject": "VP1", "matches": ["a", "b"], "reason": "r"}
    assert masked_details(details, None) == {
        "identifiers": {"x": MASKED},
        "subject": MASKED,
        "matches": [MASKED, MASKED],
        "reason": "r",
    }


def test_reading_the_audit_writes_nothing(setup: Setup) -> None:
    add(setup, 1, action="secret_saved")
    api = setup.client()
    audit(api)
    audit(api, actor="s.khan")
    with setup.store.connect() as conn:
        assert len(list_events(conn, LOCAL_WORKSPACE)) == 1


# --- owl audit ----------------------------------------------------------------------------------


@pytest.fixture
def owl(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> Any:
    api = setup.client()
    monkeypatch.setattr(server, "client", lambda url: api)

    def run(*args: str) -> Any:
        return CliRunner().invoke(cli.app, ["audit", *args])

    return run


def test_owl_audit_prints_one_line_per_event_newest_first(owl: Any, setup: Setup) -> None:
    add(setup, 1, action="secret_saved", application="wallet", environment="uat")
    add(setup, 2, action="kill_switch_on", details={"reason": "replica on fire"})
    result = owl()
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    assert lines[0].startswith("2026-11-15 09:02:00 UTC  s.khan  kill_switch_on  -")
    assert "reason=replica on fire" in lines[0]
    assert lines[1].startswith("2026-11-15 09:01:00 UTC  s.khan  secret_saved  wallet/uat")


def test_owl_audit_filters_limits_and_writes_json(owl: Any, setup: Setup) -> None:
    for minute in range(60):
        add(setup, minute, action="secret_saved", application="wallet", details={"n": minute})
    add(setup, 61, action="kill_switch_on")
    found = owl("--action", "secret_saved", "--limit", "55", "--json")
    assert found.exit_code == 0, found.output
    events = json.loads(found.output)
    assert len(events) == 55  # across two pages
    assert {e["action"] for e in events} == {"secret_saved"}
    assert events[0]["details"]["n"] == 59


def test_owl_audit_says_when_nothing_matches(owl: Any) -> None:
    result = owl("--actor", "nobody")
    assert result.exit_code == 0
    assert "No audit events match." in result.output
