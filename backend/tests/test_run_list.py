# Author: @SaadZarook
"""GET /api/runs: recent runs, newest first, as the Runs screen lists them."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from probeowl import runs as run_service
from probeowl.model import Run
from probeowl.store.runs import save_run
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.test_read_routes import binding_values
from tests.test_runs import finished, start

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


def stored(setup: Setup, run_id: str, minutes: int, **changes: Any) -> None:
    run = Run.model_validate(
        {
            "id": run_id,
            "application": "wallet",
            "environment": "uat",
            "probe": "topup_completed",
            "kind": "validate",
            "probe_version": "sha256:abc",
            "entity": "topup",
            "identifier": "topup_id",
            "identifier_value": "tp_91f2c7",
            "subject": "tp_91f2c7",
            "status": "failed",
            "actor": {"kind": "person", "name": "s.khan"},
            "investigation": {"reason": "incident", "reference": "WALLET-482"},
            "started_at": T0 + timedelta(minutes=minutes),
            "finished_at": T0 + timedelta(minutes=minutes, seconds=2),
        }
        | changes
    )
    with setup.store.begin() as conn:
        save_run(conn, LOCAL_WORKSPACE, run)


def listed(setup: Setup, **params: str) -> dict[str, Any]:
    response = setup.client().get(
        "/api/runs", params={"application": "wallet", "environment": "uat", **params}
    )
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def test_runs_are_listed_newest_first_with_what_the_list_shows(setup: Setup) -> None:
    stored(setup, "older", 0)
    stored(setup, "newer", 5, status="passed", subject="tp_91f2b9", identifier_value="tp_91f2b9")
    stored(setup, "in-prod", 9, environment="prod")
    page = listed(setup)
    assert [run["id"] for run in page["runs"]] == ["newer", "older"]
    assert page["next"] is None
    [newer, _] = page["runs"]
    assert newer["probe_title"] == "Top-up completed"
    assert (newer["status"], newer["subject"], newer["draft"]) == ("passed", "tp_91f2b9", False)
    assert (newer["actor"], newer["reason"], newer["reference"]) == (
        "s.khan",
        "incident",
        "WALLET-482",
    )


def test_a_drafts_test_run_is_marked_and_healthy_candidates_are_left_out(setup: Setup) -> None:
    stored(setup, "failing", 0)
    stored(setup, "candidate", 1, status="passed", healthy_for="failing")
    stored(setup, "draft", 2, probe_version="draft:sha256:123")
    page = listed(setup)
    assert [(run["id"], run["draft"]) for run in page["runs"]] == [
        ("draft", True),
        ("failing", False),
    ]


def test_a_stopped_run_carries_its_reason_and_a_caveat_is_kept(setup: Setup) -> None:
    stored(setup, "stopped", 0, stop_reason="stopped by the global kill switch: replica on fire")
    stored(
        setup, "late", 1, caveat="replica lag unknown: 1 Missing result may not have arrived yet"
    )
    page = {run["id"]: run for run in listed(setup)["runs"]}
    assert page["stopped"]["stop_reason"] == "stopped by the global kill switch: replica on fire"
    assert page["late"]["caveat"].startswith("replica lag unknown")


def test_older_runs_come_page_by_page(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_service, "RUNS_PER_PAGE", 2)
    for minute in range(5):
        stored(setup, f"run-{minute}", minute)
    # Two runs started together are ordered by id, so none is skipped or repeated.
    stored(setup, "run-2b", 2)
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = listed(setup, **({"before": cursor} if cursor else {}))
        seen += [run["id"] for run in page["runs"]]
        cursor = page["next"]
        if cursor is None:
            break
    assert seen == ["run-4", "run-3", "run-2b", "run-2", "run-1", "run-0"]


def test_an_unknown_environment_is_404_and_a_bad_cursor_422(setup: Setup) -> None:
    client = setup.client()
    missing = client.get("/api/runs", params={"application": "wallet", "environment": "staging"})
    assert missing.status_code == 404
    bad = client.get(
        "/api/runs", params={"application": "wallet", "environment": "uat", "before": "nope"}
    )
    assert bad.status_code == 422


def test_masked_subjects_stay_masked_and_no_binding_leaks(setup: Setup) -> None:
    client = setup.client()
    email = "a.rahman@example.com"
    body = {
        "application": "card_access",
        "environment": "prod",
        "probe": "visitor_cards",
        "value": email,
        "investigation": {"reason": "incident", "reference": "SUP-20931"},
    }
    response = client.post("/api/runs", json=body)
    assert response.status_code == 202, response.text
    finished(client, response.json()["id"])
    finished(client, start(client, value="tp_91f2c7"))
    secrets = binding_values(setup.applications)
    for application, environment in (("card_access", "prod"), ("wallet", "prod")):
        page = client.get(
            "/api/runs", params={"application": application, "environment": environment}
        )
        assert page.status_code == 200
        assert email not in page.text
        leaked = {value for value in secrets if value in page.text}
        assert not leaked, f"{application}/{environment} returned {sorted(leaked)}"
    [by_email] = client.get(
        "/api/runs", params={"application": "card_access", "environment": "prod"}
    ).json()["runs"]
    assert (by_email["identifier"], by_email["identifier_value"]) == ("email", "[masked]")
