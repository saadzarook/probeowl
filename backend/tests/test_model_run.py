# Author: @SaadZarook
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from probeowl.model import (
    Actor,
    ExpectationResult,
    LocationResult,
    Outcome,
    Run,
    RunStatus,
)


def test_there_are_eight_outcomes_and_skipped_is_not_one() -> None:
    assert {o.value for o in Outcome} == {
        "found",
        "missing",
        "waiting",
        "blocked",
        "ambiguous",
        "error",
        "stale",
        "not_configured",
    }
    assert {s.value for s in RunStatus} == {"running", "passed", "failed", "skipped", "ambiguous"}
    assert {r.value for r in ExpectationResult} == {"pass", "fail", "skip"}


def run(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "8812",
        "application": "wallet",
        "environment": "prod",
        "probe": "topup_completed",
        "kind": "validate",
        "probe_version": "v3",
        "entity": "topup",
        "identifier": "topup_id",
        "identifier_value": "tp_91f2c7",
        "subject": "tp_91f2c7",
        "status": "failed",
        "actor": {"kind": "person", "name": "s.khan"},
        "investigation": {"reason": "incident", "reference": "WALLET-482"},
        "started_at": datetime(2026, 10, 31, 9, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 10, 31, 9, 0, 3, tzinfo=UTC),
        "locations": [
            {"location": "ledger", "outcome": "missing"},
            {"location": "controller", "parent": "cards", "row": 0, "outcome": "found"},
        ],
        "expectations": [
            {"step": 1, "expectation": "count(ledger) == 2", "result": "fail", "detail": "0 rows"}
        ],
    }
    return data | changes


def test_failed_run_loads() -> None:
    loaded = Run.model_validate(run())
    assert loaded.status is RunStatus.FAILED
    assert loaded.locations[0].outcome is Outcome.MISSING
    assert loaded.expectations[0].result is ExpectationResult.FAIL


def test_skipped_run_needs_a_reason() -> None:
    with pytest.raises(ValidationError, match="skip_reason is required"):
        Run.model_validate(run(status="skipped"))
    Run.model_validate(run(status="skipped", skip_reason="circuit breaker open on ledger_db"))


def test_skip_reason_only_on_skipped_runs() -> None:
    with pytest.raises(ValidationError, match="skip_reason is required"):
        Run.model_validate(run(skip_reason="circuit breaker open on ledger_db"))


def test_run_by_a_person_needs_an_investigation_record() -> None:
    with pytest.raises(ValidationError, match="needs an investigation record"):
        Run.model_validate(run(investigation=None))


def test_reference_has_no_spaces() -> None:
    with pytest.raises(ValidationError, match="reference"):
        Run.model_validate(run(investigation={"reason": "incident", "reference": "WALLET 482"}))


def test_ambiguous_identifier_leaves_subject_empty() -> None:
    loaded = Run.model_validate(
        run(identifier="email", identifier_value="v***@example.com", subject=None)
    )
    assert loaded.subject is None


def test_fan_out_result_needs_parent_and_row() -> None:
    with pytest.raises(ValidationError, match="parent and row are set together"):
        LocationResult.model_validate({"location": "controller", "row": 0, "outcome": "found"})


def test_agent_acts_for_a_person() -> None:
    with pytest.raises(ValidationError, match="on_behalf_of is required"):
        Actor.model_validate({"kind": "agent", "name": "claude-desktop"})
    Actor.model_validate({"kind": "agent", "name": "claude-desktop", "on_behalf_of": "s.khan"})


def test_running_run_fills_in_as_outcomes_arrive() -> None:
    loaded = Run.model_validate(
        run(status="running", finished_at=None, locations=[], expectations=[])
    )
    assert loaded.status is RunStatus.RUNNING
    assert loaded.locations == []


def test_running_run_has_no_finished_at() -> None:
    with pytest.raises(ValidationError, match="finished_at is set once a run"):
        Run.model_validate(run(status="running"))


def test_finished_run_needs_finished_at() -> None:
    with pytest.raises(ValidationError, match="finished_at is set once a run"):
        Run.model_validate(run(finished_at=None))


def test_running_is_a_run_status_not_an_outcome() -> None:
    assert "running" not in {o.value for o in Outcome}
