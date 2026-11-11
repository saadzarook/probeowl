# Author: @SaadZarook
"""`owl inspect` end to end: the CLI, the real API and store, fake card access connectors."""

import re
from typing import Any

import pytest
from typer.testing import CliRunner

from probeowl.cli import main as cli
from probeowl.cli import server
from probeowl.connectors.lookup import ReplicaLag
from tests.conftest import Setup

runner = CliRunner()
PROD = ["--env", "prod", "--reason", "customer_complaint", "--reference", "SUP-20931"]


@pytest.fixture
def api(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> Setup:
    """owl inspect talks to the real API, in this process, instead of a server."""
    client = setup.client()
    monkeypatch.setattr(server, "client", lambda url: client)
    return setup


def owl(*args: str) -> Any:
    return runner.invoke(cli.app, ["inspect", *args])


def test_c204518_shows_its_visitors_three_card_issues(api: Setup) -> None:
    result = owl("C204518", *PROD)
    assert result.exit_code == 1, result.output
    out = result.output
    assert re.search(
        r"^Run [0-9a-f-]{36}: Visitor cards \(card_access/visitor_cards\) on C204518 in prod$",
        out,
        re.M,
    )
    assert "Resolved card_id C204518 -> visitor_profile_id VP00012931 via cards.card_id" in out
    assert re.search(r"^  cards\s+Found$", out, re.M)
    for row in (1, 2, 3):
        assert re.search(rf"^  controller\[cards {row}\]\s+Found$", out, re.M)
    assert "  fail count(cards where status == 'active') <= 1  (2 <= 1)" in out
    assert (
        "  fail all(cards where status == 'active', valid_to > now())  (fails for C198220)" in out
    )
    assert "  fail all(cards, controller.status == status)  (fails for C177031)" in out
    assert re.search(r"^Run [0-9a-f-]{36}: failed$", out, re.M)


def test_the_shared_email_is_ambiguous_with_both_profiles(api: Setup) -> None:
    result = owl("contractor.one@example.com", *PROD)
    assert result.exit_code == 3, result.output
    out = result.output
    assert "email [masked] matches 2 subjects via profile.email; nothing else ran:" in out
    assert re.search(r"^  VP00012931  .*status active", out, re.M)
    assert re.search(r"^  VP00008807  .*status inactive", out, re.M)
    assert "contractor.one@example.com" not in out  # not even echoed back
    assert re.search(
        r"^Run [0-9a-f-]{36}: Visitor cards \(card_access/visitor_cards\) on \[masked\] in prod$",
        out,
        re.M,
    )
    assert re.search(r"^Run [0-9a-f-]{36}: ambiguous$", out, re.M)
    assert not api.fakes["door_controller_api"].calls


def test_an_identifier_without_a_pattern_is_chosen(api: Setup) -> None:
    result = owl("EMP-100231", *PROD, "--identifier", "employee_no")
    # Both seeded profiles share the employee number too.
    assert result.exit_code == 3, result.output
    assert "employee_no EMP-100231 matches 2 subjects via profile.employee_no" in result.output


def test_a_value_no_inspect_probe_accepts_is_not_run(api: Setup) -> None:
    result = owl("tp_91f2c7", *PROD)
    assert result.exit_code == 2
    assert "no inspect probe whose identifiers accept this value is enabled in prod" in (
        result.output
    )


def test_production_without_a_reference_is_refused(api: Setup) -> None:
    result = owl("C204518", "--env", "prod", "--reason", "incident")
    assert result.exit_code == 2
    assert "investigation.reference: required when the environment is Production" in result.output
    assert not api.fakes["access_db"].calls


def test_an_unreachable_server_says_how_to_start_it() -> None:
    result = owl("C204518", *PROD, "--server", "http://127.0.0.1:9")
    assert result.exit_code == 2
    assert "cannot reach owl serve at http://127.0.0.1:9" in result.output


def test_run_warnings_are_printed_before_the_status(
    api: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unknown() -> ReplicaLag:
        return ReplicaLag(None, "replica lag unknown: x", "missing REPLICA MONITOR")

    monkeypatch.setattr(api.fakes["access_db"], "replica_lag", unknown, raising=False)
    out = owl("C204518", *PROD).output
    warning = (
        "warning: Replica lag unknown for access_db (missing REPLICA MONITOR): "
        "Missing results can't be confirmed fresh"
    )
    assert warning in out
    assert out.index(warning) < out.index(": failed")
