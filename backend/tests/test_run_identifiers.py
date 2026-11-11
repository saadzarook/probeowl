# Author: @SaadZarook
"""Runs started from any identifier: detected, looked up, and Ambiguous when several match."""

from typing import Any

import pytest

from probeowl.errors import ConnectorError
from probeowl.store.audit import list_events
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.test_runs import finished, outcome, start
from tests.wallet_fakes import TOPUPS


def test_a_provider_payment_id_is_detected_and_resolved_to_the_top_up(setup: Setup) -> None:
    client = setup.client()
    run = finished(client, start(client, value="pay_7hq2m9"))
    assert run["identifier"] == "provider_payment_id"
    assert run["identifier_value"] == "pay_7hq2m9"
    assert run["via"] == "record.provider_payment_id"
    assert run["subject"] == "tp_91f2c7"
    assert run["matches"][0]["subject"] == "tp_91f2c7"
    resolution = run["resolution"]
    assert (resolution["location"], resolution["outcome"]) == ("record", "found")
    assert resolution["evidence"]["parameters"] == {"provider_payment_id": "pay_7hq2m9"}
    # The record location's lookup, with its root key condition swapped for the identifier's.
    lookup, parameters = setup.fakes["wallet_db"].calls[0]
    assert "provider_payment_id = :provider_payment_id" in lookup
    assert ":topup_id" not in lookup
    assert parameters == {"provider_payment_id": "pay_7hq2m9"}
    # Then the probe ran on the subject as if started from its root key.
    assert run["status"] == "failed"
    assert outcome(run, "ledger")["outcome"] == "missing"


def test_an_identifier_can_be_chosen_instead_of_detected(setup: Setup) -> None:
    client = setup.client()
    run = finished(client, start(client, value="lt_55e19f", identifier="ledger_txn_id"))
    assert run["subject"] == "tp_91f2b9"
    assert run["status"] == "passed"


def test_an_identifier_that_finds_nothing_fails_the_run_with_nothing_else_read(
    setup: Setup,
) -> None:
    client = setup.client()
    run = finished(client, start(client, value="pay_000000"))
    assert run["status"] == "failed"
    assert run["subject"] is None
    assert run["resolution"]["outcome"] == "missing"
    assert run["stop_reason"] == "no topup has this provider_payment_id"
    assert run["locations"] == [] and run["expectations"] == []
    assert len(setup.fakes["wallet_db"].calls) == 1
    assert not setup.fakes["ledger_db"].calls and not setup.fakes["checkout_api"].calls


def test_an_identifier_that_cannot_be_looked_up_fails_the_run(setup: Setup) -> None:
    def broken(lookup: str, parameters: Any) -> list[dict[str, Any]]:
        raise ConnectorError("the statement timed out after 2 s")

    setup.fakes["wallet_db"].answer = broken
    client = setup.client()
    run = finished(client, start(client, value="pay_7hq2m9"))
    assert run["status"] == "failed"
    assert run["stop_reason"] == "the identifier could not be looked up"
    assert run["resolution"]["outcome"] == "error"
    assert run["resolution"]["reason"] == "the statement timed out after 2 s"


# --- Ambiguous ------------------------------------------------------------------------------


@pytest.fixture
def shared_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    # A second top-up carrying the same provider payment ID: one identifier, two subjects.
    monkeypatch.setitem(TOPUPS, "tp_91f2d0", TOPUPS["tp_91f2c7"] | {"ledger": 2})


@pytest.mark.usefixtures("shared_payment")
def test_several_subjects_make_the_run_ambiguous_and_nothing_else_runs(setup: Setup) -> None:
    client = setup.client()
    run = finished(client, start(client, value="pay_7hq2m9"))
    assert run["status"] == "ambiguous"
    assert run["subject"] is None
    assert [m["subject"] for m in run["matches"]] == ["tp_91f2c7", "tp_91f2d0"]
    assert run["resolution"]["outcome"] == "ambiguous"
    assert run["resolution"]["reason"] == "provider_payment_id matches 2 subjects"
    # Each match carries its record, masked like the location.
    assert run["matches"][0]["fields"]["customer_email"] == "[masked]"
    assert run["matches"][0]["fields"]["amount"] == "500.00"
    assert run["locations"] == [] and run["expectations"] == []
    assert len(setup.fakes["wallet_db"].calls) == 1
    assert not setup.fakes["ledger_db"].calls and not setup.fakes["checkout_api"].calls
    assert "c.customer@example.com" not in str(run)


@pytest.mark.usefixtures("shared_payment")
def test_picked_matches_run_with_the_ambiguous_runs_record(setup: Setup) -> None:
    client = setup.client()
    ambiguous = finished(client, start(client, value="pay_7hq2m9"))
    response = client.post(
        f"/api/runs/{ambiguous['id']}/matches", json={"subjects": ["tp_91f2d0", "tp_91f2c7"]}
    )
    assert response.status_code == 202
    started = response.json()["runs"]
    assert [r["status"] for r in started] == ["running", "running"]
    runs = [finished(client, r["id"]) for r in started]
    assert [r["subject"] for r in runs] == ["tp_91f2d0", "tp_91f2c7"]
    # Each ran the whole probe from its root key, like a run started from it.
    assert all(outcome(run, "ledger")["outcome"] == "missing" for run in runs)
    for run in runs:
        assert run["from_run"] == ambiguous["id"]
        assert run["identifier"] == "topup_id"
        assert run["investigation"]["reference"] == "WALLET-482"
    with setup.store.connect() as conn:
        [started_event, _] = list_events(conn, LOCAL_WORKSPACE, runs[0]["id"])
    assert started_event.details["from_run"] == ambiguous["id"]


@pytest.mark.usefixtures("shared_payment")
def test_picked_matches_count_as_one_run_toward_the_limit(setup: Setup) -> None:
    client = setup.client(max_concurrent=1)
    ambiguous = finished(client, start(client, value="pay_7hq2m9"))
    response = client.post(
        f"/api/runs/{ambiguous['id']}/matches", json={"subjects": ["tp_91f2c7", "tp_91f2d0"]}
    )
    assert response.status_code == 202
    for started in response.json()["runs"]:
        finished(client, started["id"])


@pytest.mark.usefixtures("shared_payment")
@pytest.mark.parametrize(
    ("subjects", "status"),
    [
        (["tp_91f2b9"], 422),  # not one of the matches
        (["tp_91f2c7", "tp_91f2c7"], 422),  # picked twice
        ([], 422),
        (["tp_91f2c7"] * 6, 422),  # more than 5
    ],
)
def test_only_the_runs_own_matches_can_be_picked(
    setup: Setup, subjects: list[str], status: int
) -> None:
    client = setup.client()
    ambiguous = finished(client, start(client, value="pay_7hq2m9"))
    before = len(setup.fakes["wallet_db"].calls)
    response = client.post(f"/api/runs/{ambiguous['id']}/matches", json={"subjects": subjects})
    assert response.status_code == status
    assert len(setup.fakes["wallet_db"].calls) == before


def test_matches_of_a_run_that_is_not_ambiguous_cannot_be_picked(setup: Setup) -> None:
    client = setup.client()
    run = finished(client, start(client))
    response = client.post(f"/api/runs/{run['id']}/matches", json={"subjects": ["tp_91f2b9"]})
    assert response.status_code == 422
    assert "not Ambiguous" in response.json()["detail"]


def test_matches_of_an_unknown_run_is_a_404(setup: Setup) -> None:
    response = setup.client().post("/api/runs/nope/matches", json={"subjects": ["tp_91f2b9"]})
    assert response.status_code == 404


def test_a_pii_identifier_is_stored_masked(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    entity = setup.applications["wallet"].entities["topup"]
    identifier = entity.identifiers["provider_payment_id"]
    monkeypatch.setitem(
        entity.identifiers, "provider_payment_id", identifier.model_copy(update={"pii": True})
    )
    client = setup.client()
    run_id = start(client, value="pay_7hq2m9")
    run = finished(client, run_id)
    assert run["identifier_value"] == "[masked]"
    assert run["resolution"]["evidence"]["parameters"] == {"provider_payment_id": "[masked]"}
    with setup.store.connect() as conn:
        [started_event, _] = list_events(conn, LOCAL_WORKSPACE, run_id)
    assert started_event.details["identifiers"] == {"provider_payment_id": "[masked]"}
