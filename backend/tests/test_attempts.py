# Author: @SaadZarook
"""At most 5 connection tests with a typed password per binding in any minute (#61)."""

from typing import Any

import pytest

from probeowl.attempts import MAX_ATTEMPTS, AttemptLimit
from probeowl.errors import TOO_MANY_ATTEMPTS, TooManyAttempts
from tests.test_setup_writes import DRAFT_URL, Setup, draft, new_password
from tests.test_setup_writes import setup as setup  # the fixture: the examples behind setup routes

KEY = ("card_access", "access_reports", "uat")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


# --- The limit -------------------------------------------------------------------------------


def test_the_sixth_attempt_within_a_minute_is_refused_and_says_when_to_try() -> None:
    clock = Clock()
    limit = AttemptLimit(clock)
    for second in range(MAX_ATTEMPTS):
        clock.now = 1000.0 + second
        limit.attempt(KEY)
    clock.now = 1010.0
    with pytest.raises(TooManyAttempts) as refused:
        limit.attempt(KEY)
    assert str(refused.value) == TOO_MANY_ATTEMPTS
    # The first attempt, at 1000, leaves the window at 1060: 50 seconds from 1010.
    assert refused.value.headers() == {"Retry-After": "50"}


def test_after_the_minute_an_attempt_is_allowed_again() -> None:
    clock = Clock()
    limit = AttemptLimit(clock)
    for _ in range(MAX_ATTEMPTS):
        limit.attempt(KEY)
    clock.now += 60.0
    limit.attempt(KEY)  # the first five have left the window


def test_a_refused_attempt_is_not_counted() -> None:
    clock = Clock()
    limit = AttemptLimit(clock)
    for _ in range(MAX_ATTEMPTS):
        limit.attempt(KEY)
    for _ in range(3):
        with pytest.raises(TooManyAttempts):
            limit.attempt(KEY)
    clock.now += 60.0
    for _ in range(MAX_ATTEMPTS):
        limit.attempt(KEY)  # five again, not two


@pytest.mark.parametrize(
    "other", [("card_access", "access_db", "uat"), ("card_access", "access_reports", "prod")]
)
def test_another_connector_or_environment_has_its_own_count(other: tuple[str, str, str]) -> None:
    limit = AttemptLimit(Clock())
    for _ in range(MAX_ATTEMPTS):
        limit.attempt(KEY)
    limit.attempt(other)


# --- Through the API ---------------------------------------------------------------------------


def tried(setup: Setup, body: dict[str, Any], times: int) -> list[Any]:
    return [setup.api.post(DRAFT_URL, json=body) for _ in range(times)]


def test_a_draft_tried_a_sixth_time_is_a_429_with_retry_after_and_nothing_is_tried(
    setup: Setup,
) -> None:
    responses = tried(setup, draft(), MAX_ATTEMPTS + 1)
    assert [r.status_code for r in responses] == [200] * MAX_ATTEMPTS + [429]
    refused = responses[-1]
    assert refused.json()["type"] == "urn:probeowl:problem:too-many-attempts"
    assert refused.json()["detail"] == TOO_MANY_ATTEMPTS
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    assert len(setup.secrets_seen) == MAX_ATTEMPTS  # the sixth used no password
    [event] = setup.events("connection_test_refused")
    assert event.details == {
        "application": "card_access",
        "connector": "access_reports",
        "environment": "uat",
        "reason": TOO_MANY_ATTEMPTS,
        "draft": True,
    }


def test_change_passwords_test_is_limited_too(setup: Setup) -> None:
    responses = tried(setup, new_password(), MAX_ATTEMPTS + 1)
    assert responses[-1].status_code == 429
    assert responses[-1].json()["detail"] == TOO_MANY_ATTEMPTS


def test_the_saved_password_is_not_limited(setup: Setup, monkeypatch: pytest.MonkeyPatch) -> None:
    import probeowl.setup_writes

    monkeypatch.setattr(probeowl.setup_writes, "resolve_secret", lambda ref: "the saved one")
    body = draft(password=None, use_saved_password=True)
    assert {r.status_code for r in tried(setup, body, MAX_ATTEMPTS + 3)} == {200}


def test_another_environment_is_not_held_up(setup: Setup) -> None:
    tried(setup, draft(), MAX_ATTEMPTS)
    other = setup.api.post(DRAFT_URL, json=new_password(connector="access_db", environment="uat"))
    assert other.status_code != 429
