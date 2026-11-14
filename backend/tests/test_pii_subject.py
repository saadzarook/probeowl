# Author: @SaadZarook
"""An entity whose root key is PII (#66): its subject is stored and shown masked everywhere, and
what needs the real value later asks for it to be pasted, never storing or returning it."""

import shutil
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from probeowl.cli import main as cli
from probeowl.cli import server
from probeowl.runs import PASTED_IN_PRODUCTION
from probeowl.store.audit import list_events
from probeowl.store.runs import get_run
from probeowl.store.tables import LOCAL_WORKSPACE
from probeowl.stored_masking import mask_stored_runs
from tests.conftest import EXAMPLES, Setup
from tests.test_run_list import stored
from tests.test_run_steps import PROBE, REVIEWED_READ, outbox, step_url
from tests.test_runs import finished, start

TOPUP = "tp_91f2c7"
PROD_RECORD = ["--reason", "incident", "--reference", "WALLET-482"]
REASON = "topup's key is PII and stored masked: paste its topup_id"


def key_is_pii(setup: Setup, application: str = "wallet", entity: str = "topup") -> None:
    """Mark the entity's root key PII in the loaded configuration, as a team could."""
    app = setup.applications[application]
    found = app.entities[entity]
    identifiers = dict(found.identifiers)
    identifiers[found.root_key] = identifiers[found.root_key].model_copy(update={"pii": True})
    app.entities[entity] = found.model_copy(update={"identifiers": identifiers})


def a_run(api: TestClient) -> dict[str, Any]:
    return finished(api, start(api, value=TOPUP))


def everything_stored(setup: Setup, but_the_audit: bool = False) -> str:
    """Every row of the store, as text: what search, exports and the CLI could ever read."""
    with setup.store.connect() as conn:
        tables = ["runs", "location_results", "evidence", "expectation_results", "runbook_steps"]
        if not but_the_audit:
            tables.append("audit_events")
        return str([list(conn.exec_driver_sql(f"SELECT * FROM {table}")) for table in tables])


# --- Stored and shown masked ------------------------------------------------------------------


def test_the_subject_is_masked_in_the_run_its_evidence_the_list_and_the_store(
    setup: Setup,
) -> None:
    key_is_pii(setup)
    api = setup.client()
    run = a_run(api)
    assert run["status"] == "failed"  # the run itself had the real value, in memory
    assert run["subject"] == "[masked]"
    assert run["identifier_value"] == "[masked]"
    for result in run["locations"]:
        if result.get("evidence"):
            assert result["evidence"]["parameters"].get("topup_id") in (None, "[masked]")
            assert all(
                row.get("topup_id") in (None, "[masked]") for row in result["evidence"]["rows"]
            )
    assert TOPUP not in str(run)
    listed = api.get("/api/runs", params={"application": "wallet", "environment": "prod"}).json()
    assert [r["subject"] for r in listed["runs"]] == ["[masked]"]
    assert TOPUP not in everything_stored(setup)


def test_the_audit_never_holds_it(setup: Setup) -> None:
    key_is_pii(setup)
    a_run(setup.client())
    with setup.store.connect() as conn:
        events = list_events(conn, LOCAL_WORKSPACE)
    assert {e.action for e in events} >= {"run_started", "run_finished"}
    assert TOPUP not in str(events)


def test_a_key_that_is_not_pii_is_stored_as_before(setup: Setup) -> None:
    run = a_run(setup.client())
    assert run["subject"] == TOPUP


# --- Runbook steps ask for it ------------------------------------------------------------------


def test_a_read_step_asks_for_the_subject_and_never_keeps_it(setup: Setup) -> None:
    key_is_pii(setup)
    outbox(setup)
    api = setup.client()
    run_id = a_run(api)["id"]
    refused = api.post(step_url(run_id, REVIEWED_READ), json={})
    assert refused.status_code == 422
    assert refused.json()["detail"] == REASON
    ran = api.post(step_url(run_id, REVIEWED_READ), json={"subject": TOPUP})
    assert ran.status_code == 200, ran.text
    assert ran.json()["result"]["evidence"]["parameters"] == {"topup_id": "[masked]"}
    [(_, parameters)] = [c for c in setup.fakes["wallet_db"].calls if "outbox_events" in c[0]]
    assert parameters == {"topup_id": TOPUP}  # the pasted value, to the database only
    assert TOPUP not in ran.text
    assert TOPUP not in everything_stored(setup)


def test_a_probe_step_asks_for_it_and_its_run_is_masked_too(setup: Setup) -> None:
    key_is_pii(setup)
    api = setup.client()
    run_id = a_run(api)["id"]
    assert api.post(step_url(run_id, PROBE), json={}).json()["detail"] == REASON
    ran = api.post(step_url(run_id, PROBE), json={"subject": TOPUP})
    assert ran.status_code == 200, ran.text
    again = finished(api, ran.json()["run"])
    assert again["subject"] == "[masked]"
    assert TOPUP not in everything_stored(setup)


def test_a_pasted_value_that_cannot_be_the_key_is_refused(setup: Setup) -> None:
    key_is_pii(setup)
    api = setup.client()
    run_id = a_run(api)["id"]
    response = api.post(step_url(run_id, PROBE), json={"subject": "not-a-topup"})
    assert response.status_code == 422


def test_the_runbook_view_leaves_the_key_unfilled(setup: Setup) -> None:
    key_is_pii(setup)
    api = setup.client()
    run_id = a_run(api)["id"]
    view = api.get(f"/api/runs/{run_id}/runbooks/topup_not_credited/steps")
    assert view.status_code == 200
    assert TOPUP not in view.text


# --- The healthy comparison asks for it --------------------------------------------------------


def test_a_healthy_sample_asks_for_this_runs_subject(setup: Setup) -> None:
    key_is_pii(setup)
    api = setup.client()
    run_id = a_run(api)["id"]
    url = f"/api/runs/{run_id}/healthy-comparison"
    refused = api.post(url, json={})
    assert refused.status_code == 422
    assert refused.json()["detail"] == REASON
    started = api.post(url, json={"failing_subject": TOPUP})
    assert started.status_code in (200, 202, 204), started.text
    deadline = time.monotonic() + 10
    shown = api.get(url).json()
    while shown["attempt"]["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
        shown = api.get(url).json()
    assert shown["failing_subject"] == "[masked]"
    assert TOPUP not in str(shown)
    # The sample left this run out by the pasted value, sent to the database only.
    [(_, parameters)] = [
        c for c in setup.fakes["wallet_db"].calls if "status = 'succeeded'" in c[0]
    ]
    assert parameters["topup_id"] == TOPUP
    assert TOPUP not in everything_stored(setup)


# --- An Ambiguous run's matches, picked by number ---------------------------------------------

EMAIL = "contractor.one@example.com"


def ambiguous(setup: Setup) -> tuple[TestClient, dict[str, Any]]:
    key_is_pii(setup, "card_access", "visitor")
    api = setup.client()
    run = finished(api, start(api, application="card_access", probe="visitor_cards", value=EMAIL))
    assert run["status"] == "ambiguous"
    return api, run


def test_matches_of_a_pii_key_are_masked_and_listed_in_a_stable_order(setup: Setup) -> None:
    _, run = ambiguous(setup)
    assert [m["subject"] for m in run["matches"]] == ["[masked]", "[masked]"]
    assert "VP00012931" not in str(run) and "VP00008807" not in str(run)


def test_a_match_is_picked_by_number_with_the_value_pasted_again(setup: Setup) -> None:
    api, run = ambiguous(setup)
    url = f"/api/runs/{run['id']}/matches"
    assert api.post(url, json={"subjects": ["[masked]"]}).status_code == 422
    picked = api.post(url, json={"value": EMAIL, "picks": [2]})
    assert picked.status_code in (200, 202), picked.text
    [started] = picked.json()["runs"]
    second = finished(api, started["id"])
    assert second["subject"] == "[masked]"
    # Number 2 in the stable order is the larger root key: VP00012931.
    looked_up = str(setup.fakes["access_db"].calls)
    assert "VP00012931" in looked_up
    assert EMAIL not in everything_stored(setup)
    assert "VP00012931" not in everything_stored(setup)


@pytest.mark.parametrize(
    ("body", "says"),
    [
        ({"value": EMAIL, "picks": [3]}, "pick each number once, from 1 to 2"),
        ({"value": EMAIL, "picks": [1, 1]}, "pick each number once, from 1 to 2"),
        ({"value": "nobody@example.com", "picks": [1]}, "the matches have changed since"),
    ],
)
def test_a_wrong_number_or_changed_matches_are_refused(
    setup: Setup, body: dict[str, Any], says: str
) -> None:
    api, run = ambiguous(setup)
    response = api.post(f"/api/runs/{run['id']}/matches", json=body)
    assert response.status_code == 422
    assert says in response.json()["detail"]


# --- owl store mask-pii, for runs stored before this -------------------------------------------


def test_mask_pii_masks_old_runs_only_with_apply(setup: Setup) -> None:
    run_id = a_run(setup.client())["id"]  # stored unmasked, as before
    key_is_pii(setup)
    with setup.store.begin() as conn:
        changed, skipped = mask_stored_runs(conn, LOCAL_WORKSPACE, setup.applications, False)
    assert [c.run_id for c in changed] == [run_id]
    assert "subject" in changed[0].fields
    assert changed[0].audit_events == 2  # run_started and run_finished: append-only, so kept
    assert skipped == 0
    with setup.store.connect() as conn:
        assert get_run(conn, LOCAL_WORKSPACE, run_id).subject == TOPUP  # a dry run wrote nothing
    with setup.store.begin() as conn:
        mask_stored_runs(conn, LOCAL_WORKSPACE, setup.applications, True)
    assert TOPUP not in everything_stored(setup, but_the_audit=True)
    with setup.store.begin() as conn:
        assert mask_stored_runs(conn, LOCAL_WORKSPACE, setup.applications, True) == ([], 0)


# --- A PII identifier is masked whatever a location lists -------------------------------------


def test_a_pii_identifier_is_masked_in_rows_its_location_does_not_list(setup: Setup) -> None:
    visitor = setup.applications["card_access"].entities["visitor"]
    profile = visitor.locations["profile"].model_copy(update={"pii": []})  # email left off
    locations = dict(visitor.locations) | {"profile": profile}
    setup.applications["card_access"].entities["visitor"] = visitor.model_copy(
        update={"locations": locations}
    )
    api = setup.client()
    run = finished(
        api, start(api, application="card_access", probe="visitor_cards", value="VP00012931")
    )
    [found] = [r for r in run["locations"] if r["location"] == "profile"]
    assert all(row["email"] == "[masked]" for row in found["evidence"]["rows"])


# --- owl check and owl store mask-pii ------------------------------------------------------


def pii_key_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "config"
    shutil.copytree(EXAMPLES, folder)
    entity = folder / "wallet" / "entities" / "topup.yaml"
    entity.write_text(
        entity.read_text(encoding="utf-8").replace(
            "topup_id: { pattern: '^tp_[0-9a-z]{6}$', unique: true }",
            "topup_id: { pattern: '^tp_[0-9a-z]{6}$', unique: true, pii: true }",
        ),
        encoding="utf-8",
    )
    return folder


def test_owl_check_warns_of_a_pii_key_and_does_not_refuse(tmp_path: Path) -> None:
    result = CliRunner().invoke(cli.app, ["check", str(pii_key_folder(tmp_path)), "--no-connect"])
    assert result.exit_code == 0, result.output
    assert (
        "warning: wallet: topup's key is PII; its runs show [masked]; "
        "find them by run ID or ticket reference"
    ) in result.output


def test_owl_store_mask_pii_is_a_dry_run_unless_applied_and_prints_no_value(
    setup: Setup, tmp_path: Path
) -> None:
    run_id = a_run(setup.client())["id"]  # stored as v0.1 would have
    folder = pii_key_folder(tmp_path)
    url = setup.store.url.render_as_string(hide_password=False)
    dry = CliRunner().invoke(cli.app, ["store", "mask-pii", str(folder), "--url", url])
    assert dry.exit_code == 0, dry.output
    assert f"{run_id}: subject, identifier" in dry.output
    assert "1 run to mask. Nothing written: run with --apply." in dry.output
    assert "2 audit events of these runs still hold the value" in dry.output
    assert (
        "These audit events can't be changed. To remove them, delete the store outside "
        "ProbeOwl; see the guide."
    ) in dry.output
    assert TOPUP not in dry.output
    applied = CliRunner().invoke(
        cli.app, ["store", "mask-pii", str(folder), "--url", url, "--apply"]
    )
    assert applied.exit_code == 0, applied.output
    assert "Masked 1 run." in applied.output
    with setup.store.connect() as conn:
        [event] = [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == "store_pii_masked"]
    assert event.details == {"runs": [run_id]}
    assert TOPUP not in everything_stored(setup, but_the_audit=True)


# --- The CLI shows what owl serve returns: masked ---------------------------------------------


def test_owl_validate_prints_the_subject_masked(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_is_pii(setup)
    client = setup.client()
    monkeypatch.setattr(server, "client", lambda url: client)
    result = CliRunner().invoke(
        cli.app,
        ["validate", "topup", TOPUP, "--env", "prod", "--application", "wallet", *PROD_RECORD],
    )
    assert result.exit_code == 1, result.output  # tp_91f2c7 fails, as planted
    assert "on [masked]" in result.output
    assert TOPUP not in result.output


# --- A pasted subject is a new lookup: pattern, Production record, its own audit event -------


def stored_masked(setup: Setup, run_id: str, environment: str, reference: str | None) -> None:
    """A finished run stored with a PII key, as v0.2 stores it, with or without a reference."""
    record: dict[str, Any] = {"reason": "incident"}
    if reference is not None:
        record["reference"] = reference
    stored(
        setup,
        run_id,
        0,
        environment=environment,
        subject="[masked]",
        identifier_value="[masked]",
        investigation=record,
    )


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("step", {"subject": TOPUP}),
        ("healthy", {"failing_subject": TOPUP}),
    ],
)
def test_in_production_a_pasted_subject_needs_the_runs_reason_and_reference(
    setup: Setup, action: str, body: dict[str, Any]
) -> None:
    key_is_pii(setup)
    outbox(setup)
    api = setup.client()
    stored_masked(setup, "prod-no-ref", "prod", None)
    url = (
        step_url("prod-no-ref", REVIEWED_READ)
        if action == "step"
        else "/api/runs/prod-no-ref/healthy-comparison"
    )
    refused = api.post(url, json=body)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == PASTED_IN_PRODUCTION
    assert not [c for c in setup.fakes["wallet_db"].calls if "outbox_events" in c[0]]


def test_outside_production_a_run_without_a_reference_may_paste(setup: Setup) -> None:
    key_is_pii(setup)
    outbox(setup)
    stored_masked(setup, "uat-no-ref", "uat", None)
    ran = setup.client().post(step_url("uat-no-ref", REVIEWED_READ), json={"subject": TOPUP})
    assert ran.status_code == 200, ran.text


def test_a_healthy_comparison_checks_the_pasted_value_is_the_key(setup: Setup) -> None:
    key_is_pii(setup)
    api = setup.client()
    run_id = a_run(api)["id"]
    url = f"/api/runs/{run_id}/healthy-comparison"
    refused = api.post(url, json={"failing_subject": "not-a-topup"})
    assert refused.status_code == 422
    assert "does not look like a topup_id" in refused.json()["detail"]


def test_each_pasted_lookup_writes_its_own_audit_event_with_the_subject_masked(
    setup: Setup,
) -> None:
    key_is_pii(setup)
    outbox(setup)
    api = setup.client()
    run_id = a_run(api)["id"]
    assert api.post(step_url(run_id, REVIEWED_READ), json={"subject": TOPUP}).status_code == 200
    assert api.post(step_url(run_id, PROBE), json={"subject": TOPUP}).status_code == 200
    started = api.post(f"/api/runs/{run_id}/healthy-comparison", json={"failing_subject": TOPUP})
    assert started.status_code in (200, 202), started.text
    with setup.store.connect() as conn:
        events = list_events(conn, LOCAL_WORKSPACE, run_id)
    # The run's own run_finished shows its subject [masked] too; these are the pasted lookups'.
    # healthy_comparison_finished ends the same comparison, whenever it ends: not a lookup.
    done = ("run_started", "run_finished", "healthy_comparison_finished")
    actions = [e for e in events if e.action not in done]
    pasted = [e for e in actions if e.details.get("subject") == "[masked]"]
    assert len(pasted) == len(actions)  # each pasted lookup says so
    assert sorted(e.action for e in pasted) == [
        "healthy_comparison_started",
        "runbook_step_run",
        "runbook_step_run",
    ]
    assert TOPUP not in str(events)
