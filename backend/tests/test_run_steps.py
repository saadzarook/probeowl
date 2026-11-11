# Author: @SaadZarook
"""Running a runbook's steps: the route takes a step's ID, and the server decides what runs."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from probeowl.api.app import create_app
from probeowl.connectors.plan import Access, PlanStep
from probeowl.errors import ConfigError, ConnectorError, MissingGrant
from probeowl.model.kill_switch import KillScope, KillTarget
from probeowl.model.run import Actor, ActorKind, ValueKind
from probeowl.model.runbook import ManualStep, ReadQueryStep, Review, WriteQueryStep, sql_sha256
from probeowl.runbook_steps import _sql_literal, _Value
from probeowl.runs import RunLimits, RunService
from probeowl.store.audit import list_events
from probeowl.store.runs import get_run, save_run
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.conftest import Setup
from tests.local_client import local_client
from tests.plan_fakes import index_seek
from tests.test_read_routes import binding_values
from tests.test_runs import finished, start

SUBJECT = "tp_91f2c7"
RUNBOOK = "topup_not_credited"
ACTOR = Actor(kind=ActorKind.PERSON, name="s.khan")
# The example's steps, by ID.
REVIEWED_READ = "check-outbox"
UNREVIEWED_READ = "check-locks"
EXTERNAL = "ledger-errors"
MANUAL = "confirm-capture"
WRITE = "requeue-posting"
PROBE = "revalidate"


def step_url(run_id: str, step: str, action: str = "run") -> str:
    return f"/api/runs/{run_id}/runbooks/{RUNBOOK}/steps/{step}/{action}"


def outbox(setup: Setup) -> None:
    """The reviewed read's table answers with one row."""
    fake = setup.fakes["wallet_db"]
    real = fake.answer

    def answer(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        if "outbox_events" in lookup:
            return [{"id": 7, "status": "failed", "created_at": datetime(2026, 3, 4, 8, 0)}]
        return real(lookup, p)

    fake.answer = answer


def failed_run(api: TestClient) -> str:
    run_id = start(api, value=SUBJECT)
    assert finished(api, run_id)["status"] == "failed"
    return run_id


def steps(api: TestClient, run_id: str) -> dict[str, dict[str, Any]]:
    found = api.get(f"/api/runs/{run_id}/runbooks/{RUNBOOK}/steps")
    assert found.status_code == 200, found.text
    return {step["id"]: step for step in found.json()}


def events(setup: Setup, action: str) -> list[Any]:
    with setup.store.connect() as conn:
        return [e for e in list_events(conn, LOCAL_WORKSPACE) if e.action == action]


def add_step(setup: Setup, step: Any) -> str:
    """Add a step to the example's runbook; its ID."""
    runbook = setup.applications["wallet"].runbooks[RUNBOOK]
    steps_now = [*runbook.steps, step]
    setup.applications["wallet"].runbooks[RUNBOOK] = runbook.model_copy(update={"steps": steps_now})
    return str(step.id)


def reviewed_read(sql: str, using: str = "wallet_db") -> ReadQueryStep:
    review = Review(by="s.khan", date=datetime(2026, 10, 12).date(), sql_sha256=sql_sha256(sql))
    return ReadQueryStep(
        kind="read_query", id="added", title="Added", using=using, sql=sql, reviewed=review
    )


def large_scan(sql: str) -> list[PlanStep]:
    return [PlanStep("Table Scan", Access.SCAN, "outbox_events", None, 5_000_000)]


def small_scan(sql: str) -> list[PlanStep]:
    return [PlanStep("Table Scan", Access.SCAN, "outbox_events", None, 12)]


# --- It runs only what the server marks runnable --------------------------------------------------


def test_a_reviewed_read_runs_and_its_result_is_kept_with_the_run(setup: Setup) -> None:
    outbox(setup)
    api = setup.client()
    run_id = failed_run(api)
    response = api.post(step_url(run_id, REVIEWED_READ), json={})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["outcome"] == "found"
    assert result["evidence"]["rows"][0]["status"] == "failed"
    assert result["evidence"]["parameters"] == {"topup_id": SUBJECT}
    assert result["actor"] == "s.khan"
    assert steps(api, run_id)[REVIEWED_READ]["result"]["outcome"] == "found"


def test_its_sql_comes_from_the_runbook_and_its_values_from_the_run(setup: Setup) -> None:
    outbox(setup)
    api = setup.client()
    run_id = failed_run(api)
    api.post(step_url(run_id, REVIEWED_READ), json={})
    [(sql, parameters)] = [c for c in setup.fakes["wallet_db"].calls if "outbox_events" in c[0]]
    assert "FROM outbox_events WHERE aggregate_id = :topup_id" in sql
    assert parameters == {"topup_id": SUBJECT}


@pytest.mark.parametrize(
    "body",
    [
        {"sql": "SELECT 1"},
        {"step": "1"},
        {"parameters": {"topup_id": "tp_000000"}},
        {"run_without_plan_check": True, "sql": "SELECT 1"},
    ],
)
def test_the_route_takes_no_sql_and_no_values(setup: Setup, body: dict[str, Any]) -> None:
    api = setup.client()
    run_id = failed_run(api)
    calls = len(setup.fakes["wallet_db"].calls)
    assert api.post(step_url(run_id, REVIEWED_READ), json=body).status_code == 422
    assert len(setup.fakes["wallet_db"].calls) == calls


def test_a_write_step_is_always_refused(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    calls = len(setup.fakes["wallet_db"].calls)
    response = api.post(step_url(run_id, WRITE), json={"run_without_plan_check": True})
    assert response.status_code == 422
    assert "never run by ProbeOwl" in response.json()["detail"]
    assert len(setup.fakes["wallet_db"].calls) == calls
    assert setup.plans.asked == []


def test_a_read_step_that_changes_data_is_refused_even_with_a_review(setup: Setup) -> None:
    sql = "UPDATE outbox_events SET status = 'pending' WHERE aggregate_id = :topup_id"
    step = add_step(setup, reviewed_read(sql))
    api = setup.client()
    run_id = failed_run(api)
    calls = len(setup.fakes["wallet_db"].calls)
    response = api.post(step_url(run_id, step), json={})
    assert response.status_code == 422
    assert "never run by ProbeOwl" in response.json()["detail"]
    assert len(setup.fakes["wallet_db"].calls) == calls


def test_a_reference_query_is_refused_and_says_why(setup: Setup) -> None:
    api = setup.client()
    response = api.post(step_url(failed_run(api), UNREVIEWED_READ), json={})
    assert response.status_code == 422
    assert response.json()["detail"] == "this step is copy only: not reviewed yet"


def test_a_read_whose_sql_changed_after_its_review_is_refused(setup: Setup) -> None:
    step = reviewed_read("SELECT id FROM outbox_events WHERE aggregate_id = :topup_id")
    changed = step.model_copy(update={"sql": f"{step.sql} AND status = 'failed'"})
    number = add_step(setup, changed)
    api = setup.client()
    response = api.post(step_url(failed_run(api), number), json={})
    assert response.status_code == 422
    assert "its SQL changed after it was reviewed" in response.json()["detail"]


@pytest.mark.parametrize("step", [EXTERNAL, MANUAL])
def test_a_step_with_nothing_to_run_is_refused(setup: Setup, step: str) -> None:
    api = setup.client()
    response = api.post(step_url(failed_run(api), step), json={})
    assert response.status_code == 422
    assert "does not run this step" in response.json()["detail"]


def test_what_does_not_exist_is_not_found(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    assert api.post(step_url(run_id, "99"), json={}).status_code == 404
    assert api.post(step_url("nope", "1"), json={}).status_code == 404
    other = f"/api/runs/{run_id}/runbooks/nope/steps/1/run"
    assert api.post(other, json={}).status_code == 404


def test_it_needs_the_request_header(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    del api.headers["X-ProbeOwl-Request"]
    assert api.post(step_url(run_id, REVIEWED_READ), json={}).status_code == 403


# --- A probe step ---------------------------------------------------------------------------------


def test_a_probe_step_runs_the_probe_again_under_the_investigation(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    response = api.post(step_url(run_id, PROBE), json={})
    assert response.status_code == 200, response.text
    again = finished(api, response.json()["run"])
    assert again["subject"] == SUBJECT
    assert again["probe"] == "topup_completed"
    assert run_id in again["investigation"]["related_runs"]
    assert again["investigation"]["reference"] == "WALLET-482"


def test_a_probe_step_for_a_probe_that_is_gone_is_refused(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    del setup.applications["wallet"].probes["topup_completed"]
    response = api.post(step_url(run_id, PROBE), json={})
    assert response.status_code == 422
    assert "no probe named topup_completed" in response.json()["detail"]


# --- Its plan, by the rules a probe's save goes by -----------------------------------------------


def test_the_plan_is_read_before_the_read(setup: Setup) -> None:
    outbox(setup)
    api = setup.client()
    run_id = failed_run(api)
    api.post(step_url(run_id, REVIEWED_READ), json={})
    assert [(c, e) for c, e, _ in setup.plans.asked] == [("wallet_db", "prod")]


def test_a_full_scan_of_a_large_table_is_refused_and_nothing_runs(setup: Setup) -> None:
    outbox(setup)
    setup.plans.planner = large_scan
    api = setup.client()
    run_id = failed_run(api)
    response = api.post(step_url(run_id, REVIEWED_READ), json={"run_without_plan_check": True})
    assert response.status_code == 422
    # The message a probe's save gives, under the step's own start.
    assert response.json()["detail"].startswith("this step cannot run: ")
    assert "would read all of" in response.json()["detail"]
    assert "Index the column it filters on" in response.json()["detail"]
    assert not [c for c in setup.fakes["wallet_db"].calls if "outbox_events" in c[0]]
    assert steps(api, run_id)[REVIEWED_READ]["result"] is None


def test_a_scan_of_a_small_table_runs_with_a_warning(setup: Setup) -> None:
    outbox(setup)
    setup.plans.planner = small_scan
    api = setup.client()
    result = api.post(step_url(failed_run(api), REVIEWED_READ), json={}).json()["result"]
    assert result["outcome"] == "found"
    assert "index it before it grows" in result["warnings"][0]


def test_in_production_an_unreadable_plan_needs_an_acknowledgement(setup: Setup) -> None:
    outbox(setup)
    setup.plans.unbound = frozenset({"wallet_db"})
    api = setup.client()
    run_id = failed_run(api)
    refused = api.post(step_url(run_id, REVIEWED_READ), json={})
    assert refused.status_code == 422
    assert "plan checks could not run in prod" in refused.json()["detail"]
    assert "to run without plan check, confirm it" in refused.json()["detail"]
    assert not [c for c in setup.fakes["wallet_db"].calls if "outbox_events" in c[0]]

    allowed = api.post(step_url(run_id, REVIEWED_READ), json={"run_without_plan_check": True})
    assert allowed.status_code == 200
    [event] = events(setup, "runbook_step_run")
    assert event.details["run_without_plan_check"] is True
    assert event.details["plans_not_run"]
    assert event.actor.name == "s.khan"


def test_outside_production_an_unreadable_plan_is_only_a_warning(setup: Setup) -> None:
    outbox(setup)
    setup.plans.unbound = frozenset({"wallet_db"})
    api = setup.client()
    run_id = start(api, value=SUBJECT, environment="uat")
    finished(api, run_id)
    response = api.post(step_url(run_id, REVIEWED_READ), json={})
    assert response.status_code == 200, response.text
    assert "index use not checked" in response.json()["result"]["warnings"][0]
    assert events(setup, "runbook_step_run")[0].details["run_without_plan_check"] is False


# --- What else stops it --------------------------------------------------------------------------


def test_a_kill_switch_refuses_it_before_the_plan_is_read(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    target = KillTarget(scope=KillScope.CONNECTOR, application="wallet", connector="wallet_db")
    setup.service().kill.engage(target, "on fire", "INC-1", ACTOR)
    response = api.post(step_url(run_id, REVIEWED_READ), json={})
    assert response.status_code == 403
    assert "on fire" in response.json()["detail"]
    assert setup.plans.asked == []


def test_it_counts_toward_the_run_limit(setup: Setup) -> None:
    service = setup.service(max_concurrent=1)
    api = local_client(create_app(service, plans=setup.plans))
    run_id = failed_run(api)
    finished(api, run_id)
    assert service._slots.acquire(blocking=False)
    assert api.post(step_url(run_id, REVIEWED_READ), json={}).status_code == 429
    service._slots.release()
    assert api.post(step_url(run_id, REVIEWED_READ), json={}).status_code == 200
    assert service._slots.acquire(blocking=False)  # it gave its slot back
    service._slots.release()


def test_a_connector_that_fails_is_an_error_with_the_reason(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)

    def fails(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        raise ConnectorError("the connection was refused")

    setup.fakes["wallet_db"].answer = fails
    result = api.post(step_url(run_id, REVIEWED_READ), json={}).json()["result"]
    assert result["outcome"] == "error"
    assert result["reason"] == "the connection was refused"
    assert result["evidence"] is None


def test_a_secret_that_cannot_be_read_is_an_error_with_the_reason(setup: Setup) -> None:
    def no_secret(connector: Any, environment: str) -> Any:
        raise ConfigError("the secret of connector wallet_db in prod cannot be read")

    api = setup.client()
    run_id = failed_run(api)
    service = RunService(
        setup.applications, setup.store, RunLimits(), no_secret, actor_name="s.khan"
    )
    fresh = local_client(create_app(service, plans=setup.plans))
    result = fresh.post(step_url(run_id, REVIEWED_READ), json={}).json()["result"]
    assert result["outcome"] == "error"
    assert "cannot be read" in result["reason"]


def test_no_rows_is_missing(setup: Setup) -> None:
    fake = setup.fakes["wallet_db"]
    real = fake.answer
    fake.answer = lambda lookup, p: [] if "outbox_events" in lookup else real(lookup, p)
    api = setup.client()
    result = api.post(step_url(failed_run(api), REVIEWED_READ), json={}).json()["result"]
    assert result["outcome"] == "missing"


def test_a_run_still_going_is_refused(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    with setup.store.begin() as conn:
        run = get_run(conn, LOCAL_WORKSPACE, run_id)
        save_run(conn, LOCAL_WORKSPACE, run.model_copy(update={"subject": None}))
    response = api.post(step_url(run_id, REVIEWED_READ), json={})
    assert response.status_code == 422
    assert "on one subject" in response.json()["detail"]


def test_a_runbook_for_another_entity_is_refused(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    runbook = setup.applications["wallet"].runbooks[RUNBOOK]
    other = runbook.applies_to.model_copy(update={"entity": "visitor"})
    setup.applications["wallet"].runbooks[RUNBOOK] = runbook.model_copy(
        update={"applies_to": other}
    )
    response = api.post(step_url(run_id, REVIEWED_READ), json={})
    assert response.status_code == 422
    assert "is for visitor" in response.json()["detail"]


# --- Values: masked stays unfilled ----------------------------------------------------------------


def test_values_fill_from_the_run_in_each_steps_own_syntax(setup: Setup) -> None:
    api = setup.client()
    found = steps(api, failed_run(api))
    assert f"aggregate_id = '{SUBJECT}'" in found[REVIEWED_READ]["filled"]
    assert f'contains(content, "{SUBJECT}")' in found[EXTERNAL]["filled"]
    assert f"aggregate_id = '{SUBJECT}'" in found[WRITE]["filled"]
    assert found[MANUAL]["filled"] is None
    assert found[REVIEWED_READ]["unfilled"] == []


def test_a_masked_value_stays_unfilled_and_the_step_cannot_run(setup: Setup) -> None:
    sql = "SELECT id FROM outbox_events WHERE aggregate_id = :customer_email"
    number = add_step(setup, reviewed_read(sql))
    api = setup.client()
    run_id = failed_run(api)
    step = steps(api, run_id)[number]
    assert ":customer_email" in step["filled"]
    assert "c.customer@example.com" not in str(step)
    assert step["unfilled"] == [
        {"parameter": "customer_email", "why": "masked in this run, so it is never filled in"}
    ]
    refused = api.post(step_url(run_id, number), json={})
    assert refused.status_code == 422
    assert "masked in this run" in refused.json()["detail"]
    assert not [c for c in setup.fakes["wallet_db"].calls if ":customer_email" in c[0]]


def test_a_write_query_never_fills_in_a_masked_value(setup: Setup) -> None:
    write = WriteQueryStep(
        kind="write_query",
        id="added-write",
        title="Fix it",
        using="wallet_db",
        requires="change approval",
        sql="UPDATE topups SET status = 'x' WHERE customer_email = :customer_email",
    )
    number = add_step(setup, write)
    api = setup.client()
    copied = api.post(step_url(failed_run(api), number, "copy")).json()
    assert ":customer_email" in copied["text"]
    assert "c.customer@example.com" not in copied["text"]
    assert copied["unfilled"][0]["parameter"] == "customer_email"


def test_a_value_two_locations_disagree_on_stays_unfilled(setup: Setup) -> None:
    number = add_step(setup, reviewed_read("SELECT id FROM outbox_events WHERE status = :status"))
    api = setup.client()
    step = steps(api, failed_run(api))[number]
    assert step["unfilled"][0]["why"] == "two locations of this run hold different values for it"


def test_a_value_on_several_rows_stays_unfilled(setup: Setup) -> None:
    number = add_step(
        setup, reviewed_read("SELECT id FROM outbox_events WHERE status = :event_type")
    )
    api = setup.client()
    step = steps(api, failed_run(api))[number]
    assert step["unfilled"][0]["why"] == "this run has several rows with it"


def test_a_value_the_run_does_not_have_stays_unfilled(setup: Setup) -> None:
    number = add_step(setup, reviewed_read("SELECT id FROM outbox_events WHERE id = :nothing"))
    api = setup.client()
    step = steps(api, failed_run(api))[number]
    assert step["unfilled"][0]["why"] == "this run has no such value"


def test_a_parameter_like_thing_in_a_string_is_left_alone(setup: Setup) -> None:
    number = add_step(
        setup, reviewed_read("SELECT ':topup_id' AS t FROM outbox_events WHERE id = :topup_id")
    )
    api = setup.client()
    filled = steps(api, failed_run(api))[number]["filled"]
    assert "':topup_id'" in filled
    assert f"id = '{SUBJECT}'" in filled


def test_a_date_is_bound_as_a_date(setup: Setup) -> None:
    fake = setup.fakes["wallet_db"]
    real = fake.answer
    when = datetime(2026, 3, 4, 8, 0)

    def answer(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        if "outbox_events" in lookup:
            return [{"id": 7}]
        return [{**r, "created_at": when} if "created_at" in r else r for r in real(lookup, p)]

    fake.answer = answer
    add = reviewed_read("SELECT id FROM outbox_events WHERE created_at = :created_at")
    number = add_step(setup, add)
    api = setup.client()
    assert api.post(step_url(failed_run(api), number), json={}).status_code == 200
    [sent] = [p for sql, p in fake.calls if sql == add.sql]
    assert sent["created_at"] == when
    assert isinstance(sent["created_at"], datetime)


@pytest.mark.parametrize(
    ("found", "backslashes", "literal"),
    [
        (_Value("it's", ValueKind.TEXT), False, "'it''s'"),
        (_Value("a\\b", ValueKind.TEXT), True, "'a\\\\b'"),
        (_Value("a\\b", ValueKind.TEXT), False, "'a\\b'"),
        (_Value("500.25", ValueKind.DECIMAL), False, "500.25"),
        (_Value(7, ValueKind.INTEGER), False, "7"),
        (_Value(True, ValueKind.BOOLEAN), False, "1"),
        (_Value("2026-03-04T08:00:00", ValueKind.DATETIME), False, "'2026-03-04T08:00:00'"),
    ],
)
def test_a_literal_cannot_end_its_string(found: _Value, backslashes: bool, literal: str) -> None:
    assert _sql_literal(found, backslashes) == literal


# --- Results are masked -----------------------------------------------------------------------


def test_a_result_is_masked_by_the_catalog_whatever_the_save_check_saw(setup: Setup) -> None:
    fake = setup.fakes["wallet_db"]
    real = fake.answer

    def answer(lookup: str, p: Mapping[str, object]) -> list[dict[str, Any]]:
        if "outbox_events" in lookup:
            return [{"id": 7, "customer_email": "c.customer@example.com"}]
        return real(lookup, p)

    fake.answer = answer
    api = setup.client()
    run_id = failed_run(api)
    result = api.post(step_url(run_id, REVIEWED_READ), json={}).json()["result"]
    assert result["evidence"]["rows"] == [{"id": 7, "customer_email": "[masked]"}]
    assert "c.customer@example.com" not in str(steps(api, run_id))
    assert "c.customer@example.com" not in str(
        [e.details for e in events(setup, "runbook_step_run")]
    )


def test_the_audit_keeps_the_outcome_not_the_values(setup: Setup) -> None:
    outbox(setup)
    api = setup.client()
    run_id = failed_run(api)
    api.post(step_url(run_id, REVIEWED_READ), json={})
    [event] = events(setup, "runbook_step_run")
    assert event.run_id == run_id
    assert event.environment == "prod"
    assert event.details["outcome"] == "found"
    assert event.details["rows"] == 1
    assert "failed" not in str(event.details)


# --- Copying a write query ---------------------------------------------------------------------


def test_a_copy_has_its_header_and_values_and_is_audited(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    response = api.post(step_url(run_id, WRITE, "copy"))
    assert response.status_code == 200, response.text
    text = response.json()["text"]
    first, *rest = text.splitlines()
    assert first == f"-- WRITE · Production · run {run_id} · needs change approval; run by a DBA"
    assert f"aggregate_id = '{SUBJECT}'" in "\n".join(rest)
    [event] = events(setup, "write_query_copied")
    assert event.run_id == run_id
    assert event.environment == "prod"
    assert event.actor.name == "s.khan"
    assert event.details["step"] == WRITE
    assert SUBJECT not in str(event.details)


def test_a_copy_never_touches_a_database(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    calls = len(setup.fakes["wallet_db"].calls)
    api.post(step_url(run_id, WRITE, "copy"))
    assert len(setup.fakes["wallet_db"].calls) == calls
    assert setup.plans.asked == []


def test_requires_cannot_end_the_header_comment(setup: Setup) -> None:
    write = WriteQueryStep(
        kind="write_query",
        id="added-write",
        title="Fix",
        using="wallet_db",
        requires="approval\nDROP TABLE topups",
        sql="DELETE FROM t WHERE id = :topup_id",
    )
    number = add_step(setup, write)
    api = setup.client()
    text = api.post(step_url(failed_run(api), number, "copy")).json()["text"]
    assert text.splitlines()[0].endswith("needs approval DROP TABLE topups")
    assert text.splitlines()[1].startswith("DELETE")


def test_a_read_step_labelled_wrongly_copies_as_a_write(setup: Setup) -> None:
    sql = "UPDATE topups SET status = 'x' WHERE id = :topup_id"
    number = add_step(setup, reviewed_read(sql))
    api = setup.client()
    copied = api.post(step_url(failed_run(api), number, "copy"))
    assert copied.status_code == 200
    assert copied.json()["text"].startswith("-- WRITE")


@pytest.mark.parametrize("step", [REVIEWED_READ, EXTERNAL, MANUAL, PROBE])
def test_only_a_write_querys_copy_is_recorded_here(setup: Setup, step: str) -> None:
    api = setup.client()
    response = api.post(step_url(failed_run(api), step, "copy"))
    assert response.status_code == 422
    assert events(setup, "write_query_copied") == []


def test_a_copy_with_no_run_leaves_its_parameters_and_is_audited(setup: Setup) -> None:
    api = setup.client()
    url = f"/api/applications/wallet/runbooks/{RUNBOOK}/steps/{WRITE}/copy"
    response = api.post(url)
    assert response.status_code == 200, response.text
    text = response.json()["text"]
    assert text.startswith("-- WRITE · no run · needs")
    assert ":topup_id" in text
    [event] = events(setup, "write_query_copied")
    assert event.details["application"] == "wallet"


def test_a_copy_with_no_run_of_what_does_not_exist_is_not_found(setup: Setup) -> None:
    api = setup.client()
    assert api.post("/api/applications/wallet/runbooks/nope/steps/1/copy").status_code == 404
    url = f"/api/applications/wallet/runbooks/{RUNBOOK}/steps/99/copy"
    assert api.post(url).status_code == 404


# --- Ticks -------------------------------------------------------------------------------------


def test_a_tick_is_saved_with_the_run_and_audited(setup: Setup) -> None:
    api = setup.client()
    run_id = failed_run(api)
    assert steps(api, run_id)[MANUAL]["done"] is False
    assert api.post(step_url(run_id, MANUAL, "tick"), json={"done": True}).status_code == 204
    assert steps(api, run_id)[MANUAL]["done"] is True
    # Another owl serve on the same store sees it.
    assert steps(setup.client(), run_id)[MANUAL]["done"] is True
    assert api.post(step_url(run_id, MANUAL, "tick"), json={"done": False}).status_code == 204
    assert steps(api, run_id)[MANUAL]["done"] is False
    assert [e.details["done"] for e in events(setup, "runbook_step_ticked")] == [True, False]


def test_only_a_manual_step_can_be_ticked(setup: Setup) -> None:
    api = setup.client()
    response = api.post(step_url(failed_run(api), WRITE, "tick"), json={"done": True})
    assert response.status_code == 422
    assert "only a manual step" in response.json()["detail"]


def test_ticks_are_per_run(setup: Setup) -> None:
    api = setup.client()
    first, second = failed_run(api), failed_run(api)
    api.post(step_url(first, MANUAL, "tick"), json={"done": True})
    assert steps(api, second)[MANUAL]["done"] is False


def test_a_manual_step_added_later_is_found(setup: Setup) -> None:
    step = ManualStep(kind="manual", id="another", title="Another", text="Check it.")
    number = add_step(setup, step)
    api = setup.client()
    assert steps(api, failed_run(api))[number]["filled"] is None


# --- Where and who ------------------------------------------------------------------------------


def test_steps_in_a_run_never_say_where_or_who(setup: Setup) -> None:
    outbox(setup)
    api = setup.client()
    run_id = failed_run(api)
    api.post(step_url(run_id, REVIEWED_READ), json={})
    text = api.get(f"/api/runs/{run_id}/runbooks/{RUNBOOK}/steps").text
    assert not {value for value in binding_values(setup.applications) if value in text}


def test_a_well_indexed_step_has_no_warnings(setup: Setup) -> None:
    outbox(setup)
    setup.plans.planner = index_seek
    api = setup.client()
    result = api.post(step_url(failed_run(api), REVIEWED_READ), json={}).json()["result"]
    assert result["warnings"] == []


# --- A step's ID, never its position --------------------------------------------------------------


def test_results_and_ticks_stay_with_their_step_when_steps_move(setup: Setup) -> None:
    outbox(setup)
    api = setup.client()
    run_id = failed_run(api)
    api.post(step_url(run_id, REVIEWED_READ), json={})
    api.post(step_url(run_id, MANUAL, "tick"), json={"done": True})
    runbook = setup.applications["wallet"].runbooks[RUNBOOK]
    moved = list(reversed(runbook.steps))
    setup.applications["wallet"].runbooks[RUNBOOK] = runbook.model_copy(update={"steps": moved})
    found = steps(api, run_id)
    assert found[REVIEWED_READ]["result"]["outcome"] == "found"
    assert found[MANUAL]["done"] is True
    assert found[UNREVIEWED_READ]["result"] is None
    assert found[WRITE]["done"] is False


def test_a_position_is_not_an_id(setup: Setup) -> None:
    api = setup.client()
    assert api.post(step_url(failed_run(api), "1"), json={}).status_code == 404


def test_the_audit_names_the_steps_id(setup: Setup) -> None:
    outbox(setup)
    api = setup.client()
    run_id = failed_run(api)
    api.post(step_url(run_id, REVIEWED_READ), json={})
    api.post(step_url(run_id, MANUAL, "tick"), json={"done": True})
    api.post(step_url(run_id, WRITE, "copy"))
    assert events(setup, "runbook_step_run")[0].details["step"] == REVIEWED_READ
    assert events(setup, "runbook_step_ticked")[0].details["step"] == MANUAL
    assert events(setup, "write_query_copied")[0].details["step"] == WRITE


def test_a_grant_the_database_names_makes_the_step_copy_only(setup: Setup) -> None:
    # As Oracle refuses v$session for a login without SELECT on SYS.V_$SESSION.
    outbox(setup)

    def refused(sql: str) -> list[PlanStep]:
        raise MissingGrant("SELECT on SYS.V_$SESSION")

    setup.plans.planner = refused
    api = setup.client()
    run_id = failed_run(api)
    answer = api.post(step_url(run_id, REVIEWED_READ), json={})
    assert answer.status_code == 422
    assert answer.json()["detail"] == (
        "this step is copy only: wallet_db's login in prod is missing SELECT on "
        "SYS.V_$SESSION, which a DBA grants, as for the lag grants (docs/connectors.md)"
    )
    # Nothing was read, and a confirmation to skip the plan check does not change that.
    again = api.post(step_url(run_id, REVIEWED_READ), json={"run_without_plan_check": True})
    assert again.status_code == 422
    assert not [c for c in setup.fakes["wallet_db"].calls if "outbox_events" in c[0]]
