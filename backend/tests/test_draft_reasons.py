# Author: @SaadZarook
"""Test connection on a draft says one of the fixed reasons, never the server's own text or the
host (#57's follow-up): with the real adapters, against a host that can't be found."""

from pathlib import Path

import pytest

from probeowl.connector_status import ConnectorStatuses
from probeowl.connectors.failures import _MESSAGES
from probeowl.guards.breaker import CircuitBreaker
from probeowl.model.check import load_applications
from probeowl.model.connector import RestConnector, SqlConnector

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
HOST = "probeowl-no-such-host.invalid"  # .invalid never resolves (RFC 6761)
PASSWORD = "draft-reason-test-pw"
FIXED = set(_MESSAGES.values())


def statuses() -> ConnectorStatuses:
    return ConnectorStatuses(load_applications(EXAMPLES), CircuitBreaker(), lambda app, c: None)


@pytest.mark.parametrize("kind", ["sqlserver", "oracle", "mariadb"])
def test_a_sql_draft_that_cannot_connect_says_a_fixed_reason_without_the_host(kind: str) -> None:
    database = "FREEPDB1" if kind == "oracle" else "db"
    secret = "keyring:probeowl/draft.uat"
    binding = {"host": HOST, "port": 1, "database": database, "user": "ro", "secret": secret}
    connector = SqlConnector.model_validate(
        {"connector": "draft", "type": kind, "environments": {"uat": binding}}
    )
    result = statuses().test_draft("card_access", connector, "uat", lambda ref: PASSWORD)
    assert result.reachable is False
    assert result.reason in FIXED, result.reason
    assert HOST not in (result.reason or "") and PASSWORD not in (result.reason or "")


def test_a_rest_draft_that_cannot_connect_says_why_without_the_host() -> None:
    connector = RestConnector.model_validate(
        {
            "connector": "draft",
            "type": "rest",
            "environments": {"uat": {"base_url": f"https://{HOST}"}},
        }
    )
    result = statuses().test_draft("card_access", connector, "uat", lambda ref: PASSWORD)
    assert result.reachable is False
    assert result.reason is not None and HOST not in result.reason
