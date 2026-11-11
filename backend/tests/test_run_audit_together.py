# Author: @SaadZarook
"""A change and its audit event commit together: never a change without its event (#242)."""

from typing import Any

import pytest
from sqlalchemy import Connection

import probeowl.runs
from probeowl.model.run import RunStatus
from probeowl.store.audit import AuditEvent, list_events
from probeowl.store.runs import get_run
from probeowl.store.tables import LOCAL_WORKSPACE
from tests.audit_fails import audit_fails
from tests.conftest import Setup
from tests.test_healthy_comparison import HEALTHY, sample_finds, started, url
from tests.test_healthy_comparison import failed_run as failed_to_compare
from tests.test_run_breaker import Clock, on_replica
from tests.test_run_breaker import clock as clock  # the fixture, used by name below
from tests.test_run_steps import MANUAL, events, failed_run, step_url, steps
from tests.test_runs import body, finished, runs_stored, start


def test_no_reader_sees_a_finished_run_before_its_run_finished_event(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[RunStatus] = []
    append = probeowl.runs.append_event

    def watching(conn: Connection, workspace: str, event: AuditEvent, *rest: Any) -> None:
        if event.action == "run_finished":
            # Another connection, as the API's GET /api/runs/{id} would read it.
            with setup.store.connect() as other:
                seen.append(get_run(other, workspace, str(event.run_id)).status)
        append(conn, workspace, event, *rest)

    monkeypatch.setattr(probeowl.runs, "append_event", watching)
    client = setup.client()
    run = finished(client, start(client))
    assert seen == [RunStatus.RUNNING]  # not yet finished while its event was being written
    with setup.store.connect() as conn:
        found = list_events(conn, LOCAL_WORKSPACE, run["id"])
    assert [event.action for event in found] == ["run_started", "run_finished"]


def test_a_run_skipped_at_its_start_is_not_kept_if_its_events_cannot_be(
    setup: Setup, clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_replica(setup, 120)
    client = setup.client()
    assert finished(client, start(client))["status"] == "skipped"  # and the connector paused
    audit_fails(monkeypatch, probeowl.runs, "run_finished")
    response = client.post("/api/runs", json=body())
    assert response.status_code == 500
    assert runs_stored(setup) == 1  # only the first: not the one whose events failed
    assert len(events(setup, "run_started")) == 1


def test_a_tick_whose_event_cannot_be_written_is_not_saved(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = setup.client()
    run_id = failed_run(api)
    audit_fails(monkeypatch, probeowl.runs, "runbook_step_ticked")
    assert api.post(step_url(run_id, MANUAL, "tick"), json={"done": True}).status_code == 500
    assert steps(api, run_id)[MANUAL]["done"] is False
    assert events(setup, "runbook_step_ticked") == []


def test_a_comparison_whose_event_cannot_be_written_does_not_start(
    setup: Setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_finds(setup, HEALTHY)
    api = setup.client()
    run_id = failed_to_compare(api)
    with monkeypatch.context() as patch:
        audit_fails(patch, probeowl.runs, "healthy_comparison_started")
        assert api.post(url(run_id), json={}).status_code == 500
    with setup.store.connect() as conn:
        assert get_run(conn, LOCAL_WORKSPACE, run_id).healthy_attempt is None
    # Nothing was left holding its slot: once the store can be written, it starts.
    assert started(api, run_id)["attempt"]["status"] == "compared"
