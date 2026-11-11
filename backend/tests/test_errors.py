# Author: @SaadZarook
import pytest

from probeowl.errors import (
    ConfigError,
    ConnectorError,
    GuardViolation,
    NotFoundError,
    ProbeOwlError,
)


@pytest.mark.parametrize("error", [ConfigError, ConnectorError, GuardViolation, NotFoundError])
def test_every_error_is_caught_as_probeowl_error(error: type[ProbeOwlError]) -> None:
    with pytest.raises(ProbeOwlError, match="wallet_db has no binding"):
        raise error("wallet_db has no binding")


def test_probeowl_error_is_not_a_builtin_lookup_error() -> None:
    # A Missing record is an outcome, not an exception; NotFoundError must not be
    # swallowed by code that catches LookupError or KeyError.
    assert not issubclass(NotFoundError, LookupError)
