# Author: @SaadZarook
"""Every SQL lookup's evidence says how its connection was protected."""

from typing import Any

import pytest

from probeowl.connectors.sql import transport
from probeowl.model import SqlBinding


def binding(**changes: Any) -> SqlBinding:
    data: dict[str, Any] = {"host": "db", "user": "probeowl_ro", "secret": "env:PW"}
    return SqlBinding.model_validate(data | changes)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "TLS, server certificate verified"),
        ({"trust_server_certificate": True}, "TLS, any server certificate trusted"),
        ({"encrypt": False}, "not encrypted"),
    ],
)
def test_transport_for_evidence(changes: dict[str, Any], expected: str) -> None:
    assert transport(binding(**changes)) == expected
