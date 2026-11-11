# Author: @SaadZarook
"""Comparing a run with a known-good subject: candidates, audited runs, and the field diff."""

import logging
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from probeowl.api.app import create_app
from probeowl.checks.rules import folder_failures
from probeowl.errors import ConnectorError
from probeowl.healthy import Fingerprints, compare_fields
from probeowl.model.catalog import ColumnRole
from probeowl.model.check import load_applications
from probeowl.model.entity import HealthySample
from probeowl.model.healthy import FieldResult
from probeowl.model.kill_switch import KillScope, KillTarget
from probeowl.model.run import Actor, ActorKind
from probeowl.store.audit import list_events
from probeowl.store.runs import get_run, save_run
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.local_client import local_client
from tests.test_read_routes import binding_values
from tests.test_runs import finished, start

EXAMPLES = Path(__file__).parents[2] / "examples"
FAILING = "tp_91f2c7"
HEALTHY = "tp_91f2b9"
ALSO_FAILS = "tp_93b001"
UNKNOWN = ["tp_aaaaa1", "tp_aaaaa2", "tp_aaaaa3", "tp_aaaaa4"]
ACTOR = Actor(kind=ActorKind.PERSON, name="s.khan")
EMAIL = "c.customer@example.com"


def url(run_id: str) -> str:
    return f"/api/runs/{run_id}/healthy-comparison"


def sample_finds(setup: Setup, *subjects: str) -> None:
    """The wallet_db sample lookup answers with these subjects, newest first."""
    fake = setup.fakes["wallet_db"]
    real = fake.answer

    def answer(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        if "status = 'succeeded'" in lookup:
            return [{"topup_id": subject} for subject in subjects]
        return real(lookup, p)

    fake.answer = answer


def failed_run(api: TestClient) -> str:
    run_id = start(api, value=FAILING)
    assert finished(api, run_id)["status"] == "failed"
    return run_id


def compared(api: TestClient, run_id: str, within_s: float = 10) -> dict[str, Any]:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        found: dict[str, Any] = api.get(url(run_id)).json()
        if found["attempt"] is not None and found["attempt"]["status"] != "running":
            return found
        time.sleep(0.02)
    raise AssertionError(f"the comparison of {run_id} still running after {within_s} s")


def started(api: TestClient, run_id: str, **body: Any) -> dict[str, Any]:
    response = api.post(url(run_id), json=body)
    assert response.status_code == 202, response.text
    return compared(api, run_id)


def row(found: dict[str, Any], location: str, field: str | None = None) -> dict[str, Any]:
    [match] = [d for d in found["differences"] if d["location"] == location and d["field"] == field]
    return match


def events(setup: Setup, action: str) -> list[Any]:
    with setup.store.connect() as conn:
        return [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == action]


# --- Finding the healthy record ---------------------------------------------------------------


def test_before_any_attempt_it_says_what_could_start_one(setup: Setup) -> None:
    api = setup.client()
    found = api.get(url(failed_run(api))).json()
    assert found["attempt"] is None
    assert found["has_sample"] is True
    assert found["choices"] == [{"name": "same_payment_method", "title": "Same payment method"}]
    assert found["differences"] == []


def test_the_first_candidate_that_passes_is_compared(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    found = started(api, run_id)
    assert found["attempt"]["status"] == "compared"
    assert found["failing_subject"] == FAILING
    assert found["healthy_subject"] == HEALTHY
    assert found["warning"] is None
    assert row(found, "ledger")["failing"] == "Missing, 0 rows"
    assert row(found, "ledger")["healthy"] == "Found, 2 rows"
    assert row(found, "ledger")["result"] == "different"
    assert row(found, "webhook")["result"] == "different"


def test_the_healthy_run_links_back_and_reuses_the_investigation(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    found = started(api, run_id)
    failing = api.get(f"/api/runs/{run_id}").json()
    healthy = api.get(f"/api/runs/{found['attempt']['healthy_run']}").json()
    assert healthy["healthy_for"] == run_id
    assert healthy["investigation"]["reason"] == failing["investigation"]["reason"]
    assert healthy["investigation"]["reference"] == failing["investigation"]["reference"]
    assert run_id in healthy["investigation"]["related_runs"]
    assert healthy["environment"] == failing["environment"]


def test_a_candidate_that_does_not_pass_is_passed_over(setup: Setup) -> None:
    sample_finds(setup, ALSO_FAILS, HEALTHY)
    api = setup.client()
    found = started(api, failed_run(api))
    assert found["attempt"]["status"] == "compared"
    assert len(found["attempt"]["runs"]) == 2
    assert found["healthy_subject"] == HEALTHY


def test_it_stops_after_three_candidates_and_says_so(setup: Setup) -> None:
    sample_finds(setup, *UNKNOWN)
    api = setup.client()
    found = started(api, failed_run(api))
    assert found["attempt"]["status"] == "none_passed"
    assert len(found["attempt"]["runs"]) == 3
    assert found["attempt"]["message"] == ("none of the 3 candidates passed; paste a known-good ID")
    assert found["differences"] == []


def test_no_candidates_says_to_paste(setup: Setup) -> None:
    sample_finds(setup)
    api = setup.client()
    found = started(api, failed_run(api))
    assert found["attempt"]["status"] == "no_candidates"
    assert "paste a known-good ID" in found["attempt"]["message"]


def test_the_failing_subject_is_never_its_own_candidate(setup: Setup) -> None:
    sample_finds(setup, FAILING)
    api = setup.client()
    assert started(api, failed_run(api))["attempt"]["status"] == "no_candidates"


def test_the_sample_is_kept_with_root_keys_only(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    sample = started(api, failed_run(api))["attempt"]["sample"]
    assert sample["rows"] == [{"topup_id": HEALTHY}]
    assert "status = 'succeeded'" in sample["query"]


def test_a_closer_match_uses_its_own_lookup(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    started(api, run_id, narrow="same_payment_method")
    sent = [lookup for lookup, _ in setup.fakes["wallet_db"].calls]
    assert [s for s in sent if "payment_method = :payment_method" in s]


def test_an_unknown_closer_match_is_refused(setup: Setup) -> None:
    api = setup.client()
    response = api.post(url(failed_run(api)), json={"narrow": "nope"})
    assert response.status_code == 422
    assert "no closer match named nope" in response.json()["detail"]


# --- A pasted ID --------------------------------------------------------------------------------


def test_a_pasted_id_runs_once_without_the_sample(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    found = started(api, run_id, subject=HEALTHY)
    assert found["attempt"]["pasted"] is True
    assert found["attempt"]["sample"] is None
    assert len(found["attempt"]["runs"]) == 1
    assert found["healthy_subject"] == HEALTHY
    assert found["warning"] is None


def test_a_pasted_id_that_does_not_pass_is_compared_with_a_warning(setup: Setup) -> None:
    api = setup.client()
    found = started(api, failed_run(api), subject=ALSO_FAILS)
    assert found["attempt"]["status"] == "compared"
    assert found["warning"] == (
        f"{ALSO_FAILS} did not pass (failed), so its differences may not point at the cause"
    )
    assert found["differences"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"subject": FAILING}, "that is the subject of this run"),
        ({"subject": "nothing like an id"}, ""),
        ({"subject": HEALTHY, "narrow": "same_payment_method"}, "not both"),
    ],
)
def test_a_pasted_id_that_cannot_be_used_is_refused(
    setup: Setup, body: dict[str, str], message: str
) -> None:
    api = setup.client()
    response = api.post(url(failed_run(api)), json=body)
    assert response.status_code == 422
    assert message in response.json()["detail"]


# --- What refuses a comparison ------------------------------------------------------------------


def test_an_unknown_run_is_not_found(setup: Setup) -> None:
    api = setup.client()
    assert api.get(url("nope")).status_code == 404
    assert api.post(url("nope"), json={}).status_code == 404


def stop(setup: Setup, connector: str) -> None:
    target = KillTarget(scope=KillScope.CONNECTOR, application="wallet", connector=connector)
    setup.service().kill.engage(target, "on fire", "INC-1", ACTOR)


def test_a_run_that_did_not_finish_on_a_subject_is_refused(setup: Setup) -> None:
    stop(setup, "wallet_db")
    api = setup.client()
    run_id = start(api, value=FAILING)
    assert finished(api, run_id)["status"] == "skipped"
    response = api.post(url(run_id), json={"subject": HEALTHY})
    assert response.status_code == 422
    assert "only a finished run" in response.json()["detail"]


def test_a_kill_switch_on_the_samples_connector_refuses_it(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    calls = len(setup.fakes["wallet_db"].calls)
    stop(setup, "wallet_db")
    response = api.post(url(run_id), json={})
    assert response.status_code == 403
    assert "on fire" in response.json()["detail"]
    assert len(setup.fakes["wallet_db"].calls) == calls


def test_a_kill_switch_on_a_candidates_connector_says_why(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    stop(setup, "ledger_db")
    found = started(api, run_id)
    assert found["attempt"]["status"] == "could_not_run"
    assert "on fire" in found["attempt"]["message"]
    assert found["differences"] == []


def test_it_counts_toward_the_concurrent_run_limit(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    service = setup.service(max_concurrent=1)
    api = local_client(create_app(service))
    run_id = failed_run(api)
    finished(api, run_id)
    assert service._slots.acquire(blocking=False)
    refused = api.post(url(run_id), json={})
    assert refused.status_code == 429
    service._slots.release()
    assert started(api, run_id)["attempt"]["status"] == "compared"
    # It gave its slot back.
    deadline = time.monotonic() + 5
    while not service._slots.acquire(blocking=False):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    service._slots.release()


def test_a_run_missing_what_the_sample_needs_says_to_paste(setup: Setup) -> None:
    api = setup.client()
    run_id = start(api, value=UNKNOWN[0])
    assert finished(api, run_id)["status"] == "failed"
    response = api.post(url(run_id), json={})
    assert response.status_code == 422
    assert "paste a known-good ID" in response.json()["detail"]


def test_an_entity_with_no_sample_says_to_paste(setup: Setup) -> None:
    setup.applications["wallet"].entities["topup"].healthy_sample = None
    api = setup.client()
    run_id = failed_run(api)
    assert api.get(url(run_id)).json()["has_sample"] is False
    response = api.post(url(run_id), json={})
    assert response.status_code == 422
    assert "has no healthy_sample" in response.json()["detail"]
    assert started(api, run_id, subject=HEALTHY)["attempt"]["status"] == "compared"


def test_a_comparison_owl_serve_did_not_finish_says_so(setup: Setup) -> None:
    sample_finds(setup)
    api = setup.client()
    run_id = failed_run(api)
    started(api, run_id)
    with setup.store.begin() as conn:
        run = get_run(conn, LOCAL_WORKSPACE, run_id)
        attempt = run.healthy_attempt
        assert attempt is not None
        running = attempt.model_copy(update={"status": "running"})
        save_run(conn, LOCAL_WORKSPACE, run.model_copy(update={"healthy_attempt": running}))
    found = api.get(url(run_id)).json()
    assert found["attempt"]["status"] == "could_not_run"
    assert "owl serve stopped" in found["attempt"]["message"]


# --- The audit ----------------------------------------------------------------------------------


def test_it_is_audited_and_each_candidate_run_is_too(setup: Setup) -> None:
    sample_finds(setup, ALSO_FAILS, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    found = started(api, run_id)
    assert [e.run_id for e in events(setup, "healthy_comparison_started")] == [run_id]
    [ended] = events(setup, "healthy_comparison_finished")
    assert ended.details["status"] == "compared"
    assert ended.details["runs"] == found["attempt"]["runs"]
    linked = [e.run_id for e in events(setup, "run_started") if e.details["healthy_for"] == run_id]
    assert linked == found["attempt"]["runs"]


# --- Compare fields ------------------------------------------------------------------------------


def test_catalog_columns_for_sql_and_the_list_for_rest() -> None:
    app = load_applications(EXAMPLES)["wallet"]
    entity = app.entities["topup"]
    record = compare_fields(app, entity.locations["record"])
    assert set(record.fields) == {"amount", "status", "payment_method", "app_version"}
    assert record.varying == {"amount"}
    payment = compare_fields(app, entity.locations["payment"])
    assert payment.fields == ("status", "amount", "currency")
    assert payment.varying == {"amount"}


def test_a_field_expected_to_vary_is_shown_but_not_called_different(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    found = started(api, failed_run(api))
    amount = row(found, "record", "amount")
    assert amount["result"] == "expected_to_vary"
    assert (amount["failing"], amount["healthy"]) == ("500.00", "200.00")
    assert row(found, "payment", "amount")["result"] == "expected_to_vary"
    assert row(found, "record", "payment_method")["result"] == "same"


def test_a_field_on_a_location_one_side_did_not_read_is_not_compared(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    found = started(api, failed_run(api))
    assert row(found, "ledger", "amount")["result"] == "not_compared"


# --- Masked fields ------------------------------------------------------------------------------


def mark_email_compared(setup: Setup) -> None:
    catalog = setup.applications["wallet"].catalog
    assert catalog is not None
    [table] = [t for t in catalog.tables if t.table == "topups"]
    table.columns["customer_email"].roles.append(ColumnRole.COMPARE)


def different_email(setup: Setup, subject: str) -> None:
    fake = setup.fakes["wallet_db"]
    real = fake.answer

    def answer(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        rows = real(lookup, p)
        if p.get("topup_id") == subject and "customer_email" in lookup:
            return [{**r, "customer_email": "someone.else@example.com"} for r in rows]
        return rows

    fake.answer = answer


def test_a_masked_field_that_matches_says_same_and_shows_nothing(setup: Setup) -> None:
    mark_email_compared(setup)
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    response_found = started(api, run_id)
    email = row(response_found, "record", "customer_email")
    assert email["masked"] is True
    assert email["result"] == "same"
    assert email["failing"] is None and email["healthy"] is None


def test_a_masked_field_that_differs_says_different_and_shows_nothing(setup: Setup) -> None:
    mark_email_compared(setup)
    different_email(setup, HEALTHY)
    sample_finds(setup, HEALTHY)
    api = setup.client()
    email = row(started(api, failed_run(api)), "record", "customer_email")
    assert email["result"] == "different"
    assert email["failing"] is None and email["healthy"] is None


def test_after_a_restart_a_masked_field_is_not_compared(setup: Setup) -> None:
    mark_email_compared(setup)
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    started(api, run_id)
    # A new owl serve: the same store, and none of the first one's memory.
    after = setup.client().get(url(run_id)).json()
    assert row(after, "record", "customer_email")["result"] == "not_compared"
    assert row(after, "record", "payment_method")["result"] == "same"


def test_no_fingerprint_or_value_reaches_a_response_the_audit_or_a_log(
    setup: Setup, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    mark_email_compared(setup)
    sample_finds(setup, HEALTHY)
    service = setup.service()
    seen: list[bytes] = []
    real = service._fingerprints._digest

    def digest(value: Any) -> bytes:
        seen.append(real(value))
        return seen[-1]

    service._fingerprints._digest = digest  # type: ignore[method-assign]
    api = local_client(create_app(service))
    run_id = failed_run(api)
    found = started(api, run_id)
    assert seen
    bodies = [
        api.get(url(run_id)).text,
        api.get(f"/api/runs/{run_id}").text,
        api.get(f"/api/runs/{found['attempt']['healthy_run']}").text,
    ]
    with setup.store.connect() as conn:
        bodies.append(str([e.model_dump() for e in list_events(conn, LOCAL_WORKSPACE)]))
    bodies.append(caplog.text)
    for text in bodies:
        assert EMAIL not in text
        for fingerprint in seen:
            assert fingerprint.hex() not in text


def test_fingerprints_compare_as_sets_and_never_show_themselves() -> None:
    prints = Fingerprints()
    prints.remember("a", "cards", "email", ["x", "y", None])
    prints.remember("b", "cards", "email", ["y", None, "x"])
    prints.remember("c", "cards", "email", ["x", "z", None])
    assert prints.compare("a", "b", "cards", "email") is FieldResult.SAME
    assert prints.compare("a", "c", "cards", "email") is FieldResult.DIFFERENT
    assert prints.compare("a", "nope", "cards", "email") is FieldResult.NOT_COMPARED
    assert prints.compare("a", "b", "cards", "other") is FieldResult.NOT_COMPARED
    assert "x" not in repr(prints).replace("Fingerprints", "")


def test_two_sessions_never_share_a_key() -> None:
    assert Fingerprints()._digest("x") != Fingerprints()._digest("x")


def test_only_so_many_runs_are_kept() -> None:
    prints = Fingerprints(keep_runs=1)
    prints.remember("a", "l", "f", ["x"])
    prints.remember("b", "l", "f", ["x"])
    assert prints.compare("a", "b", "l", "f") is FieldResult.NOT_COMPARED


# --- Each parameter is bound as its real type ----------------------------------------------------


def test_a_date_from_the_failing_run_is_bound_as_a_date(setup: Setup) -> None:
    fake = setup.fakes["wallet_db"]
    real = fake.answer
    when = datetime(2026, 3, 4, 8, 0)

    def answer(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        if "status = 'succeeded'" in lookup:
            return [{"topup_id": HEALTHY}]
        return [{**r, "created_at": when} if "created_at" in r else r for r in real(lookup, p)]

    fake.answer = answer
    api = setup.client()
    run_id = failed_run(api)
    stored = api.get(f"/api/runs/{run_id}").json()
    [record] = [r for r in stored["locations"] if r["location"] == "record"]
    assert record["evidence"]["column_types"]["created_at"] == "datetime"
    assert record["evidence"]["rows"][0]["created_at"] == "2026-03-04T08:00:00"
    started(api, run_id)
    [sent] = [p for lookup, p in fake.calls if "status = 'succeeded'" in lookup]
    assert sent["created_at"] == when
    assert isinstance(sent["created_at"], datetime)
    assert sent["topup_id"] == FAILING


def test_a_run_that_recorded_no_types_is_refused_rather_than_bound_as_text(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    with setup.store.begin() as conn:
        run = get_run(conn, LOCAL_WORKSPACE, run_id)
        older = [
            r.model_copy(update={"evidence": r.evidence.model_copy(update={"column_types": {}})})
            if r.evidence
            else r
            for r in run.locations
        ]
        save_run(conn, LOCAL_WORKSPACE, run.model_copy(update={"locations": older}))
    response = api.post(url(run_id), json={})
    assert response.status_code == 422
    assert "run the probe again" in response.json()["detail"]


def test_a_sample_parameter_must_come_from_a_sql_location() -> None:
    app = load_applications(EXAMPLES)["wallet"]
    entity = app.entities["topup"]
    assert entity.healthy_sample is not None
    entity.healthy_sample = HealthySample(
        using="wallet_db",
        lookup=entity.healthy_sample.lookup,
        parameters={**entity.healthy_sample.parameters, "created_at": "payment.status"},
    )
    assert [f for f in folder_failures(app) if "use a SQL location's field" in f]


# --- Where and who --------------------------------------------------------------------------------


def test_a_comparison_never_says_where_or_who(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    started(api, run_id)
    text = api.get(url(run_id)).text
    assert not {value for value in binding_values(setup.applications) if value in text}


def test_it_needs_the_request_header(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    del api.headers["X-ProbeOwl-Request"]
    assert api.post(url(run_id), json={}).status_code == 403


# --- A new comparison replaces the last ----------------------------------------------------------


def test_a_first_comparison_replaces_nothing(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    attempt = started(api, failed_run(api))["attempt"]
    assert attempt["replaced"] is False
    assert attempt["replaced_subject"] is None


def test_a_new_comparison_says_who_it_replaced_even_after_a_restart(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    first = started(api, run_id)
    second = started(api, run_id, subject=ALSO_FAILS)
    assert second["attempt"]["replaced"] is True
    assert second["attempt"]["replaced_subject"] == HEALTHY
    assert second["healthy_subject"] == ALSO_FAILS
    after = setup.client().get(url(run_id)).json()
    assert after["attempt"]["replaced_subject"] == HEALTHY
    [_, latest] = events(setup, "healthy_comparison_started")
    assert latest.details["replaced_run"] == first["attempt"]["healthy_run"]


def test_replacing_an_attempt_that_compared_nothing_names_no_one(setup: Setup) -> None:
    sample_finds(setup)
    api = setup.client()
    run_id = failed_run(api)
    assert started(api, run_id)["attempt"]["status"] == "no_candidates"
    attempt = started(api, run_id, subject=HEALTHY)["attempt"]
    assert attempt["replaced"] is True
    assert attempt["replaced_subject"] is None


def test_a_refused_comparison_leaves_the_last_one_in_place(setup: Setup) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_run(api)
    started(api, run_id)
    assert api.post(url(run_id), json={"narrow": "nope"}).status_code == 422
    found = api.get(url(run_id)).json()
    assert found["healthy_subject"] == HEALTHY
    assert found["attempt"]["replaced"] is False


# --- Outcomes that show nothing ------------------------------------------------------------------


def test_a_connector_error_on_both_sides_is_not_compared(setup: Setup) -> None:
    def fails(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        raise ConnectorError("the connection was refused")

    api = setup.client()
    setup.fakes["wallet_db"].answer = fails
    broken = start(api, value=FAILING)
    assert finished(api, broken)["status"] == "failed"
    found = started(api, broken, subject=HEALTHY)
    record = row(found, "record")
    assert record["failing"] == record["healthy"] == "Error, 0 rows"
    assert record["result"] == "not_compared"
    assert row(found, "ledger")["result"] == "not_compared"


def test_an_outcome_on_one_side_only_is_a_difference(setup: Setup) -> None:
    api = setup.client()
    found = started(api, failed_run(api), subject=HEALTHY)
    assert row(found, "ledger")["result"] == "different"
    assert row(found, "record")["result"] == "same"
