# Author: @SaadZarook
"""Inspect runs on the card access example through the API, against fake connectors."""

from typing import Any

from tests.conftest import Setup
from tests.test_runs import finished, start


def inspect(setup: Setup, value: str, **changes: Any) -> dict[str, Any]:
    client = setup.client()
    run_id = start(
        client,
        application="card_access",
        probe="visitor_cards",
        value=value,
        **changes,
    )
    return finished(client, run_id)


def results(run: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    return {e["expectation"]: (e["result"], e["detail"]) for e in run["expectations"]}


def test_inspecting_card_c204518_shows_its_visitors_three_card_issues(setup: Setup) -> None:
    run = inspect(setup, "C204518")
    assert run["kind"] == "inspect"
    assert (run["identifier"], run["via"], run["subject"]) == (
        "card_id",
        "cards.card_id",
        "VP00012931",
    )
    assert run["status"] == "failed"
    assert results(run) == {
        "count(cards where status == 'active') <= 1": ("fail", "2 <= 1"),
        "all(cards where status == 'active', valid_to > now())": ("fail", "fails for C198220"),
        "all(cards, controller.status == status)": ("fail", "fails for C177031"),
    }
    controller = [r for r in run["locations"] if r["location"] == "controller"]
    assert [(r["parent"], r["row"], r["outcome"]) for r in controller] == [
        ("cards", 0, "found"),
        ("cards", 1, "found"),
        ("cards", 2, "found"),
    ]


def test_the_email_two_profiles_share_is_ambiguous_with_both(setup: Setup) -> None:
    run = inspect(setup, "contractor.one@example.com")
    assert run["status"] == "ambiguous"
    assert run["identifier"] == "email"
    assert run["identifier_value"] == "[masked]"  # email is a PII identifier
    assert sorted(m["subject"] for m in run["matches"]) == ["VP00008807", "VP00012931"]
    assert all(m["fields"]["email"] == "[masked]" for m in run["matches"])
    assert run["locations"] == []
    assert "contractor.one@example.com" not in str(run)
    assert not setup.fakes["door_controller_api"].calls
