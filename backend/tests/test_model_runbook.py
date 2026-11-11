# Author: @SaadZarook
from typing import Any

import pytest
from pydantic import ValidationError

from probeowl.model import Runbook
from probeowl.model.runbook import ProbeStep, WriteQueryStep


def runbook(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "runbook": "topup_not_credited",
        "title": "Top-up captured but wallet not credited",
        "application": "wallet",
        "applies_to": {"entity": "topup", "when": ["ledger.missing"]},
        "owner": "payments-team",
        "last_verified": "2026-10-12",
        "steps": [
            {
                "kind": "write_query",
                "id": "step-1",
                "title": "Re-queue the ledger posting",
                "using": "wallet_db",
                "requires": "change approval",
                "sql": "UPDATE outbox_events SET status = 'pending' WHERE aggregate_id = :topup_id",
            },
            {
                "kind": "probe",
                "id": "step-inline",
                "title": "Re-validate",
                "probe": "topup_completed",
            },
        ],
    }
    return data | changes


def test_runbook_loads_each_step_as_its_kind() -> None:
    loaded = Runbook.model_validate(runbook())
    assert isinstance(loaded.steps[0], WriteQueryStep)
    assert isinstance(loaded.steps[1], ProbeStep)


def test_write_query_needs_requires() -> None:
    data = runbook()
    data["steps"][0].pop("requires")
    with pytest.raises(ValidationError, match="requires"):
        Runbook.model_validate(data)


def test_unknown_step_kind_is_rejected() -> None:
    data = runbook(
        steps=[
            {"kind": "fix_query", "id": "step-inline", "title": "x", "sql": "UPDATE t SET a = 1"}
        ]
    )
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        Runbook.model_validate(data)


@pytest.mark.parametrize("when", ["ledger.skipped", "ledger", "Ledger.missing"])
def test_applies_to_names_a_location_and_an_outcome(when: str) -> None:
    data = runbook(applies_to={"entity": "topup", "when": [when]})
    with pytest.raises(ValidationError, match="String should match pattern"):
        Runbook.model_validate(data)


def test_runbook_needs_a_step() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        Runbook.model_validate(runbook(steps=[]))
