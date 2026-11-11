# Author: @SaadZarook
from typing import Any

import pytest
from pydantic import ValidationError

from probeowl.model import Probe, ProbeKind


def topup_completed() -> dict[str, Any]:
    return {
        "probe": "topup_completed",
        "kind": "validate",
        "entity": "topup",
        "owner": "payments-team",
        "steps": [
            {"expect": "count(webhook) >= 1"},
            {
                "when": "record.status == 'succeeded'",
                "expect": ["count(ledger) == 2", "exists(credited_event)"],
            },
        ],
    }


def test_validate_probe_with_steps_loads() -> None:
    probe = Probe.model_validate(topup_completed())
    assert probe.kind is ProbeKind.VALIDATE
    assert probe.steps[0].when is None
    assert probe.steps[0].expect == ["count(webhook) >= 1"]
    assert probe.steps[1].expect == ["count(ledger) == 2", "exists(credited_event)"]


def test_top_level_expect_becomes_one_step_without_when() -> None:
    probe = Probe.model_validate(
        {
            "probe": "visitor_cards",
            "kind": "inspect",
            "entity": "visitor",
            "owner": "access-team",
            "expect": ["count(cards where status = 'active') <= 1"],
        }
    )
    assert len(probe.steps) == 1
    assert probe.steps[0].when is None
    assert probe.steps[0].expect == ["count(cards where status = 'active') <= 1"]
    assert "expect" not in probe.model_dump()


def test_steps_and_top_level_expect_together_are_rejected() -> None:
    data = topup_completed() | {"expect": "count(ledger) == 2"}
    with pytest.raises(ValidationError, match="either steps or a top-level expect"):
        Probe.model_validate(data)


def test_probe_needs_expectations() -> None:
    data = topup_completed()
    data.pop("steps")
    with pytest.raises(ValidationError, match="steps"):
        Probe.model_validate(data)


def test_empty_expect_list_is_rejected() -> None:
    data = topup_completed()
    data["steps"] = [{"when": "record.status == 'failed'", "expect": []}]
    with pytest.raises(ValidationError, match="at least 1 item"):
        Probe.model_validate(data)


def test_health_is_declared_but_not_supported_yet() -> None:
    data = topup_completed() | {"kind": "health"}
    with pytest.raises(ValidationError, match="health probes are not supported yet"):
        Probe.model_validate(data)


def test_owner_is_required() -> None:
    data = topup_completed()
    data.pop("owner")
    with pytest.raises(ValidationError, match="owner"):
        Probe.model_validate(data)
